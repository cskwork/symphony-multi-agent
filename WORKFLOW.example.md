---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, "In Progress", Review, QA]
  terminal_states: [Closed, Cancelled, Canceled, Duplicate, Done]
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
  kind: codex          # codex | claude | gemini | pi
  max_concurrent_agents: 4
  max_turns: 20
  max_retry_backoff_ms: 300000
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
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

gemini:
  # `gemini -p` (no argument) prints help in Gemini CLI 0.39+; pass an
  # empty `""` so the prompt comes purely from stdin.
  command: 'gemini -p ""'
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

pi:
  # `pi --mode json -p ""` emits JSONL events; stdin carries the prompt and
  # `--session <id>` is appended automatically on continuation turns.
  # Auth: sign in once with `pi` → `/login` (OAuth). Credentials are cached
  # at `~/.pi/agent/auth.json` and inherited by every subprocess Symphony
  # spawns — no env var or `--api-key` flag is needed.
  command: 'pi --mode json -p ""'
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

tui:
  language: en               # `en` (default) or `ko`. SYMPHONY_LANG env overrides.
  max_cards_per_column: 6    # cap each column at N cards; rest collapses to "+M more"
---

You are picking up issue {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}This is retry attempt {{ attempt }}. Read the previous Linear
comment thread first and address the root cause from the prior failure, not the
symptom.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

{% if issue.labels %}Labels: {{ issue.labels | join: ", " }}{% endif %}

{% if issue.blocked_by %}
This issue depends on:
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }} ({{ blocker.state }})
{% endfor %}
{% endif %}

## Production pipeline (six stages, no skipping)

Every issue flows through the same gates. Honour the gate that matches
`{{ issue.state }}`. Each stage owns one transition; never jump ahead.

```
  Todo / In Progress  ->  Review  ->  QA  ->  Done
                              \                 ^
                               +-> Blocked      |
                                                |
              (QA failure rewinds to In Progress)
```

State transitions and stage notes are written via the `linear_graphql` tool:
`issueUpdate` for state changes, `commentCreate` for the per-stage notes
described below. Each stage produces one comment.

## Stage rules

### IMPLEMENT  -- when state is `Todo` or `In Progress`

1. Research first: search the repo, the test suite, and library docs before
   writing anything new. Reuse battle-tested helpers over hand-rolled ones.
2. Post a Plan comment: the smallest sustainable change, the files you
   intend to touch, the test you will write first.
3. TDD loop: write a failing test, make it pass, refactor. No production
   code without a test exercising it.
4. Open a draft PR. Post an Implementation comment with the PR link, the
   touched files, and the commit-style intent of each change.
5. Transition state to `Review`.

### REVIEW  -- when state is `Review`

1. Read the diff on the PR (`git diff origin/main...HEAD`). Re-read the
   touched files end-to-end, not just the hunks.
2. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
3. Fix every CRITICAL and HIGH issue. Post a Review comment with one
   bullet per finding (`severity | file:line | fix`).
4. If unfixable / out of scope: transition state to `Blocked`, post a
   Blocker comment with what is needed and stop.
5. Otherwise transition state to `QA`.

### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

A QA pass that only inspects code is a failed QA. Run something and
capture its output as evidence.

1. Detect the project type and execute the matching real-world check:
   - **Tests**: run the full suite (`pytest -q`, `npm test`, etc.).
   - **HTTP API**: capture the As-Is response by hitting the baseline
     build and the To-Be response by hitting the new build (curl /
     httpie / `requests`). Diff the two.
   - **Web UI**: run or author a Playwright / Cypress script that walks
     the flow end-to-end. Attach screenshots / traces to the PR.
   - **CLI / script**: run the command and assert exit code plus
     observable stdout/stderr / file output.
2. Post a QA Evidence comment listing:
   - the exact commands run,
   - their exit codes,
   - a short excerpt of relevant output (3-10 lines),
   - links to PR-attached artefacts (screenshots, logs, traces).
3. If anything fails: transition state back to `In Progress`, post a QA
   Failure comment describing what regressed, and stop. Do NOT silence,
   retry, or skip the failing check.
4. If everything passes: transition state to `Done`.

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
```

Leave the state as `Done` and stop.

## Hard rules

- Never skip a stage. Never mark `Done` without a QA Evidence comment.
- Never silence failing tests or hide errors. Fix the root cause or move
  to `Blocked`.
- Touch only what the issue requires. No drive-by refactors.
