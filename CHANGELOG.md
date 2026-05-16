# Changelog

All notable changes to symphony-multi-agent are documented in this file.
Full release notes (with verification steps and per-commit detail) live on
the [GitHub Releases page](https://github.com/cskwork/symphony-multi-agent/releases);
this file is the in-repo summary.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `agent.auto_merge_capture_untracked` (default `[]`, **opt-in**) — list
  of host-repo paths whose currently-untracked files are folded into the
  same auto-merge commit on Done. Closes the gap where
  `hooks.after_create` installs host directories as **symlinks inside
  the agent workspace**: the agent writes files via the symlink (so
  they land in the host repo's real directories), but the
  `symphony/<ID>` branch only sees the symlink as a single blob, so the
  branch diff never reports the agent's per-ticket notes (e.g.
  `docs/<ID>/*`). Listing those host paths here lets auto-merge
  `git add` them alongside the branch-side checkout, producing one
  cohesive commit. Distinct from `auto_merge_exclude_paths` (which
  controls what is *skipped from branch-side checkout*); capture is
  *additive on the host side*. Default empty so existing deployments
  are unchanged.

## [0.4.2] — 2026-05-16

Builtin auto-merge on Done, board-viewer launcher integration, and a
StreamReader limit fix that unblocks long agent turns. Drop-in over
0.4.1; auto-merge is ON by default but safe-by-default (dirty host or
missing branch skips silently).

### Added
- `agent.auto_merge_on_done` (default **true**) — when a ticket reaches
  Done, fold the `symphony/<ID>` branch into the host repo's
  development branch as one selective-apply commit. Paths in
  `agent.auto_merge_exclude_paths` (default
  `kanban/llm-wiki/prompt/docs`) are stripped first so the workspace
  symlinks that `hooks.after_create` installs never reach the host
  repo. `agent.auto_merge_target_branch` defaults to `""` = the host
  repo's currently-checked-out branch.
- `tui-open.sh` now auto-starts `tools/board-viewer/server.py` in the
  background at `http://127.0.0.1:8765/` when the workflow ships one.
  Skipped silently if the port is held or the file is absent.
- `src/symphony/auto_merge.py` — new module owning the selective-apply
  flow with five outcome events: `auto_merge_completed`,
  `auto_merge_skipped_dirty`, `auto_merge_skipped_missing_branch`,
  `auto_merge_nothing_to_apply`, `auto_merge_failed`.
- Learn-stage wiki entries now open with a **beginner explainer block**
  (`## 감 잡기` in Korean, `## Getting the Feel` in English) ahead of
  the existing Summary / Invariants / Files / Decision-log technical
  reference. Same file, two layered audiences — PMs and 기획자 land on
  the beginner block, engineers scroll past it. The prompt enforces the
  tutor shape: 3-5 step core flow, exactly five plain-language terms,
  one realistic scenario, one-sentence takeaway, and a "ready to go
  deeper" pointer. Branch is `{% if language == 'ko' %}` keyed on the
  same `{{ language }}` env that drives the chrome/doc directive, so
  switching the TUI language (or `SYMPHONY_LANG`) flips both halves of
  the wiki entry in lockstep. Both `docs/symphony-prompts/file/stages/learn.md`
  and `docs/symphony-prompts/linear/stages/learn.md` updated.

### Fixed
- `claude_code`, `pi`, `gemini` backends now spawn their subprocess with
  `limit=10 MiB` on the asyncio `StreamReader`, matching `codex`. The
  asyncio default of 64 KiB was raising `LimitOverrunError` on
  stream-json events whose `result`/tool-use payload exceeded that on a
  single line, dropping the rest of the turn into `stalled_session`
  recovery. Caught on a live IB-002 turn before the fix landed.
- `_on_worker_exit(reason="normal")` at Done now fires
  `auto_merge_on_done`, `after_done` user hook, and `workspace.remove`
  inline. Previously those only ran on the reconcile-driven termination
  path; a worker that finished cleanly at Done was popped from
  `_running` *before* the next reconcile cycle, so the entire
  terminal-state post-processing was silently skipped — the host repo
  never saw the auto-merge commit and the workspace lingered.
  `_reconcile_running` remains the safety net for stale workers.

### Tests
- `tests/test_auto_merge.py` — five scenarios: happy path,
  dirty host, missing branch, implicit current-branch target,
  all-paths-excluded. Full suite: **353 passed, 6 skipped**.

## [0.4.1] — 2026-05-16

Browser HUD for headless operators, plus i18n cleanup for the
prompt-base templates. No breaking changes — drop-in over 0.4.0.

### Added
- `tools/board-viewer/` — vanilla HTML/CSS/JS + Python-stdlib browser
  HUD for Symphony kanban boards. Read-only, runs alongside the
  headless orchestrator and the textual TUI without conflict. Two
  modes: **live** proxies `/api/v1/state` every 5s (setTimeout-recursive
  polling avoids overlapping cycles); **file-only** scans `kanban/*.md`
  when Symphony is down.
- `tools/board-viewer/board-viewer-open.sh` — launcher with kanban
  auto-discovery (`$CWD/kanban` → env → CLI flag) and python3.11+
  selection.
- Progress mirror (`WORKFLOW-PROGRESS.md`) now advertises a clickable
  board-viewer URL header. Defaults to `http://127.0.0.1:8765/`
  (board-viewer-open.sh default port); override with the
  `SYMPHONY_BOARD_URL` env var; disable with `SYMPHONY_BOARD_URL=""`.

### Changed
- `docs/symphony-prompts/{file,linear}/base.md` now branch the
  "Audience & writing style" block on `{{ language }}`. English operators
  (the default) see a `**What** / **Why** / **As-Is → To-Be**` Plain-language
  header; Korean operators (`tui.language: ko`, `SYMPHONY_LANG=ko`, or `L`
  hotkey) keep the existing `**무엇** / **왜** / **As-Is → To-Be**` block.
  The doc-language preamble and the prompt body are now consistent under
  both defaults.

### Docs
- Add `CHANGELOG.md` mirroring GitHub Releases through v0.4.0.
- `llm-wiki/agent-observability.md`: drop the dated "fixed in 0.3.3"
  parenthetical from the stall-signature table — the behavior is now
  baseline, not historical.

### Security
- Board viewer sanitizes ticket markdown via DOMPurify before insertion
  (kanban .md is agent-authored, prompt-injection surface). `<script>`,
  `<iframe>`, `on*=` handlers are explicitly forbidden.
- Path-traversal defense on both static and kanban routes in
  `tools/board-viewer/server.py`.

## [0.4.0] — 2026-05-16

First release with day-one Windows support, a lifecycle hook surface, and
a per-ticket git workspace model. The 7-stage workflow becomes the
supported default; headless runs leave a human-readable progress trail.

### Added
- Cross-platform Windows support: dispatch pipeline, hooks, and host-board
  sync via directory junction + `claude --add-dir`.
- `after_done` lifecycle hook plus `qa.boot` and `qa.regression_budget`
  config keys, all surfaced in `WORKFLOW.md`.
- Per-ticket workspaces default to a git worktree of the host repo,
  with a one-commit-per-ticket guarantee on the issue branch.
- `WORKFLOW-PROGRESS.md` mirror so headless runs can be tailed without
  attaching a TUI.
- `docs/skills/` cross-platform compatibility reference.

### Changed
- 7-stage prompts (Todo / Explore / In Progress / Review / QA / Learn /
  Done) become the supported default.
- Review now rewinds to In Progress on MEDIUM findings, not just
  HIGH/CRITICAL.
- Operator skills (`using-symphony`, `symphony-oneshot`) resolve from
  any working directory.

### Fixed
- Windows hook execution and test isolation.
- `claude` backend: success-result parsing; continuation turn budget cap.
- Hook failure output surfaced instead of swallowed.
- Operator pause (Shift+P) persists across worker exits.
- Auto-commit and `basesha` scoped to the workspace.

## [0.3.4] — 2026-05-11

Turns the ticket-order rule from an implementation detail into an
operator-visible contract.

### Added
- Shift+P TUI hotkey to pause/resume the focused running worker.
- Stage-specific prompt loading from `docs/symphony-prompts/{tracker}/stages/*.md`.

### Fixed
- Dispatch sorts candidates by stable ticket registration suffix with
  `created_at` fallback, so newer or higher-priority work cannot jump
  ahead of earlier tickets in single-slot workflows.
- Hydrates blocker state from current ticket files so stale `blocked_by`
  metadata cannot let dependent work outrun its blocker.

## [0.3.3] — 2026-05-11

Safer long-running workflows: phase isolation, stricter retry/slot
handling, clearer TUI state, stronger stall detection.

### Added
- Workspace snapshot at Done: `agent.auto_commit_on_done` (default `true`)
  produces a single commit named `<identifier>: <title>`.
- Review → In Progress rewind for CRITICAL/HIGH review findings, parallel
  to the existing QA → In Progress failure loop.
- `is_rewind` prompt context so agents can distinguish a workflow rewind
  from a normal retry.

### Changed
- Rebuilds the agent backend on each phase transition so stages do not
  silently inherit prior conversation context.

### Fixed
- Worker cleanup races that could leak a running slot or let a stale
  done callback eject a live replacement worker.
- Stall timer no longer reset by claude API tool-result echoes or
  keepalive-style events; only real model progress advances the clock.
- Retry-pending tickets count against capacity, preventing a sibling
  ticket from starting during the continuation retry delay window.
- macOS/Textual child-process hangs reduced via a safer process-wait
  helper.

## [0.3.0] — 2026-05-10 — TUI quality of life + auto-archive

First release after the Textual TUI rewrite.

### Added
- Textual rewrite of the Kanban board: real focus, modals, mouse handling.
- Dense defaults: compact one-line cards, lane pagination (`t`/`T`),
  always-on detail pane.
- `L` hotkey: toggle TUI chrome language (en ↔ ko) without restart.
- `a` hotkey: archive the focused terminal-state card.
- `[` / `]` hotkey: park focus inside the detail pane.
- Auto-archive sweep: terminal-state tickets older than
  `tracker.archive_after_days` (default 30, `0` disables) move to
  `tracker.archive_state` on each poll tick. Works on Linear and file
  trackers.
- `TrackerClient.update_state(issue, target_state)` — first mutation
  method on the tracker protocol.
- Doctor: pi-auth preflight check.
- Plain-Korean header policy with stage-specific length caps (overflow
  → `docs/<id>/<stage>/details.md`).

### Changed
- File-tracker workspaces symlink `kanban/` `docs/` `llm-wiki/` back to
  the host so agent edits land in the right place.
- `SYMPHONY_WORKFLOW_DIR` env var injected into hooks so cloned workspaces
  can resolve back to the host repo.

## [0.1.0] — 2026-05-09 — symphony-multi-agent

First public release of the multi-agent fork.

### Added
- Four agent backends behind one Protocol: `agent.kind: codex | claude |
  gemini | pi`.
- Seven-stage production pipeline baked into the default prompt: Todo →
  Explore → In Progress → Review → QA → Learn → Done.
- CLI Kanban TUI on `rich`: live status indicators, per-stage column
  descriptions, per-card token breakdown, EN/KO chrome via `SYMPHONY_LANG`.
- File-based tracker — no Linear or external board required.
- Mock backend (`python -m symphony.mock_codex`) for zero-install demos.
- Per-state concurrency caps, `$VAR`/`~` expansion, dynamic WORKFLOW
  reload, structured stderr logging, `symphony doctor`.

[Unreleased]: https://github.com/cskwork/symphony-multi-agent/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.4.1
[0.4.0]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.4.0
[0.3.4]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.3.4
[0.3.3]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.3.3
[0.3.0]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.3.0
[0.1.0]: https://github.com/cskwork/symphony-multi-agent/releases/tag/v0.1.0
