---
name: symphony-oneshot
description: Use when the user wants a single prompt — a feature, a bugfix, a refactor, or a whole product — driven end-to-end through a rigorous decompose-build-verify-QA-deliver pipeline with a shared `.oneshot/vault/` for cross-agent knowledge and mechanical bash gates that refuse to close without proof. For browser apps, the QA gate produces Playwright + screenshots + a signed PDF report. Distinct from `using-symphony` (which is the bare CLI for ad-hoc tickets). Triggers on phrases like "one-shot this", "OneShot pattern", "decompose and dispatch with proof", "build with verification gates", "Playwright sign-off PDF", "fix this bug end-to-end", "ship this feature with QA evidence".
---

# Symphony OneShot

Turn one user prompt into a delivered product. Adapts the [OneShot
pattern](https://github.com/oneshot-repo/OneShot) (orchestrator-only-spawns +
shared `vault/` + autonomous loop until done) onto Symphony's Kanban
orchestrator (`using-symphony`). Production-grade verification is the gate;
for browser apps the final gate is a Playwright-driven QA PDF.

> **Hard precondition**: this skill runs *inside* the `symphony-multi-agent`
> repo. If `which symphony` fails or `WORKFLOW.md` is missing, run
> `using-symphony` setup first.

## What's different from `using-symphony`

`using-symphony` is the bare CLI; `symphony-oneshot` is one specific
opinionated workflow built on top. They are not redundant — pick the one
that fits the scope:

| `using-symphony`                          | `symphony-oneshot`                                |
|-------------------------------------------|---------------------------------------------------|
| You write the ticket; one ticket = one task | You write a *prompt*; the skill produces N tickets — works for a one-line bugfix as much as a whole product |
| One lane (Todo→Done) is fine               | Multi-lane: Brief→Plan→Build→Verify→QA→Polish→Deliver — tiny scopes collapse most of these (see "Scope" below) |
| No shared knowledge across tickets         | All tickets read/write `.oneshot/vault/`          |
| Verification is the operator's job         | Verification is a hard gate before Deliver       |
| Browser apps: ad-hoc                       | Browser apps: Playwright + screenshots + PDF required |

### Scope — the lanes scale down

The 7-lane pipeline is the *full* shape. Tiny scopes elide lanes:

| Scope                  | Typical ticket map                                                  |
|------------------------|----------------------------------------------------------------------|
| One-line bugfix        | Brief → Plan → 1 Build → Verify → Deliver (no QA, no Polish)         |
| Single feature         | Brief → Plan → 1–3 Build → Verify → (QA if web) → Deliver            |
| Refactor               | Brief → Plan → N Build → Verify → Deliver                            |
| Whole product          | All 7 lanes; multiple Build slices in parallel                       |

The Plan lane decides which lanes to instantiate. It MAY skip QA when
`.oneshot/vault/.is_browser_app` is absent and MAY collapse Polish into
Deliver when no findings exist. The vault, the gates, and the
orchestrator-only-spawns rule do not change with scope.

## Mental model in 30 seconds

```
ONE PROMPT
   │
   ▼
┌─────────────────┐    writes    ┌──────────────────┐
│  Orchestrator   │─────────────▶│  .oneshot/vault/ │  shared knowledge
│ (this session)  │              │  brief / plan /  │  (append-only ledgers)
│  ONLY spawns    │              │  decisions /     │
│  — never codes  │              │  claims / verify │
└─────────────────┘              └──────────────────┘
   │ creates                              ▲
   ▼                                      │ each worker
┌─────────────────┐  dispatches  ┌──────────────────┐  reads + appends
│ symphony tui /  │─────────────▶│  Per-lane workers│  (fresh context
│ symphony srv    │              │  Brief / Plan /  │   per ticket)
│ (file tracker)  │              │  Build / Verify /│
└─────────────────┘              │  QA / Polish /   │
                                 │  Deliver         │
                                 └──────────────────┘
                                          │
                                          ▼
                            Deliver lane WILL NOT close
                            until verify.md is green AND
                            (for web) qa-report.pdf exists.
```

**Three invariants borrowed from OneShot:**
1. The orchestrator NEVER writes implementation, plans, or verification inline. It only spawns Symphony tickets. If you catch yourself coding, stop and dispatch a ticket instead.
2. The `.oneshot/vault/` is the only persistent state. Per-ticket workspaces are throwaway.
3. The loop continues until **delivery proof exists**, not until tickets are "Done." Done without proof = re-open.

## When to use

- "Build me X end-to-end from this spec" — full product from one prompt.
- Ambitious multi-component work where parallelism + isolated contexts win.
- Any deliverable where the user expects *evidence* (tests, screenshots, PDF) not just code.

## When NOT to use

- Single-file change → use `using-symphony` directly.
- Pure exploration / research → use `explore` skill.
- The user wants to drive each ticket themselves → `using-symphony` is the right granularity.
- You're not in the `symphony-multi-agent` repo (or in a project that vendored it).

## Top-level recipe

