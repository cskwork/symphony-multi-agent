You are picking up issue {{ issue.identifier }}: {{ issue.title }}.
Current state: {{ issue.state }}.
{% if attempt %}Retry attempt {{ attempt }}. Read the previous Linear
comment thread first — the most recent Resolution, Blocker, QA Failure,
or Review Findings comment — and fix the root cause from the prior
failure, not the symptom.{% endif %}{% if is_rewind %}This turn is a rewind from a Review or QA finding. Address only
the items in the most recent Review Findings or QA Failure comment —
read it first, fix exactly those items, do NOT open new scope. Agent
context is fresh: anything not written into the Linear comment thread
or `docs/{{ issue.identifier }}/` is gone.{% endif %}

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

## Production pipeline (eight stages, no skipping)

Every issue flows through the same gates. Honour the gate that matches
`{{ issue.state }}`. Each stage owns one transition; never jump ahead.

```
  Todo  ->  Explore  ->  Plan  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Merge Gate  ->  Done
                              ^   \                ^    \                ^
                              |    +-> Blocked     |     +-> Blocked     |
                              |                    |                     |
                              +-- Review CRITICAL/HIGH/MEDIUM rewinds here|
                              +-- QA failure rewinds here ---------------+
```

`docs/llm-wiki/` is the project's domain knowledge base — one Markdown
entry per topic plus an `INDEX.md`. Explore reads it before new work;
Learn writes back after QA passes. Treat it as living memory future
tickets depend on. The first Learn stage creates the directory if missing.
Plan turns Explore's candidates into a single executable `## Plan`; In
Progress must read that plan before editing code.
After Learn writes back, merge the ticket's `symphony/{{ issue.identifier }}`
feature branch into the target branch before marking the issue `Done`.

`docs/{{ issue.identifier }}/` is this ticket's evidence root — see Hard rules below for the artefact policy. Learn writes to `${LLM_WIKI_PATH:-./docs/llm-wiki}/<topic>.md`, a sibling under the same `docs/` root.

State transitions and stage notes are written via the `linear_graphql` tool:
`issueUpdate` for state changes, `commentCreate` for the per-stage notes
described below. Each stage produces one comment.

## Audience & writing style (applies to every comment you post)
{% if language == 'ko' %}
Comments are read by **non-developers as well as developers** (PMs /
기획자 included). Every stage comment must let a non-dev grasp "what
changed, why, and how" in ~30 seconds. Code-level detail is fine, but
must come *after* a plain-language header.

**Plain-Korean header (mandatory, first lines of every stage comment
except the one-line Triage):**

```
**무엇**: <한 줄, 비-개발자도 이해 가능한 한국어>
**왜**: <한 줄, 사용자/시스템에 어떤 가치/위험이 있는지>
**As-Is → To-Be**:
- As-Is: <한 줄, 이 단계 시작 전 상태>
- To-Be: <한 줄, 이 단계 종료 후 상태>
```

After the header, write the stage-specific body — but obey the **length
caps**. Push anything over the cap into
`docs/{{ issue.identifier }}/<stage>/details.md` and add one link line
at the end: `_세부: docs/<id>/<stage>/details.md_`.

| Comment / Stage section | Body cap (after header)                | What goes in details.md            |
|-------------------------|----------------------------------------|-------------------------------------|
| Triage comment          | 1-2 lines total (no header needed)     | n/a                                 |
| `## Domain Brief`       | ≤ 12 lines                             | extra path:line citations, vendor docs, full file walks |
| `## Plan Candidates`    | ≤ 8 lines (1-2 per option)             | per-option diff sketches, deep trade-offs |
| `## Recommendation`     | ≤ 5 lines                              | first-failing-test full text         |
| `## Plan`               | ≤ 10 lines                             | full step list, risk notes, fallback commands |
| Implementation comment  | ≤ 10 lines (PR link + touched files)   | per-file change list, helper names, dataclass shapes |
| Review comment          | ≤ 6 rows in severity table (1 line each) | full check-list reasoning, fix diffs |
| Review Findings comment | severity table only (≤ 6 rows, 1 line each) | full check-list reasoning, fix diffs go to `docs/{{ issue.identifier }}/review/details.md` |
| QA Evidence comment     | header + commands + 1-line `**판정**` + AC table | raw pytest/curl/Playwright output |
| `## Learnings`          | ≤ 8 lines (3-4 bullets)                | extended rationale, follow-ups      |
| `## Wiki Updates`       | ≤ 4 lines                              | n/a (wiki is the source of truth)   |
| As-Is → To-Be Report    | ≤ 20 lines across all 4 sub-sections   | full evidence dump under docs/      |

**Style rules:**

- **Lean on code references, don't reproduce them.** The reader can read
  the code directly. Cite the top 1-3 `path:line` anchors that pin the
  change; skip function signatures, dataclass field lists, diff hunks,
  and per-line walks. Push extra citations or raw command output into
  `docs/{{ issue.identifier }}/<stage>/details.md` (or the per-stage
  artefact folders) instead of the comment.
- Korean for the Plain-Korean header and human-readable summary lines;
  English is fine inside code spans (`path:line`, identifiers, command
  output). Don't translate code symbols into Korean.
