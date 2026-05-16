# SMA-23 — Implementation Plan (detail)

This is the full plan referenced from `## Plan` in `kanban/SMA-23.md`.
Read it whole before editing code. The plan deliberately mirrors the
existing UI Zoom pattern so the next agent can copy proven shapes
instead of inventing new ones.

## 1. Chosen approach (Candidate A — variable-only override)

Keep `:root` in `tools/board-viewer/src/css/style.css` untouched as the
default ("Default" theme). Add two new selector blocks:

- `:root[data-theme="focus"] { /* light palette */ }`
- `:root[data-theme="command"] { /* refined dark palette */ }`

A new `THEME_STORAGE_KEY = "boardViewer.theme"` persists the user's
choice. JS toggles `document.documentElement.dataset.theme`. AC1 is
auto-satisfied: when the user has no saved preference, no `data-theme`
attribute is set, so `:root` (graphite default) wins. The data flow is
identical to the zoom feature, including FOUC-safe boot order.

### Why this wins (vs Candidate B / C)

- **B (body-class duplication)** rewrites every component rule per
  theme — high diff, drift-prone, breaks "lean" rule.
- **C (hybrid with per-theme tweaks)** is only justified if A produces a
  visibly inferior result in a specific component. We will inspect
  during In Progress; default to A; only adopt selected per-theme
  tweaks if the screenshot says so.
- **A** keeps the diff localized to: 1 HTML block, 1 JS block, 2 CSS
  selector blocks + 1 prep extraction + 1 chrome rule block, plus one
  new test.

## 2. File ownership and write scope

| File | Write scope |
|------|-------------|
| `tools/board-viewer/index.html` | Add one `.theme-controls` block inside `.header-right` near `.zoom-controls` (after line 59). Three buttons, each `data-theme="default|focus|command"`, `aria-pressed`. Also append one segment to the footer keys legend if a `t` shortcut is added (see §6 — default: no shortcut). |
| `tools/board-viewer/src/js/board.js` | Add the `// ---- Theme ----` block after the UI Zoom block (after line 78). Add `applyTheme(readTheme())` + `bindThemeControls()` calls inside `start()` (line 576-583), before `bindUi()`. |
| `tools/board-viewer/src/css/style.css` | (a) Add `--code-fg: oklch(82% 0.13 50);` to `:root`; replace literal at line 840 with `var(--code-fg)`. (b) Add `:root[data-theme="focus"] { … }` and `:root[data-theme="command"] { … }` blocks after the existing `:root` (around line 65). (c) Add `.theme-controls` + `.theme-btn` rules in the chrome section near `.zoom-controls` (after line 347). |
| `tools/board-viewer/README.md` | If — and only if — visible control names change (e.g., a `t` shortcut is added or label wording differs from "Default / Focus / Command"), add one line under the existing keyboard/UI docs. Default plan: no README change. |
| `tests/test_board_viewer.py` | Add `test_theme_switcher_static_contract` — string-level assertions across all three front-end files, mirroring the existing zoom/branch-policy tests. |

No other files. No Python changes. No `server.py` changes.

## 3. Ordered implementation steps

Stop after each step and confirm the named verification before moving on.

### Step 0 — Branch + first failing test (RED)

1. Confirm you are on the ticket worktree branch `symphony/SMA-23` and
   `git status` is clean.
2. Append `test_theme_switcher_static_contract` to
   `tests/test_board_viewer.py` (full body in §5).
3. Run `pytest tests/test_board_viewer.py::test_theme_switcher_static_contract -q`.
4. **Stop condition**: test fails with assertion errors (the HTML/JS/CSS
   strings are not present yet). Do NOT proceed until you see RED.

### Step 1 — CSS variable prep (independent, low risk)

1. In `tools/board-viewer/src/css/style.css`, inside the `:root` block
   (between lines 9-65), add `  --code-fg: oklch(82% 0.13 50);` in the
   Text or Type-related section.
2. At line 840, replace `color: oklch(82% 0.13 50);` with
   `color: var(--code-fg);`.
