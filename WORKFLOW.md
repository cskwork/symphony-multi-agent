---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Done, Cancelled, Blocked]
  state_descriptions:
    Todo: "Triage; route to Explore"
    Explore: "Brief from llm-wiki + git + code"
    "In Progress": "TDD loop, draft branch"
    Review: "Read diff, fix CRITICAL/HIGH"
    QA: "pytest -q + real-CLI smoke"
    Learn: "Distill learnings, update llm-wiki"
    Done: "As-Is -> To-Be report"

polling:
  interval_ms: 30000

workspace:
  root: ~/symphony_workspaces

hooks:
  # The workspace starts empty. Seed it with a fresh clone of the host
  # repo so each ticket has an isolated working copy on its own branch.
  #
  # IMPORTANT: kanban/, docs/, and llm-wiki/ are symlinked back to the
  # host repo so that agent edits (state transitions, evidence, wiki
  # updates) are visible to Symphony's FileBoardTracker, which reads
  # board_root relative to WORKFLOW.md (the host), not the workspace.
  after_create: |
    set -euo pipefail
    ISSUE_ID="$(basename "$PWD")"
    HOST_REPO="${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
    git clone --no-hardlinks "$HOST_REPO" .
    git checkout -b "symphony/${ISSUE_ID}"
    # Symlink shared directories back to host so agent edits are visible
    # to Symphony's file tracker (which reads from the host's board_root).
    for dir in kanban docs llm-wiki; do
      rm -rf "$dir"
      ln -s "$HOST_REPO/$dir" "$dir"
    done
    python3.11 -m venv .venv
    .venv/bin/pip install --quiet -e '.[dev]'
  before_run: |
    set -euo pipefail
    git fetch origin main --quiet || true
  after_run: |
    echo "run finished at $(date -u +%FT%TZ)"

agent:
  kind: claude
  max_concurrent_agents: 3
  max_turns: 20
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    Todo: 3
    Explore: 3
    "In Progress": 3
    Review: 3
    QA: 3
    Learn: 3

claude:
  command: claude -p --output-format stream-json --verbose --permission-mode acceptEdits
  resume_across_turns: true
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000

codex:
  command: codex app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy: workspace-write

gemini:
  command: 'gemini -p ""'

pi:
  command: 'pi --mode json -p ""'
  resume_across_turns: true

server:
  port: 9999

tui:
  language: en
  max_cards_per_column: 6
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

## Production pipeline (seven stages, no skipping)

```
  Todo  ->  Explore  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Done
                              \                       \                    ^
                               +-> Blocked             +-> Blocked          |
                                                                            |
                              (QA failure rewinds to In Progress)
```

The ticket file lives at `kanban/{{ issue.identifier }}.md`. Edit the YAML
front matter `state:` field to transition; append narrative sections to the
body. Symphony reconciles on the next poll tick.

`docs/{{ issue.identifier }}/` is this ticket's evidence root. Every
artefact this ticket produces lives under `docs/{{ issue.identifier }}/<stage>/`.

## Stage rules

### TRIAGE  -- when state is `Todo`

1. Read the ticket end-to-end. Confirm there is enough information to start.
2. Append a one-line `## Triage` and set state to `Explore`.

### EXPLORE  -- when state is `Explore`

1. Open `llm-wiki/INDEX.md` and read every entry plausibly related to the
   ticket. Follow links into entry files.
2. Skim git history for prior work in adjacent areas: `git log --oneline -- <path>`
   then `git show <sha>` on the most relevant commits.
3. Read the actual source files end-to-end (not just hunks) so the brief
   reflects current state.
4. Drop boost notes into `docs/{{ issue.identifier }}/explore/`.
5. Append three sections to the ticket:
   - `## Domain Brief` — facts, invariants, references (`path:line`).
   - `## Plan Candidates` — 2-3 approaches with trade-offs.
   - `## Recommendation` — chosen option, rationale, first failing test.
6. Set state to `In Progress`.

### IMPLEMENT  -- when state is `In Progress`

1. Read the `## Recommendation` section first. Implement that option.
2. TDD loop: failing test, minimal implementation, refactor.
3. Pair the change with `docs/{{ issue.identifier }}/work/feature.md`.
4. Append `## Implementation` listing touched files.
5. Set state to `Review`.

### REVIEW  -- when state is `Review`

1. Read your own diff (`git diff`). Re-read touched files end-to-end.
2. Apply checklist: clarity, naming, error handling, security, simplicity.
3. Fix every CRITICAL / HIGH issue. Record under `## Review`
   (`severity | file:line | fix`).
4. Set state to `QA`.

### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

1. Run `.venv/bin/pytest -q` from the workspace root. All must pass.
2. Run real-CLI smoke for the affected backend per the ticket's
   "Verification" section. Capture stdout/stderr to
   `docs/{{ issue.identifier }}/qa/`.
3. Append `## QA Evidence` with: commands, exit codes, 3-10 line output
   excerpts, paths to larger artefacts.
4. On failure: set state back to `In Progress`, add `## QA Failure`.
5. On pass: set state to `Learn`.

### LEARN  -- when state is `Learn`

1. Compare the Explore brief against reality.
2. For each non-trivial finding, update `llm-wiki/`. Edit existing entries
   in place (append to **Decision log**) or create
   `llm-wiki/<topic-slug>.md` with the standard shape.
3. Append `## Learnings` and `## Wiki Updates` to the ticket.
4. Set state to `Done`.

### DONE  -- when state is `Done`

Terminal. Append `## As-Is -> To-Be Report` with sections: As-Is, To-Be,
Reasoning, Evidence. Stop.

## Hard rules (every stage)

- Never skip a stage. Never mark `Done` without `## QA Evidence`.
- Never silence failing tests or hide errors. Fix root cause or move to
  `Blocked` with `## Blocker`.
- Touch only what the ticket requires. No drive-by refactors.
- All artefacts under `docs/{{ issue.identifier }}/<stage>/`.
- The shared engineering rules at the top of
  `docs/PRD-telemetry-and-sessions.md` apply to every ticket in this round.
