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
  after_create: |
    set -euo pipefail
    ISSUE_ID="$(basename "$PWD")"
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
    WORKTREE_PATH="$PWD"
    BRANCH="symphony/${ISSUE_ID}"
    cd "$HOST_REPO"
    BASE_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || git branch --show-current 2>/dev/null || true)"
    FEATURE_BASE_BRANCH="${SYMPHONY_FEATURE_BASE_BRANCH:-${BASE_BRANCH:-}}"
    MERGE_TARGET_BRANCH="${SYMPHONY_MERGE_TARGET_BRANCH:-${FEATURE_BASE_BRANCH:-${BASE_BRANCH:-}}}"
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
    # Link tracker-managed directories back to host so agent state
    # transitions are visible to Symphony's FileBoardTracker (which reads
    # board_root from the host repo, not from this worktree's checkout).
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
      # Hide host-owned symlink/junction roots from this worktree's git
      # status. Otherwise a reused workspace can record kanban as a 120000
      # symlink blob and delete the real tree on the ticket branch.
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
    # Honors any pre-commit hooks in the host repo — if they fail, this
    # turn's snapshot fails and the next turn picks up where files are.
    MSG="$(sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' .symphony/commit-message.txt 2>/dev/null || true)"
    [ -n "$MSG" ] || MSG="turn $(date -u +%FT%TZ)"
    case "$MSG" in wip:*) COMMIT_MSG="$MSG" ;; *) COMMIT_MSG="wip: $MSG" ;; esac
    LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
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
