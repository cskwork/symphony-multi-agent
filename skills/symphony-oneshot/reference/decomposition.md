# Decomposition heuristics — turning one prompt into N tickets

The Plan lane is the only lane that decides what tickets exist. Quality of
decomposition is the upper bound on quality of delivery — if the Plan lane
ships a bad ticket map, no amount of clever Build/Verify/QA work fixes it.

## The decomposition checklist (Plan lane runs this)

For each candidate ticket:

1. **Is it independently testable?** — A ticket whose tests need another
   un-built ticket should be merged with that ticket OR sequenced via
   `blocked_by`.
2. **Is its spec self-contained?** — Worker reads only the ticket's
   description + vault. If a third file is needed, add it to the spec.
3. **Does it fit one context window?** — Rough rule: a Build ticket should
   touch ≤5 files and ≤500 net lines. Bigger → split.
4. **Does it own one contract?** — Each Build ticket owns one section of
   `contracts.md`. Two tickets owning the same contract is a merge conflict
   waiting to happen.

## Ticket numbering is task order

The Plan lane must treat ticket IDs as ordering metadata, not decoration.
After writing the task table in `plan.md`, assign suffixes by walking that
table from top to bottom. The first Build task is `BUILD-1`, the second is
`BUILD-2`, and so on; the same applies to `VERIFY-N`, `QA-N`, `POLISH-N`, and
`DELIVER-N` if there are multiple tickets in those lanes. Then create the
kanban files in that same order. Do not sort by lane name, priority, or ease
of implementation when assigning numbers.

## Common slice patterns by product type

### CRUD web app
```
BUILD-1  Schema + migrations            (no deps)
BUILD-2  Auth: signup/login/session     (deps: BUILD-1)
BUILD-3  API: <resource> CRUD           (deps: BUILD-1, BUILD-2)
BUILD-4  Web UI: list + detail + form   (deps: BUILD-3)
BUILD-5  Web UI: signup/login pages     (deps: BUILD-2)
VERIFY-1 Full suite + integration       (deps: BUILD-*)
QA-1     Playwright golden + edge       (deps: BUILD-4, BUILD-5)
DELIVER-1 Package + README + tag        (deps: VERIFY-1, QA-1)
```

### CLI tool
```
BUILD-1  Core domain logic + unit tests   (no deps)
BUILD-2  CLI surface (argparse/clap/etc)  (deps: BUILD-1)
BUILD-3  Output formatting                (deps: BUILD-1)
BUILD-4  Config loading (env/file)        (deps: BUILD-2)
VERIFY-1 Integration tests + golden CLI   (deps: BUILD-*)
DELIVER-1 README + man page + binary      (deps: VERIFY-1)
```
(no QA lane — `.is_browser_app` not set)

### Static landing page
```
BUILD-1  Hero + nav + footer (semantic HTML)   (no deps)
BUILD-2  Section components                    (deps: BUILD-1)
BUILD-3  Styling + responsive                  (deps: BUILD-2)
BUILD-4  Build pipeline (vite/astro/etc)       (deps: BUILD-3)
VERIFY-1 Lighthouse + HTML validation          (deps: BUILD-*)
QA-1     Playwright cross-viewport + a11y      (deps: BUILD-*)
DELIVER-1 Deploy script + DNS notes            (deps: VERIFY-1, QA-1)
```

## Anti-patterns

| Anti-pattern | Why it breaks | Fix |
|--------------|---------------|-----|
| One giant BUILD-1 "implement everything" | No parallelism; verify lane has nothing to compare against | Split per layer or per route |
| Build tickets call each other's internals | Re-introduces sequential dependency | Talk only via `contracts.md` |
| QA ticket created before any Build ticket | Nothing to test; QA worker idles or hallucinates | QA `blocked_by` all relevant Build tickets |
| Verify ticket per Build ticket | Ledger fragmentation; can't see integration failures | One Verify ticket per release; runs full suite |
| "Polish" used as catchall for missed work | Polish balloons; brief.md gets re-litigated | Polish only addresses verified findings; new scope → new ticket |

## Sizing the Plan

If decomposition produces >12 Build tickets, the Plan lane should *itself*
split: produce a `plan-phase-1.md`, `plan-phase-2.md` and only spawn
phase 1 tickets now. The Deliver gate of phase 1 then triggers a fresh
Plan ticket for phase 2 (set `blocked_by` accordingly).

This caps the in-flight surface area at ~10 active tickets, which is
roughly the most a single Verify lane can hold in its head.
