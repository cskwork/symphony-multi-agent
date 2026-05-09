---
name: using-symphony
description: Use when the user wants to dispatch coding agents (Codex / Claude Code / Gemini) against a Kanban board via this `symphony-multi-agent` repo — adding/listing/transitioning tickets, launching the TUI, inspecting orchestrator state, customizing the workflow (lanes, per-state prompts), delegating sub-tasks to free up context, or diagnosing dispatch failures. Triggers on phrases like "add a symphony task", "run symphony", "dispatch this ticket", "symphony board", "WORKFLOW.md", "symphony tui won't start", "ticket failed with worker_exit", "customize kanban states", "deploy pipeline workflow", "delegate to symphony".
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

## Always run preflight first

```bash
symphony doctor ./WORKFLOW.md
```

Catches port collision, missing agent CLI on `$PATH`, the shipped
placeholder clone URL, unwritable workspace, and missing board directory in
one pass. Exit 0 if green; otherwise read FAIL lines and fix before
launching.

## Top three recipes

### 1. Add and run a single ticket

```bash
symphony board init ./kanban                                  # once per repo
symphony board new TASK-1 "<title>" --description "<spec>"
symphony tui ./WORKFLOW.md                                    # interactive (TTY required)
```

### 2. Headless launch + JSON observation (no TTY)

```bash
symphony ./WORKFLOW.md --port 9999 2>> log/symphony.log &
curl -s http://127.0.0.1:9999/api/v1/state | jq
```

### 3. Demo without an agent CLI installed

Set `codex.command: python -m symphony.mock_codex` in WORKFLOW.md. The mock
speaks the same JSON-RPC protocol as Codex and emits simulated turns —
useful to verify orchestrator + TUI wiring before installing a real
backend.

## Decision: which reference page do I open?

| If the user wants to…                                      | Read                                       |
|------------------------------------------------------------|--------------------------------------------|
| Add / list / show / move tickets, run the TUI or API       | `reference/operations.md`                  |
| Edit `WORKFLOW.md` (agent kind, hooks, tracker, workspace) | `reference/workflow-config.md`             |
| Add custom lanes, per-state prompts, deploy pipelines      | `reference/customization.md`               |
| Offload sub-tasks from the calling agent's context         | `reference/delegation.md`                  |
| Diagnose `worker_exit`, `hook_failed`, blank TUI, etc.     | `reference/troubleshooting.md`             |

## When NOT to use this skill

- The user wants to write code inside a workspace symphony already created
  for them — that's a normal coding task; use the agent backend's
  conventions, not symphony's CLI.
- The user is in a different repo (not `symphony-multi-agent`) — the
  `symphony` CLI is project-tooling specific to this repo.
- The user wants Linear integration — see `README.md` and
  `WORKFLOW.example.md` for the `tracker.kind: linear` config; then
  upstream Symphony docs apply.

## Quick reference

| You want to…                          | Run                                                          |
|---------------------------------------|--------------------------------------------------------------|
| Preflight                             | `symphony doctor ./WORKFLOW.md`                              |
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
| Capture logs                          | `symphony … 2>> log/symphony.log` then `tail -F log/...`     |
