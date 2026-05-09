# symphony-multi-agent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Tests: 73 passing](https://img.shields.io/badge/tests-73%20passing-brightgreen.svg)](#tests)

> Drive any coding-agent CLI — Codex, Claude Code, or Gemini — from one
> orchestrator, with a Jira-style Kanban board rendered straight in your
> terminal.

## What it looks like

`symphony tui ./WORKFLOW.md` opens a full-terminal Kanban board. Columns are
your tracker's states; cards show the active agent, turn count, last event,
and accumulated tokens. Live indicators: ● running, ↻ retry queued, ✓ done.

![symphony tui screenshot](docs/tui-screenshot.svg)

<details>
<summary>Plain-text version (for terminals viewing raw README)</summary>

```text
  symphony-multi-agent  agent=claude  tracker=file  workflow=WORKFLOW.md           running=2  retrying=1  generated_at
                                                                                                          2026-05-09T03:48:09Z

╭───────────────── Todo (3) ─────────────────╮      ╭───────────── In Progress (2) ──────────────╮
│    DEMO-120                                │      │    DEMO-104  ●                             │
│   Migrate auth middleware to async  P1     │      │   Fix race condition in pagination cursor  │
│   #backend  #tech-debt                     │      │     P1                                     │
│                                            │      │   turn 4  turn_completed  20,180 tok       │
│    DEMO-111  ↻                             │      │   Patched cursor advance; running tests... │
│   Refactor cache invalidation helper  P2   │      │                                            │
│   retry #2  turn_error: Turn timed out     │      │    DEMO-098  ●                             │
│                                            │      │   Add /api/search rate limiting  P2        │
│    DEMO-121                                │      │   turn 2  turn_completed  11,310 tok       │
│   Wire feature flag for new dashboard  P2  │      │   Added token-bucket middleware…           │
│   blocked by DEMO-098                      │      ╰────────────────────────────────────────────╯
╰────────────────────────────────────────────╯
╭──────────────── Review (1) ────────────────╮      ╭───────────────── Done (2) ─────────────────╮
│    DEMO-122                                │      │    DEMO-088   DEMO-091                     │
│   Doc: contributor onboarding guide  P3    │      │   chore work (deps bump, dead-code drop)   │
│   #docs                                    │      │   #chore                                   │
╰────────────────────────────────────────────╯      ╰────────────────────────────────────────────╯

  tokens  in=84,200  out=27,640  total=111,840   runtime=412.7s   rate-limits=requests_remaining=4823, tokens_remaining=1.2M
```

</details>

