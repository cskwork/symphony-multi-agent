#!/usr/bin/env bash
# Symphony after_create hook — extracted from WORKFLOW.md.
#
# Attach the empty ticket workspace as a git worktree of the host repo on a
# per-ticket `symphony/<ID>` branch, symlink (or junction on Windows) any
# host-owned board roots back into the worktree, and prime a Python venv for
# the agent. Behaviour is identical to the inline hook that previously lived
# inside WORKFLOW.md — see workflow-v0.5.2.md (C4) for the rationale.
#
# Env vars consumed (Symphony injects these before invoking the hook):
#   SYMPHONY_WORKFLOW_DIR        Path to the host repo containing WORKFLOW.md.
#   SYMPHONY_FEATURE_BASE_BRANCH Optional override for the new branch's start point.
#   SYMPHONY_MERGE_TARGET_BRANCH Optional override for the Learn-gate merge target.
set -euo pipefail
ISSUE_ID="$(basename "$PWD")"
HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
WORKTREE_PATH="$PWD"
BRANCH="symphony/${ISSUE_ID}"
# Symphony pre-creates the workspace dir; git worktree add refuses to
# populate an existing path, so drop the empty dir first.
cd "$HOST_REPO"
BASE_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || git branch --show-current 2>/dev/null || true)"
FEATURE_BASE_BRANCH="${SYMPHONY_FEATURE_BASE_BRANCH:-${BASE_BRANCH:-}}"
MERGE_TARGET_BRANCH="${SYMPHONY_MERGE_TARGET_BRANCH:-${FEATURE_BASE_BRANCH:-${BASE_BRANCH:-}}}"
# `git worktree add` (git >= 2.30) tolerates an existing *empty* target
# directory, which is exactly what Symphony pre-creates here. Trying to
# rmdir it first runs straight into Windows file-indexer / AV scans that
# hold a transient handle on the fresh dir and used to trip the
# dispatcher into a retry loop with `Device or resource busy`. Skipping
# the rmdir avoids the race entirely.
#
# A prior crashed attempt may have left `.git/worktrees/<ID>` registered;
# detach it first so the next `add` doesn't fail with
# "missing but already registered" or "already checked out". `remove`
# tolerates a non-existent path (returns non-zero, ignored); `prune`
# mops up any leftover admin files.
git worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true
git worktree prune 2>/dev/null || true
# Reuse the branch if a prior worktree was reaped without prune.
if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  git worktree add "$WORKTREE_PATH" "$BRANCH"
elif [ -n "$FEATURE_BASE_BRANCH" ]; then
  git worktree add "$WORKTREE_PATH" -b "$BRANCH" "$FEATURE_BASE_BRANCH"
else
  git worktree add "$WORKTREE_PATH" -b "$BRANCH"
fi
cd "$WORKTREE_PATH"
# Record the fork point so commit_workspace_on_done can `git reset --soft`
# back to it and squash all per-turn work into a single ticket commit.
# Use --worktree so the value is scoped to .git/worktrees/<ID>/config.gitwt;
# writing without the flag leaks into the host repo's shared .git/config
# and corrupts auto_commit for unrelated workspaces nested in the host.
git config extensions.worktreeConfig true
git config --worktree symphony.basesha "$(git rev-parse HEAD)"
git config --worktree symphony.basebranch "${FEATURE_BASE_BRANCH:-${BASE_BRANCH:-}}"
git config --worktree symphony.mergetargetbranch "${MERGE_TARGET_BRANCH:-}"
# Link shared board directories back to host so agent state changes are
# visible to Symphony's file tracker (which reads host board_root).
#
# Cross-platform: on POSIX use `ln -s`; on Windows Git Bash without
# admin / Developer Mode, `ln -s` silently *copies* the source, leaving
# the worktree's kanban/ as a divergent real directory — the tracker
# never sees the agent's Done transition and dispatches forever. The
# portable fix is a Windows directory junction (`mklink /J`), which
# behaves like a real directory to every tool, works cross-volume, and
# needs no elevation.
_symphony_link_dir() {
  local target="$1" source="$2"
  rm -rf "$target"
  if [ "${OS:-}" = "Windows_NT" ] && command -v cmd.exe >/dev/null 2>&1; then
    # MSYS bash mangles backslashes inside `cmd.exe //c "..."` argument
    # strings (e.g. `\U` in `\Users` becomes garbled), so route through
    # a tiny .bat that takes %1/%2 — bat files receive properly quoted
    # args untouched. Also handles paths containing spaces.
    local target_win source_win bat bat_win
    target_win="$(cygpath -w "$(realpath -m "$target")")"
    source_win="$(cygpath -w "$source")"
    bat="${TEMP:-/tmp}/symphony-link-$$-$RANDOM.bat"
    printf '@echo off\r\nmklink /J %%1 %%2\r\n' > "$bat"
    bat_win="$(cygpath -w "$bat")"
    cmd.exe //c "$bat_win" "$target_win" "$source_win" >/dev/null
    rm -f "$bat"
  else
    ln -s "$source" "$target"
  fi
}
for dir in kanban; do
  [ -e "$HOST_REPO/$dir" ] || continue
  tracked_file="$(git rev-parse --git-path "symphony-${dir}-tracked")"
  git ls-files -z -- "$dir" > "$tracked_file" || true
  if [ -s "$tracked_file" ]; then
    xargs -0 git update-index --skip-worktree -- < "$tracked_file" || true
  fi
  rm -f "$tracked_file"
  exclude_file="$(git rev-parse --git-path info/exclude)"
  mkdir -p "$(dirname "$exclude_file")"
  grep -qxF "$dir" "$exclude_file" 2>/dev/null || echo "$dir" >> "$exclude_file"
  _symphony_link_dir "$dir" "$HOST_REPO/$dir"
done
# Pick the first available Python interpreter. `python3.11` is preferred
# because the project pins to >= 3.11, but we tolerate any newer 3.x so
# the hook does not break on fresh hosts that only ship 3.12+.
PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"; break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "after_create: no python3 on PATH; skipping venv install." >&2
else
  # Hide the worker venv from the worktree's git status so per-turn
  # commit hooks (and `git status` invariants in tests) don't see it.
  exclude_file="$(git rev-parse --git-path info/exclude)"
  mkdir -p "$(dirname "$exclude_file")"
  grep -qxF ".venv" "$exclude_file" 2>/dev/null || echo ".venv" >> "$exclude_file"
  "$PYTHON" -m venv .venv
  # `python -m pip` survives the venv-script path differences between
  # POSIX (`.venv/bin/pip`) and Windows (`.venv/Scripts/pip.exe`).
  .venv/*/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
    || .venv/bin/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
    || .venv/Scripts/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
    || echo "after_create: pip install failed; agent will fall back to host python." >&2
fi
