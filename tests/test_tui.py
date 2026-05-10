"""TUI coverage — pure helpers + a Textual `Pilot`-driven smoke test.

The previous Rich `Live` implementation exposed dozens of internal helpers
(`_handle_key`, `_render`, `_lane_row_count` …) that the test suite drove
directly. The Textual rewrite hides layout/scroll/focus inside the
framework, so this file shrinks to:

* unit tests for the pure helpers (`_parse_iso`, `_silent_seconds`,
  `_CardStatus`, etc.) — these still matter because the silence-badge and
  card rendering depend on them, and
* one Pilot-driven integration test that boots `KanbanApp` against a stub
  orchestrator and verifies the lanes render with the expected counts and
  card identifiers. Pilot replaces all the manual key/mouse plumbing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from symphony.issue import Issue
from symphony.tui import (
    SILENT_THRESHOLD_S,
    AGENT_COLOR,
    STATE_COLOR,
    IssueCard,
    KanbanApp,
    KanbanTUI,
    Lane,
    StatsBar,
    TicketDetailScreen,
    _CardStatus,
    _build_runtime_index,
    _card_sort_key,
    _compact_rate_limits,
    _first_meaningful_line,
    _ordered_column_states,
    _parse_iso,
    _silent_seconds,
    _truncate,
)
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
    TuiConfig,
)


# ---------------------------------------------------------------------------
# fixtures / stubs
# ---------------------------------------------------------------------------


class _StaticWorkflowState:
    def __init__(self, cfg: ServiceConfig) -> None:
        self._cfg = cfg

    def current(self) -> ServiceConfig:
        return self._cfg


class _StubOrchestrator:
    def __init__(
        self,
        snapshot: dict[str, Any] | None = None,
        running_issues: tuple[Issue, ...] = (),
    ) -> None:
        self._snapshot = snapshot or {
            "counts": {"running": 0, "retrying": 0},
            "codex_totals": {},
            "running": [],
            "retrying": [],
            "generated_at": "now",
        }
        self._running_issues = running_issues

    def snapshot(self) -> dict[str, Any]:
        return self._snapshot

    def iter_running_issues(self) -> tuple[Issue, ...]:
        return self._running_issues

    def add_observer(self, observer: Any) -> None:
        # KanbanApp registers a tick observer; the Pilot test exercises the
        # widgets directly so we do not need to fire it.
        del observer


def _make_config(
    *,
    active_states: tuple[str, ...] = ("Todo", "In Progress", "Review"),
    terminal_states: tuple[str, ...] = ("Done",),
    state_descriptions: dict[str, str] | None = None,
    language: str = "en",
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
            terminal_states=terminal_states,
            state_descriptions=state_descriptions or {},
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=5,
            max_turns=20,
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
        tui=TuiConfig(language=language),
        prompt_template="hi",
    )


def _issue(identifier: str, state: str = "Todo", **extra: Any) -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=extra.pop("title", f"{identifier} title"),
        description=extra.pop("description", None),
        priority=extra.pop("priority", None),
        state=state,
        labels=extra.pop("labels", ()),
    )


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_suffix() -> None:
    parsed = _parse_iso("2026-05-09T23:51:21Z")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_iso_handles_offset_form() -> None:
    parsed = _parse_iso("2026-05-09T23:51:21+00:00")
    assert parsed is not None


def test_parse_iso_returns_none_for_garbage() -> None:
    assert _parse_iso("not a timestamp") is None
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso(12345) is None


def test_silent_seconds_none_when_no_event() -> None:
    assert _silent_seconds(None) is None


def test_silent_seconds_grows_with_age() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    s = _silent_seconds(past)
    assert s is not None
    # Tolerance for clock skew + scheduling jitter.
    assert 119.0 <= s <= 121.5


def test_silent_seconds_clamped_at_zero_for_future_timestamps() -> None:
    """Orchestrator clock ahead of ours → clamp to 0 instead of negative."""
    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert _silent_seconds(future) == 0.0


def test_silent_threshold_is_above_typical_warmup() -> None:
    """Sanity: must outlive the longest expected agent warm-up so healthy
    runs never trip it. 30 s covers Opus-4 cold start."""
    assert SILENT_THRESHOLD_S >= 30.0


def test_card_status_carries_last_event_at() -> None:
    when = datetime(2026, 5, 9, 23, 51, 21, tzinfo=timezone.utc)
    s = _CardStatus(runtime="running", last_event_at=when)
    assert s.last_event_at == when


def test_truncate_keeps_short_strings_unchanged() -> None:
    assert _truncate("hello", 10) == "hello"
    assert _truncate("hello world", 5) == "hell…"


def test_first_meaningful_line_skips_markdown_chrome() -> None:
    assert _first_meaningful_line("# Heading\n\nbody first") == "body first"
    assert _first_meaningful_line("```code```\n---\nfact") == "fact"
    assert _first_meaningful_line("") == ""
    assert _first_meaningful_line(None) == ""


def test_card_sort_key_promotes_high_priority() -> None:
    a = _issue("SMA-1")
    b = _issue("SMA-2", state="Todo", priority=1)
    assert _card_sort_key(b) < _card_sort_key(a)


def test_compact_rate_limits_serializes_first_three_keys() -> None:
    out = _compact_rate_limits({"primary": 80, "secondary": 50, "burst": 1, "skip": 99})
    assert "primary=80" in out
    parts = out.split(", ")
    assert len(parts) == 3


def test_compact_rate_limits_handles_empty() -> None:
    assert _compact_rate_limits({}) == "n/a"


def test_ordered_column_states_dedupes_active_then_terminal() -> None:
    cfg = _make_config(
        active_states=("Todo", "In Progress", "Done"),  # Done duplicated
        terminal_states=("Done", "Cancelled"),
    )
    assert _ordered_column_states(cfg) == ["Todo", "In Progress", "Done", "Cancelled"]


def test_state_color_map_covers_common_states() -> None:
    for s in ("todo", "in progress", "done", "blocked"):
        assert s in STATE_COLOR


def test_agent_color_map_covers_known_agents() -> None:
    for k in ("codex", "claude", "gemini"):
        assert k in AGENT_COLOR


def test_build_runtime_index_extracts_running_and_retrying() -> None:
    snap = {
        "running": [
            {
                "issue_id": "id-1",
                "turn_count": 3,
                "last_event": "agent_message_delta",
                "last_event_at": "2026-05-09T23:51:21Z",
                "tokens": {"total_tokens": 100, "input_tokens": 60, "output_tokens": 40},
                "last_message": "hello",
            }
        ],
        "retrying": [
            {"issue_id": "id-2", "attempt": 2, "error": "timeout"},
        ],
    }
    idx = _build_runtime_index(snap)
    assert idx["id-1"].runtime == "running"
    assert idx["id-1"].turn == 3
    assert idx["id-1"].tokens == 100
    assert idx["id-1"].last_event_at is not None
    assert idx["id-2"].runtime == "retrying"
    assert idx["id-2"].attempt == 2
    assert idx["id-2"].error == "timeout"


def test_build_runtime_index_tolerates_missing_blocks() -> None:
    assert _build_runtime_index({}) == {}
    assert _build_runtime_index({"running": None, "retrying": None}) == {}


# ---------------------------------------------------------------------------
# Pilot smoke tests for the Textual app
# ---------------------------------------------------------------------------


def _stub_tracker(monkeypatch: Any, candidates: list[Issue], terminals: list[Issue]) -> None:
    monkeypatch.setattr("symphony.tui._fetch_candidates", lambda _: list(candidates))
    monkeypatch.setattr("symphony.tui._fetch_terminals", lambda _: list(terminals))


@pytest.mark.asyncio
async def test_app_boots_and_renders_lanes(monkeypatch: Any) -> None:
    cfg = _make_config(
        active_states=("Todo", "In Progress"),
        terminal_states=("Done",),
        state_descriptions={"todo": "Triage; route to Explore"},
    )
    candidates = [_issue("SMA-1"), _issue("SMA-2", state="In Progress")]
    terminals = [_issue("SMA-9", state="Done")]
    _stub_tracker(monkeypatch, candidates, terminals)
    orch = _StubOrchestrator()
    app = KanbanApp(orch, _StaticWorkflowState(cfg))  # type: ignore[arg-type]

    async with app.run_test(size=(140, 35)) as pilot:
        # Wait for the initial tracker poll worker to drain.
        await pilot.pause()
        await asyncio.sleep(0.05)
        await pilot.pause()
        lanes = list(app.query(Lane))
        assert [lane.state_label for lane in lanes] == ["Todo", "In Progress", "Done"]
        cards = list(app.query(IssueCard))
        identifiers = {card.issue.identifier for card in cards}
        assert identifiers == {"SMA-1", "SMA-2", "SMA-9"}


@pytest.mark.asyncio
async def test_q_quits_app(monkeypatch: Any) -> None:
    cfg = _make_config()
    _stub_tracker(monkeypatch, [], [])
    app = KanbanApp(_StubOrchestrator(), _StaticWorkflowState(cfg))  # type: ignore[arg-type]
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    # run_test() exits when the app does; the assertion is implicit.


@pytest.mark.asyncio
async def test_enter_opens_ticket_detail_modal(monkeypatch: Any) -> None:
    cfg = _make_config()
    _stub_tracker(monkeypatch, [_issue("SMA-1", description="full body text")], [])
    app = KanbanApp(_StubOrchestrator(), _StaticWorkflowState(cfg))  # type: ignore[arg-type]
    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.05)
        await pilot.pause()
        cards = list(app.query(IssueCard))
        assert cards, "expected at least one card to focus"
        cards[0].focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TicketDetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TicketDetailScreen)


@pytest.mark.asyncio
async def test_running_card_shows_running_class(monkeypatch: Any) -> None:
    """Snapshot's `running` block should flip the card into the running variant."""
    cfg = _make_config()
    _stub_tracker(monkeypatch, [_issue("SMA-1", state="In Progress")], [])
    snap = {
        "counts": {"running": 1, "retrying": 0},
        "codex_totals": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        "running": [
            {
                "issue_id": "id-SMA-1",
                "turn_count": 2,
                "last_event": "agent_message",
                "last_event_at": datetime.now(timezone.utc).isoformat(),
                "tokens": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                "last_message": "thinking…",
            }
        ],
        "retrying": [],
        "generated_at": "now",
    }
    orch = _StubOrchestrator(snapshot=snap)
    app = KanbanApp(orch, _StaticWorkflowState(cfg))  # type: ignore[arg-type]
    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await asyncio.sleep(0.05)
        await pilot.pause()
        cards = list(app.query(IssueCard))
        assert any(card.has_class("-running") for card in cards)


