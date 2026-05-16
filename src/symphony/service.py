"""Run-state persistence for the built-in `symphony service` command.

This module intentionally avoids launching or stopping processes.  It is the
small, unit-testable layer that records what a service command started and
answers whether an existing record still points at live processes.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from .errors import SymphonyError
from .workflow import (
    ServerConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


ProcessRunningPredicate = Callable[[int | None], bool]
ServiceState = Literal["running", "stopped"]
DEFAULT_SERVICE_PORT = 9999
DEFAULT_VIEWER_PORT = 8765


class ServiceLockError(RuntimeError):
    """Raised when another service operation already owns the workflow lock."""


@dataclass(frozen=True)
class ServiceRecord:
    workflow_path: Path
    workflow_dir: Path
    host: str
    port: int
    viewer_port: int | None
    orchestrator_pid: int | None
    viewer_pid: int | None
    log_path: Path
    viewer_log_path: Path | None
    started_at: str
    orchestrator_command: list[str] = field(default_factory=list)
    viewer_command: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    record: ServiceRecord | None
    requested_port: int | None = None
    recorded_port: int | None = None


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def record_path_for(workflow_path: str | Path) -> Path:
    """Return the deterministic JSON run-state path for a workflow file."""
    resolved = _resolved(workflow_path)
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    return resolved.parent / ".symphony" / "run" / f"{digest}.json"


def lock_path_for(workflow_path: str | Path) -> Path:
    return record_path_for(workflow_path).with_suffix(".lock")


@contextlib.contextmanager
def acquire_service_lock(workflow_path: str | Path):
    """Acquire a per-workflow lock using atomic file creation."""
    path = lock_path_for(workflow_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ServiceLockError(f"service operation already in progress: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        yield path
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _path_or_none(value: Any) -> Path | None:
    if value is None:
        return None
    return Path(str(value))


def _record_to_json(record: ServiceRecord) -> dict[str, Any]:
    return {
        "workflow_path": str(record.workflow_path),
        "workflow_dir": str(record.workflow_dir),
        "host": record.host,
        "port": record.port,
        "viewer_port": record.viewer_port,
        "orchestrator_pid": record.orchestrator_pid,
        "viewer_pid": record.viewer_pid,
        "log_path": str(record.log_path),
        "viewer_log_path": str(record.viewer_log_path) if record.viewer_log_path else None,
        "started_at": record.started_at,
        "orchestrator_command": list(record.orchestrator_command),
        "viewer_command": list(record.viewer_command),
    }


def _record_from_json(data: dict[str, Any]) -> ServiceRecord:
    return ServiceRecord(
        workflow_path=Path(str(data["workflow_path"])),
        workflow_dir=Path(str(data["workflow_dir"])),
        host=str(data["host"]),
        port=int(data["port"]),
        viewer_port=int(data["viewer_port"]) if data.get("viewer_port") is not None else None,
        orchestrator_pid=(
            int(data["orchestrator_pid"])
            if data.get("orchestrator_pid") is not None
            else None
        ),
        viewer_pid=int(data["viewer_pid"]) if data.get("viewer_pid") is not None else None,
        log_path=Path(str(data["log_path"])),
        viewer_log_path=_path_or_none(data.get("viewer_log_path")),
        started_at=str(data["started_at"]),
        orchestrator_command=[str(part) for part in data.get("orchestrator_command", [])],
        viewer_command=[str(part) for part in data.get("viewer_command", [])],
    )


def load_record(workflow_path: str | Path) -> ServiceRecord | None:
    path = record_path_for(workflow_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return _record_from_json(data)
    except (TypeError, KeyError, ValueError):
        return None


def save_record(record: ServiceRecord) -> Path:
    path = record_path_for(record.workflow_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_record_to_json(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def clear_record(workflow_path: str | Path) -> None:
    try:
        record_path_for(workflow_path).unlink()
    except FileNotFoundError:
        return


def _is_process_running_windows(pid: int) -> bool:
    # PROCESS_QUERY_LIMITED_INFORMATION keeps the handle read-only.  If that
    # right is denied, fall back to PROCESS_QUERY_INFORMATION for older hosts.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    process_query_information = 0x0400
    still_active = 259

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        handle = kernel32.OpenProcess(process_query_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def is_process_running(pid: int | None) -> bool:
    """Return whether pid appears live, without raising for stale values."""
    try:
        parsed = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if parsed <= 0:
        return False

    if sys.platform == "win32":
        try:
            return _is_process_running_windows(parsed)
        except OSError:
            return False

    try:
        os.kill(parsed, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def service_status(
    workflow_path: str | Path,
    *,
    port: int | None = None,
    is_running: ProcessRunningPredicate = is_process_running,
) -> ServiceStatus:
    """Report persisted service state for a workflow.

    The saved workflow record wins over the requested port: when the same
    workflow has a live orchestrator PID, callers should treat it as already
    running even if the operator asks for a different port.
    """
    record = load_record(workflow_path)
    if record is None:
        return ServiceStatus(
            state="stopped",
            record=None,
            requested_port=port,
            recorded_port=None,
        )

    state: ServiceState = (
        "running" if is_running(record.orchestrator_pid) else "stopped"
    )
    return ServiceStatus(
        state=state,
        record=record,
        requested_port=port,
        recorded_port=record.port,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def build_orchestrator_command(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
) -> list[str]:
    """Build the shell-free command used to launch the orchestrator service."""
    workflow = _resolved(workflow_path)
    return [
        sys.executable,
        "-m",
        "symphony.cli",
        str(workflow),
        "--host",
        host,
        "--port",
        str(port),
    ]


def board_viewer_script_for(workflow_path: str | Path) -> Path | None:
    script = _resolved(workflow_path).parent / "tools" / "board-viewer" / "server.py"
    return script if script.exists() else None


def build_viewer_command(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
    viewer_port: int,
    kanban_dir: Path | None = None,
) -> list[str] | None:
    """Build the shell-free board-viewer command when a viewer is available."""
    script = board_viewer_script_for(workflow_path)
    if script is None:
        return None
    command = [
        sys.executable,
        str(script),
        "--port",
        str(viewer_port),
        "--symphony",
        f"http://{host}:{port}",
    ]
    if kanban_dir is not None:
        command.extend(["--kanban", str(kanban_dir)])
    return command


def _popen_detached(command: list[str], *, cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        log_handle.close()
    return int(proc.pid)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 0.1,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def _terminate_process_windows(pid: int, *, force: bool = False) -> bool:
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def terminate_process(pid: int | None, *, force: bool = False) -> bool:
    """Best-effort process-tree termination for a service-managed PID."""
    try:
        parsed = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if parsed <= 0 or not is_process_running(parsed):
        return False
    if sys.platform == "win32":
        try:
            return _terminate_process_windows(parsed, force=force)
        except OSError:
            return False
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(parsed, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except OSError:
        try:
            os.kill(parsed, sig)
        except ProcessLookupError:
            return False
        except OSError:
            return False
    return True


def _run_doctor_or_print(cfg: Any, *, host: str, port: int) -> bool:
    from dataclasses import replace

    from .doctor import format_results, run_checks

    checked_cfg = replace(cfg, server=ServerConfig(port=port))
    results = run_checks(checked_cfg, host=host)
    print(format_results(results, color=False))
    return not any(result.status == "fail" for result in results)


def _load_cfg(workflow_path: Path) -> Any:
    return build_service_config(load_workflow(workflow_path))


def _resolve_port(raw_port: int | None, cfg: Any) -> int:
    if raw_port is not None:
        return int(raw_port)
    if cfg.server.port is not None:
        return int(cfg.server.port)
    return DEFAULT_SERVICE_PORT


def _start(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    if not workflow.exists():
        print(f"FAIL workflow file not found: {workflow}", file=sys.stderr)
        return 2
    try:
        cfg = _load_cfg(workflow)
    except SymphonyError as exc:
        print(f"FAIL workflow load failed: {exc}", file=sys.stderr)
        return 2

    try:
        with acquire_service_lock(workflow):
            return _start_locked(args, workflow=workflow, cfg=cfg)
    except ServiceLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _start_locked(args: argparse.Namespace, *, workflow: Path, cfg: Any) -> int:
    port = _resolve_port(args.port, cfg)
    current = service_status(workflow, port=port)
    if current.state == "running" and current.record is not None:
        if args.replace:
            stop_rc = _stop(
                argparse.Namespace(
                    workflow=args.workflow,
                    timeout=10.0,
                    force=True,
                )
            )
            if stop_rc != 0:
                return stop_rc
        else:
            print(
                "already running "
                f"pid={current.record.orchestrator_pid} "
                f"port={current.record.port} "
                f"workflow={current.record.workflow_path}"
            )
            if current.record.port != port:
                print(
                    f"requested port {port} ignored; this workflow is already "
                    f"managed on port {current.record.port}"
                )
            return 0
    elif current.record is not None:
        stop_rc = _stop(
            argparse.Namespace(
                workflow=args.workflow,
                timeout=2.0,
                force=True,
            )
        )
        if stop_rc != 0:
            return stop_rc

    if not args.skip_doctor and not _run_doctor_or_print(cfg, host=args.host, port=port):
        print("service start aborted: doctor reported FAIL", file=sys.stderr)
        return 1

    workflow_dir = workflow.parent
    log_path = workflow_dir / "log" / "symphony.log"
    viewer_log_path = workflow_dir / "log" / "board-viewer.log"
    orchestrator_command = build_orchestrator_command(
        workflow,
        host=args.host,
        port=port,
    )
    orchestrator_pid: int | None = None
    viewer_pid: int | None = None
    try:
        orchestrator_pid = _popen_detached(
            orchestrator_command,
            cwd=workflow_dir,
            log_path=log_path,
        )
        if not _wait_until(lambda: is_process_running(orchestrator_pid), timeout_s=2.0):
            print(
                f"service start failed: orchestrator exited early; see {log_path}",
                file=sys.stderr,
            )
            return 1

        viewer_port = None if args.no_viewer else int(args.viewer_port)
        viewer_command: list[str] = []
        if viewer_port is not None:
            built_viewer = build_viewer_command(
                workflow,
                host=args.host,
                port=port,
                viewer_port=viewer_port,
                kanban_dir=cfg.tracker.board_root,
            )
            if built_viewer is not None:
                viewer_command = built_viewer
                viewer_pid = _popen_detached(
                    viewer_command,
                    cwd=workflow_dir,
                    log_path=viewer_log_path,
                )
                if not _wait_until(lambda: is_process_running(viewer_pid), timeout_s=1.0):
                    print(
                        f"warning: board viewer exited early; see {viewer_log_path}",
                        file=sys.stderr,
                    )
                    viewer_pid = None
    except OSError as exc:
        if viewer_pid is not None:
            terminate_process(viewer_pid, force=True)
        if orchestrator_pid is not None:
            terminate_process(orchestrator_pid, force=True)
        print(f"service start failed: {exc}", file=sys.stderr)
        return 1

    record = ServiceRecord(
        workflow_path=workflow.resolve(),
        workflow_dir=workflow_dir.resolve(),
        host=args.host,
        port=port,
        viewer_port=viewer_port if viewer_pid is not None else None,
        orchestrator_pid=orchestrator_pid,
        viewer_pid=viewer_pid,
        log_path=log_path.resolve(),
        viewer_log_path=viewer_log_path.resolve() if viewer_pid is not None else None,
        started_at=_utc_now(),
        orchestrator_command=orchestrator_command,
        viewer_command=viewer_command,
    )
    try:
        save_record(record)
    except Exception as exc:
        if viewer_pid is not None:
            terminate_process(viewer_pid, force=True)
        if orchestrator_pid is not None:
            terminate_process(orchestrator_pid, force=True)
        print(f"failed to save service record: {exc}", file=sys.stderr)
        return 1

    print(
        f"started symphony service pid={orchestrator_pid} "
        f"url=http://{args.host}:{port}/"
    )
    if viewer_pid is not None and viewer_port is not None:
        print(
            f"started board viewer pid={viewer_pid} "
            f"url=http://{args.host}:{viewer_port}/"
        )
    return 0


def _stop(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    record = load_record(workflow)
    if record is None:
        print(f"stopped workflow={workflow} (no service record)")
        return 0

    all_stopped = True
    for label, pid in (
        ("viewer", record.viewer_pid),
        ("orchestrator", record.orchestrator_pid),
    ):
        if not is_process_running(pid):
            continue
        terminate_process(pid)
        stopped = _wait_until(
            lambda pid=pid: not is_process_running(pid),
            timeout_s=float(args.timeout),
        )
        if not stopped and args.force and pid is not None:
            terminate_process(pid, force=True)
            stopped = _wait_until(
                lambda pid=pid: not is_process_running(pid),
                timeout_s=2.0,
            )
        if not stopped:
            all_stopped = False
            print(f"warning: {label} pid={pid} is still running", file=sys.stderr)

    if not all_stopped:
        print(
            f"service record kept because workflow is still running: {record.workflow_path}",
            file=sys.stderr,
        )
        return 1

    clear_record(workflow)
    print(f"stopped workflow={record.workflow_path}")
    return 0


def _status(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    port = int(args.port) if args.port is not None else None
    status = service_status(workflow, port=port)
    if status.state == "stopped":
        if status.record is None:
            print(f"stopped workflow={workflow}")
        else:
            print(
                f"stopped workflow={status.record.workflow_path} "
                f"(stale pid={status.record.orchestrator_pid})"
            )
        return 0

    assert status.record is not None
    record = status.record
    print(
        f"running workflow={record.workflow_path} "
        f"pid={record.orchestrator_pid} port={record.port} "
        f"url=http://{record.host}:{record.port}/"
    )
    if record.viewer_pid is not None and record.viewer_port is not None:
        print(
            f"viewer pid={record.viewer_pid} port={record.viewer_port} "
            f"url=http://{record.host}:{record.viewer_port}/"
        )
    if port is not None and record.port != port:
        print(
            f"requested port {port}; existing service for this workflow uses "
            f"{record.port}"
        )
    return 0


def _restart(args: argparse.Namespace) -> int:
    stop_args = argparse.Namespace(
        workflow=args.workflow,
        timeout=args.timeout,
        force=args.force,
    )
    stop_rc = _stop(stop_args)
    if stop_rc != 0:
        return stop_rc
    start_args = argparse.Namespace(
        workflow=args.workflow,
        host=args.host,
        port=args.port,
        viewer_port=args.viewer_port,
        no_viewer=args.no_viewer,
        replace=False,
        skip_doctor=args.skip_doctor,
    )
    return _start(start_args)


def _logs(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    record = load_record(workflow)
    if record is None:
        print(f"no service record for {workflow}", file=sys.stderr)
        return 1
    path = record.viewer_log_path if args.viewer else record.log_path
    if path is None or not path.exists():
        print(f"log file not found: {path}", file=sys.stderr)
        return 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-int(args.lines) :]:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony service",
        description="Manage a background Symphony service for one WORKFLOW.md.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_workflow(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "workflow",
            nargs="?",
            default=None,
            help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
        )

    p_start = sub.add_parser("start", help="start orchestrator and board viewer")
    add_workflow(p_start)
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--viewer-port", type=int, default=DEFAULT_VIEWER_PORT)
    p_start.add_argument("--no-viewer", action="store_true")
    p_start.add_argument("--replace", action="store_true")
    p_start.add_argument("--skip-doctor", action="store_true")
    p_start.set_defaults(func=_start)

    p_stop = sub.add_parser("stop", help="stop a managed service")
    add_workflow(p_stop)
    p_stop.add_argument("--timeout", type=float, default=10.0)
    p_stop.add_argument("--force", action="store_true")
    p_stop.set_defaults(func=_stop)

    p_restart = sub.add_parser("restart", help="stop then start a service")
    add_workflow(p_restart)
    p_restart.add_argument("--host", default="127.0.0.1")
    p_restart.add_argument("--port", type=int, default=None)
    p_restart.add_argument("--viewer-port", type=int, default=DEFAULT_VIEWER_PORT)
    p_restart.add_argument("--no-viewer", action="store_true")
    p_restart.add_argument("--skip-doctor", action="store_true")
    p_restart.add_argument("--timeout", type=float, default=10.0)
    p_restart.add_argument("--force", action="store_true")
    p_restart.set_defaults(func=_restart)

    p_status = sub.add_parser("status", help="show managed service status")
    add_workflow(p_status)
    p_status.add_argument("--port", type=int, default=None)
    p_status.set_defaults(func=_status)

    p_logs = sub.add_parser("logs", help="print recent service logs")
    add_workflow(p_logs)
    p_logs.add_argument("--viewer", action="store_true")
    p_logs.add_argument("--lines", type=int, default=80)
    p_logs.set_defaults(func=_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