- No drive-by jargon. If a term needs context for a 기획자, give one
  short parenthetical inline ("`Columns`(가로 정렬 컴포넌트)"). Longer
  explanations belong in `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, don't tell. "200 passed" beats "all tests passed".
- A reviewer reading only the Plain-Korean headers (skipping every
  technical body) must understand the entire ticket end-to-end.
{% else %}
Comments are read by **non-developers as well as developers** (PMs and
product managers included). Every stage comment must let a non-dev grasp
"what changed, why, and how" in ~30 seconds. Code-level detail is fine,
but must come *after* a plain-language header.

**Plain-language header (mandatory, first lines of every stage comment
except the one-line Triage):**

```
**What**: <one line, understandable by a non-developer>
**Why**: <one line, what value or risk this carries for the user/system>
**As-Is → To-Be**:
- As-Is: <one line, state before this stage>
- To-Be: <one line, state after this stage>
```

After the header, write the stage-specific body — but obey the **length
caps**. Push anything over the cap into
`docs/{{ issue.identifier }}/<stage>/details.md` and add one link line
at the end: `_details: docs/<id>/<stage>/details.md_`.

| Comment / Stage section | Body cap (after header)                | What goes in details.md            |
|-------------------------|----------------------------------------|-------------------------------------|
| Triage comment          | 1-2 lines total (no header needed)     | n/a                                 |
| `## Domain Brief`       | ≤ 12 lines                             | extra path:line citations, vendor docs, full file walks |
| `## Plan Candidates`    | ≤ 8 lines (1-2 per option)             | per-option diff sketches, deep trade-offs |
| `## Recommendation`     | ≤ 5 lines                              | first-failing-test full text         |
| `## Plan`               | ≤ 10 lines                             | full step list, risk notes, fallback commands |
| Implementation comment  | ≤ 10 lines (PR link + touched files)   | per-file change list, helper names, dataclass shapes |
| Review comment          | ≤ 6 rows in severity table (1 line each) | full check-list reasoning, fix diffs |
| Review Findings comment | severity table only (≤ 6 rows, 1 line each) | full check-list reasoning, fix diffs go to `docs/{{ issue.identifier }}/review/details.md` |
| QA Evidence comment     | header + commands + 1-line `**Verdict**` + AC table | raw pytest/curl/Playwright output |
| `## Learnings`          | ≤ 8 lines (3-4 bullets)                | extended rationale, follow-ups      |
| `## Wiki Updates`       | ≤ 4 lines                              | n/a (wiki is the source of truth)   |
| As-Is → To-Be Report    | ≤ 20 lines across all 4 sub-sections   | full evidence dump under docs/      |

**Style rules:**

- **Lean on code references, don't reproduce them.** The reader can read
  the code directly. Cite the top 1-3 `path:line` anchors that pin the
  change; skip function signatures, dataclass field lists, diff hunks,
  and per-line walks. Push extra citations or raw command output into
  `docs/{{ issue.identifier }}/<stage>/details.md` (or the per-stage
  artefact folders) instead of the comment.
- Write the Plain-language header and human-readable summary lines in
  English. English is always fine inside code spans (`path:line`,
  identifiers, command output) — don't translate code symbols.
- No drive-by jargon. If a term needs context for a non-developer, give
  one short parenthetical inline. Longer explanations belong in `details.md`.
- One thing per bullet. No nested bullets. No multi-paragraph items.
- Show, don't tell. "200 passed" beats "all tests passed".
- A reviewer reading only the Plain-language headers (skipping every
  technical body) must understand the entire ticket end-to-end.
{% endif %}

## Hard rules

- Never skip a stage. Never mark `Done` without a QA Evidence comment and a
  successful Learn Merge Gate into the target branch.
- Never silence failing tests or hide errors. Fix the root cause or move
  to `Blocked`.
- Touch only what the issue requires. No drive-by refactors.
- Every artefact this ticket produces lives under
  `docs/{{ issue.identifier }}/<stage>/` — never scatter outputs across
  `qa-artifacts/`, `runs/`, ad-hoc `tests/e2e/<name>/`, or sibling `docs/`
  files. Create the folder yourself (`mkdir -p`). The `docs/llm-wiki/`
  write-back in Learn is a sibling under `docs/`, not under this ticket's root.
- **Backward transitions are explicit, not failures.** `Review → In Progress`
  (on CRITICAL/HIGH/MEDIUM findings) and `QA → In Progress` (on test/spec
  failure, including any server-reported HIGH issue) are part of the pipeline.
  Each rewind starts the next In Progress turn with a **fresh agent context**;
  the only carry-over is what you wrote into the Linear comment thread and
  `docs/{{ issue.identifier }}/`. Treat your own writeups as the contract —
  what you didn't write down is gone.
- **Rewind cap.** Symphony counts every `Review → In Progress` and
  `QA → In Progress` transition at runtime. If the rewind count would exceed
  `agent.max_attempts` in `WORKFLOW.md` (currently `{{ agent.max_attempts }}`),
  Symphony moves the issue to `Blocked` instead. `max_attempts: 0` disables
  the cap.
