"""CLI entry point.

Subcommands:
    symphony [WORKFLOW]            run orchestrator (optionally with HTTP API via --port)
    symphony tui [WORKFLOW]        run orchestrator + Jira-style CLI Kanban TUI
    symphony board ...             file-tracker board helper
    symphony doctor [WORKFLOW]     preflight checks for WORKFLOW.md
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from .errors import SymphonyError
from .logging import configure_logging
from .orchestrator import Orchestrator
from .server import build_app, run_server
from .workflow import WorkflowState, resolve_workflow_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony",
        description="Symphony multi-agent — Codex / Claude Code / Gemini orchestration.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="enable HTTP JSON API on this port (overrides server.port)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host for HTTP API (default: loopback)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="log level: DEBUG, INFO, WARN, ERROR",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="launch the Jira-style CLI Kanban board (same as `symphony tui ...`)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    log = configure_logging(args.log_level)

    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.exists():
        log.error("workflow_path_missing", path=str(workflow_path))
        return 1

    state = WorkflowState(workflow_path)
    cfg, err = state.reload()
    if cfg is None:
        log.error("workflow_load_failed", error=str(err))
        return 1

    orchestrator = Orchestrator(state)
    try:
        await orchestrator.start()
    except SymphonyError as exc:
        log.error("startup_failed", error=str(exc))
        return 1

    server_port = args.port if args.port is not None else cfg.server.port
    runner = None
    if server_port is not None:
        app = build_app(orchestrator)
        runner, bound = await run_server(app, args.host, server_port)
        log.info("http_extension_active", host=args.host, port=bound)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass  # Windows / restricted env

    tui_task: asyncio.Task[None] | None = None
    if args.tui:
        from .tui import KanbanTUI

        tui = KanbanTUI(orchestrator, state)

        async def _tui_runner() -> None:
            try:
                await tui.run()
            finally:
                stop_event.set()

        tui_task = asyncio.create_task(_tui_runner(), name="symphony-tui")

    try:
        await stop_event.wait()
    finally:
        log.info("shutdown_initiated")
        if tui_task is not None:
            tui_task.cancel()
            try:
                await tui_task
            except (asyncio.CancelledError, Exception):
                pass
        await orchestrator.stop()
        if runner is not None:
            await runner.cleanup()
        log.info("shutdown_complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "board":
        from . import board_cli

        return board_cli.main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "doctor":
        from . import doctor

        return doctor.main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "tui":
        # Rewrite `symphony tui [...args]` as `symphony --tui [...args]`.
        raw_argv = ["--tui", *raw_argv[1:]]
    args = _build_parser().parse_args(raw_argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
