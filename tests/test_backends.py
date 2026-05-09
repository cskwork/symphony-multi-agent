"""Unit tests for the multi-agent backend layer.

These tests are deterministic — they exercise config wiring and event
normalization without spawning real CLI subprocesses. Subprocess-driven
behavior (codex app-server, claude -p stream-json, gemini -p) is left to
real-integration runs since CI cannot guarantee those binaries.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    build_backend,
)
from symphony.backends.claude_code import ClaudeCodeBackend, _extract_text
from symphony.backends.codex import _normalize_event_name
from symphony.backends.gemini import GeminiBackend
from symphony.errors import ConfigValidationError
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
)


def _make_cfg(kind: str, *, workspace_root: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=workspace_root / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=workspace_root,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=workspace_root / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind=kind,
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


def _noop_event(_: dict) -> "asyncio.Future[None]":
    fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


def test_factory_returns_codex_backend(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    # Class import is deferred inside the factory; check by name.
    assert backend.__class__.__name__ == "CodexAppServerBackend"


def test_factory_returns_claude_backend(tmp_path: Path) -> None:
    cfg = _make_cfg("claude", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    assert isinstance(backend, ClaudeCodeBackend)


def test_factory_returns_gemini_backend(tmp_path: Path) -> None:
    cfg = _make_cfg("gemini", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    assert isinstance(backend, GeminiBackend)


def test_factory_rejects_unknown_kind(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    bogus_cfg = ServiceConfig(
        workflow_path=cfg.workflow_path,
        poll_interval_ms=cfg.poll_interval_ms,
        workspace_root=cfg.workspace_root,
        tracker=cfg.tracker,
        hooks=cfg.hooks,
        agent=AgentConfig(
            kind="ollama",  # not in SUPPORTED_AGENT_KINDS
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=1,
            max_concurrent_agents_by_state={},
        ),
        codex=cfg.codex,
        claude=cfg.claude,
        gemini=cfg.gemini,
        server=cfg.server,
        prompt_template="hi",
    )
    cwd = tmp_path / "ws"
    cwd.mkdir()
    with pytest.raises(ConfigValidationError):
        build_backend(
            BackendInit(cfg=bogus_cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
        )


def test_codex_event_name_normalization() -> None:
    assert _normalize_event_name("thread/turn/completed") == EVENT_TURN_COMPLETED
    assert _normalize_event_name("thread/turn/failed") == EVENT_TURN_FAILED
    assert _normalize_event_name("approval/requested") == "approval_auto_approved"
    assert _normalize_event_name("") == EVENT_OTHER_MESSAGE
    assert _normalize_event_name("anything/else") == EVENT_OTHER_MESSAGE


def test_claude_extract_text_picks_last_text_block() -> None:
    msg = {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "tool_use", "name": "Edit"},
            {"type": "text", "text": "final answer"},
        ]
    }
    assert _extract_text(msg) == "final answer"


def test_claude_extract_text_handles_missing_content() -> None:
    assert _extract_text({}) == ""
    assert _extract_text({"content": "not-a-list"}) == ""


def test_claude_usage_accumulates_across_turns(tmp_path: Path) -> None:
    cfg = _make_cfg("claude", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = ClaudeCodeBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    backend._update_usage_absolute(
        {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 40}
    )
    backend._update_usage_absolute(
        {"input_tokens": 10, "cache_creation_input_tokens": 5, "output_tokens": 20}
    )
    usage = backend.latest_usage
    # First turn: 100 + 50 + 0 = 150 in, 40 out
    # Second turn: 10 + 0 + 5 = 15 in, 20 out
    # Cumulative: 165 in, 60 out, 225 total
    assert usage["input_tokens"] == 165
    assert usage["output_tokens"] == 60
    assert usage["total_tokens"] == 225


def test_gemini_session_id_synthesized(tmp_path: Path) -> None:
    cfg = _make_cfg("gemini", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = GeminiBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    asyncio.get_event_loop().run_until_complete(
        backend.start_session(initial_prompt="hi", issue_title="Fix login")
    )
    sid = backend.session_id
    assert sid is not None
    assert sid.startswith("gemini-")


def test_workflow_config_validates_kind(tmp_path: Path) -> None:
    from symphony.workflow import build_service_config, parse_workflow_text

    text = """---
tracker: {kind: file, board_root: ./board}
agent: {kind: claude}
---
prompt body
"""
    wf = parse_workflow_text(text, tmp_path / "WORKFLOW.md")
    cfg = build_service_config(wf)
    assert cfg.agent.kind == "claude"
    assert cfg.claude.command  # default applied


def test_workflow_config_rejects_unknown_kind(tmp_path: Path) -> None:
    from symphony.workflow import build_service_config, parse_workflow_text

    text = """---
tracker: {kind: file, board_root: ./board}
agent: {kind: bogus}
---
"""
    wf = parse_workflow_text(text, tmp_path / "WORKFLOW.md")
    with pytest.raises(ConfigValidationError):
        build_service_config(wf)
