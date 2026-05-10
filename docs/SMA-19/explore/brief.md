# SMA-19 Explore brief

## Current `GeminiBackend` shape (src/symphony/backends/gemini.py)

- `start_session` synthesises `gemini-<hex12>` (line 119). NOT a UUID; ticket
  wants raw UUID4 string so it can also serve as `--session-id`.
- `run_turn` (line 126) builds `bash -lc "<self._gemini.command>"` with stdin
  carrying the prompt; `proc.communicate()` collects stdout. Return text is
  trimmed and used as `last_message`.
- Tokens stay at the zero-init dict (line 64). No JSON parse path exists.
- `is_continuation` is explicitly deleted (line 129) — multi-turn discontinuity
  baked in.
- Stderr ring buffer absent — instead, on non-zero exit, last 20 stderr lines
  are sliced from the captured blob into a `stderr_tail` list (lines 177-181).
  Acceptable per ticket §4.

## Established multi-turn pattern (claude_code.py, pi.py)

- `ClaudeConfig.resume_across_turns: bool` (workflow.py:223) — copy this.
- claude builds the resume flag via `shlex.quote` (claude_code.py:147). For
  gemini, since we mint the UUID locally and can guarantee its shape (RFC4122),
  shlex.quote is technically redundant — but keep it for symmetry / safety.
- `_update_usage_absolute` style accumulator (claude_code.py:329) shows the
  three-bucket invariant pattern: `total_tokens += billed_in + out_t`.

## Captured gemini --output-format json shape (per ticket)

```json
{
  "session_id": "...",
  "response": "Hello there, user.",
  "stats": {"models": {"<model-name>": {"tokens": {
    "input": int, "prompt": int, "candidates": int, "total": int,
    "cached": int, "thoughts": int, "tool": int}, ...}}}
}
```

Ticket-defined token mapping (matches pi pre-#18: cached folded into input):
```
input_tokens  +=  tokens["input"]      + tokens.get("cached", 0)
output_tokens +=  tokens["candidates"] + tokens.get("thoughts", 0) + tokens.get("tool", 0)
total_tokens   = input_tokens + output_tokens
```

`stats.models` may have multiple model entries (sub-agents, tool models) — sum
across all.

## Key invariants

1. Three-bucket: `total_tokens == input_tokens + output_tokens` after every
   accumulation. (PRD shared rule §2.)
2. Public API stability: don't rename `latest_usage` keys. (PRD §1.)
3. No new external deps. (PRD §3.) — Gemini's JSON shape is parsed with stdlib
   `json` (already imported by claude_code.py — gemini.py needs to add).
4. `--session-id` is idempotent on turn 1 (creates) and turn 2 (resumes), so
   we always pass the same UUID. No conditional flag-building needed —
   simpler than claude/pi.

## Test surface — three new tests required

- `test_gemini_session_id_is_minted_locally` — UUID format, persists across
  start_session/run_turn (need UUID validation, not just startswith).
- `test_gemini_parses_token_stats_from_json_output` — feed synthetic JSON
  blob, assert all three buckets populate correctly with multi-model sum
  and cached-into-input fold.
- `test_gemini_resume_across_turns_reuses_session_id` — session id stable
  across two `run_turn` calls when `is_continuation=True`.

The existing `test_gemini_session_id_synthesized` (line 231) asserts
`sid.startswith("gemini-")` — this MUST be updated since we now mint a raw
UUID. Either replace it or rewrite to assert UUID-shape.

## Plan candidates

### A) Minimal patch — keep stdout `proc.communicate()`, add JSON parser

- `start_session` → `self._session_id = str(uuid.uuid4())`.
- `run_turn` builds `f"{cmd} --skip-trust --output-format json --session-id {sid}"`.
  Pipe prompt to stdin; `proc.communicate()` as today.
- On rc==0 stdout, `json.loads(...)`. Extract `response` for last_message,
  `stats.models.*.tokens` for usage, and verify `session_id` matches (if
  mismatched, log and trust the local one).
- Token accumulation method `_update_usage_from_stats(models)`.
- Add `resume_across_turns: bool` to `GeminiConfig` and `_make_cfg` test fixture.

**Pros**: ~80 lines diff. No `_consume_stream` machinery to maintain. Matches
ticket §3 exactly. **Cons**: If gemini ever interleaves stderr verbosity in
front of stdout, we'd need backpressure — not relevant for one-shot json mode.

### B) Adopt full claude_code _consume_stream pattern

Overkill for one-shot JSON. Ticket explicitly says `_consume_stream` shape is
NOT required. Skip.

### C) Stream-json mode with JSONL events

Ticket explicitly puts this out of scope.

## Recommendation: **Option A**.

First failing test:
`test_gemini_session_id_is_minted_locally` — assert `uuid.UUID(sid)` parses
without raising and `start_session` then `run_turn` (mocked subprocess) keep
the same id.

## References

- src/symphony/backends/gemini.py (full file read)
- src/symphony/backends/claude_code.py:140-216 (run_turn pattern)
- src/symphony/backends/__init__.py:30-150 (Protocol + factory)
- src/symphony/workflow.py:227-234 (GeminiConfig), :541-555 (build path),
  :674-676 (validate_for_dispatch)
- tests/test_backends.py:52-105 (`_make_cfg`), :231-243 (existing gemini test)
- docs/PRD-telemetry-and-sessions.md (lines 1-35: shared rules)
