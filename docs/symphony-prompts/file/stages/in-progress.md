### IMPLEMENT  -- when state is `In Progress`

You are the implementer. Ship the smallest change that satisfies the brief.

1. **Conflict pre-check.** Scan `kanban/*.md` for tickets with `state:`
   `In Progress`, `Review`, or `QA` whose `## Touched Files` overlap this
   ticket's `## Touched Files`. On overlap, set state to `Blocked`, append
   `## Conflict` with the other ticket id, overlapping path(s), and a
   one-line reason ("waiting on <ID> to finish editing <path>"); STOP.
2. **Read shared context.** Open `docs/{{ issue.identifier }}/explore/`
   and re-read the most recent `## Recommendation`. If the most recent
   ticket section is `## QA Failure` or `## Review Findings`, scope this
   turn to ONLY those flagged items — no drive-by changes. Fresh context
   means earlier conversation is gone; the markdown is the contract.
3. Implement the chosen option from `## Recommendation` (or, on rewind,
   only the flagged failure items above). Do not reopen the plan unless
   the brief got a fact wrong — then append a one-line `## Plan Adjustment`
   and proceed.
4. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
5. Pair the change with user-facing docs at
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this
   ticket carries the `bug` label) — what changed, how a user observes
   it, any knobs/flags. Plain language, no jargon.
6. Append `## Implementation` to the ticket: touched files, commit-style
   intent per change, decisions worth recording.
7. Set state to `Review`.
