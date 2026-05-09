"""CLI Kanban TUI.

Replaces the original HTML observability dashboard with a rich-rendered
Jira-style Kanban board printed to the terminal. Columns are tracker states
(active + terminal) and cards summarize each issue with its agent runtime
status (turn, last event, accumulated tokens, retry posture).

Usage:
    symphony tui ./WORKFLOW.md

The TUI runs alongside the orchestrator on the same event loop. It subscribes
as an observer so renders are pushed on each orchestrator tick, plus a 2 Hz
heartbeat refresh for elapsed-time updates between ticks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from rich.align import Align
from rich.box import ROUNDED, SIMPLE
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .i18n import t
from .issue import Issue, normalize_state
from .logging import get_logger
from .orchestrator import Orchestrator
from .tracker import build_tracker_client
from .workflow import ServiceConfig, WorkflowState


log = get_logger()


# Visual styling per state — Jira-ish color cues.
STATE_COLOR = {
    "todo": "bright_black",
    "in progress": "cyan",
    "blocked": "red",
    "review": "yellow",
    "done": "green",
    "cancelled": "magenta",
    "canceled": "magenta",
    "duplicate": "magenta",
    "closed": "green",
}

AGENT_COLOR = {
    "codex": "bright_blue",
    "claude": "bright_magenta",
    "gemini": "bright_yellow",
}


@dataclass
class _CardStatus:
    """Per-issue runtime overlay for a kanban card."""

    runtime: str = "idle"  # one of: idle, running, retrying, completed
    turn: int = 0
    last_event: str = ""
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempt: int | None = None
    error: str | None = None
    last_message: str = ""


class KanbanTUI:
    def __init__(
        self,
        orchestrator: Orchestrator,
        workflow_state: WorkflowState,
        *,
        console: Console | None = None,
    ) -> None:
        self._orch = orchestrator
        self._ws = workflow_state
        self._console = console or Console()
        self._candidates: list[Issue] = []
        self._terminal_issues: list[Issue] = []
        self._candidates_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._render_signal = asyncio.Event()

    # ------------------------------------------------------------------
    # public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        # Wake on every orchestrator tick so the board updates as state moves.
        self._orch.add_observer(self._on_orchestrator_tick)
        candidate_task = asyncio.create_task(self._candidate_refresh_loop())
        try:
            with Live(
                self._render(),
                console=self._console,
                refresh_per_second=4,
                screen=True,
                transient=False,
            ) as live:
                while not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._render_signal.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    self._render_signal.clear()
                    live.update(self._render())
        finally:
            self._stop.set()
            candidate_task.cancel()
            try:
                await candidate_task
            except (asyncio.CancelledError, Exception):
                pass

    def request_stop(self) -> None:
        self._stop.set()
        self._render_signal.set()

    async def _on_orchestrator_tick(self) -> None:
        self._render_signal.set()

    async def _candidate_refresh_loop(self) -> None:
        # Pull candidate + terminal issues from the tracker on the same
        # cadence as the orchestrator's poll. Errors are logged at debug;
        # the TUI keeps the last-known list rather than blanking. The wait
        # races against `_stop` so shutdown is responsive instead of waiting
        # out a full poll interval.
        while not self._stop.is_set():
            cfg = self._ws.current()
            if cfg is not None:
                await self._refresh_tracker(cfg)
            sleep_s = max(cfg.poll_interval_ms / 1000.0 if cfg else 30, 5)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                return  # stop fired
            except asyncio.TimeoutError:
                continue

    async def _refresh_tracker(self, cfg: ServiceConfig) -> None:
        try:
            candidates = await asyncio.to_thread(_fetch_candidates, cfg)
            terminals = await asyncio.to_thread(_fetch_terminals, cfg)
        except Exception as exc:
            log.debug("tui_tracker_fetch_failed", error=str(exc))
            return
        async with self._candidates_lock:
            self._candidates = candidates
            self._terminal_issues = terminals
        self._render_signal.set()

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def _render(self) -> Group:
        cfg = self._ws.current()
        snapshot = self._orch.snapshot()
        if cfg is None:
            return Group(Panel("workflow not loaded", border_style="red"))

        runtime_index = self._build_runtime_index(snapshot)
        column_panels = self._build_columns(cfg, runtime_index)
        header = self._build_header(cfg, snapshot)
        footer = self._build_footer(cfg, snapshot)
        return Group(header, Columns(column_panels, expand=True, equal=True), footer)

    def _build_header(self, cfg: ServiceConfig, snap: dict[str, Any]) -> Panel:
        lang = cfg.tui.language
        counts = snap.get("counts", {})
        agent_kind = cfg.agent.kind
        agent_color = AGENT_COLOR.get(agent_kind, "white")
        title = Text("symphony-multi-agent", style="bold")
        title.append(f"  {t('header.agent', lang)}", style="dim")
        title.append(agent_kind, style=f"bold {agent_color}")
        title.append(f"  {t('header.tracker', lang)}{cfg.tracker.kind}", style="dim")
        title.append(f"  {t('header.workflow', lang)}{cfg.workflow_path.name}", style="dim")
        title.append(f"  {t('header.lang', lang)}{lang}", style="bright_magenta")
        title.append(f"  {t('header.lang_hint', lang)}", style="dim italic")
        right = Text()
        right.append(f"{t('header.running', lang)}{counts.get('running', 0)}  ", style="green")
        right.append(f"{t('header.retrying', lang)}{counts.get('retrying', 0)}  ", style="yellow")
        right.append(f"{t('header.generated_at', lang)} {snap.get('generated_at', '?')}", style="dim")
        bar = Table.grid(expand=True)
        bar.add_column(justify="left", ratio=2)
        bar.add_column(justify="right", ratio=1)
        bar.add_row(title, right)
        return Panel(bar, box=SIMPLE, padding=(0, 1))

    def _build_footer(self, cfg: ServiceConfig, snap: dict[str, Any]) -> Panel:
        lang = cfg.tui.language
        totals = snap.get("codex_totals", {})
        rl = snap.get("rate_limits")
        line = Text()
        line.append(f"{t('footer.tokens', lang)}  ", style="dim")
        line.append(f"in={totals.get('input_tokens', 0):,}  ", style="cyan")
        line.append(f"out={totals.get('output_tokens', 0):,}  ", style="bright_cyan")
        line.append(f"total={totals.get('total_tokens', 0):,}", style="bold cyan")
        line.append("    ", style="dim")
        line.append(f"{t('footer.runtime', lang)}{totals.get('seconds_running', 0):.1f}s", style="dim")
        if rl:
            line.append(f"    {t('footer.rate_limits', lang)}", style="dim")
            line.append(_compact_rate_limits(rl), style="yellow")
        return Panel(line, box=SIMPLE, padding=(0, 1))

    def _build_columns(
        self, cfg: ServiceConfig, runtime_index: dict[str, _CardStatus]
    ) -> list[Panel]:
        empty_text = t("column.empty", cfg.tui.language)
        # Active columns first (in declared order), then terminal columns.
        column_states: list[str] = list(cfg.tracker.active_states) + list(
            cfg.tracker.terminal_states
        )
        # De-dup while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for s in column_states:
            key = normalize_state(s)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(s)

        issues_by_state: dict[str, list[Issue]] = {normalize_state(s): [] for s in ordered}
        for issue in self._all_known_issues():
            key = normalize_state(issue.state)
            issues_by_state.setdefault(key, []).append(issue)

        descriptions = cfg.tracker.state_descriptions
        panels: list[Panel] = []
        for state_label in ordered:
            state_key = normalize_state(state_label)
            issues = sorted(issues_by_state.get(state_key, []), key=_card_sort_key)
            color = STATE_COLOR.get(state_key, "white")
            cards = [
                self._render_card(
                    issue, runtime_index.get(issue.id, _CardStatus()), color, cfg.tui.language
                )
                for issue in issues
            ]
            legend = descriptions.get(state_key)
            elements: list[Any] = []
            if legend:
                elements.append(Text(legend, style="dim italic"))
            if cards:
                elements.extend(cards)
                body: Any = Group(*elements)
            elif legend:
                elements.append(
                    Align.center(Text(empty_text, style="dim italic"), vertical="middle")
                )
                body = Group(*elements)
            else:
                body = Align.center(Text(empty_text, style="dim italic"), vertical="middle")
            title = Text(f"{state_label} ", style=f"bold {color}")
            title.append(f"({len(issues)})", style="dim")
            panels.append(
                Panel(
                    body,
                    title=title,
                    border_style=color,
                    box=ROUNDED,
                    padding=(0, 1),
                )
            )
        return panels

    def _render_card(
        self, issue: Issue, status: _CardStatus, color: str, language: str = "en"
    ) -> Panel:
        title = Text(issue.identifier, style=f"bold {color}")
        if status.runtime == "running":
            title.append("  ●", style="bold green")
        elif status.runtime == "retrying":
            title.append("  ↻", style="bold yellow")
        elif status.runtime == "completed":
            title.append("  ✓", style="bold green")

        body = Text()
        body.append(_truncate(issue.title, 60), style="white")
        if issue.priority:
            body.append(f"  P{issue.priority}", style="bright_red bold")

        meta = Text()
        if status.runtime == "running":
            meta.append(f"{t('card.turn', language)} {status.turn}", style="green")
            if status.last_event:
                meta.append(f"  {status.last_event}", style="dim")
            if status.input_tokens or status.output_tokens or status.tokens:
                meta.append("  ", style="dim")
                meta.append(f"in={status.input_tokens:,}", style="cyan")
                meta.append(" / ", style="dim")
                meta.append(f"out={status.output_tokens:,}", style="bright_cyan")
                meta.append(" / ", style="dim")
                meta.append(f"total={status.tokens:,}", style="bold cyan")
        elif status.runtime == "retrying":
            meta.append(f"{t('card.retry', language)}{status.attempt}", style="yellow")
            if status.error:
                meta.append(f"  {_truncate(status.error, 40)}", style="dim red")
        elif issue.blocked_by:
            blocker_names = [b.identifier for b in issue.blocked_by[:3] if b.identifier]
            if blocker_names:
                meta.append(
                    f"{t('card.blocked_by', language)} {', '.join(blocker_names)}",
                    style="dim red",
                )
        elif issue.labels:
            meta.append("  ".join(f"#{l}" for l in issue.labels[:3]), style="dim")

        rows: list[Any] = [body]
        if meta.plain.strip():
            rows.append(meta)
        if status.last_message:
            rows.append(Text(_truncate(status.last_message, 90), style="dim italic"))

        return Panel(
            Group(*rows),
            title=title,
            title_align="left",
            border_style="bright_black",
            box=SIMPLE,
            padding=(0, 1),
        )

    # ------------------------------------------------------------------
    # data plumbing
    # ------------------------------------------------------------------

    def _all_known_issues(self) -> Iterable[Issue]:
        # Merge: tracker candidates + terminals + running entries' issues.
        seen_ids: set[str] = set()
        for issue in self._candidates:
            if issue.id in seen_ids:
                continue
            seen_ids.add(issue.id)
            yield issue
        for issue in self._terminal_issues:
            if issue.id in seen_ids:
                continue
            seen_ids.add(issue.id)
            yield issue
        for entry in self._orch._running.values():  # noqa: SLF001 — orchestrator co-design
            if entry.issue.id in seen_ids:
                continue
            seen_ids.add(entry.issue.id)
            yield entry.issue

    def _build_runtime_index(self, snap: dict[str, Any]) -> dict[str, _CardStatus]:
        index: dict[str, _CardStatus] = {}
        for row in snap.get("running", []):
            issue_id = row.get("issue_id") or ""
            tokens_block = row.get("tokens") or {}
            index[issue_id] = _CardStatus(
                runtime="running",
                turn=int(row.get("turn_count", 0) or 0),
                last_event=str(row.get("last_event") or ""),
                tokens=int(tokens_block.get("total_tokens") or 0),
                input_tokens=int(tokens_block.get("input_tokens") or 0),
                output_tokens=int(tokens_block.get("output_tokens") or 0),
                last_message=str(row.get("last_message") or ""),
            )
        for row in snap.get("retrying", []):
            issue_id = row.get("issue_id") or ""
            index[issue_id] = _CardStatus(
                runtime="retrying",
                attempt=int(row.get("attempt", 0) or 0),
                error=str(row.get("error") or "") or None,
            )
        return index


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: max(n - 1, 1)] + "…"


def _card_sort_key(issue: Issue) -> tuple[int, str]:
    # Higher priority (lower priority int) sorts first; Linear uses 1=urgent.
    pri = issue.priority if isinstance(issue.priority, int) and issue.priority > 0 else 99
    return (pri, issue.identifier)


def _compact_rate_limits(rl: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in rl.items():
        if isinstance(value, (int, float, str)):
            parts.append(f"{key}={value}")
        if len(parts) >= 3:
            break
    return ", ".join(parts) if parts else "n/a"


def _fetch_candidates(cfg: ServiceConfig) -> list[Issue]:
    client = build_tracker_client(cfg)
    try:
        return client.fetch_candidate_issues()
    finally:
        client.close()


def _fetch_terminals(cfg: ServiceConfig) -> list[Issue]:
    client = build_tracker_client(cfg)
    try:
        return client.fetch_issues_by_states(cfg.tracker.terminal_states)
    finally:
        client.close()
