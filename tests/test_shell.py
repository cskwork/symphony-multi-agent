"""Cross-platform bash resolution — see ``symphony._shell``."""

from __future__ import annotations

import sys
from typing import Iterator

import pytest

from symphony import _shell
from symphony._shell import _is_wsl_launcher, resolve_bash


@pytest.fixture(autouse=True)
def _reset_resolve_bash_cache() -> Iterator[None]:
    resolve_bash.cache_clear()
    yield
    resolve_bash.cache_clear()


def test_symphony_bash_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_BASH", "/custom/bash")
    assert resolve_bash() == "/custom/bash"


def test_resolve_bash_returns_nonempty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    result = resolve_bash()
    assert isinstance(result, str)
    assert result


def test_symphony_bash_override_rejecting_wsl_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SYMPHONY_BASH value pointing at the WSL launcher must not be honored
    — the whole purpose of the helper is to avoid that binary."""
    monkeypatch.setenv(
        "SYMPHONY_BASH", r"C:\Windows\System32\bash.exe"
    )
    result = resolve_bash()
    assert not _is_wsl_launcher(result)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only WSL filter")
def test_windows_filter_rejects_wsl_when_only_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``shutil.which`` would otherwise return the WSL launcher and no
    Git Bash candidate is on disk, ``resolve_bash`` must fall back to the
    bare ``"bash"`` sentinel (caught by ``doctor.check_shell``) rather than
    silently use the WSL binary."""
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    monkeypatch.setattr(
        _shell,
        "_WIN_GIT_BASH_CANDIDATES",
        (r"C:\nonexistent\nope.exe",),
    )
    monkeypatch.setattr(
        _shell.shutil,
        "which",
        lambda name: r"C:\Windows\System32\bash.exe",
    )
    assert resolve_bash() == "bash"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only WSL filter")
def test_windows_picks_git_bash_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """When a Git Bash candidate exists on disk, it wins over PATH."""
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    fake_git_bash = tmp_path / "fake-git-bash.exe"
    fake_git_bash.write_text("")
    monkeypatch.setattr(
        _shell,
        "_WIN_GIT_BASH_CANDIDATES",
        (str(fake_git_bash),),
    )
    assert resolve_bash() == str(fake_git_bash)


# ---------------------------------------------------------------------------
# safe_proc_wait — bypass for asyncio's broken child watcher under Textual
# ---------------------------------------------------------------------------


import asyncio
import subprocess as _subprocess
from types import SimpleNamespace

from symphony._shell import safe_proc_wait


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
def test_safe_proc_wait_reaps_via_thread() -> None:
    """Reaps a child the asyncio watcher never saw — the actual prod scenario.

    Symphony's bug surfaces when asyncio's child watcher silently fails to
    reap a child (Textual + macOS + 3.12). We simulate that by spawning via
    plain ``subprocess.Popen`` so asyncio never registers the PID. The
    fallback path through ``os.waitpid`` in a worker thread should still
    return the real exit code.
    """
    popen = _subprocess.Popen(
        ["bash", "-c", "exit 7"],
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
    )
    # Wrap to look like an asyncio Process for our helper's interface — we
    # only need `pid` and `returncode`.
    fake = SimpleNamespace(pid=popen.pid, returncode=None)

    rc = asyncio.run(safe_proc_wait(fake, timeout=5.0))
    assert rc == 7


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
def test_safe_proc_wait_short_circuits_when_returncode_set() -> None:
    """If the asyncio watcher already reaped, we trust ``proc.returncode``."""
    fake = SimpleNamespace(pid=99999, returncode=0)
    rc = asyncio.run(safe_proc_wait(fake, timeout=1.0))
    assert rc == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
def test_safe_proc_wait_timeout_returns_none() -> None:
    """Long-running child past the timeout returns None instead of blocking."""
    popen = _subprocess.Popen(
        ["bash", "-c", "sleep 5"],
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
    )
    fake = SimpleNamespace(pid=popen.pid, returncode=None)
    try:
        rc = asyncio.run(safe_proc_wait(fake, timeout=0.2))
        assert rc is None
    finally:
        popen.kill()


# Windows path: `os.waitpid` + WIF* helpers are POSIX-only, so the helper
# delegates to ``proc.wait()`` (the asyncio child transport's own wait).
# These tests ensure that delegation is wired up and doesn't reintroduce
# the ``module 'os' has no attribute 'WIFEXITED'`` regression.


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows delegation path")
def test_safe_proc_wait_windows_delegates_to_proc_wait() -> None:
    async def _run() -> int | None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import sys; sys.exit(7)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await safe_proc_wait(proc, timeout=10.0)
    rc = asyncio.run(_run())
    assert rc == 7


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows delegation path")
def test_safe_proc_wait_windows_short_circuits_when_returncode_set() -> None:
    fake = SimpleNamespace(pid=99999, returncode=0)
    rc = asyncio.run(safe_proc_wait(fake, timeout=1.0))
    assert rc == 0


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows delegation path")
def test_safe_proc_wait_windows_timeout_returns_none() -> None:
    async def _run() -> int | None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(5)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await safe_proc_wait(proc, timeout=0.2)
        finally:
            proc.kill()
    rc = asyncio.run(_run())
    assert rc is None
