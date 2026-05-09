"""Backwards-compatibility shim.

The original `symphony.agent` module hard-coded the Codex JSON-RPC client.
After the multi-agent refactor, the agent layer lives under
`symphony.backends.*`. This module re-exports the canonical names so existing
imports (tests, downstream tooling) keep working.
"""

from __future__ import annotations

from .backends import (  # noqa: F401
    EVENT_APPROVAL_AUTO_APPROVED,
    EVENT_MALFORMED,
    EVENT_NOTIFICATION,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_STARTUP_FAILED,
    EVENT_TURN_CANCELLED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_ENDED_WITH_ERROR,
    EVENT_TURN_FAILED,
    EVENT_TURN_INPUT_REQUIRED,
    EVENT_UNSUPPORTED_TOOL_CALL,
    AgentBackend,
    BackendInit,
    ToolDescriptor,
    TurnResult,
    build_backend,
)
from .backends.codex import (  # noqa: F401
    CodexAppServerBackend,
    linear_graphql_tool,
)


# Legacy class name retained for downstream code that imported the original
# `CodexAppServerClient` symbol.
CodexAppServerClient = CodexAppServerBackend
