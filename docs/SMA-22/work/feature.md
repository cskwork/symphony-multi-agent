# SMA-22 — Implementation notes

## Summary

Two adjacent UX gaps in the Kanban TUI fixed in one display-only patch
(`src/symphony/tui.py`) plus a small `WorkflowConfig` knob:

1. **Lane wrap on narrow terminals.** `_render` now consults
   `console.width` against the new `tui.lane_wrap_width` threshold and
   emits either one or two `Columns` groups inside the rendered
   `Group`. Default threshold is 200 (matches the spec); `0` is the
   documented "always one row" sentinel; explicit positive values let
   operators tune it per-machine.

2. **Persisted per-card token meta.** Token totals now render whenever
   the snapshot carries non-zero usage for a card, regardless of
   runtime. Live (`running`) cards keep the existing loud cyan
   palette; non-running cards use the `dim` palette so the eye is
   still drawn to active work.

3. **Footer keybinding hint.** `_build_footer` appends a localised
   `footer.controls` line ("[j/k] scroll   [q] quit"). `r` and `?`
   are deliberately omitted — they are not currently bound, and the
   spec said not to invent new ones.

## Files touched

| File | What changed |
|------|--------------|
| `src/symphony/workflow.py` | Added `TuiConfig.lane_wrap_width: int = 200`; new `_validated_non_negative_or_default` validator (mirrors the strict positive sibling but accepts `0` as the documented off-sentinel); wired into `build_service_config`. |
| `src/symphony/i18n.py` | Added `footer.controls` key for `en` + `ko`. |
| `src/symphony/tui.py` | New `_lane_rows()` helper for the wrap split (uses `math.ceil(n/2)`). New `_append_token_meta()` helper that paints the `in / out / total` triple in either loud or dim palette. `_render_card` now builds `tokens_line` whenever `status.tokens > 0` and appends it after `meta`. `_build_footer` wraps the existing `line` + a new dim controls Text in a `Group`. |
| `tests/test_tui.py` | Added the four spec-named tests (`test_lanes_wrap_below_threshold`, `test_lanes_single_row_above_threshold`, `test_lane_wrap_disabled_when_zero`, `test_done_card_keeps_tokens`) plus stub `_StubOrchestrator` / `_StaticWorkflowState` fixtures and a `_columns_in()` walker that counts `Columns` instances in a `Group` tree. |
| `tests/test_i18n.py` | Mirrored `max_cards_per_column` parsing tests for `lane_wrap_width` (default, explicit, 0-sentinel, invalid → raises). |
| `WORKFLOW.example.md`, `WORKFLOW.file.example.md` | Added `lane_wrap_width: 200` line under the `tui:` block. |

## Deferred

- `.claude/skills/using-symphony/reference/workflow-config.md` — listed
  in the ticket's "Files likely touched" but blocked by the
  sensitive-file gate. The 3-line addition (yaml example + one `###`
  subsection paragraph) is queued for the reviewer to apply manually
  during merge. Not required by acceptance criteria 1-6.

## Test results

`.venv/bin/pytest -q` → **200 passed, 2 skipped** in 5.19s on the host
repo. The four new TUI tests + four new workflow-parsing tests all
go RED → GREEN exactly once after the implementation is wired in.
