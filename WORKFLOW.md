---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Explore, "In Progress", Review, QA, Learn]
  terminal_states: [Done, Cancelled, Blocked, Archive]
  # Auto-archive sweep: terminal-state issues whose `updated_at` is older
  # than `archive_after_days` move to `archive_state` on the next poll.
  # Set `archive_after_days: 0` to disable the sweep (manual `a` hotkey
  # in the TUI still works). 30 days is a safe default — rerunning a
  # ticket or adding a comment resets the clock.
  archive_state: Archive
  archive_after_days: 30
  state_descriptions:
    Todo: "Triage; route to Explore"
    Explore: "Brief from llm-wiki + git + code"
    "In Progress": "TDD loop, draft branch"
    Review: "Read diff, fix CRITICAL/HIGH"
    QA: "pytest -q + real-CLI smoke"
    Learn: "Distill learnings, update llm-wiki"
    Done: "As-Is -> To-Be report"
    Archive: "Auto-archived after 30 days idle"

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
`## Blocker`, `## QA Failure`, or `## Review Findings` section before acting and
address the root cause, not the symptom.{% endif %}{% if is_rewind %}This turn is a rewind from a Review or QA finding. Your only job
this turn is to address the items in the most recent `## Review Findings` or
`## QA Failure` section — read it first, fix exactly those items, and do NOT
open new scope. The agent context is fresh: anything not written into the
ticket body or `docs/{{ issue.identifier }}/` is gone.{% endif %}

{% if issue.description %}
## Description

{{ issue.description }}
{% endif %}

## Production pipeline (seven stages, no skipping)

```
  Todo  ->  Explore  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Done
                              ^   \                ^    \                ^
                              |    +-> Blocked     |     +-> Blocked     |
                              |                    |                     |
                              +-- Review CRITICAL/HIGH rewinds here      |
                              +-- QA failure rewinds here ---------------+
```

The ticket file lives at `kanban/{{ issue.identifier }}.md`. Edit the YAML
front matter `state:` field to transition; append narrative sections to the
body. Symphony reconciles on the next poll tick.

`docs/{{ issue.identifier }}/` is this ticket's evidence root. Every
artefact this ticket produces lives under `docs/{{ issue.identifier }}/<stage>/`.

## Audience & writing style (applies to every comment you append)

This kanban is read by **non-developers as well as developers** (PMs /
기획자 included). Every section you append must let a non-dev grasp
"what changed, why, and how" in ~30 seconds. Code-level detail is fine,
but it must come *after* a plain-language header.

**Plain-Korean header (mandatory, first lines of every section you write):**

```
**무엇**: <한 줄, 비-개발자도 이해 가능한 한국어>
**왜**: <한 줄, 사용자/시스템에 어떤 가치/위험이 있는지>
**As-Is → To-Be**:
- As-Is: <한 줄, 이 단계 시작 전 상태>
- To-Be: <한 줄, 이 단계 종료 후 상태>
```

After the header, write the stage-specific technical body — but obey
the **length caps** below. Push everything that would push you over the
cap into `docs/{{ issue.identifier }}/<stage>/details.md` and add one
link line at the end of the section: `_세부: docs/<id>/<stage>/details.md_`.

| Stage section          | Body cap (after header) | What goes in details.md            |
|------------------------|-------------------------|-------------------------------------|
| `## Triage`            | 1-2 lines total (no header needed) | n/a                       |
| `## Domain Brief`      | ≤ 12 lines              | path:line citations beyond top 3, vendor docs, full file walks |
| `## Plan Candidates`   | ≤ 8 lines (1-2 per option) | per-option diff sketches, deep trade-off analysis |
| `## Recommendation`    | ≤ 5 lines               | first-failing-test full text         |
| `## Implementation`    | ≤ 10 lines              | per-file change list, helper names, dataclass shapes |
| `## Review`            | ≤ 6 lines (severity table, 1 line each) | full check-list reasoning, fix diffs |
| `## Review Findings`   | severity table only (≤ 6 rows, 1 line each) | full check-list reasoning, fix diffs go to `docs/{{ issue.identifier }}/review/details.md` |
| `## QA Evidence`       | header + commands + 1-line "verdict" + AC table | raw pytest, stdout, smoke logs |
| `## Learnings`         | ≤ 8 lines (3-4 bullets, 1-2 lines each) | extended rationale, follow-ups |
| `## Wiki Updates`      | ≤ 4 lines               | n/a (wiki is the source of truth)   |
| `## As-Is -> To-Be Report` (Done) | ≤ 20 lines across all 4 sub-sections | full evidence dump |

