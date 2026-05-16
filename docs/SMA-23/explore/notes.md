# SMA-23 Explore — research notes

## Code anchors

- `tools/board-viewer/index.html:54-59` — existing `.zoom-controls` segmented control sits inside `.header-right`; new `.theme-controls` should mirror this position and structure (semantic buttons, role="group", aria-label).
- `tools/board-viewer/src/css/style.css:9-65` — `:root` declares the full variable surface (backgrounds, fg, accent, semantic, priority, shadows, radii). All component rules consume these vars, so `:root[data-theme="focus"]` / `:root[data-theme="command"]` overrides flow through automatically.
- `tools/board-viewer/src/css/style.css:840` — `.modal-content code { color: oklch(82% 0.13 50); }` is a hardcoded literal that bypasses the variable system. Focus (light theme) would show muddy orange on light gray. Decide between (a) extracting to `--code-fg`, or (b) accepting per-theme literal in each theme block.
- `tools/board-viewer/src/css/style.css:643` — `.flash-toast { background: color-mix(in oklch, var(--danger) 92%, black 8%); }` derives from `--danger` so light theme would auto-darken a vivid red; acceptable.
- `tools/board-viewer/src/js/board.js:22-78` — UI Zoom block is the proven template: `ZOOM_STORAGE_KEY`, `readZoom`, `clampZoom`, `applyZoom`, `setZoom`, `nudgeZoom`, `bindZoomControls`. Theme persistence will copy the shape (`THEME_STORAGE_KEY`, `readTheme`, `applyTheme`, `setTheme`, `bindThemeControls`).
- `tools/board-viewer/src/js/board.js:576-583` — `start()` calls `applyZoom(readZoom())` BEFORE `bindUi()` to avoid FOUC. Theme restore must do the same (apply before first paint).
- `tools/board-viewer/src/js/board.js:558-562` — refresh button click pattern (`withButtonLock` + `await poll()`) confirms header buttons do not trigger card-level handlers, so a sibling `.theme-controls` block won't interfere.

## Existing test patterns

- `tests/test_board_viewer.py:21-27` — `test_header_has_branch_policy_slot` reads `index.html` text and asserts presence of well-known IDs. Same shape for `id="theme-controls"` plus three `data-theme="default|focus|command"` buttons.
- `tests/test_board_viewer.py:52-62` — `test_board_viewer_fallback_states_and_policy_mode` reads `board.js` and asserts string-level symbol presence (`fetchGitBranches`, `saveBranchPolicy`, etc.). Same shape for `THEME_STORAGE_KEY`, `applyTheme(`, `setTheme(`, `bindThemeControls(`.
- No CSS-level regression test exists yet for the chrome; we add one asserting `:root[data-theme="focus"]` and `:root[data-theme="command"]` are present and that key variables (`--bg`, `--fg`, `--accent`) get redeclared in both.

## Mobile / responsive context

- `style.css:1164-1190` (`@media (max-width: 640px)`) — `.zoom-controls { display: none; }` on mobile because pinch-zoom replaces the buttons. Theme switcher is qualitatively different (no native gesture equivalent), so it stays visible on mobile but should be permitted to wrap.
- `style.css:1140-1162` — `.app-header` already uses `flex-wrap: wrap`; new `.theme-controls` joins the existing wrap chain.

## Design references — what's transferable

- Jira light: warm-gray surfaces (#F4F5F7-ish), navy-blue accent, clear black on white. Translate to OKLCH: `--bg: oklch(98% 0.005 250)`, `--fg: oklch(22% 0.012 260)`, `--accent: oklch(48% 0.18 250)`. Light, calm, no shadow drama.
- Linear dark: near-black with cool tint, soft white text, magenta/violet accent at low chroma. Translate: `--bg: oklch(14% 0.008 280)`, `--fg: oklch(94% 0.012 280)`, `--accent: oklch(68% 0.16 295)` (calmer than today's indigo).
- Both keep cards compact and rely on tonal elevation, not heavy borders — already matches current Symphony layout.

## What we do NOT change

- Card layout, spacing, radii, font system (sans + mono split), running-badge animation, modal structure.
- Polling interval, branch controls, settings modal, keyboard shortcuts, search box, ticket detail markdown rendering.
- Any Python (`server.py`) — themes are pure front-end concern.

## Open decisions for Plan stage

1. Extract hardcoded `oklch(82% 0.13 50)` (modal inline code) into `--code-fg` variable? Recommended yes — otherwise Focus theme inline code looks off.
2. Add a `t` keyboard shortcut to cycle themes? Ticket suggests README update if visible control names change but does not require a shortcut. Default: NO shortcut, button-only. Defer to Plan.
3. Should `.theme-controls` hide on mobile like `.zoom-controls`? Default: visible (themes are a stable preference; mobile users still want to set it once).
