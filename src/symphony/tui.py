"""Textual-based Kanban TUI for Symphony.

A modern terminal dashboard built on Textual (https://textual.textualize.io).
Replaces the previous hand-rolled Rich `Live` implementation: lanes are real
focusable widgets, cards are first-class, and mouse / keyboard / modals are
handled by the framework. The orchestrator hooks (snapshot polling + observer
push) are unchanged so cli.py can continue to do `await tui.run()`.

Public surface (kept stable for cli.py and tests):

    KanbanTUI(orchestrator, workflow_state, console=None).run() -> awaitable
    KanbanTUI.request_stop()
    _CardStatus, SILENT_THRESHOLD_S, _parse_iso, _silent_seconds
    STATE_COLOR, AGENT_COLOR
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Static

from .i18n import t
from .issue import Issue, normalize_state
from .logging import get_logger
from .orchestrator import Orchestrator
from .tracker import build_tracker_client
from .workflow import ServiceConfig, WorkflowState


log = get_logger()


# Visual styling per state — Jira-ish color cues. Kept as a public constant
# so other modules and tests can reuse the palette.
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


# Threshold above which a running card grows a yellow "silent Ns" badge.
# Tuned to be just past the longest expected pi/claude turn warm-up
# (≈30 s for opus-4 cold start) so the indicator never fires on healthy runs.
SILENT_THRESHOLD_S = 30.0


# Card render densities. Compact = one-line summary; Rich = current 3–6 line layout.
DENSITY_RICH = "rich"
DENSITY_COMPACT = "compact"


# Lane fr widths used by `_apply_lane_widths`. Pulled out as constants so the
# layout is tweakable in one place and so unit tests can assert against the
# named widths instead of magic strings scattered across the file.
LANE_WIDTH_NORMAL = "1fr"
LANE_WIDTH_DIM = "0.4fr"
LANE_WIDTH_ZOOMED = "3fr"


@dataclass
class _CardStatus:
    """Per-issue runtime overlay for a kanban card."""

    runtime: str = "idle"  # idle, running, retrying, completed
    turn: int = 0
    last_event: str = ""
    last_event_at: datetime | None = None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempt: int | None = None
    error: str | None = None
    last_message: str = ""


# ---------------------------------------------------------------------------
# Pure helpers — exported for unit tests and reused by widgets.
# ---------------------------------------------------------------------------


def _parse_iso(value: Any) -> datetime | None:
    """Parse the ISO-8601 strings the orchestrator emits for `last_event_at`.

    Returns None for missing/malformed values — the renderer treats `None`
    as "no data", which is the right fallback during the first poll tick
    (before any agent event has fired) and across orchestrator restarts.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _silent_seconds(last_event_at: datetime | None) -> float | None:
    if last_event_at is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - last_event_at).total_seconds())


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: max(n - 1, 1)] + "…"


def _first_meaningful_line(description: str | None) -> str:
    """description 본문에서 첫 의미 있는 줄 (markdown 헤딩/코드펜스 skip) 반환."""
    if not description:
        return ""
    for raw in description.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(("#", "```", "---")):
            continue
        return s
    return ""


def _card_sort_key(issue: Issue) -> tuple[int, str]:
    pri = issue.priority if isinstance(issue.priority, int) and issue.priority > 0 else 99
    return (pri, issue.identifier)