**Style rules (every section):**

- **Lean on code references, don't reproduce them.** The reader can read
  the code directly. Keep code-level detail in the ticket body light:
  cite the top 1-3 `path:line` anchors that pin the change, but skip
  function signatures, dataclass field lists, diff hunks, and per-line
  walks. If you need more, put it in
  `docs/{{ issue.identifier }}/<stage>/details.md` instead of the ticket.
- Korean for the Plain-Korean header and human-readable summary lines;
  English is fine inside code spans (`path:line`, identifiers, command
  output). Don't translate code symbols into Korean.
- No drive-by jargon. If a term needs context for a 기획자, give it
  inline ("`Columns`(가로 정렬 컴포넌트)"). One short parenthetical is
  enough; longer explanations belong in `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, don't tell. "200 passed" beats "all tests passed".
- A reviewer who reads only the Plain-Korean headers (skipping every
  technical body) must still understand the entire ticket end-to-end.

## Stage rules

### TRIAGE  -- when state is `Todo`

1. Read the ticket end-to-end. Confirm there is enough information to start.
2. Append a one-line `## Triage` (Plain-Korean header NOT required for
   this single section) and set state to `Explore`.

### EXPLORE  -- when state is `Explore`

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/` if
   it exists (this may be the first stage, in which case it does not).
   On a re-explore (rare — usually after a Blocked rewind), the prior
   brief and any `## Triage` are your starting point.
2. Open `llm-wiki/INDEX.md` and read every entry plausibly related to the
   ticket. Follow links into entry files.
3. Skim git history for prior work in adjacent areas: `git log --oneline -- <path>`
   then `git show <sha>` on the most relevant commits.
4. Read the actual source files end-to-end (not just hunks) so the brief
   reflects current state.
5. Drop boost notes (full citations, file walks, vendor docs) into
   `docs/{{ issue.identifier }}/explore/`.
6. Append three sections to the ticket. **Each section starts with the
   Plain-Korean header** and obeys its body cap; push overflow into
   `docs/{{ issue.identifier }}/explore/details.md` and link it.
   - `## Domain Brief` — top 1-3 facts/invariants only; cite at most three
     `path:line` references inline. The rest goes to `details.md`.
   - `## Plan Candidates` — 2-3 approaches, *one or two lines each*
     (chosen / not-chosen / why). Detailed diff sketches go to `details.md`.
   - `## Recommendation` — chosen option name, one-line rationale, name of
     the first failing test. No code body.
7. Set state to `In Progress`.

### IMPLEMENT  -- when state is `In Progress`

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/explore/`
   and re-read the most recent `## Recommendation`. If the most recent
   ticket section is `## QA Failure` or `## Review Findings`, treat THOSE
   items as the only scope for this turn — fix exactly what the previous
   stage flagged, no drive-by changes. The fresh context that started
   this turn means earlier conversation is gone; the markdown is the
   contract.
2. Implement the chosen option from `## Recommendation` (or, on a rewind,
   exclusively address the flagged failure items above).
3. TDD loop: failing test, minimal implementation, refactor.
4. Pair the change with `docs/{{ issue.identifier }}/work/feature.md`
   (plain-language: "사용자가 무엇을 다르게 보게 되는가").
5. Append `## Implementation` with the Plain-Korean header, then the
   touched-files list. Cap at 10 lines after the header. Per-file change
   detail (helper names, dataclass shapes, line counts) goes to
   `docs/{{ issue.identifier }}/work/details.md`.
6. Set state to `Review`.

### REVIEW  -- when state is `Review`

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/work/`
   and re-read the most recent `## Implementation` section. If a
   `## Review Findings` from a prior pass exists, confirm those specific
   items are now resolved before opening new findings.
2. Read your own diff (`git diff origin/main...HEAD` or workspace
   equivalent). Re-read touched files end-to-end, not just the hunks.
3. Apply the checklist: clarity, naming, error handling, security,
   performance, simplicity, no dead code, no debug prints, no secrets.
4. Classify findings into a severity table: `severity | file:line | fix`.
   Cap at 6 rows in the body; spillover goes to
   `docs/{{ issue.identifier }}/review/details.md`.
