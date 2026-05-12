"""SPEC §17.4 — orchestrator dispatch eligibility / sort / blockers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue, sort_for_dispatch
from symphony.orchestrator import Orchestrator, RunningEntry, _sort_for_dispatch_fifo
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    WorkflowState,
)


def _make_config(
    *,
    max_concurrent: int = 5,
    per_state: dict[str, int] | None = None,
    active_states: tuple[str, ...] = ("Todo", "In Progress"),
) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="linear",
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=active_states,
            terminal_states=("Done", "Cancelled"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=max_concurrent,
            max_turns=20,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state=per_state or {},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


def _orch() -> Orchestrator:
    state = WorkflowState(Path("/tmp/no.md"))
    return Orchestrator(state)


def _issue(
    identifier: str,
    state: str = "Todo",
    blocked_by: tuple[BlockerRef, ...] = (),
    priority: int | None = 2,
    updated_at: datetime | None = None,
) -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description=None,
        priority=priority,
        state=state,
        blocked_by=blocked_by,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=updated_at,
    )


def test_should_dispatch_basic():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1")
    assert orch._should_dispatch(issue, cfg) is True


def test_should_skip_terminal_state():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Done")
    assert orch._should_dispatch(issue, cfg) is False


def test_should_skip_already_running():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1")
    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    assert orch._should_dispatch(issue, cfg) is False


def test_todo_with_non_terminal_blocker_blocked():
    cfg = _make_config()
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="In Progress")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))
    assert orch._should_dispatch(issue, cfg) is False


def test_todo_with_terminal_blocker_eligible():
    cfg = _make_config()
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="Done")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))
    assert orch._should_dispatch(issue, cfg) is True


def test_per_state_concurrency_cap():
    cfg = _make_config(per_state={"todo": 1})
    orch = _orch()
    held = _issue("MT-2", state="Todo")
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    new = _issue("MT-3", state="Todo")
    assert orch._should_dispatch(new, cfg) is False


def test_sort_for_dispatch_uses_registration_number_before_priority():
    earlier = _issue("OLV-061", priority=None)
    later = _issue("OLV-131", priority=1)

    out = [i.identifier for i in sort_for_dispatch([later, earlier])]

    assert out == ["OLV-061", "OLV-131"]


def test_orchestrator_dispatch_prioritizes_ticket_registration_order():
    """Workers run tickets in registration order, not current-state timestamp."""
    cfg = _make_config(
        max_concurrent=1,
        active_states=("Todo", "Explore", "In Progress", "Review", "QA", "Learn"),
    )
    review = _issue(
        "OLV-002",
        state="Review",
        priority=None,
        updated_at=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
    )
    todo = _issue(
        "OLV-003",
        state="Todo",
        priority=1,
        updated_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([todo, review], cfg)
    ]

    assert ordered == ["OLV-002", "OLV-003"]

    older_todo = _issue(
        "OLV-010",
        state="Todo",
        priority=None,
        updated_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
    )
    newer_review = _issue(
        "OLV-011",
        state="Review",
        priority=1,
        updated_at=datetime(2026, 1, 1, 11, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([newer_review, older_todo], cfg)
    ]

    assert ordered == ["OLV-010", "OLV-011"]

    older_registered = _issue(
        "OLV-061",
        state="Todo",
        priority=None,
        updated_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )
    newer_registered = _issue(
        "OLV-131",
        state="Todo",
        priority=1,
        updated_at=datetime(2025, 1, 1, 8, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([newer_registered, older_registered], cfg)
    ]

    assert ordered == ["OLV-061", "OLV-131"]


import asyncio
from datetime import timedelta

from symphony.orchestrator import STALL_FORCE_EJECT_GRACE_S


def test_reconcile_force_ejects_zombie_after_grace():
    """Worker that didn't die from cancel must lose its slot after grace.

    Reproduces the OLV-003 zombie pattern: a worker stuck on a
    non-cancellable await still holds its slot 17 minutes after the stall
    timeout fires. Without force-eject, every other ticket starves.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    zombie = _issue("MT-1", state="Todo")
    now = datetime.now(timezone.utc)

    async def _run() -> None:
        # `_schedule_retry` reads `self._loop` to compute the timer's
        # absolute due-time, so wire the running loop in like `start()` does.
        orch._loop = asyncio.get_running_loop()
        entry = RunningEntry(
            issue=zombie,
            started_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S * 4),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            cancelled_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S + 5),
        )
        orch._running[zombie.id] = entry
        orch._claimed.add(zombie.id)

        await orch._reconcile_running(cfg)
        # Cancel the retry timer the eject just scheduled so it doesn't
        # fire after the test loop closes.
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert zombie.id not in orch._running, "zombie slot should be freed"
    assert zombie.id not in orch._claimed, "claim should be released"
    assert zombie.id in orch._retry, "force-eject must schedule a retry"
    assert orch._retry[zombie.id].error == "force_ejected_zombie"


