# Symphony Workflow Improvements (v0.5.2 → v0.6.0)

This doc is the canonical brief for the 11-item workflow / harness upgrade
landing on `feat/workflow-accuracy-and-harness-upgrades`. Subagents read this
file (not chat history) for scope, contracts, and acceptance criteria.

Each item below has: goal, contract (what artefact / API exists when done),
files to touch, and an "out of scope" line to keep blast radius small.

The pipeline order is unchanged:
`Todo -> Explore -> Plan -> In Progress -> Review -> QA -> Learn -> Merge Gate -> Done`.

---

## A. Agent goal-achievement (prompts)

### A1. Plan stage emits an executable Definition of Done

**Goal**: QA never has to guess what "done" means. Plan writes signals;
QA scores against them.

**Contract**:
- `plan.md` prompt requires two new sections after `## Plan`:
  - `## Acceptance Tests` — bullet list of test signatures (path + function
    name or `pytest -k "expr"`), one per AC. Empty list is invalid → set
    state back to Explore with `## Plan Gaps`.
  - `## Done Signals` — observable signals: file paths that must exist,
    stdout substrings, exit codes, HTTP status + body shape. One bullet
    per signal. Cap at 8 lines.
- `qa.md` step 8 (`## QA Evidence`) gains a required `## AC Scorecard`
  sub-block: table `signal | source | result (pass/fail) | evidence path`.
  Every `## Done Signals` row must appear; missing rows fail QA.

**Files**:
- `docs/symphony-prompts/file/stages/plan.md`
- `docs/symphony-prompts/file/stages/qa.md`
- `docs/symphony-prompts/file/base.md` (length cap table: add the two
  new sections, cap 10 lines each)

**Out of scope**: changing Plan's `## Plan Candidates` / `## Recommendation`
sections; touching Linear-tracker prompts.

---

### A2. Rewind turns scope diff to flagged items

**Goal**: rewinds (Review→In Progress, QA→In Progress) stop touching files
outside the finding scope.

**Contract**:
- Orchestrator injects `SYMPHONY_REWIND_SCOPE` env var on rewind dispatch.
  Value = JSON of the most recent `## Review Findings` or `## QA Failure`
  rows: `[{"severity": "...", "file": "path", "line": 42, "fix": "..."}]`.
  Empty / non-rewind turns: env var unset.
- `in-progress.md` prompt step 2 references `$SYMPHONY_REWIND_SCOPE` when
  set: "implement only fixes for those files; if you need to touch a file
  not listed, append `## Scope Expansion` with a one-line rationale and
  proceed".
- WORKFLOW.md `after_run` hook compares `git diff --name-only` against the
  parsed scope file list. Files outside the scope cause the wip commit
  subject to be prefixed `[scope-expand]` (not a failure, a marker).

**Files**:
- `src/symphony/orchestrator.py` (env injection at dispatch)
- `docs/symphony-prompts/file/stages/in-progress.md`
- `WORKFLOW.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`
  (`after_run` hook adds the marker logic)

**Out of scope**: blocking commits on scope expansion; rewriting orchestrator
rewind-detection logic (already exists at `_is_rewind_transition`).

---

### A3. Explore emits a scored reuse inventory

**Goal**: Plan can no longer silently re-write code that already exists.

**Contract**:
- `explore.md` step 5 makes `reuse-inventory.md` a **required** output (not
  optional "drop material") with this table:
  `candidate | path:line | reuse_fit (0-1) | adapt_cost (low/med/high) | notes`
- `plan.md` candidate table gains a `reuse_from` column. For each candidate
  with `reuse_from = none`, the agent must add a one-line rationale under
  `## Plan Rationale` explaining why any inventory row with `reuse_fit >= 0.7`
  was rejected.

**Files**:
- `docs/symphony-prompts/file/stages/explore.md`
- `docs/symphony-prompts/file/stages/plan.md`

**Out of scope**: enforcing the rationale programmatically (prompt-level for
now); changing how Explore discovers reuse candidates.

---

## B. Best-practice enforcement

### B1. TDD enforcement marker

**Goal**: code changes without a paired test diff become a visible Review
finding instead of silent.

**Contract**:
- WORKFLOW.md `after_run` hook classifies changed files: if any file outside
  `tests/`, `docs/`, `kanban/`, `.symphony/` changed and no file under
  `tests/` (or matching `*test*.py`, `*.test.ts`, `*_test.go`) changed in
  the same diff, prefix the wip commit subject with `[no-test]`.
