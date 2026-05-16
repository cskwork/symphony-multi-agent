"""Tests for the builtin auto-merge-on-done feature."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from symphony._shell import resolve_bash
from symphony.auto_merge import auto_merge_on_done_best_effort, _build_script


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
    a real code change plus optional leaked workspace roots at kanban/docs."""
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


def test_auto_merge_creates_no_ff_merge_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-1", with_symlinks=False)

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-1",
            identifier="T-1",
            title="test feature",
            target_branch="main",
            exclude_paths=("kanban",),
        )
    )

    assert (repo / "feature.py").exists()
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "merge: T-1 from symphony/T-1" in log
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert len(parents) == 3, "auto merge must create an explicit merge commit"


def test_auto_merge_skips_when_host_dirty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "symphony/T-2")
    (repo / "README.md").write_text("branch change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "T-2: modify readme")
    _git(repo, "checkout", "-q", "main")
    # make host dirty on the same path the branch changes
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


def test_auto_merge_reports_real_conflict_before_dirty_overlap(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "symphony/T-CONFLICT")
    (repo / "README.md").write_text("branch change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "T-CONFLICT: modify readme")
    _git(repo, "checkout", "-q", "main")
    (repo / "README.md").write_text("target change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "main: modify readme")

    # Operator also has a dirty local edit on the same path. The merge gate
    # must still report the committed target/branch conflict first; otherwise
    # agents block on "dirty worktree" and miss the real integration work.
    (repo / "README.md").write_text("operator scratch\n")

    result = subprocess.run(
        [
            resolve_bash(),
            "-lc",
            _build_script(
                branch="symphony/T-CONFLICT",
                target="main",
                identifier="T-CONFLICT",
                title="conflict should surface",
                excludes=(),
            ),
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 50
    assert "CONFLICT" in output
    assert "SKIP: host tracked changes overlap branch merge" not in output


def test_auto_merge_allows_non_overlapping_host_dirty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-2B", with_symlinks=False)
    (repo / "local-note.txt").write_text("operator scratch\n")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-2B",
            identifier="T-2B",
            title="should merge",
            target_branch="main",
            exclude_paths=(),
        )
    )

    assert (repo / "feature.py").exists()
    assert (repo / "local-note.txt").read_text() == "operator scratch\n"
    status = subprocess.run(
        ["git", "status", "--short"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "?? local-note.txt" in status


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
    assert "merge: T-3" in dev_head
    assert "merge: T-3" not in main_head


def test_auto_merge_blocks_when_excluded_root_changed(tmp_path: Path) -> None:
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
    assert not (repo / "kanban").exists()


def test_auto_merge_captures_untracked_paths(tmp_path: Path) -> None:
    """Opt-in capture: a host-side untracked file under `docs-host/` should
    land in the same merge commit, even when the branch-side `docs` blob
    is excluded. This closes the after_create-symlink gap where docs are
    written via symlink into the host repo and never appear in the
    symphony/<ID> branch diff."""
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-5", with_symlinks=False)

    # Simulate what an agent does when writing through an after_create
    # symlink: a real file lands in the host repo's docs-host/ directory
    # as untracked content, never staged on the symphony/<ID> branch.
    docs_dir = repo / "docs-host"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("agent wrote this via symlink\n")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-5",
            identifier="T-5",
            title="capture host untracked",
            target_branch="main",
            exclude_paths=("kanban",),
            capture_untracked=("docs-host",),
        )
    )

    # feature.py was applied as before
    assert (repo / "feature.py").exists()
    # host-side untracked note got captured into the same commit
    assert (repo / "docs-host" / "note.md").exists()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "docs-host/note.md" in tree
    assert "feature.py" in tree
    # And the commit is the auto-merge commit, not a stray prior one.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    assert "merge: T-5 from symphony/T-5" in log
