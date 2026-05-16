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
from pathlib import Path
from typing import Any, Sequence

from .._shell import resolve_bash, safe_proc_wait
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

# Codex app-server protocol (v2 of `codex app-server`, codex-cli ≥ 0.39).
# Older releases used `v2/initialize`, `v2/threads.start`, `v2/threads.runTurn`
# — see commit history if you need to support a legacy app-server.
METHOD_INITIALIZE = "initialize"
METHOD_THREAD_START = "thread/start"
METHOD_TURN_START = "turn/start"
METHOD_THREAD_ARCHIVE = "thread/archive"
# `thread/approveGuardianDeniedAction` is the rough equivalent of the legacy
# respondToApproval; symphony auto-approves so this is rarely used.
METHOD_APPROVAL_RESPOND = "thread/approveGuardianDeniedAction"

# Notifications we react to.
NOTIF_TURN_COMPLETED = "turn/completed"
NOTIF_TURN_STARTED = "turn/started"
NOTIF_ITEM_COMPLETED = "item/completed"
NOTIF_THREAD_TOKEN_USAGE = "thread/tokenUsage/updated"
NOTIF_RATE_LIMITS = "account/rateLimits/updated"

# Last-message preview is rendered in the dashboard / passed back as
# `TurnResult.last_message`. 1000 chars matches what the legacy backend used.
_ASSISTANT_MESSAGE_PREVIEW_CAP = 1000


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# v2 turn/start `sandboxPolicy` is a tagged enum object, not a kebab-case
# string. We keep WORKFLOW.md's familiar kebab-case values and translate
# here. (Thread-level `sandbox` in v2 still accepts the kebab-case string.)
_SANDBOX_POLICY_TAG: dict[str, str] = {
    "workspace-write": "workspaceWrite",
    "read-only": "readOnly",
    "danger-full-access": "dangerFullAccess",
}


