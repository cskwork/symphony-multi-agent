# Code touchpoints for SMA-20

Ground-truth references collected during Explore. Used to keep the plan
honest about what already exists vs. what must be added.

## Where session_id flows today (read-only)

- `src/symphony/orchestrator.py:625-639` — `_on_codex_event` captures
  `session_started` and writes `entry.thread_id` / `entry.session_id`.
  Single trigger point per dispatch — perfect throttle.
- `src/symphony/orchestrator.py:267-283` — `_running_row` snapshot
  surfaces `session_id` in the JSON API.
- `src/symphony/orchestrator.py:407-436` — `_dispatch` constructs
  `RunningEntry` with `session_id=None` (default). This is the
  pre-population point for resume.
- `src/symphony/orchestrator.py:462-470` — where `BackendInit` is
  built. `resume_session_id` field gets wired through here.

## Per-backend resume hook points

- `src/symphony/backends/pi.py:152-164` — `run_turn` only adds
  `--session <id>` when `is_continuation and resume_across_turns and
  self._session_id`. SMA-20 needs: also add it on **turn 1** when an
  initial `_session_id` was injected at construction time. Pi accepts
  partial UUIDs and full session paths via `--session <path|id>`
  (verified via `pi --help`).
- `src/symphony/backends/claude_code.py:140-147` — symmetrical to pi;
  `--resume <id>` on continuation only. Same fix shape applies.
- `src/symphony/backends/codex.py:234-267` — `start_session` always
  calls `thread/start`. Resume path: try `thread/resume {threadId}`,
  catch the "no rollout found" error, fall through to `thread/start`.
  See `explore/codex-thread-resume-probe.md` for the protocol probe
  evidence.
- `src/symphony/backends/gemini.py:115-124` — `start_session` mints a
  synthetic id locally. SMA-19 will rewrite this to mint a real UUID
  and wire `--session-id <uuid>`; SMA-20 only needs to thread the
  external resume id through `__init__` — the actual flag is added by
  SMA-19. **Coordinate sequencing**: SMA-19 lands first, then SMA-20's
  gemini hook is a one-line `self._session_id = init.resume_session_id
  or None` change.

## Atomic-write reference

- `src/symphony/tracker_file.py:187-200` — `write_ticket_atomic` is
  the canonical pattern: `tempfile.mkstemp` in `path.parent`, write,
  `os.replace`, `os.unlink` on failure. Mirror this exactly in
  `session_store.write`.

## Workspace cleanup

- `src/symphony/workspace.py:134-150` — `WorkspaceManager.remove(...)`
  uses `_force_rmtree`. Anything inside the workspace dir
  (`<root>/<ID>/`) goes away with it — no extra cleanup wiring needed
  for `.symphony-session.json`.

## Doctor

- `src/symphony/doctor.py:142-154` — `check_workspace_root` tempfile-
  probes the workspace root parent, NOT the per-issue subdir. Per-
  issue dirs are created on-demand inside `WorkspaceManager`, after
  doctor runs. The session-store file lives inside the per-issue dir,
  so writability of that dir is guaranteed by the same OS permissions
  as the parent. **Verdict: no new doctor check needed** — note this
  in the plan and add a one-line comment in the new module pointing to
  the existing check. Acceptance #6 explicitly allows a no-op.

## Logging conventions

- Existing event names like `agent_session_started`,
  `session_store_kind_mismatch`, `session_resume_unsupported` follow a
  snake_case event name + structured kwargs. Copy the
  `log.info(event_name, key=value, ...)` shape from
  `_on_codex_event`.

## Test patterns

- `tests/test_orchestrator_dispatch.py:1-104` — the canonical
  `_make_config` and `_orch` helpers used to build a fully-wired
  ServiceConfig + Orchestrator in tests. Reuse the helper shape (or
  copy it into the new test module if cross-test extraction would be
  intrusive).
- `tests/test_backends.py` — uses real `tempfile`-backed workspaces
  and exercises backend logic without spawning subprocesses. Same
  approach for `test_session_store.py`: stick to file I/O + dataclass
  round-trips, no orchestrator.
