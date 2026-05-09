---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Review, QA]
  terminal_states: [Done, Cancelled, Blocked]
  # Optional one-line legend rendered under each TUI column header.
  state_descriptions:
    Todo: "Plan + first failing test"
    "In Progress": "TDD loop, draft PR"
    Review: "Read diff, fix CRITICAL/HIGH"
    QA: "Execute real code, capture evidence"
    Done: "As-Is -> To-Be report"

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    git clone --depth=1 git@github.com:my-org/my-repo.git .
  before_run: |
    git fetch origin main
    git reset --hard origin/main
  after_run: |
    echo "run finished at $(date)"

agent:
  kind: codex          # codex | claude | gemini
  max_concurrent_agents: 4
  max_turns: 20
  max_concurrent_agents_by_state:
    Todo: 2
    "In Progress": 4
    Review: 2
    QA: 2

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

claude:
  command: claude -p --output-format stream-json --verbose

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass `""`
  # so the prompt comes purely from stdin.
  command: 'gemini -p ""'

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

tui:
  language: en          # `en` (default) or `ko` — chrome only. SYMPHONY_LANG env overrides.
---
You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}This is retry attempt {{ attempt }}. Read the previous `## Resolution`,
`## Blocker`, or `## QA Failure` section before acting and address the root cause,
not the symptom.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

{% if issue.labels %}Labels: {{ issue.labels | join: ", " }}{% endif %}

{% if issue.blocked_by %}
This ticket depends on:
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }} ({{ blocker.state }})
{% endfor %}
{% endif %}

## Production pipeline (six stages, no skipping)

Every ticket flows through the same gates. Honour the gate that matches
`{{ issue.state }}`. Each stage owns one transition; never jump ahead.

```
  Todo / In Progress  ->  Review  ->  QA  ->  Done
                              \                 ^
                               +-> Blocked      |
                                                |
              (QA failure rewinds to In Progress)
```

The ticket file lives at `kanban/{{ issue.identifier }}.md`. Edit the YAML
front matter `state:` field to transition; append narrative sections to the
body. Symphony reconciles on the next poll tick.

## Stage rules

### IMPLEMENT  -- when state is `Todo` or `In Progress`

1. Research first. Search the workspace, the existing test suite, and the
   relevant library docs before writing anything new. Reuse battle-tested
   helpers over hand-rolled ones.
2. Write a `## Plan` section in the ticket: the smallest sustainable change,
   the files you intend to touch, and the test you will write first.
3. TDD loop: write a failing test, make it pass, refactor. No production
   code without a test that exercises it.
4. Append `## Implementation` to the ticket: list the touched files, the
   commit-style intent of each change, and any decisions worth recording.
5. Set state to `Review`.

### REVIEW  -- when state is `Review`

1. Read your own diff (`git diff`, `git status`, or whatever the workspace
   provides). Re-read the touched files end-to-end, not just the hunks.
2. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
3. Fix every CRITICAL and HIGH issue you find. Record findings under
   `## Review` (one bullet per issue with `severity | file:line | fix`).
4. If something is genuinely out of scope or unfixable, set state to
   `Blocked` and append a `## Blocker` explaining what is needed.
5. Otherwise set state to `QA`.

### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

A QA pass that only inspects code is a failed QA. You must run something
and capture its output as evidence.

1. Detect the project type and execute the matching real-world check:
   - **Tests**: run the full suite (`pytest -q`, `npm test`, `pnpm test`,
     `go test ./...`, `mvn test`, `cargo test`). All must pass.
   - **HTTP API**: capture the As-Is response by hitting the baseline build
     and the To-Be response by hitting the new build (curl / httpie /
     `requests`). Diff the two and confirm the change is what the ticket
     asked for, and nothing else.
   - **Web UI**: author or run a Playwright (or Cypress) script that walks
     the user-facing flow end-to-end. Save screenshots or traces under
     `qa-artifacts/` in the workspace.
   - **CLI / script**: run the command and assert exit code plus the
     observable stdout/stderr / file output.
2. Append `## QA Evidence` to the ticket with:
   - the exact commands run (one per line),
   - their exit codes,
   - a short excerpt of relevant output (3-10 lines), and
   - paths to any larger artefacts (logs, screenshots, traces).
3. If anything fails: set state back to `In Progress`, add a
   `## QA Failure` section describing what regressed, and stop. Do NOT
   silence, retry, or skip the failing check.
4. If everything passes: set state to `Done`.

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
```

Leave state as `Done` and stop. Do not re-run earlier stages.

## Hard rules (apply in every stage)

- Never skip a stage. Never mark `Done` without `## QA Evidence`.
- Never silence failing tests, hide errors, or add fake success paths. Fix
  the root cause or move the ticket to `Blocked`.
- Touch only what the ticket requires. No drive-by refactors.
- Record reasoning for non-trivial decisions in
  `log/changelog-YYYY-MM-DD.md` (append; do not overwrite).
