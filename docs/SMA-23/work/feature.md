# Board Viewer Themes — Default / Focus / Command

**What changed**: The Symphony web board viewer now ships with three named
themes. The existing dark graphite look is the new "Default" theme and stays
the initial appearance for anyone who has not picked another theme.

**How the user sees it**:

- A small three-button segmented control appears in the page header,
  immediately to the right of the existing UI Zoom control. The labels are
  `Default`, `Focus`, and `Command`.
- Clicking any button repaints the entire board instantly — no page reload,
  no scroll jump, polling keeps ticking, the open ticket modal stays open,
  search text and zoom value stay where they were, branch policy and
  settings dialogs are untouched.
- The active theme is highlighted with the accent color and reports
  `aria-pressed="true"` for screen readers.
- The choice is remembered in the browser (`localStorage` key
  `boardViewer.theme`). Reloading the page restores the last theme.
- If the user has never touched the switcher, no `localStorage` entry exists
  and the board renders in Default (graphite), exactly as before this
  change.

**The three themes**:

| Theme | Mood | Reference |
|-------|------|-----------|
| Default | warm graphite, indigo accent | unchanged from before |
| Focus | light off-white, calm navy accent | Jira/GitHub work surfaces |
| Command | refined near-black, magenta-violet accent | Linear-style focus dark |

The Focus and Command themes only redeclare CSS variables. The component
rules (cards, columns, modal, settings, branch controls, status dots,
priority pills, agent badges, archive/pause/resume buttons) all keep the
same shape, spacing, and typography. Only colors and shadows change.

**No knob / no flag**: there is no new keyboard shortcut, no setting in
`config.yaml`, no environment variable. The only user-visible knob is the
three buttons in the header.

**How to verify locally**:

```bash
python3 tools/board-viewer/server.py \
  --kanban ./kanban \
  --workflow ./WORKFLOW.md \
  --port 18767
# open http://127.0.0.1:18767/ and click the three theme buttons
```

**Files that changed**:

- `tools/board-viewer/index.html` — theme switcher markup in `.header-right`.
- `tools/board-viewer/src/css/style.css` — new `--code-fg` variable,
  `:root[data-theme="focus"]` and `:root[data-theme="command"]` blocks,
  `.theme-controls` / `.theme-btn` chrome rules.
- `tools/board-viewer/src/js/board.js` — `// ---- Theme ----` block,
  `applyTheme(readTheme())` + `bindThemeControls()` calls in `start()`.
- `tests/test_board_viewer.py` — new
  `test_theme_switcher_static_contract` regression.

**Rollback**: revert the single implementation commit. The `--code-fg`
variable prep is value-identical to the literal it replaces, so reverting it
alongside is safe and required only for consistency.
