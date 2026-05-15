### DONE  -- when state is `Done`

Terminal. Ticket has passed QA.

1. Append `## As-Is -> To-Be Report` with this exact structure:

   ```
   ## As-Is -> To-Be Report

   ### As-Is
   - <prior behaviour, with evidence: response payload, log line, screenshot path>

   ### To-Be
   - <new behaviour, with the matching piece of evidence>

   ### Reasoning
   - Why this approach over the alternatives considered.
   - Trade-offs accepted (performance, complexity, scope).
   - Follow-ups intentionally deferred (with ticket / file references).

   ### Evidence
   - Commands run during QA, with exit codes.
   - Test names, file paths, artefact locations.
   - Links to relevant log lines under `log/`.
   - `docs/{{ issue.identifier }}/reproduce/` — bug reproduction (bug label only).
   - `docs/{{ issue.identifier }}/explore/` — exploration boost notes.
   - `docs/{{ issue.identifier }}/work/` — user-facing feature/bug docs.
   - `docs/{{ issue.identifier }}/verify/` — review HTTP baseline/PR artefacts.
   - `docs/{{ issue.identifier }}/qa/` — QA durable specs, traces, logs.
   ```

2. Append `## Merge Command` with both blocks below:

   ```sh
   # From the host repo root (parent of the worktree):
   git -C "${SYMPHONY_WORKFLOW_DIR:-<host-repo>}" merge --no-ff symphony/{{ issue.identifier }}
   ```

   ```sh
   git -C "${SYMPHONY_WORKFLOW_DIR:-<host-repo>}" push -u origin symphony/{{ issue.identifier }}
   gh pr create --head symphony/{{ issue.identifier }} --base main \
     --title "{{ issue.identifier }}: {{ issue.title }}" \
     --body-file docs/{{ issue.identifier }}/qa/repro-after.log
   ```

3. If `hooks.after_done` is configured in `WORKFLOW.md`, it fires automatically post-squash; the Merge Command block is the manual fallback.
4. Leave state as `Done` and stop.
