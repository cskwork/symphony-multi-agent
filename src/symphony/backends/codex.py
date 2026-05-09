"""Codex app-server backend (was the original symphony agent.py).

Speaks JSON-RPC line protocol over stdio against a long-running
`codex app-server` subprocess. Lifecycle:

    await b.start()                      # spawn subprocess + readers
    await b.initialize()                 # v2/initialize
    await b.start_session(...)           # v2/threads.start
    while turns:
        await b.run_turn(...)            # v2/threads.runTurn
    await b.stop()                       # v2/threads.stop, terminate

The exact JSON-RPC method names are kept in METHOD_* so an integrator can
adjust to the targeted Codex app-server version without rewriting the
orchestration code (per upstream §10.2).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from ..errors import (
    CodexNotFound,
    PortExit,
    ResponseError,
    ResponseTimeout,
    TurnCancelled,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
)
from ..logging import get_logger
from ..workspace import validate_agent_cwd
from . import (
    EVENT_APPROVAL_AUTO_APPROVED,
    EVENT_MALFORMED,
    EVENT_NOTIFICATION,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_CANCELLED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_ENDED_WITH_ERROR,
    EVENT_TURN_FAILED,
    EVENT_TURN_INPUT_REQUIRED,
    EVENT_UNSUPPORTED_TOOL_CALL,
    BackendInit,
    ToolDescriptor,
    TurnResult,
)


log = get_logger()

MAX_LINE_BYTES = 10 * 1024 * 1024  # upstream §10.1 — 10 MB

METHOD_INITIALIZE = "v2/initialize"
METHOD_THREAD_START = "v2/threads.start"
METHOD_TURN_START = "v2/threads.runTurn"
METHOD_THREAD_STOP = "v2/threads.stop"
METHOD_AUTH_RESPOND = "v2/threads.respondToApproval"
METHOD_TOOL_RESPOND = "v2/threads.respondToToolCall"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CodexAppServerBackend:
    """One subprocess instance per worker run; speaks Codex JSON-RPC."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        codex = init.cfg.codex
        self._codex = codex
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._client_tools = init.client_tools
        self._approval_policy = codex.approval_policy
        self._sandbox_policy = codex.turn_sandbox_policy
        self._thread_sandbox = codex.thread_sandbox
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
    # AgentBackend lifecycle
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
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @property
    def session_id(self) -> str | None:
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

    async def initialize(self) -> dict[str, Any]:
        params = {
            "client": {"name": "symphony", "version": "0.2.0"},
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

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
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
            {"thread_id": self._thread_id, "session_id": self._thread_id},
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
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout("turn timed out") from exc
        turn_id = result.get("turnId") or result.get("turn_id") or result.get("id")
        self._current_turn_id = str(turn_id) if turn_id is not None else None
        await self._drain_notifications()
        status = result.get("status", EVENT_TURN_COMPLETED)
        last_message = result.get("lastMessage") or result.get("last_message") or ""
        if status == EVENT_TURN_INPUT_REQUIRED:
            await self._emit(EVENT_TURN_INPUT_REQUIRED, result)
            raise TurnInputRequired("user input required treated as failure")
        if status == EVENT_TURN_CANCELLED:
            await self._emit(EVENT_TURN_CANCELLED, result)
            raise TurnCancelled("turn cancelled")
        if status in (EVENT_TURN_FAILED, EVENT_TURN_ENDED_WITH_ERROR):
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
        # `read_timeout_ms` applies to startup/sync requests (upstream §10.6).
        # `run_turn` passes an explicit `timeout_s` derived from `turn_timeout_ms`.
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
        effective_timeout = (
            timeout_s if timeout_s is not None else self._codex.read_timeout_ms / 1000.0
        )
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
            await self._handle_notification(msg if isinstance(msg, dict) else {})
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
        # Upstream §13.5 — prefer absolute totals, ignore delta-style payloads.
        if not isinstance(payload, dict):
            return
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if isinstance(payload.get(key), (int, float)):
                self._latest_usage[key] = int(payload[key])

    async def _handle_approval(self, params: dict[str, Any]) -> None:
        # High-trust posture: auto-approve (upstream §10.5).
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
        ev_payload = payload if isinstance(payload, dict) else {"data": payload}
        try:
            await self._on_event(
                {
                    "event": event,
                    "timestamp": _utc_iso(),
                    "payload": ev_payload,
                    "usage": dict(self._latest_usage),
                    "rate_limits": dict(self._latest_rate_limits)
                    if self._latest_rate_limits
                    else None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))


def _normalize_event_name(method: str) -> str:
    """Map raw protocol method strings to normalized event vocabulary."""
    if not method:
        return EVENT_OTHER_MESSAGE
    lower = method.lower()
    if "turn" in lower and "completed" in lower:
        return EVENT_TURN_COMPLETED
    if "turn" in lower and "failed" in lower:
        return EVENT_TURN_FAILED
    if "turn" in lower and "cancel" in lower:
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


def linear_graphql_tool() -> ToolDescriptor:
    """Optional `linear_graphql` tool descriptor for Codex (upstream §10.5)."""
    return ToolDescriptor(
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
