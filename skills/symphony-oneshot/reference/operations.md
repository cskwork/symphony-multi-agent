# Operations guide

This file holds operator-facing details that are useful during a OneShot run
but too bulky for `SKILL.md`.

## OneShot vs using-symphony

| `using-symphony` | `symphony-oneshot` |
|---|---|
| You write a ticket; one ticket equals one task. | You write one prompt; the skill produces N tickets. |
| One lane can be enough. | Multi-lane pipeline: Brief, Plan, Build, Verify, QA, Polish, Deliver. |
| Shared knowledge is optional and local to the operator. | All tickets read and write `.oneshot/vault/`. |
| Verification is manually decided by the operator. | Verification is a hard gate before Deliver. |
| Browser QA is ad hoc. | Browser QA requires Playwright, screenshots, and a PDF report. |

## Scope scaling

The seven-lane pipeline is the full shape, but tiny scopes collapse lanes.
The Plan lane decides which tickets to instantiate.

| Scope | Typical ticket map |
|---|---|
| One-line bugfix | Brief -> Plan -> 1 Build -> Verify -> Deliver; usually no QA or Polish. |
| Single feature | Brief -> Plan -> 1-3 Build -> Verify -> QA if browser -> Deliver. |
| Refactor | Brief -> Plan -> N Build -> Verify -> Deliver. |
| Whole product | All seven lanes, with multiple Build slices where useful. |

The vault, gates, spawn-only orchestration, and task-order ticket numbering do
not change with scope.

## Bootstrap and run

Run from the repo root:

```bash
# 0. Preflight
symphony doctor ./WORKFLOW.md

# 1. Bootstrap vault, workflow, and intake ticket
bash skills/symphony-oneshot/templates/bootstrap.sh \
  "<user's one-shot prompt>"

# 2. Launch headless on a free port
symphony ./WORKFLOW.md --port 9999 2>> log/symphony.log &

# 3. Poll instead of tight-looping
while true; do
  state=$(curl -s http://127.0.0.1:9999/api/v1/state | jq -r '.issues[] | select(.identifier|startswith("DELIVER")) | .state' | head -1)
  curl -s http://127.0.0.1:9999/api/v1/state | jq '.counts'
  case "$state" in
    Delivered) echo "delivered"; break ;;
    Blocked|Cancelled) echo "stopped: $state - see kanban/DELIVER-*.md ## Blocker"; break ;;
  esac
  sleep 30
done

# 4. Inspect proof
ls .oneshot/vault/artifacts/
cat .oneshot/vault/verification.md
cat .oneshot/vault/delivery.md
```

If port `9999` is occupied, choose another port and keep the polling URL in
sync.

## Deliver gate summary

`reference/lanes.md` and `templates/WORKFLOW.oneshot.md` contain the exact gate.
At a minimum, Deliver requires:

- `brief.md`, `plan.md`, and `verification.md` exist and are non-empty.
- `verification.md` contains `verdict: GREEN`.
- Browser apps have `.oneshot/vault/artifacts/qa-report.pdf`.
- Browser apps have `qa-report.md` ending with `Verdict: APPROVED FOR DELIVERY`.

## Bundled templates

| File | Purpose |
|---|---|
| `templates/bootstrap.sh` | One-shot prompt -> vault, workflow, and intake ticket. |
| `templates/WORKFLOW.oneshot.md` | Drop-in workflow with all lane prompts and gates. |
| `templates/SYSTEM.md` | Orchestrator constitution copied into `.oneshot/`. |
| `templates/playwright-qa.spec.ts` | Browser QA test stub. |
| `templates/qa-pdf.sh` | Markdown QA report plus screenshots -> PDF. |

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Orchestrator starts coding. | Spawn-only invariant was ignored. | Stop and dispatch a Build ticket. |
| Tickets duplicate work. | Workers skipped the vault. | Re-read lane prompts in `reference/lanes.md`. |
| Verify passes but app is broken. | Verify trusted claims instead of rerunning. | Rerun from scratch and record real output. |
| `qa-report.pdf` is missing but Deliver closed. | Deliver gate was skipped or edited. | Restore the gate from `templates/WORKFLOW.oneshot.md`. |
| Vault history disappears. | Append-only ledgers were rewritten. | Restore from git/backups; only append to ledgers. |