def _ordered_column_states(cfg: ServiceConfig) -> list[str]:
    column_states: list[str] = list(cfg.tracker.active_states) + list(
        cfg.tracker.terminal_states
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for state in column_states:
        key = normalize_state(state)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(state)
    return ordered


def _compact_rate_limits(rl: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in rl.items():
        if isinstance(value, (int, float, str)):
            parts.append(f"{key}={value}")
        if len(parts) >= 3:
            break
    return ", ".join(parts) if parts else "n/a"


def _build_runtime_index(snap: dict[str, Any]) -> dict[str, _CardStatus]:
    index: dict[str, _CardStatus] = {}
    for row in snap.get("running", []) or []:
        issue_id = row.get("issue_id") or ""
        tokens_block = row.get("tokens") or {}
        index[issue_id] = _CardStatus(
            runtime="running",
            turn=int(row.get("turn_count", 0) or 0),
            last_event=str(row.get("last_event") or ""),
            last_event_at=_parse_iso(row.get("last_event_at")),
            tokens=int(tokens_block.get("total_tokens") or 0),
            input_tokens=int(tokens_block.get("input_tokens") or 0),
            output_tokens=int(tokens_block.get("output_tokens") or 0),
            last_message=str(row.get("last_message") or ""),
        )
    for row in snap.get("retrying", []) or []:
        issue_id = row.get("issue_id") or ""
        index[issue_id] = _CardStatus(
            runtime="retrying",
            attempt=int(row.get("attempt", 0) or 0),
            error=str(row.get("error") or "") or None,
        )
    return index


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


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class IssueCard(Static):
    """A focusable card for a single ticket. Body text is set via `update()`."""

    DEFAULT_CSS = """
    IssueCard {
        border: round $surface;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
        min-height: 3;
    }
    IssueCard:focus { border: round $accent; background: $boost; }
    IssueCard.-running { border: round green; }
    IssueCard.-retrying { border: round yellow; }
    IssueCard.-completed { border: round $success-darken-1; color: $text-muted; }
    """

    can_focus = True

    def __init__(self, issue: Issue, status: _CardStatus, language: str) -> None:
        super().__init__("")
        self._issue = issue
        self._status = status
        self._language = language
        self.id = f"card-{_safe_id(issue.id)}"
        self._refresh_body()

    @property
    def issue(self) -> Issue:
        return self._issue

    @property
    def status(self) -> _CardStatus:
        return self._status

    def update_status(self, status: _CardStatus) -> None:
        self._status = status
        self._refresh_body()

    def _refresh_body(self) -> None:
        self.set_classes("")  # reset variant classes
        if self._status.runtime in ("running", "retrying", "completed"):
            self.add_class(f"-{self._status.runtime}")
        self.update(self._render_body())

    def _render_body(self) -> Text:
        issue = self._issue
        status = self._status
        language = self._language
        color = STATE_COLOR.get(normalize_state(issue.state), "white")

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
            silent_s = _silent_seconds(status.last_event_at)
            if silent_s is not None and silent_s >= SILENT_THRESHOLD_S:
                meta.append(f"  silent {int(silent_s)}s", style="bold yellow")
            if status.last_event:
                meta.append(f"  {status.last_event}", style="dim")
            if status.input_tokens or status.output_tokens or status.tokens:
                meta.append("  ")
                _append_token_meta(meta, status, dim=False)
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

        # Idle/completed cards still surface aggregate token spend so an
        # operator can audit cost after a run wraps.
        tokens_line = Text()
        if status.runtime != "running" and (
            status.input_tokens or status.output_tokens or status.tokens
        ):
            _append_token_meta(tokens_line, status, dim=True)

        out = Text.assemble(title, "\n", body)
        desc_preview = _first_meaningful_line(issue.description)
        if desc_preview:
            out.append("\n")
            out.append(_truncate(desc_preview, 80), style="dim")
        if meta.plain.strip():
            out.append("\n")
            out.append_text(meta)
        if tokens_line.plain.strip():
            out.append("\n")
            out.append_text(tokens_line)
        if status.last_message:
            out.append("\n")
            out.append(_truncate(status.last_message, 90), style="dim italic")
        return out

    def on_click(self) -> None:
        self.focus()

    def open_details(self) -> None:
        self.app.push_screen(TicketDetailScreen(self._issue, self._status, self._language))


class Lane(Vertical):
    """One Kanban lane: title bar + a vertical scroll of IssueCards."""

    DEFAULT_CSS = """
    Lane {
        width: 1fr;
        height: 1fr;
        border: round $surface;
        padding: 0 1;
    }
    Lane.-active { border: round $accent; }
    Lane > .lane-title { height: 1; text-style: bold; }
    Lane > .lane-legend { height: auto; color: $text-muted; text-style: italic; }
    Lane > VerticalScroll { height: 1fr; }
    """

    can_focus = True

    def __init__(self, state_label: str, color: str, legend: str | None) -> None:
        super().__init__()
        self._state_label = state_label
        self._color = color
        self._legend = legend
        self._title = Static("", classes="lane-title")
        self._legend_widget = Static(legend or "", classes="lane-legend")
        self._scroll = VerticalScroll()
        self.id = f"lane-{_safe_id(state_label)}"
        self.border_title = state_label

    @property
    def state_label(self) -> str:
        return self._state_label

    def compose(self) -> ComposeResult:
        yield self._title
        if self._legend:
            yield self._legend_widget
        yield self._scroll

    def set_count(self, count: int) -> None:
        self._title.update(Text(f"{self._state_label} ({count})", style=f"bold {self._color}"))
        self.border_title = f"{self._state_label} ({count})"

    def render_cards(
        self,
        cards: list[tuple[Issue, _CardStatus]],
        empty_text: str,
        language: str,
    ) -> None:
        # Diff against existing widgets so we never tear down a card the user
        # is interacting with (focus / scroll position). `remove_children()`
        # is asynchronous in Textual; remounting on every tick would race the
        # pending removal queue and raise DuplicateIds.
        existing: dict[str, IssueCard] = {
            child.id: child  # type: ignore[misc]
            for child in self._scroll.children
            if isinstance(child, IssueCard) and child.id
        }
        # Drop the empty-state widget if we now have cards.
        if cards:
            for child in list(self._scroll.children):
                if not isinstance(child, IssueCard):
                    child.remove()
        wanted_ids: set[str] = set()
        for issue, status in cards:
            card_id = f"card-{_safe_id(issue.id)}"
            wanted_ids.add(card_id)
            existing_card = existing.pop(card_id, None)
            if existing_card is not None:
                existing_card.update_status(status)
                continue
            self._scroll.mount(IssueCard(issue, status, language))
        # Stale cards (issue moved to another lane / closed) get removed.
        for stale_id, stale_card in existing.items():
            if stale_id not in wanted_ids:
                stale_card.remove()
        if not cards and not any(
            isinstance(child, Static) and not isinstance(child, IssueCard)
            for child in self._scroll.children
        ):
            self._scroll.mount(Static(empty_text, classes="lane-empty"))


class StatsBar(Static):
    """Top status row: agent / tracker / counts / tokens."""

    DEFAULT_CSS = """
    StatsBar { height: 1; padding: 0 1; background: $boost; color: $text; }
    """

    def update_from(self, cfg: ServiceConfig, snap: dict[str, Any]) -> None:
        lang = cfg.tui.language
        agent_kind = cfg.agent.kind
        agent_color = AGENT_COLOR.get(agent_kind, "white")
        counts = snap.get("counts", {})
        totals = snap.get("codex_totals", {})

        line = Text()
        line.append(f"{t('header.agent', lang)}", style="dim")
        line.append(agent_kind, style=f"bold {agent_color}")
        line.append(f"  {t('header.tracker', lang)}{cfg.tracker.kind}", style="dim")
        line.append(f"  {t('header.workflow', lang)}{cfg.workflow_path.name}", style="dim")
        line.append(f"  {t('header.lang', lang)}{lang}", style="bright_magenta")
        line.append("    ")
        line.append(f"{t('header.running', lang)}{counts.get('running', 0)}  ", style="green")
        line.append(f"{t('header.retrying', lang)}{counts.get('retrying', 0)}  ", style="yellow")
        line.append("│  ", style="dim")
        line.append(f"{t('footer.tokens', lang)} ", style="dim")
        line.append(f"in={totals.get('input_tokens', 0):,} ", style="cyan")
        line.append(f"out={totals.get('output_tokens', 0):,} ", style="bright_cyan")
        line.append(f"total={totals.get('total_tokens', 0):,}", style="bold cyan")
        rl = snap.get("rate_limits")
        if rl:
            line.append(f"  │  {t('footer.rate_limits', lang)}", style="dim")
            line.append(_compact_rate_limits(rl), style="yellow")
        self.update(line)


# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------


class TicketDetailScreen(ModalScreen[None]):
    """Full ticket detail. Dismiss with Esc or q."""

    DEFAULT_CSS = """
    TicketDetailScreen { align: center middle; }
    #ticket-dialog {
        width: 80%;
        max-width: 120;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #ticket-dialog #ticket-title { text-style: bold; color: $accent; }
    #ticket-dialog #ticket-meta { color: $text-muted; margin-bottom: 1; }
    #ticket-dialog VerticalScroll { height: 1fr; border: round $boost; padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
    ]

    def __init__(self, issue: Issue, status: _CardStatus, language: str) -> None:
        super().__init__()
        self._issue = issue
        self._status = status
        self._language = language

    def compose(self) -> ComposeResult:
        with Container(id="ticket-dialog"):
            yield Static(self._title_text(), id="ticket-title")
            yield Static(self._meta_text(), id="ticket-meta")
            with VerticalScroll():
                yield Static(self._issue.description or "(no description)")
            yield Static("[dim]esc / q to close[/dim]")

    def _title_text(self) -> Text:
        color = STATE_COLOR.get(normalize_state(self._issue.state), "white")
        title = Text(f"{self._issue.identifier}  ", style=f"bold {color}")
        title.append(self._issue.title or "")
        return title

    def _meta_text(self) -> Text:
        meta = Text()
        meta.append(f"state={self._issue.state}", style="dim")
        if self._issue.priority:
            meta.append(f"  P{self._issue.priority}", style="bright_red bold")
        if self._issue.labels:
            meta.append("  " + " ".join(f"#{l}" for l in self._issue.labels), style="dim")
        if self._status.tokens or self._status.input_tokens or self._status.output_tokens:
            meta.append("\n")
            _append_token_meta(meta, self._status, dim=False)
        if self._status.last_message:
            meta.append("\n")
            meta.append(self._status.last_message, style="italic")
        return meta


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class _RefreshNow(Message):
    """Posted from the orchestrator observer thread to request a redraw."""


class KanbanApp(App):
    """The Textual application that draws the board."""

    CSS = """
    Screen { background: $background; }
    #board { layout: horizontal; height: 1fr; padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "help", "Help"),
        Binding("tab", "focus_next", "Next pane", show=False),
        Binding("shift+tab", "focus_previous", "Prev pane", show=False),
        Binding("j,down", "scroll_down", "Down", show=False),
        Binding("k,up", "scroll_up", "Up", show=False),
        Binding("g,home", "scroll_top", "Top", show=False),
        Binding("G,end", "scroll_bottom", "Bottom", show=False),
        Binding("space,pagedown", "page_down", "Page down", show=False),
        Binding("b,pageup", "page_up", "Page up", show=False),
        Binding("enter", "open_details", "Details"),
    ]

    def __init__(
        self,
        orchestrator: Orchestrator,
        workflow_state: WorkflowState,
    ) -> None:
        super().__init__()
        self._orch = orchestrator
        self._ws = workflow_state
        self._candidates: list[Issue] = []
        self._terminal_issues: list[Issue] = []
        self._lanes: dict[str, Lane] = {}
        self._tracker_lock = asyncio.Lock()

    # ----- composition -------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatsBar(id="stats")
        cfg = self._ws.current()
        with Container(id="board"):
            ordered = _ordered_column_states(cfg) if cfg else []
            descriptions = cfg.tracker.state_descriptions if cfg else {}
            for state_label in ordered:
                key = normalize_state(state_label)
                color = STATE_COLOR.get(key, "white")
                lane = Lane(state_label, color, descriptions.get(key))
                self._lanes[key] = lane
                yield lane
        yield Footer()

    # ----- lifecycle ---------------------------------------------------

    def on_mount(self) -> None:
        self.title = "symphony-multi-agent"
        cfg = self._ws.current()
        if cfg is not None:
            self.sub_title = f"{cfg.agent.kind} · {cfg.tracker.kind}"
        # Hook orchestrator events. Observer is invoked from the orchestrator
        # task; bouncing through call_from_thread keeps widget updates on the
        # Textual event loop.
        self._orch.add_observer(self._on_orchestrator_tick)
        # Redraw heartbeat — picks up "silent N s" tick-overs without
        # waiting on orchestrator events.
        self.set_interval(0.5, self._refresh_runtime)
        # Tracker poll. Runs in a thread worker so a slow Linear API doesn't
        # stall the UI.
        cfg = self._ws.current()
        poll_s = max(5.0, (cfg.poll_interval_ms / 1000.0) if cfg else 30.0)
        self.set_interval(poll_s, self._kick_tracker_refresh)
        self._kick_tracker_refresh()  # prime
        self._refresh_runtime()

    async def _on_orchestrator_tick(self) -> None:
        # Called from the orchestrator's asyncio task. Posting a message keeps
        # us on Textual's loop without race conditions on the widget tree.
        try:
            self.post_message(_RefreshNow())
        except Exception:  # app may already be shutting down
            log.debug("tui_post_refresh_failed")

    def on__refresh_now(self, message: _RefreshNow) -> None:  # noqa: N802 (Textual naming)
        del message
        self._refresh_runtime()

    # ----- data refresh ------------------------------------------------

    def _kick_tracker_refresh(self) -> None:
        cfg = self._ws.current()
        if cfg is None:
            return
        # Thread worker: tracker clients use blocking httpx.
        self.run_worker(self._refresh_tracker(cfg), thread=False, exclusive=True, group="tracker")

    async def _refresh_tracker(self, cfg: ServiceConfig) -> None:
        try:
            candidates = await asyncio.to_thread(_fetch_candidates, cfg)
            terminals = await asyncio.to_thread(_fetch_terminals, cfg)
        except Exception as exc:
            log.debug("tui_tracker_fetch_failed", error=str(exc))
            return
        async with self._tracker_lock:
            self._candidates = candidates
            self._terminal_issues = terminals
        self._refresh_runtime()

    def _refresh_runtime(self) -> None:
        cfg = self._ws.current()
        if cfg is None:
            return
        snapshot = self._orch.snapshot()
        self.query_one(StatsBar).update_from(cfg, snapshot)
        runtime_index = _build_runtime_index(snapshot)
        issues_by_state: dict[str, list[Issue]] = {k: [] for k in self._lanes}
        for issue in self._all_known_issues():
            key = normalize_state(issue.state)
            if key in issues_by_state:
                issues_by_state[key].append(issue)
        empty_text = t("column.empty", cfg.tui.language)
        for key, lane in self._lanes.items():
            issues = sorted(issues_by_state.get(key, []), key=_card_sort_key)
            lane.set_count(len(issues))
            cards = [
                (issue, runtime_index.get(issue.id, _CardStatus()))
                for issue in issues
            ]
            lane.render_cards(cards, empty_text, cfg.tui.language)

    def _all_known_issues(self) -> Iterable[Issue]:
        seen: set[str] = set()
        for source in (
            self._candidates,
            self._terminal_issues,
            list(self._orch.iter_running_issues()),
        ):
            for issue in source:
                if issue.id in seen:
                    continue
                seen.add(issue.id)
                yield issue

    # ----- actions -----------------------------------------------------

    def action_refresh(self) -> None:
        self._kick_tracker_refresh()
        self._refresh_runtime()
        self.notify("refreshed")

    def action_help(self) -> None:
        cfg = self._ws.current()
        lang = cfg.tui.language if cfg else "en"
        msg = (
            "q quit · r refresh · enter details · "
            "tab/shift-tab focus · j/k or ↑/↓ scroll · g/G top/bottom · "
            f"lang={lang}"
        )
        self.notify(msg, timeout=6)

    def action_open_details(self) -> None:
        focused = self.focused
        if isinstance(focused, IssueCard):
            focused.open_details()

    def action_scroll_down(self) -> None:
        self._scroll_focused(1)

    def action_scroll_up(self) -> None:
        self._scroll_focused(-1)

    def action_page_down(self) -> None:
        self._scroll_focused(10)

    def action_page_up(self) -> None:
        self._scroll_focused(-10)

    def action_scroll_top(self) -> None:
        scroll = self._focused_scroll()
        if scroll is not None:
            scroll.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        scroll = self._focused_scroll()
        if scroll is not None:
            scroll.scroll_end(animate=False)

    def _focused_scroll(self) -> VerticalScroll | None:
        node = self.focused
        while node is not None:
            if isinstance(node, VerticalScroll):
                return node
            node = node.parent  # type: ignore[assignment]
        # Fall back to the first lane's scroll so j/k still works without focus.
        for lane in self._lanes.values():
            try:
                return lane.query_one(VerticalScroll)
            except Exception:
                continue
        return None

    def _scroll_focused(self, delta: int) -> None:
        scroll = self._focused_scroll()
        if scroll is None:
            return
        scroll.scroll_relative(y=delta, animate=False)


# ---------------------------------------------------------------------------
# Compatibility wrapper used by cli.py
# ---------------------------------------------------------------------------


class KanbanTUI:
    """Async wrapper around `KanbanApp` so cli.py can keep `await tui.run()`.

    The legacy implementation accepted a `console` kwarg for unit-test
    rendering; the Textual app manages its own renderer so the argument is
    accepted and ignored. A `_KanbanTUI` instance is single-use — call
    `run()` once.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        workflow_state: WorkflowState,
        *,
        console: Console | None = None,
    ) -> None:
        self._orch = orchestrator
        self._ws = workflow_state
        self._console = console  # accepted for API compat; not used here
        self._app: KanbanApp | None = None

    async def run(self) -> None:
        self._app = KanbanApp(self._orch, self._ws)
        try:
            await self._app.run_async()
        except asyncio.CancelledError:
            self.request_stop()
            raise

    def request_stop(self) -> None:
        if self._app is not None:
            try:
                self._app.exit()
            except Exception:  # already exiting
                pass


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def _append_token_meta(text: Text, status: _CardStatus, *, dim: bool) -> None:
    input_style = "dim cyan" if dim else "cyan"
    output_style = "dim bright_cyan" if dim else "bright_cyan"
    total_style = "dim bold cyan" if dim else "bold cyan"
    text.append(f"in={status.input_tokens:,}", style=input_style)
    text.append(" / ", style="dim")
    text.append(f"out={status.output_tokens:,}", style=output_style)
    text.append(" / ", style="dim")
    text.append(f"total={status.tokens:,}", style=total_style)


def _safe_id(value: str) -> str:
    """Coerce arbitrary tracker IDs into Textual-safe widget IDs."""
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return out or "unnamed"