def _sandbox_policy_to_turn_payload(value: Any) -> Any:
    """Translate a workflow ``turn_sandbox_policy`` string to v2 payload."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value  # already v2-shaped
    tag = _SANDBOX_POLICY_TAG.get(str(value))
    if tag is None:
        # Unknown — let codex reject with a clear error rather than guess.
        return value
    return {"type": tag}


def _sandbox_uses_workspace_write(*values: Any) -> bool:
    """Whether any of the given sandbox values enforces workspace-write.

    Accepts the kebab-case strings used in WORKFLOW.md (`workspace-write`)
    and the v2 tagged-enum dicts (`{"type": "workspaceWrite"}`).
    """
    for value in values:
        if value == "workspace-write":
            return True
        if isinstance(value, dict) and value.get("type") == "workspaceWrite":
            return True
    return False


def _scan_workspace_symlinks(*roots: Path) -> list[str]:
    """Return realpath targets of top-level symlinks in workspace roots.

    Codex with ``sandbox_mode=workspace-write`` resolves symlinks via realpath
    and refuses to write through paths whose target falls outside the
    workspace root. When ``after_create`` symlinks host repo directories
    (kanban/, prompt/, ...) into the workspace, stage-transition
    commits silently fail and the worker burns turns repeating "쓰기 불가".

    This scan returns the resolved targets so the backend can pass them to
    codex as ``sandbox_workspace_write.writable_roots`` — keeping the safer
    workspace-write default while letting writes succeed through symlinks.

    Top-level only by design — after_create hooks symlink leaf dirs at the
    workspace root, and recursing would slow start() materially on large
    repos.
    """
    seen: set[str] = set()
    for root in roots:
        try:
            entries = list(root.iterdir())
        except (OSError, FileNotFoundError):
            continue
        for entry in entries:
            if not entry.is_symlink():
                continue
            try:
                target = entry.resolve(strict=False)
            except OSError:
                continue
            seen.add(str(target))
    return sorted(seen)


def _inject_writable_roots(command: str, writable_roots: Sequence[str]) -> str:
    """Inject ``-c sandbox_workspace_write.writable_roots=[...]`` after ``codex``.

    Only applies when the command starts with a literal ``codex`` CLI token.
    When a wrapper script is used (codex is invoked from inside the script),
    the ``SYMPHONY_CODEX_WRITABLE_ROOTS`` env var carries the same list so
    the wrapper can append ``-c`` itself.
    """
    if not writable_roots:
        return command
    stripped = command.lstrip()
    if not (stripped == "codex" or stripped.startswith(("codex ", "codex\t"))):
        return command
    leading_ws = command[: len(command) - len(stripped)]
    rest = stripped[len("codex"):]
    roots_toml = "[" + ",".join(f'"{r}"' for r in writable_roots) + "]"
    return (
        f"{leading_ws}codex -c sandbox_workspace_write.writable_roots={roots_toml}"
        f"{rest}"
    )


def _coerce_turn(result: Any) -> dict[str, Any]:
    """Extract the ``turn`` sub-object from a v2 result/notification payload.

    Returns ``{}`` when the field is missing or wrong-typed so callers can
    safely use ``.get(...)`` without isinstance guards.
    """
    if not isinstance(result, dict):
        return {}
    turn = result.get("turn")
    return turn if isinstance(turn, dict) else {}


class CodexAppServerBackend:
    """One subprocess instance per worker run; speaks Codex JSON-RPC."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        codex = init.cfg.codex
        self._codex = codex
        self._cwd = init.cwd
        self._workspace_root = init.workspace_root
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
        self._closed = False
        self._thread_id: str | None = None
        self._current_turn_id: str | None = None
        # v2 streams the assistant text via item/completed notifications
        # rather than returning it in the turn response, so we accumulate
        # the latest agentMessage text here.
        self._latest_assistant_message: str = ""
        self._turn_completion_waiter: asyncio.Future[dict[str, Any]] | None = None
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
        command, env = self._prepare_command_and_env()
        try:
            self._process = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=MAX_LINE_BYTES,
            )
        except FileNotFoundError as exc:
            raise CodexNotFound("bash not available", error=str(exc)) from exc
        if self._process.stdout is None or self._process.stdin is None:
            raise CodexNotFound("subprocess pipes not available")
        self._reader_task = asyncio.create_task(self._stdout_reader())
        self._stderr_task = asyncio.create_task(self._stderr_reader())

    def _prepare_command_and_env(self) -> tuple[str, dict[str, str]]:
        """Build the subprocess command and env, auto-fixing the symlink trap.

        When codex runs under ``workspace-write`` sandboxing and the workspace
        contains symlinks to host repo dirs (the common ``after_create``
        pattern), codex refuses writes through them. This pre-flight scans
        the workspace, resolves the symlink targets, and:

          1. injects ``-c sandbox_workspace_write.writable_roots=[...]`` after
             the ``codex`` token when the command starts with codex directly;
          2. exports ``SYMPHONY_CODEX_WRITABLE_ROOTS`` (``os.pathsep``-joined)
             so wrapper scripts can append the same override themselves.

        No-op when sandbox is not workspace-write or no symlinks exist.
        """
        env = os.environ.copy()
        command = self._codex.command
        if not _sandbox_uses_workspace_write(self._thread_sandbox, self._sandbox_policy):
            return command, env
        roots = _scan_workspace_symlinks(self._cwd, self._workspace_root)
        if not roots:
            return command, env
        env["SYMPHONY_CODEX_WRITABLE_ROOTS"] = os.pathsep.join(roots)
        new_command = _inject_writable_roots(command, roots)
        if new_command != command:
            log.info(
                "codex_writable_roots_injected",
                roots=roots,
                source="command",
            )
        else:
            log.info(
                "codex_writable_roots_exported",
                roots=roots,
                source="env",
                hint=(
                    "wrapper script detected; append `-c "
                    "sandbox_workspace_write.writable_roots=...` using "
                    "$SYMPHONY_CODEX_WRITABLE_ROOTS"
                ),
            )
        return new_command, env

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        # v2 has no `thread/stop` — `thread/archive` exists but only
        # finalizes server-side bookkeeping. Symphony already terminates
        # the subprocess below, which is sufficient for cleanup.
        if self._process is not None:
            if self._process.returncode is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass
                rc = await safe_proc_wait(self._process, timeout=2.0)
                if rc is None and self._process.returncode is None:
                    try:
                        self._process.kill()
                    except ProcessLookupError:
                        pass
                    await safe_proc_wait(self._process)
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
        # The v2 codex app-server `initialize` schema is just `clientInfo`
        # plus optional `capabilities`. Tools are no longer declared at
        # initialize time — they're handled per-thread / per-turn now.
        params = {
            "clientInfo": {"name": "symphony", "version": "0.2.0"},
        }
        return await self._request(METHOD_INITIALIZE, params)

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # v2 `thread/start` no longer accepts `initialPrompt` — the prompt
        # is delivered via the first `turn/start`. Orchestrator already
        # passes the rendered prompt as the first run_turn input, so we
        # discard `initial_prompt` and `issue_title` here.
        del initial_prompt, issue_title
        params: dict[str, Any] = {"cwd": str(self._cwd)}
        if self._thread_sandbox is not None:
            params["sandbox"] = self._thread_sandbox
        if self._approval_policy is not None:
            params["approvalPolicy"] = self._approval_policy
        result = await self._request(METHOD_THREAD_START, params)
        # v2 returns `result.thread.id` (Thread object); tolerate the legacy
        # `threadId` shape so older mock servers still work.
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = (
            (thread.get("id") if isinstance(thread, dict) else None)
            or result.get("threadId")
            or result.get("thread_id")
            or result.get("id")
        )
        if not thread_id:
            raise ResponseError(
                "codex app-server returned no thread id from thread/start",
                method=METHOD_THREAD_START,
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
        del is_continuation  # the legacy `continuation` flag is gone in v2
        params = self._build_turn_params(prompt)
        completion = self._arm_completion_waiter()
        try:
            turn = await self._send_turn_and_resolve(params, completion)
            await self._raise_for_terminal_status(turn)
            await self._emit(EVENT_TURN_COMPLETED, turn)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._current_turn_id,
                last_message=self._latest_assistant_message,
            )
        finally:
            self._turn_completion_waiter = None

    def _build_turn_params(self, prompt: str) -> dict[str, Any]:
        # v2 wraps turn input as a UserInput[] array; tolerate empty prompts
        # so callers passing "" don't get a malformed payload.
        params: dict[str, Any] = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        sandbox_payload = _sandbox_policy_to_turn_payload(self._sandbox_policy)
        if sandbox_payload is not None:
            params["sandboxPolicy"] = sandbox_payload
        if self._approval_policy is not None:
            params["approvalPolicy"] = self._approval_policy
        if self._codex.model:
            params["model"] = self._codex.model
        if self._codex.reasoning_effort:
            params["effort"] = self._codex.reasoning_effort
        return params

    def _arm_completion_waiter(self) -> asyncio.Future[dict[str, Any]]:
        # Arm *before* sending so a fast server can't notify before we listen.
        completion: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._turn_completion_waiter = completion
        return completion

    async def _send_turn_and_resolve(
        self,
        params: dict[str, Any],
        completion: asyncio.Future[dict[str, Any]],
    ) -> dict[str, Any]:
        """Send turn/start; if status=inProgress, await turn/completed notif.

        Returns the final Turn dict for downstream status parsing.
        """
        try:
            result = await self._request(
                METHOD_TURN_START,
                params,
                timeout_s=self._codex.turn_timeout_ms / 1000.0,
            )
        except ResponseTimeout as exc:
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout("turn timed out") from exc

        turn = _coerce_turn(result)
        turn_id = (
            turn.get("id")
            or result.get("turnId")
            or result.get("turn_id")
            or result.get("id")
        )
        self._current_turn_id = str(turn_id) if turn_id is not None else None

        status = (turn.get("status") or result.get("status") or "").strip()
        if status != "inProgress":
            return turn

        # Wait for `turn/completed` notification (or matching error).
        try:
            final = await asyncio.wait_for(
                completion,
                timeout=self._codex.turn_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError as exc:
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout("turn timed out waiting for completion") from exc
        final_turn = _coerce_turn(final)
        return final_turn or turn

    async def _raise_for_terminal_status(self, turn: dict[str, Any]) -> None:
        # Status mapping: v2 enum is {completed, interrupted, failed,
        # inProgress}; legacy was {turn_completed, turn_failed, …}.
        status = (turn.get("status") or "").strip()
        if status in ("completed", ""):
            return
        if status == "interrupted":
            await self._emit(EVENT_TURN_CANCELLED, turn)
            raise TurnCancelled("turn interrupted")
        if status == "failed":
            err = turn.get("error") or {}
            await self._emit(EVENT_TURN_FAILED, turn)
            if isinstance(err, dict):
                msg = err.get("message") or err.get("type") or "turn failed"
            else:
                msg = str(err)
            raise TurnFailed(msg)
        # Unknown status — don't silently coerce to success.
        await self._emit(EVENT_TURN_FAILED, turn)
        raise TurnFailed(f"unexpected turn status {status!r}")

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
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
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

    async def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or msg.get("event") or ""
        params = msg.get("params") or msg.get("payload") or msg
        # ----- token usage -----
        # Two payload shapes seen in the wild:
        #   1. Legacy (≤0.129):  params.totals.{input_tokens,output_tokens,total_tokens}
        #   2. v2 (0.130+):       params.tokenUsage.total.{inputTokens,outputTokens,
        #                          totalTokens, cachedInputTokens, reasoningOutputTokens}
        # Both are reported as ABSOLUTE cumulative totals — we overwrite
        # rather than accumulate.
        if method == NOTIF_THREAD_TOKEN_USAGE or method.endswith(
            "/tokenUsage/updated"
        ):
            handled = False
            # Try v2 shape first (more recent codex builds).
            if isinstance(params, dict):
                tu = params.get("tokenUsage")
                if isinstance(tu, dict):
                    block = tu.get("total") or tu.get("last")
                    if isinstance(block, dict):
                        self._update_tokens_from_v2_block(block)
                        handled = True
            # Fall back to legacy `totals` (or top-level snake_case).
            if not handled:
                self._update_tokens_absolute(
                    params.get("totals") if isinstance(params, dict) else None
                    or params
                )
            return
        # ----- rate limits (v2: account/rateLimits/updated) -----
        if method == NOTIF_RATE_LIMITS or method.endswith("/rateLimits"):
            rl = params.get("rateLimits") if isinstance(params, dict) else None
            if isinstance(rl, dict):
                self._latest_rate_limits = rl
            elif isinstance(params, dict) and "rateLimits" not in params:
                self._latest_rate_limits = params
            return
        # ----- turn lifecycle -----
        if method == NOTIF_TURN_COMPLETED:
            waiter = self._turn_completion_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(params if isinstance(params, dict) else {})
            return
        # ----- streamed thread items (assistant text, tool calls, etc.) -----
        if method == NOTIF_ITEM_COMPLETED:
            item = params.get("item") if isinstance(params, dict) else None
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    # Cap the dashboard preview at 1000 chars; mark truncation
                    # explicitly so a "…" trail isn't mistaken for prose.
                    if len(text) > _ASSISTANT_MESSAGE_PREVIEW_CAP:
                        self._latest_assistant_message = (
                            text[:_ASSISTANT_MESSAGE_PREVIEW_CAP] + "…"
                        )
                    else:
                        self._latest_assistant_message = text
                    await self._emit(
                        EVENT_OTHER_MESSAGE,
                        {
                            "type": "assistant",
                            "message": self._latest_assistant_message,
                            "item": item,
                        },
                    )
            elif isinstance(item, dict):
                await self._emit(EVENT_OTHER_MESSAGE, {"item": item})
            return
        # ----- legacy approval / tool-call requests -----
        if method == "approval.requested":
            await self._handle_approval(params)
            return
        if method == "tool.requested":
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

    def _update_tokens_from_v2_block(self, usage: dict[str, Any]) -> None:
        """Codex 0.130+ thread/tokenUsage/updated.tokenUsage.total shape.

        Fields seen in the wild (camelCase, captured from a real run):
          inputTokens, cachedInputTokens, outputTokens,
          reasoningOutputTokens, totalTokens

        Symphony's three-bucket model has no separate cache or reasoning
        buckets, so cached input is folded into input_tokens and reasoning
        output is folded into output_tokens.

        Codex's own `totalTokens` field excludes both cache reads and
        reasoning output (it counts only `inputTokens + outputTokens`),
        which would make symphony's three-bucket invariant fail —
        `input_tokens + output_tokens > total_tokens`. To preserve that
        invariant and stay unit-comparable with the other backends,
        symphony recomputes `total_tokens` as `folded_in + folded_out`
        rather than copying codex's narrower value.

        These notifications report ABSOLUTE cumulative totals (not deltas),
        so we overwrite rather than accumulate.
        """
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("inputTokens") or 0)
        cached = int(usage.get("cachedInputTokens") or 0)
        out_t = int(usage.get("outputTokens") or 0)
        reasoning = int(usage.get("reasoningOutputTokens") or 0)
        folded_in = in_t + cached
        folded_out = out_t + reasoning
        # Always compute total = folded_in + folded_out so the invariant
        # `total_tokens == input_tokens + output_tokens` holds. Codex's
        # narrower `totalTokens` is intentionally ignored.
        self._latest_usage["input_tokens"] = folded_in
        self._latest_usage["output_tokens"] = folded_out
        self._latest_usage["total_tokens"] = folded_in + folded_out

    async def _handle_approval(self, params: dict[str, Any]) -> None:
        # Best-effort auto-approve. The legacy `respondToApproval` method is
        # gone in v2 — set `approval_policy: never` in WORKFLOW.md to
        # prevent codex from asking in the first place. If we *do* get here
        # despite that, log and emit but don't try the v2 equivalent
        # (`thread/approveGuardianDeniedAction`) without verifying its
        # exact param shape per release.
        approval_id = params.get("id") or params.get("approvalId")
        if approval_id is None:
            return
        await self._emit(EVENT_APPROVAL_AUTO_APPROVED, params)
        log.warning(
            "codex_approval_received_but_v2_respond_unimplemented",
            approval_id=str(approval_id),
            hint="set codex.approval_policy: never to avoid this path",
        )

    async def _handle_tool_call(self, params: dict[str, Any]) -> None:
        # Same caveat as `_handle_approval` — the legacy `respondToToolCall`
        # method has no direct v2 replacement. Symphony advertises no
        # tools at initialize time anymore, so codex shouldn't request
        # symphony-side tools — we emit a diagnostic event and return.
        await self._emit(EVENT_UNSUPPORTED_TOOL_CALL, params)
        log.warning(
            "codex_tool_call_received_but_v2_respond_unimplemented",
            tool=str(params.get("name") or params.get("tool") or ""),
        )

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
