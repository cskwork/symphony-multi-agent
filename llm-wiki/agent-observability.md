# Agent observability — what fires, when, and where to look

Headless symphony runs (no TUI, just `symphony ./WORKFLOW.md --port 9999`)
emit a structured stderr stream. The fields below are the minimum signal
needed to know whether a run is genuinely progressing vs. silently stuck.
Codified here because pre-2026-05 runs had a 90-second log gap between
`hook_completed before_run` and `worker_exit` — events were tracked on
the `RunningEntry` but never logged, so headless operators had to poll the
JSON API to see anything happen.

## The minimum log line set per run

Run grep-friendly anchors:

```bash
grep -E '(dispatch|agent_session|agent_turn|reconcile_terminate|worker_exit)' \
  log/symphony.log
```

Expected sequence for a healthy 1-turn run:

```
INFO dispatch                        issue_id=X identifier=X attempt=null
INFO hook_start hook=after_create    cwd=~/symphony_workspaces/X
INFO hook_completed                  hook=after_create
INFO hook_start hook=before_run      cwd=~/symphony_workspaces/X
INFO hook_completed                  hook=before_run
INFO agent_session_started           issue_id=X session_id=<uuid>
INFO agent_turn_completed            turn=1 input_tokens=N output_tokens=M total_tokens=N+M last_message=…
INFO reconcile_terminate_terminal    state=Done
INFO hook_start hook=after_run       cwd=~/symphony_workspaces/X
INFO hook_completed                  hook=after_run
INFO worker_exit                     reason=normal error=null
```

Multi-turn runs repeat `agent_turn_completed` with `turn=N` incrementing.
Failures replace `agent_turn_completed` with `WARN agent_turn_failed turn=N reason=...`.

## Stall signatures

| Symptom in log                                    | Likely cause                                           | First action                                       |
|---------------------------------------------------|--------------------------------------------------------|----------------------------------------------------|
| `dispatch` then nothing for >60s                  | Agent CLI not booting (auth missing, wrong path)       | `symphony doctor ./WORKFLOW.md`                    |
| `agent_session_started` then nothing for >5min    | Agent stuck in a tool call (long fetch, hung subprocess) | Lower `<kind>.turn_timeout_ms` or kill the child   |
| repeated `agent_turn_failed reason=…`             | Real backend / prompt error, not an env issue          | Read the `reason=` value; inspect backend stderr   |
| `hook_after_run_skipped_missing_cwd`              | Agent or hook removed its own workspace before exit    | Cosmetic; ignore unless `after_run` is load-bearing |

## Why this matters for the prompt template

The pipeline prompt in `WORKFLOW.example.md` tells the agent to append a
`## Resolution` (or `## Triage`, `## Implementation`) section as it
transitions states. Those sections are the *human* trail. The events
above are the *operator* trail — they confirm the orchestrator + backend
plumbing is moving even when the agent's text output is sparse.

If you're authoring a new prompt template, do **not** ask the agent to
emit log lines that mimic these — they're orchestrator-side and will
never be honoured. Ask for ticket-body sections instead, and rely on
`agent_turn_completed.last_message` for the live preview snippet.

## Cross-references

- `src/symphony/orchestrator.py:_on_codex_event` — where the events are logged
- `src/symphony/doctor.py:check_pi_auth` — preflight for the `agent.kind=pi` auth file
- `src/symphony/workspace.py:after_run_best_effort` — the missing-cwd skip
- `tests/test_doctor.py` and `tests/test_workspace.py` — coverage anchors
