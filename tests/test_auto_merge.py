"""Tests for the builtin auto-merge-on-done feature."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from symphony.auto_merge import auto_merge_on_done_best_effort


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _make_symphony_branch(repo: Path, ident: str, *, with_symlinks: bool = True) -> None:
    """Create a symphony/<ident> branch that mirrors what after_create produces:
    a real code change plus workspace symlinks at kanban/docs."""
    _git(repo, "checkout", "-q", "-b", f"symphony/{ident}")
    (repo / "feature.py").write_text("print('hi')\n")
    _git(repo, "add", "feature.py")
    if with_symlinks:
        # Workspace symlink stand-ins — just regular files in the branch
        # for test purposes (we only need them to appear in the diff).
        (repo / "kanban").write_text("symlink-stand-in\n")
        (repo / "docs").write_text("symlink-stand-in\n")
        _git(repo, "add", "kanban", "docs")
    _git(repo, "commit", "-q", "-m", f"{ident}: feature + workspace")
    _git(repo, "checkout", "-q", "main")


def test_auto_merge_applies_changes_and_excludes_symlinks(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-1")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-1",
            identifier="T-1",
            title="test feature",
            target_branch="main",
            exclude_paths=("kanban", "docs"),
        )
    )

    # feature.py applied, but kanban/docs excluded
    assert (repo / "feature.py").exists()
    assert not (repo / "kanban").exists()
    assert not (repo / "docs").exists()
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "apply T-1 from symphony/T-1" in log


def test_auto_merge_skips_when_host_dirty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-2", with_symlinks=False)
    # make host dirty
    (repo / "README.md").write_text("modified\n")

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-2",
            identifier="T-2",
            title="should skip",
            target_branch="main",
            exclude_paths=(),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head_before == head_after, "skip on dirty host must not create commit"


def test_auto_merge_skips_missing_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/does-not-exist",
            identifier="T-X",
            title="missing",
            target_branch="main",
            exclude_paths=(),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head_before == head_after


def test_auto_merge_uses_current_branch_when_target_empty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "dev")
    _make_symphony_branch(repo, "T-3", with_symlinks=False)
    _git(repo, "checkout", "-q", "dev")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-3",
            identifier="T-3",
            title="auto-pick branch",
            target_branch="",  # empty -> current
            exclude_paths=(),
        )
    )

    # commit landed on dev, not main
    dev_head = subprocess.run(
        ["git", "log", "--oneline", "-1", "dev"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    main_head = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "apply T-3" in dev_head
    assert "apply T-3" not in main_head


def test_auto_merge_nothing_to_apply_when_all_excluded(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    # Branch only adds a path that we then exclude entirely.
    _git(repo, "checkout", "-q", "-b", "symphony/T-4")
    (repo / "kanban").write_text("only-this\n")
    _git(repo, "add", "kanban")
    _git(repo, "commit", "-q", "-m", "T-4: only workspace")
    _git(repo, "checkout", "-q", "main")

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-4",
            identifier="T-4",
            title="all excluded",
            target_branch="main",
            exclude_paths=("kanban",),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head_before == head_after
