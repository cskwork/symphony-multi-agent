# SMA-23 — reuse inventory

What existing primitives the implementer can lift directly, with anchors.

## JS — copy the zoom pattern

| Concern                | Existing                                                                | New (theme)                                                                                  |
|------------------------|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Storage key constant   | `ZOOM_STORAGE_KEY = "boardViewer.uiZoom"` (`board.js:23`)               | `THEME_STORAGE_KEY = "boardViewer.theme"`                                                    |
| Read helper            | `readZoom()` (`board.js:29-38`) with try/catch around localStorage      | `readTheme()` returning `"default" | "focus" | "command"`, falling back to `"default"` on any error |
| Apply helper           | `applyZoom(z)` writes `--ui-zoom` CSS var + updates DOM (`board.js:44`) | `applyTheme(theme)` toggles `document.documentElement.dataset.theme` + updates `aria-pressed` on buttons |
| Setter                 | `setZoom(z)` calls apply + persists (`board.js:54-62`)                  | `setTheme(theme)` calls apply + persists                                                     |
| Binder                 | `bindZoomControls()` (`board.js:71-78`)                                 | `bindThemeControls()` wires three buttons → `setTheme(btn.dataset.theme)`                    |
| Boot order             | `start()` calls `applyZoom(readZoom())` before paint (`board.js:578`)   | Same — `applyTheme(readTheme())` first, then `bindThemeControls()` after DOM bind            |

## HTML — copy the zoom-controls block

`index.html:54-59` is the template:

```
<div class="zoom-controls" role="group" aria-label="UI 크기 조절">
  <button id="zoom-out" class="zoom-btn" ...>−</button>
  <span id="zoom-value" ...>100%</span>
  <button id="zoom-in"  class="zoom-btn" ...>+</button>
  <button id="zoom-reset" class="zoom-btn zoom-reset" ...>⟲</button>
</div>
```

`.theme-controls` will use the same surface: a `role="group"` container with three `<button>` children, each carrying `data-theme="default|focus|command"` and `aria-pressed`.

## CSS — variable-only override

All component selectors already read from `var(--*)`. Two new block scopes:

- `:root[data-theme="focus"] { /* light palette */ }`
- `:root[data-theme="command"] { /* refined dark palette */ }`

Two strategic exceptions to plan for:
- `style.css:840` — inline code color literal (extract to var or duplicate per theme).
- `.theme-controls` and `.theme-btn` style block must be added near `.zoom-controls` (line 311) so the chrome shares visual language.

## Tests — copy the static-string pattern

`tests/test_board_viewer.py` already covers HTML / JS / CSS files with plain `.read_text() + assert "..." in text` checks. The new test:

- asserts theme switcher markup in `index.html`,
- asserts theme helper symbols in `board.js`,
- asserts `:root[data-theme="focus"]` and `:root[data-theme="command"]` in `style.css`,
- asserts localStorage key string and default fallback name.
