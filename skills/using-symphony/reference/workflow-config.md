# Authoring `WORKFLOW.md`

`WORKFLOW.md` is a **hybrid file**:
- **YAML frontmatter** = orchestrator config (tracker, hooks, agent, etc.)
- **Prompt source** = either external files declared under `prompts:` or,
  if no matching prompt file is configured, the strict-Liquid body after
  the frontmatter.

When editing, distinguish runtime config from agent instructions. New
workflows should prefer `prompts.base` + `prompts.stages`; the body is the
legacy fallback.

## Minimal template

```markdown
---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Cancelled, Blocked]

workspace:
  root: ~/symphony_workspaces

hooks:
  after_create: |
    : noop
  before_run: |
    : noop
  after_run: |
    echo "run finished at $(date)"

agent:
  kind: claude          # codex | claude | gemini | pi
  max_concurrent_agents: 4
  max_turns: 20

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

server:
  port: 9999

prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Done: ./docs/symphony-prompts/file/stages/done.md
---

This body is only used when `prompts:` is removed or no matching stage
prompt exists.

You are picking up ticket {{ issue.identifier }}: {{ issue.title }}.
…
```

## Picking the agent

Set `agent.kind`:
- **`codex`** — Codex `app-server`. Best for multi-turn JSON-RPC sessions; most mature backend. Long-running stdio JSON-RPC connection; one process for the whole run.
- **`claude`** — Claude Code. Fresh subprocess per turn with `--resume <session-id>` from turn 2 onward. NDJSON event stream.
- **`gemini`** — Gemini CLI. One-shot per turn, no session continuity (each turn is independent).
- **`pi`** — Pi coding-agent (`pi --mode json -p ""`). Per-turn subprocess with `--session <id>` resume from turn 2 onward; JSONL events. Multiplexes Anthropic / OpenAI / Gemini / Bedrock backends behind one CLI — useful when you want to swap LLM providers without changing Symphony config. Auth: sign in once with `pi` → `/login` (OAuth); credentials cached at `~/.pi/agent/auth.json` and inherited by every subprocess Symphony spawns. `symphony doctor` warns if the auth file is missing.

Each backend reads its own block (`codex`, `claude`, `gemini`, `pi`); the
others are ignored. The `codex.linear_graphql` client tool is only
advertised when `agent.kind: codex`.

## Prompt files

Preferred current shape:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    Explore: ./docs/symphony-prompts/file/stages/explore.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Review: ./docs/symphony-prompts/file/stages/review.md
    QA: ./docs/symphony-prompts/file/stages/qa.md
    Learn: ./docs/symphony-prompts/file/stages/learn.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

At dispatch, Symphony sends `base` plus only the file matching the ticket's
current `state`. Paths are resolved relative to `WORKFLOW.md`; `$VAR`
values are expanded through the usual config indirection. Stage keys are
matched case-insensitively after trimming whitespace.

Missing prompt files fail config validation with `prompt file not found`.
If `prompts:` is absent, or a state has no configured stage file, Symphony
falls back to the inline body below the frontmatter. Keep that body as a
short legacy fallback, not as the primary place for all stage rules.

## Hooks

Each hook is a shell script that runs in the workspace directory:

| Hook            | When                                | Common use                          |
|-----------------|-------------------------------------|-------------------------------------|
| `after_create`  | once, when workspace is first created | attach a `git worktree` of the host repo on a `symphony/<ID>` branch + record fork-point (`git config symphony.basesha <HEAD>`) for the end-of-run squash |
| `before_run`    | before every turn                   | `git fetch` to pull latest main — **never `git reset --hard`**; that would discard the agent's mid-run work |
| `after_run`     | after every turn                    | per-turn **commit-or-amend**: first turn creates `wip: turn …`, every subsequent turn `--amend --no-edit`s into it. Branch stays at one commit but each turn is durably written to `.git/objects` |
| `before_remove` | before workspace cleanup            | `git worktree remove --force` to drop the registration before Symphony rmtree's the dir |

### Default workspace mechanism: `git worktree`

`WORKFLOW.example.md` and `WORKFLOW.file.example.md` ship with
`after_create` hooks that attach the per-ticket workspace as a **git
worktree** of `$SYMPHONY_WORKFLOW_DIR` on a fresh `symphony/<ID>`
branch. The host's working tree is never modified, and the operator
merges results back explicitly with `git -C <host> merge symphony/<ID>`
(or by opening a PR from that branch) — never automatic. Worktrees
share the host's object DB so setup is near-instant compared to a
full clone, and the branch is immediately visible to host-side `git`
commands.

