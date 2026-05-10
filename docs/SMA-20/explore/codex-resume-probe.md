# Codex 0.130 thread/resume — probe results (2026-05-10)

PRD acceptance #4 third bullet: "verify via probe before assuming" whether
codex 0.130 supports resuming a thread across `app-server` restarts.
Probed against `codex-cli 0.130.0` on macOS arm64.

## Probe steps

1. `codex app-server` spawned via stdio JSON-RPC.
2. `initialize` → succeeds, returns `userAgent`, `codexHome`, etc.
3. `thread/resume { threadId: "fake-uuid-123" }` → server validates UUID
   format and returns:
   ```
   {"error":{"code":-32600,
     "message":"invalid thread id: invalid character: expected an optional
       prefix of `urn:uuid:` followed by [0-9a-fA-F-], found `k` at 3"}}
   ```
   So the **method exists** and validates input.
4. `thread/start { cwd: ... }` → mints a fresh UUIDv7 thread id, e.g.
   `019e102c-f836-7a91-be7b-7b7348a98399`. `result.thread.id` is the canonical id.
5. Immediately calling `thread/resume` on that fresh id (before any
   `turn/start`) → returns:
   ```
   {"error":{"code":-32600,
     "message":"no rollout found for thread id 019e102c-..."}}
   ```
6. Pointing `thread/resume` at a **persisted** rollout from earlier today
   (`~/.codex/sessions/2026/05/10/rollout-2026-05-10T13-23-18-019e1020-14fa-7f22-9920-b3b85cc791f0.jsonl`)
   → succeeds, returns full Thread object with `forkedFromId: null`,
   `preview: ...`, `status: {type: "idle"}`.

## Conclusion

Codex 0.130 **DOES** support `thread/resume` across app-server restarts,
**provided** the rollout file exists on disk under
`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread-uuid>.jsonl`.
Rollouts are written by codex itself once a turn has produced output —
not at `thread/start` time.

## Implications for symphony

- A symphony crash *after* `thread/start` but *before* the first
  `turn/start` completes will leave a session id with no on-disk rollout.
  `thread/resume` on the next dispatch will return `-32600 no rollout
  found`. This must be handled gracefully.
- The simplest mitigation: catch the resume error from codex, log
  `session_resume_unsupported` (PRD's chosen log line) with the actual
  reason, then fall back to `thread/start`. Cache savings are lost in
  that edge case but the dispatch still succeeds.
- For pi/claude/gemini the rollout/file dependency does not exist — the
  CLI simply re-creates session state from its own on-disk store
  (`~/.pi/agent/sessions/...`, `~/.claude/sessions/...`). Resume is
  unconditional.

## Code shape

In `CodexAppServerBackend.start_session`, when `init.resume_session_id`
is set:

```python
if self._resume_session_id is not None:
    try:
        result = await self._request("thread/resume", {"threadId": self._resume_session_id})
        # parse thread.id same as thread/start result
        ...
        return self._thread_id
    except ResponseError as exc:
        log.warning("session_resume_unsupported",
                    reason=str(exc), thread_id=self._resume_session_id)
        # fall through to fresh thread/start
# original thread/start path
```

`ResponseError` already wraps JSON-RPC error responses (`backends/codex.py`
existing pattern around `_request`). We don't need new error types.

## What we are NOT verifying

- Whether codex auto-deletes old rollouts (it doesn't appear to — rollout
  files from January 2026 still present on this machine). Operators with
  their own retention policy may delete rollouts; resume will fail and
  fall back. Acceptable.
- Whether `thread/resume` carries the prior turn token usage back into
  the new app-server's `thread/tokenUsage/updated` notifications. Not
  needed for this ticket — token totals are owned by the
  `_apply_token_totals` accumulator on RunningEntry, which we're
  pre-populating from the persisted file (acceptance #3).
