### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

A QA pass that only inspects code is a failed QA. You must run something
and capture its output as evidence.

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/work/`
   and the most recent `## Review` / `## Review Findings`. Confirm what
   the change is supposed to deliver before deciding what to execute. The
   fresh context here has no memory of Implement — the artefacts are the
   brief.
2. Detect the project type and execute the matching real-world check:
   - **Tests**: run the full suite (`pytest -q`, `npm test`, `pnpm test`,
     `go test ./...`, `mvn test`, `cargo test`). All must pass.
   - **HTTP API**: capture the As-Is response by hitting the baseline build
     and the To-Be response by hitting the new build (curl / httpie /
     `requests`). Diff the two and confirm the change is what the ticket
     asked for, and nothing else. Save artefacts under
     `docs/{{ issue.identifier }}/qa/`.
   - **Web UI**: author a durable Playwright (or Cypress) spec at
     `docs/{{ issue.identifier }}/qa/e2e.spec.ts` that walks the user-facing
     flow end-to-end. Run it and save traces, videos, and HAR under
     `docs/{{ issue.identifier }}/qa/` (e.g. `traces/`, `videos/`, `har/`).
   - **CLI / script**: run the command and assert exit code plus the
     observable stdout/stderr / file output. Save the run log to
     `docs/{{ issue.identifier }}/qa/cli.log`.
3. Append `## QA Evidence` to the ticket with:
   - the exact commands run (one per line),
   - their exit codes,
   - a short excerpt of relevant output (3-10 lines), and
   - paths to any larger artefacts (logs, screenshots, traces) under
     `docs/{{ issue.identifier }}/qa/`.
4. If anything fails: set state back to `In Progress`, add a
   `## QA Failure` section describing what regressed, and stop. Do NOT
   silence, retry, or skip the failing check.
5. If everything passes: set state to `Learn`.
