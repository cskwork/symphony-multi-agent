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
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Static

from .i18n import SUPPORTED_LANGUAGES, t
from .issue import Issue, normalize_state, registration_order_key
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


# Header bar must stay one line; cap the in-progress IDs we render and roll the
# rest into a `+N` suffix. Five fits comfortably on an 80-col terminal alongside
# agent / tracker / lang / counts; reduce if those grow.
_RUNNING_IDS_MAX = 5


@dataclass
class _CardStatus:
    """Per-issue runtime overlay for a kanban card."""

    runtime: str = "idle"  # idle, running, retrying, completed
    turn: int = 0
    attempt_turn: int = 0
    attempt_kind: str = ""
    last_event: str = ""
    last_event_at: datetime | None = None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempt: int | None = None
    error: str | None = None
    last_message: str = ""
    # True when the orchestrator has been asked to hold this worker at the
    # next turn boundary. Surfaced from `snapshot()["running"][N]["paused"]`.
    paused: bool = False


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


def _card_sort_key(issue: Issue) -> tuple[int, str, int, float, str]:
    return registration_order_key(issue)


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
            attempt_turn=int(row.get("attempt_turn_count", 0) or 0),
            attempt_kind=str(row.get("attempt_kind") or ""),
            last_event=str(row.get("last_event") or ""),
            last_event_at=_parse_iso(row.get("last_event_at")),
            tokens=int(tokens_block.get("total_tokens") or 0),
            input_tokens=int(tokens_block.get("input_tokens") or 0),
            output_tokens=int(tokens_block.get("output_tokens") or 0),
            last_message=str(row.get("last_message") or ""),
            paused=bool(row.get("paused", False)),
        )
    for row in snap.get("retrying", []) or []:
        issue_id = row.get("issue_id") or ""
        index[issue_id] = _CardStatus(
            runtime="retrying",
            attempt=int(row.get("attempt", 0) or 0),
            error=str(row.get("error") or "") or None,
            paused=bool(row.get("paused", False)),
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
    IssueCard.-compact {
        margin-bottom: 0;
        min-height: 1;
        padding: 0 1;
        border: none;
    }
    IssueCard:focus { border: round $accent; background: $boost; }
    IssueCard.-compact:focus { background: $boost; border: none; }
    IssueCard.-running { border: round green; }
    IssueCard.-retrying { border: round yellow; }
    IssueCard.-completed { border: round $success-darken-1; color: $text-muted; }
    IssueCard.-paused { border: round magenta; }
    IssueCard.-compact.-running { color: green; border: none; }
    IssueCard.-compact.-retrying { color: yellow; border: none; }
    IssueCard.-compact.-completed { color: $text-muted; border: none; }
    IssueCard.-compact.-paused { color: magenta; border: none; }
    """

    can_focus = True

    def __init__(
        self,
        issue: Issue,
        status: _CardStatus,
        language: str,
        *,
        density: str = DENSITY_RICH,
    ) -> None:
        super().__init__("")
        self._issue = issue
        self._status = status
        self._language = language
        self._density = density
        self.id = f"card-{_safe_id(issue.id)}"
        self._refresh_body()

    @property
    def issue(self) -> Issue:
        return self._issue

    @property
    def status(self) -> _CardStatus:
        return self._status

    @property
    def density(self) -> str:
        return self._density

    def update_status(self, status: _CardStatus) -> None:
        self._status = status
        self._refresh_body()

    def set_density(self, density: str) -> None:
        if density == self._density:
            return
        self._density = density
        self._refresh_body()

    def _refresh_body(self) -> None:
        self.set_classes("")  # reset variant classes
        if self._density == DENSITY_COMPACT:
            self.add_class("-compact")
        if self._status.runtime in ("running", "retrying", "completed"):
            self.add_class(f"-{self._status.runtime}")
        # Pause variant is orthogonal to runtime — it overlays "running" so
        # the border colour changes while still flagging it as in-flight.
        if self._status.paused:
            self.add_class("-paused")
        self.update(self._render_body())

    def _render_body(self) -> Text:
        if self._density == DENSITY_COMPACT:
            return self._render_compact()
        return self._render_rich()

    def _render_compact(self) -> Text:
        """One-line summary for dense boards: ID • badge • title • tokens."""
        issue = self._issue
        status = self._status
        color = STATE_COLOR.get(normalize_state(issue.state), "white")
        line = Text()
        line.append(issue.identifier, style=f"bold {color}")
        if status.runtime == "running" and status.paused:
            line.append(" ⏸", style="bold bright_magenta")
        elif status.runtime == "running":
            line.append(" ●", style="bold green")
        elif status.runtime == "retrying":
            line.append(" ↻", style="bold yellow")
        elif status.runtime == "completed":
            line.append(" ✓", style="bold green")
        if issue.priority:
            line.append(f" P{issue.priority}", style="bright_red bold")
        line.append("  ")
        line.append(_truncate(issue.title or "", 60), style="white")
        if status.runtime == "running":
            silent_s = _silent_seconds(status.last_event_at)
            if silent_s is not None and silent_s >= SILENT_THRESHOLD_S:
                line.append(f"  silent {int(silent_s)}s", style="bold yellow")
        if status.tokens:
            line.append(f"  {status.tokens:,}t", style="dim cyan")
        return line

    def _render_rich(self) -> Text:
        issue = self._issue
        status = self._status
        language = self._language
        color = STATE_COLOR.get(normalize_state(issue.state), "white")

        title = Text(issue.identifier, style=f"bold {color}")
        if status.runtime == "running" and status.paused:
            title.append("  ⏸", style="bold bright_magenta")
        elif status.runtime == "running":
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
            if status.paused:
                meta.append(
                    f"{t('card.paused', language)}  ",
                    style="bold bright_magenta",
                )
            meta.append(f"{t('card.turn', language)} {status.turn}", style="green")
            if status.attempt_kind in ("continuation", "retry") and status.attempt_turn:
                meta.append(
                    f"  {status.attempt_kind} {status.attempt_turn}",
                    style="dim",
                )
            silent_s = _silent_seconds(status.last_event_at)
            # Paused workers are intentionally idle — suppress the silent
            # badge so the card doesn't look stuck when the operator put
            # it on hold.
            if (
                not status.paused
                and silent_s is not None
                and silent_s >= SILENT_THRESHOLD_S
            ):
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
    Lane.-empty { border: round $surface-darken-1; color: $text-muted; }
    Lane.-zoomed { border: round $accent; }
    Lane.-terminal { border: round $surface-darken-1; }
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
        self._card_count = 0

    @property
    def state_label(self) -> str:
        return self._state_label

    @property
    def card_count(self) -> int:
        return self._card_count

    @property
    def is_empty(self) -> bool:
        return self._card_count == 0

    def compose(self) -> ComposeResult:
        yield self._title
        if self._legend:
            yield self._legend_widget
        yield self._scroll

    def set_count(self, count: int) -> None:
        self._card_count = count
        self._title.update(Text(f"{self._state_label} ({count})", style=f"bold {self._color}"))
        self.border_title = f"{self._state_label} ({count})"

    def render_cards(
        self,
        cards: list[tuple[Issue, _CardStatus]],
        empty_text: str,
        language: str,
        *,
        density: str = DENSITY_RICH,
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
                existing_card.set_density(density)
                continue
            self._scroll.mount(IssueCard(issue, status, language, density=density))
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

    def update_from(
        self,
        cfg: ServiceConfig,
        snap: dict[str, Any],
        language: str | None = None,
    ) -> None:
        # `language` lets the App pass the in-session override (`L` toggle).
        # Falls back to `cfg.tui.language` so existing callers / tests that
        # don't know about the override still work.
        lang = language if language is not None else cfg.tui.language
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
        line.append(f"{t('header.running', lang)}{counts.get('running', 0)}", style="green")
        running_rows = snap.get("running") or []
        if running_rows:
            visible_ids = [
                str(row.get("issue_id") or "")
                for row in running_rows[:_RUNNING_IDS_MAX]
            ]
            visible_ids = [vid for vid in visible_ids if vid]
            if visible_ids:
                line.append(" [", style="green")
                line.append(", ".join(visible_ids), style="bold green")
                overflow = len(running_rows) - len(visible_ids)
                if overflow > 0:
                    line.append(f" +{overflow}", style="dim green")
                line.append("]", style="green")
        line.append("  ")
        line.append(f"{t('header.retrying', lang)}{counts.get('retrying', 0)}  ", style="yellow")
        # Paused count is folded into the header only when non-zero so the
        # status bar stays compact on the common case.
        paused_count = sum(
            1 for row in running_rows if row.get("paused")
        )
        if paused_count:
            line.append(
                f"{t('header.paused', lang)}{paused_count}  ",
                style="bright_magenta",
            )
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
# Inline detail pane + filter bar
# ---------------------------------------------------------------------------


class DetailPane(Vertical):
    """Right-side pane mirroring the focused IssueCard.

    Lives next to `#board` inside `#main`. Toggled with `p`. Width collapses to
    `0` (display: none) when hidden so it does not steal lane width — this is
    the cheap operator-mode that lets cards stay one-line in `#board` while
    the full description / last_message / token block lives over here.
    """

    DEFAULT_CSS = """
    DetailPane {
        width: 0;
        display: none;
        border: round $accent;
        padding: 0 1;
    }
    DetailPane.-visible {
        width: 60;
        display: block;
    }
    DetailPane > #detail-title { height: auto; text-style: bold; }
    DetailPane > #detail-meta { height: auto; color: $text-muted; margin-bottom: 1; }
    DetailPane > VerticalScroll { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__(id="detail-pane")
        self._title = Static("", id="detail-title")
        self._meta = Static("", id="detail-meta")
        self._body = Static("", id="detail-body", markup=False)
        self._scroll = VerticalScroll()

    def compose(self) -> ComposeResult:
        yield self._title
        yield self._meta
        with self._scroll:
            yield self._body

    @property
    def scroll(self) -> VerticalScroll:
        # Exposed so the App can `.focus()` the inner scroll directly — once
        # focus lives there, `_focused_scroll` walks straight up to it and
        # arrow / j / k / pgup / pgdn target the description body.
        return self._scroll

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("-visible")
        else:
            self.remove_class("-visible")

    @property
    def is_open(self) -> bool:
        return self.has_class("-visible")

    def show_for(self, issue: Issue, status: _CardStatus) -> None:
        color = STATE_COLOR.get(normalize_state(issue.state), "white")
        title = Text(issue.identifier, style=f"bold {color}")
        if issue.title:
            title.append(f"  {issue.title}", style="white")
        self._title.update(title)

        meta = Text()
        meta.append(f"state={issue.state}", style="dim")
        if issue.priority:
            meta.append(f"  P{issue.priority}", style="bright_red bold")
        if issue.labels:
            meta.append("  " + " ".join(f"#{l}" for l in issue.labels), style="dim")
        if status.runtime != "idle":
            runtime_label = (
                f"runtime={status.runtime} (paused)"
                if status.paused
                else f"runtime={status.runtime}"
            )
            runtime_style = "bright_magenta" if status.paused else "green"
            meta.append(f"\n{runtime_label}", style=runtime_style)
            if status.turn:
                meta.append(f"  turn={status.turn}", style="dim")
            if status.attempt:
                meta.append(f"  retry#{status.attempt}", style="yellow")
            if status.error:
                meta.append(f"\nerror: {status.error}", style="red")
        if status.tokens or status.input_tokens or status.output_tokens:
            meta.append("\n")
            _append_token_meta(meta, status, dim=False)
        self._meta.update(meta)

        body = issue.description or "(no description)"
        if status.last_message:
            body = f"{body}\n\n— last message —\n{status.last_message}"
        self._body.update(body)

    def show_placeholder(self) -> None:
        self._title.update(Text("(no card focused)", style="dim italic"))
        self._meta.update("")
        self._body.update("Press p to hide this pane, or focus a card.")


class FilterBar(Container):
    """One-line filter prompt above the footer. Hidden until `/` is pressed."""

    DEFAULT_CSS = """
    FilterBar {
        height: 0;
        display: none;
    }
    FilterBar.-visible { height: 3; display: block; }
    FilterBar > Input { height: 3; }
    """

    def __init__(self) -> None:
        super().__init__(id="filter-bar")

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="filter: type to match identifier/title/labels — esc to clear",
            id="filter-input",
        )

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("-visible")
        else:
            self.remove_class("-visible")

    @property
    def is_open(self) -> bool:
        return self.has_class("-visible")


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
                yield Static(self._issue.description or "(no description)", markup=False)
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
    #main { layout: horizontal; height: 1fr; padding: 0 1; }
    #board { layout: horizontal; height: 1fr; width: 1fr; }
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
        # Iter1: Focus zoom — digits 1..9 zoom that lane to 3fr (others 0.4fr).
        # `0` resets. Escape also resets a zoom (and closes filter if open).
        Binding("1", "zoom_lane(0)", show=False),
        Binding("2", "zoom_lane(1)", show=False),
        Binding("3", "zoom_lane(2)", show=False),
        Binding("4", "zoom_lane(3)", show=False),
        Binding("5", "zoom_lane(4)", show=False),
        Binding("6", "zoom_lane(5)", show=False),
        Binding("7", "zoom_lane(6)", show=False),
        Binding("8", "zoom_lane(7)", show=False),
        Binding("9", "zoom_lane(8)", show=False),
        Binding("0", "reset_zoom", "Reset zoom", show=False),
        # Lane window pagination — show N lanes at a time, page through the rest.
        # `t` advances; `T` (shift+t) goes back. `+` / `-` resize the window.
        Binding("t", "next_page", "Next lanes"),
        Binding("T", "prev_page", "Prev lanes", show=False),
        Binding("plus,equals_sign", "grow_window", "Wider", show=False),
        Binding("minus", "shrink_window", "Narrower", show=False),
        Binding("d", "toggle_density", "Density"),
        Binding("p", "toggle_detail", "Detail pane"),
        # `]` parks focus inside the detail pane so arrow / j / k / pgup / pgdn
        # scroll the description body. `[` returns focus to the board.
        Binding("right_square_bracket", "focus_detail", "Detail focus", show=False),
        Binding("left_square_bracket", "focus_board", "Board focus", show=False),
        Binding("L", "toggle_language", "Language"),
        Binding("a", "archive_focused", "Archive"),
        Binding("P", "toggle_pause_focused", "Pause/resume"),
        Binding("slash", "open_filter", "Filter"),
        Binding("escape", "escape", "Close filter / zoom", show=False),
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
        # Lane keys in the order they were composed. Used by digit zoom — index
        # `i` lights up `_lane_order[i]`.
        self._lane_order: list[str] = []
        # Set of normalized terminal state keys (Done, Closed, Cancelled, ...).
        self._terminal_keys: set[str] = set()
        self._tracker_lock = asyncio.Lock()
        # UX state.
        self._zoomed_lane: str | None = None
        # Compact density default — one-line cards keep many lanes scannable
        # at once. Press `d` to flip to the multi-line rich layout.
        self._density: str = DENSITY_COMPACT
        # Detail pane is default-on so the focused card always has a place
        # to spread out — keeps each lane card terse without losing detail.
        self._detail_visible: bool = True
        self._filter_query: str = ""
        # Lane window pagination — show `_window_size` consecutive lanes
        # starting at index `_window_start`. `t` advances by a full page,
        # `+`/`-` adjust the window size at runtime. Initial size comes from
        # `tui.visible_lanes` in WORKFLOW.md (default 5).
        cfg = self._ws.current()
        self._window_size: int = cfg.tui.visible_lanes if cfg else 5
        self._window_start: int = 0
        # Cache the last-rendered focused card so we don't re-render the
        # detail pane every 0.5 s heartbeat unless focus actually moved.
        self._last_focused_card_id: str | None = None
        # In-session language override. None = follow `tui.language` from the
        # WORKFLOW.md config; set by `L` to flip chrome locale without
        # restarting the TUI. Reset on relaunch — persistence belongs in
        # WORKFLOW.md / SYMPHONY_LANG, not in TUI state.
        self._language_override: str | None = None

    # ----- composition -------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatsBar(id="stats")
        cfg = self._ws.current()
        terminal_states = set(cfg.tracker.terminal_states) if cfg else set()
        self._terminal_keys = {normalize_state(s) for s in terminal_states}
        with Container(id="main"):
            with Container(id="board"):
                ordered = _ordered_column_states(cfg) if cfg else []
                descriptions = cfg.tracker.state_descriptions if cfg else {}
                for state_label in ordered:
                    key = normalize_state(state_label)
                    color = STATE_COLOR.get(key, "white")
                    lane = Lane(state_label, color, descriptions.get(key))
                    if key in self._terminal_keys:
                        lane.add_class("-terminal")
                    self._lanes[key] = lane
                    self._lane_order.append(key)
                    yield lane
            pane = DetailPane()
            # Detail pane is on by default — toggle with `p`.
            pane.set_visible(self._detail_visible)
            yield pane
        yield FilterBar()
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
        try:
            stats = self.query_one(StatsBar)
        except (NoMatches, ScreenStackError):
            return
        stats.update_from(
            cfg, snapshot, language=self._effective_language()
        )
        runtime_index = _build_runtime_index(snapshot)
        issues_by_state: dict[str, list[Issue]] = {k: [] for k in self._lanes}
        for issue in self._all_known_issues():
            key = normalize_state(issue.state)
            if key in issues_by_state:
                issues_by_state[key].append(issue)
        # Apply substring filter — empty query is a no-op so the hot path stays
        # identical to the unfiltered branch.
        if self._filter_query:
            q = self._filter_query
            for key in list(issues_by_state.keys()):
                issues_by_state[key] = [
                    i for i in issues_by_state[key] if _matches_filter(i, q)
                ]
        language = self._effective_language()
        empty_text = t("column.empty", language)
        for key, lane in self._lanes.items():
            issues = sorted(issues_by_state.get(key, []), key=_card_sort_key)
            lane.set_count(len(issues))
            cards = [
                (issue, runtime_index.get(issue.id, _CardStatus()))
                for issue in issues
            ]
            lane.render_cards(
                cards, empty_text, language, density=self._density
            )
        # Lane widths depend on counts (empty → dim) and on user state
        # (zoom / show_terminals), so re-apply after counts settle.
        self._apply_lane_widths()
        self._refresh_detail_pane()

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
        lang = self._effective_language()
        page = self._current_page_index() + 1
        total_pages = self._page_count()
        msg = (
            "q quit · r refresh · enter details · "
            "1-9 zoom lane · 0/esc reset · "
            f"t/T page lanes ({page}/{total_pages}) · +/- resize window · "
            "d density · p detail-pane · ]/[ focus detail/board · "
            "L language · a archive · P pause/resume · / filter · "
            "tab focus · j/k scroll · g/G top/bottom · "
            f"lang={lang}"
        )
        self.notify(msg, timeout=8)

    def action_open_details(self) -> None:
        focused = self.focused
        if isinstance(focused, IssueCard):
            focused.open_details()

    # ----- Iter1: focus zoom + empty lane collapse ---------------------

    def action_zoom_lane(self, idx: int) -> None:
        # `idx` is 0-based within the *current* window — pressing `1` always
        # zooms the leftmost visible lane regardless of pagination.
        window = sorted(self._window_indices())
        if idx < 0 or idx >= len(window):
            return
        target = self._lane_order[window[idx]]
        if self._zoomed_lane == target:
            self._zoomed_lane = None
        else:
            self._zoomed_lane = target
        self._apply_lane_widths()

    def action_reset_zoom(self) -> None:
        if self._zoomed_lane is None:
            return
        self._zoomed_lane = None
        self._apply_lane_widths()

    def _apply_lane_widths(self) -> None:
        """Single source of truth for lane sizing + visibility.

        Priority order (top wins):
            1. Outside the current window → `display: none` (paged off-screen).
            2. Zoom — the zoomed lane gets `LANE_WIDTH_ZOOMED`, others dim.
            3. Empty lane — narrow + `.-empty` class for muted styling.
            4. Terminal lane — narrow (Done/Closed are reference, not workspace).
            5. Default — `LANE_WIDTH_NORMAL`.
        """
        window = self._window_indices()
        for idx, lane_key in enumerate(self._lane_order):
            lane = self._lanes[lane_key]
            is_terminal = lane_key in self._terminal_keys
            lane.remove_class("-zoomed")
            lane.remove_class("-empty")

            if idx not in window:
                # Paged off-screen — hide entirely so visible lanes get the
                # full width allocation.
                lane.display = False
                continue
            lane.display = True

            if self._zoomed_lane is not None:
                if lane_key == self._zoomed_lane:
                    lane.styles.width = LANE_WIDTH_ZOOMED
                    lane.add_class("-zoomed")
                else:
                    lane.styles.width = LANE_WIDTH_DIM
                continue

            if lane.is_empty:
                lane.styles.width = LANE_WIDTH_DIM
                lane.add_class("-empty")
            elif is_terminal:
                lane.styles.width = LANE_WIDTH_DIM
            else:
                lane.styles.width = LANE_WIDTH_NORMAL

    def _window_indices(self) -> set[int]:
        """Indices of lanes currently visible in the lane window.

        Honors partial trailing pages — if `total=8, size=5`, page 1 shows
        indices {5, 6, 7} (3 lanes), not {3, 4, 5, 6, 7}. Snapping back to
        a full window would force the user to re-see lanes they already saw.
        """
        total = len(self._lane_order)
        if total == 0:
            return set()
        size = max(1, self._window_size)
        if self._window_start < 0 or self._window_start >= total:
            # Wrapped or invalidated → reset to the start.
            self._window_start = 0
        end = min(total, self._window_start + size)
        return set(range(self._window_start, end))

    def _page_count(self) -> int:
        total = len(self._lane_order)
        if total == 0:
            return 1
        size = max(1, self._window_size)
        # Ceil division — a partial last page still counts as a page.
        return (total + size - 1) // size

    def _current_page_index(self) -> int:
        size = max(1, self._window_size)
        return self._window_start // size

    # ----- lane window pagination -------------------------------------

    def action_next_page(self) -> None:
        """Slide the lane window forward one full page (wraps to 0)."""
        total = len(self._lane_order)
        if total == 0:
            return
        size = max(1, self._window_size)
        next_start = self._window_start + size
        if next_start >= total:
            next_start = 0
        self._window_start = next_start
        # Zoom is bound to a specific lane; if that lane just paged off,
        # clear zoom so we don't show "lane is zoomed but invisible" state.
        if self._zoomed_lane is not None and self._lanes[self._zoomed_lane].display is False:
            self._zoomed_lane = None
        self._apply_lane_widths()
        self._notify_page()

    def action_prev_page(self) -> None:
        total = len(self._lane_order)
        if total == 0:
            return
        size = max(1, self._window_size)
        if self._window_start == 0:
            # Wrap to the last page on a page-aligned boundary.
            last_page_start = ((total - 1) // size) * size
            self._window_start = max(0, last_page_start)
        else:
            self._window_start = max(0, self._window_start - size)
        if self._zoomed_lane is not None and self._lanes[self._zoomed_lane].display is False:
            self._zoomed_lane = None
        self._apply_lane_widths()
        self._notify_page()

    def action_grow_window(self) -> None:
        if self._window_size >= len(self._lane_order):
            return
        self._window_size += 1
        self._apply_lane_widths()
        self._notify_page(prefix="window")

    def action_shrink_window(self) -> None:
        if self._window_size <= 1:
            return
        self._window_size -= 1
        self._apply_lane_widths()
        self._notify_page(prefix="window")

    def _notify_page(self, *, prefix: str = "page") -> None:
        page = self._current_page_index() + 1
        total_pages = self._page_count()
        size = self._window_size
        self.notify(
            f"{prefix} {page}/{total_pages}  ({size} lanes/page)",
            timeout=2,
        )

    # ----- card density -----------------------------------------------

    def action_toggle_density(self) -> None:
        self._density = (
            DENSITY_COMPACT if self._density == DENSITY_RICH else DENSITY_RICH
        )
        # Cards re-render through the next _refresh_runtime tick; trigger one
        # immediately so the keystroke feels instant.
        self._refresh_runtime()
        self.notify(f"density: {self._density}", timeout=2)

    def _effective_language(self) -> str:
        """Resolve the language used for chrome rendering this frame.

        In-session override (`L`) wins over `tui.language` from WORKFLOW.md
        so the toggle feels instant without rewriting config.
        """
        if self._language_override is not None:
            return self._language_override
        cfg = self._ws.current()
        return cfg.tui.language if cfg else "en"

    def action_toggle_language(self) -> None:
        current = self._effective_language()
        try:
            idx = SUPPORTED_LANGUAGES.index(current)
        except ValueError:
            idx = -1
        self._language_override = SUPPORTED_LANGUAGES[
            (idx + 1) % len(SUPPORTED_LANGUAGES)
        ]
        self._refresh_runtime()
        self.notify(f"language: {self._language_override}", timeout=2)

    def action_archive_focused(self) -> None:
        """Move the focused card to the configured archive state.

        Only fires for cards already in a *terminal* state — auto-archive
        and the manual hotkey both target Done-ish lanes, so accidentally
        archiving an in-flight ticket from the TUI shouldn't be possible.
        """
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        cfg = self._ws.current()
        if cfg is None:
            return
        terminal_keys = {normalize_state(s) for s in cfg.tracker.terminal_states}
        archive_key = normalize_state(cfg.tracker.archive_state)
        issue = focused.issue
        state_key = normalize_state(issue.state)
        if state_key not in terminal_keys:
            self.notify(
                f"only terminal states can be archived (state={issue.state})",
                timeout=3,
            )
            return
        if state_key == archive_key:
            self.notify("already archived", timeout=2)
            return
        # Tracker mutation is blocking httpx / file IO — punt to a worker
        # so the keystroke stays responsive. After the call lands, kick a
        # tracker refresh so the lane re-paints.
        self.run_worker(
            self._archive_issue(cfg, issue),
            thread=False,
            exclusive=False,
            group="archive",
        )

    async def _archive_issue(self, cfg: ServiceConfig, issue: Issue) -> None:
        target = cfg.tracker.archive_state
        try:
            await asyncio.to_thread(self._call_update_state, cfg, issue, target)
        except Exception as exc:
            log.warning(
                "tui_archive_failed", identifier=issue.identifier, error=str(exc)
            )
            self.notify(f"archive failed: {exc}", timeout=4, severity="error")
            return
        self.notify(f"archived {issue.identifier}", timeout=2)
        self._kick_tracker_refresh()

    @staticmethod
    def _call_update_state(
        cfg: ServiceConfig, issue: Issue, target_state: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            client.update_state(issue, target_state)
        finally:
            client.close()

    # ----- pause / resume ---------------------------------------------

    def action_toggle_pause_focused(self) -> None:
        """Hold or release the worker behind the focused card.

        The pause is queued — the in-flight turn (if any) is allowed to
        finish so the model isn't aborted mid-thought. Pause is only
        offered for currently running cards; resume is offered for any
        paused card (running OR retrying) because pause now persists
        across worker exit and a held ticket may have moved into the
        retry queue.
        """
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        issue_id = focused.issue.id
        if self._orch.is_paused(issue_id):
            if self._orch.resume_worker(issue_id):
                self.notify(f"resumed {focused.issue.identifier}", timeout=2)
            else:
                self.notify("resume had no effect", timeout=2)
        else:
            if focused.status.runtime != "running":
                self.notify(
                    f"only running workers can be paused (runtime={focused.status.runtime})",
                    timeout=3,
                )
                return
            if self._orch.pause_worker(issue_id):
                self.notify(
                    f"paused {focused.issue.identifier} (after current turn)",
                    timeout=3,
                )
            else:
                self.notify("pause had no effect", timeout=2)
        # Snapshot polling drives the visual update on the next tick; force
        # an immediate redraw so the keystroke feels responsive.
        self._refresh_runtime()

    # ----- Iter3: detail pane + filter --------------------------------

    def action_toggle_detail(self) -> None:
        self._detail_visible = not self._detail_visible
        pane = self.query_one(DetailPane)
        pane.set_visible(self._detail_visible)
        self._last_focused_card_id = None  # force a refresh
        self._refresh_detail_pane()

    def _refresh_detail_pane(self) -> None:
        if not self._detail_visible:
            return
        try:
            pane = self.query_one(DetailPane)
        except Exception:
            return
        focused = self.focused
        if isinstance(focused, IssueCard):
            # Live runtime fields (turn count, tokens, last_message) keep
            # changing each tick, so we re-render even when the same card
            # is still focused.
            self._last_focused_card_id = focused.id
            pane.show_for(focused.issue, focused.status)
            return
        # Focus may have shifted INTO the pane itself (user pressed `]` to
        # scroll the description). Keep showing the previously focused card
        # — otherwise the pane would blank out the moment the user sat down
        # in it. Live runtime fields still update by re-resolving the card.
        if (
            focused is not None
            and self._last_focused_card_id is not None
            and self._is_within_detail_pane(focused)
        ):
            card = self._find_card_by_id(self._last_focused_card_id)
            if card is not None:
                pane.show_for(card.issue, card.status)
            return
        if self._last_focused_card_id is not None:
            self._last_focused_card_id = None
            pane.show_placeholder()

    @staticmethod
    def _is_within_detail_pane(node: Any) -> bool:
        cur: Any = node
        while cur is not None:
            if isinstance(cur, DetailPane):
                return True
            cur = getattr(cur, "parent", None)
        return False

    def _find_card_by_id(self, card_id: str) -> IssueCard | None:
        try:
            return self.query_one(f"#{card_id}", IssueCard)
        except Exception:
            return None

    def action_focus_detail(self) -> None:
        """Park focus inside the detail pane so arrow keys scroll its body."""
        if not self._detail_visible:
            self.notify("detail pane is hidden — press p to show", timeout=2)
            return
        try:
            pane = self.query_one(DetailPane)
        except Exception:
            return
        pane.scroll.focus()

    def action_focus_board(self) -> None:
        """Return focus to the first card in the first non-empty visible lane."""
        for lane in self._lanes.values():
            if not lane.display or lane.is_empty:
                continue
            for card in lane.query(IssueCard):
                card.focus()
                return
            lane.focus()
            return

    def action_open_filter(self) -> None:
        bar = self.query_one(FilterBar)
        bar.set_visible(True)
        try:
            inp = bar.query_one("#filter-input", Input)
        except Exception:
            return
        inp.focus()

    def action_escape(self) -> None:
        # Esc cascades — if filter is open, close it; else if zoomed, unzoom.
        bar = self.query_one(FilterBar)
        if bar.is_open:
            self._close_filter()
            return
        if self._zoomed_lane is not None:
            self._zoomed_lane = None
            self._apply_lane_widths()

    def _close_filter(self) -> None:
        bar = self.query_one(FilterBar)
        try:
            inp = bar.query_one("#filter-input", Input)
            inp.value = ""
        except Exception:
            pass
        bar.set_visible(False)
        self._filter_query = ""
        self._refresh_runtime()
        # Move focus back to the first non-empty visible lane so j/k still work.
        for lane in self._lanes.values():
            if lane.display and not lane.is_empty:
                lane.focus()
                break

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(event.input, "id", "") != "filter-input":
            return
        self._filter_query = (event.value or "").strip().lower()
        self._refresh_runtime()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if getattr(event.input, "id", "") != "filter-input":
            return
        # Enter: keep filter active, but move focus back to the board so
        # arrow keys / digits work again.
        for lane in self._lanes.values():
            if lane.display and not lane.is_empty:
                lane.focus()
                break

    def on_descendant_focus(self, event: Any) -> None:
        # Whenever focus lands on a different IssueCard, update the detail
        # pane. Cheaper than polling self.focused on every heartbeat tick.
        del event
        self._refresh_detail_pane()

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


def _matches_filter(issue: Issue, query: str) -> bool:
    """Case-insensitive substring match against identifier / title / labels.

    `query` must already be lowercased by the caller — saving the .lower() per
    candidate keeps the per-tick filter cheap when the board has many cards.
    """
    if not query:
        return True
    if query in (issue.identifier or "").lower():
        return True
    if query in (issue.title or "").lower():
        return True
    for label in issue.labels or ():
        if query in (label or "").lower():
            return True
    return False
