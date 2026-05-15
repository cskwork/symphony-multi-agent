# SMA-18 Explore notes

## Source-of-truth lines

- `src/symphony/backends/pi.py:29-31` — module docstring claims the
  intentional fold of `cacheRead + cacheWrite` into `input_tokens`. Must
  be rewritten to describe the 4-bucket contract.
- `src/symphony/backends/pi.py:86-90` — `_latest_usage` dict shape.
  Add `cache_input_tokens: 0`.
- `src/symphony/backends/pi.py:407-420` — `_update_usage` does the fold.
  Stop folding `cacheRead + cacheWrite` into `input_tokens`; instead
  accumulate them into `cache_input_tokens`. Total still equals sum of
  all three.
- `src/symphony/orchestrator.py:64-84` — `RunningEntry` accumulator
  fields (`codex_*` legacy prefix). Add `codex_cache_input_tokens` and
  `last_reported_cache_input_tokens` to mirror the existing pattern.
- `src/symphony/orchestrator.py:267-283` — `_running_row` exposes
  `tokens` block in JSON snapshot. Add `cache_input_tokens` key.
- `src/symphony/orchestrator.py:702-718` — `_apply_token_totals` reads
  three keys. Read optional `cache_input_tokens` and accumulate into
  the new RunningEntry field. Default to 0 when absent (claude / codex
  / gemini path).
- `src/symphony/orchestrator.py:530-538` (worker_turn_completed) and
  `:645-654` (agent_turn_completed) — both log `input/output/total`.
  PRD Verification expects `cache_input_tokens=K` in the
  `agent_turn_completed` line, so extend both logs.
- `tests/test_backends.py:542-562` — `test_pi_usage_accumulates_across_messages`
  must be updated: `input_tokens` no longer folds cache.

## Invariants to preserve

1. `total_tokens == input_tokens + output_tokens + cache_input_tokens`
   (the new "three-bucket-plus-cache" invariant from PRD AC#1/#2).
2. Numerical value of `total_tokens` unchanged from before the ticket
   for any sequence of `_update_usage` calls (PRD AC#2).
3. Claude / codex / gemini backends still emit a 3-key `latest_usage`;
   orchestrator must not crash when `cache_input_tokens` is absent.
4. JSON snapshot `tokens` block always carries the key (defaulting to
   0 for non-pi backends) so consumers can rely on its presence.

## Real-pi smoke command (from PRD §Verification)

```
SYMPHONY_LOG_LEVEL=INFO .venv/bin/symphony WORKFLOW.md --port 9999 ...
```

Capture stderr; grep `agent_turn_completed`; assert four `*_tokens=`
fields and the invariant.
