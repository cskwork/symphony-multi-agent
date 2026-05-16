#!/usr/bin/env python3
"""Symphony Board Viewer — 정적 HTML 서버 + Symphony API 프록시.

역할:
  1. 정적 파일 serving (이 디렉토리 / index.html, src/*)
  2. Symphony API 프록시:
       GET  /api/symphony/state            — 보드 스냅샷
       GET  /api/symphony/<ID>             — 단일 이슈 디버그
       POST /api/symphony/refresh          — 즉시 reconcile/poll
       POST /api/symphony/<ID>/pause       — 다음 turn 경계에서 일시정지
       POST /api/symphony/<ID>/resume      — 일시정지 해제
  3. Kanban 파일 인덱스/원본: /api/kanban/index, /api/kanban/<ID>.md

설계 원칙:
  - stdlib 만 사용 (Python 3.11+)
  - 노출하는 mutating endpoint는 orchestrator가 이미 공개한 3개
    (refresh / pause / resume)만 화이트리스트로 프록시. 그 외 GET 전부.
  - 127.0.0.1 바인딩 + CORS `*` — 같은 origin 보험
  - Symphony가 죽어도 board file 인덱스는 동작해야 함 (degraded mode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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


def _resolve_project_root(cli_path: str | None, kanban_dir: Path) -> Path:
    if cli_path:
        p = Path(cli_path).expanduser().resolve()
        if p.is_dir():
            return p
    if kanban_dir.is_dir() and kanban_dir.name == "kanban":
        return kanban_dir.parent.resolve()
    return Path.cwd().resolve()


def _resolve_workflow_path(cli_path: str | None) -> Path | None:
    """WORKFLOW.md 우선순위:
       1. --workflow CLI 인자
       2. BOARD_VIEWER_WORKFLOW 환경변수
       3. $PWD/WORKFLOW.md
       4. kanban 부모 디렉토리의 WORKFLOW.md
    """
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_path = os.environ.get("BOARD_VIEWER_WORKFLOW")
    if env_path:
        return Path(env_path).expanduser().resolve()
    cwd_workflow = Path.cwd() / "WORKFLOW.md"
    if cwd_workflow.is_file():
        return cwd_workflow.resolve()
    kanban_dir = globals().get("KANBAN_DIR")
    if not isinstance(kanban_dir, Path):
        kanban_dir = _resolve_kanban_dir(None)
    kanban_parent_workflow = kanban_dir.parent / "WORKFLOW.md"
    if kanban_parent_workflow.is_file():
        return kanban_parent_workflow.resolve()
    return None


# 부팅 시 결정 — main에서 argparse 후 set
KANBAN_DIR: Path = _resolve_kanban_dir(None)
WORKFLOW_PATH: Path | None = _resolve_workflow_path(None)
PROJECT_ROOT: Path = Path.cwd()

ACTIVE_STATES = ["Todo", "Explore", "Plan", "In Progress", "Review", "QA", "Learn"]
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

    지원: scalar value, 단순 list ('- item' 형식), 1-depth nested map.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    block = m.group(1)
    body = text[m.end():]

    data: dict[str, Any] = {}
    current_list_key: str | None = None
    current_container_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            current_list_key = None
            current_container_key = None
            continue

        # one-level nested map item, e.g. agent:\n  kind: codex
        nested_kv = re.match(r"^\s+([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if nested_kv and current_container_key is not None:
            if not isinstance(data.get(current_container_key), dict):
                data[current_container_key] = {}
            data[current_container_key][nested_kv.group(1)] = _coerce_scalar(
                nested_kv.group(2)
            )
            current_list_key = None
            continue

        # list item
        list_match = re.match(r"^\s*-\s+(.*)$", line)
        if list_match and current_list_key is not None:
            if not isinstance(data.get(current_list_key), list):
                data[current_list_key] = []
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
            current_container_key = key
        else:
            data[key] = _coerce_scalar(rest)
            current_list_key = None
            current_container_key = None

    return data, body


def _parse_agent_kind(front: dict[str, Any]) -> str | None:
    raw = front.get("agent_kind")
    if raw is None and isinstance(front.get("agent"), dict):
        raw = front["agent"].get("kind")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip().lower()


def _parse_done_agent_kind(
    front: dict[str, Any], state: str, agent_kind: str | None
) -> str | None:
    raw = front.get("done_agent_kind") or front.get("completed_agent_kind")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    if state.strip().lower() == "done":
        return agent_kind
    return None


# ---------------------------------------------------------------------------
# Git branch / WORKFLOW branch policy helpers
# ---------------------------------------------------------------------------


def _workflow_repo_dir() -> Path:
    if WORKFLOW_PATH is not None:
        return WORKFLOW_PATH.parent
    return KANBAN_DIR.parent


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or _workflow_repo_dir()),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git failed").strip())
    return result.stdout


def list_git_branches() -> dict[str, Any]:
    """Return local git branches plus the currently checked-out branch."""
    try:
        raw = _git("branch", "--format=%(refname:short)")
        current = _git("branch", "--show-current").strip()
        repo_root = _git("rev-parse", "--show-toplevel").strip()
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "branches": [],
            "current_branch": "",
            "repo_root": "",
            "workflow_path": str(WORKFLOW_PATH or ""),
            "feature_base_branch": "",
            "merge_target_branch": "",
        }
    branches = sorted({line.strip() for line in raw.splitlines() if line.strip()})
    policy = read_workflow_branch_policy()
    return {
        "ok": True,
        "branches": branches,
        "current_branch": current,
        "repo_root": repo_root,
        "workflow_path": str(WORKFLOW_PATH or ""),
        "feature_base_branch": policy.get("feature_base_branch", ""),
        "merge_target_branch": policy.get("auto_merge_target_branch", ""),
    }


def read_workflow_branch_policy() -> dict[str, str]:
    if WORKFLOW_PATH is None or not WORKFLOW_PATH.exists():
        return {"feature_base_branch": "", "auto_merge_target_branch": ""}
    try:
        front, _body = parse_frontmatter(WORKFLOW_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {"feature_base_branch": "", "auto_merge_target_branch": ""}
    agent = front.get("agent") if isinstance(front.get("agent"), dict) else {}
    return {
        "feature_base_branch": _policy_value(agent.get("feature_base_branch")),
        "auto_merge_target_branch": _policy_value(agent.get("auto_merge_target_branch")),
    }


def _policy_value(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _yaml_scalar(value: str) -> str:
    return json.dumps(value)


def _split_workflow_frontmatter(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("WORKFLOW.md must use YAML front matter")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx], lines[idx:]
    raise ValueError("WORKFLOW.md front matter is not terminated")


def update_workflow_branch_policy(
    *, feature_base_branch: str | None = None, merge_target_branch: str | None = None
) -> dict[str, str]:
    if WORKFLOW_PATH is None:
        raise ValueError("WORKFLOW.md path is unavailable")
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    front_lines, tail_lines = _split_workflow_frontmatter(text)
    updates: dict[str, str] = {}
    if feature_base_branch is not None:
        updates["feature_base_branch"] = feature_base_branch
    if merge_target_branch is not None:
        updates["auto_merge_target_branch"] = merge_target_branch
    if updates:
        front_lines = _upsert_agent_frontmatter_fields(front_lines, updates)
        WORKFLOW_PATH.write_text("---\n" + "".join(front_lines) + "".join(tail_lines), encoding="utf-8")
    return read_workflow_branch_policy()


def _upsert_agent_frontmatter_fields(
    lines: list[str], updates: dict[str, str]
) -> list[str]:
    agent_idx = next((i for i, line in enumerate(lines) if re.match(r"^agent:\s*$", line)), None)
    if agent_idx is None:
        insert = ["agent:\n"] + [f"  {key}: {_yaml_scalar(value)}\n" for key, value in updates.items()]
        return lines + (["\n"] if lines and lines[-1].strip() else []) + insert

    end = len(lines)
    for idx in range(agent_idx + 1, len(lines)):
        if re.match(r"^[A-Za-z0-9_\-]+:\s*", lines[idx]):
            end = idx
            break

    existing: set[str] = set()
    for idx in range(agent_idx + 1, end):
        match = re.match(r"^(\s+)([A-Za-z0-9_\-]+)\s*:\s*.*$", lines[idx])
        if not match:
            continue
        key = match.group(2)
        if key in updates:
            lines[idx] = f"{match.group(1)}{key}: {_yaml_scalar(updates[key])}\n"
            existing.add(key)

    missing = [key for key in updates if key not in existing]
    if missing:
        insert_at = end
        lines[insert_at:insert_at] = [
            f"  {key}: {_yaml_scalar(updates[key])}\n" for key in missing
        ]
    return lines


# ---------------------------------------------------------------------------
# Settings (.symphony/config.yaml + .env)
#
# config.yaml 은 UI 가 작성하는 단일 진실 원천. .env 는 그 파일에서 파생된
# view — `scripts/*.sh` 가 `source .env` 로 흡수하기 때문에 atomic 동기화가
# 필요하다. UI 가 관리하지 않는 .env 키(사용자가 손으로 넣은 것) 는 항상
# 보존한다 — read → in-place update → write 패턴.
# ---------------------------------------------------------------------------

DOGRAH_FIELDS = ("base_url", "mcp_url", "ui_url", "api_key")
DOGRAH_ENV_KEY = {
    "base_url": "DOGRAH_BASE_URL",
    "mcp_url": "DOGRAH_MCP_URL",
    "ui_url": "DOGRAH_UI_URL",
    "api_key": "DOGRAH_API_KEY",
}
DB_FIELDS = ("driver", "host", "port", "user", "password", "database")
DB_DRIVERS = ("mysql", "postgres", "sqlite", "mssql", "oracle")
DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SCHEME_BY_DRIVER = {
    "mysql": "mysql",
    "postgres": "postgresql",
    "sqlite": "sqlite",
    "mssql": "mssql",
    "oracle": "oracle",
}


def _config_yaml_path() -> Path:
    return PROJECT_ROOT / ".symphony" / "config.yaml"


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _parse_config_yaml(text: str) -> dict[str, Any]:
    """Settings 전용 단순 YAML 파서.

    지원: 2-space indent, scalar value, 1-depth 와 2-depth nested map.
    config.yaml 은 이 서버 본인이 emit 하므로 형식이 고정되어 있다.
    """
    data: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, data)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(0, data)]
        kv = re.match(r"^\s*([A-Za-z0-9_\-]+)\s*:\s*(.*)$", raw)
        if not kv:
            continue
        key = kv.group(1)
        rest = kv.group(2).strip()
        parent = stack[-1][1]
        if rest == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent + 2, child))
        else:
            parent[key] = _coerce_scalar(rest)
    return data


def _emit_config_yaml(config: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Symphony board-viewer settings. UI-managed; do not edit by hand.",
        "# Companion .env is regenerated atomically on every save.",
        "",
    ]
    dograh = config.get("dograh") or {}
    if dograh:
        lines.append("dograh:")
        for f in DOGRAH_FIELDS:
            v = dograh.get(f, "")
            lines.append(f"  {f}: {_yaml_scalar(str(v))}")
        lines.append("")
    databases = config.get("databases") or {}
    if databases:
        lines.append("databases:")
        for name in sorted(databases.keys()):
            entry = databases[name] or {}
            lines.append(f"  {name}:")
            for f in DB_FIELDS:
                v = entry.get(f, "")
                lines.append(f"    {f}: {_yaml_scalar(str(v))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def read_settings() -> dict[str, Any]:
    path = _config_yaml_path()
    if not path.exists():
        return {"dograh": _default_dograh(), "databases": {}}
    try:
        text = path.read_text(encoding="utf-8")
        parsed = _parse_config_yaml(text)
    except OSError:
        return {"dograh": _default_dograh(), "databases": {}}
    raw_dograh = parsed.get("dograh")
    dograh: dict[str, Any] = raw_dograh if isinstance(raw_dograh, dict) else {}
    raw_db = parsed.get("databases")
    databases_raw: dict[str, Any] = raw_db if isinstance(raw_db, dict) else {}
    databases: dict[str, dict[str, Any]] = {}
    for name, entry in databases_raw.items():
        if isinstance(entry, dict):
            databases[name] = {f: str(entry.get(f, "")) for f in DB_FIELDS}
    return {
        "dograh": {f: str(dograh.get(f, "")) for f in DOGRAH_FIELDS},
        "databases": databases,
    }


def _default_dograh() -> dict[str, str]:
    return {
        "base_url": "http://localhost:8000",
        "mcp_url": "http://localhost:8000/api/v1/mcp/",
        "ui_url": "http://localhost:3010",
        "api_key": "",
    }


def _validate_url(value: str, *, allow_empty: bool = False) -> str | None:
    if not value:
        return None if allow_empty else "must be non-empty"
    if not re.match(r"^https?://", value):
        return "must start with http:// or https://"
    return None


def _validate_dograh(payload: Any) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "dograh must be an object"
    out: dict[str, str] = {}
    for f in DOGRAH_FIELDS:
        v = payload.get(f, "")
        if not isinstance(v, str):
            return None, f"dograh.{f} must be string"
        v = v.strip()
        if f in ("base_url", "mcp_url", "ui_url"):
            err = _validate_url(v, allow_empty=False)
            if err:
                return None, f"dograh.{f}: {err}"
        out[f] = v
    return out, None


def _validate_databases(payload: Any) -> tuple[dict[str, dict[str, str]] | None, str | None]:
    if payload is None:
        return {}, None
    if not isinstance(payload, dict):
        return None, "databases must be an object"
    out: dict[str, dict[str, str]] = {}
    for name, entry in payload.items():
        if not isinstance(name, str) or not DB_NAME_RE.match(name):
            return None, f"databases name '{name}': must match [a-z][a-z0-9_]*"
        if not isinstance(entry, dict):
            return None, f"databases.{name} must be an object"
        driver = str(entry.get("driver", "")).strip().lower()
        if driver not in DB_DRIVERS:
            return None, f"databases.{name}.driver: must be one of {list(DB_DRIVERS)}"
        host = str(entry.get("host", "")).strip()
        port_raw = entry.get("port", "")
        port = str(port_raw).strip()
        if port and not re.fullmatch(r"\d{1,5}", port):
            return None, f"databases.{name}.port: must be digits"
        user = str(entry.get("user", "")).strip()
        password = str(entry.get("password", ""))  # password may contain spaces; do not strip
        database = str(entry.get("database", "")).strip()
        if driver != "sqlite":
            if not host:
                return None, f"databases.{name}.host: required for driver {driver}"
        out[name] = {
            "driver": driver,
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
        }
    return out, None


def _url_encode(s: str) -> str:
    import urllib.parse

    return urllib.parse.quote(s, safe="")


def _db_url(entry: dict[str, str]) -> str:
    driver = entry.get("driver", "")
    scheme = SCHEME_BY_DRIVER.get(driver, driver)
    user = entry.get("user", "")
    password = entry.get("password", "")
    host = entry.get("host", "")
    port = entry.get("port", "")
    database = entry.get("database", "")
    if driver == "sqlite":
        return f"sqlite:///{database}" if database else "sqlite://"
    auth = ""
    if user:
        if password:
            auth = f"{_url_encode(user)}:{_url_encode(password)}@"
        else:
            auth = f"{_url_encode(user)}@"
    netloc = host
    if port:
        netloc = f"{host}:{port}"
    path = f"/{database}" if database else ""
    return f"{scheme}://{auth}{netloc}{path}"


def _managed_env_keys(config: dict[str, Any]) -> set[str]:
    keys = set(DOGRAH_ENV_KEY.values())
    raw_db = config.get("databases")
    databases: dict[str, Any] = raw_db if isinstance(raw_db, dict) else {}
    for name in databases.keys():
        upper = name.upper()
        keys.add(f"{upper}_URL")
        for f in ("DRIVER", "HOST", "PORT", "USER", "PASSWORD", "DATABASE"):
            keys.add(f"{upper}_{f}")
    return keys


def _config_to_env(config: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    raw_dograh = config.get("dograh")
    dograh: dict[str, Any] = raw_dograh if isinstance(raw_dograh, dict) else {}
    for f in DOGRAH_FIELDS:
        v = dograh.get(f, "")
        if v != "":
            env[DOGRAH_ENV_KEY[f]] = v
    raw_db = config.get("databases")
    databases: dict[str, Any] = raw_db if isinstance(raw_db, dict) else {}
    for name, entry in databases.items():
        if not isinstance(entry, dict):
            continue
        upper = name.upper()
        env[f"{upper}_DRIVER"] = entry.get("driver", "")
        env[f"{upper}_HOST"] = entry.get("host", "")
        env[f"{upper}_PORT"] = entry.get("port", "")
        env[f"{upper}_USER"] = entry.get("user", "")
        env[f"{upper}_PASSWORD"] = entry.get("password", "")
        env[f"{upper}_DATABASE"] = entry.get("database", "")
        env[f"{upper}_URL"] = _db_url(entry)
    return env


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    # Quote if value contains whitespace, '#', or shell-significant chars.
    if re.search(r"[\s\"'\\$`#=]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _read_env_lines() -> list[str]:
    path = _env_path()
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _merge_env_lines(existing: list[str], new_env: dict[str, str], managed_keys: set[str]) -> list[str]:
    """Replace managed-key lines in-place; drop managed keys absent from new_env;
    preserve every other line (comments, blanks, third-party keys).
    Missing managed keys with non-empty values are appended at the end.
    """
    out: list[str] = []
    seen: set[str] = set()
    assignment_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in existing:
        m = assignment_re.match(line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if key in managed_keys:
            if key in new_env and new_env[key] != "":
                out.append(f"{key}={_quote_env_value(new_env[key])}")
                seen.add(key)
            # else: drop the line (key emptied/removed via UI)
        else:
            out.append(line)
    appended: list[str] = []
    for key in sorted(new_env.keys()):
        if key in seen:
            continue
        if new_env[key] == "":
            continue
        appended.append(f"{key}={_quote_env_value(new_env[key])}")
    if appended:
        if out and out[-1].strip() != "":
            out.append("")
        out.append("# Managed by Symphony board-viewer Settings — values mirror .symphony/config.yaml.")
        out.extend(appended)
    return out


def write_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Write config.yaml first (canonical), then merge .env. Both atomic."""
    yaml_text = _emit_config_yaml(config)
    _atomic_write(_config_yaml_path(), yaml_text)
    existing_lines = _read_env_lines()
    new_env = _config_to_env(config)
    managed_keys = _managed_env_keys(config)
    merged = _merge_env_lines(existing_lines, new_env, managed_keys)
    env_text = "\n".join(merged).rstrip() + "\n"
    _atomic_write(_env_path(), env_text)
    return {
        "config_path": str(_config_yaml_path()),
        "env_path": str(_env_path()),
        "managed_env_keys": sorted(managed_keys),
        "config": config,
    }


