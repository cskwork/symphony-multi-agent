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
INFO dispatch                          issue_id=X identifier=X attempt=null
INFO hook_start hook=after_create      cwd=~/symphony_workspaces/X
INFO hook_completed                    hook=after_create
INFO hook_start hook=before_run        cwd=~/symphony_workspaces/X
INFO hook_completed                    hook=before_run
INFO agent_session_started             issue_id=X session_id=<uuid>
INFO reconcile_skip_active_worker      state=Done last_event_age_s=3.1   # if the agent just emitted
INFO agent_turn_completed              turn=1 input_tokens=N cache_input_tokens=K output_tokens=M total_tokens=N+K+M last_message=…
INFO worker_turn_completed             turn=1 input_tokens=N cache_input_tokens=K …  # worker-side mirror, fires even under cancellation
INFO hook_start hook=after_run         cwd=~/symphony_workspaces/X
INFO hook_completed                    hook=after_run
INFO worker_exit                       reason=normal error=null
```

Multi-turn runs repeat `agent_turn_completed` (and `worker_turn_completed`)
with `turn=N` incrementing. Failures replace those with `WARN
agent_turn_failed turn=N reason=... stderr_tail=[...]`.

Claude Code reports prompt-cache reads/creates separately from fresh prompt
input. Symphony exposes that as `cache_input_tokens` in logs and
`/api/v1/state` so a large cached context does not look like fresh input burn;
`total_tokens` still includes fresh input + cache input + output.

Pi-only events that may interleave at any point:

- `agent_compaction phase=start` / `phase=end tokens_before=N` — context
  compaction triggered (manual or auto-threshold)
- `agent_internal_retry phase=start attempt=K max_attempts=N` /
  `phase=end success=true|false` — backend-internal retry on a transient
  upstream error (network, ratelimit)

## Stall signatures

| Symptom in log                                    | Likely cause                                           | First action                                       |
|---------------------------------------------------|--------------------------------------------------------|----------------------------------------------------|
| `dispatch` then nothing for >60s                  | Agent CLI not booting (auth missing, wrong path)       | `symphony doctor ./WORKFLOW.md`                    |
| `WARN stalled_session elapsed_ms>=stall_timeout_ms` | No real model progress within `<kind>.stall_timeout_ms` (default 5min). Detected via `last_progress_timestamp`, which advances only on lifecycle events, token deltas, or `EVENT_OTHER_MESSAGE` whose payload is from the assistant role — claude API tool_result echoes / stream keepalive do not reset the clock. | Reconcile will cancel the worker on this tick and force-eject after 30 s grace if cancel doesn't land — let it. Inspect `last_codex_message` in `/api/v1/state` to see the model's last visible output |
| `ERROR stalled_worker_force_ejected elapsed_since_cancel_s>30` | Cancel fired 30+ s ago but the worker never returned (parked on a non-cancellable await). Slot was freed and ticket re-queued for retry. | No action needed unless this repeats for the same ticket — then check `agent.kind` backend's subprocess-wait code (see `project_symphony_async_subprocess_helper.md`) |
| repeated `agent_turn_failed reason=… stderr_tail=[...]` | Real backend error                                | Read `stderr_tail` array — it's the literal stderr from pi/claude/gemini |
| `hook_after_run_skipped_missing_cwd`              | Agent or hook removed its own workspace before exit    | Cosmetic; ignore unless `after_run` is load-bearing |
| `reconcile_terminate_terminal last_event_age_s>10` | Worker stuck — reconcile force-cancelled it          | Inspect the agent CLI's last activity; raise turn_timeout_ms if legitimate work was in progress |

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

- `src/symphony/orchestrator.py:_on_codex_event` — where listener-side events are logged
- `src/symphony/orchestrator.py:_run_worker` — where `worker_turn_completed` fires (race-tolerant)
- `src/symphony/orchestrator.py:_reconcile` — grace-period skip for active workers
- `src/symphony/backends/pi.py` — stderr ring buffer, compaction/retry event mapping
- `src/symphony/backends/claude_code.py` — same stderr ring buffer pattern
- `src/symphony/doctor.py:check_pi_auth` — preflight for the `agent.kind=pi` auth file
- `src/symphony/workspace.py:after_run_best_effort` — the missing-cwd skip
- `src/symphony/tui.py` — `_silent_seconds`, `SILENT_THRESHOLD_S` for the silence badge
- `tests/test_doctor.py`, `tests/test_workspace.py`, `tests/test_backends.py`, `tests/test_workflow.py`, `tests/test_tui.py` — coverage anchors
