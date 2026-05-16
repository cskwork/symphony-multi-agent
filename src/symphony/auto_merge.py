"""Auto-merge a finished ticket's `symphony/<ID>` branch into the host repo.

Fires once when a ticket reaches Done, immediately after
`commit_workspace_on_done`. Merges the whole `symphony/<ID>` branch into
the target branch with an explicit `--no-ff` merge commit. Paths listed in
`exclude_paths` are workspace-only roots that must not appear in the branch
diff; if they changed, the merge is blocked instead of silently applying a
partial branch.

Safety contract: this is best-effort.
- Target/branch merge conflict -> fail before dirty-host checks
- Dirty host overlap          -> skip, log `auto_merge_skipped_dirty`
- Branch does not exist       -> skip, log `auto_merge_skipped_missing_branch`
- Nothing to apply after excl -> skip, log `auto_merge_nothing_to_apply`
- Excluded root changed       -> block, log `auto_merge_blocked_excluded_paths`
- Any other git error         -> log `auto_merge_failed` and return

The caller never sees an exception. Instead, the result reports whether
the merge gate is satisfied so the orchestrator can keep successful Done
tickets moving while blocking failed gates before dependents trust them.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ._shell import resolve_bash
from .logging import get_logger

log = get_logger()


# Generous budget — a large repo checkout + commit needs headroom, but
# we don't want a hung git to block Symphony's shutdown forever.
_AUTO_MERGE_TIMEOUT_S = 120.0

# Exit codes from the shell script. Kept distinct so the Python wrapper
# can log a specific event for each outcome.
_RC_OK = 0
_RC_SKIP_DIRTY = 41
_RC_SKIP_MISSING_BRANCH = 42
_RC_NOTHING_TO_APPLY = 43
_RC_BLOCKED_EXCLUDED = 44
_RC_FAIL_GIT = 50
_RC_FAIL_COMMIT = 51


@dataclass(frozen=True)
class AutoMergeResult:
    ok: bool
    status: str
    detail: str = ""


async def auto_merge_on_done_best_effort(
    *,
    workflow_dir: Path,
    branch: str,
    identifier: str,
    title: str,
    target_branch: str,
    exclude_paths: tuple[str, ...] | list[str],
    capture_untracked: tuple[str, ...] | list[str] = (),
) -> AutoMergeResult:
    """Selectively apply `branch` onto `target_branch` in `workflow_dir`.

    `capture_untracked` is an opt-in list of host-repo paths whose currently
    untracked files should be `git add`ed into the same merge commit. Used
    to recover files written via after_create symlinks that the branch
    cannot see (see docstring header).
    """
    target = (target_branch or "").strip()
    excludes = tuple(p for p in exclude_paths if p)
    captures = tuple(p for p in capture_untracked if p)
    script = _build_script(
        branch=branch,
        target=target,
        identifier=identifier,
        title=title or "",
        excludes=excludes,
        captures=captures,
    )

    def _do_run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [resolve_bash(), "-lc", script],
            cwd=str(workflow_dir),
            capture_output=True,
            timeout=_AUTO_MERGE_TIMEOUT_S,
            env=os.environ.copy(),
            check=False,
        )

    log.info(
        "auto_merge_start",
        path=str(workflow_dir),
        identifier=identifier,
        branch=branch,
        target=target or "(current)",
    )
    try:
        result = await asyncio.to_thread(_do_run)
    except subprocess.TimeoutExpired:
        log.warning(
            "auto_merge_timeout", path=str(workflow_dir), identifier=identifier
        )
        return AutoMergeResult(False, "timeout")
    except Exception as exc:
        log.warning(
            "auto_merge_failed",
            path=str(workflow_dir),
            identifier=identifier,
            error=str(exc),
        )
        return AutoMergeResult(False, "error", str(exc))

    stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    rc = result.returncode

    if rc == _RC_OK:
        log.info(
            "auto_merge_completed",
            path=str(workflow_dir),
            identifier=identifier,
            stdout=stdout[:400],
        )
        return AutoMergeResult(True, "merged", stdout)
    elif rc == _RC_SKIP_DIRTY:
        log.info(
            "auto_merge_skipped_dirty",
            path=str(workflow_dir),
            identifier=identifier,
            stdout=stdout[:400],
        )
        return AutoMergeResult(False, "dirty_overlap", stdout)
    elif rc == _RC_SKIP_MISSING_BRANCH:
        log.info(
            "auto_merge_skipped_missing_branch",
            path=str(workflow_dir),
            identifier=identifier,
            branch=branch,
        )
        return AutoMergeResult(False, "missing_branch", f"branch {branch} missing")
    elif rc == _RC_NOTHING_TO_APPLY:
        log.info(
            "auto_merge_nothing_to_apply",
            path=str(workflow_dir),
            identifier=identifier,
        )
        return AutoMergeResult(True, "nothing_to_apply", stdout)
    elif rc == _RC_BLOCKED_EXCLUDED:
        log.warning(
            "auto_merge_blocked_excluded_paths",
            path=str(workflow_dir),
            identifier=identifier,
            stdout=stdout[:400],
        )
        return AutoMergeResult(False, "excluded_paths", stdout)
    elif rc in (_RC_FAIL_GIT, _RC_FAIL_COMMIT):
        log.warning(
            "auto_merge_failed",
            path=str(workflow_dir),
            identifier=identifier,
            rc=rc,
            stdout=stdout[:400],
            stderr=stderr[:400],
        )
        status = "commit_failed" if rc == _RC_FAIL_COMMIT else "git_failed"
        detail = "\n".join(part for part in (stdout, stderr) if part)
        return AutoMergeResult(False, status, detail)
    else:
        log.warning(
            "auto_merge_failed_unknown_rc",
            path=str(workflow_dir),
            identifier=identifier,
            rc=rc,
            stdout=stdout[:400],
            stderr=stderr[:400],
        )
        detail = "\n".join(part for part in (stdout, stderr) if part)
        return AutoMergeResult(False, f"unknown_rc_{rc}", detail)


def _build_script(
    *,
    branch: str,
    target: str,
    identifier: str,
    title: str,
    excludes: tuple[str, ...],
    captures: tuple[str, ...] = (),
) -> str:
    """Shell-out script for the branch merge.

    Kept as one bash invocation (not a sequence of python subprocess calls)
    so the flow either creates one merge commit or leaves the host repo
    untouched, except for non-overlapping pre-existing dirty files that Git
    preserves across the merge.
    """
    exclude_re = "^(" + "|".join(excludes) + ")$" if excludes else ""
    capture_block = ""
    if captures:
        quoted = " ".join(shlex.quote(p) for p in captures)
        capture_block = (
            f"for cap in {quoted}; do\n"
            '  if [ -n "$cap" ] && [ -d "$cap" ] && [ ! -L "$cap" ]; then\n'
            '    git add -- "$cap" 2>/dev/null || true\n'
            "  fi\n"
            "done\n"
        )
    return (
        "set -uo pipefail\n"
        f"BRANCH={shlex.quote(branch)}\n"
        f"TARGET={shlex.quote(target)}\n"
        f"EXCLUDE_RE={shlex.quote(exclude_re)}\n"
        f"IDENT={shlex.quote(identifier)}\n"
        f"TITLE={shlex.quote(title)}\n"
        "if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then\n"
        '  echo "FAIL: not a git repo"; exit 50\n'
        "fi\n"
        'if [ -z "$TARGET" ]; then\n'
        '  TARGET="$(git symbolic-ref --short HEAD 2>/dev/null || true)"\n'
        '  if [ -z "$TARGET" ]; then echo "FAIL: detached HEAD"; exit 50; fi\n'
        "fi\n"
        'CURR="$(git symbolic-ref --short HEAD 2>/dev/null || true)"\n'
        'if [ "$CURR" != "$TARGET" ]; then\n'
        '  git checkout "$TARGET" >/dev/null 2>&1 || '
        '{ echo "FAIL: checkout $TARGET"; exit 50; }\n'
        "fi\n"
        'if ! git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then\n'
        '  echo "SKIP: branch $BRANCH missing"; exit 42\n'
        "fi\n"
        'CHANGED="$(git diff --name-only "$TARGET".."$BRANCH" || true)"\n'
        'if [ -n "$EXCLUDE_RE" ]; then\n'
        '  BAD="$(printf "%s\\n" "$CHANGED" | grep -E "$EXCLUDE_RE" || true)"\n'
        '  if [ -n "$BAD" ]; then\n'
        '    echo "BLOCK: branch changed excluded workspace roots:"\n'
        '    printf "%s\\n" "$BAD"\n'
        "    exit 44\n"
        "  fi\n"
        "fi\n"
        'MERGE_TREE_OUTPUT="$(git merge-tree --write-tree "$TARGET" "$BRANCH" 2>&1)"\n'
        "MERGE_TREE_RC=$?\n"
        'if [ "$MERGE_TREE_RC" -ne 0 ]; then\n'
        '  echo "FAIL: committed target/branch merge conflict"\n'
        '  printf "%s\\n" "$MERGE_TREE_OUTPUT"\n'
        "  exit 50\n"
        "fi\n"
        'DIRTY="$( { git diff --name-only; git diff --cached --name-only; } | sort -u )"\n'
        'if [ -n "$DIRTY" ]; then\n'
        '  OVERLAP="$(comm -12 '
        '<(printf "%s\\n" "$DIRTY" | sort -u) '
        '<(printf "%s\\n" "$CHANGED" | sort -u) || true)"\n'
        '  if [ -n "$OVERLAP" ]; then\n'
        '    echo "SKIP: host tracked changes overlap branch merge:"\n'
        '    printf "%s\\n" "$OVERLAP"\n'
        "    exit 41\n"
        "  fi\n"
        '  echo "WARN: preserving non-overlapping host tracked changes"\n'
        "fi\n"
        f"HAS_CAPTURES={1 if captures else 0}\n"
        'if [ -z "$CHANGED" ] && [ "$HAS_CAPTURES" = "0" ]; then\n'
        '  echo "SKIP: nothing differs"; exit 43\n'
        "fi\n"
        'SHA="$(git rev-parse --short "$BRANCH")"\n'
        'git -c user.email=symphony@local -c user.name=symphony merge '
        '--no-ff --no-commit "$BRANCH" || '
        '{ echo "FAIL: merge failed"; git merge --abort >/dev/null 2>&1 || true; exit 50; }\n'
        + capture_block +
        "if git diff --cached --quiet; then\n"
        '  echo "SKIP: nothing staged after merge"; '
        'git merge --abort >/dev/null 2>&1 || true; exit 43\n'
        "fi\n"
        "git -c user.email=symphony@local -c user.name=symphony commit "
        '-m "merge: ${IDENT} from ${BRANCH} (${SHA})" '
        '-m "${TITLE}" '
        '-m "Source: ${BRANCH} ${SHA}" '
        '|| { echo "FAIL: commit failed"; git merge --abort >/dev/null 2>&1 || true; exit 51; }\n'
        'echo "OK: ${BRANCH} (${SHA}) merged to ${TARGET}"\n'
    )