- `review.md` step 3 adds: "scan `git log --format=%s symphony.basesha..HEAD`
  for `[no-test]` markers; each one becomes a HIGH severity row in the
  finding table unless the In Progress turn is documentation-only."

**Files**:
- `WORKFLOW.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`
- `docs/symphony-prompts/file/stages/review.md`

**Out of scope**: refusing the commit; auto-running tests in the hook.

---

### B2. Review stage emits a dedicated Security Audit

**Goal**: the 7-item security checklist is a deliverable, not a footnote.

**Contract**:
- `review.md` requires a `## Security Audit` section before `## Review`
  with this exact 7-row table (one row per item, in this order):
  `check | verdict (pass/fail/n/a) | evidence (path:line or "n/a")`.
  Checks: `secrets`, `input-validation`, `sql-injection`, `xss`, `csrf`,
  `authz`, `rate-limit`.
- Any `fail` row auto-promotes to a CRITICAL row in `## Review Findings`
  and triggers Review → In Progress rewind.
- `n/a` is acceptable but the evidence column must explain why (e.g.,
  `n/a — docs-only change`).

**Files**:
- `docs/symphony-prompts/file/stages/review.md`
- `docs/symphony-prompts/file/base.md` (length cap table: `## Security Audit`
  exactly 7 rows; no spillover allowed)

**Out of scope**: language-specific security tooling integration; coupling to
external SAST.

---

### B3. Observability hooks in wiki entries

**Goal**: every Learn wiki entry records the observability surface of the
code it documents.

**Contract**:
- `learn.md` wiki template (both KO and EN) adds under `## Technical
  Reference`:
  ```
  **Observability hooks:**
  - log: `<event_name>` at `path:line` — what it signals
  - metric: `<metric_name>` at `path:line` — what it counts
  - trace: `<span_name>` at `path:line` — what it spans
  ```
  Use `- none` if the code has no observability surface (acceptable for
  pure utility modules); QA / Review do not enforce on `none`.
- `plan.md` candidate table adds an `observability` column (`add`,
  `change`, `none`) so Plan declares intent up front.

**Files**:
- `docs/symphony-prompts/file/stages/learn.md`
- `docs/symphony-prompts/file/stages/plan.md`

**Out of scope**: shipping a logging library; auto-instrumenting.

---

## C. Harness

### C1. Conflict pre-check at dispatch (system-level)

**Goal**: orchestrator refuses to dispatch a ticket whose `## Touched Files`
overlap an in-flight ticket. Move the contract out of the agent prompt
where it can be forgotten / miscounted.

**Contract**:
- New helper `orchestrator._touched_files_for(issue) -> set[str]` parses
  the `## Touched Files` bullet list from the ticket markdown body.
- `_dispatch_one` (or equivalent) before claiming the issue: build the
  union of `_touched_files_for(other)` for `other` in `_running ∪ _retry`,
  intersect with the candidate's set. On non-empty intersection: set the
  ticket to `Blocked`, append `## Conflict` with the other ticket's id and
  the overlapping paths, skip dispatch.
- Once enforced system-side, `in-progress.md` step 1 (the agent-side
  conflict pre-check) is removed.

**Files**:
- `src/symphony/orchestrator.py`
- `tests/test_orchestrator_dispatch.py` (new test: two tickets with
  overlapping Touched Files → only one dispatches)
- `docs/symphony-prompts/file/stages/in-progress.md` (remove step 1)

**Out of scope**: parsing the markdown body for any other section; lifting
other agent-side preconditions.

---

### C2. Backend stall-progress predicate abstraction

**Goal**: the meta-event filter that fixed OLV-002 (commit `499e787`) lives
only in `claude_code.py`. Lift it to the backend base class so future
backends can override consistently.

**Contract**:
- New method on the backend base class:
  `def is_progress_event(self, event: dict) -> bool`. Default returns
  `True` (no filter, conservative).
- `claude_code.py` overrides it with the existing `type == "assistant"`
  predicate (move the current inline check into this method).
- `pi.py`, `gemini.py`, `codex.py` keep the default for now (they were
  not affected by the bug), but the override hook exists.
