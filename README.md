# Symphony — reference implementation (SPEC v1)

A Python implementation of the
[OpenAI Symphony service specification](https://github.com/openai/symphony/blob/main/SPEC.md).

Symphony is a long-running automation service that polls an issue tracker
(Linear in v1), creates an isolated per-issue workspace, and runs a Codex
coding-agent session inside that workspace.

> The orchestrator is a scheduler/runner and tracker reader. Ticket writes
> (state transitions, comments, PR links) are performed by the coding agent
> using tools available in the workflow/runtime environment.

## Status

- All §18.1 *Core Conformance* items implemented.
- §13.7 OPTIONAL HTTP server extension implemented.
- §10.5 OPTIONAL `linear_graphql` client-side tool advertised to the agent.
- Pluggable tracker layer with two adapters out of the box:
  - `tracker.kind: linear` — Linear GraphQL (§11.2)
  - `tracker.kind: file`   — local file-based Kanban (one Markdown file per
    ticket with YAML front matter)
- §A SSH worker extension *not* implemented (local execution only).

## Trust posture (§15.1)

This implementation targets a **trusted developer machine**:

- Approval: agent approval requests are **auto-approved** (high-trust example
  in §10.5). To tighten this, set `codex.approval_policy` and remove the
  auto-approval handler in `agent.py::_handle_approval`.
- Sandbox: per-turn sandbox policy is taken from `codex.turn_sandbox_policy`.
  Default unless explicitly set is whatever the targeted Codex app-server
  defaults to.
- User-input-required turns are treated as **failure** (§10.5 example).
- Workflow hooks run via `bash -lc` inside the workspace directory; treat
  hooks as fully trusted configuration.
- Workspace path containment (§9.5) is enforced before launching the agent
  and before workspace removal.
- API tokens are redacted from log lines and never echoed back.

## Layout

```
src/symphony/
  errors.py            §5.5 §10.6 §11.4 typed errors
  logging.py           §13 structured key=value logger
  issue.py             §4.1.1 §4.2 normalized Issue model
  workflow.py          §5 §6 WORKFLOW.md loader + typed config + reload
  prompt.py            §5.4 §12 strict Liquid-semantics renderer
  tracker.py           §11 TrackerClient protocol + adapter factory
  tracker_linear.py    §11 Linear GraphQL adapter
  tracker_file.py      §11 File-based Kanban adapter
  workspace.py         §9 workspace manager + hooks + safety invariants
  agent.py             §10 Codex app-server JSON-RPC stdio client
  orchestrator.py      §7 §8 §16 single-authority state machine
  server.py            §13.7 optional HTTP observability extension
  cli.py               §17.7 CLI entry point
  board_cli.py         `symphony board ...` helper for file-based tracker
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

1. Place a `WORKFLOW.md` in the directory you run Symphony from
   (or pass an explicit path).
2. Provide a Linear API token, e.g. `export LINEAR_API_KEY=lin_...`.
3. Make `codex` available on `$PATH` so the configured `codex.command`
   resolves.

```bash
symphony ./WORKFLOW.md --port 8080
# or
python -m symphony ./WORKFLOW.md
```

`WORKFLOW.example.md` in this repo is a complete Linear reference workflow.
`WORKFLOW.file.example.md` is the equivalent for the file-based Kanban.

## File-based Kanban (`tracker.kind: file`)

If you do not have Linear or want a fully local Kanban, set:

```yaml
tracker:
  kind: file
  board_root: ./kanban       # default: ./board next to WORKFLOW.md
  active_states: [Todo, In Progress]
  terminal_states: [Done, Cancelled]
```

Each ticket is one Markdown file under `board_root`:

```markdown
---
id: DEV-001
title: Fix login bug
state: Todo
priority: 2
labels: [backend, bug]
blocked_by:
  - identifier: DEV-099
    state: Todo
created_at: 2026-05-08T10:00:00Z
updated_at: 2026-05-08T10:00:00Z
---

Description body in Markdown.
```

Symphony reads the directory on every poll tick. The coding agent updates
ticket state by rewriting its front matter via its built-in shell/file tools
— no special API tool required.

### `symphony board` helper

```bash
symphony board init ./kanban                    # create the directory + sample
symphony board ls [--state Todo]                # list tickets
symphony board new DEV-1 "Title" --priority 2   # create a ticket
symphony board mv  DEV-1 "In Progress"          # change state
symphony board show DEV-1                       # print ticket
```

The helper auto-discovers `board_root` from `./WORKFLOW.md`. Pass `--workflow
PATH` or `--root PATH` to override.

## HTTP API (extension §13.7)

When `--port` is set or `server.port` is in the workflow front matter:

| Method | Path                       | Purpose                                       |
|--------|----------------------------|-----------------------------------------------|
| GET    | `/`                        | Minimal HTML dashboard                        |
| GET    | `/api/v1/state`            | Snapshot — running, retrying, totals, limits  |
| GET    | `/api/v1/<identifier>`     | Issue detail (404 with structured error)      |
| POST   | `/api/v1/refresh`          | Coalesced trigger of poll + reconcile         |

Errors use `{"error":{"code":"...","message":"..."}}`. Wrong methods on
defined routes return `405`.

## Tests

```bash
pytest -q
```

The deterministic conformance suite exercises §17.1 (workflow/config),
§17.2 (workspace + safety), §17.4 (dispatch eligibility/sort/blockers),
§17.6 (logging redaction + sink-failure resilience), and §17.7 (CLI startup).

A **Real Integration Profile** (§17.8) is intentionally not in CI — those
checks need a real `LINEAR_API_KEY` and a Codex binary. Run them locally.

## What is *not* implemented

- §A SSH worker extension — single-host only.
- §13.7 dashboard is server-rendered HTML; there is no client-side SPA.
- Persistent retry queue across process restarts (§18.2 TODO).
- Tracker adapters beyond Linear and the file-based Kanban (§18.2 TODO).
- First-class tracker write APIs in the orchestrator (§18.2 TODO). Ticket
  writes still happen through the agent — `linear_graphql` for Linear, or
  direct file edits for the file-based Kanban.

## Codex app-server protocol notes

§10 designates the targeted Codex app-server documentation as the source of
truth for protocol shape. This implementation uses JSON-RPC line framing over
stdio and best-effort method names defined in `agent.py` (`METHOD_*`
constants). If the targeted Codex version uses different method names, those
constants are the only edits required.