5. **If any CRITICAL or HIGH finding exists:** set state back to
   `In Progress`, append `## Review Findings` with the Plain-Korean header
   + the severity table, and STOP. Do NOT fix the findings inside Review —
   that is In Progress's job, with a fresh context. Symphony will dispatch
   a new fix turn automatically.
6. If the only findings are MEDIUM/LOW (or none): append `## Review` with
   the Plain-Korean header + the same severity table — flag the deferred
   items in the same section so Learn can address them — and set state to
   `QA`.
7. If something is genuinely unfixable / out of scope: set state to
   `Blocked` and append a `## Blocker` (Plain-Korean header required).

### QA  -- when state is `QA`  (THIS STAGE MUST EXECUTE REAL CODE)

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/work/`
   and the most recent `## Review` / `## Review Findings`. Confirm what
   the change is supposed to deliver before deciding what to execute. The
   fresh context here has no memory of Implement — the artefacts are the
   brief.
2. Run `.venv/bin/pytest -q` from the workspace root. All must pass.
3. Run real-CLI smoke for the affected backend per the ticket's
   "Verification" section. Capture stdout/stderr to
   `docs/{{ issue.identifier }}/qa/`.
4. Append `## QA Evidence` with the Plain-Korean header, then:
   - the exact commands run + exit codes (3-10 line excerpts only;
     full logs stay under `docs/{{ issue.identifier }}/qa/`),
   - a single `**판정**: PASS | FAIL — 한 줄 결론` line right before the
     acceptance-criteria table,
   - an AC mapping table (one row per acceptance criterion: `AC | 결과 | 근거`).
5. On failure: set state back to `In Progress`, add `## QA Failure` (also
   with the Plain-Korean header — what regressed, why it matters).
6. On pass: set state to `Learn`.

### LEARN  -- when state is `Learn`

1. **Read shared context first.** Walk `docs/{{ issue.identifier }}/explore/`,
   `work/`, `qa/` and the prior ticket sections (`## Recommendation`,
   `## Implementation`, `## QA Evidence`) end-to-end. Learn's job is to
   compare brief vs. reality — the markdown IS the brief.
2. Compare the Explore brief against reality.
3. For each non-trivial finding, update `llm-wiki/`. Edit existing entries
   in place (append to **Decision log**) or create
   `llm-wiki/<topic-slug>.md` with the standard shape.
4. Append `## Learnings` (Plain-Korean header + 3-4 bulleted insights,
   1-2 lines each) and `## Wiki Updates` (Plain-Korean header + ≤ 4 lines
   listing wiki paths touched). Long rationale per insight goes to
   `docs/{{ issue.identifier }}/learn/details.md`.
5. Set state to `Done`.

### DONE  -- when state is `Done`

Terminal. Append `## As-Is -> To-Be Report` with the Plain-Korean header
followed by four sub-sections: **As-Is**, **To-Be**, **근거(Reasoning)**,
**증거(Evidence)**. Cap the whole report at ~20 lines. The header's
As-Is/To-Be should be the *ticket-level* before/after (not the most
recent stage). Stop.

## Hard rules (every stage)

- Never skip a stage. Never mark `Done` without `## QA Evidence`.
- Never silence failing tests or hide errors. Fix root cause or move to
  `Blocked` with `## Blocker` (Plain-Korean header required).
- Touch only what the ticket requires. No drive-by refactors.
- All artefacts under `docs/{{ issue.identifier }}/<stage>/`. Overflow
  beyond stage caps lives in `docs/{{ issue.identifier }}/<stage>/details.md`.
- Every appended section (except the one-line `## Triage`) starts with
  the Plain-Korean header. A reviewer scrolling the ticket should be able
  to read only the headers and understand the entire ticket.
- **Backward transitions are explicit, not failures.** `Review → In Progress`
  (on CRITICAL/HIGH findings) and `QA → In Progress` (on test/spec failure)
  are part of the pipeline. Each rewind starts the next In Progress turn
  with a **fresh agent context**; the only carry-over is what you wrote
  into the ticket body and `docs/{{ issue.identifier }}/`. Treat your own
  writeups as the contract — what you didn't write down is gone.
- The shared engineering rules at the top of
  `docs/PRD-telemetry-and-sessions.md` apply to every ticket in this round.
