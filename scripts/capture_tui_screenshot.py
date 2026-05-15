"""Regenerate `docs/tui-screenshot.svg` from the live Textual TUI.

Builds a `KanbanApp` against an in-memory demo state (no tracker IO),
runs it via Textual's headless `Pilot`, focuses a running card so the
detail pane has interesting content, and exports the rendered screen
as SVG via `App.save_screenshot()`.

Usage:
    .venv/bin/python scripts/capture_tui_screenshot.py

The demo state intentionally exercises:
- a running card with token counters (focused → fills the detail pane),
- a retrying card (yellow ↻),
- a blocked-by ticket,
- terminal Done + Archive lanes,
to keep the screenshot visually informative on first-impression.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Reuse the test fixtures so the demo App matches what the suite already
# exercises — no parallel mock surface to drift.
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from symphony.issue import Issue  # noqa: E402
from symphony.tui import KanbanApp  # noqa: E402
from test_tui import _StaticWorkflowState, _StubOrchestrator, _make_config  # noqa: E402


SVG_OUT = ROOT / "docs" / "tui-screenshot.svg"


def _issue(
    identifier: str,
    *,
    state: str,
    title: str,
    priority: int = 2,
    labels: tuple[str, ...] = (),
    blocked_by_id: str | None = None,
    description: str | None = None,
    updated_offset_days: int = 0,
) -> Issue:
    from symphony.issue import BlockerRef

    blocked: tuple[BlockerRef, ...] = ()
    if blocked_by_id is not None:
        blocked = (BlockerRef(id=blocked_by_id, identifier=blocked_by_id, state=None),)
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=title,
        description=description,
        priority=priority,
        state=state,
        labels=labels,
        blocked_by=blocked,
        updated_at=datetime.now(timezone.utc) - timedelta(days=updated_offset_days),
    )


def _demo_issues() -> list[Issue]:
    return [
        # Todo lane — fresh + retrying + blocked.
        _issue(
            "DEMO-120",
            state="Todo",
            title="Migrate auth middleware to async",
            priority=1,
            labels=("backend", "tech-debt"),
        ),
        _issue(
            "DEMO-111",
            state="Todo",
            title="Refactor cache invalidation helper",
            priority=2,
        ),
        _issue(
            "DEMO-121",
            state="Todo",
            title="Wire feature flag for new dashboard",
            priority=2,
            blocked_by_id="DEMO-098",
        ),
        # In Progress — two running, will overlay tokens via snapshot.
        _issue(
            "DEMO-104",
            state="In Progress",
            title="Fix race condition in pagination cursor",
            priority=1,
            description="Patched cursor advance; running test suite...",
        ),
        _issue(
            "DEMO-098",
            state="In Progress",
            title="Add /api/search rate limiting",
            priority=2,
            description="Added token-bucket middleware with 60 req/min default.",
        ),
        # Review.
        _issue(
            "DEMO-122",
            state="Review",
            title="Doc: contributor onboarding guide",
            priority=3,
            labels=("docs",),
        ),
        # Done — recent.
        _issue(
            "DEMO-088",
            state="Done",
            title="Drop dead-code paths from worker queue",
            priority=2,
            labels=("chore",),
            updated_offset_days=2,
        ),
        _issue(
            "DEMO-091",
            state="Done",
            title="Bump dependencies to satisfy CVE-2026-4001",
            priority=2,
            updated_offset_days=8,
        ),
        # Archive — what auto-sweep moved.
        _issue(
            "DEMO-074",
            state="Archive",
            title="Old experimental flag cleanup",
            priority=3,
            updated_offset_days=45,
        ),
    ]


def _demo_snapshot() -> dict[str, Any]:
    """Mock orchestrator snapshot — running cards + token totals + rate-limit chip."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "counts": {"running": 2, "retrying": 1},
        "codex_totals": {
            "input_tokens": 84_200,
            "output_tokens": 27_640,
            "total_tokens": 111_840,
            "seconds_running": 412.7,
        },
        "running": [
            {
                "issue_id": "id-DEMO-104",
                "turn_count": 4,
                "last_event": "agent_turn_completed",
                "last_event_at": now_iso,
                "tokens": {
                    "input_tokens": 14_200,
                    "output_tokens": 5_980,
                    "total_tokens": 20_180,
                },
                "last_message": "Patched cursor advance; running test suite...",
            },
            {
                "issue_id": "id-DEMO-098",
                "turn_count": 2,
                "last_event": "agent_turn_completed",
                "last_event_at": now_iso,
                "tokens": {
                    "input_tokens": 7_400,
                    "output_tokens": 3_910,
                    "total_tokens": 11_310,
                },
                "last_message": "Added token-bucket middleware with 60 req/min default.",
            },
        ],
        "retrying": [
            {
                "issue_id": "id-DEMO-111",
                "attempt": 2,
                "error": "turn_error: Turn timed out after 60min",
            }
        ],
        "rate_limits": {
            "requests_remaining": 4823,
            "tokens_remaining": "1.2M",
        },
        "generated_at": now_iso,
    }


def _stub_tracker_calls(issues: list[Issue]) -> None:
    """Replace tracker fetch helpers so the App reads from `issues` only."""
    from symphony import tui as tui_module

    active = [i for i in issues if i.state in {"Todo", "In Progress", "Review"}]
    terminal = [i for i in issues if i.state in {"Done", "Archive"}]
    tui_module._fetch_candidates = lambda _cfg: list(active)  # type: ignore[assignment]
    tui_module._fetch_terminals = lambda _cfg: list(terminal)  # type: ignore[assignment]


async def _capture() -> None:
    issues = _demo_issues()
    _stub_tracker_calls(issues)

    cfg = _make_config(
        active_states=("Todo", "In Progress", "Review"),
        terminal_states=("Done", "Archive"),
    )
    snap = _demo_snapshot()
    orch = _StubOrchestrator(snapshot=snap)
    app = KanbanApp(orch, _StaticWorkflowState(cfg))  # type: ignore[arg-type]

    # 180×40 keeps the SVG close to a typical wide terminal without forcing
    # word-wrap on card titles.
    async with app.run_test(size=(180, 40)) as pilot:
        # Two pauses: first lets the worker drain, second lets layout settle
        # after the Pilot reports the lanes have populated.
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        # Focus a running card so the right-hand detail pane has interesting
        # content (turn count, tokens, last_message, runtime). Without an
        # explicit focus the pane shows its empty placeholder, which makes
        # the right half of the screenshot look broken at first glance.
        from symphony.tui import IssueCard

        target = next(
            (
                c
                for c in app.query(IssueCard)
                if c.issue.identifier == "DEMO-104"
            ),
            None,
        )
        if target is not None:
            target.focus()
            await pilot.pause()
            await asyncio.sleep(0.05)
            await pilot.pause()
        SVG_OUT.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(SVG_OUT))

    print(f"wrote SVG → {SVG_OUT.relative_to(ROOT)}")


def main() -> None:
    asyncio.run(_capture())


if __name__ == "__main__":
    main()
