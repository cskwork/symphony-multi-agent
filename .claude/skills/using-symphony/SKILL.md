---
name: using-symphony
description: Use when the user wants to dispatch coding agents (Codex / Claude Code / Gemini / Pi) against a Kanban board via this `symphony-multi-agent` repo — adding/listing/transitioning tickets, launching the TUI, inspecting orchestrator state, customizing the workflow (lanes, per-state prompts), delegating sub-tasks to free up context, or diagnosing dispatch failures. Triggers on phrases like "add a symphony task", "run symphony", "dispatch this ticket", "symphony board", "WORKFLOW.md", "symphony tui won't start", "ticket failed with worker_exit", "customize kanban states", "deploy pipeline workflow", "delegate to symphony", "agent.kind: pi", "agent silent for N seconds".
---

# Using Symphony

Symphony is a polling orchestrator that takes Kanban tickets and runs a
coding-agent CLI (Codex, Claude Code, Gemini, or Pi) against each one in an
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
                              interval_ms)                          gemini |
                                                                    pi)
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

Catches port collision, missing agent CLI on `$PATH`, missing pi auth
(`~/.pi/agent/auth.json` when `agent.kind: pi`), the shipped placeholder
clone URL, unwritable workspace, and missing board directory in one pass.
Exit 0 if green; otherwise read FAIL lines and fix before launching.

## Bootstrapping Symphony into another project (REQUIRED)

Whenever you introduce Symphony to a project that does **not** live inside
this repo, **always copy `tui-open.sh` (and `tui-open.bat` for Windows)
alongside `WORKFLOW.md`** so the operator has a one-shot Kanban-UI
launcher. Do this every time — even if the user only asks for tickets or
headless mode — because they will want the board view sooner or later,
and the launcher carries safety logic (`lsof` port-collision check,
doctor preflight, new-window spawn) that plain `symphony tui` does not.

```bash
# from inside the symphony-multi-agent checkout, into the target project:
cp tui-open.sh tui-open.bat   /path/to/target-project/
cp WORKFLOW.example.md        /path/to/target-project/WORKFLOW.md   # then edit
chmod +x /path/to/target-project/tui-open.sh
```

Then the operator opens the board with:

```bash
./tui-open.sh                 # macOS/Linux — uses ./WORKFLOW.md
./tui-open.sh path/to/WORKFLOW.md
tui-open.bat                  # Windows — uses .\WORKFLOW.md
```

Why the launcher matters (don't skip it):
- Auto-detects an existing TUI on the workflow's `server.port` and raises
  the existing window instead of crashing on `EADDRINUSE`.
- Runs `symphony doctor` before swapping the screen, so failures stay
  readable.
- Prefers `<project>/.venv/bin/symphony` over PATH — works without global
  install.
- Spawns a real new window (macOS `.command` via iTerm/Terminal,
  Linux via `$TERMINAL`/gnome-terminal/konsole/xterm), avoiding the
  `osascript do script` "silent tab in another Space" trap.

If the target project has no `.venv`, also remind the user to run
`python3.11 -m venv .venv && .venv/bin/pip install -e <symphony-multi-agent>`
(or `pip install symphony-multi-agent` once published) so the launcher's
venv-first lookup succeeds.

## Headless visibility — what to grep for in `log/symphony.log`

When running without the TUI, these INFO/WARN lines tell you the run is
actually progressing (vs. hung):

| Log message                           | Means                                                |
|---------------------------------------|------------------------------------------------------|
| `dispatch issue_id=...`               | Orchestrator picked up a ticket                      |
| `hook_completed hook=after_create`    | Workspace seeded; per-ticket cwd is ready            |
| `agent_session_started session_id=`   | Agent CLI booted and minted a session id             |
| `agent_turn_completed turn=N total_tokens=...` | Turn finished; tokens accumulated; live preview snippet attached |
| `worker_turn_completed turn=N ...`    | Worker-side mirror of the above; guaranteed to fire even when reconcile races in |
| `agent_turn_failed reason=... stderr_tail=[...]` | Backend reported a turn-level failure; last 20 stderr lines attached |
| `agent_compaction phase=start/end`    | Pi only — context compaction (auto or `/compact`)    |
| `agent_internal_retry phase=start/end` | Pi only — backend-internal LLM retry on transient error |
| `reconcile_skip_active_worker`        | Reconcile saw terminal state but worker is still emitting events; lets it exit naturally |
| `reconcile_terminate_terminal state=` | Ticket reached a terminal state and worker is stale (>10 s silent) — force-cancel |
| `worker_exit reason=normal`           | Successful end-to-end run                            |

If you see `dispatch` but no `agent_session_started` within a minute, the
backend is stuck before its first event — inspect `pi`/`claude`/`codex`
stdin and auth (see troubleshooting reference).

## Top three recipes

### 1. Add and run a single ticket

```bash
symphony board init ./kanban                                  # once per repo
symphony board new TASK-1 "<title>" --description "<spec>"
./tui-open.sh ./WORKFLOW.md                                   # preferred — see "Bootstrapping" above
# or: symphony tui ./WORKFLOW.md                              # plain CLI; TTY required
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
- The user wants a *whole product* built end-to-end from a single prompt
  (with shared-knowledge vault, multi-lane workflow, and PDF-gated
  verification) — use `symphony-oneshot` instead. That skill builds on
  this one; understand `using-symphony` first, then layer the OneShot
  pattern on top.

## Quick reference

| You want to…                          | Run                                                          |
|---------------------------------------|--------------------------------------------------------------|
| Preflight                             | `symphony doctor ./WORKFLOW.md`                              |
| Init the file-based board             | `symphony board init ./kanban`                               |
| Add a ticket                          | `symphony board new <ID> "<title>" --priority N`             |
| List tickets                          | `symphony board ls [--state STATE]`                          |
| Show a ticket                         | `symphony board show <ID>`                                   |
| Force a state transition              | `symphony board mv <ID> <state>`                             |
| Launch TUI (preferred, new window)    | `./tui-open.sh ./WORKFLOW.md`                                |
| Launch TUI (plain CLI, current TTY)   | `symphony tui ./WORKFLOW.md`                                 |
| Headless + JSON API                   | `symphony ./WORKFLOW.md --port 9999`                         |
| Force a poll/reconcile                | `curl -X POST http://127.0.0.1:9999/api/v1/refresh`          |
| Snapshot state                        | `curl -s http://127.0.0.1:9999/api/v1/state \| jq`           |
| Issue debug                           | `curl -s http://127.0.0.1:9999/api/v1/<ID> \| jq`            |
| Stop a stuck server                   | `lsof -ti :9999 \| xargs -r kill`                            |
| Capture logs                          | `symphony … 2>> log/symphony.log` then `tail -F log/...`     |
