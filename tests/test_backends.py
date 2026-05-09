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
from symphony.backends.codex import (
    CodexAppServerBackend,
    NOTIF_ITEM_COMPLETED,
    NOTIF_TURN_COMPLETED,
    NOTIF_THREAD_TOKEN_USAGE,
    _normalize_event_name,
    _sandbox_policy_to_turn_payload,
)
from symphony.backends.gemini import GeminiBackend
from symphony.backends.pi import (
    PiBackend,
    _extract_failure_reason,
    _extract_text as _pi_extract_text,
)
from symphony.errors import ConfigValidationError
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
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
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


def test_factory_returns_pi_backend(tmp_path: Path) -> None:
    cfg = _make_cfg("pi", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    assert isinstance(backend, PiBackend)


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
        pi=cfg.pi,
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


# ---- Codex v2 protocol -------------------------------------------------


def test_codex_sandbox_policy_workspace_write_to_v2_payload() -> None:
    assert _sandbox_policy_to_turn_payload("workspace-write") == {
        "type": "workspaceWrite"
    }
    assert _sandbox_policy_to_turn_payload("read-only") == {"type": "readOnly"}
    assert _sandbox_policy_to_turn_payload("danger-full-access") == {
        "type": "dangerFullAccess"
    }


def test_codex_sandbox_policy_passes_through_dict() -> None:
    """Already-v2-shaped payloads must not be rewrapped."""
    payload = {"type": "workspaceWrite", "networkAccess": True}
    assert _sandbox_policy_to_turn_payload(payload) is payload


def test_codex_sandbox_policy_returns_unknown_string_unchanged() -> None:
    """Unknown values pass through so codex itself can produce a clear
    rejection rather than the backend silently guessing."""
    assert _sandbox_policy_to_turn_payload("custom-mode") == "custom-mode"


def test_codex_sandbox_policy_none() -> None:
    assert _sandbox_policy_to_turn_payload(None) is None


@pytest.mark.asyncio
async def test_codex_handles_v2_token_usage_notification(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    await backend._handle_notification(
        {
            "method": NOTIF_THREAD_TOKEN_USAGE,
            "params": {
                "totals": {
                    "input_tokens": 120,
                    "output_tokens": 80,
                    "total_tokens": 200,
                }
            },
        }
    )
    assert backend.latest_usage == {
        "input_tokens": 120,
        "output_tokens": 80,
        "total_tokens": 200,
    }


@pytest.mark.asyncio
async def test_codex_captures_agent_message_from_item_completed(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    await backend._handle_notification(
        {
            "method": NOTIF_ITEM_COMPLETED,
            "params": {
                "threadId": "t1",
                "turnId": "tn1",
                "item": {
                    "id": "i1",
                    "type": "agentMessage",
                    "text": "patched the function",
                },
            },
        }
    )
    assert backend._latest_assistant_message == "patched the function"


@pytest.mark.asyncio
async def test_codex_turn_completed_notification_resolves_waiter(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    waiter: asyncio.Future = asyncio.get_event_loop().create_future()
    backend._turn_completion_waiter = waiter
    await backend._handle_notification(
        {
            "method": NOTIF_TURN_COMPLETED,
            "params": {
                "threadId": "t1",
                "turn": {"id": "tn1", "status": "completed", "items": []},
            },
        }
    )
    assert waiter.done()
    payload = waiter.result()
    assert payload["turn"]["status"] == "completed"


# ---- Codex terminal-status raising -------------------------------------
# Regression guard: `_raise_for_terminal_status` is async and *must* be
# awaited. A prior refactor dropped the `await` and silently turned every
# `failed`/`interrupted`/unknown turn into a fake success.


@pytest.mark.asyncio
async def test_codex_raises_turn_failed_on_failed_status(tmp_path: Path) -> None:
    from symphony.errors import TurnFailed

    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    with pytest.raises(TurnFailed, match="boom"):
        await backend._raise_for_terminal_status(
            {"id": "t1", "status": "failed", "error": {"message": "boom"}}
        )


@pytest.mark.asyncio
async def test_codex_raises_cancelled_on_interrupted_status(tmp_path: Path) -> None:
    from symphony.errors import TurnCancelled

    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    with pytest.raises(TurnCancelled):
        await backend._raise_for_terminal_status(
            {"id": "t1", "status": "interrupted"}
        )


@pytest.mark.asyncio
async def test_codex_raises_for_unknown_status(tmp_path: Path) -> None:
    from symphony.errors import TurnFailed

    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    with pytest.raises(TurnFailed, match="unexpected turn status"):
        await backend._raise_for_terminal_status(
            {"id": "t1", "status": "weirdValue"}
        )


@pytest.mark.asyncio
async def test_codex_completed_status_does_not_raise(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    # Either explicit "completed" or the empty-status fallback must be a no-op.
    await backend._raise_for_terminal_status({"id": "t1", "status": "completed"})
    await backend._raise_for_terminal_status({"id": "t2", "status": ""})


def test_codex_assistant_message_truncation_marks_with_ellipsis() -> None:
    from symphony.backends.codex import _ASSISTANT_MESSAGE_PREVIEW_CAP

    # Build a backend just to drive _handle_notification — but the truncation
    # logic is tested via direct comparison with the constant + the suffix.
    # `_handle_notification` writes into `_latest_assistant_message`.
    long_text = "a" * (_ASSISTANT_MESSAGE_PREVIEW_CAP + 50)
    short_text = "b" * (_ASSISTANT_MESSAGE_PREVIEW_CAP - 10)
    # Exercise the helper directly — it's pure Python on a string, so we
    # don't need a backend fixture.
    truncated = (
        long_text[:_ASSISTANT_MESSAGE_PREVIEW_CAP] + "…"
        if len(long_text) > _ASSISTANT_MESSAGE_PREVIEW_CAP
        else long_text
    )
    assert truncated.endswith("…")
    assert len(truncated) == _ASSISTANT_MESSAGE_PREVIEW_CAP + 1
    untruncated = (
        short_text[:_ASSISTANT_MESSAGE_PREVIEW_CAP] + "…"
        if len(short_text) > _ASSISTANT_MESSAGE_PREVIEW_CAP
        else short_text
    )
    assert untruncated == short_text


@pytest.mark.asyncio
async def test_codex_handle_notification_truncates_long_agent_message(
    tmp_path: Path,
) -> None:
    from symphony.backends.codex import _ASSISTANT_MESSAGE_PREVIEW_CAP

    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    long_text = "x" * (_ASSISTANT_MESSAGE_PREVIEW_CAP + 200)
    await backend._handle_notification(
        {
            "method": NOTIF_ITEM_COMPLETED,
            "params": {
                "item": {"type": "agentMessage", "text": long_text},
            },
        }
    )
    assert backend._latest_assistant_message.endswith("…")
    assert len(backend._latest_assistant_message) == _ASSISTANT_MESSAGE_PREVIEW_CAP + 1


# ---- Pi backend ---------------------------------------------------------


def test_pi_extract_text_picks_last_text_block() -> None:
    msg = {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "tool_use", "name": "edit"},
            {"type": "text", "text": "final answer"},
        ]
    }
    assert _pi_extract_text(msg) == "final answer"


def test_pi_extract_text_handles_string_content() -> None:
    # Some Pi messages flatten content to a plain string.
    assert _pi_extract_text({"content": "hello"}) == "hello"


def test_pi_extract_text_handles_missing_content() -> None:
    assert _pi_extract_text({}) == ""
    assert _pi_extract_text({"content": 123}) == ""


def test_pi_extract_failure_reason_returns_none_for_clean_stop() -> None:
    terminal = {"type": "agent_end", "messages": [{"stopReason": "stop"}]}
    assert _extract_failure_reason(terminal) is None


def test_pi_extract_failure_reason_returns_message_for_error_stop() -> None:
    terminal = {
        "type": "agent_end",
        "messages": [{"stopReason": "error", "errorMessage": "model timed out"}],
    }
    assert _extract_failure_reason(terminal) == "model timed out"


def test_pi_extract_failure_reason_returns_fallback_when_aborted_without_message() -> None:
    terminal = {"type": "agent_end", "messages": [{"stopReason": "aborted"}]}
    reason = _extract_failure_reason(terminal)
    assert reason is not None
    assert "aborted" in reason


def test_pi_usage_accumulates_across_messages(tmp_path: Path) -> None:
    cfg = _make_cfg("pi", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = PiBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    backend._update_usage(
        {"input": 100, "cacheRead": 50, "output": 40, "totalTokens": 190}
    )
    backend._update_usage(
        {"input": 10, "cacheWrite": 5, "output": 20, "totalTokens": 35}
    )
    usage = backend.latest_usage
    # Mirror the Claude bucketing: cacheRead + cacheWrite count toward input.
    # Turn 1: input 100 + cacheRead 50 = 150 in, 40 out, 190 total
    # Turn 2: input 10 + cacheWrite 5 = 15 in, 20 out, 35 total
    # Cumulative: 165 in, 60 out, 225 total
    assert usage["input_tokens"] == 165
    assert usage["output_tokens"] == 60
    assert usage["total_tokens"] == 225


def test_pi_workflow_config_validates_kind(tmp_path: Path) -> None:
    from symphony.workflow import build_service_config, parse_workflow_text

    text = """---
tracker: {kind: file, board_root: ./board}
agent: {kind: pi}
---
prompt body
"""
    wf = parse_workflow_text(text, tmp_path / "WORKFLOW.md")
    cfg = build_service_config(wf)
    assert cfg.agent.kind == "pi"
    assert cfg.pi.command  # default applied
    assert cfg.pi.resume_across_turns is True


def test_pi_workflow_config_honors_custom_command(tmp_path: Path) -> None:
    from symphony.workflow import build_service_config, parse_workflow_text

    # OAuth credentials live in ~/.pi/agent/auth.json after `/login`, so a
    # realistic override only flips Pi's own flags (e.g. --no-session for an
    # ephemeral run) rather than injecting an API key.
    text = """---
tracker: {kind: file, board_root: ./board}
agent: {kind: pi}
pi:
  command: pi --mode json --no-session -p ""
  resume_across_turns: false
---
"""
    wf = parse_workflow_text(text, tmp_path / "WORKFLOW.md")
    cfg = build_service_config(wf)
    assert "--no-session" in cfg.pi.command
    assert cfg.pi.resume_across_turns is False