# ---------------------------------------------------------------------------
# Symphony API 호출 — GET은 자유, POST는 화이트리스트로만 통과
# ---------------------------------------------------------------------------


def _symphony_request(method: str, path: str) -> tuple[int, bytes, str]:
    """Symphony API 단일 호출. 반환: (status, body_bytes, content_type).

    네트워크 실패는 (599, b'{"error":...}', 'application/json') 으로 매핑.
    """
    url = f"{SYMPHONY_BASE.rstrip('/')}{path}"
    try:
        req = urllib.request.Request(url, method=method)
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


def symphony_get(path: str) -> tuple[int, bytes, str]:
    return _symphony_request("GET", path)


def symphony_post(path: str) -> tuple[int, bytes, str]:
    return _symphony_request("POST", path)


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
        agent_kind = _parse_agent_kind(fm)
        done_agent_kind = _parse_done_agent_kind(fm, str(state), agent_kind)
        tickets.append(
            {
                "id": ticket_id,
                "identifier": fm.get("identifier") or ticket_id,
                "title": title,
                "state": state,
                "priority": priority,
                "labels": labels,
                "agent_kind": agent_kind,
                "done_agent_kind": done_agent_kind,
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

    # ---- POST ----
    # 화이트리스트 외 경로는 405.
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/workflow/branch-policy":
            if not self._local_origin_allowed():
                self._drain_request_body()
                self._send_json(403, {"error": "forbidden_origin"})
                return
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid_json"})
                return
            available = set(list_git_branches().get("branches") or [])
            base = _policy_value(payload.get("feature_base_branch"))
            target = _policy_value(payload.get("merge_target_branch"))
            for label, branch in (
                ("feature_base_branch", base),
                ("merge_target_branch", target),
            ):
                if branch and branch not in available:
                    self._send_json(
                        400,
                        {
                            "error": "unknown_branch",
                            "field": label,
                            "branch": branch,
                        },
                    )
                    return
            try:
                policy = update_workflow_branch_policy(
                    feature_base_branch=base,
                    merge_target_branch=target,
                )
            except Exception as exc:
                self._send_json(
                    500,
                    {"error": "workflow_update_failed", "message": str(exc)},
                )
                return
            self._send_json(200, {"ok": True, "policy": policy})
            return

        if path == "/api/settings":
            if not self._local_origin_allowed():
                self._drain_request_body()
                self._send_json(403, {"error": "forbidden_origin"})
                return
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid_json"})
                return
            dograh, err = _validate_dograh(payload.get("dograh"))
            if err:
                self._send_json(400, {"error": "validation", "message": err})
                return
            databases, err = _validate_databases(payload.get("databases"))
            if err:
                self._send_json(400, {"error": "validation", "message": err})
                return
            try:
                result = write_settings({"dograh": dograh, "databases": databases})
            except Exception as exc:
                self._send_json(500, {"error": "write_failed", "message": str(exc)})
                return
            self._send_json(200, {"ok": True, **result})
            return

        # refresh — payload 없는 단순 트리거
        if path == "/api/symphony/refresh":
            # 클라이언트 body는 무시(stdlib http 서버는 close-notify까지 안 해도 됨).
            # 단, Content-Length가 와 있으면 소비해서 keepalive 흐름을 깨끗하게.
            self._drain_request_body()
            status, body, ct = symphony_post("/api/v1/refresh")
            self._send(status, body, ct)
            return

        # pause / resume — {identifier} 화이트리스트
        m = re.fullmatch(
            r"/api/symphony/([A-Za-z0-9_\-]+)/(pause|resume)", path
        )
        if m:
            self._drain_request_body()
            identifier, action = m.group(1), m.group(2)
            status, body, ct = symphony_post(
                f"/api/v1/{identifier}/{action}"
            )
            self._send(status, body, ct)
            return

        self._send_json(
            405,
            {
                "error": "method_not_allowed",
                "message": "POST only allowed on /api/workflow/branch-policy, /api/settings, /api/symphony/refresh, /api/symphony/<id>/(pause|resume)",
            },
        )

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _local_origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return True
        return origin in {
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
            f"http://[::1]:{PORT}",
        }

    def _drain_request_body(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 0:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    # ---- GET ----
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        # API: Symphony state 프록시
        if path == "/api/symphony/state":
            status, body, ct = symphony_get("/api/v1/state")
            self._send(status, body, ct)
            return

        # API: Settings (current config.yaml + computed .env preview)
        if path == "/api/settings":
            try:
                config = read_settings()
            except Exception as exc:
                self._send_json(500, {"error": "read_failed", "message": str(exc)})
                return
            env_preview = _config_to_env(config)
            self._send_json(
                200,
                {
                    "config": config,
                    "env_preview": env_preview,
                    "config_path": str(_config_yaml_path()),
                    "env_path": str(_env_path()),
                    "drivers": list(DB_DRIVERS),
                },
            )
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

        if path == "/api/git/branches":
            self._send_json(200, list_git_branches())
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
        description="Symphony Board Viewer — kanban HUD + safe workflow controls"
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
    parser.add_argument(
        "--workflow",
        metavar="FILE",
        default=None,
        help="WORKFLOW.md 경로 (생략 시: $BOARD_VIEWER_WORKFLOW → $PWD/WORKFLOW.md)",
    )
    parser.add_argument(
        "--project-root",
        metavar="DIR",
        default=None,
        help="프로젝트 root 경로 (.symphony/config.yaml 과 .env 기준; 생략 시 --kanban 의 부모, fallback $PWD)",
    )
    args = parser.parse_args()

    # 전역 set — BoardHandler 내부에서 참조하는 모듈 글로벌을 갱신
    global KANBAN_DIR, PORT, SYMPHONY_BASE, WORKFLOW_PATH, PROJECT_ROOT
    KANBAN_DIR = _resolve_kanban_dir(args.kanban)
    WORKFLOW_PATH = _resolve_workflow_path(args.workflow)
    PROJECT_ROOT = _resolve_project_root(args.project_root, KANBAN_DIR)
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
  workflow  : {WORKFLOW_PATH or "(not found)"}
  project   : {PROJECT_ROOT}
  config    : {_config_yaml_path()}{" (missing)" if not _config_yaml_path().exists() else ""}
  env       : {_env_path()}{" (missing)" if not _env_path().exists() else ""}
  symphony  : {SYMPHONY_BASE}
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
