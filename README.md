# symphony-multi-agent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Tests: 205 passing](https://img.shields.io/badge/tests-205%20passing-brightgreen.svg)](#tests)

> Drive any coding-agent CLI — Codex, Claude Code, Gemini, or Pi — from one
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
  agent=codex  tracker=linear  workflow=WORKFLOW.md  lang=en   running=2  retrying=1   │  tokens in=84,200 out=27,640 total=111,840
                                                                                       │  rate-limits=requests_remaining=4823, tokens_remaining=1.2M

╭── Todo (3) ──────╮ ╭── In Progress (2) ──╮ ╭── Review (1) ──╮ ╭── Done (2) ──╮ ╭── Archive (1) ──╮ ╭── detail ───────────────────────╮
│  DEMO-120  P1    │ │  DEMO-104  ●  P1    │ │  DEMO-122  P3  │ │  DEMO-088    │ │  DEMO-074       │ │  DEMO-104                       │
│  Migrate auth …  │ │  Fix race condi…    │ │  Doc: contri…  │ │  Drop dead-… │ │  Old experim…   │ │  Fix race condition in pagina…  │
│  #backend …      │ │  turn 4  20,180t    │ │  #docs         │ │  DEMO-091    │ │                 │ │                                 │
│                  │ │  Patched cursor…    │ ╰────────────────╯ │  Bump deps…  │ ╰─────────────────╯ │  state=In Progress              │
│  DEMO-111  ↻ P2  │ │                     │                    ╰──────────────╯                     │  runtime=running                │
│  Refactor cach…  │ │  DEMO-098  ●  P2    │                                                         │  turn=4                         │
│  retry #2  tur…  │ │  Add /api/sear…     │                                                         │  in=14,200  out=5,980           │
│                  │ │  turn 2  11,310t    │                                                         │  total=20,180                   │
│  DEMO-121  P2    │ │  Added token-bu…    │                                                         │  Patched cursor advance;        │
│  Wire feature …  │ ╰─────────────────────╯                                                         │  running test suite...          │
│  blocked by D…   │                                                                                 ╰─────────────────────────────────╯
╰──────────────────╯

q quit · r refresh · enter details · 1-9 zoom lane · t/T page lanes · d density · p detail-pane · L language · a archive · / filter · ?
```

</details>

A multi-agent fork of [OpenAI's Symphony reference implementation](https://github.com/openai/symphony).
Upstream polls a tracker (Linear or a local Markdown Kanban) and runs a Codex
session inside a per-issue workspace. This fork keeps that orchestrator and
adds:

1. A pluggable **AgentBackend** layer with four concrete adapters:
   - **Codex** — `codex app-server` (JSON-RPC stdio, multi-turn) — original
   - **Claude Code** — `claude -p --output-format stream-json --verbose`
     (NDJSON events, per-turn subprocess with `--resume`)
   - **Gemini** — `gemini -p ""` (one-shot per turn, stdin prompt → stdout result)
   - **Pi** — `pi --mode json -p ""` (JSONL events, per-turn subprocess with
     `--session` resume; supports Anthropic / OpenAI / Gemini / Bedrock backends
     under one CLI — see [pi.dev](https://pi.dev))
2. A **Jira-style CLI Kanban TUI** built on [Textual](https://textual.textualize.io)
   that replaces the upstream server-rendered HTML dashboard. Columns are
   tracker states; cards show the active agent, turn count, last event, and
   accumulated tokens. Cards are focusable, the mouse wheel scrolls each lane,
   and pressing `enter` on a card opens a full-detail modal.

The orchestrator, scheduler, retry policy, workspace manager, tracker layer,
and prompt renderer are unchanged from upstream — this fork is a thin layer
on top of a battle-tested orchestrator core.

## Pick an agent

Set `agent.kind` in your `WORKFLOW.md`:

```yaml
agent:
  kind: claude          # codex | claude | gemini | pi

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

pi:
  command: pi --mode json -p ""
  resume_across_turns: true
  turn_timeout_ms: 3600000
