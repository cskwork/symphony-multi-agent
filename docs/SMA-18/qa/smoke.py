"""SMA-18 real-pi smoke.

Drives PiBackend directly for one turn; prints the four token buckets so we
can verify the 4-bucket contract end-to-end against a real pi CLI.

Asserts the SMA-18 invariant:
    total_tokens == input_tokens + output_tokens + cache_input_tokens
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Source-of-truth note for future operators:
#
# This script originally ran inside a symphony worktree at
# /Users/danny/symphony_workspaces/SMA-18 with `WORKSPACE` hard-coded
# to that path. The orchestrator reaped that worktree once the ticket
# transitioned to Done — taking the worktree-local src/ and
# uncommitted code edits with it (see Decision log entry 2026-05-10
# in llm-wiki/production-pipeline.md). The SMA-18 code now lives at
# the PARA-shared src/ directly. We import via the editable install
# wired by .venv/bin/python and assert the loaded module path so any
# future operator running this script knows where the code came from.
from symphony.backends import BackendInit  # noqa: E402
from symphony.backends import pi as _pi_mod  # noqa: E402

assert _pi_mod.__file__ and "cache_input_tokens" in Path(_pi_mod.__file__).read_text(), (
    f"loaded pi from {_pi_mod.__file__}; SMA-18 4-bucket changes missing"
)
print(f"loaded pi.py from: {_pi_mod.__file__}", flush=True)

from symphony.backends.pi import PiBackend  # noqa: E402
from symphony.workflow import (  # noqa: E402
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


def _make_cfg(workspace_root: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=workspace_root / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=workspace_root,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            board_root=workspace_root / "kanban",
            active_states=("Todo", "In Progress"),
            terminal_states=("Done", "Cancelled"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="pi",
            max_concurrent_agents=1,
            max_turns=1,
            max_retry_backoff_ms=60_000,
            max_concurrent_agents_by_state={},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="ignored",
    )


async def _noop_event(payload: dict) -> None:
    return None


async def main() -> int:
    qa_dir = Path(__file__).resolve().parent
    cwd = qa_dir / "smoke-cwd"
    cwd.mkdir(exist_ok=True)
    cfg = _make_cfg(qa_dir)
    backend = PiBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=qa_dir, on_event=_noop_event)
    )
    await backend.start()
    try:
        await backend.start_session(initial_prompt="hi", issue_title=None)
        await backend.run_turn(
            prompt="Say the single word 'pong' and stop.",
            is_continuation=False,
        )
    finally:
        await backend.stop()
    usage = backend.latest_usage
    print(json.dumps({"session_id": backend.session_id, "usage": usage}, indent=2, sort_keys=True))
    # SMA-18 invariant
    inv_lhs = usage["total_tokens"]
    inv_rhs = usage["input_tokens"] + usage["output_tokens"] + usage["cache_input_tokens"]
    assert inv_lhs == inv_rhs, f"invariant violated: total={inv_lhs} vs sum={inv_rhs}"
    # Sanity: all four keys exist and are ints.
    for k in ("input_tokens", "output_tokens", "cache_input_tokens", "total_tokens"):
        assert k in usage, f"missing bucket: {k}"
        assert isinstance(usage[k], int), f"non-int bucket: {k}={usage[k]!r}"
    print("OK: 4 buckets present; invariant total = in + out + cache holds")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
