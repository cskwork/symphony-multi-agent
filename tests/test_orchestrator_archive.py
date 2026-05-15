"""Orchestrator auto-archive sweep — `_archive_sweep` integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from symphony.issue import Issue
from symphony.orchestrator import Orchestrator
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


def _cfg(*, archive_after_days: int = 30) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="linear",
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=("Todo",),
            terminal_states=("Done", "Archive"),
            archive_state="Archive",
            archive_after_days=archive_after_days,
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=10,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
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
            command="claude -p",
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


def _issue(identifier: str, *, state: str, days_old: int) -> Issue:
    return Issue(
        id=f"uuid-{identifier}",
        identifier=identifier,
        title=identifier,
        description=None,
        priority=None,
        state=state,
        updated_at=datetime.now(timezone.utc) - timedelta(days=days_old),
    )


@pytest.mark.asyncio
async def test_archive_sweep_moves_stale_done(monkeypatch) -> None:
    """Stale Done issues get update_state(_, "Archive") on each tick."""
    cfg = _cfg(archive_after_days=30)
    state = WorkflowState(Path("/tmp/no.md"))
    orch = Orchestrator(state)

    fresh = _issue("FRESH", state="Done", days_old=5)
    stale = _issue("STALE", state="Done", days_old=60)
    already = _issue("DONE-LONG-AGO", state="Archive", days_old=400)

    moved: list[tuple[str, str]] = []

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_terminal_issues",
        staticmethod(lambda _cfg: [fresh, stale, already]),
    )

    def fake_update(_cfg: ServiceConfig, issue: Issue, target_state: str) -> None:
        moved.append((issue.identifier, target_state))

    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(fake_update)
    )

    await orch._archive_sweep(cfg)
    assert moved == [("STALE", "Archive")]


@pytest.mark.asyncio
async def test_archive_sweep_disabled_when_zero(monkeypatch) -> None:
    cfg = _cfg(archive_after_days=0)
    state = WorkflowState(Path("/tmp/no.md"))
    orch = Orchestrator(state)

    called: list[Any] = []

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_terminal_issues",
        staticmethod(lambda _cfg: called.append("fetched") or []),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(lambda *_a, **_kw: called.append("update")),
    )

    await orch._archive_sweep(cfg)
    # Disabled path returns immediately — no tracker calls, period.
    assert called == []


@pytest.mark.asyncio
async def test_archive_sweep_swallows_per_issue_failures(monkeypatch) -> None:
    """One failing update_state must not abort the rest of the sweep."""
    cfg = _cfg(archive_after_days=30)
    state = WorkflowState(Path("/tmp/no.md"))
    orch = Orchestrator(state)

    a = _issue("A", state="Done", days_old=60)
    b = _issue("B", state="Done", days_old=60)

    moved: list[str] = []

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_terminal_issues",
        staticmethod(lambda _cfg: [a, b]),
    )

    def fake_update(_cfg, issue, target):
        if issue.identifier == "A":
            raise RuntimeError("simulated 5xx")
        moved.append(issue.identifier)

    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(fake_update)
    )

    await orch._archive_sweep(cfg)
    # B should have been archived even though A failed.
    assert moved == ["B"]
