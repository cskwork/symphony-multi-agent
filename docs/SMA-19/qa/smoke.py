"""SMA-19 memo-word smoke: drive `GeminiBackend` directly for two turns,
verify turn-2 has access to turn-1 context.

Run: .venv/bin/python docs/SMA-19/qa/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# `docs/SMA-19/` is hardlinked into a sibling repo on this machine, so
# `Path(__file__).resolve()` can land in the wrong tree. Pin to the
# workspace where the ticket is being executed.
sys.path.insert(0, "/Users/danny/symphony_workspaces/SMA-19/src")

from symphony.backends import BackendInit, build_backend  # noqa: E402
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
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=workspace_root / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="gemini",
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
            turn_timeout_ms=180_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
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


async def main() -> int:
    events: list[dict] = []

    async def on_event(payload: dict) -> None:
        events.append(payload)
        # Compact echo for the QA log
        ev = payload.get("event")
        usage = payload.get("usage") or {}
        sys.stderr.write(
            f"  event={ev} usage={usage} pid={payload.get('agent_pid')}\n"
        )
        sys.stderr.flush()

    with TemporaryDirectory(prefix="sma19-smoke-") as tmp:
        ws_root = Path(tmp).resolve()
        cwd = ws_root / "ws"
        cwd.mkdir()
        cfg = _make_cfg(ws_root)
        backend = build_backend(
            BackendInit(
                cfg=cfg, cwd=cwd, workspace_root=ws_root, on_event=on_event
            )
        )
        await backend.start()
        sid = await backend.start_session(
            initial_prompt="hi", issue_title="SMA-19 smoke"
        )
        print(f"\n=== minted session_id: {sid} ===\n")

        memo = "okra"
        prompt1 = (
            f"Memorize the secret memo word: '{memo}'. "
            f"Reply with exactly: 'memo received'. Nothing else."
        )
        print(f">>> turn 1 prompt: {prompt1}")
        r1 = await backend.run_turn(prompt=prompt1, is_continuation=False)
        print(f"<<< turn 1 last_message: {r1.last_message!r}")
        usage_after_t1 = backend.latest_usage
        print(f"    usage after turn 1: {usage_after_t1}")
        assert backend.session_id == sid, "session id drifted between turns"

        prompt2 = (
            "What was the secret memo word I asked you to memorize? "
            "Reply with the single word and nothing else."
        )
        print(f"\n>>> turn 2 prompt: {prompt2}")
        r2 = await backend.run_turn(prompt=prompt2, is_continuation=True)
        print(f"<<< turn 2 last_message: {r2.last_message!r}")
        usage_after_t2 = backend.latest_usage
        print(f"    usage after turn 2: {usage_after_t2}")
        assert backend.session_id == sid, "session id drifted between turns"

        await backend.stop()

        # Memo-word check
        if memo.lower() in r2.last_message.lower():
            print(f"\nPASS: turn 2 recalled memo word '{memo}' "
                  f"(in {r2.last_message!r}).")
        else:
            print(f"\nFAIL: turn 2 did NOT recall memo word '{memo}' "
                  f"(got {r2.last_message!r}).")
            return 1

        # Three-bucket invariant
        for label, u in [("turn1", usage_after_t1), ("turn2", usage_after_t2)]:
            assert u["total_tokens"] == u["input_tokens"] + u["output_tokens"], (
                f"three-bucket invariant broken at {label}: {u}"
            )
            assert u["total_tokens"] > 0, f"no tokens accumulated by {label}"
        print("PASS: three-bucket invariant holds and totals are non-zero.")
        print(f"\nSession id stable across turns: {sid}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