def test_reconcile_first_stall_only_cancels():
    """A live worker that just crossed stall_timeout gets cancel + flag, not eject.

    The grace window starts only after the cancel. The first reconcile tick
    that detects a stall must NOT eject — it must give the cancel time to
    propagate first.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            await orch._reconcile_running(cfg)

            assert issue.id in orch._running, "first stall must NOT eject"
            assert (
                orch._running[issue.id].cancelled_at is not None
            ), "cancel must be flagged"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_running_snapshot_includes_worker_task_stack():
    """State snapshots expose where a running worker coroutine is parked.

    This is the normal `/api/v1/state` path, so operators can diagnose a
    stuck pre-turn worker even if the dedicated debug endpoint is unavailable
    in a stale process.
    """
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> dict:
        event = asyncio.Event()

        async def _parked_worker() -> None:
            await event.wait()

        worker_task = asyncio.create_task(
            _parked_worker(), name="symphony-worker-MT-1"
        )
        try:
            await asyncio.sleep(0)
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            return orch.snapshot()
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    snapshot = asyncio.run(_run())
    task_debug = snapshot["running"][0]["worker_task"]

    assert task_debug["name"] == "symphony-worker-MT-1"
    assert task_debug["done"] is False
    assert any("_parked_worker" in frame for frame in task_debug["stack"])


def test_dispatch_task_cancelled_before_start_releases_running_slot():
    """A worker cancelled before its coroutine first runs still cleans up.

    Python does not enter a coroutine's body/finally block when a freshly
    created task is cancelled before its first scheduling slice. Symphony
    must not leave that issue in `_running` forever.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._dispatch(issue, cfg, attempt=None)
        task = orch._running[issue.id].worker_task
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert issue.id not in orch._running
    assert issue.id in orch._retry
    assert "worker_task_cancelled_before_start" in (orch._retry[issue.id].error or "")


def test_available_slots_counts_retry_pending_against_budget():
    """A ticket with a pending retry holds its slot through Done.

    Without this, the 1s `CONTINUATION_RETRY_DELAY_MS` window between a
    worker exiting and its retry firing would let another ticket claim
    the slot — surfacing as "OLV-005 starts while OLV-002 is still
    in Review" even though `max_concurrent_agents == 1`.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        # Empty board: one slot is available.
        assert orch._available_slots(cfg) == 1

        # Worker exit path: `_on_worker_exit` removes the entry from
        # `_running` and queues a retry. Simulate by scheduling a retry
        # directly (no running entry).
        orch._schedule_retry(
            "id-OLV-002",
            identifier="OLV-002",
            attempt=1,
            delay_ms=1_000,
            error=None,
        )
        try:
            assert "id-OLV-002" in orch._retry
            # The retry-pending ticket holds the slot.
            assert orch._available_slots(cfg) == 0
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_reconcile_stalls_on_progress_timestamp_not_codex_timestamp():
    """A worker still receiving meta events but no real progress must stall.

    Reproduces OLV-002 (2026-05-10): claude API kept emitting tool_result
    echoes / stream pings as `EVENT_OTHER_MESSAGE`, which previously bumped
    `last_codex_timestamp` and indefinitely deferred the 5-min stall. The
    fix splits stall-detection time from UI-activity time: stall reads
    `last_progress_timestamp`, which only advances on real model output.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                # UI-side timestamp is fresh — meta event arrived 1s ago.
                last_codex_timestamp=now - timedelta(seconds=1),
                # Stall-side timestamp is far past the 300_000 ms threshold.
                last_progress_timestamp=now - timedelta(minutes=10),
            )
            orch._running[issue.id] = entry

            await orch._reconcile_running(cfg)

            assert (
                orch._running[issue.id].cancelled_at is not None
            ), "stall must trigger on stale last_progress_timestamp even if last_codex_timestamp is fresh"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_on_codex_event_user_role_other_message_does_not_advance_progress():
    """Tool_result echoes from claude_code (kind='user') must NOT count as progress.

    These are the events that fooled the old stall detector. They still
    update `last_codex_timestamp` for UI freshness, but `last_progress_timestamp`
    must stay pinned at the prior progress event.
    """
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        baseline = datetime.now(timezone.utc) - timedelta(minutes=10)
        entry = RunningEntry(
            issue=issue,
            started_at=baseline,
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            last_codex_timestamp=baseline,
            last_progress_timestamp=baseline,
        )
        orch._running[issue.id] = entry

        # User-role passthrough — what claude_code emits for tool_result.
        # No tokens, no lifecycle, type='user'.
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "user", "message": {"content": []}},
                "usage": {},
                "rate_limits": None,
            },
        )

        # last_codex_timestamp moves forward (UI stays "alive"), but
        # last_progress_timestamp must NOT advance.
        assert entry.last_codex_timestamp is not None
        assert entry.last_codex_timestamp > baseline
        assert entry.last_progress_timestamp == baseline

        # Now the assistant message variant — this DOES count as progress.
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "assistant", "message": {"content": []}},
                "usage": {},
                "rate_limits": None,
            },
        )

        assert entry.last_progress_timestamp is not None
        assert entry.last_progress_timestamp > baseline

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Auto-commit at Done — see workspace.commit_workspace_on_done.
# ---------------------------------------------------------------------------


