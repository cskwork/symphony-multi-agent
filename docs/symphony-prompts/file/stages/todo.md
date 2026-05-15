### TRIAGE  -- when state is `Todo`

1. Read the ticket end-to-end. Confirm there is enough information
   (description, acceptance criteria, blocking links) to start exploring.
2. If the ticket is under-specified or ambiguous, append a `## Triage`
   section listing the missing inputs and set state to `Blocked`.
3. Otherwise append a one-line `## Triage` ("ticket is actionable; routing
   to Explore") and set state to `Explore`. Do no implementation in
   `Todo` — research belongs in `Explore`.
{% for label in issue.labels %}{% if label == "bug" %}
4. Because this ticket carries the `bug` label, capture the symptom *as is*
   before any RCA. Author a Playwright (or Cypress) spec that walks the
   failing flow at `docs/{{ issue.identifier }}/reproduce/repro.spec.ts`,
   run it, and save trace/screenshot/console under
   `docs/{{ issue.identifier }}/reproduce/`. Append `## Reproduction` to
   the ticket with the command, spec path, and a 3-10 line failure excerpt.
   Triage still ends with state `Explore`.
{% endif %}{% endfor %}
