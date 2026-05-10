# SMA-22 — Explore notes

## Where the problems live

### Problem A — single-row layout in `_render`
- `src/symphony/tui.py:273` — `Group(header, Columns(column_panels, expand=True, equal=True), footer)`.
  `expand=True, equal=True` forces every state lane into a single row regardless of width. With
  7 active + 2 terminal = 9 lanes the cards are unreadable on terminals < ~200 chars.
- `_build_columns` (lines 321–412) returns `list[Panel]` already in declared order (active first,
  then terminal). De-dup preserves order; the consumer in `_render` is the only place that decides
  the layout.

### Problem B — token meta gated on running runtime
- `_render_card` (lines 414–477). The meta line that prints `in / out / total` lives **inside the
  `if status.runtime == "running":` branch** (lines 431–446). When a ticket flips to `Done` /
  `Blocked`, runtime is no longer `"running"` and the cost line vanishes from the card.
- Note: orchestrator drops `RunningEntry` after worker exit, so `_build_runtime_index` only carries
  rows for currently-running / retrying issues. The bug is visible in the *narrow window* where
  the agent has already transitioned the ticket to `Done` but its worker hasn't yet exited — the
  snapshot still has tokens but the renderer ignores them. Fix is display-only: gate the meta on
  `status.tokens > 0` (not on `runtime`); dim it for non-running so live cards stay loud.

## Configuration plumbing reused

- `TuiConfig` lives at `src/symphony/workflow.py:258-271`. Existing fields: `language`,
  `max_cards_per_column`. Construction in `build_service_config` at lines 586-602.
- `_validated_positive_or_default` (`workflow.py:624-638`) is the strict validator (raises on
  bool / non-int / ≤0). `max_cards_per_column` does **not** use it — it silently coerces invalid
  inputs to `None`.
- For `lane_wrap_width` we want a tightly typed positive-int validator that *additionally* accepts
  `0` as the documented "off" sentinel (per acceptance criterion 2). Negative / bool / non-int
  raise `ConfigValidationError`.

## Console width access

- `rich.console.Console` exposes `.width` as a property (current terminal width, with fallback).
  Tests can construct `Console(width=N)` to control it.
- TUI already holds `self._console`. `_render` can read `self._console.width` to decide whether
  to wrap.

## Keybinding inventory (for footer hint)

From `_handle_key` in `tui.py` and `_keyboard.py`:
- `q`, `Q`, `ESC`, `CTRL_C` → quit
- `j`, `J`, `DOWN`, `PGDN` → scroll down
- `k`, `K`, `UP`, `PGUP` → scroll up
- `g`, `HOME` → top
- `G`, `END` → bottom

There is no `r` (refresh) handler nor a `?` (help) handler. The spec said "If `?` is not
currently bound, omit it." Same applies to `[r] refresh`. Real bindings → `[j/k] scroll` and
`[q] quit`. Add a parallel localized i18n key (`footer.controls`) so the footer line follows the
existing pattern rather than hardcoding English.

## Test surface

- `tests/test_tui.py` is small (67 lines) — only covers helpers. No layout assertions.
- `tests/test_i18n.py:183-234` already parametrizes `tui.max_cards_per_column` valid + invalid
  inputs. Mirror this for `tui.lane_wrap_width` (default, explicit value, the `0` sentinel,
  invalid → raise).

## Files I will touch

| File | Change |
|------|--------|
| `src/symphony/workflow.py` | Add `TuiConfig.lane_wrap_width: int = 200`; tiny non-negative-int validator. |
| `src/symphony/i18n.py` | Add `footer.controls` key for EN + KO. |
| `src/symphony/tui.py` | `_render`: split lanes into 1 or 2 rows based on console width × `lane_wrap_width`. `_render_card`: render token meta whenever `status.tokens > 0`, dim for non-running. `_build_footer`: append a dim controls hint line. |
| `tests/test_tui.py` | Four new tests (lane wrap below / above threshold, disabled-when-zero, done card keeps tokens). |
| `tests/test_i18n.py` | `lane_wrap_width` parsing tests (default, explicit, 0-sentinel, invalid raises). |
| `WORKFLOW.example.md`, `WORKFLOW.file.example.md` | One-line `lane_wrap_width: 200` under `tui:`. |
| `.claude/skills/using-symphony/reference/workflow-config.md` | One-line note on `lane_wrap_width`. |

## Risks / unknowns

- `Columns` lays out one row only — wrapping is two `Columns(...)` groups in sequence inside
  `Group`. Each row's width auto-expands so this stays a one-line change in `_render`.
- Lane distribution: spec says "roughly evenly across rows (e.g. 4 + rest), preserving the
  declared order". Use `n_first = math.ceil(len(panels) / 2)`. For 9 lanes → 5 + 4. For 7 → 4 + 3.
- Token dim styling: existing live styling is `cyan / bright_cyan / bold cyan` per piece. For
  non-running cards switch all three pieces to a `dim` style. A bool flag at meta-build time
  selects the style — same data path, different style strings.
