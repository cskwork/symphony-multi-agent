"""Phase-transition handoff between Kanban states (§16.5 fresh context).

The orchestrator must tear down the backend session and rebuild it with a
freshly rendered first-turn prompt whenever the issue changes state mid
run. Shared knowledge between phases flows only via on-disk artefacts and
the ticket body — never via accumulated backend conversation context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from symphony import orchestrator as orch_mod
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    PromptConfig,
    ServerConfig,
    ServiceConfig,
    SUPPORTED_AGENT_KINDS,
    TrackerConfig,
    TuiConfig,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeBackend:
    """Records every call so tests can assert on counts and arguments."""

    init_id: int
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start_session(
        self, *, initial_prompt: str, issue_title: str
    ) -> None:
        self.calls.append(
            (
                "start_session",
                {
                    "initial_prompt": initial_prompt,
                    "issue_title": issue_title,
                },
            )
        )

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.calls.append(
            (
                "run_turn",
                {"prompt": prompt, "is_continuation": is_continuation},
            )
        )

    async def stop(self) -> None:
        self.calls.append(("stop", {}))


class _FakeWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "fake"
        self.created_now = True


class _FakeWorkspaceManager:
    """Minimal stand-in for `WorkspaceManager` used by `_run_agent_attempt`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def path_for(self, identifier: str) -> Path:
        del identifier
        return self._path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        del identifier
        return _FakeWorkspace(self._path)

    async def before_run(self, path: Path) -> None:
        del path
        return None

    async def after_run_best_effort(self, path: Path) -> None:
        del path
        return None


# ---------------------------------------------------------------------------
# Config / fixtures
# ---------------------------------------------------------------------------


def _make_config(
    *, max_turns: int = 5, prompt_template: str | None = None, prompts: PromptConfig | None = None
) -> ServiceConfig:
    # Prompt template references {{ issue.state }} and {{ is_rewind }} so
    # the rendered first prompt is observably different across phase
    # transitions AND the rewind signal is testable end-to-end.
    template = prompt_template or (
        "issue={{ issue.identifier }} state={{ issue.state }} rewind={{ is_rewind }}"
    )
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="file",  # avoids `linear_graphql_tool()` in the codex tools list
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=("Todo", "Explore", "In Progress", "Review"),
            terminal_states=("Done", "Cancelled"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=max_turns,
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
        tui=TuiConfig(language="en", visible_lanes=5),
        prompts=prompts or PromptConfig(),
        prompt_template=template,
    )


