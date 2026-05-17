### IMPLEMENT  -- when state is `In Progress`

You are the implementer. Ship the smallest change that satisfies the brief.

1. **Read the plan first.** Re-read the most recent `## Plan` and
   `docs/{{ issue.identifier }}/plan/implementation-plan.md` if it exists.
   That plan should be enough to implement. Use Explore notes, llm-wiki, or
   other docs only as reference material when the plan is ambiguous or
   missing a required detail. If `## Plan` is missing, set state to `Plan`,
   append `## Plan Missing`, and stop. Fresh context means earlier
   conversation is gone; the markdown is the contract.
2. **Rewind scope.** If `$SYMPHONY_REWIND_SCOPE` is set (Symphony injects
   it on Review→In Progress / QA→In Progress rewinds), it is a JSON array
   of `{severity, file, line, fix}` rows from the most recent
   `## Review Findings` or `## QA Failure`. Implement only fixes for those
   files. If you genuinely need to touch a file not listed, append
   `## Scope Expansion` with a one-line rationale per extra file and
   proceed — this does not block, but Symphony marks the wip commit
   `[scope-expand]`. When the env var is unset, follow `## Plan` normally;
   if the most recent ticket section is `## QA Failure` or
   `## Review Findings` and the env var is missing, fall back to scoping
   the turn to those flagged items.
3. Implement the chosen option from `## Plan` (or, on rewind, only the
   flagged failure items above). Do not reopen the plan unless the brief
   got a fact wrong — then append a one-line `## Plan Adjustment` and
   proceed.
4. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
5. Pair the change with user-facing docs at
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this
   ticket carries the `bug` label) — what changed, how a user observes
   it, any knobs/flags. Plain language, no jargon.
6. Before `Review`, write one concise commit subject to
   `.symphony/commit-message.txt`; Symphony commits it after the turn.
   Append `## Implementation` with intent per change and decisions worth
   recording.
7. Set state to `Review`.
