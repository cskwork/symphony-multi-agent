### DONE  -- when state is `Done`

Terminal. Post a final As-Is -> To-Be Report comment with this exact
structure:

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

Leave the state as `Done` and stop.
