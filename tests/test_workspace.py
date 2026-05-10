"""SPEC §17.2 — workspace manager and safety invariants."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from symphony.errors import InvalidWorkspaceCwd, SymphonyError
from symphony.workflow import HooksConfig
from symphony.workspace import (
    WorkspaceManager,
    commit_workspace_on_done,
    validate_agent_cwd,
)


_HAS_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env={
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": os.environ.get("PATH", ""),
        },
    )


def _hooks(**overrides) -> HooksConfig:
    base = dict(
        after_create=None,
        before_run=None,
        after_run=None,
        before_remove=None,
        # Generous default — Git Bash on Windows takes 1–4 s for a cold
        # `bash -lc` startup; 2 s caused false-positive timeouts in CI.
        timeout_ms=30_000,
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
    # Hook writes into its own cwd (the workspace) using a relative path so
    # the assertion is independent of how bash on the host parses absolute
    # paths — MSYS bash on Windows mishandles drive-letter prefixes when
    # they're embedded in the script string.
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create="echo created > marker"),
    )
    ws1 = await mgr.create_or_reuse("MT-2")
    marker = ws1.path / "marker"
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


@pytest.mark.asyncio
async def test_after_run_skipped_when_cwd_missing(tmp_path):
    """If the agent (or anything else) deletes the workspace before exit,
    after_run_best_effort must skip the hook silently rather than spawn
    bash with a missing cwd (which raises a noisy FileNotFoundError that
    the user cannot act on)."""
    mgr = WorkspaceManager(
        tmp_path / "ws", _hooks(after_run="echo should-not-run > marker")
    )
    ws = await mgr.create_or_reuse("MT-6")
    # Simulate post-agent deletion.
    import shutil as _shutil
    _shutil.rmtree(ws.path)
    # Should not raise; hook is skipped, no marker created elsewhere.
    await mgr.after_run_best_effort(ws.path)
    assert not ws.path.exists()


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


@pytest.mark.asyncio
async def test_workflow_dir_env_exported(tmp_path):
    wf_dir = tmp_path / "host"
    wf_dir.mkdir()
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create='echo "$SYMPHONY_WORKFLOW_DIR" > wfdir'),
        workflow_dir=wf_dir,
    )
    ws = await mgr.create_or_reuse("MT-ENV")
    content = (ws.path / "wfdir").read_text().strip()
    assert content == str(wf_dir)


# ---------------------------------------------------------------------------
# auto-commit on Done — commit_workspace_on_done
# ---------------------------------------------------------------------------


def _git_id_env(monkeypatch, home):
    """Set per-test git author/committer + isolated HOME so commits don't
    pick up the developer's global ~/.gitconfig (sigstore signing, etc.)."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_initialises_fresh_repo(
    tmp_path, monkeypatch
):
    """Workspace with no .git ancestor: init + commit creates first revision."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.txt").write_text("hello")

    await commit_workspace_on_done(ws, identifier="OLV-1", title="setup db")

    assert (ws / ".git").is_dir()
    log = _git(ws, "log", "--oneline")
    assert "OLV-1: setup db" in log.stdout


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_reuses_parent_repo(
    tmp_path, monkeypatch
):
    """Workspace nested in an existing repo: commit lands there, no nested .git."""
    _git_id_env(monkeypatch, tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q", "-b", "main")
    (parent / "seed.txt").write_text("seed")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-q", "-m", "seed")

    nested = parent / "ws"
    nested.mkdir()
    (nested / "out.txt").write_text("nested work")

    await commit_workspace_on_done(nested, identifier="OLV-2", title="nested")

    assert not (nested / ".git").exists()
    log = _git(parent, "log", "--oneline")
    assert "OLV-2: nested" in log.stdout


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_skips_when_nothing_to_commit(
    tmp_path, monkeypatch
):
    """Empty workspace with init: helper logs and returns, no commit created."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()  # empty — no files to commit

    await commit_workspace_on_done(ws, identifier="OLV-3", title="empty")

    assert (ws / ".git").is_dir()
    # `git log` errors with exit 128 on a zero-commit repo (no HEAD yet),
    # so count revs instead — empty workspace must produce zero commits.
    count = _git(ws, "rev-list", "--all", "--count")
    assert count.stdout.strip() == "0"


@pytest.mark.asyncio
async def test_commit_workspace_on_done_missing_path_is_silent_noop(tmp_path):
    """Workspace already removed by hook/agent: helper must not raise."""
    missing = tmp_path / "gone"
    # Don't create it.
    await commit_workspace_on_done(missing, identifier="OLV-4", title="x")
    # No exception = pass.
