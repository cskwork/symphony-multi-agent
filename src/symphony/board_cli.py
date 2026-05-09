"""`symphony board ...` — minimal helper to manage a file-based Kanban.

Subcommands:
    init <root>                       create the board directory + sample
    ls   [--state STATE]              list tickets (optionally filtered)
    new  <id> <title> [--state ...]   create a new ticket
    mv   <id> <new-state>             change a ticket's state
    show <id>                         print a ticket's contents

These commands operate directly on the configured `tracker.board_root` from
the current `WORKFLOW.md` (or an explicit one passed with --workflow).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import SymphonyError
from .tracker_file import FileBoardTracker, parse_ticket_file
from .workflow import (
    DEFAULT_BOARD_ROOT_NAME,
    TrackerConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


def _resolve_tracker(args: argparse.Namespace) -> TrackerConfig | None:
    """Return TrackerConfig from WORKFLOW.md or None to fall back to --root."""
    workflow_path = resolve_workflow_path(args.workflow)
    if workflow_path.exists():
        try:
            cfg = build_service_config(load_workflow(workflow_path))
        except SymphonyError as exc:
            print(f"warn: workflow load failed ({exc}); using --root", file=sys.stderr)
            return None
        if cfg.tracker.kind != "file":
            print(
                f"warn: tracker.kind is {cfg.tracker.kind!r}, not 'file'; using --root",
                file=sys.stderr,
            )
            return None
        return cfg.tracker
    return None


def _tracker_from_root(root: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Cancelled"),
        board_root=root.resolve(),
    )


def _get_tracker(args: argparse.Namespace) -> TrackerConfig:
    cfg = _resolve_tracker(args)
    if cfg is not None:
        return cfg
    if args.root is None:
        # Default: ./board next to the workflow.
        wf_path = resolve_workflow_path(args.workflow)
        return _tracker_from_root(wf_path.parent / DEFAULT_BOARD_ROOT_NAME)
    return _tracker_from_root(Path(args.root))


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    tracker = _tracker_from_root(root)
    fbt = FileBoardTracker(tracker)
    sample_id = "DEMO-001"
    if (root / f"{sample_id}.md").exists():
        print(f"board already initialized at {root}")
        return 0
    fbt.create(
        identifier=sample_id,
        title="Symphony demo ticket",
        state="Todo",
        priority=2,
        labels=["demo"],
        description=(
            "This is a sample ticket. Replace the body with your real task. "
            "Symphony will pick it up on the next poll tick if its state is in "
            "tracker.active_states."
        ),
    )
    print(f"initialized board at {root}, sample ticket {sample_id}.md")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    issues = fbt._scan_all()  # type: ignore[attr-defined]
    if args.state:
        target = args.state.lower()
        issues = [i for i in issues if i.state.lower() == target]
    if not issues:
        print("(no tickets)")
        return 0
    width_id = max(len(i.identifier) for i in issues)
    width_state = max(len(i.state) for i in issues)
    for i in issues:
        prio = "" if i.priority is None else f" P{i.priority}"
        labels = f" [{', '.join(i.labels)}]" if i.labels else ""
        print(
            f"{i.identifier:<{width_id}}  {i.state:<{width_state}}  {i.title}{prio}{labels}"
        )
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    try:
        path = fbt.create(
            identifier=args.id,
            title=args.title,
            state=args.state,
            priority=args.priority,
            labels=args.labels.split(",") if args.labels else None,
            description=args.description or "",
        )
    except SymphonyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created {path}")
    return 0


def cmd_mv(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    try:
        path = fbt.transition(args.id, args.state)
    except SymphonyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.id} -> {args.state} ({path})")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    tracker = _get_tracker(args)
    fbt = FileBoardTracker(tracker)
    path = fbt.find_path(args.id)
    if path is None:
        print(f"error: ticket {args.id} not found", file=sys.stderr)
        return 1
    front, body = parse_ticket_file(path)
    print(f"# {front.get('identifier', args.id)} ({front.get('state', '?')})")
    print(f"title: {front.get('title', '')}")
    if front.get("priority") is not None:
        print(f"priority: {front['priority']}")
    if front.get("labels"):
        print(f"labels: {', '.join(front['labels'])}")
    if body:
        print()
        print(body)
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony board",
        description="Manage a file-based Kanban tracker.",
    )

    def add_workflow_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workflow", default=None, help="path to WORKFLOW.md")
        p.add_argument("--root", default=None, help="board root (overrides WORKFLOW.md)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize a new board directory")
    p_init.add_argument("root", help="board directory")
    p_init.set_defaults(func=cmd_init)

    p_ls = sub.add_parser("ls", help="list tickets")
    add_workflow_args(p_ls)
    p_ls.add_argument("--state", default=None, help="filter by state (case-insensitive)")
    p_ls.set_defaults(func=cmd_ls)

    p_new = sub.add_parser("new", help="create a ticket")
    add_workflow_args(p_new)
    p_new.add_argument("id", help="ticket identifier (e.g. DEV-001)")
    p_new.add_argument("title", help="ticket title")
    p_new.add_argument("--state", default="Todo")
    p_new.add_argument("--priority", type=int, default=None)
    p_new.add_argument("--labels", default=None, help="comma-separated labels")
    p_new.add_argument("--description", default=None)
    p_new.set_defaults(func=cmd_new)

    p_mv = sub.add_parser("mv", help="change a ticket state")
    add_workflow_args(p_mv)
    p_mv.add_argument("id", help="ticket identifier")
    p_mv.add_argument("state", help="new state")
    p_mv.set_defaults(func=cmd_mv)

    p_show = sub.add_parser("show", help="print a ticket's contents")
    add_workflow_args(p_show)
    p_show.add_argument("id", help="ticket identifier")
    p_show.set_defaults(func=cmd_show)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
