### TRIAGE  -- when state is `Todo`

Triage the ticket; route it. No implementation here — research belongs in `Explore`.

1. Read the ticket end-to-end. Check for description, acceptance criteria, and blocking links.
2. If under-specified or ambiguous: post a Triage comment listing the missing inputs, transition state to `Blocked`.
3. Otherwise: post a one-line Triage comment ("ticket is actionable; routing to Explore") and transition state to `Explore`.
{% for label in issue.labels %}{% if label == "bug" %}
4. `bug` label — capture the symptom *as is* before any RCA. Author a Playwright (or Cypress) spec walking the failing flow at `docs/{{ issue.identifier }}/reproduce/repro.spec.ts`, run it, save trace/screenshot/console under `docs/{{ issue.identifier }}/reproduce/`. Post a Reproduction comment with the command, spec path, and a 3-10 line failure excerpt. Triage still ends with state `Explore`.
{% endif %}{% endfor %}
