"""Gemini CLI backend.

Drives `gemini -p` once per turn. Gemini's headless mode does not expose a
persistent session protocol or structured event stream — the prompt comes
from stdin and the model's final response goes to stdout. This backend wraps
that one-shot model into the AgentBackend contract.

Behavior summary:
- `start_session` returns a synthetic session id (timestamp-based) and emits
  `session_started` immediately.
- `run_turn` pipes the prompt to `gemini -p`'s stdin, waits for the process
  to exit, and emits a single `turn_completed` (or `turn_failed`) event with
  the captured stdout as the last message.
- Token usage is unavailable from the CLI in stable form, so totals stay at
  zero. Rate limits are likewise unavailable.

Multi-turn continuity is not supported: each `run_turn` is independent.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
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
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
)


log = get_logger()

# StreamReader line-buffer limit for the subprocess pipes. Gemini currently
# reads stdout with `.read()` (no line limit applies) but we set this for
# consistency with the other backends and as a safety net for future
# refactors. Matches codex.py.
MAX_LINE_BYTES = 10 * 1024 * 1024


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class GeminiBackend(BaseAgentBackend):
    """One subprocess per turn; captures stdout as the turn result."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._gemini = init.cfg.gemini
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

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
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
        return None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "gemini"}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        del initial_prompt, issue_title
        self._session_id = f"gemini-{uuid.uuid4().hex[:12]}"
        await self._emit(
            EVENT_SESSION_STARTED,
            {"session_id": self._session_id, "thread_id": self._session_id},
        )
        return self._session_id

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._closed:
            raise ResponseError("backend is closed")
        del is_continuation  # gemini -p is stateless; flag is informational

        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                self._gemini.command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc

        # `stop()` may have flipped `_closed` while we awaited spawn — reap
        # the orphaned process and bail.
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
                raise PortExit("gemini stdin closed", error=str(exc)) from exc

            timeout_s = self._gemini.turn_timeout_ms / 1000.0
            assert proc.stdout is not None and proc.stderr is not None
            stdout_task = asyncio.create_task(proc.stdout.read())
            stderr_task = asyncio.create_task(proc.stderr.read())
            try:
                stdout, stderr, safe_rc = await asyncio.wait_for(
                    asyncio.gather(
                        stdout_task,
                        stderr_task,
                        safe_proc_wait(proc),
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError as exc:
                stdout_task.cancel()
                stderr_task.cancel()
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout("gemini turn timed out") from exc

            rc = safe_rc if safe_rc is not None else (proc.returncode or 0)
            if rc != 0:
                err_msg = (stderr or b"").decode("utf-8", errors="replace").strip()[:400]
                # Standardize on `stderr_tail` (list[str]) so orchestrator /
                # operator grep handles every backend the same way; keep the
                # legacy `stderr` key for back-compat with anything that read
                # the previous shape.
                tail = [s for s in err_msg.splitlines() if s][-20:]
                payload = {
                    "reason": f"gemini exit {rc}" + (f"; stderr: {err_msg}" if err_msg else ""),
                    "stderr_tail": tail,
                    "stderr": err_msg,
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(err_msg or f"gemini failed with exit {rc}")

            result_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            payload = {
                "result": result_text,
                "session_id": self._session_id,
                "exit_code": rc,
            }
            await self._emit(EVENT_TURN_COMPLETED, payload)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=result_text[:400],
            )
        finally:
            self._active_proc = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

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

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._on_event(
                {
                    "event": event,
                    "timestamp": _utc_iso(),
                    "payload": payload if isinstance(payload, dict) else {"data": payload},
                    "usage": dict(self._latest_usage),
                    "rate_limits": None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))
