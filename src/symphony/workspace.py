"""SPEC §9 — workspace manager and lifecycle hooks."""

from __future__ import annotations

import asyncio
import os
import shutil
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

    def update_hooks(
        self, hooks: HooksConfig, *, workflow_dir: Path | None = None
    ) -> None:
        # §6.2 — apply reloaded hooks to future executions.
        self._hooks = hooks
        if workflow_dir is not None:
            self._workflow_dir = workflow_dir

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
        process = await asyncio.create_subprocess_exec(
            resolve_bash(),
            "-lc",
            script,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "SYMPHONY_WORKFLOW_DIR": str(self._workflow_dir) if self._workflow_dir else "",
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            log.error("hook_timeout", hook=name, cwd=str(cwd))
            raise SymphonyError(f"hook {name} timed out", hook=name)

        rc = process.returncode or 0
        if rc != 0:
            log.error(
                "hook_failed",
                hook=name,
                cwd=str(cwd),
                returncode=rc,
                stderr=_truncate(stderr.decode("utf-8", errors="replace")),
            )
            raise SymphonyError(f"hook {name} exited {rc}", hook=name, returncode=rc)
        log.info(
            "hook_completed",
            hook=name,
            cwd=str(cwd),
            stdout=_truncate(stdout.decode("utf-8", errors="replace")),
        )


def _truncate(value: str, limit: int = 400) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...(truncated)"


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
