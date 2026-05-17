### PLAN  -- when state is `Plan`

Turn Explore into a professional implementation plan that the next agent can
execute by reading only `## Plan`. Do not write production code in this stage.

1. Read `docs/{{ issue.identifier }}/explore/` (including the required
   `reuse-inventory.md`), `## Domain Brief`, `## Plan Candidates`,
   `## Recommendation`, and any `## Triage` / `## Reproduction` sections.
2. Choose or refine the recommended approach. If the Explore brief missed a
   blocking fact, set state to `Blocked` and append `## Plan Blocker` with
   the exact missing input. Do not guess.
3. Create `docs/{{ issue.identifier }}/plan/implementation-plan.md` when the
   plan needs more than the concise ticket section below.
4. Append `## Plan` with enough precision for a fresh In Progress agent to
   implement without re-reading Explore by default:
   - chosen approach and why it wins,
   - exact file/module ownership and expected write scope,
   - ordered implementation steps with dependencies and stop conditions,
   - data/API contracts, env vars, migrations, or UI states that must work,
   - first failing test, verification commands, and required evidence,
   - acceptance criteria, user-visible behavior, rollback/risk notes.
   If any bullet would be vague ("wire it up", "handle errors", "make UI
   nice"), replace it with concrete files, commands, states, or payloads.
   The candidate set inside `## Plan` (or `## Plan Candidates` if you
   refresh it) MUST be a Markdown table — not a bullet list — using
   exactly this header (extra columns allowed at the end):

   ```
   | option | summary | reuse_from | observability |
   |--------|---------|------------|---------------|
   | A      | ...     | path:line  | add           |
   | B      | ...     | none       | none          |
   ```

   - `reuse_from`: a `path:line` from `reuse-inventory.md`, or `none`.
   - `observability`: `add`, `change`, or `none` — declares whether this
     candidate adds, modifies, or skips logs/metrics/traces.
   - Live agent demo (2026-05-17) showed bullets silently dropping
     both columns; the explicit header above is non-optional so Plan
     Rationale and Learn can rely on the columns existing.
5. Append `## Acceptance Tests` — one bullet per AC, each a runnable test
   signature (e.g. `tests/test_foo.py::test_bar` or
   `pytest -k "expr"` / `npm test -- --grep "..."`). Empty list is invalid:
   set state back to `Explore`, append `## Plan Gaps` with what is missing,
   and STOP.
6. Append `## Done Signals` — one bullet per observable signal QA can check
   (file path that must exist, stdout substring, exit code, HTTP status +
   body shape). Cap 8 lines. QA scores against this list row-for-row.
7. If you rejected any `reuse-inventory.md` row with `reuse_fit >= 0.7`,
   append `## Plan Rationale` with one line per rejected row explaining
   why (e.g. `path:line — reuse_fit 0.8 rejected: API shape mismatch`).
8. Set state to `In Progress`. In Progress must read this `## Plan` before
   editing code.