A multi-agent fork of [OpenAI's Symphony reference implementation](https://github.com/openai/symphony).
Upstream polls a tracker (Linear or a local Markdown Kanban) and runs a Codex
session inside a per-issue workspace. This fork keeps that orchestrator and
adds:

1. A pluggable **AgentBackend** layer with three concrete adapters:
   - **Codex** — `codex app-server` (JSON-RPC stdio, multi-turn) — original
   - **Claude Code** — `claude -p --output-format stream-json --verbose`
     (NDJSON events, per-turn subprocess with `--resume`)
   - **Gemini** — `gemini -p` (one-shot per turn, stdin prompt → stdout result)
2. A **Jira-style CLI Kanban TUI** built on `rich` that replaces the upstream
   server-rendered HTML dashboard. Columns are tracker states; cards show the
   active agent, turn count, last event, and accumulated tokens.

The orchestrator, scheduler, retry policy, workspace manager, tracker layer,
and prompt renderer are unchanged from upstream — this fork is a thin layer
on top of a battle-tested orchestrator core.

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

## Try it in 60 seconds (no agent CLI required)

Want to see the TUI move cards around before installing `codex`, `claude`,
or `gemini`? Use the bundled **mock backend** — it speaks the same JSON-RPC
protocol as Codex but does no real work, just simulates turns and emits
token-usage ticks.

```bash
git clone https://github.com/cskwork/symphony-multi-agent.git
cd symphony-multi-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# WORKFLOW.md pointed at the mock backend
cat > WORKFLOW.md <<'YAML'
---
tracker: { kind: file, board_root: ./kanban,
           active_states: [Todo, "In Progress"],
           terminal_states: [Done, Cancelled, Blocked] }
polling: { interval_ms: 5000 }
workspace: { root: ~/symphony_workspaces }
hooks:
  after_create: ": noop"
  before_run:   ": noop"
  after_run:    "echo done"
agent:  { kind: codex, max_concurrent_agents: 2, max_turns: 3 }
codex:  { command: python -m symphony.mock_codex }
server: { port: 9999 }
---
You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
YAML

symphony board init ./kanban
symphony board new TASK-1 "smoke test"
symphony tui ./WORKFLOW.md
```

Within ~5 seconds TASK-1 grows a green ● indicator in the **Todo** column,
with a turn counter and token totals climbing. Quit with `Ctrl-C` when
you've seen enough; then proceed to the real walkthrough below.

> Cards stay in their original column under the mock — only a real agent
> would rewrite `kanban/TASK-1.md` to move the card to **Done**. The mock
> exists to prove the orchestrator → backend → workspace → hooks pipeline
> end-to-end without an LLM call.

> Tunables for the mock: `SYMPHONY_MOCK_TURN_SECONDS=12`,
> `SYMPHONY_MOCK_FAIL_EVERY_N_TURNS=3`, etc. — see `src/symphony/mock_codex.py`.

---

## Preflight — `symphony doctor`

Before launching, sanity-check your setup:

```bash
symphony doctor ./WORKFLOW.md
```

Output (one line per check):

```
PASS  server.port=9999              127.0.0.1:9999 is free
PASS  agent.kind=claude             claude → /usr/local/bin/claude
FAIL  hooks.after_create            contains placeholder 'my-org/my-repo' — every dispatch will fail with rc=128. Replace with a real clone target or `: noop`.
PASS  workspace.root=~/symphony_workspaces  exists and is writable
PASS  tracker.board_root            ./kanban (3 tickets)
```

Exit code is `0` when all checks pass, `1` if any FAIL, `2` if `WORKFLOW.md`
itself can't be loaded. The doctor catches the most common first-run
failures in one pass: port collision, missing CLI on `$PATH`, the shipped
placeholder clone URL, unwritable workspace, missing board directory.

---

## Quickstart — your first task end-to-end

This walks from a clean clone to a running ticket, using the file-based
tracker and Claude Code as the agent.

### 1. Initialize the board

```bash
symphony board init ./kanban
# → initialized board at ./kanban, sample ticket DEMO-001.md
```

Each ticket is one Markdown file with YAML frontmatter at `kanban/<ID>.md`.
The orchestrator only **reads** ticket files; the agent **writes** them when
it transitions state.

### 2. Author `WORKFLOW.md`

Use the **file-tracker** example (the other one, `WORKFLOW.example.md`,
points at Linear and needs an API key):

```bash
cp WORKFLOW.file.example.md WORKFLOW.md
```

Three blocks matter for first-run sanity:

```yaml
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Cancelled, Blocked]

workspace:
  root: ~/symphony_workspaces

hooks:
  # Each ticket gets its own workspace at workspace.root/<ID>.
  # after_create runs once when that workspace is created.
  after_create: |
    : noop                       # ← replace with `git clone …` for real work
  before_run: |
    : noop                       # runs before every agent turn
  after_run: |
    echo "run finished at $(date)"
```

> ⚠ The shipped `WORKFLOW.md` uses `git clone --depth=1 git@github.com:my-org/my-repo.git .`
> as a placeholder. If left unchanged, **every dispatch fails immediately**
> on the SSH clone (`returncode=128`, `worker_exit reason=error`). Either
> point it at a real repo or use `: noop` while you experiment.

### 3. Add a ticket

```bash
symphony board new TASK-1 "Fix flaky pagination test" \
  --priority 2 \
  --labels backend,test \
  --description "tests/test_pagination.py::test_cursor_advance is flaky on CI."
# → created kanban/TASK-1.md
```

Inspect:

```bash
symphony board ls                    # all tickets
symphony board ls --state Todo       # filter by state
symphony board show TASK-1           # full body
```

### 4. Launch the TUI

```bash
symphony tui ./WORKFLOW.md
```

Within one poll tick (`polling.interval_ms`, default 30s) the orchestrator
dispatches a worker, the card grows a green ● indicator (with turn counter
and token totals), and the agent runs. On success the agent rewrites
`kanban/TASK-1.md` to set `state: Done` and append a `## Resolution`
section — that file edit is what moves the card from the **Todo** column
into **Done**. Quit with `Ctrl-C`.

> Cards are placed in columns based on the ticket file's `state` field
> (`tui.py` reads it on each tick). The green ● indicator is overlaid on
> top of the card and does **not** change which column it sits in. So a
> running ticket stays in **Todo** until the agent itself rewrites the
> file — that's by design (the orchestrator only reads ticket files; the
> agent owns writes).