3. **Stop condition**: open the board viewer in a browser, open a ticket
   modal that contains inline code — color must look identical to before
   (it is the same literal, just promoted to a variable).

### Step 2 — Focus and Command palette blocks (CSS)

1. Immediately after the closing `}` of `:root` (line 65), add two new
   blocks. Use OKLCH values from §4 of this document. Each block must
   redeclare at minimum these variables so the override stays complete
   even if `:root` order changes later:
   - `--bg`, `--bg-elev-1`, `--bg-elev-2`, `--bg-elev-3`
   - `--border`, `--border-strong`
   - `--fg`, `--fg-muted`, `--fg-dim`
   - `--accent`, `--accent-strong`, `--accent-soft`, `--accent-fg`
   - `--good`, `--warn`, `--danger`, `--info`
   - `--p1`, `--p2`, `--p3`, `--p4`
   - `--shadow-card`, `--shadow-elev`
   - `--code-fg`
2. `--running` does not need to be redeclared (it derives from `--good`).
3. Font, tracking, radii are deliberately NOT redeclared — they are
   shared chrome and must remain visually stable across themes.
4. **Stop condition**: in DevTools, manually run
   `document.documentElement.dataset.theme = "focus"`; entire board
   should flip to the light palette without layout shift. Repeat with
   `"command"` and `""` (default).

### Step 3 — Theme switcher HTML

1. In `tools/board-viewer/index.html`, immediately after the closing
   `</div>` of `.zoom-controls` (after line 59), add:

   ```html
   <div class="theme-controls" id="theme-controls" role="group" aria-label="테마">
     <button type="button" class="theme-btn" data-theme="default" aria-pressed="true" title="Default 테마">Default</button>
     <button type="button" class="theme-btn" data-theme="focus"   aria-pressed="false" title="Focus 테마 (밝은)">Focus</button>
     <button type="button" class="theme-btn" data-theme="command" aria-pressed="false" title="Command 테마 (집중 다크)">Command</button>
   </div>
   ```

2. **Stop condition**: reload the viewer — three buttons appear in the
   header next to the zoom controls. Clicking them does nothing yet
   (binding in Step 5).

### Step 4 — Theme chrome CSS

1. After `.zoom-value { … }` (around line 347), add a `.theme-controls`
   / `.theme-btn` block. Constraints:
   - Match `.zoom-controls` container style (same `background:
     var(--bg-elev-2)`, same `border`, `border-radius`, `padding`).
   - `.theme-btn` width must be stable regardless of selected state
     (use fixed `padding`, do not change `font-weight` on pressed) so
     switching does not shift layout.
   - `.theme-btn[aria-pressed="true"]` uses `background:
     var(--accent-soft); color: var(--fg);`.
   - `.theme-btn:focus-visible` reuses the existing `outline: 2px solid
     var(--accent-strong); outline-offset: 2px;` pattern.
2. **Stop condition**: header chrome stays the same height; the three
   buttons read clearly in all three themes after Step 5 wires the
   state.

### Step 5 — Theme JS

1. After the UI Zoom block in `board.js` (after line 78), add:

   ```js
   // ---- Theme ------------------------------------------------------------
   const THEME_STORAGE_KEY = "boardViewer.theme";
   const THEMES = ["default", "focus", "command"];
   const THEME_DEFAULT = "default";

   function readTheme() {
     try {
       const raw = localStorage.getItem(THEME_STORAGE_KEY);
       if (raw && THEMES.includes(raw)) return raw;
     } catch {
       /* localStorage 차단 환경 — 기본값 사용 */
     }
     return THEME_DEFAULT;
   }

   function applyTheme(theme) {
     const next = THEMES.includes(theme) ? theme : THEME_DEFAULT;
     if (next === THEME_DEFAULT) {
       delete document.documentElement.dataset.theme;
     } else {
       document.documentElement.dataset.theme = next;
     }
     document.querySelectorAll(".theme-btn").forEach((btn) => {
       const pressed = btn.dataset.theme === next;
       btn.setAttribute("aria-pressed", pressed ? "true" : "false");
       btn.classList.toggle("is-active", pressed);
     });
   }

   function setTheme(theme) {
     const next = THEMES.includes(theme) ? theme : THEME_DEFAULT;
     applyTheme(next);
     try {
       localStorage.setItem(THEME_STORAGE_KEY, next);
     } catch {
       /* persistence 실패는 무시 — UI는 정상 동작 */
     }
   }

   function bindThemeControls() {
     document.querySelectorAll(".theme-btn").forEach((btn) => {
       btn.addEventListener("click", () => setTheme(btn.dataset.theme));
     });
   }
   ```

