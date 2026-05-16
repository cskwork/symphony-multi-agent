### PLAN  -- when state is `Plan`

Turn Explore into a professional implementation plan that the next agent can
execute by reading only `## Plan`. Do not write production code in this stage.

1. Read `docs/{{ issue.identifier }}/explore/`, the Domain Brief,
   Plan Candidates, Recommendation, and any Triage / Reproduction comments.
2. Choose or refine the recommended approach. If the Explore brief missed a
   blocking fact, move the issue to `Blocked` and post `## Plan Blocker`
   with the exact missing input. Do not guess.
3. Create `docs/{{ issue.identifier }}/plan/implementation-plan.md` when the
   plan needs more than the concise comment below.
4. Post `## Plan` with enough precision for a fresh In Progress agent to
   implement without re-reading Explore by default:
   - chosen approach and why it wins,
   - exact file/module ownership and expected write scope,
   - ordered implementation steps with dependencies and stop conditions,
   - data/API contracts, env vars, migrations, or UI states that must work,
   - first failing test, verification commands, and required evidence,
   - acceptance criteria, user-visible behavior, rollback/risk notes.
   If any bullet would be vague ("wire it up", "handle errors", "make UI
   nice"), replace it with concrete files, commands, states, or payloads.
5. Transition state to `In Progress`. In Progress must read this `## Plan`
   before editing code.
