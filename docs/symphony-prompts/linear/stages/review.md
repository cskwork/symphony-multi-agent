### REVIEW  -- when state is `Review`

You are the reviewer. Find issues; do not fix them.

1. **Read shared context.** Open `docs/{{ issue.identifier }}/work/` and
   re-read the most recent Implementation comment. If a prior Review
   Findings comment exists, confirm those specific items are resolved
   before opening new findings.
2. Review the current workspace code and deliverables. Use the latest
   In Progress commit or PR diff to identify files and changed line
   ranges, then open touched files end-to-end. Docs are reviewable when
   they are deliverables; ignore only root symlink/junction metadata for
   host-backed `kanban/` or `prompt/` plumbing unless the issue is
   explicitly about Symphony setup.
3. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
4. Use live HTTP proof only when this issue changed runtime API behavior
   or its acceptance criteria explicitly require endpoint execution. For
   docs-only API mapping / scenario-definition issues, verify against
   source contracts, route definitions, schemas, and existing tests instead;
   do not probe live endpoints just because the document names APIs. When
   live proof is required, hit both baseline (As-Is) and the new build
   (To-Be) with curl/httpie/`requests` and save under
   `docs/{{ issue.identifier }}/verify/`: `baseline.json`, `pr.json`,
   `diff.txt`, `curl.log`.
5. Classify findings into a severity table: `severity | file:line | fix`.
   Cap at 6 rows in the comment body; spillover goes to
   `docs/{{ issue.identifier }}/review/details.md`.
6. **If any CRITICAL, HIGH, or MEDIUM finding exists:** transition state
   back to `In Progress`, post a Review Findings comment with the
   Plain-Korean header + the severity table (referencing any verify
   artefacts under `docs/{{ issue.identifier }}/verify/`), and STOP. Do
   NOT fix findings inside Review — that is In Progress's job, with a
   fresh context. Symphony dispatches a new fix turn automatically.
7. If prior Review Findings are resolved and no CRITICAL, HIGH, or
   MEDIUM finding remains, do not post another Review Findings comment.
   Post a Review comment and transition state to `QA` in the same turn;
   staying in `Review` after a clean review is a workflow failure.
8. If the only findings are LOW (or none): post a Review comment with
   the Plain-Korean header + the same severity table — flag deferred LOW
   items in the same comment so Learn can address them — and transition
   state to `QA`.
9. If something is genuinely unfixable / out of scope: transition state
   to `Blocked`, post a Blocker comment with what is needed and stop.
