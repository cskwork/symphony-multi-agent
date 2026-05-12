---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Closed, Cancelled, Canceled, Duplicate, Done, Archive]
  # Auto-archive sweep — terminal-state issues whose `updated_at` is older
  # than `archive_after_days` move to `archive_state` on each poll tick.
  # Set `archive_after_days: 0` to disable the sweep (TUI `a` hotkey still
  # works). 30 days is a safe default for visible projects.
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
  # Default: attach the per-ticket workspace as a git worktree of the
  # host repo on a symphony/<ID> branch. The host working tree is never
  # touched. Operator merges back via `git -C <HOST_REPO> merge symphony/<ID>`
  # (or PR from that branch) — explicit, never automatic.
  #
  # If your code lives in a *different* remote than where WORKFLOW.md
  # sits (common with Linear setups where the config repo is config-only),
  # replace the worktree commands with a `git clone <remote> .` instead.
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
    # Linear tracker reads from its API, not the file system, so no
    # symlink-back step is needed. (For tracker.kind=file, also symlink
    # kanban/docs/llm-wiki back to $HOST_REPO — see WORKFLOW.file.example.md.)
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
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 2
    Explore: 2
    "In Progress": 4
    Review: 2
    QA: 2
    Learn: 2
  # When a ticket reaches Done cleanly, snapshot the workspace into one
  # git commit (`<identifier>: <title>`). If the workspace is nested
  # inside an existing repo, the commit lands there; otherwise `git init`
  # runs first. Set to false if your workspace is an existing repo with
  # strict commit-style rules you don't want auto-touched.
  auto_commit_on_done: true

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass an
  # empty `""` so the prompt comes purely from stdin.
  command: 'gemini -p ""'
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

pi:
  # `pi --mode json -p ""` emits JSONL events; stdin carries the prompt and
  # `--session <id>` is appended automatically on continuation turns.
  # Auth: sign in once with `pi` → `/login` (OAuth). Credentials are cached
  # at `~/.pi/agent/auth.json` and inherited by every subprocess Symphony
  # spawns — no env var or `--api-key` flag is needed.
  command: 'pi --mode json -p ""'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

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
  base: ./docs/symphony-prompts/linear/base.md
  stages:
    Todo: ./docs/symphony-prompts/linear/stages/todo.md
    Explore: ./docs/symphony-prompts/linear/stages/explore.md
    "In Progress": ./docs/symphony-prompts/linear/stages/in-progress.md
    Review: ./docs/symphony-prompts/linear/stages/review.md
    QA: ./docs/symphony-prompts/linear/stages/qa.md
    Learn: ./docs/symphony-prompts/linear/stages/learn.md
    Done: ./docs/symphony-prompts/linear/stages/done.md

---

This workflow uses stage-specific prompt files configured under `prompts`.
Customize `docs/symphony-prompts/linear/` to change the agent instructions.
If the `prompts` block is removed, Symphony falls back to this short legacy body.

You are working on {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
Follow the board state instructions configured for this workflow.
