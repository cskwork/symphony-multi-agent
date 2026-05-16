### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

QA must execute real code. Inspecting the diff is not QA.

1. Read `docs/{{ issue.identifier }}/work/` and the most recent `## Review` / `## Review Findings` to learn what the change should deliver.

2. Map the API surface from the diff: method, path, auth, request schema, response schema. Append as `## API Surface`. If no API, jump to **Non-API fallbacks**.

3. Source 3-5 realistic payloads (happy / edge / invalid):
   - Preferred: a database MCP tool (`mcp__*postgres*`, `mcp__*mysql*`, `mcp__*sqlite*`, `mcp__*mssql*`, `mcp__*bigquery*`, `mcp__*mongodb*`) or the `database` skill — inspect schema, sample rows with `SELECT ... LIMIT`.
   - Fallback: synthesize from schema / model / DTO / migration / OpenAPI files and tag each file with `"_source": "synthesized from <path>"`.
   - Save as `docs/{{ issue.identifier }}/qa/payloads/<scenario>.json`. Mask PII. Never invent fields the schema does not declare.

4. Boot As-Is and To-Be:
   - Use `qa.boot.command` from `WORKFLOW.md` if set, exporting `SYMPHONY_QA_PORT` from `qa.boot.asis_port` / `qa.boot.tobe_port`, merging `qa.boot.env`, and bringing up `qa.boot.compose_file` first if specified. Else fall back to the project's standard boot on two free ports.
   - As-Is = `git config symphony.basesha` checked out via `git worktree add ../asis $(git config symphony.basesha)`. To-Be = current HEAD.
   - If `qa.boot.health_url` is set, poll `${url//\$\{PORT\}/<port>}` until 200 or fail QA.

5. Replay every payload against both builds, capturing status, body, headers of interest, and `latency_ms` (wall-clock). Save raw to `docs/{{ issue.identifier }}/qa/runs/<scenario>.{asis,tobe}.json` with `latency_ms` at top level. Tear down both servers and `git worktree remove ../asis`.

6. Diff and judge:
   - Per-scenario diff at `docs/{{ issue.identifier }}/qa/diff/<scenario>.diff`. Confirm only the intended change — no surprise renames, leaked PII, broken unrelated scenarios, or status regressions on invalid/unauthorized rows.
   - Performance gate from `qa.regression_budget`: for each scenario where As-Is `latency_ms` ≥ `min_baseline_ms`, fail if To-Be `latency_ms` > `latency_factor × As-Is`. Record breach as `scenario | as-is ms | to-be ms | factor`. `latency_factor: 0` disables.

7. Bug repro closure (only if `bug` label): re-run `docs/{{ issue.identifier }}/reproduce/repro.spec.ts` against To-Be, save to `docs/{{ issue.identifier }}/qa/repro-after.log`, and require it to pass. Never skip.

8. Append `## QA Evidence` to the ticket with: payload data source (DB tool + query, or `synthesized from <schema file>`), boot recipe used, exact commands run with exit codes, a `scenario × {As-Is status, As-Is ms, To-Be status, To-Be ms, verdict}` matrix, the repro re-run result line for `bug` tickets, and paths under `docs/{{ issue.identifier }}/qa/`.

9. On any failure (correctness, latency, repro, or any server-reported HIGH issue): set state to `In Progress`, append `## QA Failure` naming the scenario and exact field/status/latency/severity that regressed, stop. No silencing, retrying, or skipping.

10. On pass: set state to `Learn`.

---

**Non-API fallbacks** (only when step 2 finds no API surface):
- Tests: run the full suite (`pytest -q`, `npm test`, `pnpm test`, `go test ./...`, `mvn test`, `cargo test`). All must pass.
- Web UI: write a Playwright (or Cypress) spec at `docs/{{ issue.identifier }}/qa/e2e.spec.ts`; save traces, videos, HAR under `docs/{{ issue.identifier }}/qa/`.
- CLI: run the command, assert exit code and stdout/stderr / file output, save to `docs/{{ issue.identifier }}/qa/cli.log`.

Step 7 (bug repro closure) still applies in non-API mode if `reproduce/repro.spec.ts` exists.
