### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

A QA pass that only inspects code is a failed QA. Run something and
capture its output as evidence.

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/work/`
   and the most recent Review / Review Findings comment. Confirm what the
   change is supposed to deliver before deciding what to execute. The
   fresh context here has no memory of Implement — the artefacts and
   prior comments are the brief.
2. Detect the project type and execute the matching real-world check:
   - **Tests**: run the full suite (`pytest -q`, `npm test`, etc.).
   - **HTTP API**: capture the As-Is response by hitting the baseline
     build and the To-Be response by hitting the new build (curl /
     httpie / `requests`). Diff the two. Save artefacts under
     `docs/{{ issue.identifier }}/qa/`.
   - **Web UI**: author a durable Playwright / Cypress spec at
     `docs/{{ issue.identifier }}/qa/e2e.spec.ts` that walks the flow
     end-to-end. Run it and save traces, videos, and HAR under
     `docs/{{ issue.identifier }}/qa/` (e.g. `traces/`, `videos/`, `har/`).
   - **CLI / script**: run the command and assert exit code plus
     observable stdout/stderr / file output. Save the run log to
     `docs/{{ issue.identifier }}/qa/cli.log`.
3. Post a QA Evidence comment listing:
   - the exact commands run,
   - their exit codes,
   - a short excerpt of relevant output (3-10 lines),
   - links to artefacts under `docs/{{ issue.identifier }}/qa/`.
4. If anything fails: transition state back to `In Progress`, post a QA
   Failure comment describing what regressed, and stop. Do NOT silence,
   retry, or skip the failing check.
5. If everything passes: transition state to `Learn`.
