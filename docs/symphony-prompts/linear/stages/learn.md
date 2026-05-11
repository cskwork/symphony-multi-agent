### LEARN  -- when state is `Learn`

The point of Learn is to make the next ticket cheaper. Distill what this
ticket actually taught the team and write it back into `llm-wiki/` so
future Explore stages can find it.

1. **Read shared context first.** Walk `docs/{{ issue.identifier }}/explore/`,
   `work/`, `qa/` and the prior Linear comments (Recommendation,
   Implementation, QA Evidence) end-to-end. Learn's job is to compare
   brief vs. reality — the markdown and comment trail IS the brief.
2. Compare the Explore brief against reality:
   - Which assumptions held? Which were wrong? Why?
   - Which constraint, gotcha, or invariant only became visible during
     implementation, review, or QA?
   - Which prior wiki entry (if any) was incomplete or misleading?
3. For each non-trivial finding, update `llm-wiki/`:
   - If a relevant entry exists, edit it in place. Append to its
     **Decision log** with a `YYYY-MM-DD | <issue.identifier> | note`
     line and refresh **Last updated**.
   - Otherwise create `llm-wiki/<topic-slug>.md` with this exact shape:

     ```
     # <Topic Title>

     **Summary:** one-paragraph overview (what this domain area is and
     why a coding agent would need to know it).

     **Invariants & Constraints:**
     - ...

     **Files of interest:**
     - `path/to/file.py:123` — what the line region does.

     **Decision log:**
     - YYYY-MM-DD | <issue.identifier> | what changed and why.

     **Last updated:** YYYY-MM-DD by <issue.identifier>.
     ```

   - Add or refresh the matching row in `llm-wiki/INDEX.md`
     (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).
     Create `INDEX.md` with a header row if it does not yet exist.
4. Commit the wiki edits onto the ticket's PR (same branch — wiki updates
   are part of the change). Do not push wiki edits in a separate PR.
5. Post a Learn comment with two sections:
   - `## Learnings` — bullets of new facts, constraints, or surprises
     this ticket exposed.
   - `## Wiki Updates` — list of `llm-wiki/<file>.md` paths created or
     modified, one line each with a brief changelog.
6. Transition state to `Done`. If you found nothing genuinely new, say
   so explicitly in the Learn comment ("no new wiki entries; existing
   coverage was correct") and still transition.
