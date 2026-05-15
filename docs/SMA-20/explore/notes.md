# SMA-20 — Explore notes

Working notes that ground the Domain Brief / Plan Candidates / Recommendation
appended to the ticket. Citations use `path:line` form against the symphony
repo at branch `symphony/SMA-20`.

## 1. Where session id is born today

| Backend | Where session id is captured | Resume on continuation? |
|---------|------------------------------|--------------------------|
| pi      | `pi.py:275-282` (JSONL `type: session`) | `pi.py:158-163` adds `--session <id>` only when `is_continuation=True`. |
| claude  | `claude_code.py:253-260` (`system.subtype=init`) and again at `result` (272-278) | `claude_code.py:141-147` adds `--resume <id>` only when `is_continuation=True`. |
| gemini  | `gemini.py:118-124` mints a synthetic id during `start_session` and emits `EVENT_SESSION_STARTED` | No `is_continuation` honoring; `gemini -p` is stateless today. SMA-19 will change this — out of scope here. |
| codex   | `codex.py:247-267` after `thread/start` returns | `is_continuation` arg is deleted (codex.py:272). v2 reuses the same `threadId` for every turn within a backend instance. |

In all four backends the captured id flows up through
`EVENT_SESSION_STARTED` and is recorded on `RunningEntry.thread_id` /
`entry.session_id` in `orchestrator.py:625-639`. Nothing persists it to disk.

## 2. Atomic-write reference

`tracker_file.py:187-200` (`write_ticket_atomic`) is the reference shape PRD
points to:

```python
fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(serialize_ticket(front, body))
    os.replace(tmp, path)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
```

Mirror this pattern for `session_store.write(...)`.

## 3. Workspace path resolution

`WorkspaceManager.path_for(identifier)` (`workspace.py:84-86`) returns
`<root>/<workspace_key(identifier)>` — `_WORKSPACE_KEY_INVALID` (issue.py:54)
sanitizes anything outside `[A-Za-z0-9._-]` to `_`. The session-store file
must live at `<that_path>/.symphony-session.json`. This means we must call
`workspace_key()` (or reuse `WorkspaceManager.path_for`) to resolve the
path; we cannot just append the raw `issue.identifier`.

## 4. The session-id mutation in `_on_codex_event`

`orchestrator.py:625-644`:

- On `EVENT_SESSION_STARTED`, `entry.thread_id = entry.session_id = sid`.
- On `EVENT_TURN_COMPLETED` for codex, `entry.session_id` becomes
  `f"{thread_id}-{turn_id}"` so codex consumers can distinguish turns.

**Implication for SMA-20**: when persisting we must store
`entry.thread_id`, not `entry.session_id`. Otherwise codex would write a
new value to disk every turn and resume would fail because the suffix is
turn-scoped, not session-scoped.

## 5. Codex `thread/resume` IS supported (probe)

`codex --version` → `codex-cli 0.130.0`.

```
codex app-server generate-json-schema --out docs/SMA-20/explore/codex-schema
ls docs/SMA-20/explore/codex-schema/v2/ | grep -i resume
# → ThreadResumeParams.json  ThreadResumeResponse.json
grep '"thread/' docs/SMA-20/explore/codex-schema/codex_app_server_protocol.v2.schemas.json
# → "thread/start" "thread/resume" + many others
```

`ThreadResumeParams` (per the schema, lines 1-98):
- Required: `threadId`.
- Optional: `cwd`, `sandbox`, `approvalPolicy`, `model`, `personality`, etc.
- Three resume modes documented: by `threadId` (load from disk), by
  `history` (in-memory), or by `path`. **We use `threadId`** — symphony
  already drives codex through stdio app-server, so disk-resume is exactly
  the lifecycle we want.
- Response is `ThreadResumeResponse` with the same `thread` object shape
  as `ThreadStartResponse` (so `_thread_id = result['thread']['id']` keeps
  working).

**Conclusion**: PRD's worry that codex 0.130 may not support resume is
**false** for 0.130.0. Add `METHOD_THREAD_RESUME = "thread/resume"` and
swap it in when `resume_session_id` is passed.

The defensive path (`session_resume_unsupported` log + fall back to
`thread/start`) is still worth keeping for older codex builds and for
when the resume call returns an error indicating the thread no longer
exists (e.g. local cache was cleared). Treat any `ResponseError` raised
by `thread/resume` as a soft fallback to `thread/start` plus a WARN log.

## 6. CLI flag verification (probed locally)

```
pi --help | grep -E '\-\-session'
#   --session <path|id>    Use specific session file or partial UUID

claude --help | grep -E '\-\-(resume|session-id)'
#   --fork-session
#   -r, --resume [value]
#   --session-id <uuid>

gemini --help | grep -E '\-\-(resume|session-id)'
#   -r, --resume                 Resume a previous session...
#       --session-id             Start a new session with a manually provided UUID
```

So the resume-on-turn-1 wiring is:
- pi    : `--session <id>` (works on turn 1 even if the session was created
          in a prior process — pi auto-saves under `~/.pi/agent/sessions/`)
- claude: `--resume <uuid>` on turn 1+
- gemini: `--session-id <uuid>` on turn 1 (declares the session id; SMA-19
          is the ticket that actually lands the JSON output parser; here we
          only ensure the flag is accepted)
- codex : RPC method `thread/resume { threadId }` instead of `thread/start`

## 7. Existing tests to extend

- `tests/test_backends.py` — `test_factory_*` for each backend; backend-
  specific resume tests can be added next to existing usage tests.
- `tests/test_orchestrator_dispatch.py` — `_make_config()` already
  constructs every backend config; we'll need a new test that invokes a
  fake `_dispatch` path with a pre-existing session file and asserts the
  RunningEntry comes up with the persisted session_id.
- `tests/test_session_store.py` — new file (per PRD acceptance #7).

## 8. Doctor check decision

`check_workspace_root` (`doctor.py:142-154`) already creates a temp file at
`<workspace_root>/.symphony-doctor-*` to verify writability. The session
file lives at `<workspace_root>/<id>/.symphony-session.json` — same parent
filesystem. Adding a separate `check_session_store_writable` would be
redundant. **Verdict**: no new doctor check needed; mention the
redundancy in the ticket Implementation note so reviewers can see we
considered it.

## 9. File layout summary (new + touched)

NEW:
- `src/symphony/session_store.py` — `read(path) -> SessionRecord | None`
  + `write(path, kind, session_id) -> None` + dataclass `SessionRecord`.
- `tests/test_session_store.py` — four cases per PRD acceptance #7.
- `llm-wiki/session-persistence.md` — wiki page indexed in `INDEX.md`.

TOUCHED:
- `src/symphony/backends/__init__.py` — `BackendInit.resume_session_id: str | None = None`.
- `src/symphony/backends/pi.py` — honor resume on turn 1.
- `src/symphony/backends/claude_code.py` — honor resume on turn 1.
- `src/symphony/backends/gemini.py` — honor resume on turn 1 (`--session-id`).
- `src/symphony/backends/codex.py` — `thread/resume` when set, fallback on error.
- `src/symphony/orchestrator.py` — load on dispatch, save in
  `_on_codex_event` (throttled per turn).
- `tests/test_orchestrator_dispatch.py` — round-trip test.
- `llm-wiki/INDEX.md` — add `session-persistence` row.
- `.claude/skills/using-symphony/reference/workflow-config.md` — note
  `.symphony-session.json` and "delete to force fresh session".
