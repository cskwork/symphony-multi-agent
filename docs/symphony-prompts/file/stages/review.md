### REVIEW  -- when state is `Review`

You are the reviewer. Find issues; do not fix them.

1. **Read shared context.** Open `docs/{{ issue.identifier }}/work/` and
   re-read the most recent `## Implementation` section. If a prior
   `## Review Findings` exists, confirm those specific items are resolved
   before opening new findings.
2. Review the current workspace code and deliverables. Use `git diff` /
   `git status` only as a fallback; prefer the latest In Progress wip
   commit (`git show --stat`, then `git show --unified=0`) to identify
   files and changed line ranges, then open touched files end-to-end.
   Docs are reviewable when they are deliverables; ignore only root
   symlink/junction metadata for host-backed `kanban/` or `prompt/`
   plumbing unless the ticket is explicitly about Symphony setup.
3. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
   Then scan `git log --format=%s $(git config symphony.basesha)..HEAD`
   for `[no-test]` commit-subject markers (Symphony's `after_run` hook
   prefixes wip commits that changed production code without a paired
   test). Each `[no-test]` marker becomes a HIGH severity row in
   `## Review Findings` (file: the unpaired production path; fix: "add a
   test exercising this change") UNLESS the In Progress turn was
   documentation-only (only `docs/`, `kanban/`, `.symphony/` paths
   changed in that commit). Note the exemption in `## Review` if you
   apply it.
4. Use live HTTP proof only when this ticket changed runtime API behavior
   or its acceptance criteria explicitly require endpoint execution. For
   docs-only API mapping / scenario-definition tickets, verify against
   source contracts, route definitions, schemas, and existing tests instead;
   do not probe live endpoints just because the document names APIs. When
   live proof is required, hit both baseline (As-Is) and the new build
   (To-Be) with curl/httpie/`requests` and save under
   `docs/{{ issue.identifier }}/verify/`: `baseline.json`, `pr.json`,
   `diff.txt`, `curl.log`.
5. **Security Audit (mandatory, before `## Review`).** Append a
   `## Security Audit` section with exactly this 7-row table — same row
   order, no extras, no spillover:
   `check | verdict (pass/fail/n/a) | evidence (path:line or "n/a — <reason>")`
   - `secrets`
   - `input-validation`
   - `sql-injection`
   - `xss`
   - `csrf`
   - `authz`
   - `rate-limit`
   `n/a` is acceptable but the evidence cell must explain why (e.g.
   `n/a — docs-only change`). Any `fail` row auto-promotes to a CRITICAL
   row in `## Review Findings` (file = the cited `path:line`, fix = "fix
   the security gap named in the audit") and triggers Review → In Progress.
6. Classify findings into a severity table: `severity | file:line | fix`.
   Include `[no-test]` HIGH rows from step 3 and `fail`-promoted CRITICAL
   rows from step 5. Cap at 6 rows in the body; spillover goes to
   `docs/{{ issue.identifier }}/review/details.md`.
7. **If any CRITICAL, HIGH, or MEDIUM finding exists:** set state back to
   `In Progress`, append `## Review Findings` with the Plain-Korean
   header + the severity table (referencing any verify artefacts under
   `docs/{{ issue.identifier }}/verify/`), and STOP. Do NOT fix findings
   inside Review — that is In Progress's job, with a fresh context.
   Symphony dispatches a new fix turn automatically.
8. If prior `## Review Findings` are resolved and no CRITICAL, HIGH, or
   MEDIUM finding remains, do not append another `## Review Findings`
   section. Append `## Review` and set state to `QA` in the same turn;
   staying in `Review` after a clean review is a workflow failure.
9. If the only findings are LOW (or none): append `## Review` with the
   Plain-Korean header + the same severity table — flag deferred LOW
   items in the same section so Learn can address them — and set state
   to `QA`.
10. If something is genuinely out of scope or unfixable: set state to
    `Blocked` and append `## Blocker` explaining what is needed.
