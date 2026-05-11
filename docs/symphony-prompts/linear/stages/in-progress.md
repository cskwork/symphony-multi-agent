### IMPLEMENT  -- when state is `In Progress`

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/explore/`
   and re-read the most recent Recommendation. If the most recent ticket
   comment is a QA Failure or Review Findings comment, treat THOSE items
   as the only scope for this turn — fix exactly what the previous stage
   flagged, no drive-by changes. The fresh context that started this turn
   means earlier conversation is gone; the markdown and Linear comments
   are the contract.
2. Implement the chosen option from the Explore Recommendation (or, on a
   rewind, exclusively address the flagged failure items above); do not
   reopen the plan unless you find a fact the brief got wrong (in which
   case post a one-line note and proceed).
3. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
4. Pair the change with user-facing documentation under
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this ticket
   carries the `bug` label) — what changed, how a user observes it, any
   knobs/flags. Plain language, no jargon.
5. Open a draft PR. Post an Implementation comment with the PR link, the
   touched files, and the commit-style intent of each change.
6. Transition state to `Review`.
