# Authoring `WORKFLOW.md`

`WORKFLOW.md` is a **hybrid file**:
- **YAML frontmatter** = orchestrator config (tracker, hooks, agent, etc.)
- **Body** = strict-Liquid prompt template injected as the agent's system
  prompt per turn — `{{ issue.identifier }}`, `{{ issue.description }}`,
  `{% if attempt %}…{% endif %}`, etc.

When editing, distinguish the two halves.

## Minimal template

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

## Picking the agent

Set `agent.kind`:
- **`codex`** — Codex `app-server`. Best for multi-turn JSON-RPC sessions; most mature backend.
- **`claude`** — Claude Code. Fresh subprocess per turn with `--resume <session-id>` from turn 2 onward.
- **`gemini`** — Gemini CLI. One-shot per turn, no session continuity (each turn is independent).

Each backend reads its own block (`codex`, `claude`, `gemini`); the others
are ignored. The `codex.linear_graphql` client tool is only advertised when
`agent.kind: codex`.

## Hooks

Each hook is a shell script that runs in the workspace directory:

| Hook            | When                                | Common use                          |
|-----------------|-------------------------------------|-------------------------------------|
| `after_create`  | once, when workspace is first created | clone the repo the agent works in |
| `before_run`    | before every turn                   | `git fetch` to pull latest main     |
| `after_run`     | after every turn                    | log markers, push branches          |
| `before_remove` | before workspace cleanup            | persist artifacts                   |

**Failure mode**: if `after_create` exits non-zero, the worker dies
immediately with `worker_exit reason=error`. The shipped sample
(`WORKFLOW.example.md` / `WORKFLOW.file.example.md`) uses a placeholder
`git clone git@github.com:my-org/my-repo.git .` — that fails out of the
box. Replace with `: noop` for experiments or with a real clone for actual
work. `symphony doctor` catches this.

## Tracker

Two kinds:

```yaml
# File-backed (no external deps):
tracker:
  kind: file
  board_root: ./kanban

# Linear-backed:
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY      # $VAR expands from environment
```

`active_states` are columns the orchestrator polls and dispatches from.
`terminal_states` are end columns (the orchestrator stops watching once a
ticket lands here).

### Column legends (`tracker.state_descriptions`)

Optional. Maps a state name to a one-line description that the TUI
renders under each column header. Useful when lanes encode workflow
semantics (Triage / Fix / Self-review / Deploy) that aren't obvious
from the lane name alone.

```yaml
tracker:
  active_states: [Todo, "In Progress", Review]
  terminal_states: [Done, Cancelled, Blocked]
  state_descriptions:
    Todo: "Triage: read PR + decide next action"
    "In Progress": "Apply fix locally, run tests"
    Review: "Self-review the diff before Done"
    Cancelled: "Junk / stale / agent-rejected"
```

Keys are matched case-insensitively. Empty strings and non-string
values are dropped. Omit the field entirely to keep the original
column-name-only header.

## Workspace + concurrency

```yaml
workspace:
  root: ~/symphony_workspaces       # one subdirectory per ticket created here

agent:
  max_concurrent_agents: 4
  max_concurrent_agents_by_state:    # optional per-lane throttle
    Todo: 2
    "In Progress": 4
    "Deploy Ready": 1                # never deploy two things at once
```

## Optional HTTP API

```yaml
server:
  port: 9999    # omit to disable; --port on CLI overrides
```

When unset, the orchestrator runs without an HTTP server and you can only
observe via stderr logs and the TUI.
