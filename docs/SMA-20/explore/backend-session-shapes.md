# Backend session/resume surfaces — current state (pre-SMA-20)

Captured by reading `src/symphony/backends/{pi,claude_code,codex,gemini}.py`
end-to-end on 2026-05-10.

## How each backend mints / reuses session ids today

| backend | mints at                          | stored as                     | continuation flag | EVENT_SESSION_STARTED fires at |
|---------|-----------------------------------|-------------------------------|-------------------|--------------------------------|
| codex   | `thread/start` JSON-RPC response  | `_thread_id` (UUIDv7 string)  | n/a (single proc) | inside `start_session`, before turn 1 |
| claude  | `system.init` event in turn 1     | `_session_id`                 | `--resume <id>`   | inside `_consume_stream` mid-turn-1 |
| pi      | `session` JSONL header in turn 1  | `_session_id`                 | `--session <id>`  | inside `_consume_stream` mid-turn-1 |
| gemini  | locally synthesized UUID          | `_session_id` (`gemini-<hex>`)| n/a (one-shot)    | inside `start_session` (synthetic) |

Pi/claude already pass `--session`/`--resume` on **continuation turns
within the same dispatch** (gated by `is_continuation` AND
`resume_across_turns` config). The acceptance criteria flip the gate:
when `BackendInit.resume_session_id` is non-null, pre-populate
`_session_id` so the same flag-injection logic fires on turn 1 too.

## Key call sites

### orchestrator.py — _on_codex_event (lines 587-700)

- Reads `event["payload"]["session_id"|"thread_id"|"threadId"]` on
  `EVENT_SESSION_STARTED` and assigns `entry.thread_id` /
  `entry.session_id` (orchestrator.py:625-639).
- `_apply_token_totals` (orchestrator.py:702-718) accumulates from
  `event["usage"]` deltas. Symphony **does not** persist these across
  restarts today — token counts reset to zero on restart. Per PRD
  acceptance #3 we pre-populate the new RunningEntry with the stored
  session id only, not historical token totals (out of scope per PRD's
  "Out of scope" + tests covering pure session id round-trip).

### orchestrator.py — _dispatch (lines 407-436)

- Constructs `RunningEntry` synchronously, kicks off
  `_run_agent_attempt` worker.
- `BackendInit` is built inside `_run_agent_attempt`
  (orchestrator.py:462-470) **after** `_workspace_manager.create_or_reuse`
  resolves the workspace path. So the natural read point for the session
  store is **inside `_run_agent_attempt`** before
  `BackendInit(...)` is constructed.
- That said, the kanban ticket says "wire load/save into `_dispatch`".
  The intent is clearly "in the dispatch path", not "literally inside
  the synchronous `_dispatch` method". Code shape: read inside
  `_run_agent_attempt` after `workspace.path` is known.

### Cleanup behavior

- `worker_exit reason=normal` → keeps the workspace (and our session
  file). `WorkspaceManager.remove(...)` is only called from
  `_reconcile_running` for terminal-state issues
  (orchestrator.py:917-918) and from `_startup_terminal_cleanup`
  (orchestrator.py:990) — both already use `shutil.rmtree`, so a
  `.symphony-session.json` file inside the workspace is naturally
  swept away with the directory. **No new cleanup logic needed.**

## BackendInit invariants

- `BackendInit` is a `@dataclass` with `client_tools` defaulted to an
  empty list. Adding `resume_session_id: str | None = None` at the end
  is non-breaking (every existing call site uses keyword args).

## Public-API stability check (PRD shared rule #1)

- `AgentBackend` Protocol is unchanged — `resume_session_id` is on
  `BackendInit`, not on the Protocol's method signatures.
- `/api/v1/state` JSON shape is unchanged — `RunningEntry.session_id`
  was already in the snapshot (`_running_row` orchestrator.py:267-283).
  Pre-populating it from disk just means the field is non-null sooner.
- `latest_usage["total_tokens"]` invariant is untouched (we don't
  persist token totals).

## Tests already exercising adjacent code

- `tests/test_backends.py:test_gemini_session_id_synthesized` —
  template for a session-id round-trip test.
- `tests/test_orchestrator_dispatch.py` — eligibility tests; the
  resume-path test will probably mock the `BackendInit` construction
  rather than spawn a real backend.
- `tests/test_workspace.py` — workspace lifecycle including remove();
  worth a glance for the .symphony-session.json cleanup assertion.

## Edge cases collected from probing + reading

1. **First-write-before-first-turn for codex**: orchestrator emits
   EVENT_SESSION_STARTED right after `thread/start` returns. The
   rollout file does NOT yet exist on codex's side. If symphony crashes
   *here*, the persisted session id will fail `thread/resume` next time.
   → graceful fallback (catch + log + fresh start).
2. **Corrupt JSON file**: PRD acceptance #7 requires this test. Use
   `json.JSONDecodeError` catch → log warning → return None from
   `load_session()`. Treat as "no session on disk".
3. **kind mismatch** (file says pi, config says claude): operator
   changed `agent.kind` in WORKFLOW.md after a previous run. Per PRD,
   log `session_store_kind_mismatch` (WARN), proceed with fresh session.
   Do NOT delete the file — operator may switch back. (Or do delete?
   PRD doesn't say. Keep the file: less destructive, easier to debug.)
4. **Throttling**: PRD says "throttle to once per turn". Implement by
   only writing on EVENT_SESSION_STARTED + EVENT_TURN_COMPLETED.
   Compaction events / agent_retry events do NOT trigger writes —
   they don't change the session id and writes-per-tool-call would
   thrash disk on long pi runs.
5. **Atomic write contention**: `tempfile.mkstemp` + `os.replace` is
   per-issue, and only the dispatch worker for that issue writes its
   own file. No cross-process contention even with
   `max_concurrent_agents>1`.

## Relevant prior tickets

- SMA-19 ("Gemini multi-shot via --session-id"): kanban says Done but
  changes haven't merged to main. The current `gemini.py` still does
  the synthetic-id-only behavior. **Plan implication**: the gemini
  resume_session_id wiring in this ticket is a no-op at the CLI level
  today — it just preserves `_session_id` so the orchestrator's
  RunningEntry sees a stable id. When SMA-19 lands, the merge will
  pass that id to `gemini --session-id <id>` automatically because
  SMA-19's command-builder reads `self._session_id`. **No coordination
  ordering required between the two PRs.**
- SMA-22 (round-4 epic) and SMA-18 (pi cache split): unrelated to this
  ticket; PRD shared-rules apply.
