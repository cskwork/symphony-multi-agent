# TUI rendering — display invariants and config knobs

**Summary:** `src/symphony/tui.py` is the operator-facing Kanban view. It
reads the orchestrator's JSON snapshot, *not* the tracker, so its data
horizon is whatever the orchestrator currently holds. Three display-time
knobs live under `tui.*` in `WORKFLOW.md`: `language`,
`max_cards_per_column`, `lane_wrap_width`. Two non-trivial display
constraints recur often enough to surface here:

1. The orchestrator drops `RunningEntry` (and therefore per-issue token
   totals) the moment a worker exits. The snapshot has no historical
   per-issue token field, only `codex_totals` (workspace-wide running
   totals). Card-level token rendering is therefore live-only by
   construction, with one exception: there is a brief window between
   the agent transitioning a ticket to a terminal state and the worker
   exiting where the snapshot still has `running[].tokens` for a card
   whose `state == "Done"`. The TUI must keep showing the meta line
   during that window or the cost trail vanishes mid-flight.
2. `rich.columns.Columns` is one row only. Multi-row "wrap" layouts
   are built by emitting two `Columns(...)` groups inside a single
   `Group(...)`. Rich's natural fallback when panels don't fit
   horizontally is to flow them into multiple visual rows *within
   each Columns group* — that's not a wrap; it's Columns' natural
   behaviour. Use `lane_wrap_width` to force structural splits when
   the operator's terminal is too narrow for the full lane count.

## Invariants & Constraints

- The TUI never raises on missing data. `_render` returns
  `Group(Panel("workflow not loaded", border_style="red"))` when
  `WorkflowState.current()` is `None` — never blanks the screen and
  never crashes the live loop.
- Render is gated on `_render_signal` (orchestrator tick observer +
  2 Hz heartbeat). New display knobs must therefore be safe to read
  on every tick, not just at startup.
- `_build_runtime_index` returns only entries for issues currently in
  `snap["running"]` or `snap["retrying"]`. Cards rendered for issues
  outside that set get the default `_CardStatus(runtime="idle",
  tokens=0, ...)`. Display logic that wants to differentiate
  "running with tokens" from "anywhere else with tokens" must gate on
  `tokens > 0`, not on `runtime == "running"`.
- Per-card visual layout reserves five potential lines (top-down):
  title (`identifier + glyph`), body (`title + Pn`), description preview,
  meta (`turn N / silent / last_event`, retry, blockers, or labels),
  tokens (when `tokens > 0`), last_message. Adding a new line costs
  density at the column-cap boundary — see SMA-22 review notes on the
  inline-vs-separate token-line refinement.
- `lane_wrap_width: 0` is the documented "always one row" sentinel.
  Negative / non-numeric / bool values raise `ConfigValidationError`
  via `_validated_non_negative_or_default` — sibling of the strict
  positive-int validator that allows zero specifically as a sentinel
  (round-4 doctor-friendly invariant).
- The footer hint (`footer.controls` i18n key) lists only bindings
  the TUI actually wires up. There is no `r` (refresh) or `?` (help)
  binding; surfacing them would mislead operators. If a binding is
  added, update both EN and KO entries.

## Files of interest

- `src/symphony/tui.py:_render` — `Group(header, *_lane_rows(panels,
  cfg.tui.lane_wrap_width), footer)`. Returns one or two `Columns`
  groups via `_lane_rows()` (split point is `math.ceil(n/2)`).
- `src/symphony/tui.py:_render_card` — per-card layout. Token meta is
  inline within the running branch; non-running cards get a separate
  `tokens_line` whenever `status.tokens > 0` (forward infrastructure
  — currently unreachable because the snapshot doesn't carry tokens
  for non-running rows, but lives there for the future when
  per-issue completed tokens may be persisted).
- `src/symphony/tui.py:_append_token_meta` — paints the
  `in / out / total` triple in either loud or dim cyan. Single source
  of truth for the token palette; both code paths call it.
- `src/symphony/tui.py:_build_footer` — wraps the totals line and the
  controls hint in `Group(...)` inside a `Panel`.
- `src/symphony/i18n.py` — `header.controls`, `footer.controls`,
  `card.turn`, `card.retry`, `card.blocked_by`, `column.empty`,
  `column.more`, `column.more_above`, language fallbacks.
- `src/symphony/workflow.py:TuiConfig` — `language`,
  `max_cards_per_column`, `lane_wrap_width`. New fields with
  sentinel-zero semantics should reuse
  `_validated_non_negative_or_default`.
- `tests/test_tui.py` — `_StubOrchestrator` / `_StaticWorkflowState`
  fixtures + `_columns_in()` walker make the render path testable
  without a live `rich.live.Live`.

## Decision log

- 2026-05-09 | SMA-?? | Added `SILENT_THRESHOLD_S` badge for stalled
  running cards. Threshold sized at 30 s to outlive Opus-4 cold start
  so healthy runs never trip it.
- 2026-05-09 | SMA-?? | Introduced `tui.max_cards_per_column` to cap
  long columns and surface `+N more` indicators (silent overflow in
  `rich.live.Live(screen=True)` was the original footgun).
- 2026-05-10 | SMA-22 | Added `tui.lane_wrap_width` (default 200,
  `0` = off-sentinel) for narrow-terminal split. Persisted per-card
  token meta past the running runtime by gating on `tokens > 0` and
  styling dim for non-running. Footer gained a localised `[j/k]
  scroll   [q] quit` hint (omits unbound `r` and `?` per spec).
  Added `_validated_non_negative_or_default` so future
  sentinel-zero TuiConfig fields can reuse the strict-but-zero shape.

**Last updated:** 2026-05-10 by SMA-22.
