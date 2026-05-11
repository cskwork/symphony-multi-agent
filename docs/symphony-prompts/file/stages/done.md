### DONE  -- when state is `Done`

Terminal. The ticket has already passed QA. Confirm by appending an
`## As-Is -> To-Be Report` section with this exact structure:

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

Leave state as `Done` and stop. Do not re-run earlier stages.