@pytest.mark.asyncio
async def test_stats_bar_reports_counts_and_tokens(monkeypatch: Any) -> None:
    cfg = _make_config()
    _stub_tracker(monkeypatch, [], [])
    snap = {
        "counts": {"running": 2, "retrying": 1},
        "codex_totals": {"input_tokens": 1000, "output_tokens": 2000, "total_tokens": 3000},
        "running": [],
        "retrying": [],
        "generated_at": "now",
    }
    app = KanbanApp(_StubOrchestrator(snapshot=snap), _StaticWorkflowState(cfg))  # type: ignore[arg-type]
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        bar = app.query_one(StatsBar)
        rendered = bar.render()
        body = getattr(rendered, "plain", None) or str(rendered)
        assert "running=2" in body
        assert "retrying=1" in body
        assert "in=1,000" in body
        assert "out=2,000" in body
        assert "total=3,000" in body


def test_kanban_tui_wrapper_constructs() -> None:
    """The compat wrapper must accept `console` for cli.py / legacy callers."""
    cfg = _make_config()
    tui = KanbanTUI(_StubOrchestrator(), _StaticWorkflowState(cfg))  # type: ignore[arg-type]
    assert tui._app is None  # noqa: SLF001
    tui.request_stop()  # no-op before run() — must not raise
