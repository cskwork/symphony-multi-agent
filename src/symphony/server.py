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

import json

from aiohttp import web

from .logging import get_logger
from .orchestrator import Orchestrator


log = get_logger()

ROOT_HINT = (
    "symphony-multi-agent JSON API.\n"
    "The HTML dashboard was replaced by a CLI Kanban — run `symphony tui`.\n"
    "API: GET /api/v1/state, GET /api/v1/<identifier>, POST /api/v1/refresh\n"
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

    async def handle_method_not_allowed(request: web.Request) -> web.Response:
        return _error_response(405, "method_not_allowed", request.method)

    app.router.add_get("/", handle_root)
    app.router.add_get("/api/v1/state", handle_state)
    app.router.add_get("/api/v1/refresh", handle_method_not_allowed)
    app.router.add_post("/api/v1/refresh", handle_refresh)
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
