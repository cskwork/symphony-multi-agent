---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Done, Cancelled, Blocked]
  # Optional one-line legend rendered under each TUI column header.
  state_descriptions:
    Todo: "Triage; route to Explore"
    Explore: "Brief from llm-wiki + git + code"
    "In Progress": "TDD loop, draft PR"
    Review: "Read diff, fix CRITICAL/HIGH"
    QA: "Execute real code, capture evidence"
    Learn: "Distill learnings, update llm-wiki"
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
  max_concurrent_agents_by_state:
    Todo: 2
    Explore: 2
    "In Progress": 4
    Review: 2
    QA: 2
    Learn: 2

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

pi:
  # `pi --mode json` emits JSONL events; stdin carries the prompt.
  # Auth: sign in once with `pi` → `/login` (OAuth). Credentials cached at
  # `~/.pi/agent/auth.json` are inherited automatically.
  command: 'pi --mode json -p ""'

server:
  port: 9999            # optional JSON API; the primary UI is `symphony tui`

tui:
  language: en               # `en` (default) or `ko`. SYMPHONY_LANG env overrides.
  max_cards_per_column: 6    # cap each column at N cards; rest collapses to "+M more"
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

## Production pipeline (seven stages, no skipping)

Every ticket flows through the same gates. Honour the gate that matches
`{{ issue.state }}`. Each stage owns one transition; never jump ahead.

```
  Todo  ->  Explore  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Done
                              \                       \                    ^
                               +-> Blocked             +-> Blocked          |
                                                                            |
                              (QA failure rewinds to In Progress)
```

`llm-wiki/` is the project's domain knowledge base — one Markdown entry
per topic, plus an `INDEX.md` listing them. It lives at the workspace
root next to the source code (parallel to `kanban/`). Explore reads it
before any new work; Learn writes back to it after QA passes. Treat it
as a living memory that future tickets depend on. The first Learn stage
that runs creates the directory if it does not yet exist.

The ticket file lives at `kanban/{{ issue.identifier }}.md`. Edit the YAML
front matter `state:` field to transition; append narrative sections to the
body. Symphony reconciles on the next poll tick.

## Stage rules

### TRIAGE  -- when state is `Todo`

1. Read the ticket end-to-end. Confirm there is enough information
   (description, acceptance criteria, blocking links) to start exploring.
2. If the ticket is under-specified or ambiguous, append a `## Triage`
   section listing the missing inputs and set state to `Blocked`.
3. Otherwise append a one-line `## Triage` ("ticket is actionable; routing
   to Explore") and set state to `Explore`. Do no implementation in
   `Todo` — research belongs in `Explore`.

### EXPLORE  -- when state is `Explore`

You are a domain-knowing researcher walking three lenses in one turn:
**domain expert** (what does this code mean?), **implementer** (smallest
sustainable change?), **risk reviewer** (what could go wrong?).

1. Open `llm-wiki/INDEX.md`. Read every entry whose topic plausibly relates
   to the ticket. Follow links into the entry files. If `llm-wiki/` does
   not exist yet, note that and continue — Learn will seed it later.
2. Skim git history for prior work in adjacent areas: for each file the
   ticket likely touches, run `git log --oneline -- <path>` and read the
   one or two most relevant commits in full (`git show <sha>`). Capture
   why prior changes were made, not just what they did.
3. Read the actual source files end-to-end (not just hunks) so the brief
   reflects current state, not stale memory.
4. Apply each lens explicitly and append three sections to the ticket:
   - `## Domain Brief` — key facts, invariants, and references
     (`path:line`, wiki entry titles, commit SHAs) the implementer must
     know before writing code.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs
     (complexity, blast radius, reversibility). Be specific about files
     touched and tests added per option.
   - `## Recommendation` — the option you choose, the rationale (why
     this lens won), the risks accepted, and the first failing test
     the implementer should write.
5. Set state to `In Progress`.

### IMPLEMENT  -- when state is `In Progress`

1. Read the `## Recommendation` section from Explore first. Implement the
   chosen option; do not reopen the plan unless you find a fact the brief
   got wrong (in which case append a one-line `## Plan Adjustment` note
   and proceed).
2. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test that exercises it.
3. Append `## Implementation` to the ticket: list the touched files, the
   commit-style intent of each change, and any decisions worth recording.
4. Set state to `Review`.

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
4. If everything passes: set state to `Learn`.

### LEARN  -- when state is `Learn`

The point of Learn is to make the next ticket cheaper. Distill what this
ticket actually taught you and write it back into `llm-wiki/` so future
Explore stages can find it.

1. Compare the Explore brief against reality:
   - Which assumptions held? Which were wrong? Why?
   - Which constraint, gotcha, or invariant only became visible during
     implementation, review, or QA?
   - Which prior wiki entry (if any) was incomplete or misleading?
2. For each non-trivial finding, update `llm-wiki/`:
   - If a relevant entry exists, edit it in place. Append to its
     **Decision log** with a `YYYY-MM-DD | <issue.identifier> | note`
     line and refresh **Last updated**.
   - Otherwise create `llm-wiki/<topic-slug>.md` with this exact shape:

     ```
     # <Topic Title>

     **Summary:** one-paragraph overview (what this domain area is and
     why a coding agent would need to know it).

     **Invariants & Constraints:**
     - ...

     **Files of interest:**
     - `path/to/file.py:123` — what the line region does.

     **Decision log:**
     - YYYY-MM-DD | <issue.identifier> | what changed and why.

     **Last updated:** YYYY-MM-DD by <issue.identifier>.
     ```

   - Add or refresh the matching row in `llm-wiki/INDEX.md`
     (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).
     Create `INDEX.md` with a header row if it does not yet exist.
3. Append `## Learnings` to the ticket — bullets of new facts, constraints,
   or surprises this ticket exposed.
4. Append `## Wiki Updates` to the ticket — list of `llm-wiki/<file>.md`
   paths created or modified, one line each with a brief changelog.
5. Set state to `Done`. If you found nothing genuinely new, say so
   explicitly under `## Learnings` ("no new wiki entries; existing
   coverage was correct") and still transition.

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
