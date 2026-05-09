---
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Closed, Cancelled, Canceled, Duplicate, Done]
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
  max_retry_backoff_ms: 300000
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

## Production pipeline (seven stages, no skipping)

Every issue flows through the same gates. Honour the gate that matches
`{{ issue.state }}`. Each stage owns one transition; never jump ahead.

```
  Todo  ->  Explore  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Done
                              \                       \                    ^
                               +-> Blocked             +-> Blocked          |
                                                                            |
                              (QA failure rewinds to In Progress)
```

`llm-wiki/` is the project's domain knowledge base — one Markdown entry per
topic, plus an `INDEX.md` that lists them. Explore reads it before any new
work; Learn writes back to it after QA passes. Treat it as a living memory
that future tickets depend on. If the directory does not yet exist, the
first Learn stage that runs creates it.

`docs/{{ issue.identifier }}/` is this ticket's evidence root — see Hard rules at the bottom for the artefact policy. Learn writes to `${LLM_WIKI_PATH:-./llm-wiki}/<topic>.md`, the only artefact outside that root.

State transitions and stage notes are written via the `linear_graphql` tool:
`issueUpdate` for state changes, `commentCreate` for the per-stage notes
described below. Each stage produces one comment.

## Stage rules

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

### EXPLORE  -- when state is `Explore`

You are a domain-knowing researcher walking three lenses in one turn:
**domain expert** (what does this code mean?), **implementer** (smallest
sustainable change?), **risk reviewer** (what could go wrong?).

1. Open `llm-wiki/INDEX.md`. Path defaults to ./llm-wiki/ but respects
   $LLM_WIKI_PATH if set. Read every entry whose topic plausibly relates
   to the ticket. Follow links into the entry files. If `llm-wiki/` does
   not exist yet, note that and continue — Learn will seed it later.
2. Skim git history for prior work in adjacent areas: for each file the
   ticket likely touches, run `git log --oneline -- <path>` and read the
   one or two most relevant commits in full (`git show <sha>`). Capture
   why prior changes were made, not just what they did.
3. Read the actual source files end-to-end (not just hunks) so the brief
   reflects current state, not stale memory.
4. Drop boost material — citations, vendor-doc snippets, candidate helpers,
   reuse inventory — into `docs/{{ issue.identifier }}/explore/` (e.g.
   `notes.md`, `vendor-docs.md`, `reuse-inventory.md`). The brief sections
   below cite these files.
5. Apply each lens explicitly and produce one consolidated Explore
   comment with three sections:
   - `## Domain Brief` — key facts, invariants, and references
     (`path:line`, wiki entry titles, commit SHAs) the implementer must
     know before writing code.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs
     (complexity, blast radius, reversibility). Be specific about files
     touched and tests added per option.
   - `## Recommendation` — the option you choose, the rationale (why
     this lens won), the risks accepted, and the first failing test
     the implementer should write.
6. Transition state to `In Progress`.

### IMPLEMENT  -- when state is `In Progress`

1. Read the Explore Recommendation comment first. Implement the chosen
   option; do not reopen the plan unless you find a fact the brief got
   wrong (in which case post a one-line note and proceed).
2. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
3. Pair the change with user-facing documentation under
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this ticket
   carries the `bug` label) — what changed, how a user observes it, any
   knobs/flags. Plain language, no jargon.
4. Open a draft PR. Post an Implementation comment with the PR link, the
   touched files, and the commit-style intent of each change.
5. Transition state to `Review`.

### REVIEW  -- when state is `Review`

1. Read the diff on the PR (`git diff origin/main...HEAD`). Re-read the
   touched files end-to-end, not just the hunks.
2. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
3. Verify with live HTTP proof when the change touches an API. Hit both
   baseline (As-Is) and the new build (To-Be) with curl/httpie/`requests`
   and save under `docs/{{ issue.identifier }}/verify/`:
   `baseline.json`, `pr.json`, `diff.txt`, `curl.log`. Code-only review
   for an API change is not enough.
4. Fix every CRITICAL and HIGH issue. Post a Review comment with one
   bullet per finding (`severity | file:line | fix`), referencing any
   verify artefacts under docs/{{ issue.identifier }}/verify/.
5. If unfixable / out of scope: transition state to `Blocked`, post a
   Blocker comment with what is needed and stop.
6. Otherwise transition state to `QA`.

### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

A QA pass that only inspects code is a failed QA. Run something and
capture its output as evidence.

1. Detect the project type and execute the matching real-world check:
   - **Tests**: run the full suite (`pytest -q`, `npm test`, etc.).
   - **HTTP API**: capture the As-Is response by hitting the baseline
     build and the To-Be response by hitting the new build (curl /
     httpie / `requests`). Diff the two. Save artefacts under
     `docs/{{ issue.identifier }}/qa/`.
   - **Web UI**: author a durable Playwright / Cypress spec at
     `docs/{{ issue.identifier }}/qa/e2e.spec.ts` that walks the flow
     end-to-end. Run it and save traces, videos, and HAR under
     `docs/{{ issue.identifier }}/qa/` (e.g. `traces/`, `videos/`, `har/`).
   - **CLI / script**: run the command and assert exit code plus
     observable stdout/stderr / file output. Save the run log to
     `docs/{{ issue.identifier }}/qa/cli.log`.
2. Post a QA Evidence comment listing:
   - the exact commands run,
   - their exit codes,
   - a short excerpt of relevant output (3-10 lines),
   - links to artefacts under `docs/{{ issue.identifier }}/qa/`.
3. If anything fails: transition state back to `In Progress`, post a QA
   Failure comment describing what regressed, and stop. Do NOT silence,
   retry, or skip the failing check.
4. If everything passes: transition state to `Learn`.

### LEARN  -- when state is `Learn`

The point of Learn is to make the next ticket cheaper. Distill what this
ticket actually taught the team and write it back into `llm-wiki/` so
future Explore stages can find it.

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
3. Commit the wiki edits onto the ticket's PR (same branch — wiki updates
   are part of the change). Do not push wiki edits in a separate PR.
4. Post a Learn comment with two sections:
   - `## Learnings` — bullets of new facts, constraints, or surprises
     this ticket exposed.
   - `## Wiki Updates` — list of `llm-wiki/<file>.md` paths created or
     modified, one line each with a brief changelog.
5. Transition state to `Done`. If you found nothing genuinely new, say
   so explicitly in the Learn comment ("no new wiki entries; existing
   coverage was correct") and still transition.

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

## Hard rules

- Never skip a stage. Never mark `Done` without a QA Evidence comment.
- Never silence failing tests or hide errors. Fix the root cause or move
  to `Blocked`.
- Touch only what the issue requires. No drive-by refactors.
- Every artefact this ticket produces lives under
  `docs/{{ issue.identifier }}/<stage>/` — never scatter outputs across
  `qa-artifacts/`, `runs/`, ad-hoc `tests/e2e/<name>/`, or sibling `docs/`
  files. Create the folder yourself (`mkdir -p`). The llm-wiki write-back
  in Learn is the only artefact that lives outside this root.
