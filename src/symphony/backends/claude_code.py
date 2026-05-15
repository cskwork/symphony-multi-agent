"""Claude Code CLI backend.

Drives `claude -p --output-format stream-json --verbose` once per turn. The
underlying CLI does not have a persistent app-server; sessions are tracked by
ID, so this backend spawns a fresh subprocess each turn and uses
`--resume <session-id>` for continuity.

Stream-JSON event shape (one line of JSON per event):

  {"type":"system","subtype":"init","session_id":"...","model":"...",...}
  {"type":"assistant","message":{...,"usage":{...}},"session_id":"..."}
  {"type":"user","message":{"content":[{"type":"tool_result",...}]},...}
  {"type":"result","subtype":"success","is_error":false,
   "usage":{"input_tokens":N,"output_tokens":N,...},"result":"...",
   "total_cost_usd":N,"session_id":"...","duration_ms":N,...}

Errors surface as `result` events with `is_error:true` and a `subtype`
indicating the failure mode (e.g. `error_max_turns`).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from collections import deque
from typing import Any

from .._shell import resolve_bash, safe_proc_wait
from ..errors import (
    PortExit,
    ResponseError,
    TurnFailed,
    TurnTimeout,
)
from ..logging import get_logger
from ..workspace import validate_agent_cwd
from . import (
    EVENT_MALFORMED,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    TurnResult,
)


log = get_logger()

PENDING_SESSION_ID = "pending"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ClaudeCodeBackend:
    """One subprocess per turn; speaks Claude Code stream-json."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._claude = init.cfg.claude
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._session_id: str | None = None
        self._closed = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._latest_rate_limits: dict[str, Any] | None = None
        self._last_message: str = ""
        # Bounded stderr ring buffer — see PiBackend for the rationale.
        self._stderr_tail: deque[str] = deque(maxlen=20)

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        # No persistent process — subprocesses are per-turn.
        return None

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._active_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            rc = await safe_proc_wait(proc, timeout=2.0)
            if rc is None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await safe_proc_wait(proc)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return self._active_proc.pid if self._active_proc is not None else None

    @property
    def latest_usage(self) -> dict[str, int]:
        return dict(self._latest_usage)

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return dict(self._latest_rate_limits) if self._latest_rate_limits is not None else None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "claude_code"}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # Claude Code creates the session implicitly on the first `claude -p`
        # invocation. Return a placeholder; the real session id arrives in the
        # init event of the first run_turn and triggers `session_started`.
        del initial_prompt, issue_title
        return PENDING_SESSION_ID

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._closed:
            raise ResponseError("backend is closed")

        cmd = self._claude.command
        if (
            is_continuation
            and self._claude.resume_across_turns
            and self._session_id
            and self._session_id != PENDING_SESSION_ID
        ):
            cmd = f"{cmd} --resume {shlex.quote(self._session_id)}"

        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                cmd,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc

        # `stop()` may have flipped `_closed` while we awaited spawn — in that
        # case the process is orphaned because `stop()` only inspects
        # `_active_proc` and we hadn't published yet. Reap and bail.
        if self._closed:
            await self._reap(proc)
            raise ResponseError("backend closed during spawn")
        self._active_proc = proc
        try:
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PortExit("claude stdin closed", error=str(exc)) from exc

            timeout_s = self._claude.turn_timeout_ms / 1000.0
            try:
                terminal = await asyncio.wait_for(
                    self._consume_stream(proc), timeout=timeout_s
                )
            except asyncio.TimeoutError as exc:
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout("claude turn timed out") from exc

            await safe_proc_wait(proc)
            if terminal is None:
                # Stream ended without a `result` event — treat as failure.
                stderr_blob = self._stderr_blob()
                err_msg = (
                    f"claude exited with no result event (rc={proc.returncode})"
                    + (f"; stderr: {stderr_blob}" if stderr_blob else "")
                )
                await self._emit(
                    EVENT_TURN_FAILED,
                    {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
                )
                raise TurnFailed(err_msg)

            if _is_error_result(terminal):
                payload = {**terminal, "stderr_tail": list(self._stderr_tail)}
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(
                    str(terminal.get("subtype") or terminal.get("error") or "claude turn failed")
                )

            self._last_message = str(terminal.get("result") or "")[:400]
            await self._emit(EVENT_TURN_COMPLETED, terminal)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=str(terminal.get("session_id") or self._session_id or ""),
                last_message=self._last_message,
            )
        finally:
            self._active_proc = None

    # ------------------------------------------------------------------
    # stream-json parsing
    # ------------------------------------------------------------------

    async def _consume_stream(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Read stream-json events; return the terminal `result` event or None."""
        assert proc.stdout is not None
        terminal: dict[str, Any] | None = None
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("claude_stdout_read_error", error=str(exc))
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    await self._emit(EVENT_MALFORMED, {"raw": text[:500]})
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "system" and msg.get("subtype") == "init":
                    sid = msg.get("session_id")
                    if isinstance(sid, str) and sid:
                        self._session_id = sid
                        await self._emit(
                            EVENT_SESSION_STARTED,
                            {"session_id": sid, "thread_id": sid},
                        )
                elif kind == "assistant":
                    # Mid-stream `usage` deltas are ignored; the terminal
                    # `result` event is the source of truth for accumulation.
                    last_text = _extract_text(msg.get("message") or {})
                    if last_text:
                        self._last_message = last_text[:400]
                    await self._emit(EVENT_OTHER_MESSAGE, msg)
                elif kind == "user":
                    await self._emit(EVENT_OTHER_MESSAGE, msg)
                elif kind == "result":
                    self._update_usage_absolute(msg.get("usage") or {})
                    sid = msg.get("session_id")
                    if isinstance(sid, str) and sid and not self._session_id:
                        self._session_id = sid
                        await self._emit(
                            EVENT_SESSION_STARTED,
                            {"session_id": sid, "thread_id": sid},
                        )
                    terminal = msg
                else:
                    await self._emit(EVENT_OTHER_MESSAGE, msg)
        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):
                pass
        return terminal

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while True:
            try:
                line = await proc.stderr.readline()
            except (asyncio.CancelledError, Exception):
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)
            log.debug("claude_stderr", line=text)

    def _stderr_blob(self) -> str:
        """Compact stderr tail for failure messages (≤400 chars)."""
        if not self._stderr_tail:
            return ""
        joined = " | ".join(self._stderr_tail)
        return joined if len(joined) <= 400 else joined[-400:]

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        """Best-effort terminate→wait→kill ladder; mirrors `stop()`."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        rc = await safe_proc_wait(proc, timeout=2.0)
        if rc is None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await safe_proc_wait(proc)

    def _update_usage_absolute(self, usage: dict[str, Any]) -> None:
        # Each `result` event reports usage for that one turn — accumulate.
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        out_t = int(usage.get("output_tokens") or 0)
        # Treat cache reads/creates as part of the input bucket so totals stay
        # comparable with Codex-side `input_tokens`. Anthropic charges cache
        # reads at a discount but Symphony's totals are unit counts, not cost.
        billed_in = in_t + cache_read + cache_create
        self._latest_usage["input_tokens"] += billed_in
        self._latest_usage["output_tokens"] += out_t
        self._latest_usage["total_tokens"] += billed_in + out_t

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._on_event(
                {
                    "event": event,
                    "timestamp": _utc_iso(),
                    "payload": payload if isinstance(payload, dict) else {"data": payload},
                    "usage": dict(self._latest_usage),
                    "rate_limits": dict(self._latest_rate_limits)
                    if self._latest_rate_limits
                    else None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))


def _extract_text(message: dict[str, Any]) -> str:
    """Pull the last text block out of a Claude assistant message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    for block in reversed(content):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                return text
    return ""


def _is_error_result(event: dict[str, Any]) -> bool:
    subtype = str(event.get("subtype") or "").lower()
    if subtype == "success":
        return False
    if subtype.startswith("error"):
        return True

    value = event.get("is_error")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)
