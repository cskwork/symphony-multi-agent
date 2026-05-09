"""Mock Codex app-server — for demos and integration tests.

Speaks the same JSON-RPC line protocol as `agent.py` expects, but does no
real work. It simulates a turn that takes a configurable amount of time and
emits periodic token-usage notifications so the dashboard shows changing
state.

Run via:

    python -m symphony.mock_codex

Environment variables:

    SYMPHONY_MOCK_TURN_SECONDS         total turn duration (default 12)
    SYMPHONY_MOCK_TICK_SECONDS         token-usage tick interval (default 2)
    SYMPHONY_MOCK_TOKENS_PER_TICK      tokens added per tick (default 250)
    SYMPHONY_MOCK_FAIL_EVERY_N_TURNS   make the Nth turn fail (default 0=never)
    SYMPHONY_MOCK_MAX_TURNS            stop accepting turns after N (default 0=unlimited)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


TURN_SECONDS = _env_float("SYMPHONY_MOCK_TURN_SECONDS", 12.0)
TICK_SECONDS = _env_float("SYMPHONY_MOCK_TICK_SECONDS", 2.0)
TOKENS_PER_TICK = _env_int("SYMPHONY_MOCK_TOKENS_PER_TICK", 250)
FAIL_EVERY_N = _env_int("SYMPHONY_MOCK_FAIL_EVERY_N_TURNS", 0)
MAX_TURNS = _env_int("SYMPHONY_MOCK_MAX_TURNS", 0)


class _Stdio:
    """Cross-platform async wrapper around blocking stdio.

    Uses ``run_in_executor`` for stdin reads and stdout flushes so we don't
    rely on ``loop.connect_read_pipe(sys.stdin)`` — that path fails on Windows
    under ``ProactorEventLoop`` with ``OSError: [WinError 6]`` when the
    interpreter's stdin handle is a redirected anonymous pipe.
    """

    def __init__(self) -> None:
        self._stdin = sys.stdin.buffer
        self._stdout = sys.stdout.buffer

    async def start(self) -> None:
        return None

    async def readline(self) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._stdin.readline)

    def write_json(self, obj: dict[str, Any]) -> None:
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._stdout.write(line)
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def drain(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._stdout.flush)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _log(msg: str, **fields: Any) -> None:
    """Diagnostics on stderr only (Symphony separates stderr from protocol)."""
    parts = [f"[mock-codex] {msg}"]
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    print(" ".join(parts), file=sys.stderr, flush=True)


async def _emit_token_progress(
    io: _Stdio, totals: dict[str, int], duration: float
) -> dict[str, int]:
    """Emit periodic tokenUsage notifications for `duration` seconds."""
    elapsed = 0.0
    while elapsed < duration:
        sleep_s = min(TICK_SECONDS, duration - elapsed)
        await asyncio.sleep(sleep_s)
        elapsed += sleep_s
        totals["input_tokens"] += random.randint(
            int(TOKENS_PER_TICK * 0.4), int(TOKENS_PER_TICK * 0.6)
        )
        totals["output_tokens"] += random.randint(
            int(TOKENS_PER_TICK * 0.2), int(TOKENS_PER_TICK * 0.6)
        )
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        io.write_json(
            {
                "jsonrpc": "2.0",
                "method": "thread/tokenUsage/updated",
                "params": {"totals": dict(totals)},
            }
        )
        # A friendly notification too — picked up as last_message on the dashboard.
        io.write_json(
            {
                "jsonrpc": "2.0",
                "method": "notification",
                "params": {
                    "message": random.choice(
                        [
                            "Reading repo structure...",
                            "Drafting patch...",
                            "Running tests...",
                            "Reviewing diff...",
                            "Updating ticket...",
                            "Considering edge cases...",
                        ]
                    )
                },
            }
        )
        await io.drain()
    return totals


async def main() -> int:
    io = _Stdio()
    await io.start()
    _log("started", pid=os.getpid())

    state: dict[str, Any] = {
        "thread_count": 0,
        "turn_count": 0,
        "current_thread": None,
        "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }

    while True:
        line = await io.readline()
        if not line:
            _log("stdin closed; exiting")
            return 0
        try:
            msg = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        _log("request", method=method, id=msg_id)

        if method == "initialize":
            io.write_json(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "serverInfo": {"name": "mock-codex", "version": "0.0.1"},
                        "capabilities": {},
                    },
                }
            )
            await io.drain()
            continue

        if method == "thread/start":
            state["thread_count"] += 1
            tid = f"mock-thread-{state['thread_count']}"
            state["current_thread"] = tid
            cwd = params.get("cwd")
            _log("thread.start", thread_id=tid, cwd=cwd)
            io.write_json(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "thread": {"id": tid},
                        "cwd": cwd or "",
                        "model": "mock-model",
                        "modelProvider": "mock",
                        "approvalPolicy": "never",
                        "approvalsReviewer": "user",
                        "sandbox": "workspace-write",
                    },
                }
            )
            await io.drain()
            continue

        if method == "turn/start":
            state["turn_count"] += 1
            turn_n = state["turn_count"]
            tid = f"mock-turn-{turn_n}"
            should_fail = FAIL_EVERY_N and (turn_n % FAIL_EVERY_N == 0)
            duration = max(0.1, TURN_SECONDS + random.uniform(-1.5, 1.5))
            _log("turn.start", turn_id=tid, will_fail=should_fail, duration_s=round(duration, 2))

            # Respond immediately with status=inProgress so the backend
            # exercises the v2 notification-driven completion path.
            io.write_json(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "turn": {"id": tid, "status": "inProgress", "items": []}
                    },
                }
            )
            await io.drain()

            # Stream token-usage notifications during the simulated work.
            await _emit_token_progress(io, state["totals"], duration)

            # Emit an item/completed agentMessage so backends can capture
            # last_message — mirrors what the real codex does.
            io.write_json(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "threadId": state["current_thread"],
                        "turnId": tid,
                        "item": {
                            "id": f"item-{turn_n}",
                            "type": "agentMessage",
                            "text": f"Mock turn {turn_n} finished after {duration:.1f}s.",
                        },
                    },
                }
            )
            await io.drain()

            # Final turn/completed notification.
            final_status = "failed" if should_fail else "completed"
            final_turn = {"id": tid, "status": final_status, "items": []}
            if should_fail:
                final_turn["error"] = {
                    "type": "other",
                    "message": "mock simulated failure",
                }
            io.write_json(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {
                        "threadId": state["current_thread"],
                        "turn": final_turn,
                    },
                }
            )
            await io.drain()

            if MAX_TURNS and state["turn_count"] >= MAX_TURNS:
                _log("max_turns_reached", n=MAX_TURNS)
                # Exit so the worker treats this as subprocess death.
                return 0
            continue

        if method == "thread/archive":
            io.write_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            await io.drain()
            continue

        # Unknown method — return an empty result rather than stalling.
        if msg_id is not None:
            io.write_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            await io.drain()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
