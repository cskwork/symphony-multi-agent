"""SPEC §9 — workspace manager and lifecycle hooks."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ._shell import resolve_bash
from .errors import InvalidWorkspaceCwd, SymphonyError
from .issue import workspace_key
from .logging import get_logger
from .workflow import HooksConfig

log = get_logger()


def _try_rmtree_once(path: Path) -> tuple[bool, str | None, bool]:
    """Single rmtree attempt.

    Returns ``(success, last_error, retryable)``. ``retryable`` is True only
    for ``PermissionError`` on Windows — every other failure must propagate
    immediately so POSIX permission errors aren't masked.
    """
    try:
        shutil.rmtree(path)
        return True, None, False
    except FileNotFoundError:
        return True, None, False
    except PermissionError as exc:
        return False, str(exc), sys.platform == "win32"
    except OSError as exc:
        return False, str(exc), False


async def _force_rmtree(path: Path, *, attempts: int = 5) -> tuple[bool, str | None]:
    """Best-effort recursive delete with brief retry on Windows.

    Windows can hold a directory's handle open for tens of milliseconds after
    a child subprocess exits (the subprocess used the directory as its cwd),
    causing ``shutil.rmtree`` to fail with ``PermissionError`` even though the
    process is gone. The backoff uses ``await asyncio.sleep`` so concurrent
    workspace cleanups don't stall the event loop.
    """
    last_err: str | None = None
    for i in range(attempts):
        ok, err, retryable = _try_rmtree_once(path)
        if ok:
            return True, None
        last_err = err
        if not retryable or i == attempts - 1:
            return False, last_err
        await asyncio.sleep(0.05 * (i + 1))
    return False, last_err


@dataclass(frozen=True)
class Workspace:
    path: Path
    workspace_key: str
    created_now: bool


class WorkspaceManager:
    """§9.1, §9.2 — sanitized per-issue workspace directories."""

    def __init__(self, root: Path, hooks: HooksConfig, *, workflow_dir: Path | None = None) -> None:
        self._root = root.resolve()
        self._hooks = hooks
        self._workflow_dir = workflow_dir
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def update_hooks(self, hooks: HooksConfig) -> None:
        # §6.2 — apply reloaded hooks to future executions.
        self._hooks = hooks

    def path_for(self, identifier: str) -> Path:
        key = workspace_key(identifier)
        return (self._root / key).resolve()

    async def create_or_reuse(self, identifier: str) -> Workspace:
        key = workspace_key(identifier)
        path = (self._root / key).resolve()
        self._enforce_root_containment(path)

        if path.exists() and not path.is_dir():
            raise SymphonyError(
                "workspace path occupied by non-directory", path=str(path)
            )

        created_now = not path.exists()
        path.mkdir(parents=True, exist_ok=True)

        if created_now and self._hooks.after_create:
            try:
                await self._run_hook("after_create", self._hooks.after_create, path)
            except Exception:
                # §9.4 — after_create failure is fatal; clean partial directory.
                ok, err = await _force_rmtree(path)
                if not ok:
                    log.warning(
                        "workspace_cleanup_incomplete", path=str(path), error=err
                    )
                raise

        return Workspace(path=path, workspace_key=key, created_now=created_now)

    async def before_run(self, path: Path) -> None:
        if self._hooks.before_run:
            await self._run_hook("before_run", self._hooks.before_run, path)

    async def after_run_best_effort(self, path: Path) -> None:
        if not self._hooks.after_run:
            return
        # If the agent (or an external process) removed the workspace before we
        # got here, skip the hook — spawning bash with a missing cwd raises an
        # opaque FileNotFoundError that callers cannot act on. Logging at
        # INFO keeps the trail without the false-alarm warning.
        if not path.exists():
            log.info("hook_after_run_skipped_missing_cwd", path=str(path))
            return
        try:
            await self._run_hook("after_run", self._hooks.after_run, path)
        except Exception as exc:  # §9.4 — log and ignore.
            log.warning("hook_after_run_failed", path=str(path), error=str(exc))

    async def remove(self, path: Path) -> None:
        path = path.resolve()
        try:
            self._enforce_root_containment(path)
        except InvalidWorkspaceCwd as exc:
            log.error("refused_remove_outside_root", path=str(path), error=str(exc))
            return
        if not path.exists():
            return
        if self._hooks.before_remove:
            try:
                await self._run_hook("before_remove", self._hooks.before_remove, path)
            except Exception as exc:  # §9.4 — log and ignore.
                log.warning("hook_before_remove_failed", path=str(path), error=str(exc))
        ok, err = await _force_rmtree(path)
        if not ok:
            log.warning("workspace_remove_failed", path=str(path), error=err)

    def _enforce_root_containment(self, path: Path) -> None:
        """§9.5 invariant 2."""
        try:
            path.resolve().relative_to(self._root)
        except ValueError as exc:
            raise InvalidWorkspaceCwd(
                "workspace path escapes workspace root",
                path=str(path),
                root=str(self._root),
            ) from exc

    async def _run_hook(self, name: str, script: str, cwd: Path) -> None:
        timeout_s = max(self._hooks.timeout_ms, 0) / 1000.0
        log.info("hook_start", hook=name, cwd=str(cwd))
        # §9.4 — run script via `bash -lc` with workspace cwd.
        #
        # We deliberately route through a worker thread + blocking
        # `subprocess.run` instead of `asyncio.create_subprocess_exec`. The
        # asyncio child-watcher is fragile under Textual on macOS (Python
        # 3.12): subprocesses spawn fine, exit fine, but `await proc.wait()`
        # never resolves because the watcher never observes the SIGCHLD
        # / waitpid event. The symptom is a zombie `<defunct>` child and a
        # worker stuck forever inside the timeout-cleanup `await
        # process.wait()`. Using `subprocess.run` in a thread bypasses the
        # watcher entirely — `os.waitpid` runs in the worker thread and
        # returns deterministically.
        env = {
            **os.environ,
            "SYMPHONY_WORKFLOW_DIR": str(self._workflow_dir)
            if self._workflow_dir
            else "",
        }

        def _do_run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [resolve_bash(), "-lc", script],
                cwd=str(cwd),
                capture_output=True,
                timeout=timeout_s if timeout_s > 0 else None,
                env=env,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_do_run)
        except subprocess.TimeoutExpired:
            log.error("hook_timeout", hook=name, cwd=str(cwd))
            raise SymphonyError(f"hook {name} timed out", hook=name)

        rc = result.returncode or 0
        stderr_bytes = result.stderr or b""
        stdout_bytes = result.stdout or b""
        stderr_text = _truncate(stderr_bytes.decode("utf-8", errors="replace")).strip()
        stdout_text = _truncate(stdout_bytes.decode("utf-8", errors="replace")).strip()
        if rc != 0:
            log.error(
                "hook_failed",
                hook=name,
                cwd=str(cwd),
                returncode=rc,
                stderr=stderr_text,
            )
            message = f"hook {name} exited {rc}"
            if stderr_text:
                message = f"{message}; stderr: {stderr_text}"
            elif stdout_text:
                message = f"{message}; stdout: {stdout_text}"
            raise SymphonyError(message, hook=name, returncode=rc)
        log.info(
            "hook_completed",
            hook=name,
            cwd=str(cwd),
            stdout=stdout_text,
        )


def _truncate(value: str, limit: int = 400) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...(truncated)"


async def commit_workspace_on_done(
    path: Path,
    *,
    identifier: str,
    title: str,
    exit_reason: str | None = None,
    state: str | None = None,
    timeout_s: float = 60.0,
) -> None:
    """Snapshot the per-ticket workspace into one git commit on worker exit.

    Always called before `WorkspaceManager.remove()` — the goal is that no
    work the agent left in the worktree gets discarded by `git worktree
    remove --force`. Fires for every exit (Done, Cancelled, Blocked,
    error, timeout, reconcile-terminated) when `auto_commit_on_done` is
    on; the commit message includes the exit reason / state for non-Done
    cases so a quick `git log` makes the situation obvious.

    Lenient by design — every failure (missing path, no diffs, pre-commit
    rejection, signing error, timeout) logs a warning and returns. We
    never raise out of the worker exit path; a failed auto-commit is a
    housekeeping miss surfaced by the warning, not a regression that
    blocks the queue.

    Reuses any enclosing git repo (`git -C path rev-parse --git-dir`).
    Only initialises a new repo when the workspace has no git ancestor,
    so workspaces nested inside an existing project repo just add a
    commit to that project's history rather than creating a nested
    `.git`. With the worktree-default hooks the commit lands on the
    `symphony/<ID>` branch the worktree is checked out on.
    """
    if not path.exists():
        log.info("auto_commit_skipped_missing_workspace", path=str(path))
        return

    safe_title = (title or "").replace("\n", " ").strip()[:200] or "(no title)"
    normalized_state = (state or "").strip().lower()
    suffix = ""
    if normalized_state == "done":
        # Work reached Done — message stays clean even when the cleanup
        # path (reconcile / startup) supplied an exit_reason.
        suffix = ""
    elif normalized_state:
        suffix = f" [state: {state}]"
    elif exit_reason and exit_reason != "normal":
        suffix = f" [exit: {exit_reason}]"
    msg = f"{identifier}: {safe_title}{suffix}"

    # One-commit-per-ticket: if the worktree's `after_create` recorded a
    # fork point in `git config symphony.basesha`, soft-reset to that base
    # so all per-turn commits + still-uncommitted changes collapse into a
    # single commit with the ticket subject. When no base is recorded
    # (legacy workspaces, non-worktree setups), fall back to a plain
    # commit-on-top — preserves correctness without forcing operators to
    # re-bootstrap.
    script = (
        'set -u\n'
        'if ! git rev-parse --git-dir >/dev/null 2>&1; then\n'
        '  git init -q || exit 41\n'
        'fi\n'
        'BASE="$(git config --get symphony.basesha 2>/dev/null || true)"\n'
        'git add -A || exit 42\n'
        'HAS_STAGED=1\n'
        'git diff --cached --quiet && HAS_STAGED=0\n'
        'HAS_NEW_COMMITS=0\n'
        'if [ -n "$BASE" ] && git rev-parse --verify "$BASE" >/dev/null 2>&1; then\n'
        '  HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo "")"\n'
        '  if [ -n "$HEAD_SHA" ] && [ "$HEAD_SHA" != "$BASE" ]; then\n'
        '    HAS_NEW_COMMITS=1\n'
        '  fi\n'
        'fi\n'
        'if [ "$HAS_STAGED" -eq 0 ] && [ "$HAS_NEW_COMMITS" -eq 0 ]; then\n'
        '  echo "auto_commit: nothing to commit"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$HAS_NEW_COMMITS" -eq 1 ]; then\n'
        '  # Collapse every commit since the recorded fork point + any\n'
        '  # currently-staged changes into one. --soft preserves the index\n'
        '  # and working tree so the final `git commit` captures everything.\n'
        '  git reset --soft "$BASE" || exit 44\n'
        'fi\n'
        'git commit -m "$SYMPHONY_AUTO_COMMIT_MSG" || exit 43\n'
    )
    env = {
        **os.environ,
        "SYMPHONY_AUTO_COMMIT_MSG": msg,
    }

    def _do_run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [resolve_bash(), "-lc", script],
            cwd=str(path),
            capture_output=True,
            timeout=timeout_s if timeout_s > 0 else None,
            env=env,
            check=False,
        )

    log.info("auto_commit_start", path=str(path), identifier=identifier)
    try:
        result = await asyncio.to_thread(_do_run)
    except subprocess.TimeoutExpired:
        log.warning("auto_commit_timeout", path=str(path), identifier=identifier)
        return
    except Exception as exc:
        log.warning(
            "auto_commit_spawn_failed",
            path=str(path),
            identifier=identifier,
            error=str(exc),
        )
        return

    rc = result.returncode or 0
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    if rc == 0:
        log.info(
            "auto_commit_completed",
            path=str(path),
            identifier=identifier,
            stdout=_truncate(stdout),
        )
        return
    log.warning(
        "auto_commit_failed",
        path=str(path),
        identifier=identifier,
        returncode=rc,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
    )


def validate_agent_cwd(cwd: Path, workspace_root: Path) -> None:
    """§9.5 invariants 1 + 2 — refuse to launch outside workspace root."""
    cwd = cwd.resolve()
    workspace_root = workspace_root.resolve()
    try:
        cwd.relative_to(workspace_root)
    except ValueError as exc:
        raise InvalidWorkspaceCwd(
            "agent cwd not under workspace root",
            cwd=str(cwd),
            root=str(workspace_root),
        ) from exc
    if not cwd.is_dir():
        raise InvalidWorkspaceCwd("agent cwd is not a directory", cwd=str(cwd))
