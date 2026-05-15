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
    else
      git worktree add "$WORKTREE_PATH" -b "$BRANCH"
    fi
    cd "$WORKTREE_PATH"
    # Record the fork point so commit_workspace_on_done can `git reset --soft`
    # back to it and squash all per-turn work into a single ticket commit.
    git config symphony.basesha "$(git rev-parse HEAD)"
    # Symlink shared directories back to host so agent edits are visible
    # to Symphony's file tracker (which reads from the host's board_root).
    for dir in kanban docs llm-wiki; do
      rm -rf "$dir"
      ln -s "$HOST_REPO/$dir" "$dir"
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
      "$PYTHON" -m venv .venv
      # `python -m pip` survives the venv-script path differences between
      # POSIX (`.venv/bin/pip`) and Windows (`.venv/Scripts/pip.exe`).
      .venv/*/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
        || .venv/bin/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
        || .venv/Scripts/python -m pip install --quiet -e '.[dev]' 2>/dev/null \
        || echo "after_create: pip install failed; agent will fall back to host python." >&2
    fi
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
    git add -A 2>/dev/null || true
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
  max_concurrent_agents: 3
  max_turns: 20
  max_retry_backoff_ms: 300000
  # Soft cap on stage rewinds (Review→In Progress + QA→In Progress
  # combined). The base prompt counts `## Review Findings` and
  # `## QA Failure` sections in the ticket body; on the (max_attempts+1)th
  # turn that would otherwise rewind, the agent transitions to `Blocked`
  # with `## Budget Exceeded` instead. Set to 0 to disable the cap.
  max_attempts: 5
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
