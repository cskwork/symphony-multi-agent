# SMA-18 Implementation note

## Change shape

Pi `latest_usage` grew from 3 to 4 buckets:

```python
# Before (pre-SMA-18)
{"input_tokens": int, "output_tokens": int, "total_tokens": int}

# After (SMA-18)
{
    "input_tokens": int,           # uncached input only
    "output_tokens": int,
    "cache_input_tokens": int,     # cacheRead + cacheWrite
    "total_tokens": int,           # = input + output + cache
}
```

`total_tokens` numerical value is unchanged — only the partition between
`input_tokens` and `cache_input_tokens` shifted.

Other backends (claude, codex, gemini) keep their three-bucket dict.
Orchestrator's `_apply_token_totals` defaults missing `cache_input_tokens`
to 0, so non-pi paths are unaffected.

## Files touched

| file | section | reason |
|---|---|---|
| `src/symphony/backends/pi.py` | docstring (lines 25-37) | Document the new 4-bucket contract |
| `src/symphony/backends/pi.py` | `_latest_usage` init (line 90+) | Add `cache_input_tokens: 0` |
| `src/symphony/backends/pi.py` | `_update_usage` (lines 412-430) | Stop folding cache into input; track separately |
| `src/symphony/orchestrator.py` | `RunningEntry` (line 64+) | Add `codex_cache_input_tokens` accumulator + `last_reported_cache_input_tokens` delta tracker |
| `src/symphony/orchestrator.py` | `_apply_token_totals` (lines 702+) | Read optional `cache_input_tokens`; default to 0; accumulate delta |
| `src/symphony/orchestrator.py` | `_running_row` `tokens` block | Expose `cache_input_tokens` in the JSON snapshot |
| `src/symphony/orchestrator.py` | `worker_turn_completed` log line | Add `cache_input_tokens=N` field |
| `src/symphony/orchestrator.py` | `agent_turn_completed` log line | Add `cache_input_tokens=N` field — required by PRD §Verification |
| `tests/test_backends.py` | `test_pi_usage_accumulates_across_messages` | Update expected bucketing (cache no longer folded into input); assert new invariant |
| `tests/test_backends.py` | new `test_pi_usage_separates_cache_input_tokens` | Single-message: assert all 4 buckets and `total = in + out + cache` |
| `tests/test_orchestrator_dispatch.py` | new 3 tests | (a) accumulates cache, (b) tolerates absent key, (c) `_running_row` exposes it |

## Invariants asserted

- `total_tokens == input_tokens + output_tokens + cache_input_tokens` —
  asserted in both new pi tests.
- `cache_input_tokens` defaults to 0 when the backend omits it —
  asserted in `test_apply_token_totals_tolerates_missing_cache_input_tokens`.
- JSON snapshot `tokens` block always carries the key — asserted in
  `test_running_row_exposes_cache_input_tokens`.

## Deliberately NOT changed

- Orchestrator-global `_CodexTotals` does not gain a cache field. Cache
  is already part of `total_tokens`, so the global aggregate stays
  correct without redundant tracking. Adding a global cache counter is
  a future ticket if billing/cost reporting needs it.
- TUI: PRD AC#3 explicitly says "TUI does not need to render it".
- Other backends' `_update_usage` paths: out of scope per ticket.

## Test result

```
.venv/bin/pytest -q
195 passed, 2 skipped in 4.51s
```

(was 191 → 195, growth = 4 new tests + 0 regressions)