2. In `start()` (lines 576-583), apply BEFORE `bindUi()`, mirroring
   zoom:

   ```js
   async function start() {
     applyZoom(readZoom());
     applyTheme(readTheme());     // ← FOUC-safe: must run before paint
     bindZoomControls();
     bindThemeControls();          // ← after DOM is bound
     bindUi();
     bindShortcuts();
     schedulePoll(0);
   }
   ```

3. **Stop condition**: in the browser, click each of the three
   buttons — board flips palette immediately; reload — last choice
   persists; `aria-pressed` reflects the active theme.

### Step 6 — Make the new test pass (GREEN)

1. Re-run `pytest tests/test_board_viewer.py::test_theme_switcher_static_contract -q`.
2. **Stop condition**: test passes.

### Step 7 — Full suite + smoke

1. `pytest tests/test_board_viewer.py -q` — entire file must remain
   green (regression check on the existing 10 tests).
2. `pytest -q` — full suite green.
3. `python3 tools/board-viewer/server.py --kanban ./kanban --workflow ./WORKFLOW.md --port 18767`
   and walk every AC (see §6).

## 4. Palette specifications

Both palettes are restrained and avoid one-note gradient aesthetics.
Lightness values follow the existing convention (chroma drops near
0/100).

### Focus (light, Jira/GitHub-inspired)

| Variable | Value | Notes |
|----------|-------|-------|
| `--bg` | `oklch(98% 0.005 250)` | warm-cool off-white |
| `--bg-elev-1` | `oklch(96% 0.006 250)` | column background |
| `--bg-elev-2` | `oklch(94% 0.007 250)` | card background |
| `--bg-elev-3` | `oklch(91% 0.008 250)` | hover, pressed |
| `--border` | `oklch(87% 0.010 250)` | hairlines |
| `--border-strong` | `oklch(78% 0.012 250)` | strong borders |
| `--fg` | `oklch(22% 0.012 260)` | near-black ink |
| `--fg-muted` | `oklch(42% 0.012 260)` | secondary text |
| `--fg-dim` | `oklch(55% 0.012 260)` | tertiary text |
| `--accent` | `oklch(48% 0.18 250)` | calm navy-blue |
| `--accent-strong` | `oklch(42% 0.20 250)` | hover/pressed |
| `--accent-soft` | `oklch(48% 0.18 250 / 0.14)` | soft tint |
| `--accent-fg` | `oklch(99% 0.003 250)` | text on accent |
| `--good` | `oklch(52% 0.16 150)` | green |
| `--warn` | `oklch(60% 0.16 80)` | amber |
| `--danger` | `oklch(50% 0.20 25)` | red |
| `--info` | `oklch(50% 0.14 230)` | blue |
| `--p1` | `oklch(50% 0.20 25)` | p1 mirrors danger |
| `--p2` | `oklch(60% 0.16 80)` | p2 amber |
| `--p3` | `oklch(50% 0.14 230)` | p3 blue |
| `--p4` | `oklch(55% 0.012 260)` | p4 gray |
| `--shadow-card` | `0 1px 0 oklch(100% 0 0 / 0.6), 0 1px 2px oklch(0% 0 0 / 0.08)` | very soft |
| `--shadow-elev` | `0 12px 32px oklch(0% 0 0 / 0.12), 0 2px 6px oklch(0% 0 0 / 0.08)` | modal elevation |
| `--code-fg` | `oklch(42% 0.18 25)` | terracotta on light |

### Command (refined dark, Linear-inspired)

