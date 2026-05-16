#!/usr/bin/env python3
"""Symphony Board Viewer — 정적 HTML 서버 + Symphony API 프록시.

역할:
  1. 정적 파일 serving (이 디렉토리 / index.html, src/*)
  2. Symphony API 프록시: /api/symphony/state, /api/symphony/<ID>
  3. Kanban 파일 인덱스/원본: /api/kanban/index, /api/kanban/<ID>.md

설계 원칙:
  - stdlib 만 사용 (Python 3.11+)
  - 모든 동작 READ-ONLY (state-변경 endpoint는 호출하지 않음)
  - CORS 허용 (`*`) — 같은 origin이므로 사실상 보험
  - Symphony가 죽어도 board file 인덱스는 동작해야 함 (degraded mode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent
SYMPHONY_BASE = os.environ.get("SYMPHONY_BASE", "http://127.0.0.1:9999")
PORT = int(os.environ.get("BOARD_VIEWER_PORT", "8765"))
SYMPHONY_TIMEOUT = 2.0  # seconds


def _resolve_kanban_dir(cli_path: str | None) -> Path:
    """KANBAN_DIR 우선순위:
       1. --kanban CLI 인자
       2. BOARD_VIEWER_KANBAN_DIR 환경변수
       3. $PWD/kanban (현재 작업 디렉토리에 kanban이 있으면)
       4. ROOT_DIR.parent.parent/kanban (dograh-demo 내부 호출 fallback)
    """
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_path = os.environ.get("BOARD_VIEWER_KANBAN_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve()
    cwd_kanban = Path.cwd() / "kanban"
    if cwd_kanban.is_dir():
        return cwd_kanban.resolve()
    return (ROOT_DIR.parent.parent / "kanban").resolve()


# 부팅 시 결정 — main에서 argparse 후 set
KANBAN_DIR: Path = _resolve_kanban_dir(None)

ACTIVE_STATES = ["Todo", "Explore", "In Progress", "Review", "QA", "Learn"]
TERMINAL_STATES = ["Done", "Cancelled", "Blocked", "Archive"]
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES

# 정적 파일 MIME
MIME_BY_EXT = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# ---------------------------------------------------------------------------
# 간단 YAML frontmatter 파서 (stdlib 만)
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _coerce_scalar(value: str) -> Any:
    v = value.strip()
    if not v:
        return ""
    # quoted string
    if (v.startswith("'") and v.endswith("'")) or (
        v.startswith('"') and v.endswith('"')
    ):
        return v[1:-1]
    # int
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except ValueError:
            pass
    # bool / null
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    return v


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """매우 단순한 YAML frontmatter 파서.

    지원: scalar value, 단순 list ('- item' 형식). 중첩 dict는 지원하지 않음.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    block = m.group(1)
    body = text[m.end():]

    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            current_list_key = None
            continue

        # list item
        list_match = re.match(r"^\s*-\s+(.*)$", line)
        if list_match and current_list_key is not None:
            item = _coerce_scalar(list_match.group(1))
            data[current_list_key].append(item)
            continue

        # key: value
        kv = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1)
        rest = kv.group(2).strip()
        if rest == "":
            # next lines may be list
            data[key] = []
            current_list_key = key
        else:
            data[key] = _coerce_scalar(rest)
            current_list_key = None

    return data, body


# ---------------------------------------------------------------------------
# Symphony API 호출 (전부 GET; mutating endpoint는 호출 안 함)
# ---------------------------------------------------------------------------


def symphony_get(path: str) -> tuple[int, bytes, str]:
    """Symphony API GET. 반환: (status, body_bytes, content_type).

    네트워크 실패는 (599, b'{"error":...}', 'application/json') 으로 매핑.
    """
    url = f"{SYMPHONY_BASE.rstrip('/')}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=SYMPHONY_TIMEOUT) as resp:
            body = resp.read()
            ct = resp.headers.get("Content-Type", "application/json")
            return resp.status, body, ct
    except urllib.error.HTTPError as e:
        try:
            payload = e.read()
        except Exception:
            payload = b""
        ct = e.headers.get("Content-Type", "application/json") if e.headers else "application/json"
        return e.code, payload, ct
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        msg = json.dumps(
            {"error": {"code": "symphony_unreachable", "message": str(e)}}
        ).encode("utf-8")
        return 599, msg, "application/json"


# ---------------------------------------------------------------------------
# Kanban 파일 인덱스
# ---------------------------------------------------------------------------