- The orchestrator / dispatcher only resets `last_progress_timestamp`
  when `backend.is_progress_event(event)` is True.

**Files**:
- `src/symphony/backends/__init__.py` (or wherever the base class lives —
  inspect first; may need to introduce a thin base)
- `src/symphony/backends/claude_code.py`
- `tests/test_backends.py` (new: predicate behaviour per backend)

**Out of scope**: changing stall_timeout_ms semantics; adding new
backends.

**Implementation note (spec deviation, intentional)**: the brief said
`codex.py` keeps the default predicate, but the existing tests
`test_on_codex_event_extracts_nested_item_preview_without_stall_progress`
and `test_codex_other_message_with_input_only_token_growth_does_not_advance_progress`
prove codex's OTHER_MESSAGE bucket carries both real assistant previews
and tool / item notifications. Removing the filter regresses those tests.
`CodexAppServerBackend` therefore overrides `is_progress_event` with the
same `type == "assistant"` predicate as claude. Pi and gemini still
inherit the default.

---

### C3. Adaptive token budget per state

**Goal**: dispatch tells the agent how many tokens the stage usually costs
so prompts can self-regulate verbosity. Memory-of-past-runs only — no
hard cap change.

**Contract**:
- New EMA (alpha = 0.3, simple Python) of completion tokens per state,
  persisted in the state file (extend whatever JSON the orchestrator
  already writes; if none, add `.symphony/token_ema.json`).
- On dispatch, orchestrator injects two env vars: `SYMPHONY_TOKEN_EMA`
  (the rolling mean for this state, int) and `SYMPHONY_TOKEN_BUDGET`
  (the configured hard cap from `agent.max_total_tokens_by_state`).
- `base.md` reads them in a new top-of-file directive:
  `Soft budget for this stage: ~{{ token_ema }} tokens (hard cap
  {{ token_budget }}). Stay concise; cite path:line, defer to details.md.`
  Gracefully render nothing when the env vars are unset.

**Files**:
- `src/symphony/orchestrator.py` (EMA update on turn completion;
  env injection on dispatch)
- `src/symphony/prompt.py` (template context: token_ema, token_budget)
- `docs/symphony-prompts/file/base.md`
- `tests/test_orchestrator_dispatch.py` (EMA persists and updates)

**Out of scope**: changing hard caps; per-ticket budget overrides.

---

### C4. Extract after_create hook to a shell script

**Goal**: 200-line bash heredoc in WORKFLOW.md becomes a versioned,
testable shell script. Hook stays one line.

**Contract**:
- New file `scripts/symphony-setup-worktree.sh` — verbatim copy of the
  current `after_create` body, with a shebang and `set -euo pipefail`.
- WORKFLOW.md, WORKFLOW.example.md, WORKFLOW.file.example.md `after_create`
  becomes: `bash "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"`.
- Script is `chmod +x` and committed.
- README mentions the script under "Hooks" so users editing it know
  where to look.

**Files**:
- `scripts/symphony-setup-worktree.sh` (new, +x)
- `WORKFLOW.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`
- `README.md` (one-line pointer)

**Out of scope**: rewriting in Python; splitting into smaller scripts;
changing what the hook does.

---

### C5. Wiki-sweep cron CLI

**Goal**: per-ticket wiki integrity sweep (dup/orphan/stale/contradiction)
moves out of Learn prompt into a scheduled CLI.

**Contract**:
- New CLI: `symphony wiki-sweep [--root docs/llm-wiki] [--dry-run]`.
  Implements the same four checks Learn currently does (duplicate slugs,
  orphans missing INDEX rows, INDEX rows missing files, entries with
  `Last updated > 90 days` → append ` (stale?)` idempotently).
- Orchestrator runs the sweep automatically after every Nth `Done`
  transition (default `wiki.sweep_every_n: 10` in WORKFLOW.md, set to 0
  to disable).
- `learn.md` step 4 is reduced to: "if this ticket invalidates a wiki
  entry, update it and log the prior wrong claim; cross-entry
  contradictions noticed in passing → append `## Wiki Conflict` to the
  ticket. Bulk dup/orphan/stale sweep is handled by `symphony
  wiki-sweep`."

