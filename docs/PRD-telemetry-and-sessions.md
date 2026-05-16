# PRD — Telemetry & Session Continuity (round 4)

Three independent improvements, each suitable as one symphony ticket. They
do not depend on each other and may be dispatched in parallel
(`max_concurrent_agents: 3`). Codex `total_tokens` invariant fix (#17.5)
already shipped on `main` separately; this PRD covers the remaining
candidates from that round.

> **Working repo**: `cskwork/symphony-multi-agent`, branch off `main`.
> All tickets must keep `pytest -q` green (191 → ≥ 191) and pass real-CLI
> e2e smoke for the affected backend before transitioning to `Done`.

## Shared engineering rules

These apply to every ticket below:

1. **Public API stability**. The `AgentBackend` Protocol in
   `src/symphony/backends/__init__.py` and the JSON shape returned by
   `/api/v1/state` are public. Add fields, never rename or remove them.
2. **Three-bucket invariant**. `latest_usage["total_tokens"]` must equal
   `input_tokens + output_tokens` after every accumulation. Add new buckets
   as additional keys (e.g. `cache_input_tokens`) — do **not** redefine
   `total`. Tests must assert the invariant.
3. **No new external deps** without explicit go-ahead. The codebase has no
   network/IO libraries beyond `aiohttp`/`PyYAML`; keep it that way.
4. **Tests required**. Every behavioural change ships with at least one
   regression test. Use `tests/test_backends.py` / `tests/test_workflow.py`
   patterns; add a new file if the surface is genuinely new.
5. **Doctor-friendly failures**. If a config field becomes load-bearing
   (e.g. session-store path), `symphony doctor` must surface a clear
   FAIL/WARN line on misconfig rather than crashing mid-dispatch.
6. **No premature optimisation**. Atomic-write JSON files, in-memory
   dicts, simple async-await patterns — these are fine. Skip any
   coroutine-pool / lock-free / shared-memory tricks.

---

## Ticket #18 — Pi: separate `cache_input_tokens` from `input_tokens`

### Problem

`PiBackend._update_usage` (`src/symphony/backends/pi.py`) folds Pi's
`cacheRead + cacheWrite + input` into a single `input_tokens` bucket so
totals are unit-comparable with the Claude backend. This is documented as
intentional — but it conflates two things operators want to see
separately:

- **Fresh prompt tokens** the model actually had to read (`input`)
- **Cache traffic** that Anthropic bills at a discount and that
  legitimately should be visible separately for cost / context-size
  reasoning (`cacheRead`, `cacheWrite`)

Today the only way to see the cache split is to attach a debugger to the
backend or grep raw pi JSONL. Operators ask "why is my pi run showing
130k input tokens for what should be a 5k prompt?" and the answer
("4 LLM calls × 30k cached prompt") isn't surfaceable.

### Out of scope

- Changing the cross-backend unit-comparable view. `total_tokens` must
  stay equal to the sum that includes folded cache (so a pi run still
  shows "I burned ~131k tokens" in totals).
- Re-bucketing claude. Claude's cache_read / cache_creation rules are
  upstream-dictated and may differ from pi's; do this ticket pi-only.

### Goals (acceptance criteria)

1. `PiBackend.latest_usage` returns a 4-bucket dict:
   ```python
   {
     "input_tokens": int,           # uncached input, folded sum across messages
     "output_tokens": int,
     "cache_input_tokens": int,     # cacheRead + cacheWrite, folded
     "total_tokens": int,           # = input_tokens + output_tokens + cache_input_tokens
   }
   ```
2. Backwards-compatibility: callers that do `usage["total_tokens"]` see
   the same number they saw before this ticket landed (i.e. cache is
   still part of `total_tokens`). Add `cache_input_tokens`; don't subtract
   from `input_tokens`.

   - **Wait** — the spec says "separate" cache from input. Resolve the
     tension this way: rename the *internal-to-pi* contribution. Old
     `input_tokens` was `input + cacheRead + cacheWrite`; new
     `input_tokens` is just `input`; new `cache_input_tokens` is
     `cacheRead + cacheWrite`. `total_tokens = input + output + cache`.
     The numerical value of `total_tokens` is therefore identical to what
     it was before; only `input_tokens` shrinks and `cache_input_tokens`
     appears.
3. Orchestrator side: `_apply_token_totals` and the JSON snapshot row
   accept the new key. The TUI does not need to render it (there's no
   room on the card; expose via JSON only). Add it to the
   `tokens` block in `_running_row` (`src/symphony/orchestrator.py`).
4. Tests:
   - `test_pi_usage_separates_cache_input_tokens` — feed a synthetic
     `message_end` usage and assert all four buckets including the
     three-bucket-plus-one invariant `total = input + output + cache`.
   - The existing `test_pi_usage_accumulates_across_messages` must be
     updated to expect `input_tokens` to NOT include cache (numerical
     change). Document the change in the test docstring.
5. Other backends (claude / codex / gemini) keep their three-bucket
   shape. Their `_apply_token_totals` path may pass `cache_input_tokens=0`
   implicitly; the orchestrator must not crash on its absence.

### Files likely touched

- `src/symphony/backends/pi.py` — `_update_usage`, `latest_usage` init
- `src/symphony/orchestrator.py` — `RunningEntry` (add `pi_cache_input_tokens` accumulator), `_apply_token_totals`, `_running_row`
- `tests/test_backends.py` — pi usage assertions
- `docs/llm-wiki/agent-observability.md` — note the four-bucket contract

### Verification

Real `agent.kind=pi` smoke. Expected `agent_turn_completed` shape:

```
input_tokens=N output_tokens=M cache_input_tokens=K total_tokens=N+M+K
```

For a typical 1-turn pi run, expect `cache_input_tokens` to dwarf
`input_tokens` (it's the system prompt being reprocessed each LLM call).

### Estimated size

~40 lines in pi.py + ~15 lines in orchestrator.py + ~30 lines of tests.
**One agent turn should be enough.** No new files.

---

## Ticket #19 — Gemini: multi-shot via `--session-id` + `--output-format json`

### Problem

`GeminiBackend` (`src/symphony/backends/gemini.py`) currently calls
`gemini -p ""` per turn with **no session continuity** between turns and
**no token telemetry** (turns report `input_tokens=0 output_tokens=0
total_tokens=0`). Smoke testing on round 3 confirmed: a multi-turn
gemini run loses all conversation history between turns and looks
visually identical to a stuck run from the operator's side.

Gemini CLI 0.41+ already exposes the building blocks:

```
-r, --resume <id_or_index>     resume a previous session
    --session-id <UUID>        start a new session with a chosen UUID
-o, --output-format            text | json | stream-json
    --skip-trust               trust the cwd in headless mode
```

Captured shape of `gemini --skip-trust --output-format json -p ""`:

```json
{
  "session_id": "88974b6c-…",
  "response": "Hello there, user.",
  "stats": {
    "models": {
      "gemini-3-flash-preview": {
        "tokens": {
          "input": 13045, "prompt": 13045, "candidates": 48,
          "total": 13093, "cached": 0, "thoughts": 48, "tool": 0
        },
        "api": { "totalRequests": 1, "totalLatencyMs": 2015 }
      }
    }
  }
}
```

### Out of scope

- Gemini stream-json mode (incremental events). The one-shot json mode
  above is sufficient and simpler.
- Session resumption across symphony restarts (covered by Ticket #20).
- Caching/billing nuance — gemini reports `cached` separately but for
  this ticket it's fine to fold into `input_tokens` (matching pi's
  pre-#18 behaviour).

### Goals (acceptance criteria)

1. `GeminiConfig` gains a `resume_across_turns: bool = True` field
   matching `claude` and `pi`. Default true so existing workflows
   benefit automatically.
2. `GeminiBackend`:
   - `start_session` mints a UUID locally and remembers it as
     `_session_id`. Returns it.
   - `run_turn` builds the command with `--skip-trust --output-format
     json --session-id <uuid>` (always — session-id is idempotent on
     turn 1 because it just declares the new session's id) and pipes
     the prompt to stdin. **Note**: gemini's `--prompt` argument is
     appended to stdin per its docs; we keep `-p ""` so stdin alone is
     the prompt.
   - On stdout, parse the single JSON object. Extract `session_id`
     (use it to update `_session_id` in case gemini changed it),
     `response` (use as `last_message`), and `stats.models.*.tokens`
     (sum across all model entries → `_latest_usage`).
   - Token mapping (per-model entry):
     ```
     input_tokens  +=  tokens["input"]    + tokens.get("cached", 0)
     output_tokens +=  tokens["candidates"] + tokens.get("thoughts", 0) + tokens.get("tool", 0)
     total_tokens  =  input_tokens + output_tokens
     ```
     If `tokens["total"]` is present and matches `input + candidates`,
     prefer adding the components (so we capture cached / thoughts /
     tool which gemini's `total` may or may not include — see probe
     output where `total=13093 = input(13045) + candidates(48)`,
     ignoring `cached/thoughts/tool`).
3. The output-format change implies the existing
   `proc.communicate()`-and-decode approach already works (gemini
   prints the JSON object then exits). The `_consume_stream` shape
   from pi/claude is **not** required.
4. Stderr capture: keep the existing `stderr_tail` ring buffer
   pattern (`gemini.py` already follows this; preserve it after the
   refactor).
5. Doctor: no new check needed — `gemini` already on `$PATH` covers
   the new flags. If you find a way to verify `--output-format json`
   works without spawning a real model call, add it as a WARN check.
6. Tests:
   - `test_gemini_session_id_is_minted_locally` — session id is a UUID
     and persists across `start_session` / `run_turn` calls.
   - `test_gemini_parses_token_stats_from_json_output` — feed a
     synthetic gemini JSON blob and assert all three buckets populate.
   - `test_gemini_resume_across_turns_reuses_session_id` —
     `is_continuation=True` on the second `run_turn` doesn't mint a new
     id.

### Files likely touched

- `src/symphony/backends/gemini.py` — most of the changes
- `src/symphony/workflow.py` — add `resume_across_turns` to `GeminiConfig`
- `tests/test_backends.py` — three new gemini tests
- `WORKFLOW.example.md` / `WORKFLOW.file.example.md` — update the
  `gemini:` block snippet to show the new command and field
- `.claude/skills/using-symphony/reference/workflow-config.md` — bump
  the gemini bullet to mention "now multi-turn capable"
- `docs/llm-wiki/agent-observability.md` — gemini section

### Verification

Real `agent.kind=gemini` smoke with the same `kanban/SMOKE-*.md` ticket
fixture used in round 3. **Expectation**: 2-turn run (Todo → In Progress
→ Done) where turn 2 demonstrably has access to turn 1's context.
Concrete check: instruct the agent in turn 1 to pick a "memo word", then
in turn 2 ask it to recite the word — the response in `last_message`
must contain that word.

### Estimated size

~80 lines in gemini.py + ~15 lines in workflow.py + ~50 lines of tests
+ ~10 lines of doc updates. **Two agent turns** is reasonable.

---

## Ticket #20 — Session ID persistence across symphony restarts

### Problem

Every backend (`pi`, `claude`, `codex`) supports session resumption via
`--session <id>` / `--resume <id>` / `thread/start { threadId: ... }`,
and symphony already exploits this **within a single symphony process**:
turn 2 onward of the same dispatch reuses the session minted on turn 1.

But the session id lives only on the in-memory `RunningEntry`. If
symphony crashes / is killed / restarts mid-ticket:

1. The next `dispatch` mints a **new** session.
2. The agent CLI loses all prior conversation context.
3. Cache savings are wiped (Claude / Pi rebuild prompt cache from scratch).
4. Token totals on the JSON snapshot reset to zero, masking real cost.

For long-running tickets (e.g. a multi-day refactor that flows through
the eight-stage pipeline), this is a real cost.

### Out of scope

- Cross-machine session migration. Sessions are local state; if you
  move the workspace to a new host, starting fresh is acceptable.
- Encrypting the on-disk session id. It's no more sensitive than the
  workspace contents themselves and lives next to them.
- Distinguishing "agent crashed mid-turn" vs "symphony crashed
  mid-turn". Both cases cause the same recovery: resume the session and
  let the next turn pick up.

### Goals (acceptance criteria)

1. **On-disk store**: per-issue session metadata at
   `<workspace.root>/<ID>/.symphony-session.json` with shape:
   ```json
   {
     "version": 1,
     "agent_kind": "pi",                 // matches the kind that minted it
     "session_id": "019e0f48-…",
     "minted_at": "2026-05-10T00:43:23Z",
     "last_used_at": "2026-05-10T00:43:41Z"
   }
   ```
   Use atomic write (`tempfile.mkstemp` + `os.replace`, mirroring
   `tracker_file.write_ticket_atomic`).
2. **Write path**: every time `_on_codex_event` records a
   `session_id` for an issue, also flush the JSON file. (Throttle to
   once per turn to avoid disk thrash; safe since we already only
   handle session_started + per-turn updates.)
3. **Read path**: `_dispatch` reads the file before constructing the
   `BackendInit`. If the file exists and `agent_kind` matches the
   current `cfg.agent.kind`, pre-populate the new `RunningEntry` with
   the stored `session_id` AND tell the backend to resume. If the file
   exists but `agent_kind` differs (operator changed agent.kind in
   WORKFLOW.md), log `session_store_kind_mismatch` (WARN) and start
   fresh.
4. **Backend integration**: extend `BackendInit` with an optional
   `resume_session_id: str | None = None`. Each backend, when set:
   - `pi`: pass `--session <id>` on **turn 1** as well (currently only
     for `is_continuation=True`). The CLI is happy to resume a known
     session.
   - `claude`: same — `--resume <id>` on turn 1 onward.
   - `codex`: replace the `thread/start` call with
     `thread/resume { threadId: <id> }` if the codex 0.130 protocol
     supports it; otherwise mint a new thread and log
     `session_resume_unsupported` (codex doesn't actually persist
     threads across `app-server` restarts as of writing — verify via
     probe before assuming). If unsupported, set `resume_session_id`
     to None on the entry and proceed normally.
   - `gemini`: pass `--session-id <id>` (already in scope per #19).
5. **Cleanup**: on `worker_exit reason=normal`, the session file
   stays on disk (next run can resume). On
   `workspace_manager.remove(...)` (reconcile force-delete), the file
   goes away with the workspace — no extra logic needed.
6. **Doctor check**: `check_session_store_writable` validates that
   `<workspace.root>/.symphony-write-probe` can be created+removed.
   Already covered by `check_workspace_root` for the parent dir, so
   this may be a no-op — verify before adding.
7. **Tests** (in `tests/test_session_store.py`, new file):
   - `test_writes_atomic_session_file` — after `_on_codex_event`
     emits `session_started`, file exists with the right JSON.
   - `test_round_trip_through_restart` — write file, re-instantiate
     orchestrator, dispatch the same ticket, observe that
     `RunningEntry.session_id` matches the persisted value.
   - `test_kind_mismatch_starts_fresh` — file says `pi`, config says
     `claude`, expect WARN log + fresh session minted.
   - `test_corrupt_file_falls_back_gracefully` — file is invalid
     JSON; orchestrator logs warning and proceeds with a fresh
     session.

### Files likely touched

- `src/symphony/session_store.py` — new module, ~80 lines
- `src/symphony/orchestrator.py` — wire load/save into `_dispatch` and
  `_on_codex_event`
- `src/symphony/backends/__init__.py` — `BackendInit.resume_session_id`
- `src/symphony/backends/{pi,claude_code,gemini,codex}.py` — honour
  `resume_session_id` on first turn
- `src/symphony/doctor.py` — possibly a new check (see goal #6)
- `tests/test_session_store.py` — new file
- `tests/test_orchestrator_dispatch.py` — extend for resume path
- `docs/llm-wiki/` — new entry `session-persistence.md` indexed in `INDEX.md`
- `.claude/skills/using-symphony/reference/workflow-config.md` — note
  the new file's location; mention "delete the file to force fresh
  session"

### Verification

Two-phase smoke:

**Phase 1 — write side**:
```bash
symphony ./WORKFLOW.md --port 9988 &
sleep 30   # let dispatch run, session start
ls -la ~/symphony_workspaces/<ID>/.symphony-session.json
# expect: file exists, contains valid JSON with current agent_kind
```

**Phase 2 — restart resumption**:
```bash
kill $(lsof -ti :9988)
# Edit kanban/<ID>.md to set state back to In Progress (or insert a new sub-task)
symphony ./WORKFLOW.md --port 9988 &
grep agent_session_started log/symphony.log
# expect: session_id matches the value in .symphony-session.json
# expect: NO new session id in this dispatch
```

For pi specifically, also verify the agent has memory of the prior
conversation — ask it to summarise the prior turn in the new dispatch's
prompt; the answer should reference what was discussed before the
restart.

### Estimated size

~120 lines new (session_store.py) + ~30 lines wiring + ~80 lines tests
+ docs. **3–4 agent turns** depending on how the agent splits the work
across the eight-stage pipeline. The Plan stage should call out the
codex resumption uncertainty (goal #4 third bullet) explicitly.

---

## Cross-cutting verification (Ticket #21, after all three above land)

Before tagging the round complete:

1. `pytest -q` — all tests pass; total count ≥ 191 + new ones.
2. `symphony doctor ./WORKFLOW.md` — clean for every `agent.kind`
   value across the four backends.
3. Real-CLI e2e for **all four backends** with the seeded
   `number.ts` fixture (same one used in rounds 1–3):
   - pi: 2-turn run, see `cache_input_tokens > 0` in
     `agent_turn_completed`
   - claude: unchanged
   - codex: unchanged
   - gemini: 2-turn run, turn 2 references turn 1 context
4. Restart resumption: kill symphony mid-pi-turn, restart, observe
   `session_resumed` log line and matching session_id.
5. Update `docs/llm-wiki/INDEX.md` with the new entries.
6. Update `using-symphony` skill description to advertise the
   four-bucket pi usage and gemini multi-turn.

When all six are green, open one PR per ticket (#18, #19, #20)
sequenced in that order so reviewers see the smallest first. Squash on
merge with the original ticket number in the subject.
