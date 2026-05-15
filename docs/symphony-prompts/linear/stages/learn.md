### LEARN  -- when state is `Learn`

Make the next ticket cheaper. Distill what this ticket taught and write it back into `llm-wiki/`.

1. Read `docs/{{ issue.identifier }}/{explore,work,qa}/` and prior Linear comments (Recommendation, Implementation, QA Evidence) end-to-end.
2. Compare brief vs reality: which assumptions held or were wrong, which constraint/invariant only surfaced now, which prior wiki entry was incomplete or misleading.
3. Update `llm-wiki/`: edit existing entry by appending a `YYYY-MM-DD | <issue.identifier> | note` Decision log row and refreshing **Last updated**, OR create `llm-wiki/<topic-slug>.md` using the exact template below; then add/refresh its row in `INDEX.md` (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).

   ```
   # <Topic Title>

   **Summary:** one-paragraph overview.

   **Invariants & Constraints:**
   - ...

   **Files of interest:**
   - `path/to/file.py:123` — what the line region does.

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

4. Wiki integrity sweep before transitioning:
   - Duplicates: merge same-slug rows into the entry with newer Last updated, absorb distinct Invariants/Decision log rows, `git rm` loser file and drop its INDEX row.
   - Orphans: every `llm-wiki/*.md` (except `INDEX.md`) has an INDEX row; every INDEX row has a file. Reconcile both directions.
   - Stale: if Last updated > 90 days, append ` (stale?)` to the INDEX summary cell (idempotent).
   - Contradictions: if this ticket disproves an entry, update it and log the prior wrong claim; for cross-entry conflicts noticed in passing, post a `Wiki Conflict` comment pointing at both files.
5. Commit wiki edits onto the ticket's PR (same branch, not a separate PR).
6. Post a Learn comment with `## Learnings` (3-4 bullets of new facts/constraints/surprises) and `## Wiki Updates` (paths created/modified/removed, one line each with changelog: `merged`, `created`, `marked stale`, `dropped orphan row`, `updated invariant`).
7. Transition state to `Done`. If nothing new and sweep was clean, say so explicitly in the Learn comment and still transition.
