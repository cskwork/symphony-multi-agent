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
  The shipped `after_create` hook attaches that directory as a `git
  worktree` of the host repo on a `symphony/<ID>` branch — host working
  tree stays untouched, merge back explicitly via
  `git -C <host> merge symphony/<ID>` (or open a PR from that branch).
- Ticket IDs are an ordering contract. Symphony sorts dispatch candidates by
  stable ticket registration suffix (for example `TASK-001` before
  `TASK-002`) before mutable fields like priority or update time. When
  creating multiple tickets, assign numeric suffixes in the same order as the
  task list and create the files in that order.

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
TARGET=/path/to/target-project
cp tui-open.sh tui-open.bat       "$TARGET/"
cp WORKFLOW.example.md            "$TARGET/WORKFLOW.md"            # then edit
mkdir -p                          "$TARGET/docs"
cp -R docs/symphony-prompts       "$TARGET/docs/"                  # MANDATORY — see below
cp -R skills                      "$TARGET/"                       # operator skills (source of truth)
cp AGENTS.md GEMINI.md            "$TARGET/"                       # codex / gemini entry points
mkdir -p                          "$TARGET/.claude/skills"
ln -s ../../skills/using-symphony   "$TARGET/.claude/skills/using-symphony"
ln -s ../../skills/symphony-oneshot "$TARGET/.claude/skills/symphony-oneshot"
chmod +x "$TARGET/tui-open.sh"
```

### Why all four (skills/, .claude/skills/ symlinks, AGENTS.md, GEMINI.md)

The same skill content has to be discoverable from whichever CLI the
**operator** runs. Each platform looks in a different place:

| Operator CLI       | Discovery path                                                |
|--------------------|---------------------------------------------------------------|
| Claude Code        | `.claude/skills/<name>/SKILL.md` (symlink → `skills/<name>/`) |
| Codex              | `AGENTS.md` at repo root (references `skills/<name>/SKILL.md`) |
| Gemini CLI         | `GEMINI.md` at repo root (references `skills/<name>/SKILL.md`) |
| Pi                 | No documented skill convention — read `skills/` manually       |

`skills/<name>/` is the **single source of truth**; the other three are
pointers. Edit only the canonical files under `skills/`. Worker-side
behavior is unaffected — dispatched codex/claude/gemini/pi workers get
their guidance from `docs/symphony-prompts/`, not from these skills.

### Preserve the 7-stage pipeline by default (DO NOT simplify)

`WORKFLOW.example.md` ships with the full production pipeline
`[Todo, Explore, "In Progress", Review, QA, Learn]` + `Done` and
matching `prompts.stages` that wire each lane to a stage-specific prompt
under `docs/symphony-prompts/<linear|file>/stages/*.md`. This is the
*supported default*, not a maximalist example — Explore briefs the agent
from `llm-wiki/`, QA captures evidence, Learn writes back to the wiki,
and the base prompt's "no skipping" gate refers to these by name.

**When bootstrapping, do not shorten `active_states` or drop
`prompts.stages` entries** unless the user explicitly asks for a smaller
workflow. Trimming to `[Todo, "In Progress", Review]` (a common LLM
"simplification" reflex) silently breaks:

- the `Explore → In Progress → Review → QA → Learn → Done` flow the
  base prompt assumes,
- the QA evidence gate that the base prompt requires before `Done`,
- the Learn write-back to `llm-wiki/` future tickets depend on.

If the target project does need a different workflow (e.g. deploy
pipeline), edit *both* `tracker.active_states` and the `prompts.stages`
map together, and add matching stage files under
`docs/symphony-prompts/<flavor>/stages/`. See
`reference/customization.md` for the lane-rename recipe.

### Pick the right prompt flavor

- `tracker.kind: file` (default for repos that want kanban as files)
  → keep `docs/symphony-prompts/file/...` and point `prompts.base` and
  `prompts.stages` at the `file/` subtree.
- `tracker.kind: linear` (Linear-backed) → keep
  `docs/symphony-prompts/linear/...` (the example ships pointing here).

The two flavors differ in *where* the agent writes stage notes
(ticket-file body vs Linear comments) — picking the wrong one will
generate artefacts in the wrong place. You can copy only the flavor
you need if disk hygiene matters, but copying both is fine.

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
symphony board new TASK-001 "<title>" --description "<spec>"
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
| Set up / debug on Windows or macOS, or port to a new platform | `reference/platform-compat.md`          |

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
| Headless (auto-mirrors WORKFLOW-PROGRESS.md) | `symphony ./WORKFLOW.md`                              |
| Headless without progress mirror      | `symphony ./WORKFLOW.md --no-progress-md`                    |
| Headless + JSON API                   | `symphony ./WORKFLOW.md --port 9999`                         |
| Force a poll/reconcile                | `curl -X POST http://127.0.0.1:9999/api/v1/refresh`          |
| Snapshot state                        | `curl -s http://127.0.0.1:9999/api/v1/state \| jq`           |
| Issue debug                           | `curl -s http://127.0.0.1:9999/api/v1/<ID> \| jq`            |
| Stop a stuck server                   | `lsof -ti :9999 \| xargs -r kill`                            |
| Capture logs                          | `symphony … 2>> log/symphony.log` then `tail -F log/...`     |
