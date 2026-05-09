---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, In Progress]
  terminal_states: [Done, Cancelled, Blocked]

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    git clone --depth=1 git@github.com:my-org/my-repo.git .
  before_run: |
    git fetch origin main
    git reset --hard origin/main
  after_run: |
    echo "run finished at $(date)"

agent:
  kind: codex          # codex | claude | gemini
  max_concurrent_agents: 4
  max_turns: 20
  max_concurrent_agents_by_state:
    Todo: 2
    "In Progress": 4

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

claude:
  command: claude -p --output-format stream-json --verbose

gemini:
  command: gemini -p

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`
---

You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}This is retry attempt {{ attempt }}.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

{% if issue.labels %}Labels: {{ issue.labels | join: ", " }}{% endif %}

{% if issue.blocked_by %}
This ticket depends on:
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }} ({{ blocker.state }})
{% endfor %}
{% endif %}

## How to update the ticket

The ticket file is located at `kanban/{{ issue.identifier }}.md`. To change
state, edit its YAML front matter `state:` field and rewrite the file.
Allowed states are: {{ issue.state }}, plus those listed in tracker.active_states /
terminal_states inside WORKFLOW.md.

When you finish (or hit a blocker), use your shell tools to update the file
in-place — Symphony will reconcile on the next poll tick.

Workflow expectations:
- Read the repo, plan a small change, and implement it.
- Run the project's tests before considering the work complete.
- When done, set the ticket `state` to `Done` and append a `## Resolution`
  section to the body explaining what was changed.
- If you cannot proceed, set `state` to `Blocked` and append a `## Blocker`
  section explaining what is needed.
