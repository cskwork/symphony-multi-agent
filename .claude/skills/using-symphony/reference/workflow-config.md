# Authoring `WORKFLOW.md`

`WORKFLOW.md` is a **hybrid file**:
- **YAML frontmatter** = orchestrator config (tracker, hooks, agent, etc.)
- **Body** = strict-Liquid prompt template injected as the agent's system
  prompt per turn — `{{ issue.identifier }}`, `{{ issue.description }}`,
  `{% if attempt %}…{% endif %}`, etc.

When editing, distinguish the two halves.

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
  kind: claude          # codex | claude | gemini
  max_concurrent_agents: 4
  max_turns: 20

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
  turn_timeout_ms: 3600000

server:
  port: 9999
---

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

## Hooks

Each hook is a shell script that runs in the workspace directory:

| Hook            | When                                | Common use                          |
|-----------------|-------------------------------------|-------------------------------------|
| `after_create`  | once, when workspace is first created | clone the repo the agent works in |
| `before_run`    | before every turn                   | `git fetch` to pull latest main     |
| `after_run`     | after every turn                    | log markers, push branches          |
| `before_remove` | before workspace cleanup            | persist artifacts                   |

**Failure mode**: if `after_create` exits non-zero, the worker dies
immediately with `worker_exit reason=error`. The shipped sample
(`WORKFLOW.example.md` / `WORKFLOW.file.example.md`) uses a placeholder
`git clone git@github.com:my-org/my-repo.git .` — that fails out of the
box. Replace with `: noop` for experiments or with a real clone for actual
work. `symphony doctor` catches this.

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
