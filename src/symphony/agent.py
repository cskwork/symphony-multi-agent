"""SPEC §10 — Codex app-server client.

The Codex app-server protocol is the source of truth for message schemas
(per §10). This module implements Symphony's orchestration responsibilities:

- launch the subprocess via `bash -lc <command>` in the per-issue workspace,
- speak a JSON-RPC line protocol over stdio,
- enforce read/turn/stall timeouts,
- normalize emitted events into the runtime event vocabulary in §10.4,
- extract token/rate-limit telemetry per §13.5.

The exact JSON-RPC method names match common Codex app-server conventions
(`v2/threads.start`, `v2/threads.runTurn`, `v2/threads.stop`, etc.). They
remain in one place (METHOD_*) so an integrator can adjust to the targeted
Codex app-server version without rewriting the orchestration code.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .errors import (
    CodexNotFound,
    PortExit,
    ResponseError,
    ResponseTimeout,
    TurnCancelled,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
)
from .logging import get_logger
from .workflow import CodexConfig
from .workspace import validate_agent_cwd


log = get_logger()

MAX_LINE_BYTES = 10 * 1024 * 1024  # §10.1 — 10 MB

# §10.2 method names (best-effort defaults; adjust per Codex app-server version).
METHOD_INITIALIZE = "v2/initialize"
METHOD_THREAD_START = "v2/threads.start"
METHOD_TURN_START = "v2/threads.runTurn"
METHOD_THREAD_STOP = "v2/threads.stop"
METHOD_AUTH_RESPOND = "v2/threads.respondToApproval"
METHOD_TOOL_RESPOND = "v2/threads.respondToToolCall"


# §10.4 emitted event vocabulary (Symphony-side, normalized).
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
class AgentEvent:
    event: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] | None = None
    rate_limits: dict[str, Any] | None = None
    pid: int | None = None


@dataclass
class TurnResult:
    status: str  # one of EVENT_TURN_COMPLETED / TURN_FAILED / TURN_CANCELLED
    turn_id: str | None
    last_message: str = ""
    error: str | None = None


@dataclass
class _ToolDescriptor:
    name: str
    description: str
    schema: dict[str, Any]


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CodexAppServerClient:
    """One subprocess instance per worker run.

    Lifecycle:
        async with CodexAppServerClient(...).run() as session:
            await session.start_thread(prompt=..., issue=...)
            for turn in turns:
                result = await session.run_turn(prompt=..., is_continuation=False)
                ...
    """

    def __init__(
        self,
        codex: CodexConfig,
        cwd: Path,
        workspace_root: Path,
        on_event: EventCallback,
        approval_policy: Any = None,
        sandbox_policy: Any = None,
        thread_sandbox: Any = None,
        client_tools: list[_ToolDescriptor] | None = None,
    ) -> None:
        validate_agent_cwd(cwd, workspace_root)
        self._codex = codex
        self._cwd = cwd
        self._on_event = on_event
        self._approval_policy = approval_policy or codex.approval_policy
        self._sandbox_policy = sandbox_policy or codex.turn_sandbox_policy
        self._thread_sandbox = thread_sandbox or codex.thread_sandbox
        self._client_tools = client_tools or []
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notif_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._latest_rate_limits: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # subprocess lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                self._codex.command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
            )
        except FileNotFoundError as exc:
            raise CodexNotFound("bash not available", error=str(exc)) from exc
        if self._process.stdout is None or self._process.stdin is None:
            raise CodexNotFound("subprocess pipes not available")
        self._reader_task = asyncio.create_task(self._stdout_reader())
        self._stderr_task = asyncio.create_task(self._stderr_reader())

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread_id and self._process and self._process.returncode is None:
            try:
                await asyncio.wait_for(
                    self._request(METHOD_THREAD_STOP, {"threadId": self._thread_id}),
                    timeout=self._codex.read_timeout_ms / 1000.0,
                )
            except Exception:
                pass
        if self._process is not None:
            if self._process.returncode is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    try:
                        self._process.kill()
                    except ProcessLookupError:
                        pass
                    await self._process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def latest_usage(self) -> dict[str, int]:
        return dict(self._latest_usage)

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return dict(self._latest_rate_limits) if self._latest_rate_limits is not None else None

    # ------------------------------------------------------------------
    # session and turn helpers
    # ------------------------------------------------------------------

    async def initialize(self) -> dict[str, Any]:
        params = {
            "client": {"name": "symphony", "version": "0.1.0"},
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "schema": tool.schema,
                }
                for tool in self._client_tools
            ],
        }
        return await self._request(METHOD_INITIALIZE, params)

    async def start_thread(self, *, initial_prompt: str, issue_title: str | None) -> str:
        params: dict[str, Any] = {
            "cwd": str(self._cwd),
            "initialPrompt": initial_prompt,
        }
        if issue_title:
            params["title"] = issue_title
        if self._thread_sandbox is not None:
            params["sandbox"] = self._thread_sandbox
        if self._approval_policy is not None:
            params["approvalPolicy"] = self._approval_policy
        result = await self._request(METHOD_THREAD_START, params)
        thread_id = (
            result.get("threadId")
            or result.get("thread_id")
            or result.get("id")
            or "thread"
        )
        self._thread_id = str(thread_id)
        await self._emit(
            EVENT_SESSION_STARTED,
            {"thread_id": self._thread_id},
        )
        return self._thread_id

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._thread_id is None:
            raise ResponseError("run_turn called before thread started")
        params: dict[str, Any] = {
            "threadId": self._thread_id,
            "prompt": prompt,
            "cwd": str(self._cwd),
            "continuation": is_continuation,
        }
        if self._sandbox_policy is not None:
            params["sandboxPolicy"] = self._sandbox_policy
        if self._approval_policy is not None:
            params["approvalPolicy"] = self._approval_policy
        try:
            result = await self._request(
                METHOD_TURN_START,
                params,
                timeout_s=self._codex.turn_timeout_ms / 1000.0,
            )
        except ResponseTimeout as exc:
            await self._emit(
                EVENT_TURN_FAILED,
                {"reason": "turn_timeout"},
            )
            raise TurnTimeout("turn timed out") from exc
        turn_id = result.get("turnId") or result.get("turn_id") or result.get("id")
        self._current_turn_id = str(turn_id) if turn_id is not None else None
        # Drain any pending notifications to capture token usage / rate limits.
        await self._drain_notifications()
        status = result.get("status", EVENT_TURN_COMPLETED)
        last_message = result.get("lastMessage") or result.get("last_message") or ""
        if status == EVENT_TURN_INPUT_REQUIRED:
            # §10.5 documented high-trust posture treats this as failure.
            await self._emit(EVENT_TURN_INPUT_REQUIRED, result)
            raise TurnInputRequired("user input required treated as failure")
        if status == EVENT_TURN_CANCELLED:
            await self._emit(EVENT_TURN_CANCELLED, result)
            raise TurnCancelled("turn cancelled")
        if status == EVENT_TURN_FAILED or status == EVENT_TURN_ENDED_WITH_ERROR:
            await self._emit(status, result)
            raise TurnFailed(str(result.get("error") or "turn failed"))
        await self._emit(EVENT_TURN_COMPLETED, result)
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=self._current_turn_id,
            last_message=last_message,
        )

    # ------------------------------------------------------------------
    # JSON-RPC line protocol over stdio
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await the response.

        §10.6 — `read_timeout_ms` applies only to startup/sync requests.
        Callers that need a longer window (notably `run_turn`) pass an explicit
        `timeout_s` derived from `turn_timeout_ms`.
        """
        if self._closed:
            raise ResponseError("client is closed")
        if self._process is None or self._process.stdin is None:
            raise ResponseError("subprocess not started")
        msg_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future
        body = json.dumps(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
            ensure_ascii=False,
        )
        try:
            self._process.stdin.write((body + "\n").encode("utf-8"))
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(msg_id, None)
            raise PortExit("stdin closed", error=str(exc)) from exc
        effective_timeout = timeout_s if timeout_s is not None else self._codex.read_timeout_ms / 1000.0
        try:
            response = await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise ResponseTimeout("response timeout", method=method) from exc
        if "error" in response and response["error"] is not None:
            raise ResponseError(
                str(response["error"].get("message", "rpc error")),
                method=method,
            )
        return response.get("result") or {}

    async def _stdout_reader(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        while True:
            try:
                line = await stdout.readline()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("codex_stdout_read_error", error=str(exc))
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
            if isinstance(msg, dict) and "id" in msg and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if not fut.done():
                    fut.set_result(msg)
                continue
            # Notification / event from the agent.
            await self._handle_notification(msg if isinstance(msg, dict) else {})
        # Stream closed -> resolve all pending with PortExit.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(PortExit("subprocess stdout closed"))
        self._pending.clear()

    async def _stderr_reader(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stderr = self._process.stderr
        while True:
            try:
                line = await stderr.readline()
            except asyncio.CancelledError:
                raise
            except Exception:
                break
            if not line:
                break
            log.debug("codex_stderr", line=line.decode("utf-8", errors="replace").rstrip())

    async def _drain_notifications(self) -> None:
        """Process any queued notifications synchronously."""
        while not self._notif_queue.empty():
            msg = self._notif_queue.get_nowait()
            await self._handle_notification(msg)

    async def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or msg.get("event") or ""
        params = msg.get("params") or msg.get("payload") or msg
        if method.endswith("/tokenUsage/updated") or method == "thread/tokenUsage/updated":
            self._update_tokens_absolute(params.get("totals") or params)
        elif method.endswith("/rateLimits"):
            self._latest_rate_limits = params.get("rateLimits") or params
        elif method == "approval.requested":
            await self._handle_approval(params)
            return
        elif method == "tool.requested":
            await self._handle_tool_call(params)
            return
        await self._emit(_normalize_event_name(method), params)

    def _update_tokens_absolute(self, payload: dict[str, Any]) -> None:
        # §13.5 — prefer absolute totals, ignore delta-style payloads.
        if not isinstance(payload, dict):
            return
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if isinstance(payload.get(key), (int, float)):
                self._latest_usage[key] = int(payload[key])

    async def _handle_approval(self, params: dict[str, Any]) -> None:
        # §10.5 example high-trust behavior: auto-approve.
        approval_id = params.get("id") or params.get("approvalId")
        if approval_id is None:
            return
        await self._emit(EVENT_APPROVAL_AUTO_APPROVED, params)
        try:
            await self._request(METHOD_AUTH_RESPOND, {"id": approval_id, "approved": True})
        except Exception as exc:
            log.warning("approval_respond_failed", error=str(exc))

    async def _handle_tool_call(self, params: dict[str, Any]) -> None:
        tool_name = params.get("name") or params.get("tool")
        call_id = params.get("id") or params.get("callId")
        # §10.5 — unsupported tool calls return failure result, do not stall.
        await self._emit(EVENT_UNSUPPORTED_TOOL_CALL, params)
        if call_id is None:
            return
        try:
            await self._request(
                METHOD_TOOL_RESPOND,
                {
                    "id": call_id,
                    "ok": False,
                    "error": {
                        "code": "tool_not_supported",
                        "message": f"tool {tool_name} not supported",
                    },
                },
            )
        except Exception as exc:
            log.warning("tool_respond_failed", error=str(exc))

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        ev = AgentEvent(
            event=event,
            timestamp=_utc_iso(),
            payload=payload if isinstance(payload, dict) else {"data": payload},
            usage=dict(self._latest_usage),
            rate_limits=dict(self._latest_rate_limits) if self._latest_rate_limits else None,
            pid=self.pid,
        )
        try:
            await self._on_event(
                {
                    "event": ev.event,
                    "timestamp": ev.timestamp,
                    "payload": ev.payload,
                    "usage": ev.usage,
                    "rate_limits": ev.rate_limits,
                    "codex_app_server_pid": ev.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))


def _normalize_event_name(method: str) -> str:
    """Map raw protocol method strings to §10.4 vocabulary."""
    if not method:
        return EVENT_OTHER_MESSAGE
    lower = method.lower()
    if "turn" in lower and "completed" in lower:
        return EVENT_TURN_COMPLETED
    if "turn" in lower and "failed" in lower:
        return EVENT_TURN_FAILED
    if "turn" in lower and ("cancel" in lower):
        return EVENT_TURN_CANCELLED
    if "input" in lower and "required" in lower:
        return EVENT_TURN_INPUT_REQUIRED
    if "approval" in lower:
        return EVENT_APPROVAL_AUTO_APPROVED
    if "tool" in lower and ("unsupported" in lower or "request" in lower):
        return EVENT_UNSUPPORTED_TOOL_CALL
    if "notif" in lower:
        return EVENT_NOTIFICATION
    return EVENT_OTHER_MESSAGE


# Convenience: optional `linear_graphql` tool descriptor (§10.5 extension).
def linear_graphql_tool() -> _ToolDescriptor:
    return _ToolDescriptor(
        name="linear_graphql",
        description="Execute a raw GraphQL query or mutation against Linear using configured auth.",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "variables": {"type": "object"},
            },
            "required": ["query"],
        },
    )