```

Each backend reads its own block (`codex`, `claude`, `gemini`, `pi`); only the
one matching `agent.kind` is used at runtime. The Codex `linear_graphql`
client tool is only advertised when `agent.kind=codex`.

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
| `pi`         | `pi` (Pi coding-agent — `npm i -g @earendil-works/pi-coding-agent` or `curl -fsSL https://pi.dev/install.sh \| sh`; sign in once via `pi` → `/login` (OAuth, credentials cached at `~/.pi/agent/auth.json`) — no env var needed) |

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
FAIL  hooks.after_create            contains placeholder 'my-org/my-repo' — every dispatch will fail with rc=128. Switch to the worktree default or replace with a real clone / `: noop`.
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

Four blocks matter for first-run sanity:

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
  # The shipped default attaches it as a `git worktree` of the host repo
  # on a `symphony/<ID>` branch — host working tree stays untouched.
  # Use `: noop` instead while you experiment without a host repo.
  after_create: |
    : noop                       # ← swap for the worktree default in WORKFLOW.file.example.md
  before_run: |
    : noop                       # runs before every agent turn
  after_run: |
    echo "run finished at $(date)"

prompts:
  # Symphony sends base plus only the file for the ticket's current state.
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
```

> ⚠ The shipped `WORKFLOW.example.md` / `WORKFLOW.file.example.md` default to
> attaching the per-ticket workspace as a **git worktree** of the host repo
> (the directory containing `WORKFLOW.md`) on a `symphony/<ID>` branch. The
> host working tree is never disturbed; merge results back with
> `git -C <host> merge symphony/<ID>` (or open a PR from that branch) when
> you're satisfied — explicit operator action, never automatic.
>
> If your code lives in a *different* remote than the WORKFLOW.md repo,
> swap the hook for `git clone <remote> .` instead. While experimenting
> without any repo, use `: noop`.

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
stage-specific prompt files configured by `WORKFLOW.md`.

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

## Per-ticket artefacts

Every artefact a ticket produces lives under `docs/<TICKET-ID>/<stage>/`. See [`docs/PIPELINE.md`](docs/PIPELINE.md#per-ticket-artefact-root) for the layout, what to commit, and the `${LLM_WIKI_PATH:-./llm-wiki}/` carve-out.

## Custom prompts

`WORKFLOW.md` can point at editable prompt files under `docs/`:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    Explore: ./docs/symphony-prompts/file/stages/explore.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
```

Symphony sends `base` plus only the prompt file for the ticket's current
state, keeping each turn smaller than the old all-stage prompt. If the
`prompts` block is absent, the inline body of `WORKFLOW.md` still works as
the legacy fallback.

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

Key bindings (also auto-listed in the footer):

| Key                | Action                                       |
|--------------------|----------------------------------------------|
| `q`                | Quit (drains active workers cleanly)         |
| `r`                | Force a refresh + re-poll the tracker        |
| `?`                | Show all key bindings as a notification      |
| `tab` / `shift+tab`| Move focus to next / previous card or lane   |
| `j` / `↓`          | Scroll focused lane down one row             |
| `k` / `↑`          | Scroll focused lane up one row               |
| `space` / `pgdn`   | Page down                                    |
| `b` / `pgup`       | Page up                                      |
| `g` / `home`       | Jump to top                                  |
| `G` / `end`        | Jump to bottom                               |
| `enter`            | Open the focused card's full-detail modal    |
| `esc` / `q`        | Close the modal (when one is open)           |

Mouse: clicking a card focuses it, the wheel scrolls its lane.

#### One-shot launchers

For developers who don't want to remember the full `symphony tui` invocation,
the repo ships two launcher scripts that prefer `.venv/bin/symphony` over
`PATH`, run `symphony doctor` first, then open the TUI in a new terminal
window:

```bash
./tui-open.sh                     # macOS / Linux — uses iTerm or Terminal.app
./tui-open.sh path/to/WORKFLOW.md # explicit workflow path
tui-open.bat                      # Windows — uses cmd /k
```

