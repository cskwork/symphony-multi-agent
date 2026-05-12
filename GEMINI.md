# GEMINI.md — Gemini CLI entry point

This repo is **Symphony**, a polling orchestrator that dispatches coding
agents (Codex / Claude Code / Gemini / Pi) at a Kanban board. This file is
the discovery point Gemini CLI reads on startup so the **operator** — the
human or agent running `symphony` — has the same skill guidance Claude Code
gets from `.claude/skills/`.

## Skill activation

Gemini activates skills through the `activate_skill` tool after reading
their metadata. Treat each entry below as a skill definition: read the
`description` to decide if it applies to the current user request, then
load `skills/<name>/SKILL.md` (via `Read` or `read_file`) and follow it.

Source of truth lives in `skills/<name>/`. `.claude/skills/` is a thin
symlink layer for Claude Code's native discovery only — do not edit
through it.

## Available skills (operator-facing)

### `using-symphony`

> Use when the user wants to dispatch coding agents (Codex / Claude Code /
> Gemini / Pi) against a Kanban board via this `symphony-multi-agent` repo
> — adding/listing/transitioning tickets, launching the TUI, inspecting
> orchestrator state, customizing the workflow (lanes, per-state prompts),
> delegating sub-tasks to free up context, or diagnosing dispatch failures.
> Triggers on phrases like "add a symphony task", "run symphony", "dispatch
> this ticket", "symphony board", "WORKFLOW.md", "symphony tui won't start",
> "ticket failed with worker_exit", "customize kanban states", "deploy
> pipeline workflow", "delegate to symphony", "agent.kind: pi", "agent
> silent for N seconds".

Entry: `skills/using-symphony/SKILL.md`

### `symphony-oneshot`

> Use when the user wants a single prompt — a feature, a bugfix, a
> refactor, or a whole product — driven end-to-end through a rigorous
> decompose-build-verify-QA-deliver pipeline with a shared `.oneshot/vault/`
> for cross-agent knowledge and mechanical bash gates that refuse to close
> without proof. For browser apps, the QA gate produces Playwright +
> screenshots + a signed PDF report. Distinct from `using-symphony` (which
> is the bare CLI for ad-hoc tickets). Triggers on phrases like "one-shot
> this", "OneShot pattern", "decompose and dispatch with proof", "build
> with verification gates", "Playwright sign-off PDF", "fix this bug
> end-to-end", "ship this feature with QA evidence".

Entry: `skills/symphony-oneshot/SKILL.md`

## Worker-side guidance

Dispatched Gemini workers (running inside a per-ticket workspace) do
**not** consume these operator skills. Worker behavior comes from
`WORKFLOW.md`'s `prompts.base` + `prompts.stages` map, which renders stage
prompts from `docs/symphony-prompts/<flavor>/`. That prompt layer is
already cross-platform — codex/claude/gemini/pi workers all receive the
same rendered prompt for a given ticket state.

## Tool mapping

The skill files use Claude Code tool names (`Read`, `Bash`, `Edit`,
`Glob`, `Grep`, `Skill`). The Gemini equivalents:

| Claude Code  | Gemini CLI              |
|--------------|-------------------------|
| `Read`       | `read_file`             |
| `Bash`       | `run_shell_command`     |
| `Edit`       | `edit` / `replace_file_content` |
| `Glob`       | `glob`                  |
| `Grep`       | `search_file_content`   |
| `Skill`      | `activate_skill`        |

## Conventions for this repo

- Read `WORKFLOW.md` and a couple of `kanban/*.md` files before any
  recommendation — settings vary per fork.
- Run `symphony doctor ./WORKFLOW.md` before launching anything.
- See `skills/using-symphony/SKILL.md` "Bootstrapping" for the full file
  set required when copying Symphony into another project.
