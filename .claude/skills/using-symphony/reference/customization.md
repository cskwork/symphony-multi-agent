# Customizing the workflow — multi-stage lanes + per-state prompts

Symphony does **not** hardcode the canonical "Todo / In Progress / Done"
workflow. The orchestrator treats state strings opaquely; what columns
exist and what the agent should do in each is entirely up to the operator.

## Add or rename lanes

Edit `tracker.active_states` and `tracker.terminal_states`. The TUI builds
columns from `active_states + terminal_states` in declared order
(`tui.py:212`). For a deploy pipeline:

```yaml
tracker:
  kind: file
  board_root: ./kanban
  active_states:
    - Reproduce
    - Todo
    - "In Progress"
    - Review
    - "Deploy Ready"
    - Deployed
    - Verified
  terminal_states:
    - Done
    - Cancelled
    - Blocked

agent:
  kind: claude
  max_concurrent_agents: 4
  max_concurrent_agents_by_state:
    "Deploy Ready": 1     # never deploy two things at once
    Verified: 1
```

`symphony board mv TASK-1 "Deploy Ready"` quotes are required for
multi-word state names. The agent transitions states from inside its
prompt by rewriting the ticket file's frontmatter `state:` field.

## Per-state default system prompt (lane-specific behavior)

The WORKFLOW.md body is a **strict Liquid-subset prompt template**
(`prompt.py:1-9`) that re-renders on every turn. `issue.state` is exposed
to the template (`issue.py:33-46`), so branching by current lane gives
each lane its own implicit "system prompt":

```liquid
You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.

{% if issue.state == "Reproduce" %}
## Stage: Reproduce
- Your only goal is to write a failing test that reproduces the bug.
- Do NOT fix anything. Stop after the test fails reliably.
- When done, set the ticket `state` to `Todo`.

{% elsif issue.state == "Review" %}
## Stage: Review
- Read the diff against `main`. Check tests, security, style.
- Append `## Review` with findings.
- If green, set `state` to `Deploy Ready`. Otherwise `state` to `In Progress`.

{% elsif issue.state == "Deploy Ready" %}
## Stage: Deploy
- Run `make deploy-staging`. Capture the build URL.
- Append `## Deploy` with the URL. Set `state` to `Deployed`.

{% elsif issue.state == "Deployed" %}
## Stage: Verify
- Run smoke tests against the URL in `## Deploy`.
- If green, set `state` to `Verified`. Otherwise `state` to `Blocked`.

{% else %}
## Default
{{ issue.description }}
When done, set `state` to `Done` and append `## Resolution`.
{% endif %}
```

This is the "each board has its own default system prompt" pattern: a
ticket entering a lane automatically picks up that lane's instructions on
the next turn, because the orchestrator re-renders the template using the
current `issue.state` every time it dispatches.

### Why this works rather than a `prompts_by_state:` config block

Symphony's orchestrator is **stateless w.r.t. lane semantics** — it knows
nothing about "Review" or "Deploy" vs. "Todo". By pushing the per-lane
instructions into the prompt template (the only piece the orchestrator
re-evaluates per turn anyway), the customization rides on existing
machinery instead of inventing a parallel configuration surface. Trade-off:
the user has to write Liquid `{% if %}` blocks, but they get full
expressiveness — variable interpolation, blockers, attempt counter, labels,
etc. — without a new schema to learn.

## What customization does *not* (yet) support

| Want                                                | Status | Workaround                                                                  |
|-----------------------------------------------------|--------|-----------------------------------------------------------------------------|
| Per-state agent kind (e.g. claude for Review, codex for Implement) | ❌      | Single `agent.kind` only. Use prompt branching to vary behavior instead.    |
| Per-state turn limits / timeouts                    | ❌      | Globals (`agent.max_turns`, `<kind>.turn_timeout_ms`). PR territory to add. |
| Auto-progression without an agent edit              | ❌      | The agent itself rewrites `kanban/<ID>.md` `state:` to advance.             |
| Hard ordering between lanes                         | ⚠      | Use `blocked_by` in ticket frontmatter; advisory only.                      |

## Available template variables

The renderer exposes these to the body (see `prompt.py` + `issue.py`):

| Variable                  | Type    | Notes                                                |
|---------------------------|---------|------------------------------------------------------|
| `{{ issue.identifier }}`  | string  | e.g. `TASK-1`                                        |
| `{{ issue.title }}`       | string  |                                                      |
| `{{ issue.description }}` | string  | full ticket body minus frontmatter                   |
| `{{ issue.state }}`       | string  | current lane                                         |
| `{{ issue.priority }}`    | int     | nullable                                             |
| `{{ issue.labels }}`      | list    | use `{{ labels \| join: ", " }}`                     |
| `{{ issue.blocked_by }}`  | list    | each item has `.id`, `.identifier`, `.state`         |
| `{{ attempt }}`           | int     | retry attempt; null on first try                     |

Unknown variables / filters intentionally raise `TemplateRenderError` —
typos surface immediately rather than silently emitting empty strings.