The matching `before_remove` hook runs `git worktree remove --force`
so cleanup also drops the `.git/worktrees/<ID>` registration; without
it the registration lingers until `git worktree prune`.

If your code lives in a *different* remote than the WORKFLOW.md repo
(common for Linear setups where the config repo is config-only), swap
the worktree commands for `git clone <remote> .`. While experimenting
without any repo, use `: noop`.

### One commit per ticket (`agent.auto_commit_on_done`)

`agent.auto_commit_on_done: true` (the default) gives you a
**one-commit-per-ticket guarantee** on the `symphony/<ID>` branch:

1. `after_create` records the fork-point (`git config symphony.basesha
   $(git rev-parse HEAD)`).
2. `after_run` commits-or-amends every turn into a single `wip: turn …`
   commit on the branch — durable across hard crashes (SIGKILL, host
   reboot) because each turn's tree lands in `.git/objects`.
3. On any clean worker exit (Done, Cancelled, Blocked) and on every
   cleanup path (`reconcile_terminate_terminal`,
   `_startup_terminal_cleanup`), the orchestrator's
   `commit_workspace_on_done`:
   - reads `symphony.basesha`
   - `git reset --soft <basesha>` to collapse the wip commit + any
     agent-authored commits into the index
   - `git commit -m "<ID>: <title>[ <suffix>]"` — single ticket commit

Net result: `git log symphony/<ID>` shows exactly **base + 1 commit**.
Operator merges with `git -C <host> merge --ff-only symphony/<ID>` (or
opens a PR) and the host history stays clean.

Commit-message convention:

| Exit shape                         | Subject                                            |
|------------------------------------|----------------------------------------------------|
| Reached `Done`, normal worker exit | `<ID>: <title>`                                    |
| Other terminal state (Cancelled)   | `<ID>: <title> [state: Cancelled]`                 |
| Force-pruned by reconcile/startup  | `<ID>: <title> [state: <state>]` (or `[exit: …]`) |

**Mid-run hard-crash recovery**: even if Symphony itself is SIGKILLed
before any cleanup runs, the per-turn amend leaves the branch at the
last completed turn's wip commit (durably in `.git/refs/heads/symphony/
<ID>` and `.git/objects`). Operator can rename it manually with
`git -C <host> commit --amend -m "<ID>: <title>"` then merge.

**Pre-commit hook compatibility**: the per-turn commit and the final
squash commit both honour the host repo's pre-commit hooks. If a hook
fails, the auto-commit fails and the operator sees the rejection in
`log/symphony.log` — work is still on disk in the worktree, no silent
loss.

**Opting out**: set `auto_commit_on_done: false` only when the
workspace is an existing repo with strict commit-style rules you don't
want auto-touched. Then you own snapshotting and squashing yourself in
`before_remove`. Recovery from a discarded worktree without it is
`git fsck --lost-found` plus luck.

**Failure mode**: if `after_create` exits non-zero, the worker dies
immediately with `worker_exit reason=error`. The shipped sample's
worktree hook fails when `$SYMPHONY_WORKFLOW_DIR` is not a git repo
or when `symphony/<ID>` is already checked out in another worktree;
a clone-mode override using a placeholder
`git@github.com:my-org/my-repo.git` URL fails the same way.
`symphony doctor` catches the placeholder case.

### Hook authoring rules

Hooks run via `bash -lc` in the workspace directory and inherit only
`os.environ.copy()` — no ticket-specific env vars are injected
(`workspace.py:155-167`). The ticket ID is recoverable as
`basename "$PWD"`; everything else must be parsed out of
`<board_root>/<ID>.md` by hand. Six rules to avoid the most common
self-inflicted hook failures:

1. **Never parse YAML frontmatter with `awk -F':'`.** It splits on every
   colon, so a value like `repo: https://github.com/owner/repo.git`
   becomes `$2 = "https"` — `git clone https .` then fails with rc=128
   and the worker bounces through retries with the same error. Capture
   everything after the first `: ` instead:
   ```bash
   # Wrong:
   REPO=$(awk -F': *' '/^repo:/ {print $2; exit}' "$TICKET")

   # Right:
   REPO=$(sed -n 's/^repo: *\(.*\)$/\1/p' "$TICKET" | head -1 | tr -d '\r"')
   ```
