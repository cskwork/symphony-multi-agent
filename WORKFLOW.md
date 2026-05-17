---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, Plan, "In Progress", Review, QA, Learn]
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
    Plan: "Lock implementation plan"
    "In Progress": "TDD loop, draft branch"
    Review: "Read diff, fix CRITICAL/HIGH/MEDIUM"
    QA: "pytest -q + real-CLI smoke"
    Learn: "Distill learnings, update llm-wiki"
    Done: "As-Is -> To-Be report"
    Archive: "Auto-archived after 30 days idle"

polling:
  interval_ms: 30000

# Wiki integrity sweep — runs `symphony wiki-sweep` automatically after every
# Nth `Done` transition. The sweep flags duplicate slugs, INDEX↔file orphans,
# missing files, and entries older than 90 days (idempotently appends
# ` (stale?)` to their INDEX row). Set `sweep_every_n: 0` to disable. The
# operator can also run `symphony wiki-sweep --root docs/llm-wiki` manually.
wiki:
  sweep_every_n: 10
  root: ./docs/llm-wiki

workspace:
  root: ~/symphony_workspaces
  reuse_policy: refresh

hooks:
  # The workspace starts empty. Attach it as a git worktree of the host
  # repo on a per-ticket symphony/<ID> branch so the host working tree
  # stays untouched while the agent works. Product changes and docs/
  # artefacts stay on the feature branch. The default Learn gate merges
  # that feature branch into the target branch before the ticket can move
  # to Done.
  #
  # IMPORTANT: only host-owned board roots such as kanban/ are symlinked
  # back to the host repo. docs/ stays branch-local so review/QA evidence
  # and wiki updates are reviewable deliverables.
  # Body extracted to scripts/symphony-setup-worktree.sh so the hook stays
  # one line and the worktree-setup logic is versioned, lintable, and
  # testable on its own. Edit the script to change worktree provisioning,
  # symlink behaviour, or venv install. SYMPHONY_WORKFLOW_DIR points at
  # this WORKFLOW.md's directory; the script expects to be invoked from
  # the ticket workspace cwd that Symphony pre-creates.
  after_create: |
    bash "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"
  before_run: |
    # NEVER `git reset --hard` inside a worktree — discards mid-run work.
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
      echo "run finished at $(date -u +%FT%TZ) (no changes)"
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
      # SYMPHONY_REWIND_SCOPE is JSON: extract `"file": "..."` values without
      # a jq dependency. POSIX sed; tolerant of escaped quotes inside `fix`.
      SCOPE_FILES="$(printf '%s' "$SYMPHONY_REWIND_SCOPE" \
        | tr ',' '\n' \
        | sed -n 's/.*"file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    fi
    # `IFS=$'\n'` is bash-only; the POSIX-portable equivalent is the
    # printf trick below (command substitution strips a trailing newline,
    # so we append a sentinel byte and strip it back off). Inline literal
    # newlines also break the YAML literal block — the closing quote at
    # column 0 prematurely terminates the `|` scalar.
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
    LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
    # On amend, preserve markers the previous turn set AND fold in markers
    # detected this turn so a later prod-only turn still gets `[no-test]`.
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
    if [ "${LAST#wip:}" != "$LAST" ]; then
      # Re-derive the timestamp portion from the existing subject (drop
      # any prefixes, then drop the leading `wip: `). Falls back to a
      # fresh stamp when parsing fails.
      STRIPPED="$LAST"
      while :; do
        case "$STRIPPED" in
          "[no-test]"*) STRIPPED="${STRIPPED#"[no-test]"}" ;;
          "[scope-expand]"*) STRIPPED="${STRIPPED#"[scope-expand]"}" ;;
          " "*) STRIPPED="${STRIPPED# }" ;;
          *) break ;;
        esac
      done
      case "$STRIPPED" in
        wip:*) STRIPPED="${STRIPPED#wip:}" ;;
      esac
      STRIPPED="${STRIPPED# }"
      [ -n "$STRIPPED" ] || STRIPPED="turn $(date -u +%FT%TZ)"
      SUBJECT="wip: $STRIPPED"
      if [ -n "$MERGED_PREFIX" ]; then
        SUBJECT="${MERGED_PREFIX} ${SUBJECT}"
      fi
      git -c user.email=symphony@local -c user.name=symphony \
          commit --amend -m "$SUBJECT" >/dev/null 2>&1 || true
    else
      SUBJECT="wip: turn $(date -u +%FT%TZ)"
      if [ -n "$PREFIX" ]; then
        SUBJECT="${PREFIX} ${SUBJECT}"
      fi
      git -c user.email=symphony@local -c user.name=symphony \
          commit -m "$SUBJECT" >/dev/null 2>&1 || true
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
  # after_done: fires once when the ticket reaches `Done` (after the
  # squash commit lands). Receives the same env as the other hooks plus
  # `SYMPHONY_ISSUE_ID` and `SYMPHONY_ISSUE_TITLE`. Cwd is the ticket's
  # worktree, still attached. Lenient — failures only log a warning and
  # do not block cleanup. Uncomment to push the branch and open a PR
  # (requires `gh` and a writeable `origin`).
  #
  # after_done: |
  #   set -uo pipefail
  #   HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?}"
  #   ISSUE_ID="${SYMPHONY_ISSUE_ID:?}"
  #   TITLE="${SYMPHONY_ISSUE_TITLE:-}"
  #   BRANCH="symphony/${ISSUE_ID}"
  #   git -C "$HOST_REPO" push -u origin "$BRANCH" 2>/dev/null || true
  #   command -v gh >/dev/null 2>&1 || exit 0
  #   gh -R "$(git -C "$HOST_REPO" remote get-url origin 2>/dev/null)" \
  #     pr create --head "$BRANCH" --base main \
  #     --title "${ISSUE_ID}: ${TITLE}" \
  #     --body "Auto-opened by Symphony after ${ISSUE_ID} reached Done. \
  #            See docs/${ISSUE_ID}/ for evidence." 2>/dev/null || true

