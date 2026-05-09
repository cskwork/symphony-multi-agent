"""SPEC §17.2 — workspace manager and safety invariants."""

from __future__ import annotations

import pytest

from symphony.errors import InvalidWorkspaceCwd, SymphonyError
from symphony.workflow import HooksConfig
from symphony.workspace import WorkspaceManager, validate_agent_cwd


def _hooks(**overrides) -> HooksConfig:
    base = dict(
        after_create=None,
        before_run=None,
        after_run=None,
        before_remove=None,
        timeout_ms=2000,
    )
    base.update(overrides)
    return HooksConfig(**base)


@pytest.mark.asyncio
async def test_create_and_reuse(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks())
    ws1 = await mgr.create_or_reuse("MT-1")
    assert ws1.created_now is True
    assert ws1.path.exists()
    ws2 = await mgr.create_or_reuse("MT-1")
    assert ws2.created_now is False
    assert ws2.path == ws1.path


@pytest.mark.asyncio
async def test_sanitization(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks())
    ws = await mgr.create_or_reuse("../escape")
    expected = (tmp_path / "ws" / ".._escape").resolve()
    assert ws.path == expected


@pytest.mark.asyncio
async def test_after_create_hook_runs_only_on_creation(tmp_path):
    marker = tmp_path / "marker"
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create=f"echo created > {marker}"),
    )
    ws1 = await mgr.create_or_reuse("MT-2")
    assert marker.exists()
    marker.unlink()
    await mgr.create_or_reuse("MT-2")
    assert not marker.exists()  # not re-run on reuse
    assert ws1.path.exists()


@pytest.mark.asyncio
async def test_after_create_failure_aborts(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(after_create="exit 7"))
    with pytest.raises(SymphonyError):
        await mgr.create_or_reuse("MT-3")
    # Partial directory cleaned up.
    assert not (tmp_path / "ws" / "MT-3").exists()


@pytest.mark.asyncio
async def test_before_run_aborts_attempt(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(before_run="exit 9"))
    ws = await mgr.create_or_reuse("MT-4")
    with pytest.raises(SymphonyError):
        await mgr.before_run(ws.path)


@pytest.mark.asyncio
async def test_after_run_failure_is_logged_and_ignored(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(after_run="exit 11"))
    ws = await mgr.create_or_reuse("MT-5")
    # Should not raise.
    await mgr.after_run_best_effort(ws.path)


def test_validate_agent_cwd_rejects_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(InvalidWorkspaceCwd):
        validate_agent_cwd(outside, root)


def test_validate_agent_cwd_accepts_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "MT-1"
    inside.mkdir()
    validate_agent_cwd(inside, root)
