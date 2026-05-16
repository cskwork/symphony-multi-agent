"""WORKFLOW-PROGRESS.md mirror.

A `ProgressFileWriter` registers itself as an Orchestrator observer
(`orchestrator.add_observer`). Each fire snapshots the orchestrator,
re-scans the tracker for current state of every ticket, diffs against
the previously-observed states to detect transitions, and rewrites the
target file atomically. Failures only warn; they never propagate.

Headless invocation: `symphony WORKFLOW.md` (no `--tui`) keeps the
orchestrator running while this writer keeps WORKFLOW-PROGRESS.md
in sync, so non-TUI users still see live state.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import tracker as tracker_module
from .issue import Issue, normalize_state
from .logging import get_logger
from .workflow import ServiceConfig, WorkflowState

log = get_logger()


@dataclass(frozen=True)
class _Transition:
    at: datetime
    identifier: str
    from_state: str | None
    to_state: str


def _format_elapsed(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _format_tokens(total: int) -> str:
    if total <= 0:
        return ""
    if total < 1000:
        return f"{total} tok"
    return f"{total // 1000}k tok"


def _format_running_meta(row: dict[str, Any], now: datetime) -> str:
    started_at_text = row.get("started_at")
    elapsed = ""
    if isinstance(started_at_text, str):
        try:
            started = datetime.fromisoformat(started_at_text.replace("Z", "+00:00"))
            elapsed = _format_elapsed((now - started).total_seconds())
        except ValueError:
            elapsed = ""
    tokens_blob = row.get("tokens") or {}
    total = 0
    if isinstance(tokens_blob, dict):
        total = int(tokens_blob.get("total_tokens") or 0)
    parts: list[str] = []
    attempt_kind = row.get("attempt_kind")
    attempt = row.get("attempt")
    if attempt_kind == "retry" and attempt:
        parts.append(f"retry {attempt}")
    if elapsed:
        parts.append(elapsed)
    tok_text = _format_tokens(total)
    if tok_text:
        parts.append(tok_text)
    if row.get("paused"):
        parts.append("paused")
    return " · ".join(parts)


def _format_ticket_cell(
    issue: Issue,
    running_row: dict[str, Any] | None,
    retry_row: dict[str, Any] | None,
    now: datetime,
) -> str:
    if running_row is not None:
        meta = _format_running_meta(running_row, now)
        return f"{issue.identifier} ({meta})" if meta else issue.identifier
    if retry_row is not None:
        attempt = retry_row.get("attempt")
        kind = retry_row.get("kind") or "retry"
        return f"{issue.identifier} ({kind} {attempt})"
    return issue.identifier


def _ordered_states(cfg: ServiceConfig) -> list[str]:
    """Active states first (preserves user ordering), then terminal states."""
    seen: set[str] = set()
    out: list[str] = []
    for s in cfg.tracker.active_states:
        key = normalize_state(s)
        if key not in seen:
            seen.add(key)
            out.append(s)
    for s in cfg.tracker.terminal_states:
        key = normalize_state(s)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def render_progress_md(
    cfg: ServiceConfig,
    issues: Iterable[Issue],
    running_by_id: dict[str, dict[str, Any]],
    retry_by_id: dict[str, dict[str, Any]],
    transitions: Iterable[_Transition],
    *,
    generated_at: datetime,
) -> str:
    """Render the progress markdown body. Pure function — easy to test."""
    state_order = _ordered_states(cfg)
    by_state: dict[str, list[Issue]] = {s: [] for s in state_order}
    catchall: list[Issue] = []
    state_lookup = {normalize_state(s): s for s in state_order}
    for issue in issues:
        bucket = state_lookup.get(normalize_state(issue.state))
        if bucket is None:
            catchall.append(issue)
        else:
            by_state[bucket].append(issue)

    lines: list[str] = []
    lines.append("# Symphony Progress")
    lines.append(
        f"_Updated: {generated_at.strftime('%Y-%m-%d %H:%M:%S')} UTC_"
    )
    lines.append("")
    lines.append(f"_Workflow: {cfg.workflow_path}_")
    lines.append("")
    lines.append("## Kanban")
    lines.append("")
    lines.append("| State | Tickets |")
    lines.append("|-------|---------|")
    for state in state_order:
        items = sorted(by_state.get(state, ()), key=lambda i: i.identifier)
        if not items:
            cell = "—"
        else:
            cell = ", ".join(
                _format_ticket_cell(
                    i,
                    running_by_id.get(i.identifier),
                    retry_by_id.get(i.identifier),
                    generated_at,
                )
                for i in items
            )
        lines.append(f"| {state} | {cell} |")
    if catchall:
        rendered = ", ".join(
            f"{i.identifier} [{i.state}]"
            for i in sorted(catchall, key=lambda x: x.identifier)
        )
        lines.append(f"| _other_ | {rendered} |")
    lines.append("")

    transitions_list = list(transitions)
    if transitions_list:
        lines.append("## Recent transitions")
        lines.append("")
        for t in reversed(transitions_list):  # newest first
            from_text = t.from_state or "(new)"
            ts = t.at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                f"- `{ts}Z`  **{t.identifier}**  {from_text} → {t.to_state}"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Auto-generated by Symphony. Disable with `symphony --no-progress-md` "
        "or `progress.enabled: false` in WORKFLOW.md._"
    )
    return "\n".join(lines) + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    """Mirror tracker_file.write_ticket_atomic — temp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-progress-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ProgressFileWriter:
    """Observer that mirrors orchestrator state to a markdown file."""

    def __init__(
        self,
        orchestrator: Any,  # Orchestrator — typed as Any to avoid import cycle.
        workflow_state: WorkflowState,
        path: Path,
        *,
        max_transitions: int = 20,
    ) -> None:
        self._orch = orchestrator
        self._state = workflow_state
        self._path = Path(path)
        self._max_transitions = max(int(max_transitions), 0)
        self._last_states: dict[str, str] = {}
        self._transitions: deque[_Transition] = deque(maxlen=max(self._max_transitions, 1))
        self._first_render_done = False
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def register(self) -> None:
        self._orch.add_observer(self.on_change)

    async def on_change(self) -> None:
        # Serialise: the orchestrator may notify back-to-back (tick + worker
        # exit in the same loop iteration). One write at a time keeps the
        # transition log consistent.
        async with self._lock:
            try:
                await self._refresh()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "progress_md_refresh_failed",
                    path=str(self._path),
                    error=str(exc),
                )

    async def _refresh(self) -> None:
        cfg = self._state.current()
        if cfg is None:
            return
        issues = await asyncio.to_thread(self._scan_tracker, cfg)
        now = datetime.now(timezone.utc)
        seen: dict[str, str] = {}
        for issue in issues:
            seen[issue.identifier] = issue.state
        if self._first_render_done and self._max_transitions > 0:
            for ident, state in seen.items():
                prev = self._last_states.get(ident)
                if prev is not None and normalize_state(prev) != normalize_state(state):
                    self._transitions.append(
                        _Transition(at=now, identifier=ident, from_state=prev, to_state=state)
                    )
                elif prev is None:
                    self._transitions.append(
                        _Transition(at=now, identifier=ident, from_state=None, to_state=state)
                    )
            for ident, prev in self._last_states.items():
                if ident not in seen:
                    self._transitions.append(
                        _Transition(at=now, identifier=ident, from_state=prev, to_state="(removed)")
                    )
        self._last_states = seen
        self._first_render_done = True

        snap = self._orch.snapshot()
        running_by_id = {
            row["issue_identifier"]: row
            for row in snap.get("running", ())
            if isinstance(row, dict) and row.get("issue_identifier")
        }
        retry_by_id = {
            row["issue_identifier"]: row
            for row in snap.get("retrying", ())
            if isinstance(row, dict) and row.get("issue_identifier")
        }
        text = render_progress_md(
            cfg,
            issues,
            running_by_id,
            retry_by_id,
            list(self._transitions),
            generated_at=now,
        )
        await asyncio.to_thread(_atomic_write_text, self._path, text)

    @staticmethod
    def _scan_tracker(cfg: ServiceConfig) -> list[Issue]:
        states = list(cfg.tracker.active_states) + [
            s for s in cfg.tracker.terminal_states if s not in cfg.tracker.active_states
        ]
        try:
            with tracker_module.context_manager(cfg) as client:
                return client.fetch_issues_by_states(states)
        except Exception as exc:
            log.warning("progress_md_tracker_scan_failed", error=str(exc))
            return []
