"""Auto-merge a finished ticket's `symphony/<ID>` branch into the host repo.

Fires once when a ticket reaches Done, immediately after
`commit_workspace_on_done`. Applies all paths changed in `symphony/<ID>`
vs the target branch's HEAD — *except* paths listed in `exclude_paths`,
which are workspace symlinks that the reference `after_create` hook
installs (kanban/llm-wiki/prompt/docs). A plain `git merge` would try to
materialise those symlinks back onto the host working tree where they
clash with the original directories; selective apply avoids that
entirely.

Safety contract: this is best-effort.
- Dirty host working tree     -> skip, log `auto_merge_skipped_dirty`
- Branch does not exist       -> skip, log `auto_merge_skipped_missing_branch`
- Nothing to apply after excl -> skip, log `auto_merge_nothing_to_apply`
- Any other git error         -> log `auto_merge_failed` and return

The caller never sees an exception; Symphony's queue keeps moving even
if a single ticket's merge fails.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
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
_RC_FAIL_GIT = 50
_RC_FAIL_COMMIT = 51


async def auto_merge_on_done_best_effort(
    *,
    workflow_dir: Path,
    branch: str,
    identifier: str,
    title: str,
    target_branch: str,
    exclude_paths: tuple[str, ...] | list[str],
) -> None:
    """Selectively apply `branch` onto `target_branch` in `workflow_dir`."""
    target = (target_branch or "").strip()
    excludes = tuple(p for p in exclude_paths if p)
    script = _build_script(
        branch=branch,
        target=target,
        identifier=identifier,
        title=title or "",
        excludes=excludes,
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
        return
    except Exception as exc:
        log.warning(
            "auto_merge_failed",
            path=str(workflow_dir),
            identifier=identifier,
            error=str(exc),
        )
        return

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
    elif rc == _RC_SKIP_DIRTY:
        log.info(
            "auto_merge_skipped_dirty",
            path=str(workflow_dir),
            identifier=identifier,
            stdout=stdout[:400],
        )
    elif rc == _RC_SKIP_MISSING_BRANCH:
        log.info(
            "auto_merge_skipped_missing_branch",
            path=str(workflow_dir),
            identifier=identifier,
            branch=branch,
        )
    elif rc == _RC_NOTHING_TO_APPLY:
        log.info(
            "auto_merge_nothing_to_apply",
            path=str(workflow_dir),
            identifier=identifier,
        )
    elif rc in (_RC_FAIL_GIT, _RC_FAIL_COMMIT):
        log.warning(
            "auto_merge_failed",
            path=str(workflow_dir),
            identifier=identifier,
            rc=rc,
            stdout=stdout[:400],
            stderr=stderr[:400],
        )
    else:
        log.warning(
            "auto_merge_failed_unknown_rc",
            path=str(workflow_dir),
            identifier=identifier,
            rc=rc,
            stdout=stdout[:400],
            stderr=stderr[:400],
        )


def _build_script(
    *,
    branch: str,
    target: str,
    identifier: str,
    title: str,
    excludes: tuple[str, ...],
) -> str:
    """Shell-out script for the selective-apply merge.

    Kept as one bash invocation (not a sequence of python subprocess
    calls) so the entire flow either rolls forward to a commit or leaves
    the host repo untouched — no half-applied state is reachable.
    """
    exclude_re = "^(" + "|".join(excludes) + ")$" if excludes else ""
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
        'if [ -n "$(git status -uno --porcelain)" ]; then\n'
        '  echo "SKIP: host repo has tracked changes"; exit 41\n'
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
        'if [ -n "$EXCLUDE_RE" ]; then\n'
        '  AM="$(git diff --name-only --diff-filter=AM "$TARGET".."$BRANCH" | '
        'grep -vE "$EXCLUDE_RE" || true)"\n'
        '  DEL="$(git diff --name-only --diff-filter=D "$TARGET".."$BRANCH" | '
        'grep -vE "$EXCLUDE_RE" || true)"\n'
        "else\n"
        '  AM="$(git diff --name-only --diff-filter=AM "$TARGET".."$BRANCH" || true)"\n'
        '  DEL="$(git diff --name-only --diff-filter=D "$TARGET".."$BRANCH" || true)"\n'
        "fi\n"
        'if [ -z "$AM" ] && [ -z "$DEL" ]; then\n'
        '  echo "SKIP: nothing non-excluded differs"; exit 43\n'
        "fi\n"
        '[ -n "$AM" ] && echo "$AM" | xargs -I{} git checkout "$BRANCH" -- "{}"\n'
        '[ -n "$DEL" ] && echo "$DEL" | xargs -I{} git rm -f -q -- "{}" 2>/dev/null\n'
        "if git diff --cached --quiet; then\n"
        '  echo "SKIP: nothing staged"; exit 43\n'
        "fi\n"
        'SHA="$(git rev-parse --short "$BRANCH")"\n'
        "git -c user.email=symphony@local -c user.name=symphony commit "
        '-m "feat: apply ${IDENT} from ${BRANCH} (${SHA})" '
        '-m "${TITLE}" '
        '-m "Workspace symlinks excluded: ${EXCLUDE_RE}" '
        '-m "Source: ${BRANCH} ${SHA}" '
        '|| { echo "FAIL: commit failed"; exit 51; }\n'
        'echo "OK: ${BRANCH} (${SHA}) applied to ${TARGET}"\n'
    )
