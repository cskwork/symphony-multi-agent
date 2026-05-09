# symphony-multi-agent

Multi-agent fork of [OpenAI's Symphony reference implementation](https://github.com/openai/symphony).

The upstream service polls a tracker and runs a **Codex** coding-agent session
inside a per-issue workspace. This fork adds:

1. A pluggable **AgentBackend** layer that supports three CLI agents behind
   one interface:
   - **Codex** — `codex app-server` (JSON-RPC stdio, multi-turn) — original
   - **Claude Code** — `claude -p --output-format stream-json --verbose`
     (NDJSON events, per-turn subprocess with `--resume`)
   - **Gemini** — `gemini -p` (one-shot per turn, stdin prompt → stdout result)
2. A **Jira-style CLI Kanban TUI** built on `rich` that replaces the upstream
   server-rendered HTML dashboard. Columns are tracker states; cards show the
   active agent, turn count, last event, and accumulated tokens.

The orchestrator, scheduler, retry policy, workspace manager, tracker layer,
and prompt renderer are unchanged from upstream.

## Pick an agent

Set `agent.kind` in your `WORKFLOW.md`:

```yaml
agent:
  kind: claude          # codex | claude | gemini

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000
```

Each backend reads its own block (`codex`, `claude`, `gemini`); only the one
matching `agent.kind` is used at runtime. The Codex `linear_graphql` client
tool is only advertised when `agent.kind=codex`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Make the relevant CLI available on `$PATH`:

| `agent.kind` | required CLI on `$PATH` |
|--------------|------------------------|
| `codex`      | `codex` (with `app-server` subcommand) |
| `claude`     | `claude` (Claude Code) |
| `gemini`     | `gemini` (Gemini CLI)  |

## Run

### Background service + JSON API

```bash
symphony ./WORKFLOW.md --port 8080
```

JSON API endpoints (unchanged from upstream):

| Method | Path                       | Purpose                                      |
|--------|----------------------------|----------------------------------------------|
| GET    | `/api/v1/state`            | Snapshot — running, retrying, totals, limits |
| GET    | `/api/v1/<identifier>`     | Issue detail (404 with structured error)     |
| POST   | `/api/v1/refresh`          | Coalesced trigger of poll + reconcile        |

The HTML dashboard at `/` from upstream has been removed in this fork; the
primary UI is the CLI Kanban below.

### CLI Kanban TUI (primary UI)

```bash
symphony tui ./WORKFLOW.md
# equivalent
symphony ./WORKFLOW.md --tui
```

Columns are tracker states (`active_states` first, then `terminal_states`).
Cards display issue identifier + title, priority, labels (or blockers), and a
runtime indicator:

- **● green** — currently running, shows `turn N`, last event, accumulated tokens
- **↻ yellow** — in retry queue, shows `retry #N` and the last error
- **✓ green** — completed in this session

Quit with `Ctrl-C` (clean shutdown drains active workers).

### File-based Kanban tracker

If you don't have Linear, use the local Markdown-file tracker (unchanged from
upstream):

```yaml
tracker:
  kind: file
  board_root: ./kanban
```

```bash
symphony board init ./kanban
symphony board new DEV-1 "Title" --priority 2
symphony tui ./WORKFLOW.md
```

## Layout

```
src/symphony/
  backends/
    __init__.py        AgentBackend Protocol + factory + normalized events
    codex.py           Codex JSON-RPC stdio backend (was upstream agent.py)
    claude_code.py     Claude Code stream-json backend
    gemini.py          Gemini one-shot backend
  agent.py             back-compat shim re-exporting backends.* symbols
  workflow.py          typed config — adds AgentConfig.kind + Claude/Gemini configs
  orchestrator.py      unchanged scheduler; uses build_backend() factory
  tui.py               rich Kanban TUI (replaces server.py dashboard)
  server.py            JSON API only (HTML root removed)
  cli.py               adds `tui` subcommand / `--tui` flag
  ...
```

## Tests

```bash
pytest -q
```

73 tests pass: the upstream conformance suite plus 11 backend unit tests
covering the factory, event normalization, Claude usage accumulation, and
Gemini session synthesis. Subprocess-driven integration tests against real
CLIs are intentionally not in CI — run them locally.

## Design notes

### Why three different lifecycles behind one Protocol?

- **Codex** opens one `app-server` subprocess per issue and speaks JSON-RPC
  for the lifetime of the worker — multi-turn within one process.
- **Claude Code** has no persistent server; sessions are tracked by ID. Each
  `run_turn` spawns a fresh `claude -p` and uses `--resume <session-id>` from
  turn 2 onward.
- **Gemini CLI** is one-shot per invocation with no native session model.
  Each turn is independent; we synthesize a `gemini-<uuid>` session id so the
  orchestrator's bookkeeping stays consistent.

The `AgentBackend` Protocol hides these differences. The orchestrator only
sees normalized events (`session_started`, `turn_completed`, `turn_failed`,
…) and the latest usage / rate-limit snapshots.

### What the TUI deliberately does not do

- No mouse interaction, no card drag-drop. It is a read-only board.
- No drill-down view — use `/api/v1/<identifier>` for raw issue debug.
- No log tailing — agent output goes to stderr/log files, not the TUI.

This matches the upstream design philosophy: the orchestrator is the source
of truth, the UI is a thin observer.

## What is *not* implemented

Inherited from upstream:

- SSH worker extension — single-host only.
- Persistent retry queue across process restarts.
- Tracker adapters beyond Linear and the file-based Kanban.
- First-class tracker write APIs in the orchestrator. Ticket writes still
  happen through the agent (`linear_graphql` for Codex, direct file edits for
  the file-based Kanban).

Fork-specific gaps:

- Claude Code's mid-turn streaming usage events are read but not surfaced;
  the terminal `result` event is the source of truth for token totals.
- Gemini token usage is not reported by the CLI in stable form, so totals
  stay at zero for that backend.
- Multi-turn continuity for Gemini is not supported (no session protocol
  exists in the CLI). Each `run_turn` is independent.
