# SMA-23 — Implementation details

Companion notes to the `## Implementation` body in `kanban/SMA-23.md`. The
body keeps the plain-language summary; this file holds the per-file change
log, palette rationale, and verification trace that does not fit the cap.

## Per-file change log

### `tools/board-viewer/index.html`

- Added one `.theme-controls` block immediately after the existing
  `.zoom-controls` block inside `.header-right`. Three semantic buttons:
  `data-theme="default" | "focus" | "command"`, each with `aria-pressed`,
  Korean `title` tooltip, and English label. `role="group"` +
  `aria-label="테마"` matches the zoom group convention.

### `tools/board-viewer/src/css/style.css`

- **Prep**: added `--code-fg: oklch(82% 0.13 50);` inside `:root` (the
  graphite default) and replaced the hardcoded literal at the old line
  840 (`.modal-content code { color: oklch(82% 0.13 50); }`) with
  `color: var(--code-fg);`. Visually identical for Default; gives Focus
  and Command a hook to retune the modal inline-code color.
- Added two override blocks right after `:root`:
  `:root[data-theme="focus"] { … }` (warm-cool light, calm navy accent)
  and `:root[data-theme="command"] { … }` (cool near-black, calm
  magenta-violet accent). Each block redeclares the full token surface
  the plan listed (`--bg`, `--bg-elev-1..3`, `--border`, `--border-strong`,
  `--fg`, `--fg-muted`, `--fg-dim`, `--accent`, `--accent-strong`,
  `--accent-soft`, `--accent-fg`, `--good`, `--warn`, `--danger`, `--info`,
  `--p1..p4`, `--shadow-card`, `--shadow-elev`, `--code-fg`).
- `--running` is intentionally not redeclared: it is `var(--good)` in
  `:root` and CSS variable resolution picks up each theme's `--good`
  cascade-side, not the value at declaration time.
- Font, tracking, and radii are intentionally not redeclared. These are
  "chrome" (page identity) — themes only swap the palette.
- Added a new `/* Theme controls (segmented) */` section in the chrome
  area immediately after `.zoom-value`: `.theme-controls` mirrors the
  `.zoom-controls` container (same `var(--bg-elev-2)` background, same
  border, `var(--r-sm)` radius, identical padding) so the two segmented
  groups align visually. `.theme-btn` uses fixed `padding: 4px 10px;
  min-width: 58px;` so the active state never shifts the row width.
  `.theme-btn[aria-pressed="true"]` paints `var(--accent-soft)`
  background + `var(--fg)` foreground — exactly the accent treatment the
  modal section heading already uses. `:focus-visible` reuses the
  `2px solid var(--accent-strong); outline-offset: 2px;` pattern that
  `.refresh-btn` and `.settings-btn` use.

### `tools/board-viewer/src/js/board.js`

- Added a `// ---- Theme ----` block immediately after the UI Zoom block.
  Shape mirrors zoom exactly:
  - `THEME_STORAGE_KEY = "boardViewer.theme"` (paired with the existing
    `ZOOM_STORAGE_KEY = "boardViewer.uiZoom"`).
  - `THEMES = ["default", "focus", "command"]` whitelist.
  - `readTheme()` returns one of the three names. Missing pref → falls
    back to `"default"`. localStorage exceptions (Safari private mode)
    are swallowed silently with the same comment style as zoom.
  - `applyTheme(theme)` removes `documentElement.dataset.theme` for
    Default (so `:root` wins) and sets it to `"focus"` / `"command"`
    otherwise. It also updates every `.theme-btn`'s `aria-pressed` and
    toggles an `is-active` class for future use.
  - `setTheme(theme)` = `applyTheme()` + try/catch `localStorage`
    write. Whitelist guard `THEMES.includes(theme)` makes the function
    safe against junk values.
  - `bindThemeControls()` wires every `.theme-btn` click via
    `btn.dataset.theme`.
- In `start()` the FOUC-safe order is:

  ```text
  applyZoom(readZoom());
  applyTheme(readTheme());     // ← runs before bindUi/paint
  bindZoomControls();
  bindThemeControls();
  bindUi();
  ```

  `applyTheme` is called BEFORE any other UI bind so the very first
  paint already carries the right palette. Bind happens after the DOM
  is wired, mirroring `bindZoomControls`.

### `tests/test_board_viewer.py`

- Added `test_theme_switcher_static_contract` exactly as drafted in the
  plan. It pins (a) the three `data-theme="..."` buttons in `index.html`,
  (b) the `THEME_STORAGE_KEY` literal + four function names + the
  FOUC-safe `applyTheme(readTheme())` call in `board.js`, (c) the two
  `:root[data-theme="..."]` blocks each redeclaring `--bg`, `--fg`,
  `--accent` in `style.css`, (d) the `--code-fg` extraction.

### Files deliberately NOT touched

- `tools/board-viewer/README.md` — visible control names did not
  change. Plan §2 made the README update conditional and the condition
  did not trigger. Footer keys legend in `index.html` is unchanged
  because no new keyboard shortcut was added.
- `tools/board-viewer/server.py` and any Python module — themes are a
  pure front-end change.
- `tools/board-viewer/src/js/ticket.js`, `api.js`, `settings.js`,
  `utils.js` — board card rendering, settings modal, and API client
  pick up the new palette automatically via existing `var(--…)` reads.

## Palette decisions

The two override tables in `docs/SMA-23/plan/implementation-plan.md` §4
were used verbatim. Two notes worth recording:

- **Focus accent (`oklch(48% 0.18 250)`)** lands close to Linear's "blue"
  / GitHub Primer's blue-500, but slightly less saturated to avoid
  competing with the priority pills. The `accent-soft` alpha is `0.14`
  to keep the segmented pressed state visible on the off-white background
  without forming a heavy block.
- **Command accent (`oklch(68% 0.16 295)`)** is a calm
  magenta-violet — adjacent to Linear's brand hue without copying it.
  The `accent-soft` alpha is `0.18` because dark backgrounds need more
  opacity for the pressed state to register.

## Verification trace

- `pytest tests/test_board_viewer.py::test_theme_switcher_static_contract -q`
  → first run RED (`assert 'id="theme-controls"' in html`), then GREEN
  after Step 3.
- `pytest tests/test_board_viewer.py -q` → `11 passed in 0.26s` (10
  existing + 1 new).
- `pytest -q` → `464 passed, 6 skipped in 53.36s`. No regressions.
- Smoke server boot: `python3 tools/board-viewer/server.py --kanban
  ./kanban --workflow ./WORKFLOW.md --port 18767` came up cleanly;
  `curl http://127.0.0.1:18767/` returned the new
  `.theme-controls` block; `curl …/src/css/style.css` returned both
  `:root[data-theme="..."]` blocks and three `--code-fg` declarations;
  `curl …/src/js/board.js` returned the `THEME_STORAGE_KEY` literal and
  the `applyTheme(readTheme())` FOUC-safe call.
- Browser walkthrough (every-AC + screenshots) is deliberately left to
  QA so evidence lives under `docs/SMA-23/qa/`.

## Plan adjustment log

No plan adjustment required. The plan was followed step by step:

- Step 0 (RED) ✓
- Step 1 (`--code-fg` extraction) ✓
- Step 2 (Focus + Command palette blocks) ✓
- Step 3 (theme switcher markup) ✓
- Step 4 (theme chrome CSS) ✓
- Step 5 (theme JS + `start()` wiring) ✓
- Step 6 (GREEN) ✓
- Step 7 (full suite + server smoke) ✓
