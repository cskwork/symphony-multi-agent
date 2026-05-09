"""SPEC §13.7 — optional HTTP observability extension.

Endpoints:
    GET  /                       — minimal HTML dashboard
    GET  /api/v1/state           — runtime snapshot
    GET  /api/v1/<identifier>    — issue debug detail
    POST /api/v1/refresh         — trigger immediate poll/reconcile
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from .logging import get_logger
from .orchestrator import Orchestrator


log = get_logger()


def _error_response(status: int, code: str, message: str) -> web.Response:
    body = {"error": {"code": code, "message": message}}
    return web.json_response(body, status=status)


def build_app(orchestrator: Orchestrator) -> web.Application:
    app = web.Application()

    async def handle_root(_request: web.Request) -> web.Response:
        snapshot = orchestrator.snapshot()
        body = _render_dashboard(snapshot)
        return web.Response(text=body, content_type="text/html")

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


def _render_dashboard(snapshot: dict[str, Any]) -> str:
    counts = snapshot.get("counts", {})
    running = snapshot.get("running", [])
    retry = snapshot.get("retrying", [])
    totals = snapshot.get("codex_totals", {})
    rate_limits = snapshot.get("rate_limits")
    rows = "\n".join(
        f"<tr><td>{r['issue_identifier']}</td><td>{r['state']}</td>"
        f"<td>{r.get('turn_count', 0)}</td><td>{r.get('last_event') or ''}</td>"
        f"<td>{r['tokens']['total_tokens']}</td></tr>"
        for r in running
    ) or "<tr><td colspan='5'><em>none</em></td></tr>"
    retry_rows = "\n".join(
        f"<tr><td>{r['issue_identifier']}</td><td>{r['attempt']}</td>"
        f"<td>{r['error'] or ''}</td><td>{r['due_at']}</td></tr>"
        for r in retry
    ) or "<tr><td colspan='4'><em>none</em></td></tr>"
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Symphony</title>
<style>
  body {{ font-family: ui-monospace, monospace; padding: 1.5rem; }}
  h1 {{ font-size: 1.2rem; }}
  table {{ border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.7rem; text-align: left; }}
</style></head>
<body>
<h1>Symphony — generated_at {snapshot.get('generated_at', '?')}</h1>
<p>running={counts.get('running', 0)} retrying={counts.get('retrying', 0)}</p>
<h2>Running</h2>
<table>
<tr><th>identifier</th><th>state</th><th>turn</th><th>last_event</th><th>tokens</th></tr>
{rows}
</table>
<h2>Retrying</h2>
<table>
<tr><th>identifier</th><th>attempt</th><th>error</th><th>due_at</th></tr>
{retry_rows}
</table>
<h2>Totals</h2>
<pre>{json.dumps(totals, indent=2)}</pre>
<h2>Rate Limits</h2>
<pre>{json.dumps(rate_limits, indent=2) if rate_limits else 'null'}</pre>
</body></html>
"""


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
