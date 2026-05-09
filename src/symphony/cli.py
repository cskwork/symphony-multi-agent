"""SPEC §17.7 — CLI entry point."""

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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="symphony",
        description="Symphony — coding agent orchestration service.",
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
        help="enable HTTP observability extension on this port (overrides server.port)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host for HTTP extension (default: loopback)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="log level: DEBUG, INFO, WARN, ERROR",
    )
    return parser.parse_args(argv)


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

    try:
        await stop_event.wait()
    finally:
        log.info("shutdown_initiated")
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
    args = _parse_args(raw_argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
