---
name: using-symphony
description: Use when the user wants to dispatch coding agents (Codex / Claude Code / Gemini) against a Kanban board via this `symphony-multi-agent` repo — adding/listing/transitioning tickets, launching the TUI, inspecting orchestrator state, or diagnosing dispatch failures. Triggers on phrases like "add a symphony task", "run symphony", "dispatch this ticket", "symphony board", "WORKFLOW.md", "symphony tui won't start", "ticket failed with worker_exit".
---

# Using Symphony

Symphony is a polling orchestrator that takes Kanban tickets and runs a
coding-agent CLI (Codex, Claude Code, or Gemini) against each one in an
isolated per-ticket workspace. This skill covers the operator's day-to-day:
authoring tickets, launching the orchestrator, and triaging failures.

> Always read `WORKFLOW.md` and one or two `kanban/*.md` files first to
> ground recommendations in the project's actual config — settings vary
> across forks.

## Mental model in 30 seconds

```
WORKFLOW.md  ─▶  Orchestrator  ─poll─▶  kanban/*.md  ─dispatch─▶  AgentBackend
   (config)                  (every                                (codex |
                              polling.                              claude |
                              interval_ms)                          gemini)
                                                                        │
                                                                        ▼
                                                            workspace.root/<ID>
                                                            (after_create hook
                                                             ran once here)
                                                                        │
                                                              turn loop with
                                                              before_run / after_run
                                                              hooks per turn
                                                                        │
                                                                        ▼
                                                  Agent edits kanban/<ID>.md
                                                  → state: Done + ## Resolution
```

Key invariants:
- The **orchestrator only reads** ticket files. It never writes them.
- The **agent writes** ticket files (via its filesystem tool) to transition
  state. That's how a ticket moves to `Done`.
- Each ticket runs in its own **workspace directory** under `workspace.root`
  (default `~/symphony_workspaces/<ID>`). Hooks run inside that directory.

## Try it without an agent CLI installed

`src/symphony/mock_codex.py` ships a JSON-RPC mock that the codex backend
can drive. Use it to demo or smoke-test without `codex` / `claude` /
`gemini` on `$PATH`:

```yaml
agent:
  kind: codex
codex:
  command: python -m symphony.mock_codex
```