def _install_running_entry(orch: Orchestrator, issue: Issue) -> RunningEntry:
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp/ws-fake"),
    )
    orch._running[issue.id] = entry
    return entry


def _stub_workflow_state_returning(
    orch: Orchestrator, cfg, monkeypatch: pytest.MonkeyPatch
) -> list[dict]:
    """Force `self._workflow_state.current()` to return cfg; capture commit calls.

    Uses monkeypatch so the module-level rebind of commit_workspace_on_done
    auto-reverts at test teardown — otherwise the stub leaks into other
    tests that exercise orchestrator paths (observed: TUI integration
    tests that drive a real worker exit path).
    """
    import symphony.orchestrator as _orch_mod

    captured: list[dict] = []
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _capture(path, *, identifier, title, **_):
        captured.append(
            {"path": path, "identifier": identifier, "title": title}
        )

    monkeypatch.setattr(_orch_mod, "commit_workspace_on_done", _capture)
    return captured


def test_on_worker_exit_commits_workspace_at_done(monkeypatch):
    """reason='normal' + state='Done' + auto_commit_on_done=True ⇒ commit fires."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-DONE", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(captured) == 1, "commit must be invoked exactly once"
            assert captured[0]["identifier"] == "MT-DONE"
            assert captured[0]["title"] == "MT-DONE title"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_commits_workspace_for_non_done_terminal_state(monkeypatch):
    """Worker exited cleanly on Cancelled/Blocked — must still snapshot the
    worktree so `git worktree remove --force` doesn't discard the agent's
    work. The commit message includes the state for traceability."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-CANCEL", state="Cancelled")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(captured) == 1, (
                "commit must fire on every clean worker exit so worktree "
                "removal can't lose uncommitted work"
            )
            assert captured[0]["identifier"] == "MT-CANCEL"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_respects_auto_commit_off(monkeypatch):
    """auto_commit_on_done=False ⇒ no commit even at Done."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_off = _replace_agent_field(base_cfg, auto_commit_on_done=False)
    orch = _orch()
    issue = _issue("MT-OFF", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg_off, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert captured == [], "auto_commit_on_done=False must suppress commit"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def _replace_agent_field(cfg, **agent_overrides):
    """Return a new ServiceConfig with `agent` swapped for an updated AgentConfig."""
    from dataclasses import replace

    new_agent = replace(cfg.agent, **agent_overrides)
    return replace(cfg, agent=new_agent)


# ---------------------------------------------------------------------------
# Operator-driven pause / resume.
# ---------------------------------------------------------------------------


def test_pause_worker_rejects_unknown_issue():
    """Pausing a ticket that isn't running must report failure, not crash."""
    orch = _orch()
    assert orch.pause_worker("id-missing") is False
    assert orch.is_paused("id-missing") is False


