"""SPEC §7, §8, §16 — orchestrator state machine.

The orchestrator is the single authority for scheduling state. All worker
outcomes are reported back through asyncio queues and converted into
explicit state transitions (§7.0).

Concurrency model:
- One asyncio event loop owns mutation of `running`, `claimed`, and
  `retry_attempts`. Workers run as tasks; tracker calls run in a thread
  executor; codex events arrive via async callbacks routed through a queue.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .backends import (
    EVENT_AGENT_RETRY,
    EVENT_COMPACTION,
    EVENT_TURN_FAILED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    BackendInit,
    build_backend,
)
from .archive import select_archivable
from .backends.codex import linear_graphql_tool
from .errors import (
    SymphonyError,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
    TurnCancelled,
)
from .issue import Issue, normalize_state, sort_for_dispatch
from .logging import get_logger
from .prompt import build_prompt_env, render
from .tracker import build_tracker_client
from .workflow import (
    ServiceConfig,
    WorkflowState,
    validate_for_dispatch,
)
from .workspace import WorkspaceManager


log = get_logger()


CONTINUATION_RETRY_DELAY_MS = 1_000  # §7.1
RETRY_BASE_MS = 10_000  # §8.4


# ---------------------------------------------------------------------------
# Runtime data structures
# ---------------------------------------------------------------------------


@dataclass
class RunningEntry:
    issue: Issue
    started_at: datetime
    retry_attempt: int | None
    worker_task: asyncio.Task[None]
    workspace_path: Path
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    turn_count: int = 0
    last_codex_event: str | None = None
    last_codex_message: str = ""
    last_codex_timestamp: datetime | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    codex_app_server_pid: int | None = None
    last_error: str | None = None


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    timer_handle: asyncio.TimerHandle
    error: str | None = None


@dataclass
class _CodexTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    seconds_running: float = 0.0


# Keep snapshot of recent events for §13.7 issue endpoint.
@dataclass
class _IssueDebug:
    restart_count: int = 0
    current_retry_attempt: int = 0
    last_workspace: Path | None = None
    last_error: str | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    def __init__(
        self,
        workflow_state: WorkflowState,
    ) -> None:
        self._workflow_state = workflow_state
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: dict[str, RunningEntry] = {}
        self._claimed: set[str] = set()
        self._retry: dict[str, RetryEntry] = {}
        self._completed: set[str] = set()
        self._totals = _CodexTotals()
        self._latest_rate_limits: dict[str, Any] | None = None
        self._issue_debug: dict[str, _IssueDebug] = {}
        self._workspace_manager: WorkspaceManager | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._tick_event = asyncio.Event()
        self._stopping = False
        self._refresh_pending = False
        self._observers: list[Callable[[], Awaitable[None]]] = []

    # ------------------------------------------------------------------
    # public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        cfg = self._workflow_state.current()
        if cfg is None:
            cfg, err = self._workflow_state.reload()
            if err is not None or cfg is None:
                raise err or SymphonyError("workflow not loaded")
        validate_for_dispatch(cfg)
        self._workspace_manager = WorkspaceManager(cfg.workspace_root, cfg.hooks, workflow_dir=cfg.workflow_path.parent)
        await self._startup_terminal_cleanup(cfg)
        self._tick_task = asyncio.create_task(self._tick_loop(), name="symphony-tick")

    async def stop(self) -> None:
        self._stopping = True
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):
                pass
        for entry in list(self._running.values()):
            entry.worker_task.cancel()
        for entry in list(self._retry.values()):
            entry.timer_handle.cancel()
        for entry in list(self._running.values()):
            try:
                await entry.worker_task
            except (asyncio.CancelledError, Exception):
                pass
        self._running.clear()
        self._retry.clear()

    # ------------------------------------------------------------------
    # observers (§13)
    # ------------------------------------------------------------------

    def add_observer(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._observers.append(callback)

    async def _notify_observers(self) -> None:
        for cb in list(self._observers):
            try:
                await cb()
            except Exception as exc:
                log.warning("observer_failed", error=str(exc))

    # ------------------------------------------------------------------
    # snapshot / API surface (§13.3, §13.7)
    # ------------------------------------------------------------------

    def request_refresh(self) -> bool:
        """§13.7.2 POST /refresh — schedule an immediate tick."""
        if self._refresh_pending:
            return True  # coalesced
        self._refresh_pending = True
        self._tick_event.set()
        return False

    def iter_running_issues(self) -> tuple[Issue, ...]:
        """Return the issues currently owned by running workers."""
        return tuple(entry.issue for entry in self._running.values())

    def snapshot(self) -> dict[str, Any]:
        running_rows = [self._running_row(eid, entry) for eid, entry in self._running.items()]
        retry_rows = [self._retry_row(entry) for entry in self._retry.values()]
        active_seconds = sum(
            (datetime.now(timezone.utc) - entry.started_at).total_seconds()
            for entry in self._running.values()
        )
        return {
            "generated_at": _utc_iso_z(),
            "counts": {"running": len(running_rows), "retrying": len(retry_rows)},
            "running": running_rows,
            "retrying": retry_rows,
            "codex_totals": {
                "input_tokens": self._totals.input_tokens,
                "output_tokens": self._totals.output_tokens,
                "total_tokens": self._totals.total_tokens,
                "seconds_running": round(self._totals.seconds_running + active_seconds, 1),
            },
            "rate_limits": self._latest_rate_limits,
        }

    def issue_snapshot(self, identifier: str) -> dict[str, Any] | None:
        for issue_id, entry in self._running.items():
            if entry.issue.identifier == identifier:
                debug = self._issue_debug.get(issue_id, _IssueDebug())
                return {
                    "issue_identifier": entry.issue.identifier,
                    "issue_id": issue_id,
                    "status": "running",
                    "workspace": {"path": str(entry.workspace_path)},
                    "attempts": {
                        "restart_count": debug.restart_count,
                        "current_retry_attempt": debug.current_retry_attempt,
                    },
                    "running": self._running_row(issue_id, entry),
                    "retry": None,
                    "logs": {"codex_session_logs": []},
                    "recent_events": list(debug.recent_events[-20:]),
                    "last_error": entry.last_error,
                    "tracked": {},
                }
        for issue_id, retry in self._retry.items():
            if retry.identifier == identifier:
                debug = self._issue_debug.get(issue_id, _IssueDebug())
                return {
                    "issue_identifier": identifier,
                    "issue_id": issue_id,
                    "status": "retrying",
                    "workspace": {
                        "path": str(debug.last_workspace) if debug.last_workspace else None
                    },
                    "attempts": {
                        "restart_count": debug.restart_count,
                        "current_retry_attempt": retry.attempt,
                    },
                    "running": None,
                    "retry": self._retry_row(retry),
                    "logs": {"codex_session_logs": []},
                    "recent_events": list(debug.recent_events[-20:]),
                    "last_error": retry.error,
                    "tracked": {},
                }
        return None

    def _running_row(self, issue_id: str, entry: RunningEntry) -> dict[str, Any]:
        return {
            "issue_id": issue_id,
            "issue_identifier": entry.issue.identifier,
            "state": entry.issue.state,
            "session_id": entry.session_id,
            "turn_count": entry.turn_count,
            "last_event": entry.last_codex_event,
            "last_message": entry.last_codex_message,
            "started_at": _to_iso(entry.started_at),
            "last_event_at": _to_iso(entry.last_codex_timestamp),
            "tokens": {
                "input_tokens": entry.codex_input_tokens,
                "output_tokens": entry.codex_output_tokens,
                "total_tokens": entry.codex_total_tokens,
            },
        }

    @staticmethod
    def _retry_row(entry: RetryEntry) -> dict[str, Any]:
        return {
            "issue_id": entry.issue_id,
            "issue_identifier": entry.identifier,
            "attempt": entry.attempt,
            "due_at": _from_monotonic_to_iso(entry.due_at_ms),
            "error": entry.error,
        }

    # ------------------------------------------------------------------
    # tick loop (§16.2)
    # ------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        # Fire an immediate tick.
        while not self._stopping:
            await self._on_tick()
            cfg = self._workflow_state.current()
            poll_ms = cfg.poll_interval_ms if cfg is not None else 30_000
            try:
                await asyncio.wait_for(self._tick_event.wait(), timeout=poll_ms / 1000.0)
            except asyncio.TimeoutError:
                pass
            self._tick_event.clear()
            self._refresh_pending = False

    async def _on_tick(self) -> None:
        cfg, err = self._workflow_state.reload()
        if err is not None and cfg is None:
            cfg = self._workflow_state.current()
            if cfg is None:
                log.error("workflow_unavailable", error=str(err))
                await self._notify_observers()
                return
            log.warning("workflow_reload_failed", error=str(err))
        assert cfg is not None
        # Apply hot-reloadable settings.
        if self._workspace_manager is not None and self._workspace_manager.root != cfg.workspace_root.resolve():
            log.info("workspace_root_changed", new=str(cfg.workspace_root))
            self._workspace_manager = WorkspaceManager(cfg.workspace_root, cfg.hooks, workflow_dir=cfg.workflow_path.parent)
        elif self._workspace_manager is not None:
            self._workspace_manager.update_hooks(cfg.hooks)

        await self._reconcile_running(cfg)

        try:
            validate_for_dispatch(cfg)
        except SymphonyError as exc:
            log.error("dispatch_validation_failed", error=str(exc))
            await self._notify_observers()
            return

        # Fetch candidates.
        try:
            candidates = await self._fetch_candidates(cfg)
        except Exception as exc:
            log.warning("candidate_fetch_failed", error=str(exc))
            await self._notify_observers()
            return

        for issue in sort_for_dispatch(candidates):
            if self._available_slots(cfg) <= 0:
                break
            if self._should_dispatch(issue, cfg):
                self._dispatch(issue, cfg, attempt=None)

        await self._archive_sweep(cfg)

        await self._notify_observers()

    async def _archive_sweep(self, cfg: ServiceConfig) -> None:
        """Auto-archive terminal-state issues older than `archive_after_days`.

        Runs once per tick. Disabled when `archive_after_days <= 0`. Failures
        are logged and swallowed — one stale issue should not break the tick.
        """
        if cfg.tracker.archive_after_days <= 0:
            return
        try:
            terminal_issues = await asyncio.to_thread(
                self._tracker_call_terminal_issues, cfg
            )
        except Exception as exc:
            log.warning("archive_sweep_fetch_failed", error=str(exc))
            return
        stale = select_archivable(
            terminal_issues,
            terminal_states=cfg.tracker.terminal_states,
            archive_state=cfg.tracker.archive_state,
            archive_after_days=cfg.tracker.archive_after_days,
        )
        for issue in stale:
            try:
                await asyncio.to_thread(
                    self._tracker_call_update_state,
                    cfg,
                    issue,
                    cfg.tracker.archive_state,
                )
                log.info(
                    "archive_sweep_moved",
                    identifier=issue.identifier,
                    target=cfg.tracker.archive_state,
                )
            except Exception as exc:
                log.warning(
                    "archive_sweep_update_failed",
                    identifier=issue.identifier,
                    error=str(exc),
                )

    @staticmethod
    def _tracker_call_update_state(
        cfg: ServiceConfig, issue: Issue, target_state: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            client.update_state(issue, target_state)
        finally:
            client.close()

    # ------------------------------------------------------------------
    # candidate selection (§8.2)
    # ------------------------------------------------------------------

    def _should_dispatch(self, issue: Issue, cfg: ServiceConfig) -> bool:
        """§8.2 — eligibility for the poll-tick dispatch path."""
        return self._eligible(issue, cfg, owning_retry=False)

    def _eligible(
        self, issue: Issue, cfg: ServiceConfig, *, owning_retry: bool
    ) -> bool:
        """Shared eligibility logic.

        `owning_retry=True` is set by the retry handler — it already owns the
        issue's claim (§7.1: `Claimed = Running or RetryQueued`), so the
        `_claimed`/`_running` self-membership checks would otherwise create a
        false-negative loop where the retry timer keeps rescheduling itself.
        """
        if issue.id in self._running:
            return False
        if not owning_retry and issue.id in self._claimed:
            return False
        active = {s.lower() for s in cfg.tracker.active_states}
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        state = normalize_state(issue.state)
        if state in terminal or state not in active:
            return False
        if not (issue.id and issue.identifier and issue.title and issue.state):
            return False
        # Per-state limit (§8.3).
        per_state_cap = cfg.agent.max_concurrent_agents_by_state.get(state)
        if per_state_cap is not None:
            current_in_state = sum(
                1
                for entry in self._running.values()
                if normalize_state(entry.issue.state) == state
            )
            if current_in_state >= per_state_cap:
                return False
        # Blocker rule for Todo (§8.2).
        if state == "todo" and issue.blocked_by:
            for blocker in issue.blocked_by:
                if not blocker.state or normalize_state(blocker.state) not in terminal:
                    return False
        return True

    def _available_slots(self, cfg: ServiceConfig) -> int:
        return max(cfg.agent.max_concurrent_agents - len(self._running), 0)

    # ------------------------------------------------------------------
    # dispatch (§16.4)
    # ------------------------------------------------------------------

    def _dispatch(self, issue: Issue, cfg: ServiceConfig, *, attempt: int | None) -> None:
        # Cancel any existing retry timer.
        existing_retry = self._retry.pop(issue.id, None)
        if existing_retry is not None:
            existing_retry.timer_handle.cancel()

        worker_task = asyncio.create_task(
            self._run_agent_attempt(issue, attempt, cfg),
            name=f"symphony-worker-{issue.identifier}",
        )
        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=attempt,
            worker_task=worker_task,
            workspace_path=self._workspace_manager.path_for(issue.identifier)
            if self._workspace_manager
            else Path("/"),
        )
        self._running[issue.id] = entry
        self._claimed.add(issue.id)
        debug = self._issue_debug.setdefault(issue.id, _IssueDebug())
        if attempt is not None:
            debug.restart_count += 1
        log.info(
            "dispatch",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt,
        )

    # ------------------------------------------------------------------
    # worker (§16.5)
    # ------------------------------------------------------------------

    async def _run_agent_attempt(
        self, issue: Issue, attempt: int | None, cfg: ServiceConfig
    ) -> None:
        outcome: str = "normal"
        error: str | None = None
        try:
            assert self._workspace_manager is not None
            workspace = await self._workspace_manager.create_or_reuse(issue.identifier)
            self._running[issue.id].workspace_path = workspace.path
            try:
                await self._workspace_manager.before_run(workspace.path)
            except Exception as exc:
                outcome = "before_run_error"
                error = str(exc)
                return

            tools = []
            if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
                tools.append(linear_graphql_tool())

            client = build_backend(
                BackendInit(
                    cfg=cfg,
                    cwd=workspace.path,
                    workspace_root=cfg.workspace_root,
                    on_event=lambda ev: self._on_codex_event(issue.id, ev),
                    client_tools=tools,
                )
            )
            try:
                await client.start()
                await client.initialize()

                turn_number = 1
                env = build_prompt_env(issue, attempt)
                env["turn_number"] = turn_number
                env["max_turns"] = cfg.agent.max_turns
                first_prompt = render(cfg.prompt_template, env)
                await client.start_session(
                    initial_prompt=first_prompt,
                    issue_title=f"{issue.identifier}: {issue.title}",
                )

                while True:
                    is_continuation = turn_number > 1
                    if is_continuation:
                        env = {
                            "issue": issue.to_template_dict(),
                            "attempt": attempt,
                            "turn_number": turn_number,
                            "max_turns": cfg.agent.max_turns,
                        }
                        prompt = (
                            "Continue working on the issue. Re-check the tracker if needed. "
                            f"This is turn {turn_number} of up to {cfg.agent.max_turns}."
                        )
                    else:
                        prompt = first_prompt

                    self._running[issue.id].turn_count = turn_number
                    # Symmetry with worker_turn_completed — a single line per
                    # turn-start so multi-turn runs (especially slow ones
                    # like gemini -p where a single turn can take 60-90s)
                    # don't look stuck between turns.
                    log.info(
                        "worker_turn_started",
                        issue_id=issue.id,
                        identifier=self._running[issue.id].issue.identifier,
                        turn=turn_number,
                        max_turns=cfg.agent.max_turns,
                        is_continuation=is_continuation,
                    )
                    try:
                        await client.run_turn(prompt=prompt, is_continuation=is_continuation)
                    except (TurnTimeout, TurnFailed, TurnCancelled, TurnInputRequired) as exc:
                        outcome = "turn_error"
                        error = str(exc)
                        return

                    # Synchronous log on the worker's hot path — the
                    # listener-side `agent_turn_completed` log fires from
                    # `_on_codex_event` via the EVENT_TURN_COMPLETED emit,
                    # but reconcile can cancel the worker between the emit
                    # and the listener running, swallowing the visibility
                    # signal. Logging here guarantees one line per
                    # successful turn even when reconcile races us.
                    running_entry = self._running.get(issue.id)
                    if running_entry is not None:
                        log.info(
                            "worker_turn_completed",
                            issue_id=issue.id,
                            identifier=running_entry.issue.identifier,
                            turn=turn_number,
                            input_tokens=running_entry.codex_input_tokens,
                            output_tokens=running_entry.codex_output_tokens,
                            total_tokens=running_entry.codex_total_tokens,
                        )

                    # Refresh issue state.
                    refreshed = await self._refresh_issue_state(cfg, issue.id)
                    if refreshed is None:
                        outcome = "issue_state_refresh_failed"
                        error = "could not refresh issue state"
                        return
                    issue = refreshed
                    self._running[issue.id].issue = issue
                    state = normalize_state(issue.state)
                    active = {s.lower() for s in cfg.tracker.active_states}
                    if state not in active:
                        break
                    if turn_number >= cfg.agent.max_turns:
                        break
                    turn_number += 1
            finally:
                await client.stop()
                await self._workspace_manager.after_run_best_effort(workspace.path)
        except SymphonyError as exc:
            outcome = "error"
            error = str(exc)
        except Exception as exc:
            outcome = "error"
            error = str(exc)
            log.error("worker_unhandled_error", issue_id=issue.id, error=str(exc))
        finally:
            await self._on_worker_exit(issue.id, outcome, error)

    async def _refresh_issue_state(
        self, cfg: ServiceConfig, issue_id: str
    ) -> Issue | None:
        try:
            results = await asyncio.to_thread(
                self._tracker_call_states_by_ids, cfg, [issue_id]
            )
        except Exception as exc:
            log.warning("issue_state_refresh_failed", issue_id=issue_id, error=str(exc))
            return None
        for issue in results:
            if issue.id == issue_id:
                return issue
        return None

    # ------------------------------------------------------------------
    # codex events
    # ------------------------------------------------------------------

    async def _on_codex_event(self, issue_id: str, event: dict[str, Any]) -> None:
        entry = self._running.get(issue_id)
        if entry is None:
            return
        ev_name = str(event.get("event") or "")
        entry.last_codex_event = ev_name
        ts_text = event.get("timestamp")
        if isinstance(ts_text, str):
            try:
                entry.last_codex_timestamp = datetime.fromisoformat(
                    ts_text.replace("Z", "+00:00")
                )
            except ValueError:
                entry.last_codex_timestamp = datetime.now(timezone.utc)
        else:
            entry.last_codex_timestamp = datetime.now(timezone.utc)
        pid = event.get("codex_app_server_pid")
        if isinstance(pid, int):
            entry.codex_app_server_pid = pid
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            msg = payload.get("message") or payload.get("lastMessage") or ""
            if isinstance(msg, str):
                entry.last_codex_message = msg[:400]
        # Token deltas (§13.5).
        usage = event.get("usage") or {}
        if isinstance(usage, dict):
            self._apply_token_totals(entry, usage)
        # Rate limits.
        rl = event.get("rate_limits")
        if isinstance(rl, dict):
            self._latest_rate_limits = rl
        # Update session id when known. The backend reports a single session
        # identifier; this orchestrator stores it as `thread_id` for legacy
        # snapshot-shape stability and mirrors it as `session_id`. Codex
        # additionally exposes per-turn ids; when present they suffix the
        # session id so consumers can distinguish turns. Non-Codex backends
        # never set `turn_id`, so the suffix is silently skipped for them.
        if ev_name == EVENT_SESSION_STARTED:
            sid = (
                payload.get("session_id")
                or payload.get("thread_id")
                or payload.get("threadId")
            ) if isinstance(payload, dict) else None
            if sid:
                entry.thread_id = str(sid)
                entry.session_id = entry.thread_id
            log.info(
                "agent_session_started",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                session_id=entry.session_id,
            )
        if ev_name == EVENT_TURN_COMPLETED:
            turn_id = payload.get("turnId") or payload.get("turn_id")
            if turn_id and entry.thread_id:
                entry.turn_id = str(turn_id)
                entry.session_id = f"{entry.thread_id}-{entry.turn_id}"
            log.info(
                "agent_turn_completed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                turn=entry.turn_count,
                input_tokens=entry.codex_input_tokens,
                output_tokens=entry.codex_output_tokens,
                total_tokens=entry.codex_total_tokens,
                last_message=(entry.last_codex_message or "")[:160],
            )
        if ev_name == EVENT_TURN_FAILED:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            stderr_tail = payload.get("stderr_tail") if isinstance(payload, dict) else None
            log.warning(
                "agent_turn_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                turn=entry.turn_count,
                reason=str(reason) if reason else "",
                stderr_tail=stderr_tail if isinstance(stderr_tail, list) else None,
            )
        if ev_name == EVENT_COMPACTION:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            log.info(
                "agent_compaction",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                reason=str(payload.get("reason") or "")
                if isinstance(payload, dict) else "",
                tokens_before=payload.get("tokens_before")
                if isinstance(payload, dict) else None,
            )
        if ev_name == EVENT_AGENT_RETRY:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            log.info(
                "agent_internal_retry",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                attempt=payload.get("attempt") if isinstance(payload, dict) else None,
                error=str(payload.get("error") or payload.get("final_error") or "")
                if isinstance(payload, dict) else "",
            )

        # Track recent events.
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.recent_events.append(
            {
                "at": ts_text or _utc_iso_z(),
                "event": ev_name,
                "message": entry.last_codex_message,
            }
        )
        if len(debug.recent_events) > 50:
            debug.recent_events = debug.recent_events[-50:]

    def _apply_token_totals(self, entry: RunningEntry, totals: dict[str, Any]) -> None:
        in_tok = int(totals.get("input_tokens") or 0)
        out_tok = int(totals.get("output_tokens") or 0)
        tot_tok = int(totals.get("total_tokens") or (in_tok + out_tok))
        # §13.5 — track deltas from last reported absolute totals.
        delta_in = max(in_tok - entry.last_reported_input_tokens, 0)
        delta_out = max(out_tok - entry.last_reported_output_tokens, 0)
        delta_total = max(tot_tok - entry.last_reported_total_tokens, 0)
        entry.last_reported_input_tokens = in_tok
        entry.last_reported_output_tokens = out_tok
        entry.last_reported_total_tokens = tot_tok
        entry.codex_input_tokens += delta_in
        entry.codex_output_tokens += delta_out
        entry.codex_total_tokens += delta_total
        self._totals.input_tokens += delta_in
        self._totals.output_tokens += delta_out
        self._totals.total_tokens += delta_total

    # ------------------------------------------------------------------
    # worker exit handling (§16.6)
    # ------------------------------------------------------------------

    async def _on_worker_exit(self, issue_id: str, reason: str, error: str | None) -> None:
        entry = self._running.pop(issue_id, None)
        if entry is None:
            return
        elapsed = (datetime.now(timezone.utc) - entry.started_at).total_seconds()
        self._totals.seconds_running += elapsed
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.last_workspace = entry.workspace_path
        debug.last_error = error

        if reason == "normal":
            self._completed.add(issue_id)
            self._schedule_retry(
                issue_id,
                identifier=entry.issue.identifier,
                attempt=1,
                delay_ms=CONTINUATION_RETRY_DELAY_MS,
                error=None,
            )
        else:
            next_attempt = (entry.retry_attempt or 0) + 1
            cfg = self._workflow_state.current()
            cap = cfg.agent.max_retry_backoff_ms if cfg is not None else 300_000
            delay_ms = min(RETRY_BASE_MS * (2 ** (next_attempt - 1)), cap)
            self._schedule_retry(
                issue_id,
                identifier=entry.issue.identifier,
                attempt=next_attempt,
                delay_ms=delay_ms,
                error=f"{reason}: {error}" if error else reason,
            )
        log.info(
            "worker_exit",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            reason=reason,
            error=error,
        )
        await self._notify_observers()

    # ------------------------------------------------------------------
    # retry handling (§16.6)
    # ------------------------------------------------------------------

    def _schedule_retry(
        self,
        issue_id: str,
        *,
        identifier: str,
        attempt: int,
        delay_ms: int,
        error: str | None,
    ) -> None:
        if self._loop is None:
            return
        existing = self._retry.pop(issue_id, None)
        if existing is not None:
            existing.timer_handle.cancel()
        due = self._loop.time() + delay_ms / 1000.0
        handle = self._loop.call_later(
            delay_ms / 1000.0,
            lambda: asyncio.create_task(self._on_retry_timer(issue_id)),
        )
        self._retry[issue_id] = RetryEntry(
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            due_at_ms=due * 1000.0,
            timer_handle=handle,
            error=error,
        )
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.current_retry_attempt = attempt

    async def _on_retry_timer(self, issue_id: str) -> None:
        retry = self._retry.pop(issue_id, None)
        if retry is None:
            return
        cfg = self._workflow_state.current()
        if cfg is None:
            self._claimed.discard(issue_id)
            return
        try:
            candidates = await self._fetch_candidates(cfg)
        except Exception as exc:
            self._schedule_retry(
                issue_id,
                identifier=retry.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error=f"retry poll failed: {exc}",
            )
            return
        match = next((i for i in candidates if i.id == issue_id), None)
        if match is None:
            self._claimed.discard(issue_id)
            log.info("retry_release", issue_id=issue_id, identifier=retry.identifier)
            return
        if not self._eligible(match, cfg, owning_retry=True):
            self._schedule_retry(
                issue_id,
                identifier=match.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error="not eligible at retry time",
            )
            return
        if self._available_slots(cfg) == 0:
            self._schedule_retry(
                issue_id,
                identifier=match.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error="no available orchestrator slots",
            )
            return
        self._dispatch(match, cfg, attempt=retry.attempt)

    # ------------------------------------------------------------------
    # reconciliation (§16.3)
    # ------------------------------------------------------------------

    async def _reconcile_running(self, cfg: ServiceConfig) -> None:
        # Part A: stall detection.
        _, _, stall_timeout_ms = cfg.backend_timeouts()
        if stall_timeout_ms > 0:
            now = datetime.now(timezone.utc)
            for issue_id, entry in list(self._running.items()):
                seen = entry.last_codex_timestamp or entry.started_at
                elapsed_ms = (now - seen).total_seconds() * 1000
                if elapsed_ms > stall_timeout_ms:
                    log.warning(
                        "stalled_session",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed_ms),
                    )
                    entry.worker_task.cancel()
        # Part B: tracker state refresh.
        running_ids = list(self._running.keys())
        if not running_ids:
            return
        try:
            refreshed = await asyncio.to_thread(
                self._tracker_call_states_by_ids, cfg, running_ids
            )
        except Exception as exc:
            log.debug("reconciliation_state_refresh_failed", error=str(exc))
            return
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        active = {s.lower() for s in cfg.tracker.active_states}
        # Grace period: a worker that just emitted an event is almost
        # certainly already inside its own natural-exit path (post run_turn).
        # Cancelling it now races the worker's own _refresh_issue_state and
        # tends to: (a) drop the in-flight EVENT_TURN_COMPLETED listener,
        # losing observability; (b) wipe the workspace before after_run can
        # capture artefacts. Reserve cancellation for genuinely-stuck
        # workers — the worker's own loop will exit cleanly within a tick
        # or two when the agent transitions to a terminal state.
        RECONCILE_RECENT_EVENT_GRACE_S = 10.0
        now = datetime.now(timezone.utc)
        for issue in refreshed:
            entry = self._running.get(issue.id)
            if entry is None:
                continue
            state = normalize_state(issue.state)
            if state in terminal:
                last_seen = entry.last_codex_timestamp
                age = (now - last_seen).total_seconds() if last_seen else None
                if age is not None and age < RECONCILE_RECENT_EVENT_GRACE_S:
                    # Active worker — let it exit on its own.
                    log.info(
                        "reconcile_skip_active_worker",
                        issue_id=issue.id,
                        identifier=issue.identifier,
                        state=issue.state,
                        last_event_age_s=round(age, 1),
                    )
                    continue
                log.info(
                    "reconcile_terminate_terminal",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    state=issue.state,
                    last_event_age_s=round(age, 1) if age is not None else None,
                )
                entry.worker_task.cancel()
                if self._workspace_manager is not None:
                    await self._workspace_manager.remove(entry.workspace_path)
            elif state in active:
                # Update in-memory issue snapshot.
                entry.issue = Issue(
                    id=issue.id,
                    identifier=issue.identifier or entry.issue.identifier,
                    title=issue.title or entry.issue.title,
                    description=entry.issue.description,
                    priority=entry.issue.priority,
                    state=issue.state,
                    branch_name=entry.issue.branch_name,
                    url=entry.issue.url,
                    labels=entry.issue.labels,
                    blocked_by=entry.issue.blocked_by,
                    created_at=entry.issue.created_at,
                    updated_at=entry.issue.updated_at,
                )
            else:
                log.info(
                    "reconcile_terminate_inactive",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    state=issue.state,
                )
                entry.worker_task.cancel()

    # ------------------------------------------------------------------
    # tracker access
    # ------------------------------------------------------------------

    async def _fetch_candidates(self, cfg: ServiceConfig) -> list[Issue]:
        return await asyncio.to_thread(self._tracker_call_candidates, cfg)

    @staticmethod
    def _tracker_call_candidates(cfg: ServiceConfig) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_candidate_issues()
        finally:
            client.close()

    @staticmethod
    def _tracker_call_states_by_ids(cfg: ServiceConfig, ids: list[str]) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issue_states_by_ids(ids)
        finally:
            client.close()

    @staticmethod
    def _tracker_call_terminal_issues(cfg: ServiceConfig) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issues_by_states(cfg.tracker.terminal_states)
        finally:
            client.close()

    # ------------------------------------------------------------------
    # startup cleanup (§8.6)
    # ------------------------------------------------------------------

    async def _startup_terminal_cleanup(self, cfg: ServiceConfig) -> None:
        try:
            terminals = await asyncio.to_thread(self._tracker_call_terminal_issues, cfg)
        except Exception as exc:
            log.warning("startup_terminal_fetch_failed", error=str(exc))
            return
        if self._workspace_manager is None:
            return
        for issue in terminals:
            path = self._workspace_manager.path_for(issue.identifier)
            if path.exists():
                await self._workspace_manager.remove(path)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _utc_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_monotonic_to_iso(due_at_ms: float) -> str:
    """Best-effort: project monotonic time onto wall clock for display."""
    loop = asyncio.get_event_loop()
    now_mono = loop.time() * 1000.0
    delta_seconds = max((due_at_ms - now_mono) / 1000.0, 0.0)
    target = datetime.now(timezone.utc).timestamp() + delta_seconds
    return datetime.fromtimestamp(target, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
