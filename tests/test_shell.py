"""Cross-platform bash resolution — see ``symphony._shell``."""

from __future__ import annotations

import sys

import pytest

from symphony import _shell
from symphony._shell import _is_wsl_launcher, resolve_bash


@pytest.fixture(autouse=True)
def _reset_resolve_bash_cache() -> None:
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