def test_pause_then_resume_flips_state_and_snapshot_reports_it():
    """`is_paused` + snapshot row both reflect the operator's pause toggle."""
    orch = _orch()
    issue = _issue("MT-1")

    async def _run() -> None:
        event = asyncio.Event()

        async def _parked_worker() -> None:
            await event.wait()

        worker_task = asyncio.create_task(_parked_worker())
        try:
            await asyncio.sleep(0)
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )

            assert orch.is_paused(issue.id) is False
            assert orch.pause_worker(issue.id) is True
            assert orch.is_paused(issue.id) is True

            snap = orch.snapshot()
            row = next(r for r in snap["running"] if r["issue_id"] == issue.id)
            assert row["paused"] is True

            # Re-pausing an already-paused worker is a no-op (no double-clear).
            assert orch.pause_worker(issue.id) is False

            assert orch.resume_worker(issue.id) is True
            assert orch.is_paused(issue.id) is False
            assert orch.resume_worker(issue.id) is False
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_pause_event_blocks_then_resume_releases_worker():
    """A coroutine awaiting the pause event blocks until resume_worker fires."""
    orch = _orch()
    issue = _issue("MT-1")

    async def _run() -> bool:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)
        event = orch._pause_events[issue.id]
        assert not event.is_set()

        observed_release = False

        async def _waiter() -> None:
            nonlocal observed_release
            await event.wait()
            observed_release = True

        waiter_task = asyncio.create_task(_waiter())
        # Yield so the waiter parks on the event.
        await asyncio.sleep(0)
        assert not waiter_task.done(), "waiter must be parked while paused"

        orch.resume_worker(issue.id)
        await asyncio.wait_for(waiter_task, timeout=1.0)
        return observed_release

    released = asyncio.run(_run())
    assert released is True


def test_reconcile_skips_stall_detection_for_paused_worker():
    """A paused worker that hasn't emitted progress in 10 min must NOT be cancelled."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                # No progress in 10 min — would normally fire the stall.
                last_progress_timestamp=now - timedelta(minutes=10),
            )
            orch._running[issue.id] = entry
            orch.pause_worker(issue.id)

            await orch._reconcile_running(cfg)

            # Pause overrides stall detection — the entry must not be cancelled.
            assert orch._running[issue.id].cancelled_at is None
            assert worker_task.cancelled() is False
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_worker_exit_preserves_pause_flag_for_held_ticket():
    """Pause is per-issue — a worker exit must keep `_paused_issue_ids` intact.

    Operator's intent ("hold this ticket") shouldn't evaporate just because
    the in-flight turn errored out or completed. The wakeup event is the
    per-worker piece; the pause flag is the per-issue piece.
    """
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)
        assert orch.is_paused(issue.id) is True

        try:
            await orch._on_worker_exit(issue.id, reason="turn_error", error="boom")

            # Wakeup event popped (per-worker), but pause flag preserved.
            assert issue.id not in orch._pause_events
            assert orch.is_paused(issue.id) is True
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_eligible_refuses_paused_ticket_for_dispatch_and_retry():
    """`_eligible` returns False for a paused issue on both code paths.

    Without this, a worker that exits while paused would re-dispatch via
    `_on_retry_timer`, surfacing as auto-unpause to the operator.
    """
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    orch._paused_issue_ids.add(issue.id)

    assert orch._eligible(issue, cfg, owning_retry=False) is False
    assert orch._eligible(issue, cfg, owning_retry=True) is False

    orch._paused_issue_ids.discard(issue.id)
    assert orch._eligible(issue, cfg, owning_retry=False) is True


def test_retry_timer_reparks_paused_ticket_without_dispatching(monkeypatch):
    """A retry timer firing on a paused ticket reschedules without dispatch."""
    from symphony.orchestrator import PAUSED_RETRY_HOLD_MS

    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._paused_issue_ids.add(issue.id)
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

        # Schedule a "natural" retry — pretend a worker just exited.
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=100,
            error="turn_error: simulated",
        )
        original_attempt = orch._retry[issue.id].attempt
        try:
            await orch._on_retry_timer(issue.id)

            # Should NOT dispatch; should re-park under the same attempt.
            assert issue.id not in orch._running, "paused ticket must not dispatch"
            reparked = orch._retry.get(issue.id)
            assert reparked is not None, "retry must remain scheduled"
            assert reparked.attempt == original_attempt, (
                "paused re-park must not consume a retry attempt"
            )
            assert reparked.error == "paused"
            # Hold delay roughly matches PAUSED_RETRY_HOLD_MS.
            expected_due = (
                orch._loop.time() * 1000 + PAUSED_RETRY_HOLD_MS
            )
            assert abs(reparked.due_at_ms - expected_due) < 500
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_resume_worker_releases_held_retry_immediately(monkeypatch):
    """Resume must kick the retry-hold timer so the operator doesn't wait it out."""
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._paused_issue_ids.add(issue.id)
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

        async def _fake_fetch(_cfg):
            return [issue]

        monkeypatch.setattr(orch, "_fetch_candidates", _fake_fetch)

        dispatched: list[str] = []

        def _capture_dispatch(matched_issue, _cfg, *, attempt):
            dispatched.append(matched_issue.id)

        monkeypatch.setattr(orch, "_dispatch", _capture_dispatch)

        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=60_000,  # long timer — only resume should fire it
            error="turn_error",
        )

        assert orch.resume_worker(issue.id) is True
        # Let the create_task() chain run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert dispatched == [issue.id], (
            "resume must fire the held retry, not wait out the timer"
        )
        assert orch.is_paused(issue.id) is False

    asyncio.run(_run())


