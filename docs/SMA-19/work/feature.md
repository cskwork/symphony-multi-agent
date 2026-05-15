# SMA-19 — Gemini multi-shot via `--session-id` + `--output-format json`

## Files touched

- `src/symphony/backends/gemini.py` — full rewrite of `start_session` /
  `run_turn`. Mints UUID4 in `start_session`, builds command with
  `--skip-trust --output-format json --session-id <uuid>` (always — flag is
  idempotent), parses single JSON object from stdout, sums
  `stats.models.*.tokens` per ticket mapping, keeps stderr ring buffer.
- `src/symphony/workflow.py` — `GeminiConfig.resume_across_turns: bool`
  added; `build_service_config` reads `gemini.resume_across_turns` (default
  True). No default on the dataclass field — mirrors `ClaudeConfig` /
  `PiConfig`.
- `tests/test_backends.py` — three new tests:
  - `test_gemini_session_id_is_minted_locally` — UUID4 round-trip + flag
    inclusion + skip-trust + output-format checks.
  - `test_gemini_parses_token_stats_from_json_output` — multi-model
    accumulation with cached/thoughts/tool fold; three-bucket invariant.
  - `test_gemini_resume_across_turns_reuses_session_id` — same UUID across
    turn 1 and turn 2 commands.
  - replaced obsolete `test_gemini_session_id_synthesized` (asserted
    `gemini-` prefix that no longer exists).
  - test fixture `_make_cfg` updated for new field.
- `tests/test_orchestrator_dispatch.py` — fixture updated for new field.
- `WORKFLOW.example.md`, `WORKFLOW.file.example.md` — gemini block notes
  the auto-appended flags + `resume_across_turns: true` default.
- `llm-wiki/agent-observability.md` — gemini multi-turn + token mapping
  cross-reference appended.
- `.claude/skills/using-symphony/reference/workflow-config.md` — was on
  the ticket's file list, but the Edit tool was permission-blocked for
  this path. Logged in `## Review` so an operator can apply the one-liner
  manually if desired (the user-facing copy is already captured in
  `WORKFLOW.example.md` and the wiki).

## Token mapping (per ticket)

```
input_tokens  +=  tokens["input"]      + tokens.get("cached", 0)
output_tokens +=  tokens["candidates"] + tokens.get("thoughts", 0) + tokens.get("tool", 0)
total_tokens   = input_tokens + output_tokens
```

Sum across `stats.models.*` so multi-model turns (sub-agents, tool models)
report combined cost. Three-bucket invariant `total == input + output`
asserted in test.

## Public-API impact

- `GeminiConfig` gains one field; `latest_usage` keys unchanged
  (input/output/total). Per PRD §1, this is an add — never a rename.
- `session_id` shape changes from `gemini-<hex12>` to RFC4122 UUID. The
  orchestrator only stores the string; no consumer parsed the prefix.
