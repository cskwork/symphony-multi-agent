---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, In Progress]
  terminal_states: [Closed, Cancelled, Canceled, Duplicate, Done]
  # Optional one-line legend rendered under each TUI column header.
  state_descriptions:
    Todo: "Ready for an agent to pick up"
    "In Progress": "Agent actively working"
    Done: "Completed by the agent"

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
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 2
    "In Progress": 4

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass an
  # empty `""` so the prompt comes purely from stdin.
  command: 'gemini -p ""'
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

tui:
  language: en          # `en` (default) or `ko` — only TUI chrome localizes;
                        # tracker states and ticket titles stay as authored.
---

You are picking up issue {{ issue.identifier }}: {{ issue.title }}.

Current state: {{ issue.state }}.
{% if attempt %}This is retry attempt {{ attempt }}.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

{% if issue.labels %}
Labels: {{ issue.labels | join: ", " }}
{% endif %}

{% if issue.blocked_by %}
This issue depends on:
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }} ({{ blocker.state }})
{% endfor %}
{% endif %}

Workflow expectations:
- Read the repo, plan a small change, and implement it.
- Run the project's tests before considering work complete.
- When done, transition the ticket to `Human Review` and add a comment with the
  PR link using the `linear_graphql` tool.
- If you cannot proceed (missing info, conflict, ambiguous spec), transition the
  ticket to `Blocked` with a comment explaining what is needed.