Both scripts abort the launch if `doctor` reports a FAIL so you do not paint
the alt-screen on top of unreadable preflight output.

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
    pi.py              Pi --mode json backend (per-turn subprocess, --session resume)
  agent.py             back-compat shim re-exporting backends.* symbols
  workflow.py          typed config — adds AgentConfig.kind + Claude/Gemini/Pi configs
  orchestrator.py      unchanged scheduler; uses build_backend() factory
  tui.py               Textual Kanban TUI (replaces server.py dashboard)
  server.py            JSON API only (HTML root removed)
  cli.py               adds `tui` subcommand / `--tui` flag
tui-open.sh            cross-platform launcher (macOS / Linux): doctor preflight + open TUI in a new terminal window
tui-open.bat           Windows equivalent
  ...
```

## Tests

```bash
pytest -q
```

205 tests pass (2 skipped): the upstream conformance suite plus the backend
unit tests covering the factory, event normalization, Claude / Pi usage
accumulation, Gemini session synthesis, and Pi failure-reason detection,
plus Textual `Pilot`-driven smoke tests for the TUI app. Subprocess-driven
integration tests against real CLIs are intentionally not in CI — run them
locally.

## Design notes

### Why four different lifecycles behind one Protocol?

- **Codex** opens one `app-server` subprocess per issue and speaks the
  current `codex app-server` JSON-RPC protocol (`initialize` + `thread/start`
  + `turn/start` + streamed `turn/completed` and `item/completed`
  notifications). Multi-turn within one process. Older `v2/initialize`-style
  releases are not supported — pin to `codex-cli ≥ 0.39` (current upstream).
- **Claude Code** has no persistent server; sessions are tracked by ID. Each
  `run_turn` spawns a fresh `claude -p` and uses `--resume <session-id>` from
  turn 2 onward.
- **Gemini CLI** is one-shot per invocation with no native session model.
  Each turn is independent; we synthesize a `gemini-<uuid>` session id so the
  orchestrator's bookkeeping stays consistent.
- **Pi** has no persistent server but auto-saves sessions to
  `~/.pi/agent/sessions/`. Each `run_turn` spawns a fresh `pi --mode json` and
  passes `--session <id>` from turn 2 onward. The session id is read from the
  first `{"type":"session"}` JSONL line; per-message `usage` is accumulated
  off `message_end` events, and `agent_end` is treated as the terminal event.
  Auth is delegated to Pi: the OAuth/API-key store at `~/.pi/agent/auth.json`
  populated by `/login` is inherited by the subprocess, so Symphony itself
  never handles credentials.

The `AgentBackend` Protocol hides these differences. The orchestrator only
sees normalized events (`session_started`, `turn_completed`, `turn_failed`,
…) and the latest usage / rate-limit snapshots.

### What the TUI does and does not do

The board is observer-only: cards move when the agent rewrites the underlying
ticket file (file tracker) or transitions the issue (Linear), never as a
direct UI action. That matches the upstream design philosophy — the
orchestrator is the source of truth and the UI is a thin reflection.

What you *can* do interactively:

- Focus any card with `tab` / `shift+tab` or by clicking it.
- Scroll a lane with the mouse wheel, `j` / `k`, or page keys.
- Open a focused card's full description in a modal with `enter`.

What is intentionally out of scope:

- **No card drag-drop.** Move tickets via `symphony board mv ID State`
  (file tracker) or in your tracker UI directly.
- **No agent-output log pane.** Agent stdout/stderr goes to the structured
  log; tail it with `tail -F log/symphony.log` in a side terminal.
- **No write actions to the tracker** beyond what the agent does itself.

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

The TUI is built on Will McGugan's [Textual](https://textual.textualize.io)
framework, with [rich](https://github.com/Textualize/rich) used directly for
text styling inside cards.

Pipeline stage rules adapt the evidence-first ideas of [cskwork/backend-dev-skills](https://github.com/cskwork/backend-dev-skills) (MIT).

## License

[Apache 2.0](LICENSE).