| Variable | Value | Notes |
|----------|-------|-------|
| `--bg` | `oklch(14% 0.008 280)` | near-black, cool tint |
| `--bg-elev-1` | `oklch(17% 0.009 280)` | column |
| `--bg-elev-2` | `oklch(20% 0.010 280)` | card |
| `--bg-elev-3` | `oklch(23% 0.011 280)` | hover, pressed |
| `--border` | `oklch(28% 0.012 280)` | hairlines |
| `--border-strong` | `oklch(38% 0.014 280)` | strong borders |
| `--fg` | `oklch(94% 0.012 280)` | soft white |
| `--fg-muted` | `oklch(72% 0.010 280)` | secondary |
| `--fg-dim` | `oklch(56% 0.010 280)` | tertiary |
| `--accent` | `oklch(68% 0.16 295)` | calm magenta-violet |
| `--accent-strong` | `oklch(74% 0.18 295)` | hover/pressed |
| `--accent-soft` | `oklch(68% 0.16 295 / 0.18)` | soft tint |
| `--accent-fg` | `oklch(98% 0.005 295)` | text on accent |
| `--good` | `oklch(72% 0.16 150)` | green |
| `--warn` | `oklch(78% 0.14 80)` | amber |
| `--danger` | `oklch(70% 0.20 25)` | red |
| `--info` | `oklch(74% 0.14 230)` | blue |
| `--p1` | `oklch(70% 0.20 25)` | mirrors danger |
| `--p2` | `oklch(78% 0.14 80)` | amber |
| `--p3` | `oklch(74% 0.14 230)` | blue |
| `--p4` | `oklch(62% 0.012 280)` | gray |
| `--shadow-card` | `0 1px 0 oklch(100% 0 0 / 0.04), 0 1px 2px oklch(0% 0 0 / 0.4)` | crisp |
| `--shadow-elev` | `0 12px 32px oklch(0% 0 0 / 0.55), 0 2px 6px oklch(0% 0 0 / 0.45)` | modal |
| `--code-fg` | `oklch(80% 0.15 50)` | terracotta on dark |

These values can be tuned during Step 2 in DevTools if a specific
component looks off; the table is a starting point with measured
contrast against the current component rules, not a frozen contract.

## 5. First failing test (full body)

Append to `tests/test_board_viewer.py`:

```python
def test_theme_switcher_static_contract() -> None:
    html = Path("tools/board-viewer/index.html").read_text(encoding="utf-8")
    js = Path("tools/board-viewer/src/js/board.js").read_text(encoding="utf-8")
    css = Path("tools/board-viewer/src/css/style.css").read_text(encoding="utf-8")

    # 1) Theme switcher markup
    assert 'id="theme-controls"' in html
    assert 'data-theme="default"' in html
    assert 'data-theme="focus"' in html
    assert 'data-theme="command"' in html

    # 2) Theme JS contract
    assert 'THEME_STORAGE_KEY = "boardViewer.theme"' in js
    assert "function readTheme(" in js
    assert "function applyTheme(" in js
    assert "function setTheme(" in js
    assert "function bindThemeControls(" in js
    assert "applyTheme(readTheme())" in js

    # 3) CSS theme overrides re-declare the key variables
    for selector in ('[data-theme="focus"]', '[data-theme="command"]'):
        assert f":root{selector}" in css, f"missing block: :root{selector}"
    focus_block = css.split(':root[data-theme="focus"]', 1)[1].split("}", 1)[0]
    cmd_block = css.split(':root[data-theme="command"]', 1)[1].split("}", 1)[0]
    for var in ("--bg", "--fg", "--accent"):
        assert var in focus_block, f"focus block missing {var}"
        assert var in cmd_block, f"command block missing {var}"

    # 4) Prep: --code-fg variable replaces hardcoded literal at modal code style
    assert "--code-fg:" in css
    assert "color: var(--code-fg);" in css
```

If this test passes before any implementation, something is wrong —
either you skipped Step 0 RED or you're reading the wrong files.

## 6. Verification commands and required evidence

