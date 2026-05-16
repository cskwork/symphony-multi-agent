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
    # `git worktree add` (git >= 2.30) tolerates an existing *empty* target
    # directory — which is exactly what Symphony pre-creates as the workspace.
    # We rely on that here to avoid an rmdir that on Windows races against
    # the file-indexer / AV scan and used to trip the dispatcher into a
    # `Device or resource busy` retry loop.
    #
    # A prior crashed attempt may have left `.git/worktrees/<ID>` registered;
    # detach it first so the next `add` doesn't fail with
    # "missing but already registered" or "already checked out".
    git worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true
    git worktree prune 2>/dev/null || true
    if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
      git worktree add "$WORKTREE_PATH" "$BRANCH"
    else
      git worktree add "$WORKTREE_PATH" -b "$BRANCH"
    fi
    # Record the fork point so commit_workspace_on_done can `git reset --soft`
    # back to it and squash all per-turn work into a single ticket commit.
    # Use --worktree so the value is scoped to .git/worktrees/<ID>/config.gitwt;
    # writing without the flag leaks into the host repo's shared .git/config
    # and corrupts auto_commit for unrelated workspaces nested in the host.
    git -C "$WORKTREE_PATH" config extensions.worktreeConfig true
    git -C "$WORKTREE_PATH" config --worktree symphony.basesha "$(git -C "$WORKTREE_PATH" rev-parse HEAD)"
    # Linear tracker reads from its API, not the file system, so no
    # symlink-back step is needed. (For tracker.kind=file, also symlink
    # kanban/docs/llm-wiki back to $HOST_REPO — see WORKFLOW.file.example.md.)
  before_run: |
    # NEVER `git reset --hard` inside a worktree — it discards in-progress
    # work between turns. Just refresh remotes; let the agent decide if/when
    # to rebase.
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
    git add -A 2>/dev/null || true
    if git diff --cached --quiet 2>/dev/null; then
      echo "run finished at $(date) (no changes)"
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
    echo "run finished at $(date)"
  before_remove: |
    # Detach the worktree before Symphony rmtree's the dir, otherwise
    # `.git/worktrees/<ID>` lingers until `git worktree prune`. By this
    # point the orchestrator has already auto-committed any leftover
    # changes (see agent.auto_commit_on_done).
    set -uo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?}"
    WORKTREE_PATH="$PWD"
    git -C "$HOST_REPO" worktree remove --force "$WORKTREE_PATH" 2>/dev/null || true

agent:
  kind: codex          # codex | claude | gemini | pi
  max_concurrent_agents: 4
  max_turns: 20
  # Hard per-ticket budget across continuation attempts. Prevents an
  # active-state ticket from restarting forever and wasting tokens.
  max_total_turns: 60
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
  # After auto-commit on Done, fold the `symphony/<ID>` branch into the
  # host repo's main development branch as one selective-apply commit.
  # Safe-by-default: dirty host or missing branch skips silently. Paths
  # in `auto_merge_exclude_paths` are stripped first — by default this
  # covers the workspace symlinks that the reference `after_create`
  # hook installs (kanban/llm-wiki/prompt/docs), which are workspace-only
  # plumbing and should never reach the host repo.
  auto_merge_on_done: true
  # Branch to merge into. Empty string = use whatever branch is currently
  # checked out in the host repo when the ticket finishes (most flexible).
  auto_merge_target_branch: ""
  # Paths excluded from the selective apply. Override when your
  # after_create hook installs a different set of workspace symlinks.
  auto_merge_exclude_paths:
    - kanban
    - llm-wiki
    - prompt
    - docs
  # OPT-IN: paths under the host repo whose currently-untracked files
  # should be folded into the same auto-merge commit. Closes the gap
  # where `hooks.after_create` installs host directories as symlinks
  # inside the agent workspace — the agent writes files via the symlink
  # (so they land in the host repo's real directories) but those writes
  # never appear in the `symphony/<ID>` branch diff because the branch
  # only sees the symlink as a single blob. List the host-side paths
  # where you expect per-ticket notes/wiki/etc to accumulate. Defaults
  # to empty (current behaviour preserved). Distinct from
  # `auto_merge_exclude_paths` — those skip branch-side checkout; this
  # captures host-side filesystem state.
  auto_merge_capture_untracked: []
  #   - docs
  #   - llm-wiki

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
