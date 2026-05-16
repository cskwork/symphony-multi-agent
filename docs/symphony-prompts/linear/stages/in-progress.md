### IMPLEMENT  -- when state is `In Progress`

You are the implementer. Ship the smallest change that satisfies the brief.

1. **Conflict pre-check.** List Linear active issues (state `In Progress`,
   `Review`, or `QA`) and read their `docs/<other-id>/explore/` Touched
   Files for overlap with this ticket's `## Touched Files`. On overlap,
   transition state to `Blocked` and post a `## Conflict` comment with
   the other issue id, overlapping path(s), and a one-line reason
   ("waiting on <ID> to finish editing <path>"); STOP.
2. **Read shared context.** Open `docs/{{ issue.identifier }}/explore/`
   and re-read the most recent Recommendation. If the most recent ticket
   comment is a QA Failure or Review Findings comment, scope this turn
   to ONLY those flagged items — no drive-by changes. Fresh context
   means earlier conversation is gone; the markdown and Linear comments
   are the contract.
3. Implement the chosen option from the Explore Recommendation (or, on
   rewind, only the flagged failure items above). Do not reopen the plan
   unless the brief got a fact wrong — then post a one-line note and
   proceed.
4. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
5. Pair the change with user-facing docs at
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this
   ticket carries the `bug` label) — what changed, how a user observes
   it, any knobs/flags. Plain language, no jargon.
6. Open a draft PR. Post an Implementation comment with the PR link,
   touched files, and commit-style intent per change.
7. Transition state to `Review`.
