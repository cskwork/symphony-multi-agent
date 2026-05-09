"""Agent backend abstraction.

Symphony was originally hardwired to the Codex app-server JSON-RPC protocol.
This package introduces an `AgentBackend` Protocol so the orchestrator can
drive any coding-agent CLI (Codex, Claude Code, Gemini) behind one interface.

Each backend owns its own subprocess lifecycle. The Codex backend keeps the
single long-running app-server connection that speaks JSON-RPC over stdio.
The Claude and Gemini backends spawn one subprocess per turn — Claude uses
`claude -p --output-format stream-json`, Gemini uses `gemini -p` one-shot.

Normalized event vocabulary is shared across backends (see `events.py` style
constants below). The orchestrator only consumes these normalized event names
plus an `AgentEvent`-shaped dict, so it never sees backend-specific protocol
details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ..errors import ConfigValidationError
from ..workflow import ServiceConfig


# Normalized event vocabulary — every backend emits these strings only.
EVENT_SESSION_STARTED = "session_started"
EVENT_STARTUP_FAILED = "startup_failed"
EVENT_TURN_COMPLETED = "turn_completed"
EVENT_TURN_FAILED = "turn_failed"
EVENT_TURN_CANCELLED = "turn_cancelled"
EVENT_TURN_ENDED_WITH_ERROR = "turn_ended_with_error"
EVENT_TURN_INPUT_REQUIRED = "turn_input_required"
EVENT_APPROVAL_AUTO_APPROVED = "approval_auto_approved"
EVENT_UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
EVENT_NOTIFICATION = "notification"
EVENT_OTHER_MESSAGE = "other_message"
EVENT_MALFORMED = "malformed"


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TurnResult:
    status: str
    turn_id: str | None
    last_message: str = ""
    error: str | None = None


@dataclass
class ToolDescriptor:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class BackendInit:
    """Constructor inputs every backend needs.

    Keeping construction parameter list as a dataclass keeps the factory and
    tests honest — adding a new field forces every backend to acknowledge it.
    """

    cfg: ServiceConfig
    cwd: Path
    workspace_root: Path
    on_event: EventCallback
    client_tools: list[ToolDescriptor] = field(default_factory=list)


@runtime_checkable
class AgentBackend(Protocol):
    """Lifecycle contract for a coding-agent CLI driver.

    Order of calls from the orchestrator:
        await b.start()
        await b.initialize()
        await b.start_session(initial_prompt=..., issue_title=...)
        for each turn:
            await b.run_turn(prompt=..., is_continuation=...)
        await b.stop()

    Backends MUST emit normalized events through `on_event` for at least:
    - `session_started` once a session id is known,
    - `turn_completed` / `turn_failed` / `turn_cancelled` per turn outcome.

    Token + rate-limit telemetry is reported by the latest_* properties so the
    orchestrator can roll up totals without reaching into protocol payloads.
    """

    async def start(self) -> None: ...

    async def initialize(self) -> dict[str, Any]: ...

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str: ...

    async def run_turn(
        self, *, prompt: str, is_continuation: bool
    ) -> TurnResult: ...

    async def stop(self) -> None: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def pid(self) -> int | None: ...

    @property
    def latest_usage(self) -> dict[str, int]: ...

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None: ...


def build_backend(init: BackendInit) -> AgentBackend:
    """Factory: pick a concrete backend by `agent.kind`."""
    kind = init.cfg.agent.kind
    if kind == "codex":
        from .codex import CodexAppServerBackend

        return CodexAppServerBackend(init)
    if kind == "claude":
        from .claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(init)
    if kind == "gemini":
        from .gemini import GeminiBackend

        return GeminiBackend(init)
    raise ConfigValidationError(
        f"unknown agent.kind {kind!r}; expected codex, claude, or gemini"
    )
