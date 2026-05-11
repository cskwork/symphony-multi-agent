### TRIAGE  -- when state is `Todo`

1. Read the ticket end-to-end. Confirm there is enough information
   (description, acceptance criteria, blocking links) to start exploring.
2. If the ticket is under-specified or ambiguous, post a Triage comment
   listing the missing inputs and transition state to `Blocked`.
3. Otherwise post a one-line Triage comment ("ticket is actionable; routing
   to Explore") and transition state to `Explore`. Do no implementation in
   `Todo` — research belongs in `Explore`.
{% for label in issue.labels %}{% if label == "bug" %}
4. Because this ticket carries the `bug` label, capture the symptom *as is*
   before any RCA. Author a Playwright (or Cypress) spec that walks the
   failing flow at `docs/{{ issue.identifier }}/reproduce/repro.spec.ts`,
   run it, and save trace/screenshot/console under
   `docs/{{ issue.identifier }}/reproduce/`. Post a Reproduction comment
   with the command, spec path, and a 3-10 line failure excerpt.
   Triage still ends with state `Explore`.
{% endif %}{% endfor %}
