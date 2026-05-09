"""Cross-platform bash resolution — see ``symphony._shell``."""

from __future__ import annotations

import sys

import pytest

from symphony._shell import resolve_bash


def test_symphony_bash_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_BASH", "/custom/bash")
    resolve_bash.cache_clear()
    try:
        assert resolve_bash() == "/custom/bash"
    finally:
        resolve_bash.cache_clear()


def test_resolve_bash_returns_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    resolve_bash.cache_clear()
    try:
        result = resolve_bash()
        assert isinstance(result, str)
        assert result  # non-empty
    finally:
        resolve_bash.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only WSL filter")
def test_windows_does_not_pick_wsl_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Git Bash is installed, the System32 WSL launcher must not win."""
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    resolve_bash.cache_clear()
    try:
        result = resolve_bash().lower()
        assert "system32" not in result
        assert "windowsapps" not in result
    finally:
        resolve_bash.cache_clear()
