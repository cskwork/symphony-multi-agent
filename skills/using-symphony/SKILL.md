---
name: using-symphony
description: Use for Symphony Kanban operations including tickets, TUI/service runs, workflow prompts, delegation, and dispatch or worker failure triage.
---

# Using Symphony

Symphony is a polling orchestrator that reads Kanban tickets and runs a
coding-agent CLI (Codex, Claude Code, Gemini, or Pi) against each ticket in
an isolated workspace. Use this skill for operator work: creating tickets,
running the orchestrator, editing workflow config, bootstrapping Symphony into
another repo, and triaging worker failures.

Start by reading the target `WORKFLOW.md` and one or two real `kanban/*.md`
files. Symphony behavior is workflow-specific, and forks commonly customize
lanes, prompts, hooks, workspace roots, and agent backends.

## Core Model

- The orchestrator reads ticket files and dispatches eligible work; the worker
  agent edits the ticket file to move state and append reports.
- Each ticket runs in its own workspace under `workspace.root` (default
  `~/symphony_workspaces/<ID>`). The default hooks attach that directory as a
  `git worktree` on `symphony/<ID>`, leaving the host working tree untouched.
- Ticket IDs are an ordering contract. For multi-ticket work, create
  `TASK-001`, then `TASK-002`, then `TASK-003` in task-list order; Symphony
  sorts by stable numeric suffix before mutable fields like priority.
- The default Learn prompt expects the `symphony/<ID>` branch to be merged into
  the configured target branch before the ticket moves to `Done`.

## Non-Negotiable Preflight

Run this before launching or debugging a workflow:

```bash
symphony doctor ./WORKFLOW.md
```

Fix FAIL lines first. Doctor catches the common launch blockers: port
collisions, missing agent CLI, missing Pi auth, placeholder clone URLs,
unwritable workspaces, and missing board directories.

## Guardrails

- When bootstrapping Symphony into another project, copy the launcher scripts,
  skill pointers, `docs/symphony-prompts`, and platform entry files. Do not
  leave the operator with only a bare `WORKFLOW.md`; read
  `reference/bootstrapping.md` for the exact bundle.
- Preserve the shipped 8-stage pipeline (`Todo`, `Explore`, `Plan`,
  `In Progress`, `Review`, `QA`, `Learn`, `Done`) unless the user explicitly
  requests a smaller workflow. If you change lanes, update both
  `tracker.active_states` and `prompts.stages`.
- Pick the prompt flavor that matches the tracker:
  `tracker.kind: file` uses `docs/symphony-prompts/file/...`;
  `tracker.kind: linear` uses `docs/symphony-prompts/linear/...`.
- Keep detailed lane behavior in `prompts.base` and `prompts.stages` files,
  not in a huge inline `WORKFLOW.md` body.
- Do not use `git reset --hard` in `before_run`; it can erase the agent's
  previous-turn work before it is finalized.

## Common Starts

Add one file-board ticket and open the managed TUI launcher:

```bash
symphony board init ./kanban
symphony board new TASK-001 "<title>" --description "<spec>"
./tui-open.sh ./WORKFLOW.md
```

Run headless with service state and browser viewer:

```bash
symphony service start ./WORKFLOW.md --port 9999 --viewer-port 8765
symphony service status ./WORKFLOW.md
curl -s http://127.0.0.1:9999/api/v1/state | jq
```

Use `symphony service ...` for normal headless operation. It writes
per-workflow run state under `.symphony/run/` and refuses duplicate starts for
the same `WORKFLOW.md`, preventing two orchestrators from dispatching the same
board.

For smoke demos without an installed agent CLI, set `codex.command: python -m
symphony.mock_codex`; see `reference/operations.md`.

## What To Read Next

| Need | Read |
| --- | --- |
| Bootstrap Symphony into a project | `reference/bootstrapping.md` |
| Add/list/show/move tickets, run TUI/API/service | `reference/operations.md` |
| Edit `WORKFLOW.md`, agent kind, hooks, tracker, workspace | `reference/workflow-config.md` |
| Rename lanes, add per-state prompts, customize pipelines | `reference/customization.md` |
| Delegate independent sub-tasks to Symphony workers | `reference/delegation.md` |
| Diagnose `worker_exit`, `hook_failed`, blank TUI, auth stalls | `reference/troubleshooting.md` |
| Set up/debug Windows, macOS, Linux behavior | `reference/platform-compat.md` |
| Configure `.gitignore` for Symphony-generated docs/logs | `reference/gitignore-recommendations.md` |

## Headless Triage Signals

If a service appears stuck, read `log/symphony.log` and the JSON state. Useful
events include:

- `dispatch issue_id=...` - ticket picked up
- `hook_completed hook=after_create` - workspace seeded
- `agent_session_started session_id=` - backend CLI started
- `agent_turn_completed turn=N total_tokens=...` - a turn finished
- `agent_turn_failed ... stderr_tail=[...]` - backend failure; inspect stderr
- `worker_exit reason=normal` - clean end-to-end completion

If `dispatch` appears but no `agent_session_started` follows within about a
minute, inspect backend auth, command, and stdin behavior. See
`reference/troubleshooting.md`.

## When Not To Use This Skill

- The user wants to write code inside a workspace Symphony already created for
  them; handle it as a normal coding task using that agent backend's
  conventions.
- The user wants a whole product built end-to-end from a single prompt with the
  OneShot knowledge-vault and PDF-gated workflow; use `symphony-oneshot`.
- The user is asking general Linear API questions outside a Symphony workflow;
  use the project README and upstream Linear docs instead.
