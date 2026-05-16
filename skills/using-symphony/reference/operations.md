# Operations

Day-to-day commands for managing tickets and the orchestrator.

## Tickets (file-based tracker)

### Init the board

```bash
symphony board init ./kanban
```

Creates `kanban/` and a sample `DEMO-001.md`. Idempotent — re-running is
safe.

### Add a ticket

```bash
symphony board new TASK-1 "Fix flaky pagination test" \
  --priority 2 \
  --labels backend,test \
  --agent-kind claude \
  --description "tests/test_pagination.py::test_cursor_advance is flaky on CI."
```

Identifiers are free-form strings (`TASK-1`, `BUG-007`, `PROD-2026-05-09`).
Convention is `<PREFIX>-<NUMBER>` but it is not enforced. The file lands at
`kanban/<ID>.md`. Omit `--agent-kind` to use the global `agent.kind` from
`WORKFLOW.md`; set it only for tickets that need a different backend.

When creating more than one ticket, assign IDs in the same order as the task
list: `TASK-001`, then `TASK-002`, then `TASK-003`. Symphony uses the stable
numeric suffix as its first dispatch order signal before mutable fields like
priority or update time. If you are adding to an existing prefix, list or scan
the board first, continue from the highest existing suffix, and create the
files in that same order. Use zero padding consistently so humans and shell
sorts see the same order as Symphony.

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

## Running the orchestrator

### TUI mode (interactive)

```bash
symphony tui ./WORKFLOW.md
```

**TTY required.** Background processes / scripts cannot render the TUI; the
process will exit silently. If you (the agent) want to start Symphony for
the user from a non-interactive shell, use headless mode below.

### Headless (no TUI)

```bash
symphony ./WORKFLOW.md                  # progress mirror auto-on
symphony ./WORKFLOW.md --no-progress-md # disable the WORKFLOW-PROGRESS.md file
```

A live `WORKFLOW-PROGRESS.md` is rewritten next to your workflow file on
every tick and on every state change. Open the file in any editor to follow
along without a TTY — it includes the current Kanban breakdown and the
last `progress.max_transitions` (default 20) state changes.

Override location and limits in `WORKFLOW.md` frontmatter:

```yaml
progress:
  enabled: true                # default true; CLI --no-progress-md wins
  path: docs/STATUS.md         # default: WORKFLOW-PROGRESS.md beside WORKFLOW.md
  max_transitions: 20
```

CLI flag for one-off path overrides: `--progress-md-path docs/STATUS.md`.

### Headless + JSON API

```bash
symphony ./WORKFLOW.md --port 9999
curl -s http://127.0.0.1:9999/api/v1/state | jq
curl -s http://127.0.0.1:9999/api/v1/TASK-1 | jq
curl -s -X POST http://127.0.0.1:9999/api/v1/refresh   # force a poll tick
```

Endpoints:

| Method | Path                       | Purpose                                      |
|--------|----------------------------|----------------------------------------------|
| GET    | `/api/v1/state`            | Snapshot — running, retrying, totals, limits |
| GET    | `/api/v1/<identifier>`     | Issue detail (404 with structured error)     |
| POST   | `/api/v1/refresh`          | Coalesced trigger of poll + reconcile        |

### Stop a stuck server

```bash
lsof -ti :9999 | xargs -r kill        # SIGTERM (graceful)
lsof -ti :9999 | xargs -r kill -9     # only if SIGTERM doesn't take
```

### Logs

Symphony writes structured logs to **stderr only** by default
(`src/symphony/logging.py:48`). To preserve them:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
# or, headless with tee for live tail:
symphony ./WORKFLOW.md --port 9999 2>&1 | tee -a log/symphony.log
```

## Demo without an agent CLI installed

`src/symphony/mock_codex.py` ships a JSON-RPC mock that the codex backend
can drive. Use it to demo or smoke-test without `codex` / `claude` /
`gemini` on `$PATH`:

```yaml
agent:
  kind: codex
codex:
  command: python -m symphony.mock_codex
```

Tunables (env vars):

| Var                                 | Default | Effect                          |
|-------------------------------------|---------|---------------------------------|
| `SYMPHONY_MOCK_TURN_SECONDS`        | 12      | total turn duration             |
| `SYMPHONY_MOCK_TICK_SECONDS`        | 2       | token-usage tick interval       |
| `SYMPHONY_MOCK_TOKENS_PER_TICK`     | 250     | tokens added per tick           |
| `SYMPHONY_MOCK_FAIL_EVERY_N_TURNS`  | 0       | force the Nth turn to fail      |
| `SYMPHONY_MOCK_MAX_TURNS`           | 0       | stop accepting turns after N    |

The mock does **not** rewrite ticket files — cards stay in their original
column with a runtime ● indicator overlaid. To see real column transitions,
use a real agent backend.