Tunables (env vars, see the file's docstring):

| Var                                 | Default | Effect                          |
|-------------------------------------|---------|---------------------------------|
| `SYMPHONY_MOCK_TURN_SECONDS`        | 12      | total turn duration             |
| `SYMPHONY_MOCK_TICK_SECONDS`        | 2       | token-usage tick interval       |
| `SYMPHONY_MOCK_TOKENS_PER_TICK`     | 250     | tokens added per tick           |
| `SYMPHONY_MOCK_FAIL_EVERY_N_TURNS`  | 0       | force the Nth turn to fail      |
| `SYMPHONY_MOCK_MAX_TURNS`           | 0       | stop accepting turns after N    |

This is the fastest way to verify orchestrator + TUI wiring before
investing in a real backend setup.

## Common operations

### Add a ticket

```bash
symphony board new TASK-1 "Fix flaky pagination test" \
  --priority 2 \
  --labels backend,test \
  --description "tests/test_pagination.py::test_cursor_advance is flaky on CI."
```

Identifier rules: any string the user wants (`TASK-1`, `BUG-007`, `PROD-2026-05-09`).
Convention is `<PREFIX>-<NUMBER>` but it is not enforced. The file lands at
`kanban/<ID>.md`.

### List tickets

```bash
symphony board ls                       # everything
symphony board ls --state Todo          # one column
symphony board ls --state "In Progress" # quote multi-word states
```

### Inspect a ticket

```bash
symphony board show TASK-1
```

### Move a ticket (manual override)

```bash
symphony board mv TASK-1 Blocked
```

Use this only to unstick — the agent normally transitions tickets itself.

### Launch the TUI

```bash
symphony tui ./WORKFLOW.md
```

**TTY required.** Background processes / scripts cannot render the TUI; the
process will exit silently. If you (the agent) want to start Symphony for the
user from a non-interactive shell, instead start it headless and use the JSON
API:

```bash
symphony ./WORKFLOW.md --port 9999
curl -s http://127.0.0.1:9999/api/v1/state | jq
curl -s http://127.0.0.1:9999/api/v1/TASK-1 | jq
curl -s -X POST http://127.0.0.1:9999/api/v1/refresh   # force a poll tick
```

### Stop a stuck server

```bash
lsof -ti :9999 | xargs -r kill        # SIGTERM (graceful)
lsof -ti :9999 | xargs -r kill -9     # only if SIGTERM doesn't take
```

## Authoring `WORKFLOW.md`

`WORKFLOW.md` is a hybrid file:
- **YAML frontmatter** = orchestrator config (tracker, hooks, agent, etc.)
- **Body** = Jinja2/Liquid prompt template injected as the agent's system prompt
  per turn — `{{ issue.identifier }}`, `{{ issue.description }}`,
  `{% if attempt %}…{% endif %}`, etc.

When editing, distinguish the two halves:

```markdown
---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Cancelled, Blocked]

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    : noop
  before_run: |
    : noop
  after_run: |
    echo "run finished at $(date)"

agent:
  kind: claude          # codex | claude | gemini
  max_concurrent_agents: 4
  max_turns: 20

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

server:
  port: 9999
---

You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
…
```

### Picking the right agent

Set `agent.kind`:
- `codex` — best for multi-turn JSON-RPC sessions; most mature backend
- `claude` — Claude Code; fresh subprocess per turn with `--resume <session-id>`
- `gemini` — one-shot per turn, no session continuity (each turn is independent)

Each backend reads its own block (`codex`, `claude`, `gemini`); the others
are ignored.

### Hooks

Each hook is a shell script that runs in the workspace directory:
- `after_create` — runs **once**, when the workspace is first created. Common use: clone the repo the agent should work in.
- `before_run` — runs **before every turn**. Common use: `git fetch` to pull latest main.
- `after_run` — runs **after every turn**. Common use: log markers, push branches.

**Failure mode**: if `after_create` exits non-zero, the worker dies immediately
with `worker_exit reason=error`. The shipped sample uses a placeholder
`git clone git@github.com:my-org/my-repo.git .` — that fails out of the box.
Replace with `: noop` for experiments or with a real clone for actual work.

## Diagnosing dispatch failures

Symphony writes structured logs to **stderr only** by default
(`src/symphony/logging.py:48`). To preserve them, redirect at launch:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
```

Common failure signatures:

| Log line                                 | Meaning                                                  | Fix                                                                                |
|------------------------------------------|----------------------------------------------------------|------------------------------------------------------------------------------------|
| `hook_failed hook=after_create rc=128`   | First-time clone failed                                  | Replace placeholder repo URL in `WORKFLOW.md`, or set `after_create: \|\n  : noop`  |
| `worker_exit reason=error`               | Worker terminated abnormally                             | Read the preceding `hook_failed` event or backend stderr for the actual cause      |
| `outcome=turn_error`                     | Turn ended in error (timeout, agent crash, tool failure) | Inspect backend stderr; for timeouts, raise `<kind>.turn_timeout_ms`               |
| `hook_timeout`                           | Hook exceeded its time budget                            | Shorten the hook or remove blocking commands                                       |
| `OSError [Errno 48]` on startup          | Port already in use                                      | `lsof -ti :9999 \| xargs -r kill`                                                  |
| `workflow_path_missing`                  | `WORKFLOW.md` not at the path you passed                 | Pass an explicit path; default is `./WORKFLOW.md`                                  |
| `dispatch_validation_failed`             | Config invalid for the chosen `agent.kind`               | Check the matching `<kind>:` block in `WORKFLOW.md` (command, timeouts)            |
| TUI exits immediately, no error          | No TTY (running under a non-interactive shell)           | Run from a real terminal, or use `--port 9999` headless mode                       |

When triaging, always read the JSON snapshot first — it shows whether a
ticket is `running`, `retry_pending`, or `errored` and includes the last
event:

```bash
curl -s http://127.0.0.1:9999/api/v1/state | jq '.workers'
curl -s http://127.0.0.1:9999/api/v1/<ID>  | jq
```

## When NOT to use this skill

- The user wants to write code inside a workspace symphony already created
  for them — that's a normal coding task; use the agent backends'
  conventions, not symphony's CLI.
- The user is in a different repo (not `symphony-multi-agent`) — the
  `symphony` CLI is project-tooling specific to this repo.
- The user wants Linear integration — see `README.md` and
  `WORKFLOW.example.md` for the `tracker.kind: linear` config; then
  upstream Symphony docs apply.

## Quick reference

| You want to…                          | Run                                                          |
|---------------------------------------|--------------------------------------------------------------|
| Init the file-based board             | `symphony board init ./kanban`                               |
| Add a ticket                          | `symphony board new <ID> "<title>" --priority N`             |
| List tickets                          | `symphony board ls [--state STATE]`                          |
| Show a ticket                         | `symphony board show <ID>`                                   |
| Force a state transition              | `symphony board mv <ID> <state>`                             |
| Launch TUI                            | `symphony tui ./WORKFLOW.md`                                 |
| Headless + JSON API                   | `symphony ./WORKFLOW.md --port 9999`                         |
| Force a poll/reconcile                | `curl -X POST http://127.0.0.1:9999/api/v1/refresh`          |
| Snapshot state                        | `curl -s http://127.0.0.1:9999/api/v1/state \| jq`           |
| Issue debug                           | `curl -s http://127.0.0.1:9999/api/v1/<ID> \| jq`            |
| Stop a stuck server                   | `lsof -ti :9999 \| xargs -r kill`                            |
| Tail logs                             | `tail -F log/symphony.log`                                   |
