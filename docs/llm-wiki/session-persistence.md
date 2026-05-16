# Session-id persistence across symphony restarts

When symphony crashes or is killed mid-ticket, the next dispatch must hand
the **same** session id back to the backend CLI. Otherwise:

1. The agent CLI loses prior conversation context.
2. Prompt cache is rebuilt (Claude / Pi pay full cost on the next turn).
3. Token totals on the JSON snapshot reset to zero, hiding real spend.

This is real money on long-running tickets that flow through the eight-stage
pipeline across days. SMA-20 (round 4) added the persistence layer.

## On-disk shape

One JSON file per workspace, atomic-write:

```
<workspace.root>/<workspace_key(id)>/.symphony-session.json
{
  "version": 1,
  "agent_kind": "pi",
  "session_id": "019e0f48-...",
  "minted_at":   "2026-05-10T00:43:23Z",
  "last_used_at":"2026-05-10T00:43:41Z"
}
```

`workspace_key()` (`src/symphony/issue.py`) sanitizes the identifier so
Linear-style IDs like `TEAM/123` map to a safe directory name. The file is
written with `tempfile.mkstemp` + `os.replace` so a crash mid-write never
corrupts the previous content.

`session_id` always stores the **stable** id:
- pi/claude/gemini: the natural session uuid.
- codex: `entry.thread_id` (codex rewrites `entry.session_id` to
  `{thread_id}-{turn_id}` per turn — that turn-suffixed value must NOT be
  persisted).

## Read/write protocol

- `_run_agent_attempt` calls `_load_resume_session_id` *before*
  `build_backend(...)`. The id is fed into `BackendInit.resume_session_id`.
- `_on_codex_event` calls `_persist_session_id` from the
  `EVENT_SESSION_STARTED` branch. Throttled by
  `entry.last_persisted_session_id` so repeated session-started emits for
  the same id become no-ops.
- Cleanup: file rides along with the workspace — `WorkspaceManager.remove`
  rmtrees the parent dir. No bespoke deletion path.

## Backend honor-points

| Backend | Mechanism | First-turn flag                          |
|---------|-----------|------------------------------------------|
| pi      | `--session <id>`              | injected on turn 1 when `_session_id` was pre-populated |
| claude  | `--resume <id>`               | injected on turn 1 when `_session_id` was pre-populated |
| gemini  | id preserved only             | CLI flag wired by SMA-19 (`--session-id`); SMA-20 keeps the id alive across restarts |
| codex   | `thread/resume { threadId }`  | tried first; on RPC error falls back to `thread/start` and logs `session_resume_unsupported` |

## Codex fallback is load-bearing, not just legacy

Codex 0.130 supports `thread/resume` but requires the rollout JSONL on
disk under `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread-uuid>.jsonl`.
That file is written **only after** the first turn completes. If the
orchestrator dies between `thread/start` and the first `turn/start`, the
persisted thread id will fail to resume on the next dispatch with:

```
{"error":{"code":-32600,"message":"no rollout found for thread id ..."}}
```

The fallback path (catch `ResponseError`, log `session_resume_unsupported`,
retry `thread/start`) therefore fires on the normal-flow case where a
thread was minted but never used — not just on stale codex builds.

## Agent-kind mismatch

`_load_resume_session_id` compares `record.agent_kind` against
`cfg.agent.kind`. On mismatch it logs:

```
WARN session_store_kind_mismatch issue_id=... stored_kind=pi current_kind=claude
```

…and returns `None`, so the next dispatch mints a fresh session under the
new kind. The stale file is overwritten on the next session-started event.

## Operator escape hatch

Force a fresh session for one ticket:

```bash
rm ~/symphony_workspaces/<ID>/.symphony-session.json
```

The next dispatch will skip the resume path and mint anew. The agent CLI's
own session record (`~/.pi/agent/...`, `~/.claude/...`, etc.) stays
untouched — only symphony's view is reset.

## Files

- `src/symphony/session_store.py` — module owning the file format
- `src/symphony/orchestrator.py:_load_resume_session_id` — read path
- `src/symphony/orchestrator.py:_persist_session_id` — write path with throttle
- `src/symphony/backends/__init__.py:BackendInit.resume_session_id` — wire
- `src/symphony/backends/codex.py:start_session` — `thread/resume` + fallback
- `src/symphony/backends/{pi,claude_code,gemini}.py:__init__` — pre-populate `_session_id`
- `tests/test_session_store.py` — store-level tests
- `tests/test_orchestrator_dispatch.py` — load/save plumbing tests
- `tests/test_backends.py` — per-backend honor-point tests

## Decision log

- 2026-05-10 (SMA-20): chose per-workspace JSON over a single index file.
  Per-workspace cleanup is free with `WorkspaceManager.remove`; an index
  file would need locking (PRD round-4 shared rule #6 forbids locking
  tricks) and would lose every issue's session on a single corrupt write.
- 2026-05-10 (SMA-20): no new doctor check. `check_workspace_root` already
  verifies writability of the parent directory; the session file is one
  level deeper on the same filesystem and would never independently fail.
- 2026-05-10 (SMA-20, Learn): `minted_at` is **sticky** across rewrites of
  the same `(agent_kind, session_id)`. `session_store.write` reads the
  existing record and only resets `minted_at` when the id changes; an
  unchanged-id rewrite carries the original timestamp forward. Effect:
  `minted_at` reflects "when this id was first observed", not "most recent
  flush" — useful for cost-attribution audits across long-running tickets.
  Neither the PRD spec nor the Explore brief specified this; it surfaced
  during implementation when reasoning about what `minted_at` is *for*.
- 2026-05-10 (SMA-20, Learn): the orchestrator's `last_persisted_session_id`
  throttle is **not just** a per-turn anti-thrash. It also makes the
  resume-success happy path a structural no-op: `_run_agent_attempt`
  pre-populates `last_persisted_session_id = resume_session_id`, and a
  successful resume emits `EVENT_SESSION_STARTED` with that same id, so
  `_persist_session_id` short-circuits without rewriting the file.
  The first real write after restart only happens when codex falls back
  to `thread/start` (mints a new id) or pi/claude/gemini surface a fresh
  uuid. This invariant is only visible when you read backend `start_session`
  and orchestrator `_on_codex_event` together; record here so it isn't
  re-derived next time.
- 2026-05-10 (SMA-20, Learn): the literal PRD wording "throttle to once per
  turn" was *not* implemented as written — we throttle on **id change**
  instead, which is strictly stricter. Effect: `last_used_at` advances on
  id-change, not per-turn. Operators interpreting `last_used_at` as
  liveness will be wrong; treat it as "last id transition observed".
  PRD intent (anti-thrash) is preserved; PRD letter is not.
- 2026-05-10 (SMA-20, Learn): probe tooling caveat for future contributors —
  `codex app-server generate-json-schema` is **not present** in
  codex-cli 0.130.0 (the original Domain Brief's path). Resume support
  was confirmed via live JSON-RPC against the running app-server; see
  `docs/SMA-20/explore/codex-resume-probe.md` for the recipe. If a future
  ticket needs to verify another v2 RPC method, replicate that probe
  shape rather than chasing the missing schema-dump subcommand.
