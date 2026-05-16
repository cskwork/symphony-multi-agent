"""Persistent run-state helpers for `symphony service`."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony import cli
from symphony import service as service_module
from symphony.service import (
    ServiceRecord,
    ServiceLockError,
    acquire_service_lock,
    build_orchestrator_command,
    clear_record,
    is_process_running,
    load_record,
    main as service_main,
    record_path_for,
    save_record,
    service_status,
)


def _workflow(tmp_path: Path) -> Path:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8")
    return workflow


def _record(workflow_path: Path, *, pid: int | None = 1234, port: int = 9999) -> ServiceRecord:
    workflow_dir = workflow_path.parent
    return ServiceRecord(
        workflow_path=workflow_path.resolve(),
        workflow_dir=workflow_dir.resolve(),
        host="127.0.0.1",
        port=port,
        viewer_port=port + 1,
        orchestrator_pid=pid,
        viewer_pid=pid + 1 if pid is not None else None,
        log_path=workflow_dir / "log" / "symphony.log",
        viewer_log_path=workflow_dir / "log" / "symphony-viewer.log",
        started_at="2026-05-16T00:00:00Z",
        orchestrator_command=["symphony", str(workflow_path), "--port", str(port)],
        viewer_command=["symphony", "tui", str(workflow_path)],
    )


def test_record_path_is_inside_workflow_run_directory(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    path = record_path_for(workflow)

    assert path.parent == tmp_path / ".symphony" / "run"
    assert path.name.endswith(".json")
    assert all(ch.isalnum() or ch in "._-" for ch in path.name)


def test_save_and_load_record_round_trip(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    record = _record(workflow)

    save_record(record)

    loaded = load_record(workflow)
    assert loaded == record


def test_stale_record_is_reported_stopped(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))

    status = service_status(workflow, port=9999, is_running=lambda pid: False)

    assert status.state == "stopped"
    assert status.record is not None
    assert status.requested_port == 9999
    assert status.recorded_port == 9999


def test_process_running_returns_false_for_invalid_pids() -> None:
    assert is_process_running(None) is False
    assert is_process_running(0) is False
    assert is_process_running(-1) is False


def test_live_record_is_running_even_when_requested_port_differs(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, port=9999))

    status = service_status(workflow, port=10000, is_running=lambda pid: pid == 1234)

    assert status.state == "running"
    assert status.record is not None
    assert status.requested_port == 10000
    assert status.recorded_port == 9999


def test_clear_record_removes_saved_state(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow))

    assert load_record(workflow) is not None
    clear_record(workflow)

    assert load_record(workflow) is None
    assert not record_path_for(workflow).exists()


def test_build_orchestrator_command_uses_python_module(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    command = build_orchestrator_command(workflow, host="127.0.0.1", port=9999)

    assert command[1:3] == ["-m", "symphony.cli"]
    assert str(workflow.resolve()) in command
    assert "--port" in command
    assert "--host" in command


def test_service_status_cli_reports_stopped(tmp_path: Path, capsys) -> None:
    workflow = _workflow(tmp_path)

    rc = service_main(["status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out


def test_top_level_cli_routes_service_status(tmp_path: Path, capsys) -> None:
    workflow = _workflow(tmp_path)

    rc = cli.main(["service", "status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out


def test_service_stop_keeps_record_when_process_survives(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: True)
    monkeypatch.setattr(service_module, "terminate_process", lambda pid: True)
    monkeypatch.setattr(service_module, "_wait_until", lambda *args, **kwargs: False)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "record kept" in captured.err
    assert load_record(workflow) is not None


def test_service_lock_blocks_second_start_for_same_workflow(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    with acquire_service_lock(workflow):
        with pytest.raises(ServiceLockError):
            with acquire_service_lock(workflow):
                pass


def test_start_cleans_live_viewer_from_stale_record_before_doctor(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    live_pids = {1235}
    stopped: list[int | None] = []
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: pid in live_pids,
    )

    def _stop_pid(pid, *args, **kwargs):  # noqa: ANN001
        stopped.append(pid)
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *args, **kwargs: False)

    rc = service_main(["start", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert stopped == [1235]
    assert load_record(workflow) is None
    assert "doctor reported FAIL" in captured.err


def test_start_cleans_spawned_process_if_record_save_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    stopped: list[int | None] = []
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *args, **kwargs: True)
    monkeypatch.setattr(service_module, "_popen_detached", lambda *args, **kwargs: 1234)
    monkeypatch.setattr(service_module, "_wait_until", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        service_module,
        "save_record",
        lambda record: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        service_module,
        "terminate_process",
        lambda pid, *args, **kwargs: stopped.append(pid) or True,
    )

    rc = service_main(["start", "--skip-doctor", "--no-viewer", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert stopped == [1234]
    assert "failed to save service record" in captured.err


def test_restart_aborts_when_stop_fails(tmp_path: Path, monkeypatch) -> None:
    workflow = _workflow(tmp_path)
    starts: list[object] = []
    monkeypatch.setattr(service_module, "_stop", lambda args: 1)
    monkeypatch.setattr(
        service_module,
        "_start",
        lambda args: starts.append(args) or 0,
    )

    rc = service_main(["restart", str(workflow)])

    assert rc == 1
    assert starts == []