```bash
# 0. Preflight
symphony doctor ./WORKFLOW.md           # must be green

# 1. Bootstrap vault + workflow + intake ticket from the user's prompt
bash .claude/skills/symphony-oneshot/templates/bootstrap.sh \
  "<user's one-shot prompt>"            # writes .oneshot/, copies WORKFLOW.oneshot.md → WORKFLOW.md, creates INTAKE-1

# 2. Launch headless (orchestrator session has no TTY)
symphony ./WORKFLOW.md --port 9999 2>> log/symphony.log &

# 3. Watch the lanes flow. Poll, don't tight-loop. Loop terminates when DELIVER reaches Delivered.
while true; do
  state=$(curl -s http://127.0.0.1:9999/api/v1/state | jq -r '.issues[] | select(.identifier|startswith("DELIVER")) | .state' | head -1)
  curl -s http://127.0.0.1:9999/api/v1/state | jq '.counts'
  case "$state" in
    Delivered) echo "✓ delivered"; break ;;
    Blocked|Cancelled) echo "⚠ stopped: $state — see kanban/DELIVER-*.md ## Blocker"; break ;;
  esac
  sleep 30
done

# 4. Inspect proof
ls .oneshot/vault/artifacts/                  # qa-report.pdf, screenshots/, test-results/
cat .oneshot/vault/verification.md           # what was actually run
cat .oneshot/vault/delivery.md               # final manifest
```

The `bootstrap.sh` and the lane prompts do all the heavy lifting — see
references below for the *why*.

## Decision: which reference do I open?

| Need                                                           | Read                                |
|----------------------------------------------------------------|-------------------------------------|
| What lives in the vault and who owns each file                 | `reference/vault.md`                |
| The actual lane definitions + per-lane prompts (Liquid body)   | `reference/lanes.md`                |
| Browser-app QA: Playwright spec, screenshot policy, PDF gate   | `reference/qa-browser.md`           |
| Decomposition heuristics — splitting the one-shot prompt       | `reference/decomposition.md`        |
| Stuck/blocked/loop-not-terminating — diagnosis                 | `reference/troubleshooting.md`      |

## Templates (ready to copy)

| File                                          | Purpose                                          |
|-----------------------------------------------|--------------------------------------------------|
| `templates/bootstrap.sh`                      | One-shot prompt → vault + WORKFLOW.md + INTAKE-1 |
| `templates/WORKFLOW.oneshot.md`               | Drop-in WORKFLOW.md with all 7 lanes wired       |
| `templates/SYSTEM.md`                         | Orchestrator constitution (copied into vault)    |
| `templates/playwright-qa.spec.ts`             | Stub Playwright QA test (golden + edge + a11y)   |
| `templates/qa-pdf.sh`                         | Markdown QA report + screenshots → PDF (Playwright-rendered) |
| `templates/vault-skeleton/`                   | Starter `brief.md`, `plan.md`, ledgers           |

## Production-grade verification — the iron rule

> **A ticket is only Done when its claim has been independently verified.**

The Verify lane runs *after* Build and treats `claims.md` as untrusted input.
It re-runs the tests, re-types the code, re-lints, and writes the actual
results to `verification.md`. **Discrepancy between claims and verification
forces the Build ticket back open.** This mirrors OneShot's "proof trail."

For browser apps, the QA lane adds a second independent gate: it doesn't
read the code, it drives the running app via Playwright. If the QA spec
fails, or `qa-report.pdf` cannot be produced, the Deliver lane is
mechanically blocked — there's a literal `[ -f .oneshot/vault/artifacts/qa-report.pdf ] || exit 1`
in the Deliver prompt's bash gate.

See `reference/lanes.md` for the exact gate logic and `reference/qa-browser.md`
for how the PDF is produced (Playwright renders Markdown→HTML→PDF, no
external pandoc/wkhtmltopdf dependency).

## Common mistakes

| Symptom                                                | Cause                                                  | Fix                                              |
|--------------------------------------------------------|--------------------------------------------------------|--------------------------------------------------|
| Orchestrator session starts coding                      | Forgot it's spawn-only                                 | Stop. Re-read SKILL.md invariant #1. Dispatch.   |
| Tickets duplicate work                                  | Workers didn't read the vault                          | Lane prompts MUST `cat .oneshot/vault/plan.md` first; see `reference/lanes.md` |
| Verify lane passes but app is broken                    | Build claimed without running                          | Verify must `npm test` from scratch, not trust   |
| `qa-report.pdf` missing but Deliver closed             | Skipped the bash gate                                  | Re-paste lane prompt — gate is the last line     |
| Vault grows unbounded                                   | Ledgers being rewritten instead of appended            | Enforce: claims/decisions/verification are append-only |

## Cross-references

- **REQUIRES**: `using-symphony` (you must understand the underlying CLI first)
- **COMPOSES WITH**: `qa-engineer` (browser sign-off — invoked by QA lane prompt)
- **ALTERNATIVE**: `codex-goal-handoff` (similar long-horizon pattern but via Codex's `/goal` Ralph loop instead of Symphony's polling Kanban)
