---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, Plan, "In Progress", Review, QA, Learn]
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
  # Re-run after_create when reusing an existing ticket workspace. Use this
  # for workflows whose after_create installs host-board symlinks that
  # must stay fresh for state transitions to be visible to Symphony.
  reuse_policy: refresh

hooks:
  # Default: each ticket gets its own git worktree of the host repo on a
  # symphony/<ID> branch. Product changes and docs/ artefacts stay on that
  # branch; Symphony merges it back with an explicit --no-ff merge commit
  # when the ticket reaches Done.
  #
  # If your code lives in a *different* remote than the WORKFLOW.md repo,
  # replace the worktree commands with `git clone <remote> .` instead.
  # Body extracted to scripts/symphony-setup-worktree.sh — see C4 in
  # docs/improvements/workflow-v0.5.2.md. The script provisions the
  # `symphony/<ID>` worktree, records basesha/basebranch/mergetargetbranch,
  # symlinks (or Windows-junctions) `kanban/` back to the host so
  # FileBoardTracker sees state transitions, and primes a `.venv` when
  # `.[dev]` is installable from the host repo.
  after_create: |
    bash "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"
  before_run: |
    # NEVER `git reset --hard` inside a worktree — it discards in-progress
    # work between turns. Just refresh remotes; let the agent decide if/when
    # to rebase.
    set -uo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
    for dir in kanban; do
      source="$HOST_REPO/$dir"
      target="$PWD/$dir"
      [ -e "$source" ] || continue
      if [ ! -L "$target" ] && [ "${OS:-}" != "Windows_NT" ]; then
        echo "FAIL: workspace $dir must be a symlink to $source; got non-symlink $target" >&2
        exit 42
      fi
      if [ -L "$target" ] && [ "$(readlink "$target")" != "$source" ]; then
        echo "FAIL: workspace $dir points to $(readlink "$target"), expected $source" >&2
        exit 42
      fi
    done
    git fetch origin --quiet || true
  after_run: |
    # Per-turn commit-or-amend. The branch stays at the same number of
    # commits across turns (amends in place when HEAD is already a `wip:`
    # commit), but every completed turn is durably written to .git/objects
    # so even a hard crash (SIGKILL, host reboot) won't lose work. The
    # orchestrator squashes everything into a single `<ID>: <title>` commit
    # on exit — see auto_commit_on_done.
    set -uo pipefail
    git add -A -- . ':(exclude)kanban' ':(exclude).symphony' 2>/dev/null || true
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
        tests/*|docs/*|kanban/*|.symphony/*|*.md|LICENSE|LICENSE.*|NOTICE|CHANGELOG*|README*|AGENTS.md|GEMINI.md)
          : # carve-out: docs/license/wiki edits never count as production change
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
  max_total_tokens: 100000000
  max_total_tokens_by_state:
    "In Progress": 500000000
    QA: 500000000
  budget_exhausted_state: Blocked
  # Soft cap for Review/QA rewinds back into In Progress. Set 0 to disable.
  max_attempts: 3
  # Route obvious Todo tickets with Acceptance Criteria to Explore without
  # spending a model turn. Bug/blocked/ambiguous tickets still run Todo.
  auto_triage_actionable_todo: true
  max_concurrent_agents_by_state:
    Todo: 1
    Explore: 1
    Plan: 1
    "In Progress": 1
    Review: 1
    QA: 1
    Learn: 1
  # Snapshot the workspace into one git commit when a ticket reaches Done.
  # Reuses any enclosing git repo; otherwise runs `git init` first. Set to
  # false to opt out (e.g. workspace is a real repo you don't want touched).
  auto_commit_on_done: true
  # Merge policy for the Learn -> Done gate. Learn must merge the
  # `symphony/<ID>` feature branch into the target branch before setting
  # Done. kanban/ is a host-owned board link, so if it appears in the
  # feature-branch diff the merge is blocked as leaked workspace plumbing.
  # docs/ is intentionally branch-local and merges normally. The post-Done
  # auto-merge remains a best-effort fallback for older prompts.
  auto_merge_on_done: true
  # Branch/ref used as the start point for new `symphony/<ID>` feature
  # branches. Empty string = current host branch. The board viewer can
  # update this from its real git branch dropdown.
  feature_base_branch: ""
  # Branch to merge into after Learn. Empty string = same as feature base
  # branch/current host branch. The board viewer can update this too.
  auto_merge_target_branch: ""
  auto_merge_exclude_paths:
    - kanban

codex:
  command: codex app-server
  model: gpt-5.5
  reasoning_effort: high
  approval_policy: never
  # `workspace-write` is the safe default. When `after_create` symlinks
  # host repo dirs (kanban, prompt, ...) into the workspace, symphony's codex
  # backend auto-injects `-c sandbox_workspace_write.writable_roots=[...]`
  # for direct `codex ...` commands and exports the resolved targets via
  # `$SYMPHONY_CODEX_WRITABLE_ROOTS` (os.pathsep-joined) for wrapper
  # scripts to forward themselves. If blocked-write loops still appear,
  # fall back to `danger-full-access` (trusted local dev only).
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

claude:
  # `--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"` extends Claude
  # Code's write scope to the host directories that after_create
  # junctioned into the worktree. Without these, the agent silently
  # fails to flip ticket state to Done because the resolved path lands
  # outside its cwd, and Symphony's tracker keeps re-dispatching it.
  command: 'claude -p --output-format stream-json --verbose --permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"'

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
    Plan: ./docs/symphony-prompts/file/stages/plan.md
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
