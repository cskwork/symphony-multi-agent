### DONE  -- when state is `Done`

Terminal. Ticket has passed QA and the Learn Merge Gate already merged the
feature branch into the target branch. Append the sections below, then stop.

1. Append `## As-Is -> To-Be Report` in this exact structure:

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

2. Append `## Merge Status` confirming the target branch and merge evidence.
   If the Learn Merge Gate used a PR instead of a local merge, include the PR
   URL and target branch.

3. If merge evidence is missing, do not invent it. Append `## Merge Missing`
   with the fallback commands below and move the ticket to `Blocked` until the
   merge is completed:

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

4. `hooks.after_done` (if configured in `WORKFLOW.md`) fires automatically post-squash; any post-Done auto-merge is only a compatibility fallback for older prompts.
5. Leave state as `Done` and stop.