```bash
# 1. Targeted test first
pytest tests/test_board_viewer.py::test_theme_switcher_static_contract -q

# 2. Full board viewer file
pytest tests/test_board_viewer.py -q

# 3. Full suite (catch any unrelated regressions from the worktree)
pytest -q

# 4. Smoke server (uses a non-product port, won't collide with running
#    Symphony boards)
python3 tools/board-viewer/server.py \
  --kanban ./kanban \
  --workflow ./WORKFLOW.md \
  --port 18767
```

Then open `http://127.0.0.1:18767/` and walk through the AC checklist:

- **AC1**: clear `localStorage.boardViewer.theme` in DevTools → reload →
  graphite default appears.
- **AC2**: header shows `Default / Focus / Command` segmented control.
- **AC3**: click each theme → palette flips immediately, no reload.
- **AC4**: pick Focus → reload → still Focus. Repeat with Command and
  Default.
- **AC5**: while polling, switch themes mid-poll → no missed tick;
  search text, zoom value, open modal, branch policy selects, settings
  modal are all untouched (manually verify each).
- **AC6**: every chrome element listed in AC6 reads cleanly in all
  three themes (record one screenshot per theme).
- **AC7**: shrink browser to ~360px wide → header wraps; theme controls
  fit and do not overlap zoom/search.
- **AC8**: the new test + existing 10 tests all green.
- **AC9**: save the three screenshots + a short Markdown QA note at
  `docs/SMA-23/qa/notes.md`.

## 7. Acceptance criteria mapping

| AC | Covered by |
|----|------------|
| AC1 default theme initial | Step 5 (`readTheme()` falls back to `default` on miss) + `:root` left untouched in Step 2 |
| AC2 switcher present | Step 3 markup |
| AC3 immediate switch | Step 5 `applyTheme` updates `dataset.theme` synchronously |
| AC4 persistence | Step 5 `setTheme` writes localStorage; `start()` restores |
| AC5 no side effects | Plan touches only theme code; no polling, search, modal, branch, settings code is modified |
| AC6 contrast across components | Step 2 palette tables; Step 4 chrome rules |
| AC7 responsive | `.theme-controls` joins existing `flex-wrap` chain on `.app-header` |
| AC8 tests | Step 6 + existing test file remains green |
| AC9 browser evidence | Step 7 manual smoke + `docs/SMA-23/qa/notes.md` |

## 8. User-visible behavior (the contract)

- Default page load (no saved pref) → graphite, exactly as today.
- Header shows three small pill buttons: `Default | Focus | Command`,
  with the active one highlighted via `aria-pressed="true"` + accent
  background.
- Click changes palette instantly. No layout reflow, no scroll jump,
  no polling interruption.
- Reload restores last choice.
- Keyboard shortcut: NONE in the default plan (the ticket does not
  require one). If In Progress adds a `t` shortcut by mistake, drop it
  and update the README. Adding a shortcut would also require updating
  the footer keys legend (`index.html:66`).

## 9. Rollback / risk

- Rollback: a single revert of the implementation commit restores
  graphite-only behavior. The CSS prep extraction in Step 1 is
  semantically identical to the old literal, so it is safe to keep
  on revert (no rollback needed for Step 1 alone).
- Risk 1: a Focus-theme contrast issue on one specific component (e.g.
  running badge). Mitigation: every variable is redeclared in each
  block, so the In Progress agent can tweak in DevTools and copy back.
- Risk 2: `data-theme` attribute conflict with a future feature.
  Mitigation: namespace is reserved by ticket; no other code reads
  `documentElement.dataset.theme` today (grep confirms).
- Risk 3: Firefox `zoom:` quirk — orthogonal; pre-existing; not a
  theme concern.

## 10. Out of scope (explicit)

- Auto theme follow-the-OS (`prefers-color-scheme`) — not in AC; defer
  to a follow-up ticket.
- A "compact / dense" layout switch — different feature.
- Theme editor / custom palette — different feature.
- Any change to `server.py`, polling, or backend models.
- Translating control labels — labels stay in English / Korean tooltips,
  matching existing chrome.
