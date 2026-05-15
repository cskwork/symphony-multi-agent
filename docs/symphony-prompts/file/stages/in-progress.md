### IMPLEMENT  -- when state is `In Progress`

1. **Conflict pre-check.** Scan `kanban/*.md` for tickets whose `state:`
   is `In Progress`, `Review`, or `QA` and whose `## Touched Files`
   overlaps with this ticket's `## Touched Files`. If overlap exists,
   set state to `Blocked`, append a `## Conflict` section listing the
   other ticket id, the overlapping path(s), and a one-line reason
   ("waiting on <ID> to finish editing <path>"); STOP and do not implement.
2. **Read shared context first.** Open `docs/{{ issue.identifier }}/explore/`
   and re-read the most recent `## Recommendation`. If the most recent
   ticket section is `## QA Failure` or `## Review Findings`, treat THOSE
   items as the only scope for this turn — fix exactly what the previous
   stage flagged, no drive-by changes. The fresh context that started
   this turn means earlier conversation is gone; the markdown is the
   contract.
3. Implement the chosen option from `## Recommendation` (or, on a rewind,
   exclusively address the flagged failure items above); do not reopen
   the plan unless you find a fact the brief got wrong (in which case
   append a one-line `## Plan Adjustment` note and proceed).
4. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test that exercises it.
5. Pair the change with user-facing documentation under
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this ticket
   carries the `bug` label) — what changed, how a user observes it, any
   knobs/flags. Plain language, no jargon.
6. Append `## Implementation` to the ticket: list the touched files, the
   commit-style intent of each change, and any decisions worth recording.
7. Set state to `Review`.
