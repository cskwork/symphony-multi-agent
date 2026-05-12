### REVIEW  -- when state is `Review`

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/work/`
   and re-read the most recent Implementation comment. If a Review Findings
   comment from a prior pass exists, confirm those specific items are now
   resolved before opening new findings.
2. Read the diff on the PR (`git diff origin/main...HEAD`). Re-read the
   touched files end-to-end, not just the hunks.
3. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
4. Verify with live HTTP proof when the change touches an API. Hit both
   baseline (As-Is) and the new build (To-Be) with curl/httpie/`requests`
   and save under `docs/{{ issue.identifier }}/verify/`:
   `baseline.json`, `pr.json`, `diff.txt`, `curl.log`. Code-only review
   for an API change is not enough.
5. Classify findings into a severity table: `severity | file:line | fix`.
   Cap at 6 rows in the comment body; spillover goes to
   `docs/{{ issue.identifier }}/review/details.md`.
6. **If any CRITICAL, HIGH, or MEDIUM finding exists:** transition state
   back to `In Progress`, post a Review Findings comment with the
   Plain-Korean header + the severity table (referencing any verify
   artefacts under `docs/{{ issue.identifier }}/verify/`), and STOP. Do
   NOT fix the findings inside Review — that is In Progress's job, with a
   fresh context. Symphony will dispatch a new fix turn automatically.
7. If the only findings are LOW (or none): post a Review comment
   with the Plain-Korean header + the same severity table — flag the
   deferred LOW items in the same comment so Learn can address them — and
   transition state to `QA`.
8. If something is genuinely unfixable / out of scope: transition state to
   `Blocked`, post a Blocker comment with what is needed and stop.
