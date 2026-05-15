---
tracker:
  kind: file
  board_root: ./kanban_smoke
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Cancelled, Blocked]
  archive_state: Archive
  archive_after_days: 0
  state_descriptions:
    Todo: "Triage; route to In Progress"
    "In Progress": "Implement; transition to Done"
    Done: "Completed"

polling:
  interval_ms: 5000

workspace:
  root: ./tmp_workspaces/smoke

hooks:
  # Link the host board into the workspace so the agent can update the
  # ticket file via `./kanban_smoke/<ID>.md` (inside its cwd). Without
  # this, the agent's Write to `../../kanban_smoke/<ID>.md` is rejected by
  # Claude Code's sandbox AND the relative path is wrong (workspace lives
  # under `tmp_workspaces/smoke/<ID>/`, board is two levels up at the
  # repo root, not one). See `_symphony_link_dir` for the Windows-aware
  # symlink-or-junction logic.
  after_create: |
    set -euo pipefail
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
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
    _symphony_link_dir "kanban_smoke" "$HOST_REPO/kanban_smoke"
  before_run:   ": noop"
  after_run:    "echo run done"

agent:
  kind: claude
  max_concurrent_agents: 1
  max_turns: 3
  max_total_turns: 6
  max_concurrent_agents_by_state:
    Todo: 1
    "In Progress": 1

claude:
  # `--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban_smoke"` lets Claude Code
  # write through the after_create-installed junction. Without it, the
  # agent silently fails the ticket update because the resolved path
  # lands outside its cwd. Symphony injects SYMPHONY_WORKFLOW_DIR before
  # spawning each turn (see Orchestrator.start).
  command: 'claude -p --output-format stream-json --verbose --permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR/kanban_smoke"'
  resume_across_turns: true
  turn_timeout_ms: 600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

server:
  port: 9998

tui:
  language: en
  max_cards_per_column: 6

---

You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.

This is a mini-app smoke test scenario. The ticket body describes a small task to perform inside this isolated workspace.

Rules:
- Stay inside the workspace cwd. Do not touch parent directories.
- The host board is linked into the workspace as `./kanban_smoke/`. When
  work is done, rewrite `./kanban_smoke/{{ issue.identifier }}.md` to set
  `state: Done` and append a `## Resolution` section summarizing what you did.
- Keep responses short. One or two short turns is enough.
- No emojis.
