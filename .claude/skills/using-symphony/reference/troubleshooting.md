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
| `hook_failed hook=after_create rc=128`   | First-time clone failed                                  | Replace placeholder repo URL in `WORKFLOW.md`, or set `after_create: \|\n  : noop`   |
| `worker_exit reason=error`               | Worker terminated abnormally                             | Read the preceding `hook_failed` event or backend stderr for the actual cause       |
| `outcome=turn_error`                     | Turn ended in error (timeout, agent crash, tool failure) | Inspect backend stderr; for timeouts, raise `<kind>.turn_timeout_ms`                |
| `hook_timeout`                           | Hook exceeded its time budget                            | Shorten the hook or remove blocking commands                                        |
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

### Linear-tracker specific: "tickets aren't appearing"

- `LINEAR_API_KEY` set?
- `tracker.project_slug` matches the Linear project's URL slug?
- Linear API rate-limited you? Check `rate_limits` in
  `/api/v1/state` — it's mirrored from upstream headers.
