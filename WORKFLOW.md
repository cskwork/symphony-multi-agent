---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Done, Cancelled, Blocked, Archive]
  # Auto-archive sweep: terminal-state issues whose `updated_at` is older
  # than `archive_after_days` move to `archive_state` on the next poll.
  # Set `archive_after_days: 0` to disable the sweep (manual `a` hotkey
  # in the TUI still works). 30 days is a safe default — rerunning a
  # ticket or adding a comment resets the clock.
  archive_state: Archive
  archive_after_days: 30
  state_descriptions:
    Todo: "Triage; route to Explore"
    Explore: "Brief from llm-wiki + git + code"
    "In Progress": "TDD loop, draft branch"
    Review: "Read diff, fix CRITICAL/HIGH/MEDIUM"
    QA: "pytest -q + real-CLI smoke"
    Learn: "Distill learnings, update llm-wiki"
    Done: "As-Is -> To-Be report"
    Archive: "Auto-archived after 30 days idle"

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  # The workspace starts empty. Attach it as a git worktree of the host
  # repo on a per-ticket symphony/<ID> branch so the host working tree
  # stays untouched while the agent works. To merge results back run
  # `git -C <HOST_REPO> merge symphony/<ID>` (or open a PR from that
  # branch) — explicit operator action, not automatic.
  #
  # IMPORTANT: kanban/, docs/, and llm-wiki/ are symlinked back to the
  # host repo so that agent edits (state transitions, evidence, wiki
  # updates) are visible to Symphony's FileBoardTracker, which reads
  # board_root relative to WORKFLOW.md (the host), not the workspace.
  after_create: |
    set -euo pipefail
    ISSUE_ID="$(basename "$PWD")"
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
    WORKTREE_PATH="$PWD"
    BRANCH="symphony/${ISSUE_ID}"
    # Symphony pre-creates the workspace dir; git worktree add refuses to
    # populate an existing path, so drop the empty dir first.
    cd "$HOST_REPO"
    [ -d "$WORKTREE_PATH" ] && rmdir "$WORKTREE_PATH"
    # Reuse the branch if a prior worktree was reaped without prune.
    if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
      git worktree add "$WORKTREE_PATH" "$BRANCH"
    else
      git worktree add "$WORKTREE_PATH" -b "$BRANCH"
    fi
    cd "$WORKTREE_PATH"
    # Record the fork point so commit_workspace_on_done can `git reset --soft`
    # back to it and squash all per-turn work into a single ticket commit.
    git config symphony.basesha "$(git rev-parse HEAD)"
    # These paths are symlinked to the host for file-tracker visibility; do not
    # let per-turn or final auto-commits replace tracked directories with links.
    git config --add symphony.autocommitExclude kanban
    git config --add symphony.autocommitExclude docs
    git config --add symphony.autocommitExclude llm-wiki
    # Symlink shared directories back to host so agent edits are visible
    # to Symphony's file tracker (which reads from the host's board_root).
    for dir in kanban docs llm-wiki; do
      rm -rf "$dir"
      ln -s "$HOST_REPO/$dir" "$dir"
    done
    python3.11 -m venv .venv
    .venv/bin/pip install --quiet -e '.[dev]'
  before_run: |
    # NEVER `git reset --hard` inside a worktree — discards mid-run work.
    set -uo pipefail
    git fetch origin main --quiet || true
  after_run: |
    # Per-turn commit-or-amend. The branch stays at the same number of
    # commits across turns (amends in place when HEAD is already a `wip:`
    # commit), but every completed turn is durably written to .git/objects
    # so even a hard crash (SIGKILL, host reboot) won't lose work. The
    # orchestrator squashes everything into a single `<ID>: <title>` commit
    # on exit — see auto_commit_on_done.
    set -uo pipefail
    git add -A -- . ':(exclude)kanban' ':(exclude)docs' ':(exclude)llm-wiki' 2>/dev/null || true
    if git diff --cached --quiet 2>/dev/null; then
      echo "run finished at $(date -u +%FT%TZ) (no changes)"
      exit 0
    fi
    # Honors any pre-commit hooks in the host repo — if they fail, this
    # turn's snapshot fails and the next turn picks up where files are.
    LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
    if [ "${LAST#wip:}" != "$LAST" ]; then
      git -c user.email=symphony@local -c user.name=symphony \
          commit --amend --no-edit >/dev/null 2>&1 || true
    else
      git -c user.email=symphony@local -c user.name=symphony \
          commit -m "wip: turn $(date -u +%FT%TZ)" >/dev/null 2>&1 || true
    fi
    echo "run finished at $(date -u +%FT%TZ)"
  before_remove: |
    # Detach the worktree from the host before Symphony rmtree's the dir,
    # otherwise `.git/worktrees/<ID>` lingers until `git worktree prune`.
    # auto_commit_on_done already snapshotted any leftover changes by now.
    set -uo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?}"
    WORKTREE_PATH="$PWD"
    git -C "$HOST_REPO" worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true

agent:
  kind: claude
  max_concurrent_agents: 3
  max_turns: 20
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 3
    Explore: 3
    "In Progress": 3
    Review: 3
    QA: 3
    Learn: 3

claude:
  command: claude -p --output-format stream-json --verbose --permission-mode acceptEdits
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

gemini:
  command: 'gemini -p ""'

pi:
  command: 'pi --mode json -p ""'
  resume_across_turns: true

server:
  port: 9999

tui:
  language: en
  max_cards_per_column: 6

prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    Explore: ./docs/symphony-prompts/file/stages/explore.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Review: ./docs/symphony-prompts/file/stages/review.md
    QA: ./docs/symphony-prompts/file/stages/qa.md
    Learn: ./docs/symphony-prompts/file/stages/learn.md
    Done: ./docs/symphony-prompts/file/stages/done.md

---

This workflow uses stage-specific prompt files configured under `prompts`.
Customize `docs/symphony-prompts/file/` to change the agent instructions.
If the `prompts` block is removed, Symphony falls back to this short legacy body.

You are working on {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
Follow the board state instructions configured for this workflow.
