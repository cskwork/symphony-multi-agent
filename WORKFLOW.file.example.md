---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Done, Cancelled, Blocked, Archive]
  # Auto-archive sweep — terminal-state issues whose `updated_at` is older
  # than `archive_after_days` move to `archive_state` on each poll tick.
  # Set `archive_after_days: 0` to disable the sweep (TUI `a` hotkey still
  # works). 30 days is a safe default.
  archive_state: Archive
  archive_after_days: 30
  # Optional one-line legend rendered under each TUI column header.
  state_descriptions:
    Todo: "Triage; route to Explore"
    Explore: "Brief from llm-wiki + git + code"
    "In Progress": "TDD loop, draft PR"
    Review: "Read diff, fix CRITICAL/HIGH/MEDIUM"
    QA: "Execute real code, capture evidence"
    Learn: "Distill learnings, update llm-wiki"
    Done: "As-Is -> To-Be report"
    Archive: "Auto-archived after 30 days idle"

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  # Default: each ticket gets its own git worktree of the host repo on a
  # symphony/<ID> branch. The host working tree is never disturbed; merge
  # back via `git -C <HOST_REPO> merge symphony/<ID>` (or open a PR) when
  # you're satisfied — explicit operator action.
  #
  # If your code lives in a *different* remote than the WORKFLOW.md repo,
  # replace the worktree commands with `git clone <remote> .` instead.
  after_create: |
    set -euo pipefail
    ISSUE_ID="$(basename "$PWD")"
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
    WORKTREE_PATH="$PWD"
    BRANCH="symphony/${ISSUE_ID}"
    cd "$HOST_REPO"
    [ -d "$WORKTREE_PATH" ] && rmdir "$WORKTREE_PATH"
    if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
      git worktree add "$WORKTREE_PATH" "$BRANCH"
    else
      git worktree add "$WORKTREE_PATH" -b "$BRANCH"
    fi
    cd "$WORKTREE_PATH"
    # Symlink tracker-managed directories back to host so agent state
    # transitions are visible to Symphony's FileBoardTracker (which reads
    # board_root from the host repo, not from this worktree's checkout).
    for dir in kanban docs llm-wiki; do
      rm -rf "$dir"
      ln -s "$HOST_REPO/$dir" "$dir"
    done
  before_run: |
    set -euo pipefail
    git fetch origin main --quiet || true
  after_run: |
    echo "run finished at $(date)"
  before_remove: |
    # Detach the worktree before Symphony rmtree's the dir, otherwise
    # `.git/worktrees/<ID>` lingers until `git worktree prune`.
    set -uo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?}"
    WORKTREE_PATH="$PWD"
    git -C "$HOST_REPO" worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true

agent:
  kind: codex          # codex | claude | gemini | pi
  max_concurrent_agents: 4
  max_turns: 20
  max_concurrent_agents_by_state:
    Todo: 2
    Explore: 2
    "In Progress": 4
    Review: 2
    QA: 2
    Learn: 2
  # Snapshot the workspace into one git commit when a ticket reaches Done.
  # Reuses any enclosing git repo; otherwise runs `git init` first. Set to
  # false to opt out (e.g. workspace is a real repo you don't want touched).
  auto_commit_on_done: true

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

claude:
  command: claude -p --output-format stream-json --verbose

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass `""`
  # so the prompt comes purely from stdin.
  command: 'gemini -p ""'

pi:
  # `pi --mode json` emits JSONL events; stdin carries the prompt.
  # Auth: sign in once with `pi` → `/login` (OAuth). Credentials cached at
  # `~/.pi/agent/auth.json` are inherited automatically.
  command: 'pi --mode json -p ""'

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

tui:
  language: en               # `en` (default) or `ko`. SYMPHONY_LANG env overrides.
                             # Also drives artefact language: every prompt is
                             # prefixed with a one-line directive so kanban
                             # comments and docs/<id>/<stage>/*.md come back in
                             # the chosen language. `{{ language }}` is also
                             # exposed to this template for `{% if %}` branches.

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
