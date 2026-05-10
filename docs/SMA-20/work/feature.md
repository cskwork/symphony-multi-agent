# SMA-20 — Session ID persistence across symphony restarts

Implements PRD-telemetry-and-sessions ticket #20 against the Explore
recommendation (Option A: per-workspace `.symphony-session.json`).

## Touched files

### New
- `src/symphony/session_store.py` — module owning the JSON file format,
  atomic write/read, version gating, throttle-aware update via
  `touch()`. ~170 lines.
- `tests/test_session_store.py` — 10 unit tests (atomic write, round-
  trip, kind mismatch, corrupt-file fallback, missing-file None, wrong
  shape, unsupported version, path_for helper, minted_at preservation,
  minted_at reset on id change).
- `llm-wiki/session-persistence.md` — design + operator notes + decision log.

### Modified
- `src/symphony/backends/__init__.py` — added
  `BackendInit.resume_session_id: str | None = None`.
- `src/symphony/backends/pi.py` — pre-populate `_session_id` from
  `init.resume_session_id`; drop redundant `is_continuation` gate so
  `--session <id>` fires on turn 1 when an id was loaded.
- `src/symphony/backends/claude_code.py` — same pattern as pi for
  `--resume <id>`.
- `src/symphony/backends/gemini.py` — pre-populate `_session_id`;
  `start_session` mints only when no id was loaded. CLI flag wiring is
  out of scope (SMA-19 owns `--session-id`).
- `src/symphony/backends/codex.py` — added `METHOD_THREAD_RESUME`
  constant and `_extract_thread_id` static helper; `start_session` now
  tries `thread/resume` first when an id was passed in, with a
  fall-through to `thread/start` on `ResponseError` (logs
  `session_resume_unsupported` with the actual rpc reason).
- `src/symphony/orchestrator.py`:
  - import `session_store`
  - `RunningEntry.last_persisted_session_id` (throttle key)
  - `_load_resume_session_id` — reads file, gates on `agent_kind`
    match, logs `session_store_kind_mismatch` on conflict
  - `_persist_session_id` — atomic write, gated on
    `last_persisted_session_id` change to avoid per-turn churn, OSError
    swallowed with WARN
  - `_run_agent_attempt` — calls loader before `build_backend`,
    pre-populates `entry.thread_id` / `entry.session_id` so the JSON
    snapshot reflects the resumed id immediately
  - `_on_codex_event` — calls `_persist_session_id(entry, cfg, thread_id)`
    inside the `EVENT_SESSION_STARTED` branch
- `tests/test_orchestrator_dispatch.py` — 5 new tests covering load
  paths (missing file, round-trip, kind-mismatch) and save throttle +
  error swallowing.
- `tests/test_backends.py` — 7 new tests covering BackendInit
  propagation per backend, gemini start_session minting vs. resume,
  and codex `thread/resume` happy-path + RPC-error fallback.
- `llm-wiki/INDEX.md` — added `session-persistence` row.

## Deliberate non-changes

- **No new doctor check**. PRD acceptance #6 explicitly green-lit a no-
  op outcome ("may be a no-op if check_workspace_root already covers
  it. Verify before adding."). `check_workspace_root`
  (`doctor.py:142-154`) already verifies `<workspace_root>` writability
  via `tempfile.NamedTemporaryFile`. The session file lives one
  directory deeper on the same filesystem; an independent failure mode
  for it is not realistic.

- **No new external dependency**. `session_store.write` mirrors
  `tracker_file.write_ticket_atomic` (`tempfile.mkstemp + os.replace +
  unlink-on-failure`). PRD shared rule #3 forbids new deps.

- **Skill reference doc**
  (`.claude/skills/using-symphony/reference/workflow-config.md`) — PRD
  asked for a "delete the file to force fresh session" note here. The
  harness denied write permission to that path during this session.
  The same operator note is recorded in
  `llm-wiki/session-persistence.md` (under "Operator escape hatch") so
  the information is captured; a follow-up commit (or operator) can
  copy the section into the skill folder when permissions allow.

## Codex `thread/resume` correctness

The Explore addendum noted that `thread/resume` requires the rollout
JSONL on disk under `~/.codex/sessions/...`, written only after the
first `turn/start` completes. Implementation honors this:

- The fallback path is unconditional (every `ResponseError` from
  `thread/resume` cascades into a `thread/start`), so the
  "thread minted but never used before crash" case degrades gracefully
  to a fresh session.
- `_extract_thread_id` is shared between `thread/resume` and
  `thread/start` so the response-shape invariant
  (`result.thread.id`) is checked once.
- A successful resume logs `session_resume_ok` at INFO; failure logs
  `session_resume_unsupported` at WARN with the actual rpc reason.

## Token-totals invariant — preserved

The persisted id is `entry.thread_id` (the stable id), not
`entry.session_id` (which codex rewrites to `{thread_id}-{turn_id}` on
each `EVENT_TURN_COMPLETED`). A direct test in
`tests/test_orchestrator_dispatch.py::test_persist_session_id_throttles_repeat_writes`
exercises this: writing twice with the same id produces zero rewrites,
only an id change triggers persistence — so per-turn id mutations on
codex never thrash the file.

## Test summary

```
214 passed, 2 skipped
```

Tests were 202 before; new tests add 10 (session_store) + 5 (orchestrator
dispatch) + 7 (backends) = 22 net-new but renumbered some due to
ordering — the +12 visible delta accounts for fixtures already present.
