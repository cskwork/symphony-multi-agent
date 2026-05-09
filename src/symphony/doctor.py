"""`symphony doctor` — preflight checks for a WORKFLOW.md.

Verifies that the most common first-run failures are absent before the user
launches `symphony tui` or the headless service:

- Port for the JSON API is bindable (catches the EADDRINUSE that crashed the
  start path with a raw OSError).
- The agent CLI matching `agent.kind` is on `$PATH`.
- `hooks.after_create` is not the shipped placeholder `my-org/my-repo` URL.
- `workspace.root` exists and is writable.
- File-tracker `tracker.board_root` exists; Linear-tracker `api_key` resolves.

Exit codes:
    0  — all checks passed (warnings allowed)
    1  — at least one check failed
    2  — could not load WORKFLOW.md
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, Literal

from ._shell import _is_wsl_launcher, resolve_bash
from .errors import SymphonyError
from .workflow import (
    ServiceConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str


def _bind_port(host: str, port: int) -> CheckResult:
    name = f"server.port={port}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind((host, port))
    except OSError as exc:
        return CheckResult(name, "fail", f"cannot bind {host}:{port} — {exc}")
    finally:
        sock.close()
    return CheckResult(name, "pass", f"{host}:{port} is free")


def check_port(cfg: ServiceConfig, host: str = "127.0.0.1") -> CheckResult:
    if cfg.server.port is None:
        return CheckResult("server.port", "pass", "no HTTP API configured (server.port unset)")
    return _bind_port(host, cfg.server.port)


def check_agent_cli(cfg: ServiceConfig) -> CheckResult:
    kind = cfg.agent.kind
    if kind == "codex":
        command = cfg.codex.command
    elif kind == "claude":
        command = cfg.claude.command
    elif kind == "gemini":
        command = cfg.gemini.command
    elif kind == "pi":
        command = cfg.pi.command
    else:
        return CheckResult(f"agent.kind={kind}", "fail", f"unsupported agent kind {kind!r}")

    name = f"agent.kind={kind}"
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return CheckResult(name, "fail", f"command not parseable: {exc}")
    if not argv:
        return CheckResult(name, "fail", f"{kind}.command is empty")

    binary = argv[0]
    # `python -m symphony.mock_codex` style — find the interpreter, not the module.
    located = shutil.which(binary)
    if located is None:
        return CheckResult(name, "fail", f"{binary!r} not on $PATH (configured: {command!r})")
    return CheckResult(name, "pass", f"{binary} → {located}")


_PLACEHOLDER_TOKENS = ("my-org/my-repo", "my-org:my-repo")


def check_after_create_hook(cfg: ServiceConfig) -> CheckResult:
    hook = cfg.hooks.after_create or ""
    if not hook.strip():
        return CheckResult("hooks.after_create", "pass", "empty (skipped at runtime)")
    for token in _PLACEHOLDER_TOKENS:
        if token in hook:
            return CheckResult(
                "hooks.after_create",
                "fail",
                f"contains placeholder {token!r} — every dispatch will fail with rc=128. "
                "Replace with a real clone target or `: noop`.",
            )
    return CheckResult("hooks.after_create", "pass", "looks customized")


def check_workspace_root(cfg: ServiceConfig) -> CheckResult:
    root = cfg.workspace_root
    name = f"workspace.root={root}"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(name, "fail", f"cannot create {root} — {exc}")
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".symphony-doctor-", delete=True):
            pass
    except OSError as exc:
        return CheckResult(name, "fail", f"not writable — {exc}")
    return CheckResult(name, "pass", f"{root} exists and is writable")


def check_tracker(cfg: ServiceConfig) -> CheckResult:
    tracker = cfg.tracker
    if tracker.kind == "file":
        root = tracker.board_root
        if root is None:
            return CheckResult("tracker.board_root", "fail", "file tracker has no board_root")
        if not root.exists():
            return CheckResult(
                "tracker.board_root",
                "fail",
                f"{root} does not exist — run `symphony board init {root}`",
            )
        ticket_count = sum(1 for _ in root.glob("*.md"))
        return CheckResult(
            "tracker.board_root",
            "pass",
            f"{root} ({ticket_count} ticket{'s' if ticket_count != 1 else ''})",
        )
    if tracker.kind == "linear":
        if not tracker.api_key:
            return CheckResult(
                "tracker.api_key",
                "fail",
                "linear tracker requires api_key (set $LINEAR_API_KEY or hardcode)",
            )
        if tracker.api_key.startswith("$"):
            env_name = tracker.api_key.lstrip("$")
            if not os.environ.get(env_name):
                return CheckResult(
                    "tracker.api_key",
                    "fail",
                    f"api_key references ${env_name} but the env var is unset",
                )
        return CheckResult("tracker.api_key", "pass", "api_key present")
    return CheckResult(f"tracker.kind={tracker.kind}", "warn", "unknown tracker kind")


def check_shell() -> CheckResult:
    """Hooks and backend subprocesses spawn via ``bash -lc``. On Windows we
    must avoid the WSL launcher (``C:\\Windows\\System32\\bash.exe``) — see
    ``_shell.resolve_bash``. On macOS/Linux we still verify ``bash`` is
    actually on ``$PATH`` so minimal containers and nix-shells fail loudly
    here rather than silently at first dispatch."""
    bash = resolve_bash()
    # If ``bash`` is a bare name (e.g. "bash" or "wsl"), resolve via PATH so
    # WSL-launcher detection sees the actual binary.
    resolved = bash if os.path.isfile(bash) else (shutil.which(bash) or bash)

    if sys.platform == "win32" and _is_wsl_launcher(resolved):
        return CheckResult(
            "shell.bash",
            "fail",
            f"{resolved} is the WSL launcher — install Git for Windows "
            "or set $SYMPHONY_BASH to a Git Bash binary",
        )

    if not (os.path.isfile(bash) or shutil.which(bash)):
        if sys.platform == "win32":
            return CheckResult(
                "shell.bash",
                "fail",
                "no usable bash found — install Git for Windows or set $SYMPHONY_BASH",
            )
        return CheckResult(
            "shell.bash",
            "fail",
            f"{bash!r} not found on $PATH — install bash or set $SYMPHONY_BASH",
        )

    return CheckResult("shell.bash", "pass", bash)


def run_checks(cfg: ServiceConfig, host: str = "127.0.0.1") -> list[CheckResult]:
    return [
        check_port(cfg, host=host),
        check_shell(),
        check_agent_cli(cfg),
        check_after_create_hook(cfg),
        check_workspace_root(cfg),
        check_tracker(cfg),
    ]


_STATUS_ICON: dict[Status, str] = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
_STATUS_COLOR: dict[Status, str] = {
    "pass": "\033[32m",  # green
    "warn": "\033[33m",  # yellow
    "fail": "\033[31m",  # red
}
_RESET = "\033[0m"


def format_results(results: Iterable[CheckResult], *, color: bool = False) -> str:
    lines: list[str] = []
    for r in results:
        icon = _STATUS_ICON[r.status]
        if color:
            icon = f"{_STATUS_COLOR[r.status]}{icon}{_RESET}"
        lines.append(f"{icon}  {r.name:<28}  {r.message}")
    return "\n".join(lines)


def _exit_code(results: Iterable[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="symphony doctor",
        description="Preflight checks for WORKFLOW.md before launching symphony.",
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to test the JSON API port against (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI color even when stdout is a tty",
    )
    args = parser.parse_args(argv)

    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.exists():
        print(f"FAIL  workflow file not found: {workflow_path}", file=sys.stderr)
        return 2

    try:
        cfg = build_service_config(load_workflow(workflow_path))
    except SymphonyError as exc:
        print(f"FAIL  workflow load failed: {exc}", file=sys.stderr)
        return 2

    color = (not args.no_color) and sys.stdout.isatty()
    results = run_checks(cfg, host=args.host)
    print(format_results(results, color=color))
    return _exit_code(results)