def test_reconcile_part_b_skips_paused_worker_on_terminal_state(monkeypatch):
    """Reconcile must not cancel a paused worker when its state moves terminal."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            orch.pause_worker(issue.id)

            # Tracker reports the ticket moved to Done while we hold it.
            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            await orch._reconcile_running(cfg)

            assert worker_task.cancelled() is False, (
                "paused worker must survive reconcile despite terminal state"
            )
            assert issue.id in orch._running
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminate_terminal_commits_before_remove(monkeypatch):
    """Reconcile path that force-cancels a stale terminal-state worker MUST
    snapshot the workspace before calling `WorkspaceManager.remove()`,
    otherwise `git worktree remove --force` discards uncommitted work."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-RC", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-rc"),
            )
            # Backdate last activity so the 10s grace window is exhausted.
            entry.last_codex_timestamp = datetime.now(timezone.utc).replace(year=2000)
            orch._running[issue.id] = entry

            # Tracker reports the ticket has moved to a terminal state.
            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            # Capture the call order of commit + remove.
            calls: list[str] = []

            import symphony.orchestrator as _orch_mod

            async def _capture_commit(path, *, identifier, title, **_):
                calls.append(f"commit:{identifier}")

            class _StubWS:
                async def remove(self, p):
                    calls.append(f"remove:{p}")

                def path_for(self, ident):
                    return Path("/tmp/ws-rc")

            monkeypatch.setattr(_orch_mod, "commit_workspace_on_done", _capture_commit)
            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg)

            assert calls == ["commit:MT-RC", "remove:/tmp/ws-rc"], (
                f"commit must precede remove; got {calls}"
            )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminate_terminal_skips_commit_when_auto_off(monkeypatch):
    """If the operator opted out via auto_commit_on_done=False, reconcile
    must still remove but skip the commit."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_off = _replace_agent_field(base_cfg, auto_commit_on_done=False)
    orch = _orch()
    issue = _issue("MT-RC-OFF", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-off"),
            )
            entry.last_codex_timestamp = datetime.now(timezone.utc).replace(year=2000)
            orch._running[issue.id] = entry

            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            import symphony.orchestrator as _orch_mod

            commit_calls: list[str] = []
            remove_calls: list[str] = []

            async def _capture_commit(path, *, identifier, title, **_):
                commit_calls.append(identifier)

            class _StubWS:
                async def remove(self, p):
                    remove_calls.append(str(p))

                def path_for(self, ident):
                    return Path("/tmp/ws-off")

            monkeypatch.setattr(_orch_mod, "commit_workspace_on_done", _capture_commit)
            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg_off)

            assert commit_calls == [], "auto_commit_on_done=False must skip commit"
            assert remove_calls == ["/tmp/ws-off"], "remove must still happen"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_snapshot_retry_row_includes_paused_flag():
    """A paused ticket sitting in the retry queue must surface `paused` for the TUI."""
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)

        try:
            # Simulate the worker exiting while paused.
            await orch._on_worker_exit(issue.id, reason="turn_error", error="boom")

            snap = orch.snapshot()
            retry_rows = snap.get("retrying", [])
            assert retry_rows, "expected a retry row for the paused ticket"
            assert retry_rows[0]["issue_id"] == issue.id
            assert retry_rows[0]["paused"] is True
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_find_running_issue_id_resolves_human_identifier():
    """Server endpoints take `OLV-002` style ids — resolve to internal id."""
    orch = _orch()
    issue = _issue("OLV-002")
    _install_running_entry(orch, issue)

    assert orch.find_running_issue_id("OLV-002") == issue.id
    assert orch.find_running_issue_id("NOT-A-TICKET") is None