**Files**:
- `src/symphony/wiki_sweep.py` (new module)
- `src/symphony/cli.py` (register `wiki-sweep` subcommand)
- `src/symphony/orchestrator.py` (post-Done counter + sweep dispatch)
- `tests/test_wiki_sweep.py` (new)
- `docs/symphony-prompts/file/stages/learn.md`
- `WORKFLOW.md` (add `wiki.sweep_every_n: 10` block with a comment)

**Out of scope**: rewriting wiki integrity rules; moving INDEX format.

---

## Cross-cutting

### Release

- `pyproject.toml` and `src/symphony/__init__.py` bump to `0.6.0` in
  lockstep (memory `project_symphony_version_source_of_truth`).
  Rationale: 6 prompt-contract changes + 5 harness-API changes → minor,
  not patch. Bumps go in their own `chore(release): v0.6.0` commit on
  top of the feature commits.
- `CHANGELOG.md` gets a `## v0.6.0` block summarizing each of the 11
  items in plain language (PM-readable per memory
  `project_kanban_audience_includes_pms`).

### Testing

- All existing tests must still pass. Run `pytest -q`.
- New tests required: C1 (dispatch conflict), C2 (predicate), C3 (EMA
  persist), C5 (wiki-sweep).
- Prompt-only changes (A1, A2, A3, B1-prompt, B2, B3) update
  `tests/test_workflow_pipeline_prompt.py` if it asserts specific
  section names.

### Docs language

- Prompts ship bilingual blocks where `{% if language == 'ko' %}` is
  already used (base.md, learn.md). New prompt sections must follow the
  same i18n pattern (memory
  `project_language_split_chrome_vs_docs`).

### Publicity guardrails

This repo is going public (memory `project_repo_going_public`). Every
example in WORKFLOW.example.md must work from a fresh clone; the
extracted shell script (C4) must be POSIX-portable and handle the
Windows / Git Bash branch already present in the current hook.

---

## Owners (subagent dispatch map)

| Group | Items                                  | Files (broad)                                            |
|-------|----------------------------------------|----------------------------------------------------------|
| G1    | A1, A2-prompt, A3, B1-prompt, B2, B3   | `docs/symphony-prompts/file/**`                          |
| G2    | C1, C3, A2-orch, B1-hook               | `src/symphony/orchestrator.py`, `src/symphony/prompt.py`, WORKFLOW*.md `after_run` |
| G3    | C2                                     | `src/symphony/backends/**`, `tests/test_backends.py`     |
| G4    | C4, C5                                 | `scripts/`, `src/symphony/wiki_sweep.py`, `src/symphony/cli.py`, WORKFLOW*.md `after_create` |
| Final | release + integration + review + PR    | `pyproject.toml`, `src/symphony/__init__.py`, `CHANGELOG.md` |

G2 + G4 both edit WORKFLOW*.md: G2 owns `after_run` blocks; G4 owns
`after_create` block + new `wiki:` config block. No overlapping line
ranges.

---

## Post-review follow-ups (filed but not in v0.6.0)

Code review on 2026-05-17 flagged the following MEDIUM items. They are
not blockers for v0.6.0; future tickets:

- **C1 retry overlap is best-effort.** Retry-queue entries don't carry
  the full `Issue` body, so the conflict pre-check only inspects
  entries that are also in `_running`. Pulling the body from the
  tracker at dispatch time would make this complete; deferred to keep
  the dispatch hot path single-source.
- **C3 dispatch env mutates process-global `os.environ`.** Safe under
  `max_concurrent_agents=1` (current default). Future `>1` concurrency
  needs per-subprocess `env=` instead of in-place mutation.
- **C3 EMA `.json.tmp` rename on Windows.** POSIX rename is atomic; on
  Windows the rename can fail if a concurrent reader holds the file.
  Best-effort log only; no data loss.

Two HIGH items from the same review were fixed in the v0.6.0 bundle:
- C1 backticked-path-with-spaces regex (split into
  `_BULLET_PATH_BACKTICK_RE` + `_BULLET_PATH_PLAIN_RE`).
- C5 `_done_count` persistence (mirrors the EMA load/persist pattern;
  survives orchestrator restarts so sweep cadence holds).

One MEDIUM item also fixed in the bundle:
- B1 docs-only carve-out expanded to include `*.md`, `LICENSE*`,
  `NOTICE`, `CHANGELOG*`, `README*`, `AGENTS.md`, `GEMINI.md` so root
  documentation edits don't trip the `[no-test]` marker.
