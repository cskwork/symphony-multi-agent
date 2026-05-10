# Troubleshooting

## Step 1 — run preflight

```bash
symphony doctor ./WORKFLOW.md
```

Catches the most common first-run failures: port collision, missing agent
CLI on `$PATH`, the shipped placeholder clone URL, unwritable workspace,
missing board directory. Exit 0 if green.

## Step 2 — read structured logs

Symphony writes to **stderr only** by default
(`src/symphony/logging.py:48`). Capture it:

```bash
mkdir -p log
symphony tui ./WORKFLOW.md 2>> log/symphony.log
tail -F log/symphony.log
```

## Common failure signatures

| Log line                                 | Meaning                                                  | Fix                                                                                 |
|------------------------------------------|----------------------------------------------------------|-------------------------------------------------------------------------------------|
| `hook_failed hook=after_create rc=128`   | First-time clone failed                                  | Replace placeholder repo URL in `WORKFLOW.md`, or set `after_create: \|\n  : noop`. If the hook parses ticket frontmatter, see "awk vs sed for YAML values" below |
| `worker_exit reason=error`               | Worker terminated abnormally                             | Read the preceding `hook_failed` event or backend stderr for the actual cause       |
| `outcome=turn_error`                     | Turn ended in error (timeout, agent crash, tool failure) | Inspect backend stderr; for timeouts, raise `<kind>.turn_timeout_ms`                |
| `hook_timeout`                           | Hook exceeded its time budget                            | Shorten the hook or remove blocking commands                                        |
| `hook_after_run_skipped_missing_cwd`     | Workspace was deleted before `after_run` could run       | INFO-level only; usually means the agent or a hook removed its own workspace. Not a bug — ignore unless you depend on `after_run`                                                                 |
| `OSError [Errno 48]` on startup          | Port already in use                                      | `lsof -ti :9999 \| xargs -r kill`                                                   |
| `workflow_path_missing`                  | `WORKFLOW.md` not at the path you passed                 | Pass an explicit path; default is `./WORKFLOW.md`                                   |
| `dispatch_validation_failed`             | Config invalid for the chosen `agent.kind`               | Check the matching `<kind>:` block in `WORKFLOW.md` (command, timeouts)             |
| TUI exits immediately, no error          | No TTY (running under a non-interactive shell)           | Run from a real terminal, or use `--port 9999` headless mode                        |

## Step 3 — inspect runtime state

When triaging, the JSON snapshot is the source of truth:

```bash
curl -s http://127.0.0.1:9999/api/v1/state | jq '.workers'
curl -s http://127.0.0.1:9999/api/v1/<ID>  | jq
```

Useful fields:
- `.running[]` — currently active workers with last_event, tokens, turn_count
- `.retrying[]` — tickets in the retry queue with their last error
- `.counts` — quick header counts
- per-issue: `.recent_events[]` with timestamped event stream

## Common standalone issues

### "TUI started but the screen is blank"

Check whether you launched it under a multiplexer (tmux/screen) — `rich`
sometimes mis-detects color support. Try `TERM=xterm-256color`
`COLORTERM=truecolor symphony tui ./WORKFLOW.md`.

### "Worker keeps retrying"

After hitting `max_turns`, the orchestrator marks the worker for retry.
The retry queue uses exponential backoff capped at `agent.max_retry_backoff_ms`.
If the underlying issue is unfixable (e.g. agent CLI is broken), `mv` the
ticket to `Blocked` to stop the cycle.

### "Card is in `Done` but workspace still exists"

By design — workspaces persist for inspection. Manually clean up with
`rm -rf ~/symphony_workspaces/<ID>` once you've extracted what you need.

### "Two TUI sessions show different state"

Both read from the JSON snapshot via the same orchestrator instance, but
*you can only have one orchestrator process per board at a time*. Stop one
before starting another, otherwise both will fight for hook locks.

### "after_create exits 128 every retry, same error each time"

If your hook parses ticket frontmatter to compute a clone URL, the
single most common cause is an `awk -F':'` field separator that splits
on every colon. A value like `repo: https://github.com/owner/repo.git`
becomes `$2 = "https"`, which `git clone` rejects with rc=128. The
retries fail identically because the parse is deterministic.

```bash
# Wrong — splits on every colon:
REPO=$(awk -F': *' '/^repo:/ {print $2; exit}' "$TICKET")

# Right — captures everything after the first `: `:
REPO=$(sed -n 's/^repo: *\(.*\)$/\1/p' "$TICKET" | head -1 | tr -d '\r"')
```

See `reference/workflow-config.md` ("Hook authoring rules") for the
full list of related gotchas (CRLF, absolute paths, `set -e`, etc.).

### Linear-tracker specific: "tickets aren't appearing"

- `LINEAR_API_KEY` set?
- `tracker.project_slug` matches the Linear project's URL slug?
- Linear API rate-limited you? Check `rate_limits` in
  `/api/v1/state` — it's mirrored from upstream headers.

### Pi backend: "first turn fails immediately, no useful error"

Most common cause: `~/.pi/agent/auth.json` is missing or stale. Without it,
`pi --mode json` exits before emitting any events; Symphony surfaces this
as a generic `turn_error` and retries with the same identical failure.

```bash
symphony doctor ./WORKFLOW.md             # WARNs when the auth file is absent
ls -la ~/.pi/agent/auth.json              # confirm presence + recency
pi                                        # then `/login` to refresh OAuth credentials
```

The cached credentials are inherited automatically by every subprocess
Symphony spawns — you do *not* need to put `PI_API_KEY` (or any provider
env var) into `WORKFLOW.md` or the `pi:` block.

### Pi backend: "agent silent for N seconds, no events in log"

If `agent_session_started` fired but no `agent_turn_completed` follows for
5+ minutes, pi may be stuck on a long tool call (e.g. a slow fetch, or an
agent-spawned subprocess waiting on stdin). The per-turn budget is
`pi.turn_timeout_ms` (default ~1h). Lower it if you want stuck turns to
fail fast and bounce through retry instead of hanging:

```yaml
pi:
  command: 'pi --mode json -p ""'
  resume_across_turns: true
  turn_timeout_ms: 600000        # 10 min instead of the default 1h
```

### Pi backend: "token totals look 2–4× higher than Claude on the same task"

Not a bug. Pi makes one LLM API call per assistant message within a turn,
and each call's prompt is re-billed (cache reads count at a discount but
still count). A 4-call turn with a 30k-token system prompt reports ~120k
`input_tokens`; the same work on Claude reports ~30k because Claude
aggregates per-turn at the `result` event. Both totals are honest — they
just count at different granularities. Verify with the JSON probe:

```bash
echo "<prompt>" | pi --mode json -p "" 2>/dev/null \
  | jq -c 'select(.type=="message_end") | .message.usage'
# one usage block per LLM call
```
