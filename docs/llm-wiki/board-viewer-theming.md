# Board viewer theming — CSS variable token surface and the three-theme switcher

## Getting the Feel (For Beginners)

### Why board-viewer theming exists

The board viewer at `tools/board-viewer/` is the only browser-side surface
non-developers (PMs, designers, on-call operators) ever see for a Symphony
project. People work on this board for hours; a single fixed look is hostile
to daytime work or to people who prefer a bright UI. Theming lets the
operator pick a comfortable mood without anyone touching the data, polling,
or any of the operational controls.

The simplest way for a beginner to picture it:

`Core flow: read saved choice → set one HTML attribute → CSS variables swap → entire page repaints → click again to change`

There are five terms you need to internalise at this stage.

| Term | Plain-English meaning |
|---|---|
| Theme switcher | Three little buttons in the page header that say `Default / Focus / Command` and let you change the look without reloading. |
| CSS variable | A reusable color or size value declared once at the top of the stylesheet, then referenced everywhere — change it in one place and the whole page updates. |
| `data-theme` attribute | A label written on the page's root element (like `data-theme="focus"`) — CSS reads it to decide which palette of variables wins. |
| FOUC-safe boot | "Flash of unstyled content"-safe — apply the saved theme before the first paint so the user never sees the default flash for a second. |
| `localStorage` | A tiny browser-only key/value store; survives reloads but lives on this one browser. We use it to remember the user's theme choice. |

To make it concrete:

A PM opens the board on a sunny morning, finds the graphite default too
dark, clicks `Focus`, and the entire page flips to a clean light look —
columns, cards, header, footer, modal — instantly, with no scroll jump and
no missed poll tick. Later she reloads the tab; the page comes up already
in `Focus`, because the choice was saved.

The decision rule that matters at this stage:

**Just remember this: every reusable color lives in a single CSS variable, so swapping a `data-theme` attribute on the page root re-skins the whole board without any per-component code.**

When you're ready to go deeper, read the **Technical Reference** below or
[tui-rendering](tui-rendering.md) for the Textual-side counterpart.

## Technical Reference

**Summary:** `tools/board-viewer/src/css/style.css:9-65` defines the complete
token surface (backgrounds, foregrounds, accent, semantic, priority,
shadows, radii, code color). Every component rule in the file consumes
those variables; no component rule contains a literal color. As a result,
adding a theme is a single CSS selector block — `:root[data-theme="focus"]`
or `:root[data-theme="command"]` — that redeclares the same variables with
a new palette. The default graphite theme stays untouched under `:root`
itself, so an operator with no saved preference sees zero change. JS
mirrors the `UI Zoom` micro-pattern (storage key + `read*`/`apply*`/`set*`/
`bind*Controls` quartet, applied before `bindUi()` in `start()` to avoid
FOUC) and persists the choice in `localStorage("boardViewer.theme")`.

**Invariants & Constraints:**
- The `:root` block in `style.css` is the default theme and must stay
  visually unchanged when a new theme is added — that is the contract that
  makes the migration risk-free for existing users (AC1).
- Every component rule that uses a color must read it via `var(--...)` —
  never a literal `oklch(...)`, `rgb(...)`, or `#...`. A literal that
  slips in (e.g. the original `.modal-content code` color at the old
  `style.css:840`) breaks new themes silently and must be promoted to a
  variable as a prep step before the theme blocks are added.
- A new theme must redeclare the FULL list of override-eligible
  variables, not the subset it visually needs. The list lives in
  `docs/SMA-23/plan/implementation-plan.md` §2: `--bg`, `--bg-elev-1..3`,
  `--border`, `--border-strong`, `--fg`, `--fg-muted`, `--fg-dim`,
  `--accent`, `--accent-strong`, `--accent-soft`, `--accent-fg`, `--good`,
  `--warn`, `--danger`, `--info`, `--p1..p4`, `--shadow-card`,
  `--shadow-elev`, `--code-fg`. Partial overrides cause unpredictable
  bleed when `:root` ordering is later edited.
- `--running` derives from `--good` and must NOT be redeclared — CSS
  variable resolution evaluates `var(--good)` cascade-side, so it follows
  the active theme automatically.
- Fonts, tracking, radii, and spacing are deliberately NOT theme variables
  — they are page identity, not palette, and must remain stable across
  themes so theme switching never shifts layout.
- The theme JS must be applied BEFORE `bindUi()` in `start()`
  (`tools/board-viewer/src/js/board.js:625`) — same FOUC-safe order as
  `applyZoom(readZoom())`. Reordering reintroduces a one-frame flash of
  the default theme on every page load.
- `applyTheme()` MUST NOT touch any board state (poll, search, zoom,
  modal, branch policy, settings). It only mutates
  `document.documentElement.dataset.theme` and the three buttons'
  `aria-pressed`. This is what makes AC5 (no side effects) hold by
  construction rather than by manual review.
- A whitelist (`THEMES = ["default", "focus", "command"]`) gates both
  `readTheme` and `applyTheme` so a stale or attacker-controlled
  `localStorage` value cannot inject arbitrary `data-theme` strings.
- Active state in the switcher is driven by `aria-pressed="true"` only.
  An additional `is-active` class that no CSS selector reads is dead code
  — drop it the next time this file is edited (see Decision log).

**Files of interest:**
- `tools/board-viewer/src/css/style.css:9-65` — `:root` token surface
  (the default theme). All component rules below this point read from
  these variables.
- `tools/board-viewer/src/css/style.css:73-141` — `:root[data-theme="focus"]`
  light palette and `:root[data-theme="command"]` refined dark palette.
- `tools/board-viewer/src/css/style.css:357-402` — `.theme-controls`
  and `.theme-btn` chrome rules (segmented control look, fixed-width
  buttons so the active state never shifts the row).
- `tools/board-viewer/index.html:60-64` — the segmented-control markup
  (`role="group"`, three semantic buttons with `data-theme=` and
  `aria-pressed=`).
- `tools/board-viewer/src/js/board.js:81-130` — the
  `// ---- Theme ----` block: `THEME_STORAGE_KEY`, `THEMES`,
  `readTheme`, `applyTheme`, `setTheme`, `bindThemeControls`.
- `tools/board-viewer/src/js/board.js:625` — `applyTheme(readTheme())`
  called before `bindUi()` in `start()` (FOUC-safe).
- `tests/test_board_viewer.py::test_theme_switcher_static_contract` —
  static regression that pins the markup, JS symbols, CSS override
  blocks, and the `--code-fg` extraction.

**Decision log:**
- 2026-05-17 | SMA-23 | Introduced the three-theme switcher
  (`Default / Focus / Command`). Default left under `:root` untouched.
  Focus / Command implemented as `:root[data-theme="..."]` blocks that
  redeclare the full variable surface. Extracted the previously
  hardcoded `oklch(82% 0.13 50)` literal at old `style.css:840` into a
  new `--code-fg` variable so the modal inline-code color participates
  in theming.
- 2026-05-17 | SMA-23 | Adopted the UI Zoom micro-pattern verbatim
  (`*StorageKey` + `read*`/`apply*`/`set*`/`bind*Controls` + apply
  before `bindUi()` in `start()`) — this is now the standing template
  for any new page-level user preference on the board viewer (next
  candidate: density / compact-card switch).
- 2026-05-17 | SMA-23 | Left an `is-active` class toggle on the theme
  buttons in `board.js:106` with no matching CSS selector — Review
  flagged it LOW; aria-pressed already drives the pressed style. Drop
  the line next time this file is edited (not worth a standalone
  ticket).

**Last updated:** 2026-05-17 by SMA-23.
