# TUI rendering — Textual app structure and invariants

**Summary:** `src/symphony/tui.py` is the operator-facing Kanban view, built
on the [Textual](https://textual.textualize.io) framework. It composes a
`KanbanApp(App)` with a header, a stats bar, one `Lane` widget per tracker
state, and a `Footer` that auto-renders the app's `BINDINGS`. Data comes
from two sources every tick: `Orchestrator.snapshot()` for live runtime
overlays (tokens, last event, retry posture) and the tracker client (Linear
or file) for the candidate / terminal issue lists. Layout, scroll, focus,
and mouse handling are entirely the framework's job — no manual layout
math lives in this module.

The compatibility wrapper `KanbanTUI(orchestrator, workflow_state).run()`
is the only public entry point; cli.py and the launcher scripts call it.
Internally it delegates to `KanbanApp.run_async()`.

## Widget tree

```
KanbanApp(App)
├── Header(show_clock=True)
├── StatsBar (#stats)                  ← agent / tracker / counts / tokens
├── Container(#board, layout=horizontal)
│   ├── Lane (one per state in active+terminal order)
│   │   ├── Static.lane-title          ← "Todo (3)"
│   │   ├── Static.lane-legend         ← state_descriptions[state]
│   │   └── VerticalScroll
│   │       └── IssueCard×N            ← focusable, opens TicketDetailScreen
│   ├── Lane …
└── Footer()                           ← auto-shows BINDINGS
```

Modal: `TicketDetailScreen(ModalScreen[None])` — pushed on `enter` from a
focused `IssueCard`, dismissed on `esc` / `q`.

## Refresh model

- `set_interval(0.5, self._refresh_runtime)` — heartbeat that re-reads
  `Orchestrator.snapshot()` and updates lane card counts + token totals.
  Necessary so the "silent N s" badge ticks over even when no orchestrator
  event fires.
- `set_interval(poll_s, self._kick_tracker_refresh)` — periodic tracker
  fetch in a worker (`run_worker(..., exclusive=True, group="tracker")`)
  so a slow Linear API never stalls the UI.
- `Orchestrator.add_observer(self._on_orchestrator_tick)` — observer fires
  on every orchestrator tick; bounces through `self.post_message(_RefreshNow())`
  so widget updates stay on the Textual event loop.

## Card diff (DuplicateIds avoidance)

`Lane.render_cards()` does NOT call `remove_children()` followed by
`mount_all()`. `remove_children()` is asynchronous — the framework only
schedules the removal — so a fast second tick (heartbeat racing with the
prime poll) would re-mount cards while the prior generation is still in
the node list, and the per-card `id="card-<safe-issue-id>"` would collide
with `DuplicateIds`. Instead the lane:

1. Builds a dict of `{card_id → existing IssueCard}`.
2. For each desired card, either calls `existing.update_status(...)`
   (in place — preserves focus / scroll position) or `mount(IssueCard(...))`
   (only when the issue is new to this lane).
3. Removes any leftover cards (issue moved to another lane / closed).
4. Mounts the empty-state placeholder only when the lane is empty AND no
   placeholder is already present.

This also means a user's focus stays on the card they were inspecting
across redraws — important because focus drives the `enter`-opens-detail
binding.

## Invariants

- **No raise on missing data.** `_refresh_runtime` returns early when
  `WorkflowState.current()` is `None`. The compose tree always renders.
- **Pure helpers are exported.** `_parse_iso`, `_silent_seconds`,
  `_truncate`, `_first_meaningful_line`, `_card_sort_key`,
  `_compact_rate_limits`, `_ordered_column_states`, `_build_runtime_index`
  are tested as pure functions in `tests/test_tui.py` so card-rendering
  logic stays unit-testable without booting the app.
- **Snapshot drop is intentional.** `Orchestrator` drops `RunningEntry`
  when a worker exits, so per-issue token totals only live in
  `snap["running"][i].tokens` while the worker is active. Idle / completed
  cards fall back to the workspace-wide `codex_totals` shown in the stats
  bar; non-running cards still surface their last known per-issue tokens
  in dim cyan when present (forward-compatible with future per-issue
  persistence).
- **Silence threshold:** `SILENT_THRESHOLD_S = 30.0` — past this, a running
  card grows a yellow `silent Ns` badge. Sized to outlive the longest
  expected agent warm-up (Opus-4 cold start ≈30 s) so healthy runs never
  trip it.

## Files of interest

- `src/symphony/tui.py:KanbanApp` — App subclass, BINDINGS, compose, refresh
  intervals, action methods.
- `src/symphony/tui.py:Lane` — per-state widget; `render_cards()` is the
  diff-and-mount path described above.
- `src/symphony/tui.py:IssueCard` — focusable Static; `update_status()` is
  the in-place refresh path.
- `src/symphony/tui.py:StatsBar` — top-row Static; rebuilds the rich `Text`
  on each `update_from(cfg, snap)` call.
- `src/symphony/tui.py:TicketDetailScreen` — ModalScreen with the full
  description in a VerticalScroll.
- `src/symphony/tui.py:KanbanTUI` — async wrapper preserving the old
  `tui.run()` signature; new code should not depend on the wrapper.
- `tests/test_tui.py` — pure-helper unit tests + Textual `Pilot` smoke
  tests (boot the app, render, press keys, assert widget state).

## Decision log

- 2026-05-09 | SMA-?? | Added `SILENT_THRESHOLD_S` badge for stalled running
  cards.
- 2026-05-09 | SMA-?? | Original Rich `Live` implementation introduced
  `tui.max_cards_per_column` to bound visible card counts (silent overflow
  in `rich.live.Live(screen=True)` was the original footgun).
- 2026-05-10 | SMA-22 | Added `tui.lane_wrap_width` for narrow-terminal
  splits; persisted per-card token meta past worker exit.
- 2026-05-10 | post-SMA-22 | **Migrated TUI from `rich.live.Live` to
  Textual.** Lanes/cards/modal became real widgets so focus, mouse wheel,
  and detail drill-down are framework-handled. `_keyboard.py` (250 LOC of
  raw-mode + SGR mouse parsing) and the manual lane-row / page math were
  removed. `TuiConfig.max_cards_per_column` and `lane_wrap_width` were
  dropped — Textual's `VerticalScroll` and horizontal `Container` make
  them no-ops. The legacy keys are silently ignored if still set in an
  operator's `WORKFLOW.md` (no breaking change). Public API
  `KanbanTUI(orch, state).run()` was preserved so cli.py kept working.

**Last updated:** 2026-05-10 by the Textual migration follow-up.
