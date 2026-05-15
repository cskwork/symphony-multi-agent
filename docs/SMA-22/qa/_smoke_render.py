"""SMA-22 live-render smoke harness.

Builds a `KanbanTUI`, drives `_render()` against three width regimes,
and dumps the rendered text via `Console.export_text`. Mirrors the
spec's "Verification" steps without needing a real interactive TTY,
so the evidence is reproducible.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Add tests/ so we can reuse the helpers (`_service_config`, `_make_tui`).
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))

from rich.console import Console  # noqa: E402

from symphony.issue import Issue  # noqa: E402
from test_tui import _make_tui, _service_config  # noqa: E402


def _three_ticket_snapshot() -> tuple[dict, list[Issue]]:
    issues = [
        Issue(
            id="iss-live", identifier="SMA-100", title="Wire dim badge",
            description="Surface silence as a yellow badge on running cards.",
            priority=2, state="In Progress", branch_name=None, url=None,
            labels=("ux", "tui"), blocked_by=(),
            created_at=None, updated_at=None,
        ),
        Issue(
            id="iss-explore", identifier="SMA-101", title="Lane wrap design brief",
            description="Investigate ergonomic wrap thresholds.",
            priority=3, state="Explore", branch_name=None, url=None,
            labels=(), blocked_by=(),
            created_at=None, updated_at=None,
        ),
        Issue(
            id="iss-done", identifier="SMA-99", title="Persist token totals",
            description="Card retains in/out/total in dim style after Done.",
            priority=2, state="Done", branch_name=None, url=None,
            labels=(), blocked_by=(),
            created_at=None, updated_at=None,
        ),
    ]
    snap = {
        "generated_at": "2026-05-10T13:00:00Z",
        "counts": {"running": 1, "retrying": 0},
        "running": [
            {
                "issue_id": "iss-live", "issue_identifier": "SMA-100",
                "state": "In Progress", "session_id": "s",
                "turn_count": 4,
                "last_event": "agent_turn_completed",
                "last_message": "implementing _lane_rows()",
                "started_at": "2026-05-10T12:55:00Z",
                "last_event_at": datetime.now(timezone.utc).isoformat(),
                "tokens": {
                    "input_tokens": 12_300,
                    "output_tokens": 2_100,
                    "total_tokens": 14_400,
                },
            },
            {
                "issue_id": "iss-done", "issue_identifier": "SMA-99",
                "state": "Done", "session_id": "s2",
                "turn_count": 8,
                "last_event": "agent_turn_completed",
                "last_message": "wrapped up; tokens preserved",
                "started_at": "2026-05-10T11:00:00Z",
                "last_event_at": "2026-05-10T12:30:00Z",
                "tokens": {
                    "input_tokens": 1_000,
                    "output_tokens": 234,
                    "total_tokens": 1_234,
                },
            },
        ],
        "retrying": [],
        "codex_totals": {
            "input_tokens": 13_300, "output_tokens": 2_334,
            "total_tokens": 15_634, "seconds_running": 90.0,
        },
        "rate_limits": None,
    }
    return snap, issues


def render(width: int, lane_wrap_width: int, label: str) -> str:
    snap, issues = _three_ticket_snapshot()
    cfg = _service_config(lane_wrap_width=lane_wrap_width)
    tui = _make_tui(width=width, cfg=cfg, snapshot=snap, issues=issues)
    rendered = tui._render()  # noqa: SLF001 — render-path smoke
    console = Console(width=width, record=True, force_terminal=True, color_system=None)
    console.print(rendered)
    text = console.export_text()
    return f"=== {label} (width={width}, lane_wrap_width={lane_wrap_width}) ===\n{text}\n"


def main() -> None:
    out = []
    out.append(render(width=120, lane_wrap_width=200, label="narrow / default threshold"))
    out.append(render(width=160, lane_wrap_width=200, label="medium / wraps cleanly"))
    out.append(render(width=240, lane_wrap_width=200, label="wide / single row"))
    out.append(render(width=120, lane_wrap_width=0, label="narrow / wrap disabled (sentinel)"))
    sys.stdout.write("".join(out))


if __name__ == "__main__":
    main()