2. **Verify the hook body in isolation before launching the TUI.** Drop
   the `after_create` body into `/tmp/sym-verify-<ID>` and run it
   manually. Symphony cleans up failed workspaces aggressively, which
   makes post-mortem hard.
3. **`set -e` at the top of every non-trivial hook.** Without it, a
   failed `git clone` happily continues into `git checkout` and the
   error trail becomes ambiguous.
4. **Use absolute paths inside hook bodies.** `./kanban/$ID.md` is
   resolved against the workspace cwd, not the WORKFLOW.md directory —
   so it points at a file that doesn't exist. Hardcode the absolute
   board path:
   ```bash
   BOARD="/abs/path/to/kanban"
   TICKET="$BOARD/$(basename "$PWD").md"
   ```
5. **Strip CR and quotes after parsing.** `tr -d '\r"'` on every
   sed-extracted value. Frontmatter authored on Windows is CRLF, and
   string values often pick up surrounding quotes that break shell
   substitution.
6. **`after_create` runs once but is not idempotent.** Symphony removes
   the workspace on hook failure before retrying, so from-scratch is
   safe — but a partial-success scenario (e.g. clone OK, checkout fail)
   leaves you debugging in the workspace before symphony wipes it.
   Defensive checks (`[ -d .git ] && exit 0` early, `git rev-parse
   HEAD` to validate clone, etc.) are worth the lines.

## Tracker

Two kinds:

```yaml
# File-backed (no external deps):
tracker:
  kind: file
  board_root: ./kanban

# Linear-backed:
tracker:
  kind: linear
  project_slug: my-team-project
  api_key: $LINEAR_API_KEY      # $VAR expands from environment
```

`active_states` are columns the orchestrator polls and dispatches from.
`terminal_states` are end columns (the orchestrator stops watching once a
ticket lands here).

### Column legends (`tracker.state_descriptions`)

Optional. Maps a state name to a one-line description that the TUI
renders under each column header. Useful when lanes encode workflow
semantics (Triage / Fix / Self-review / Deploy) that aren't obvious
from the lane name alone.

```yaml
tracker:
  active_states: [Todo, "In Progress", Review]
  terminal_states: [Done, Cancelled, Blocked]
  state_descriptions:
    Todo: "Triage: read PR + decide next action"
    "In Progress": "Apply fix locally, run tests"
    Review: "Self-review the diff before Done"
    Cancelled: "Junk / stale / agent-rejected"
```

Keys are matched case-insensitively. Empty strings and non-string
values are dropped. Omit the field entirely to keep the original
column-name-only header.

## Workspace + concurrency

```yaml
workspace:
  root: ~/symphony_workspaces       # one subdirectory per ticket created here

agent:
  max_concurrent_agents: 4
  max_concurrent_agents_by_state:    # optional per-lane throttle
    Todo: 2
    "In Progress": 4
    "Deploy Ready": 1                # never deploy two things at once
```

## Optional HTTP API

```yaml
server:
  port: 9999    # omit to disable; --port on CLI overrides
```

When unset, the orchestrator runs without an HTTP server and you can only
observe via stderr logs and the TUI.

## TUI display tweaks

```yaml
tui:
  language: en               # `en` (default) or `ko`. Aliases like `Korean` /
                             # `ko-KR` also resolve. Unknown → English.
```

### `language`

Only TUI chrome (column placeholder, header/footer field labels, card
meta verbs `turn` / `retry #` / `blocked by`) is localized. Tracker
state names, ticket titles, and `tracker.state_descriptions` come from
user data and stay as authored.

Per-key fallback to English is silent — adding a new locale never
crashes the TUI on missing keys, but a missing translation surfaces as
the literal key string (e.g. `card.turn`) so the gap is obvious in the
running board.

The `SYMPHONY_LANG` environment variable overrides `tui.language` for the
session, so a single operator can flip locale without committing a
config change others would inherit:

```bash
SYMPHONY_LANG=ko symphony tui ./WORKFLOW.md
```

Per-lane scrolling, mouse wheel, focus traversal, and ticket-detail
modals are handled by the Textual framework — there is nothing to
configure for them.
