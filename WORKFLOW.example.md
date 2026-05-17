---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, Explore, Plan, "In Progress", Review, QA, Learn]
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
    Explore: "Brief from docs/llm-wiki + git + code"
    Plan: "Lock the implementation plan"
    "In Progress": "TDD loop, draft PR"
    Review: "Read diff, fix CRITICAL/HIGH/MEDIUM"
    QA: "Execute real code, capture evidence"
    Learn: "Distill learnings, update docs/llm-wiki"
    Done: "As-Is -> To-Be report"
    Archive: "Auto-archived after 30 days idle"

polling:
  interval_ms: 30000

# Wiki integrity sweep — see `symphony wiki-sweep --help`. The orchestrator
# runs the sweep automatically after every Nth `Done` transition. Set
# `sweep_every_n: 0` to disable; the manual CLI still works either way.
wiki:
  sweep_every_n: 10
  root: ./docs/llm-wiki

workspace:
  root: ~/symphony_workspaces

hooks:
  # Default: attach the per-ticket workspace as a git worktree of the
  # host repo on a symphony/<ID> branch. The host working tree is never
  # touched while the ticket is active. The default Learn gate merges the
  # feature branch into the target branch before the ticket can move to Done.
  #
  # If your code lives in a *different* remote than where WORKFLOW.md
  # sits (common with Linear setups where the config repo is config-only),
  # replace the worktree commands with a `git clone <remote> .` instead.
  # Body extracted to scripts/symphony-setup-worktree.sh — see C4 in
  # docs/improvements/workflow-v0.5.2.md. The script worktree-adds the
  # ticket branch, records basesha/basebranch/mergetargetbranch, and (when
  # a host-owned `kanban/` exists) symlinks/junctions it back. Linear
  # trackers read from their API so the symlink loop is a no-op here.
  after_create: |
    bash "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"
  before_run: |
    # NEVER `git reset --hard` inside a worktree — it discards in-progress
    # work between turns. Just refresh remotes; let the agent decide if/when
    # to rebase.
    set -uo pipefail
    git fetch origin --quiet || true
  after_run: |
    # Per-turn commit-or-amend. The branch stays at the same number of
    # commits across turns (amends in place when HEAD is already a `wip:`
    # commit), but every completed turn is durably written to .git/objects
    # so even a hard crash (SIGKILL, host reboot) won't lose work. The
    # orchestrator squashes everything into a single `<ID>: <title>` commit
    # on exit — see auto_commit_on_done.
    set -uo pipefail
    git add -A -- . ':(exclude).symphony' 2>/dev/null || true
    if git diff --cached --quiet 2>/dev/null; then
      echo "run finished at $(date) (no changes)"
      exit 0
    fi
    # Classify the staged diff so the wip subject carries machine-readable
    # markers. `[no-test]` = production code changed with no paired test
    # file in the same diff (workflow-v0.5.2 § B1 — review.md promotes it
    # to a HIGH finding). `[scope-expand]` = a rewind dispatch (set by the
    # orchestrator when SYMPHONY_REWIND_SCOPE is exported) but the diff
    # touched a file outside the parsed scope list (workflow-v0.5.2 § A2).
    # Both markers can stack.
    STAGED_FILES="$(git diff --cached --name-only 2>/dev/null || true)"
    PROD_CHANGED=0
    TESTS_CHANGED=0
    SCOPE_EXPAND=0
    SCOPE_FILES=""
    if [ -n "${SYMPHONY_REWIND_SCOPE:-}" ]; then
      SCOPE_FILES="$(printf '%s' "$SYMPHONY_REWIND_SCOPE" \
        | tr ',' '\n' \
        | sed -n 's/.*"file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    fi
    NL=$(printf '\nx'); NL=${NL%x}
    OLDIFS="$IFS"
    IFS="$NL"
    for f in $STAGED_FILES; do
      [ -n "$f" ] || continue
      case "$f" in
        tests/*|*_test.py|*.test.ts|*.test.tsx|*_test.go)
          TESTS_CHANGED=1
          ;;
      esac
      case "$f" in
        tests/*|docs/*|kanban/*|.symphony/*)
          : # carve-out: never counts as production change
          ;;
        *)
          PROD_CHANGED=1
          ;;
      esac
      if [ -n "$SCOPE_FILES" ] && [ "$SCOPE_EXPAND" = 0 ]; then
        in_scope=0
        for s in $SCOPE_FILES; do
          if [ "$f" = "$s" ]; then
            in_scope=1
            break
          fi
        done
        if [ "$in_scope" = 0 ]; then
          SCOPE_EXPAND=1
        fi
      fi
    done
    IFS="$OLDIFS"
    PREFIX=""
    if [ "$PROD_CHANGED" = 1 ] && [ "$TESTS_CHANGED" = 0 ]; then
      PREFIX="${PREFIX}[no-test]"
    fi
    if [ -n "${SYMPHONY_REWIND_SCOPE:-}" ] && [ "$SCOPE_EXPAND" = 1 ]; then
      PREFIX="${PREFIX}[scope-expand]"
    fi
    # Honors any pre-commit hooks in the host repo — if they fail, this
    # turn's snapshot fails and the next turn picks up where files are.
    MSG="$(sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' .symphony/commit-message.txt 2>/dev/null || true)"
    [ -n "$MSG" ] || MSG="turn $(date -u +%FT%TZ)"
    case "$MSG" in wip:*) COMMIT_MSG="$MSG" ;; *) COMMIT_MSG="wip: $MSG" ;; esac
    LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
    # On amend, preserve any markers the previous turn already set so a
    # later test-passing turn doesn't drop the historical `[no-test]`.
    # Markers are sticky within a wip subject.
    PRIOR_PREFIX=""
    case "$LAST" in
      *"[no-test]"*) PRIOR_PREFIX="${PRIOR_PREFIX}[no-test]" ;;
    esac
    case "$LAST" in
      *"[scope-expand]"*) PRIOR_PREFIX="${PRIOR_PREFIX}[scope-expand]" ;;
    esac
    MERGED_PREFIX=""
    case "$PRIOR_PREFIX$PREFIX" in
      *"[no-test]"*) MERGED_PREFIX="${MERGED_PREFIX}[no-test]" ;;
    esac
    case "$PRIOR_PREFIX$PREFIX" in
      *"[scope-expand]"*) MERGED_PREFIX="${MERGED_PREFIX}[scope-expand]" ;;
    esac
    if [ -n "$MERGED_PREFIX" ]; then
      COMMIT_MSG="${MERGED_PREFIX} ${COMMIT_MSG}"
    fi
    if [ "${LAST#wip:}" != "$LAST" ]; then
      git -c user.email=symphony@local -c user.name=symphony \
          commit --amend -m "$COMMIT_MSG" >/dev/null 2>&1 || true
    else
      git -c user.email=symphony@local -c user.name=symphony \
          commit -m "$COMMIT_MSG" >/dev/null 2>&1 || true
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
  max_concurrent_agents: 1
  max_turns: 20
  # Hard per-ticket budget across continuation attempts. Prevents an
  # active-state ticket from restarting forever and wasting tokens.
  max_total_turns: 60
  # Hard token ceiling by workflow state. The global cap is the default for
  # Review/Learn; In Progress and QA get larger build/verification budgets.
  max_total_tokens: 100000000
  max_total_tokens_by_state:
    "In Progress": 500000000
    QA: 500000000
  budget_exhausted_state: Blocked
  # Soft cap for Review/QA rewinds back into In Progress. Set 0 to disable.
  max_attempts: 3
  # File-board only: route obvious Todo tickets with Acceptance Criteria to
  # Explore without spending a model turn. Bug/blocked/ambiguous tickets still
  # run the Todo prompt.
  auto_triage_actionable_todo: true
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 1
    Explore: 1
    Plan: 1
    "In Progress": 1
    Review: 1
    QA: 1
    Learn: 1
  # When a ticket reaches Done cleanly, snapshot the workspace into one
  # git commit (`<identifier>: <title>`). If the workspace is nested
  # inside an existing repo, the commit lands there; otherwise `git init`
  # runs first. Set to false if your workspace is an existing repo with
  # strict commit-style rules you don't want auto-touched.
  auto_commit_on_done: true
  # Merge policy for the Learn -> Done gate. Learn must merge the
  # `symphony/<ID>` feature branch into this target before setting Done.
  # The post-Done auto-merge remains a best-effort fallback for older prompts.
  auto_merge_on_done: true
  # Branch/ref used as the start point for new `symphony/<ID>` feature
  # branches. Empty string = current host branch. The board viewer can
  # update this from its real git branch dropdown.
  feature_base_branch: ""
  # Branch to merge into. Empty string = use the branch the feature branch
  # was created from/current host branch (most flexible). The board viewer
  # can update this from its real git branch dropdown.
  auto_merge_target_branch: ""
  # Workspace-only roots that must not differ on the ticket branch. Linear
  # has no host symlink roots, so this stays empty. File-board workflows
  # usually set this to ["kanban"] in WORKFLOW.file.example.md.
  auto_merge_exclude_paths: []
  # Legacy escape hatch: paths under the host repo whose currently
  # untracked files should be folded into the same merge commit. Prefer
  # branch-local docs/ so reports and wiki updates merge normally.
  auto_merge_capture_untracked: []
  #   - docs
  #   - llm-wiki

codex:
  command: codex app-server
  model: gpt-5.5
  reasoning_effort: high
  approval_policy: never
  # Sandbox trade-off — read before changing:
  #   `workspace-write` (default below) keeps codex confined to the worker
  #   workspace and is the safer choice for fresh clones / shared machines.
  #   When `after_create` symlinks host-repo dirs (kanban, prompt, ...) into
  #   the workspace, symphony's codex backend now scans those symlinks at
  #   start() and auto-injects `-c sandbox_workspace_write.writable_roots`
  #   so writes through them succeed without widening the sandbox. Wrapper
  #   scripts can read `$SYMPHONY_CODEX_WRITABLE_ROOTS` (os.pathsep-joined)
  #   and pass the same override to codex themselves.
  #   If you still see "쓰기 불가" / blocked-write loops, fall back to
  #   `danger-full-access` (trusted local dev only — git branch isolation
  #   still scopes the blast radius).
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
    Plan: ./docs/symphony-prompts/linear/stages/plan.md
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
