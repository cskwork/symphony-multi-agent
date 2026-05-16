"""Claude Code token accounting regressions."""

from __future__ import annotations

from pathlib import Path

from symphony.backends import BackendInit
from symphony.backends.claude_code import ClaudeCodeBackend
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


def _make_config(tmp_path: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=tmp_path / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=tmp_path,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=tmp_path / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="claude",
            max_concurrent_agents=1,
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
        prompt_template="hi",
    )


def test_claude_usage_separates_cache_input_tokens(tmp_path: Path) -> None:
    backend = ClaudeCodeBackend(
        BackendInit(
            cfg=_make_config(tmp_path),
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=lambda _event: None,  # type: ignore[arg-type]
        )
    )

    backend._update_usage_absolute(
        {
            "input_tokens": 10,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
            "output_tokens": 4,
        }
    )

    assert backend.latest_usage == {
        "input_tokens": 10,
        "cache_input_tokens": 50,
        "output_tokens": 4,
        "total_tokens": 64,
    }
