### TRIAGE  -- when state is `Todo`

Triage the ticket; route it. No implementation here — research belongs in `Explore`.

1. Read the ticket end-to-end. Check for description, acceptance criteria, and blocking links.
2. If under-specified or ambiguous: append `## Triage` listing the missing inputs, set state to `Blocked`.
3. Otherwise: append a one-line `## Triage` ("ticket is actionable; routing to Explore") and set state to `Explore`.
{% for label in issue.labels %}{% if label == "bug" %}
4. `bug` label — capture the symptom *as is* before any RCA. Author a Playwright (or Cypress) spec walking the failing flow at `docs/{{ issue.identifier }}/reproduce/repro.spec.ts`, run it, save trace/screenshot/console under `docs/{{ issue.identifier }}/reproduce/`. Append `## Reproduction` with the command, spec path, and a 3-10 line failure excerpt. Triage still ends with state `Explore`.
{% endif %}{% endfor %}
