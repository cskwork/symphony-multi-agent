# Codex 0.130 `thread/resume` probe (2026-05-10)

## Question
Acceptance criterion #4 (third bullet) says:

> codex doesn't actually persist threads across `app-server` restarts as
> of writing — verify via probe before assuming

So: does codex 0.130's `app-server` JSON-RPC actually support
`thread/resume`, and do rollouts persist on disk?

## Method
Spawned `codex app-server` (codex-cli **0.130.0**) and sent four probes
on stdin:

1. `initialize` — handshake.
2. `thread/resume {threadId: "00000000-0000-0000-0000-000000000000"}`
   — known-bogus uuid.
3. `threadResume {...}` — wrong shape (sanity-check the schema enum).
4. `thread/start {cwd: "/tmp", resumeFromThreadId: "00000000-..."}` —
   alternate-path attempt.

## Findings

| Probe | Response | Interpretation |
|-------|----------|----------------|
| `thread/resume` | `error -32600: "no rollout found for thread id 00000000-..."` | Method exists, schema accepted, fails only because the bogus UUID has no rollout file. |
| `threadResume` | `error -32600: Invalid request: unknown variant 'threadResume'`, with the **full method enum dumped** | Enum confirms `thread/resume` is one of ~80+ supported methods. |
| `thread/start` w/ `resumeFromThreadId` | New thread id (`019e102f-...`), success | Not a resume — this is the documented **fork** behavior. |

Rollout files persist at:
```
~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<thread_id>.jsonl
```

Verified with `ls ~/.codex/sessions/2026/05/10/`:
```
rollout-2026-05-10T09-22-20-019e0f43-...jsonl
rollout-2026-05-10T09-58-04-019e0f64-...jsonl
...
rollout-2026-05-10T13-40-19-019e102f-a78f-75d1-8b7b-15c581fc1743.jsonl
```
The last one is the thread the probe just minted — file lives outside
the workspace, so it survives `workspace_manager.remove(...)` of the
issue's workspace dir.

## Conclusion

**The hedge in acceptance #4 is outdated for codex 0.130.** Symphony
SHOULD attempt `thread/resume` when `resume_session_id` is set, and
fall back to `thread/start` only on the specific
`-32600 "no rollout found"` error path.

Implementation note for `_codex.py`:
- New helper `_try_resume(thread_id)` that calls `thread/resume` and
  catches `ResponseError` whose `.message` matches `"no rollout found"`
  (substring match — full message includes the uuid).
- On rollout-missing fallback: emit `session_resume_unsupported` event
  with reason `rollout_missing`, then call existing `start_session`
  flow.
- On other RPC errors: re-raise (genuine failure, not "fall back").

## Caveat
- Codex's rollout file lives in `~/.codex/sessions/`. If the operator
  blows away `~/.codex` (or moves to a new machine), `thread/resume`
  will fail and we'll start fresh — which matches the
  out-of-scope statement on cross-machine migration. ✅
- The probe's `thread/resume` response is just an error in this
  instance; we never observed the success shape. The implementation
  must be defensive about the shape of the success result and not
  assume any specific fields beyond `thread.id`.
