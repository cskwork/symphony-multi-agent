"""Optional HTTP JSON API.

The upstream HTML dashboard at `/` was removed in the multi-agent fork — the
primary UI is the CLI Kanban (`symphony tui`). Remaining endpoints are the
programmatic JSON API: state snapshots, per-issue debug, and a coalesced
refresh trigger.

Endpoints:
    GET  /                       — text hint pointing at `symphony tui`
    GET  /api/v1/state           — runtime snapshot
    GET  /api/v1/<identifier>    — issue debug detail
    POST /api/v1/refresh         — trigger immediate poll/reconcile
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web

from .logging import get_logger
from .orchestrator import Orchestrator


log = get_logger()

ROOT_HINT = (
    "symphony-multi-agent JSON API.\n"
    "The HTML dashboard was replaced by a CLI Kanban — run `symphony tui`.\n"
    "API: GET /api/v1/state, GET /api/v1/<identifier>, POST /api/v1/refresh,\n"
    "     POST /api/v1/<identifier>/pause, POST /api/v1/<identifier>/resume\n"
)


def _error_response(status: int, code: str, message: str) -> web.Response:
    body = {"error": {"code": code, "message": message}}
    return web.json_response(body, status=status)


def build_app(orchestrator: Orchestrator) -> web.Application:
    app = web.Application()

    async def handle_root(_request: web.Request) -> web.Response:
        return web.Response(text=ROOT_HINT, content_type="text/plain")

    async def handle_state(_request: web.Request) -> web.Response:
        return web.json_response(orchestrator.snapshot())

    async def handle_issue(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        snapshot = orchestrator.issue_snapshot(identifier)
        if snapshot is None:
            return _error_response(404, "issue_not_found", f"unknown issue {identifier}")
        return web.json_response(snapshot)

    async def handle_refresh(request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.body_exists else {}
        except json.JSONDecodeError:
            return _error_response(400, "invalid_json", "request body is not JSON")
        if body and not isinstance(body, dict):
            return _error_response(400, "invalid_body", "request body must be an object")
        coalesced = orchestrator.request_refresh()
        return web.json_response(
            {
                "queued": True,
                "coalesced": coalesced,
                "requested_at": _now_iso(),
                "operations": ["poll", "reconcile"],
            },
            status=202,
        )

    async def handle_pause(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        issue_id = orchestrator.find_running_issue_id(identifier)
        if issue_id is None:
            return _error_response(
                404, "issue_not_running", f"no running worker for {identifier}"
            )
        already = orchestrator.is_paused(issue_id)
        changed = orchestrator.pause_worker(issue_id)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "issue_id": issue_id,
                "paused": True,
                "changed": changed,
                "already_paused": already,
            }
        )

    async def handle_resume(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        issue_id = orchestrator.find_running_issue_id(identifier)
        if issue_id is None:
            return _error_response(
                404, "issue_not_running", f"no running worker for {identifier}"
            )
        changed = orchestrator.resume_worker(issue_id)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "issue_id": issue_id,
                "paused": False,
                "changed": changed,
            }
        )

    async def handle_method_not_allowed(request: web.Request) -> web.Response:
        return _error_response(405, "method_not_allowed", request.method)

    async def handle_debug_tasks(_request: web.Request) -> web.Response:
        # Dump every live asyncio task with its suspended coroutine stack.
        # `Task.get_stack()` returns the deepest frame the task is parked
        # at — exactly what py-spy can't show us across the await boundary.
        out = []
        for t in asyncio.all_tasks():
            stack_frames = []
            for frame in t.get_stack():
                stack_frames.append(
                    f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
                )
            out.append(
                {
                    "name": t.get_name(),
                    "done": t.done(),
                    "cancelled": t.cancelled() if t.done() else False,
                    "coro_repr": repr(t.get_coro()),
                    "stack": stack_frames,
                }
            )
        return web.json_response({"tasks": out})

    app.router.add_get("/", handle_root)
    app.router.add_get("/api/v1/state", handle_state)
    app.router.add_get("/api/v1/refresh", handle_method_not_allowed)
    app.router.add_post("/api/v1/refresh", handle_refresh)
    app.router.add_get("/api/v1/_debug/tasks", handle_debug_tasks)
    app.router.add_post("/api/v1/{identifier}/pause", handle_pause)
    app.router.add_post("/api/v1/{identifier}/resume", handle_resume)
    app.router.add_get("/api/v1/{identifier}", handle_issue)

    return app


def _now_iso() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def run_server(
    app: web.Application, host: str, port: int
) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    bound_port = port
    for tcp_site in runner.sites:
        sockets = getattr(tcp_site, "_server", None)
        if sockets is not None and getattr(sockets, "sockets", None):
            bound_port = sockets.sockets[0].getsockname()[1]
            break
    log.info("http_server_started", host=host, port=bound_port)
    return runner, bound_port
