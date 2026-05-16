### DONE  -- when state is `Done`

Terminal. Ticket has passed QA. Post the two comments below, then stop.

1. Post a final comment with `## As-Is -> To-Be Report` in this exact structure:

   ```
   ## As-Is -> To-Be Report

   ### As-Is
   - <prior behaviour, with evidence: response payload, log line, screenshot link>

   ### To-Be
   - <new behaviour, with the matching piece of evidence>

   ### Reasoning
   - Why this approach over the alternatives considered.
   - Trade-offs accepted (performance, complexity, scope).
   - Follow-ups intentionally deferred (with linked tickets).

   ### Evidence
   - Commands run during QA, with exit codes.
   - Test names, PR-attached artefacts.
   - Links to log lines or dashboards.
   - `docs/{{ issue.identifier }}/reproduce/` — bug reproduction (bug label only).
   - `docs/{{ issue.identifier }}/explore/` — exploration boost notes.
   - `docs/{{ issue.identifier }}/work/` — user-facing feature/bug docs.
   - `docs/{{ issue.identifier }}/verify/` — review HTTP baseline/PR artefacts.
   - `docs/{{ issue.identifier }}/qa/` — QA durable specs, traces, logs.
   ```

2. Post a separate `## Merge Command` comment with both blocks below:

   ```sh
   # From the host repo root (parent of the worktree):
   git -C "${SYMPHONY_WORKFLOW_DIR:-<host-repo>}" merge --no-ff symphony/{{ issue.identifier }}
   ```

   ```sh
   git -C "${SYMPHONY_WORKFLOW_DIR:-<host-repo>}" push -u origin symphony/{{ issue.identifier }}
   gh pr create --head symphony/{{ issue.identifier }} --base main \
     --title "{{ issue.identifier }}: {{ issue.title }}"
   ```

3. `hooks.after_done` (if configured in `WORKFLOW.md`) fires automatically post-squash; the Merge Command comment is the manual fallback.
4. Leave state as `Done` and stop.
