"""SPEC §17.4 — orchestrator dispatch eligibility / sort / blockers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from symphony.issue import BlockerRef, Issue, sort_for_dispatch
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.workflow import (
    AgentConfig,
    CodexConfig,
    HooksConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    WorkflowState,
)


def _make_config(
    *,
    max_concurrent: int = 5,
    per_state: dict[str, int] | None = None,
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
            active_states=("Todo", "In Progress"),
            terminal_states=("Done", "Cancelled"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
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
