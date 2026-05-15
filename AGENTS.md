# AGENTS.md — Codex CLI entry point

This repo is **Symphony**, a polling orchestrator that dispatches coding
agents (Codex / Claude Code / Gemini / Pi) at a Kanban board. This file is
the discovery point that Codex (and any other `AGENTS.md`-respecting CLI)
reads on startup so the **operator** — the human or agent running
`symphony` — has the same skill guidance Claude Code gets from
`.claude/skills/`.

## Source of truth: `skills/`

All operator-side skills live in `skills/<name>/`. Each skill has a
`SKILL.md` with YAML frontmatter (`name`, `description`, optional triggers)
and a `reference/` folder of deep-dive pages. `.claude/skills/` is a thin
symlink layer for Claude Code's native discovery — do not edit through it,
edit the canonical files under `skills/`.

## Available skills (operator-facing)

Load `skills/<name>/SKILL.md` and follow it when the user's request matches
the trigger description below. Open `skills/<name>/reference/<page>.md` only
when the SKILL.md decision table tells you to.

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

Dispatched workers (the agent CLI running inside a per-ticket workspace) do
**not** consume these operator skills. Worker behavior is driven by
`WORKFLOW.md`'s `prompts.base` + `prompts.stages` map, which renders stage
prompts from `docs/symphony-prompts/<flavor>/`. That layer is already
cross-platform — codex/claude/gemini/pi workers all receive the same
rendered prompt for a given ticket state.

## Conventions for this repo

- Read `WORKFLOW.md` and a couple of `kanban/*.md` files before any
  recommendation — settings vary per fork.
- Run `symphony doctor ./WORKFLOW.md` before launching anything.
- See `skills/using-symphony/SKILL.md` "Bootstrapping" for the full file
  set required when copying Symphony into another project.
