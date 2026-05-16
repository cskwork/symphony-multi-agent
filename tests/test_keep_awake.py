"""keep_awake module + WORKFLOW.md `system.keep_awake` parsing."""

from __future__ import annotations

import subprocess

import pytest

from symphony.keep_awake import KeepAwake
from symphony.workflow import build_service_config, parse_workflow_text


class _FakePopen:
    """Stand-in for subprocess.Popen used by KeepAwake tests."""

    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 99999
        self._alive = alive
        self.terminated = False
        self.killed = False
        self.wait_calls: list[float | None] = []

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return 0


def test_keep_awake_noop_on_non_darwin(monkeypatch):
    monkeypatch.setattr("symphony.keep_awake.sys.platform", "linux")
    monkeypatch.setattr(
        "symphony.keep_awake.subprocess.Popen",
        lambda *a, **k: pytest.fail("Popen should not run on linux"),
    )

    awake = KeepAwake()
    assert awake.start() is False
    assert awake.active is False
    awake.stop()  # idempotent — must not raise


def test_keep_awake_noop_when_caffeinate_missing(monkeypatch):
    monkeypatch.setattr("symphony.keep_awake.sys.platform", "darwin")
    monkeypatch.setattr("symphony.keep_awake.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "symphony.keep_awake.subprocess.Popen",
        lambda *a, **k: pytest.fail("Popen should not run when caffeinate is missing"),
    )

    awake = KeepAwake()
    assert awake.start() is False
    assert awake.active is False


def test_keep_awake_spawns_caffeinate_on_darwin(monkeypatch):
    monkeypatch.setattr("symphony.keep_awake.sys.platform", "darwin")
    monkeypatch.setattr(
        "symphony.keep_awake.shutil.which", lambda name: "/usr/bin/caffeinate"
    )
    recorded: dict[str, object] = {}
    fake = _FakePopen()

    def fake_popen(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        return fake

    monkeypatch.setattr("symphony.keep_awake.subprocess.Popen", fake_popen)

    awake = KeepAwake(watch_pid=4242)
    assert awake.start() is True
    assert awake.active is True
    cmd = recorded["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "/usr/bin/caffeinate"
    assert "-d" in cmd and "-i" in cmd
    assert "-w" in cmd
    assert cmd[cmd.index("-w") + 1] == "4242"

    awake.stop()
    assert fake.terminated is True
    assert awake.active is False

    # stop() is idempotent — second call is a no-op.
    awake.stop()


def test_keep_awake_force_kills_on_terminate_timeout(monkeypatch):
    monkeypatch.setattr("symphony.keep_awake.sys.platform", "darwin")
    monkeypatch.setattr(
        "symphony.keep_awake.shutil.which", lambda name: "/usr/bin/caffeinate"
    )

    class _SlowPopen(_FakePopen):
        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            raise subprocess.TimeoutExpired(cmd="caffeinate", timeout=timeout)

    slow = _SlowPopen()
    monkeypatch.setattr(
        "symphony.keep_awake.subprocess.Popen", lambda *a, **k: slow
    )

    awake = KeepAwake()
    awake.start()
    awake.stop()
    assert slow.terminated is True
    assert slow.killed is True


def test_keep_awake_start_swallows_oserror(monkeypatch):
    monkeypatch.setattr("symphony.keep_awake.sys.platform", "darwin")
    monkeypatch.setattr(
        "symphony.keep_awake.shutil.which", lambda name: "/usr/bin/caffeinate"
    )

    def boom(*a, **k):
        raise OSError("no fork for you")

    monkeypatch.setattr("symphony.keep_awake.subprocess.Popen", boom)

    awake = KeepAwake()
    assert awake.start() is False
    assert awake.active is False


# ---------- WORKFLOW.md system.keep_awake parsing -----------------------


def test_workflow_system_keep_awake_default_true(tmp_path):
    text = (
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "agent:\n"
        "  kind: claude\n"
        "---\n"
        "prompt body\n"
    )
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(text)
    wf = parse_workflow_text(text, workflow_path)
    cfg = build_service_config(wf)
    assert cfg.system.keep_awake is True


def test_workflow_system_keep_awake_explicit_false(tmp_path):
    text = (
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "agent:\n"
        "  kind: claude\n"
        "system:\n"
        "  keep_awake: false\n"
        "---\n"
        "prompt body\n"
    )
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(text)
    wf = parse_workflow_text(text, workflow_path)
    cfg = build_service_config(wf)
    assert cfg.system.keep_awake is False


def test_workflow_system_keep_awake_rejects_non_bool(tmp_path):
    text = (
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "agent:\n"
        "  kind: claude\n"
        "system:\n"
        "  keep_awake: 1\n"
        "---\n"
        "prompt body\n"
    )
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(text)
    wf = parse_workflow_text(text, workflow_path)
    with pytest.raises(Exception, match="system.keep_awake"):
        build_service_config(wf)