agent:
  kind: claude
  max_concurrent_agents: 1
  max_turns: 20
  max_retry_backoff_ms: 300000
  # Soft cap on stage rewinds (Review→In Progress + QA→In Progress
  # combined). Symphony increments this counter at phase-transition time;
  # on the (max_attempts+1)th rewind, it moves the ticket to Blocked
  # instead of starting another In Progress pass. Set to 0 to disable.
  max_attempts: 3
  # File-board optimization: obvious Todo tickets with Acceptance Criteria
  # are routed to Explore by Symphony itself, saving a model turn. Bug tickets,
  # blocked tickets, and underspecified tickets still run the Todo prompt.
  auto_triage_actionable_todo: true
  max_concurrent_agents_by_state:
    Todo: 1
    Explore: 1
    Plan: 1
    "In Progress": 1
    Review: 1
    QA: 1
    Learn: 1
  max_total_tokens: 100000000
  max_total_tokens_by_state:
    "In Progress": 500000000
    QA: 500000000
  # Merge policy for the Learn -> Done gate. Learn must merge the
  # `symphony/<ID>` feature branch into the target branch before setting
  # Done. The post-Done auto-merge remains a best-effort fallback for older
  # prompts.
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

claude:
  # `--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"` extends Claude
  # Code's write scope to the host directories that after_create
  # junctioned into the worktree. Without these, the agent silently
  # fails to flip ticket state to Done because the resolved path lands
  # outside its cwd, and Symphony's tracker keeps re-dispatching it.
  # Symphony injects SYMPHONY_WORKFLOW_DIR before spawning each turn
  # (see Orchestrator.start).
  command: 'claude -p --output-format stream-json --verbose --permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"'
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

qa:
  # Boot recipe for As-Is/To-Be HTTP runs. The QA prompt prefers these
  # over re-discovering boot details per ticket. Leave any field blank
  # to fall back to the prompt's heuristics.
  boot:
    # Shell command that boots the API in the foreground. The QA agent
    # runs it twice (As-Is in a sibling worktree, To-Be in the current
    # worktree) on the two distinct ports below, with `SYMPHONY_QA_PORT`
    # exported. Use that variable to bind the port.
    command: ""
    # Optional health-check URL — `${PORT}` is replaced with the run's
    # port. The QA agent polls until 200 OK before sending payloads.
    health_url: ""
    # Two ports the QA agent binds, one per build. Pick free ports that
    # don't collide with anything on the host.
    asis_port: 8801
    tobe_port: 8802
    # Extra env vars merged on top of the inherited environment for
    # both runs. Keep secrets out of WORKFLOW.md — reference $VARs.
    env: {}
    # Optional docker-compose / docker-compose-like file to bring up
    # before booting. Tear-down is the QA agent's responsibility.
    compose_file: ""
  # Performance regression budget. The QA prompt records latency for
  # every payload on As-Is and To-Be; if To-Be exceeds As-Is by more
  # than `latency_factor` (e.g. 2.0 = 2× slower) on any payload, QA
  # fails with `## QA Failure`. Set to 0 to disable.
  regression_budget:
    latency_factor: 2.0
    # Absolute minimum As-Is latency (ms) before the factor applies —
    # avoids tripping on jittery sub-50ms responses.
    min_baseline_ms: 50

server:
  port: 9999

tui:
  language: en

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