def _make_issue(state: str = "Todo") -> Issue:
    return Issue(
        id="iss-1",
        identifier="MT-1",
        title="phase transition fixture",
        description=None,
        priority=2,
        state=state,
        blocked_by=(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _orch(tmp_path: Path) -> Orchestrator:
    state = WorkflowState(Path("/tmp/no.md"))
    o = Orchestrator(state)
    o._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
    return o


def _seed_running_entry(o: Orchestrator, issue: Issue, tmp_path: Path) -> None:
    o._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
    )


def _install_fake_backend(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    """Replace `symphony.orchestrator.build_backend` with a recording factory.

    Returns the list every constructed `_FakeBackend` is appended to so a
    test can assert call ordering across the (possibly multiple) backend
    instances built within a single `_run_agent_attempt` call.
    """
    instances: list[_FakeBackend] = []

    def _factory(init: Any) -> _FakeBackend:
        backend = _FakeBackend(init_id=len(instances))
        backend.calls.append(("factory", {"agent_kind": init.cfg.agent.kind}))
        instances.append(backend)
        return backend

    monkeypatch.setattr(orch_mod, "build_backend", _factory)
    return instances


def _install_state_sequence(
    monkeypatch: pytest.MonkeyPatch, states: list[str]
) -> None:
    """Walk `_refresh_issue_state` through a scripted state sequence.

    The first call returns an issue in `states[0]`, the second `states[1]`,
    and so on. Once the sequence is exhausted the worker exits via the
    inactive-state branch (we send `"Done"` last to terminate).
    """
    calls = {"i": 0}

    async def _fake_refresh(self, cfg, issue_id):  # noqa: ANN001 - test stub
        del self, cfg, issue_id
        idx = calls["i"]
        calls["i"] += 1
        next_state = states[idx] if idx < len(states) else "Done"
        return Issue(
            id="iss-1",
            identifier="MT-1",
            title="phase transition fixture",
            description=None,
            priority=2,
            state=next_state,
            blocked_by=(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _fake_refresh)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase_transition_rebuilds_backend_with_fresh_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    # Turn 1 finishes in "Todo"; refresh moves to "In Progress" → triggers
    # phase transition before turn 2 runs. After turn 2 the second refresh
    # returns "Done" so the worker exits naturally.
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # Two backends total: one for Todo, one for In Progress.
    assert len(instances) == 2
    # Original backend was stopped exactly once mid-loop (plus once more in
    # the finally block that targets the LATEST client). The original
    # instance therefore sees exactly one stop.
    original_stops = [c for c in instances[0].calls if c[0] == "stop"]
    assert len(original_stops) == 1

    # Both backends must have been driven through start → initialize →
    # start_session before any run_turn fires on them.
    for inst in instances:
        names = [c[0] for c in inst.calls]
        assert names.index("start") < names.index("initialize") < names.index(
            "start_session"
        )

    # Two distinct first-turn prompts captured — one per phase.
    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert first_prompts[0] != first_prompts[1]
    # The freshly-rendered prompt reflects the new state.
    assert "state=In Progress" in first_prompts[1]
    assert "state=Todo" in first_prompts[0]

    # Post-transition run_turn must NOT be flagged as a continuation —
    # the backend has no prior context, this is its true first turn.
    second_run = [c for c in instances[1].calls if c[0] == "run_turn"]
    # Exactly one turn per backend in the scripted state sequence
    # (Todo on first backend, In Progress → Done on second). A future
    # change that accidentally double-calls run_turn on the new backend
    # would still pass the [0] checks below — pin the count.
    assert len(second_run) == 1, f"expected one run_turn on second backend, got {len(second_run)}"
    assert second_run[0][1]["is_continuation"] is False
    # And the prompt sent on that run_turn equals the freshly rendered
    # first-turn prompt (not a build_continuation_prompt body).
    assert second_run[0][1]["prompt"] == first_prompts[1]


@pytest.mark.parametrize("agent_kind", sorted(SUPPORTED_AGENT_KINDS))
def test_run_agent_attempt_uses_ticket_agent_kind_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_kind: str
) -> None:
    cfg = _make_config(max_turns=5)
    issue = Issue(
        id="iss-1",
        identifier="MT-1",
        title="ticket-level backend",
        description=None,
        priority=2,
        state="Todo",
        agent_kind=agent_kind,
        blocked_by=(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert cfg.agent.kind == "codex"
    assert instances[0].calls[0] == ("factory", {"agent_kind": agent_kind})


def test_phase_transition_uses_stage_specific_prompt_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(
        max_turns=5,
        prompt_template="LEGACY {{ issue.state }}",
        prompts=PromptConfig(
            base_template="BASE {{ issue.identifier }}",
            stage_templates={
                "todo": "TODO ONLY {{ issue.state }}",
                "in progress": "IMPLEMENT ONLY {{ issue.state }}",
            },
        ),
    )
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert "BASE MT-1" in first_prompts[0]
    assert "TODO ONLY Todo" in first_prompts[0]
    assert "IMPLEMENT ONLY" not in first_prompts[0]
    assert "BASE MT-1" in first_prompts[1]
    assert "IMPLEMENT ONLY In Progress" in first_prompts[1]
    assert "TODO ONLY" not in first_prompts[1]
    assert "LEGACY" not in "\n".join(first_prompts)


def test_same_phase_does_not_restart_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=3)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    # Stay in Todo across turns 1 → 2, then exit by going inactive.
    _install_state_sequence(monkeypatch, ["Todo", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # Exactly one backend ever built when the state is unchanged.
    assert len(instances) == 1
    inst = instances[0]
    # Two run_turn calls: turn 1 is_continuation=False, turn 2 True.
    run_turns = [c for c in inst.calls if c[0] == "run_turn"]
    assert len(run_turns) == 2
    assert run_turns[0][1]["is_continuation"] is False
    assert run_turns[1][1]["is_continuation"] is True
    # Only the single `finally`-block stop is observed.
    stops = [c for c in inst.calls if c[0] == "stop"]
    assert len(stops) == 1


def test_worker_cleanup_uses_registered_running_issue_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must pop the original running slot even if a refreshed issue
    object carries a different tracker id.

    The TUI symptom is a card stuck in retrying with
    `worker_task_finished_without_cleanup`: the worker task completed, but
    `_on_worker_exit` was called with a key that did not match `_running`.
    """
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    o._loop = asyncio.new_event_loop()
    try:
        _seed_running_entry(o, issue, tmp_path)
        _install_fake_backend(monkeypatch)

        async def _refresh_with_different_id(self, cfg, issue_id):  # noqa: ANN001
            del self, cfg, issue_id
            return Issue(
                id="tracker-id-after-refresh",
                identifier="MT-1",
                title="phase transition fixture",
                description=None,
                priority=2,
                state="Done",
                blocked_by=(),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        monkeypatch.setattr(
            Orchestrator, "_refresh_issue_state", _refresh_with_different_id
        )

        o._loop.run_until_complete(o._run_agent_attempt(issue, attempt=None, cfg=cfg))
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        o._loop.close()

    assert issue.id not in o._running
    assert issue.id in o._retry
    assert o._retry[issue.id].error is None


def test_phase_transition_resets_session_id_on_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    # Pre-fill the bookkeeping fields so we can assert they got cleared.
    entry = o._running[issue.id]
    entry.thread_id = "thr-old"
    entry.session_id = "sess-old"
    entry.turn_id = "turn-old"

    captured: dict[str, Any] = {}

    async def _capture_run_turn(self_inst, *, prompt, is_continuation):  # noqa: ANN001
        # Mirror the original `_FakeBackend.run_turn` accounting so any
        # follow-up tests that monkeypatch + assert on `inst.calls` still
        # see the call. Otherwise this stub silently drops the record and
        # makes call-count assertions fragile to test ordering.
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        # On the second backend's first turn, snapshot the bookkeeping
        # fields BEFORE any further code runs — they must already be None.
        if self_inst.init_id == 1 and "snapshot" not in captured:
            running = o._running[issue.id]
            captured["snapshot"] = {
                "thread_id": running.thread_id,
                "session_id": running.session_id,
                "turn_id": running.turn_id,
            }

    # Re-bind run_turn on the fake to take a snapshot. Patch the method
    # on the class so each new instance picks it up.
    monkeypatch.setattr(_FakeBackend, "run_turn", _capture_run_turn)

    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Review", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert captured.get("snapshot") == {
        "thread_id": None,
        "session_id": None,
        "turn_id": None,
    }


def test_phase_transition_resets_token_high_water_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """High-water marks on `RunningEntry.last_reported_*_tokens` MUST reset
    when the backend is rebuilt. Otherwise `_apply_token_totals` computes
    `max(new - old_high, 0) = 0` against the new session's absolute totals
    and silently drops every token from the new phase until cumulative
    reporting overtakes the old mark. Cumulative `codex_*_tokens` are
    explicitly NOT reset — those are per-ticket lifetime counters."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    entry = o._running[issue.id]
    entry.last_reported_input_tokens = 5_000
    entry.last_reported_output_tokens = 3_000
    entry.last_reported_total_tokens = 8_000
    entry.codex_input_tokens = 5_000
    entry.codex_output_tokens = 3_000
    entry.codex_total_tokens = 8_000

    captured: dict[str, Any] = {}

    async def _snapshot_on_second(self_inst, *, prompt, is_continuation):  # noqa: ANN001
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        if self_inst.init_id == 1 and "snapshot" not in captured:
            running = o._running[issue.id]
            captured["snapshot"] = {
                "last_reported_input_tokens": running.last_reported_input_tokens,
                "last_reported_output_tokens": running.last_reported_output_tokens,
                "last_reported_total_tokens": running.last_reported_total_tokens,
                "codex_input_tokens": running.codex_input_tokens,
                "codex_output_tokens": running.codex_output_tokens,
                "codex_total_tokens": running.codex_total_tokens,
            }

    monkeypatch.setattr(_FakeBackend, "run_turn", _snapshot_on_second)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Review", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    snap = captured.get("snapshot")
    assert snap is not None, "second backend must have run a turn"
    # High-water marks reset so the new session's first usage report is
    # not compared against the old session's cumulative high.
    assert snap["last_reported_input_tokens"] == 0
    assert snap["last_reported_output_tokens"] == 0
    assert snap["last_reported_total_tokens"] == 0
    # Per-ticket cumulative counters intentionally preserved.
    assert snap["codex_input_tokens"] == 5_000
    assert snap["codex_output_tokens"] == 3_000
    assert snap["codex_total_tokens"] == 8_000


# ---------------------------------------------------------------------------
# Rewind detection — Review/QA → In Progress
# ---------------------------------------------------------------------------


def test_is_rewind_transition_pure_function() -> None:
    """Predicate covers the canonical rewind paths and rejects the rest."""
    from symphony.orchestrator import _is_rewind_transition

    # Canonical rewinds defined by WORKFLOW.md hard rules.
    assert _is_rewind_transition("review", "in progress") is True
    assert _is_rewind_transition("qa", "in progress") is True
    # Forward transitions are NEVER rewinds.
    assert _is_rewind_transition("todo", "explore") is False
    assert _is_rewind_transition("explore", "in progress") is False
    assert _is_rewind_transition("in progress", "review") is False
    assert _is_rewind_transition("review", "qa") is False
    assert _is_rewind_transition("qa", "learn") is False
    # Same-state self-loops are not transitions at all.
    assert _is_rewind_transition("in progress", "in progress") is False
    # Backward jumps to states OTHER than In Progress are out of scope.
    assert _is_rewind_transition("review", "explore") is False
    assert _is_rewind_transition("qa", "explore") is False


def test_review_rewind_renders_is_rewind_in_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the state goes Review → In Progress mid-worker, the rebuilt
    backend's first-turn prompt must carry `is_rewind=True` so WORKFLOW
    templates can branch the retry preamble."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Review")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)

    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2, "rewind must trigger a backend rebuild"
    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    # Initial prompt was rendered before any phase transition was known.
    assert "rewind=False" in first_prompts[0]
    # Post-rewind prompt carries the True signal — the WORKFLOW template
    # can now branch its retry preamble on it.
    assert "rewind=True" in first_prompts[1]
    assert "state=In Progress" in first_prompts[1]


def test_forward_transition_does_not_set_is_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Todo → Explore (forward) must NOT flip is_rewind."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)

    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Explore", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert "rewind=False" in first_prompts[0]
    assert "rewind=False" in first_prompts[1]


def test_run_agent_attempt_handles_orphaned_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker survives a missing `_running` entry instead of raising KeyError.

    Regression for the OLV-002 cascade where `self._running[running_issue_id]`
    direct-subscript access would raise `KeyError('OLV-002')` if a race popped
    the entry between dispatch and the first-await completion. The fix routes
    a missing entry to the `orphaned` outcome so the outer finally pops
    cleanly (None pop) and no `worker_task_finished_without_cleanup` cascade
    fires.
    """
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    # Deliberately do NOT seed a running entry — simulate the race where
    # something popped it before `_run_agent_attempt` resumed from its
    # first await.
    instances = _install_fake_backend(monkeypatch)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # Orphan path returns before `build_backend` runs.
    assert len(instances) == 0
    assert issue.id not in o._running


def test_dispatch_registers_running_entry_before_eager_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 3.12 eager tasks may run the worker before `_dispatch` returns."""

    eager_factory = getattr(asyncio, "eager_task_factory", None)
    if eager_factory is None:
        pytest.skip("asyncio.eager_task_factory requires Python 3.12+")

    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    loop = asyncio.new_event_loop()
    loop.set_task_factory(eager_factory)
    o._loop = loop
    try:
        async def _drive_dispatch() -> None:
            o._dispatch(issue, cfg, attempt=None)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drive_dispatch())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()

    assert issue.id not in o._running
    assert issue.id in o._retry
    assert o._retry[issue.id].error is None


def test_done_callback_ignores_stale_task_for_replaced_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `worker_task.add_done_callback` from a previously-finished worker
    must not pop a freshly-dispatched entry that happens to share the same
    issue id.

    Why: `_on_worker_exit` yields once at `await self._notify_observers()`.
    The continuation retry timer is only 1s away (`CONTINUATION_RETRY_DELAY_MS`),
    so a race exists where a new `_dispatch` installs a fresh entry under
    the same key BEFORE the original worker's task object reaches `done`.
    When that stale task's callback finally fires, it must verify the
    registered entry still belongs to it. Symptom is `state=Review,
    runtime=retrying, error=worker_task_finished_without_cleanup` because
    the stale callback ejects the live entry.
    """

    o = _orch(tmp_path)
    issue = _make_issue(state="Review")

    exit_calls: list[tuple[str, str, str | None]] = []

    async def _track_exit(self_inst, issue_id, reason, error):  # noqa: ANN001
        del self_inst
        exit_calls.append((issue_id, reason, error))

    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _track_exit)

    loop = asyncio.new_event_loop()
    o._loop = loop
    try:
        # Build a real done-but-not-cancelled task to mimic a worker that
        # ran its `finally` cleanly. Its entry was already popped by the
        # legitimate cleanup path.
        async def _ok() -> None:
            return None

        task1 = loop.create_task(_ok())
        loop.run_until_complete(task1)
        assert task1.done() and not task1.cancelled() and task1.exception() is None

        # Race: a fresh dispatch installs entry2 under the same key. We use
        # a never-running placeholder task so we control its lifecycle.
        async def _pending() -> None:
            await asyncio.sleep(3600)

        async def _race_window() -> None:
            entry2 = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=1,
                worker_task=loop.create_task(_pending()),
                workspace_path=tmp_path,
            )
            o._running[issue.id] = entry2
            try:
                # Stale callback for the already-finished task1 fires from
                # inside a running loop, exactly mirroring the production
                # `add_done_callback` invocation context.
                o._on_worker_task_done(issue.id, task1)
                # Drain any task the callback may have queued so a buggy
                # implementation has a chance to clobber `_running`.
                await asyncio.sleep(0)
                await asyncio.sleep(0)

                assert o._running.get(issue.id) is entry2
                assert exit_calls == [], (
                    "stale done-callback wrongly fired _on_worker_exit: "
                    f"{exit_calls!r}"
                )
            finally:
                entry2.worker_task.cancel()
                await asyncio.gather(entry2.worker_task, return_exceptions=True)

        loop.run_until_complete(_race_window())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()


def test_done_callback_ignores_task_already_in_exit_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker finishing while its `finally` cleanup is already underway must
    not be reclassified as `worker_task_finished_without_cleanup`.

    Cancellation can land on `_run_agent_attempt` while it is awaiting exit
    cleanup. The cleanup path is legitimate; the done callback is only a
    fallback for tasks whose coroutine never reached that path at all.
    """

    o = _orch(tmp_path)
    issue = _make_issue(state="Review")

    exit_calls: list[tuple[str, str, str | None]] = []

    async def _track_exit(self_inst, issue_id, reason, error):  # noqa: ANN001
        del self_inst
        exit_calls.append((issue_id, reason, error))

    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _track_exit)

    loop = asyncio.new_event_loop()
    o._loop = loop
    try:
        async def _ok() -> None:
            return None

        task = loop.create_task(_ok())
        loop.run_until_complete(task)
        assert task.done() and not task.cancelled() and task.exception() is None

        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=1,
            worker_task=task,
            workspace_path=tmp_path,
        )
        entry.exit_started_at = datetime.now(timezone.utc)
        o._running[issue.id] = entry

        async def _drive_callback() -> None:
            o._on_worker_task_done(issue.id, task)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drive_callback())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()

    assert o._running.get(issue.id) is entry
    assert exit_calls == []