def list_kanban_tickets() -> list[dict[str, Any]]:
    """kanban/*.md 전체를 scandir → frontmatter 파싱 → 정렬된 list."""
    tickets: list[dict[str, Any]] = []
    if not KANBAN_DIR.exists():
        return tickets
    for entry in os.scandir(KANBAN_DIR):
        if not entry.is_file() or not entry.name.endswith(".md"):
            continue
        path = Path(entry.path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = parse_frontmatter(text)
        ticket_id = fm.get("id") or fm.get("identifier") or path.stem
        title = fm.get("title") or ticket_id
        state = fm.get("state") or "Todo"
        priority = fm.get("priority")
        try:
            priority = int(priority) if priority is not None else None
        except (TypeError, ValueError):
            priority = None
        labels = fm.get("labels") or []
        if not isinstance(labels, list):
            labels = [labels]
        tickets.append(
            {
                "id": ticket_id,
                "identifier": fm.get("identifier") or ticket_id,
                "title": title,
                "state": state,
                "priority": priority,
                "labels": labels,
                "created_at": fm.get("created_at"),
                "updated_at": fm.get("updated_at"),
                "file": path.name,
            }
        )
    # 정렬: state 순 → priority asc → id
    state_order = {s: i for i, s in enumerate(ALL_STATES)}
    tickets.sort(
        key=lambda t: (
            state_order.get(t["state"], 999),
            t["priority"] if t["priority"] is not None else 99,
            t["id"],
        )
    )
    return tickets


def read_kanban_file(ticket_id: str) -> bytes | None:
    """단일 .md 원본. path traversal 방어."""
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", ticket_id)
    if not safe:
        return None
    candidate = KANBAN_DIR / f"{safe}.md"
    try:
        # symlink resolve 후 KANBAN_DIR 내부인지 확인
        resolved = candidate.resolve()
        if KANBAN_DIR.resolve() not in resolved.parents:
            return None
        return resolved.read_bytes()
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "BoardViewer/1.0"

    # 로그를 stderr로, 색상 없이 깔끔하게.
    # 부모 BaseHTTPRequestHandler.log_message(self, format, *args) 시그니처를 그대로 따라
    # Pyright 호환성 유지 (파라미터명 reportIncompatibleMethodOverride 회피).
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, D401, N802
        sys.stderr.write(
            "[%s] %s\n" % (self.log_date_time_string(), format % args)
        )

    # ---- 공통 응답 헬퍼 ----
    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/octet-stream",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # CORS — 같은 origin이지만 보험
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # ---- CORS preflight ----
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    # ---- GET ----
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        # API: Symphony state 프록시
        if path == "/api/symphony/state":
            status, body, ct = symphony_get("/api/v1/state")
            self._send(status, body, ct)
            return

        # API: 개별 issue 프록시 (read-only)
        m = re.fullmatch(r"/api/symphony/([A-Za-z0-9_\-]+)", path)
        if m:
            status, body, ct = symphony_get(f"/api/v1/{m.group(1)}")
            self._send(status, body, ct)
            return

        # API: 칸반 인덱스
        if path == "/api/kanban/index":
            tickets = list_kanban_tickets()
            self._send_json(
                200,
                {
                    "states": ALL_STATES,
                    "active_states": ACTIVE_STATES,
                    "terminal_states": TERMINAL_STATES,
                    "tickets": tickets,
                    "count": len(tickets),
                    # project_name: kanban_dir의 부모 디렉토리 이름. 헤더 표시용.
                    # 실제 working repo 식별 정보가 박히지 않도록 generic하게 노출.
                    "project_name": KANBAN_DIR.parent.name,
                },
            )
            return

        # API: 칸반 원본 .md
        m = re.fullmatch(r"/api/kanban/([A-Za-z0-9_\-]+)\.md", path)
        if m:
            raw = read_kanban_file(m.group(1))
            if raw is None:
                self._send_json(404, {"error": "not_found", "id": m.group(1)})
                return
            self._send(200, raw, "text/markdown; charset=utf-8")
            return

        # 정적 파일 — 안전한 join
        if path == "/" or path == "":
            path = "/index.html"
        # 시작 슬래시 제거
        rel = path.lstrip("/")
        target = (ROOT_DIR / rel).resolve()
        # ROOT_DIR 밖으로 나가면 거부
        try:
            target.relative_to(ROOT_DIR)
        except ValueError:
            self._send(403, b"forbidden", "text/plain")
            return
        if not target.exists() or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ext = target.suffix.lower()
        ct = MIME_BY_EXT.get(ext, "application/octet-stream")
        try:
            body = target.read_bytes()
        except OSError:
            self._send(500, b"read error", "text/plain")
            return
        self._send(200, body, ct)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Symphony Board Viewer — read-only kanban + symphony state HUD"
    )
    parser.add_argument(
        "--kanban",
        metavar="DIR",
        default=None,
        help="kanban 디렉토리 경로 (생략 시: $BOARD_VIEWER_KANBAN_DIR → $PWD/kanban → fallback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP 포트 (생략 시: $BOARD_VIEWER_PORT 또는 8765)",
    )
    parser.add_argument(
        "--symphony",
        metavar="URL",
        default=None,
        help="Symphony orchestrator base URL (생략 시: $SYMPHONY_BASE 또는 http://127.0.0.1:9999)",
    )
    args = parser.parse_args()

    # 전역 set — BoardHandler 내부에서 참조하는 모듈 글로벌을 갱신
    global KANBAN_DIR, PORT, SYMPHONY_BASE
    KANBAN_DIR = _resolve_kanban_dir(args.kanban)
    if args.port is not None:
        PORT = args.port
    if args.symphony is not None:
        SYMPHONY_BASE = args.symphony

    addr = ("127.0.0.1", PORT)
    httpd = ThreadingHTTPServer(addr, BoardHandler)
    url = f"http://{addr[0]}:{addr[1]}/"
    sys.stdout.write(
        f"""
Symphony Board Viewer
---------------------
  접속 URL  : {url}
  정적 root : {ROOT_DIR}
  kanban    : {KANBAN_DIR}
  symphony  : {SYMPHONY_BASE}  (read-only proxy)
  종료      : Ctrl-C
"""
    )
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\n종료합니다.\n")
        httpd.server_close()


if __name__ == "__main__":
    main()