> The TUI needs a real terminal (TTY). If you launch it from a script /
> background process / non-interactive shell, the process exits silently —
> always run it in a foreground terminal.

### 5. Inspect the result

```bash
symphony board show TASK-1               # the agent's ## Resolution lives in the body
ls ~/symphony_workspaces/TASK-1          # workspace it operated in
```

Symphony writes structured logs to **stderr only**. To keep them around,
redirect at launch:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
# or, while running headless:
symphony ./WORKFLOW.md --port 9999 2>&1 | tee -a log/symphony.log
```

Then `tail -F log/symphony.log` works.

### 6. Move tickets manually (rare)

```bash
symphony board mv TASK-1 Blocked         # forces a state transition
```

The orchestrator re-evaluates on the next poll tick. Manual transitions are
for unsticking — normally the agent transitions tickets itself per the
prompt instructions in `WORKFLOW.md`.

### How dispatch works in one diagram

```
┌────────────┐    poll      ┌──────────────┐    matches active_states
│  kanban/   │  ─────────▶  │ Orchestrator │  ─────────────────────────┐
│  *.md      │   30s tick   │ (scheduler)  │                            │
└────────────┘              └──────────────┘                            ▼
      ▲                            │                          ┌──────────────────┐
      │                            │ creates workspace        │  Workspace       │
      │ agent writes               ▼                          │  ~/sym…/TASK-1   │
      │ ## Resolution     ┌──────────────────┐                │  + after_create  │
      │ + state: Done     │  AgentBackend    │  ◀────────────│    hook ran      │
      └───────────────────│  (codex/claude/  │                └──────────────────┘
                          │   gemini)        │                          │
                          │  per-turn loop   │  before_run hook ──▶ turn(s)
                          └──────────────────┘                          │
                                                                        ▼
                                                                  after_run hook
```

---

## Run

### Background service + JSON API

```bash
symphony ./WORKFLOW.md --port 9999
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

## Contributing

PRs welcome. Before opening one:

```bash
pip install -e ".[dev]"
pytest -q          # must stay green
```

Backend adapters live under `src/symphony/backends/`. Adding a new agent
(e.g. an Ollama-driven local model) means:

1. implementing the `AgentBackend` Protocol in a new module,
2. registering it in `build_backend()` (`src/symphony/backends/__init__.py`),
3. adding a `<kind>Config` dataclass to `workflow.py` and threading it
   through `build_service_config` + `validate_for_dispatch`,
4. extending `SUPPORTED_AGENT_KINDS`.

The bar for upstreaming a backend is: passes the existing factory + event
normalization tests, doesn't bleed protocol-specific types into the
orchestrator, and ships a default `<kind>` block in `WORKFLOW.example.md`.

## Acknowledgements

This project is built on top of OpenAI's
[Symphony](https://github.com/openai/symphony) reference implementation. The
upstream Apache-2.0 licensed work provides the orchestrator, the scheduler,
and the workspace lifecycle that make this fork possible. See `NOTICE` for
attribution details.

The TUI uses Will McGugan's [rich](https://github.com/Textualize/rich)
library for terminal rendering.

## License

[Apache 2.0](LICENSE).
