---
name: symphony-oneshot
description: Use for Symphony OneShot runs that turn one prompt into ordered Kanban tickets with shared vault state, verification gates, QA evidence, and delivery proof.
---

# Symphony OneShot

Turn one user prompt into a delivered product by running an opinionated
Symphony Kanban workflow: Brief -> Plan -> Build -> Verify -> QA -> Polish ->
Deliver. The workflow adapts the OneShot pattern: the orchestrator only
spawns work, workers share state through `.oneshot/vault/`, and delivery does
not close without proof.

## Preconditions

- Work from the `symphony-multi-agent` repo, or a project that intentionally
  vendors this skill and its templates.
- If `which symphony` fails or no `WORKFLOW.md` is available, set up
  `using-symphony` first.
- Start with `symphony doctor ./WORKFLOW.md`; do not dispatch OneShot work
  against a broken Symphony setup.

## Core Invariants

1. The orchestrator never writes implementation, plans, or verification inline.
   It creates and moves Symphony tickets only. If you catch yourself coding,
   stop and dispatch a ticket.
2. `.oneshot/vault/` is the only persistent cross-agent state. Per-ticket
   workspaces are disposable.
3. The loop continues until delivery proof exists, not merely until tickets are
   Done. Done without proof means reopen or block.
4. Ticket suffixes preserve creation order. The Plan lane assigns `BUILD-N`,
   `VERIFY-N`, `QA-N`, `POLISH-N`, and `DELIVER-N` in the exact order tasks
   appear in `plan.md`, then creates the kanban files in that order.

## Operating Loop

1. Confirm the task really needs OneShot: multi-slice work, evidence-heavy
   delivery, or a user request to decompose and dispatch from one prompt.
   For a single small ticket, use `using-symphony`.
2. Bootstrap with `templates/bootstrap.sh`; it creates `.oneshot/`, copies the
   OneShot workflow template, and creates the intake ticket.
3. Launch Symphony on a free port, then poll the API at a calm interval.
4. Treat `claims.md` as untrusted. Verify reruns claims and writes
   `verification.md`; browser QA additionally produces screenshots and
   `artifacts/qa-report.pdf`.
5. Deliver only after the gate passes: non-empty brief, plan, verification;
   `verdict: GREEN`; and, for browser apps, an approved QA report plus PDF.

Detailed commands, scope-selection tables, and template descriptions live in
`reference/operations.md`.

## Reference Map

- `reference/operations.md`: when to use OneShot, scope scaling, bootstrap and
  polling commands, and bundled template purposes.
- `reference/vault.md`: vault layout, file ownership, append-only ledgers, and
  efficient vault reads.
- `reference/lanes.md`: authoritative lane semantics, prompt branches, gates,
  transitions, and concurrency rules.
- `reference/decomposition.md`: Plan-lane slicing heuristics, task ordering,
  common product decompositions, and anti-patterns.
- `reference/qa-browser.md`: Playwright QA coverage, screenshot policy,
  markdown-to-PDF report generation, and browser delivery gate details.
- `reference/troubleshooting.md`: stuck workers, blocked loops, QA/PDF failures,
  runaway cost, abort/recovery, and diagnostic commands.

Load only the reference needed for the current lane or problem. The bundled
workflow template is the source of truth if any explanatory reference drifts.

## Templates

Use the files under `templates/` rather than recreating long prompts or scripts:

- `bootstrap.sh`: one-shot prompt to vault, workflow, and intake ticket.
- `WORKFLOW.oneshot.md`: drop-in seven-lane Symphony workflow.
- `SYSTEM.md`: orchestrator constitution copied into `.oneshot/`.
- `playwright-qa.spec.ts`: browser QA stub with golden, edge, and accessibility
  coverage.
- `qa-pdf.sh`: Playwright-rendered QA PDF generator.

## Cross-References

- Requires: `using-symphony` for the underlying CLI and ticket mechanics.
- Composes with QA-specific skills only when `reference/qa-browser.md` says the
  target is a deployed environment rather than localhost.
