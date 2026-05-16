"""Pi coding-agent CLI backend.

Drives `pi --mode json -p ""` once per turn. Pi (https://pi.dev) auto-saves
sessions under `~/.pi/agent/sessions/<cwd-hash>/`; multi-turn continuity
re-enters the captured session id with `--session <id>`.

JSON mode emits one event per line on stdout. The first line is the session
header, followed by `AgentSessionEvent` items as the run progresses:

  {"type":"session","version":3,"id":"<uuid>","timestamp":"...","cwd":"..."}
  {"type":"agent_start", ...}
  {"type":"turn_start", ...}
  {"type":"message_start", ...}
  {"type":"message_update", ...}
  {"type":"message_end","message":{...,"usage":{...},"stopReason":"stop"}}
  {"type":"tool_execution_start", ...}
  {"type":"tool_execution_end","toolName":"...","isError":false,"result":...}
  {"type":"turn_end","message":AssistantMessage,"toolResults":[...]}
  {"type":"agent_end","messages":[AssistantMessage, ...]}

Terminal event: `agent_end`. Errors surface either as
`message.stopReason == "error"` with `errorMessage`, or as `tool_execution_end`
with `isError: true`. Process-level fatals go to stderr.

Usage shape inside an `AssistantMessage`:
  {"input": N, "output": N, "cacheRead": N, "cacheWrite": N,
   "totalTokens": N, "cost": {...}}

We accumulate (input + cacheRead + cacheWrite) into `input_tokens` so totals
remain comparable with the Codex / Claude buckets, where token counts (not
billed cost) are the unit.
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
    EVENT_AGENT_RETRY,
    EVENT_COMPACTION,
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

# StreamReader line-buffer limit for the subprocess pipes. The asyncio
# default of 64 KiB overflows on JSON-mode events whose `message_update` or
# tool-result payload exceeds that on a single line. Matches codex.py.
MAX_LINE_BYTES = 10 * 1024 * 1024


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class PiBackend:
    """One subprocess per turn; speaks pi --mode json JSONL."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._pi = init.cfg.pi
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
        self._last_message: str = ""
        # Bounded ring buffer of stderr lines so a TurnFailed exception can
        # carry the actual reason (auth error, network, ratelimit, ...) up to
        # the orchestrator instead of the opaque "no agent_end event" string.
        self._stderr_tail: deque[str] = deque(maxlen=20)

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
        # Pi does not surface rate-limit telemetry in the JSON stream.
        return None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "pi"}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # Pi mints a session id when the first `pi --mode json` invocation
        # writes the `session` header line. Return a placeholder; the real id
        # arrives in `_consume_stream` and triggers `session_started`.
        del initial_prompt, issue_title
        return PENDING_SESSION_ID

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._closed:
            raise ResponseError("backend is closed")

        cmd = self._pi.command
        if (
            is_continuation
            and self._pi.resume_across_turns
            and self._session_id
            and self._session_id != PENDING_SESSION_ID
        ):
            cmd = f"{cmd} --session {shlex.quote(self._session_id)}"

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
                # Pi documents stdin as appended to the `-p` argument. Since
                # the default command is `pi --mode json -p ""`, the prompt
                # arrives entirely through stdin.
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PortExit("pi stdin closed", error=str(exc)) from exc

            timeout_s = self._pi.turn_timeout_ms / 1000.0
            try:
                terminal = await asyncio.wait_for(
                    self._consume_stream(proc), timeout=timeout_s
                )
            except asyncio.TimeoutError as exc:
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout("pi turn timed out") from exc

            safe_rc = await safe_proc_wait(proc)
            if terminal is None:
                stderr_blob = self._stderr_blob()
                rc = safe_rc if safe_rc is not None else proc.returncode
                err_msg = (
                    f"pi exited with no agent_end event (rc={rc})"
                    + (f"; stderr: {stderr_blob}" if stderr_blob else "")
                )
                await self._emit(
                    EVENT_TURN_FAILED,
                    {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
                )
                raise TurnFailed(err_msg)

            failure_reason = _extract_failure_reason(terminal)
            if failure_reason is not None:
                stderr_blob = self._stderr_blob()
                if stderr_blob:
                    failure_reason = f"{failure_reason}; stderr: {stderr_blob}"
                payload = {
                    "reason": failure_reason,
                    "stderr_tail": list(self._stderr_tail),
                    **terminal,
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(failure_reason)

            await self._emit(EVENT_TURN_COMPLETED, terminal)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=self._last_message,
            )
        finally:
            self._active_proc = None

    # ------------------------------------------------------------------
    # JSONL parsing
    # ------------------------------------------------------------------

    async def _consume_stream(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Read JSONL events; return the terminal `agent_end` event or None."""
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
                    log.error("pi_stdout_read_error", error=str(exc))
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
                if kind == "session":
                    sid = msg.get("id")
                    if isinstance(sid, str) and sid:
                        self._session_id = sid
                        await self._emit(
                            EVENT_SESSION_STARTED,
                            {"session_id": sid, "thread_id": sid},
                        )
                elif kind == "message_end":
                    message = msg.get("message") or {}
                    if isinstance(message, dict):
                        self._update_usage(message.get("usage") or {})
                        last_text = _extract_text(message)
                        if last_text:
                            self._last_message = last_text[:400]
                    await self._emit(EVENT_OTHER_MESSAGE, msg)
                elif kind == "turn_end":
                    # `turn_end` carries the same AssistantMessage as the
                    # paired `message_end`; usage is already accumulated, so
                    # only refresh the last-message preview if missing.
                    message = msg.get("message") or {}
                    if isinstance(message, dict) and not self._last_message:
                        last_text = _extract_text(message)
                        if last_text:
                            self._last_message = last_text[:400]
                    await self._emit(EVENT_OTHER_MESSAGE, msg)
                elif kind == "agent_end":
                    terminal = msg
                elif kind == "compaction_start":
                    # Pi auto-compacts when the conversation approaches the
                    # model's context window (or on `/compact`). Surface as a
                    # normalized event so symphony can log it once at INFO
                    # — a sudden token drop on the next turn would otherwise
                    # be unattributable.
                    await self._emit(
                        EVENT_COMPACTION,
                        {
                            "phase": "start",
                            "reason": msg.get("reason"),
                        },
                    )
                elif kind == "compaction_end":
                    result = msg.get("result") or {}
                    payload = {
                        "phase": "end",
                        "reason": msg.get("reason"),
                        "aborted": bool(msg.get("aborted")),
                        "will_retry": bool(msg.get("willRetry")),
                    }
                    if isinstance(result, dict):
                        # Best-effort: surface tokensBefore from the
                        # CompactionEntry summary if pi includes it.
                        for src, dst in (
                            ("tokensBefore", "tokens_before"),
                            ("firstKeptEntryId", "first_kept_entry_id"),
                        ):
                            if src in result:
                                payload[dst] = result[src]
                    err = msg.get("errorMessage")
                    if isinstance(err, str) and err:
                        payload["error"] = err
                    await self._emit(EVENT_COMPACTION, payload)
                elif kind == "auto_retry_start":
                    await self._emit(
                        EVENT_AGENT_RETRY,
                        {
                            "phase": "start",
                            "attempt": msg.get("attempt"),
                            "max_attempts": msg.get("maxAttempts"),
                            "delay_ms": msg.get("delayMs"),
                            "error": msg.get("errorMessage"),
                        },
                    )
                elif kind == "auto_retry_end":
                    await self._emit(
                        EVENT_AGENT_RETRY,
                        {
                            "phase": "end",
                            "attempt": msg.get("attempt"),
                            "success": bool(msg.get("success")),
                            "final_error": msg.get("finalError"),
                        },
                    )
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
            log.debug("pi_stderr", line=text)

    def _stderr_blob(self) -> str:
        """Compact stderr tail for inclusion in failure messages (≤400 chars)."""
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

    def _update_usage(self, usage: dict[str, Any]) -> None:
        """Accumulate Pi's per-message Usage into the running totals."""
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("input") or 0)
        cache_read = int(usage.get("cacheRead") or 0)
        cache_write = int(usage.get("cacheWrite") or 0)
        out_t = int(usage.get("output") or 0)
        # Mirror the Claude backend: cache reads/writes count toward input
        # tokens so the totals stay unit-comparable across backends.
        billed_in = in_t + cache_read + cache_write
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
                    "rate_limits": None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))


def _extract_text(message: dict[str, Any]) -> str:
    """Pull the last text block out of a Pi AssistantMessage."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    return text
    text = message.get("text")
    if isinstance(text, str):
        return text
    return ""


def _extract_failure_reason(terminal: dict[str, Any]) -> str | None:
    """Return a non-empty failure reason if the terminal `agent_end` event
    indicates the run ended in error; otherwise None.

    Pi's `agent_end` carries `messages: AssistantMessage[]`; an erroring run
    leaves the final message with `stopReason == "error"` and an
    `errorMessage` field. `aborted` is also treated as a failure so the
    orchestrator can surface it as a turn failure rather than silently
    succeeding.
    """
    messages = terminal.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if not isinstance(last, dict):
        return None
    stop_reason = last.get("stopReason")
    if stop_reason in ("error", "aborted"):
        err = last.get("errorMessage")
        if isinstance(err, str) and err:
            return err
        return f"pi turn ended with stopReason={stop_reason!r}"
    return None
