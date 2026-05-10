"""SPEC §17.4 — orchestrator dispatch eligibility / sort / blockers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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


def test_sort_for_dispatch_ties_by_identifier():
    a = _issue("MT-2", priority=1)
    b = _issue("MT-1", priority=1)
    out = [i.identifier for i in sort_for_dispatch([a, b])]
    assert out == ["MT-1", "MT-2"]


def test_orchestrator_dispatch_prioritizes_oldest_active_entry():
    """Workers run active tickets FIFO by current-state entry time."""
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
