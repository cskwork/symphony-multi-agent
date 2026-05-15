"""SPEC §17.2 — workspace manager and safety invariants."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from symphony._shell import resolve_bash
from symphony.errors import InvalidWorkspaceCwd, SymphonyError
from symphony.workflow import HooksConfig
from symphony.workspace import (
    WorkspaceManager,
    commit_workspace_on_done,
    validate_agent_cwd,
)


_HAS_GIT = shutil.which("git") is not None
_BASH = resolve_bash()


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
async def test_after_create_failure_surfaces_stderr(tmp_path):
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create="echo 'requires Python >=3.12,<3.13' >&2; exit 7"),
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-3")

    message = str(exc_info.value)
    assert "hook after_create exited 7" in message
    assert "requires Python >=3.12,<3.13" in message


# Regression guard for the cross-platform symlink helper embedded in
# WORKFLOW.md / WORKFLOW.file.example.md / WORKFLOW.smoke.md. On Windows
# Git Bash without admin/Developer Mode, `ln -s` silently copies the
# source; the agent's edits then never propagate to the host board and
# the tracker re-dispatches forever. The helper falls back to a Windows
# directory junction (mklink /J) which all programs treat as a real dir.
_LINK_DIR_HELPER = r"""
set -euo pipefail
_symphony_link_dir() {
  local target="$1" source="$2"
  rm -rf "$target"
  if [ "${OS:-}" = "Windows_NT" ] && command -v cmd.exe >/dev/null 2>&1; then
    # MSYS bash mangles backslashes inside `cmd.exe //c "..."` argument
    # strings (e.g. `\U` in `\Users` becomes garbled), so route through a
    # tiny .bat that takes %1/%2 — bat files receive properly quoted args
    # untouched. Also handles paths containing spaces.
    local target_win source_win bat bat_win
    target_win="$(cygpath -w "$(realpath -m "$target")")"
    source_win="$(cygpath -w "$source")"
    bat="${TEMP:-/tmp}/symphony-link-$$-$RANDOM.bat"
    printf '@echo off\r\nmklink /J %%1 %%2\r\n' > "$bat"
    bat_win="$(cygpath -w "$bat")"
    cmd.exe //c "$bat_win" "$target_win" "$source_win" >/dev/null
    rm -f "$bat"
  else
    ln -s "$source" "$target"
  fi
}
_symphony_link_dir "$TARGET_NAME" "$SOURCE_PATH"
"""


def test_symphony_link_dir_propagates_writes_back_to_source(tmp_path):
    """The after_create symlink helper must make agent writes inside the
    workspace appear in the host's board directory. Regression guard for
    the Windows-only silent-copy defect where `ln -s` left the workspace
    with an isolated real directory."""
    host = tmp_path / "host_repo"
    host.mkdir()
    board = host / "kanban_smoke"
    board.mkdir()
    (board / "DEMO-1.md").write_text("state: Todo\n", encoding="utf-8")

    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Pre-create the target as an empty directory (Symphony does this
    # before the hook runs, so the helper has to delete it first).
    (workspace / "kanban_smoke").mkdir()

    subprocess.run(
        [_BASH, "-lc", _LINK_DIR_HELPER],
        cwd=str(workspace),
        check=True,
        env={
            **os.environ,
            "TARGET_NAME": "kanban_smoke",
            "SOURCE_PATH": str(board),
        },
    )

    linked = workspace / "kanban_smoke"
    # Existing host file is visible through the link.
    assert (linked / "DEMO-1.md").read_text(encoding="utf-8") == "state: Todo\n"

    # Writes through the link reach the host board — this is the property
    # the silent-copy bug breaks.
    (linked / "DEMO-1.md").write_text("state: Done\n", encoding="utf-8")
    assert (board / "DEMO-1.md").read_text(encoding="utf-8") == "state: Done\n"

    # New file created through the link must also appear at the host.
    (linked / "DEMO-2.md").write_text("state: Todo\n", encoding="utf-8")
    assert (board / "DEMO-2.md").exists()


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


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_tags_non_done_state(tmp_path, monkeypatch):
    """Non-Done state must appear in the commit subject so a quick `git log`
    makes obvious that the agent didn't reach Done before the snapshot."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "leftover.txt").write_text("agent left this behind")

    await commit_workspace_on_done(
        ws, identifier="OLV-5", title="cancelled mid-flight", state="Cancelled"
    )

    log = _git(ws, "log", "--oneline")
    assert "OLV-5: cancelled mid-flight [state: Cancelled]" in log.stdout


_AFTER_RUN_HOOK = r"""
set -uo pipefail
git add -A 2>/dev/null || true
if git diff --cached --quiet 2>/dev/null; then
  exit 0
fi
LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
if [ "${LAST#wip:}" != "$LAST" ]; then
  git commit --amend --no-edit >/dev/null 2>&1 || true
else
  git commit -m "wip: turn $(date -u +%FT%TZ)" >/dev/null 2>&1 || true
fi
"""


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
def test_after_run_amend_keeps_branch_at_one_wip_commit(tmp_path, monkeypatch):
    """after_run runs after every turn: first turn creates a `wip:` commit,
    subsequent turns must amend it so the branch stays at exactly one
    commit-since-base. This is what makes the per-turn safety net
    compatible with the one-commit-per-ticket guarantee."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    import subprocess

    def _run_hook() -> None:
        subprocess.run(
            [_BASH, "-lc", _AFTER_RUN_HOOK],
            cwd=str(repo),
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )

    # Turn 1 — first commit on branch.
    (repo / "t1.txt").write_text("turn1")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    last1 = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Turn 2 — amend, no new commit. SHA changes but count stays.
    (repo / "t2.txt").write_text("turn2")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2", (
        "after_run must amend the prior wip commit, not stack new ones"
    )
    last2 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert last1 != last2, "amend should produce a new SHA"

    # Turn 3 — still amends.
    (repo / "t3.txt").write_text("turn3")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"

    # All three turn files captured in the single wip commit.
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("seed.txt", "t1.txt", "t2.txt", "t3.txt"):
        assert fname in files

    # And commit_workspace_on_done collapses that wip into a single named ticket commit.
    _git(repo, "config", "symphony.basesha", base_sha)

    import asyncio
    asyncio.run(commit_workspace_on_done(repo, identifier="OLV-AM", title="amend flow"))

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log == ["OLV-AM: amend flow", "base commit"], (
        f"expected base + 1 ticket commit, got {log!r}"
    )


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
def test_after_run_does_not_amend_agent_authored_commit(tmp_path, monkeypatch):
    """If the agent itself committed (subject doesn't start with `wip:`),
    after_run must NOT clobber the agent's message via --amend; it stacks
    a new `wip:` commit on top so the agent's intent stays in the squash."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    # Agent makes a deliberate commit mid-run.
    (repo / "feature.txt").write_text("agent's deliberate work")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: add feature X")

    # after_run picks up further uncommitted changes.
    (repo / "more.txt").write_text("more work")
    import subprocess
    subprocess.run(
        [_BASH, "-lc", _AFTER_RUN_HOOK],
        cwd=str(repo), check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    # Newest first: wip on top, then agent's feat, then base.
    assert log[0].startswith("wip:")
    assert log[1] == "feat: add feature X"
    assert log[2] == "base commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_squashes_to_recorded_base(
    tmp_path, monkeypatch
):
    """When `git config symphony.basesha` is set (the worktree-default
    after_create hook records this), commit_workspace_on_done must soft-
    reset to that fork point so all per-turn commits + uncommitted changes
    collapse into ONE ticket commit. Anything else breaks the
    'one-commit-per-ticket' guarantee operators rely on for clean merges."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Simulate after_create's record-the-fork-point step.
    _git(repo, "config", "symphony.basesha", base_sha)

    # Simulate per-turn agent activity: three commits accumulating on the
    # branch, plus uncommitted leftover changes at the end.
    (repo / "turn1.txt").write_text("t1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 1")

    (repo / "turn2.txt").write_text("t2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 2")

    (repo / "turn3.txt").write_text("t3")  # uncommitted leftover

    # Pre-state: HEAD is two commits past base + dirty working tree.
    pre_count = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert pre_count == "3"

    await commit_workspace_on_done(repo, identifier="OLV-SQ", title="squash demo")

    # After: branch has exactly base + 1 ticket commit, all turn files
    # captured in that single commit.
    post_count = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert post_count == "2", (
        f"expected base + 1 ticket commit, got {post_count} commits"
    )
    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log[0] == "OLV-SQ: squash demo"
    assert log[1] == "base commit"
    # All three turn files must be present in the squashed commit.
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("seed.txt", "turn1.txt", "turn2.txt", "turn3.txt"):
        assert fname in files, f"{fname} missing from squashed commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_no_base_falls_back_to_plain_commit(
    tmp_path, monkeypatch
):
    """Legacy / non-worktree workspaces have no `symphony.basesha` recorded;
    helper must still commit (no squash) so existing setups don't regress."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    # No symphony.basesha config — simulate legacy workspace.
    (repo / "new.txt").write_text("uncommitted")

    await commit_workspace_on_done(repo, identifier="OLV-LEG", title="legacy")

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log[0] == "OLV-LEG: legacy"
    assert log[1] == "base commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_tags_abnormal_exit(tmp_path, monkeypatch):
    """When the worker died (reason != normal) and the state is still active,
    surface the exit reason in the subject."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "wip.txt").write_text("partial")

    await commit_workspace_on_done(
        ws,
        identifier="OLV-6",
        title="recovered work",
        exit_reason="reconcile_terminate_terminal",
        state="Done",  # Done state suppresses the suffix even when reason is non-normal
    )
    log = _git(ws, "log", "--oneline")
    assert "OLV-6: recovered work" in log.stdout
    assert "[state:" not in log.stdout
    assert "[exit:" not in log.stdout

    # Now without the Done state — exit reason should surface.
    (ws / "wip.txt").write_text("partial v2")
    await commit_workspace_on_done(
        ws,
        identifier="OLV-7",
        title="leftover",
        exit_reason="reconcile_terminate_terminal",
        state="In Progress",
    )
    log = _git(ws, "log", "--oneline")
    assert "OLV-7: leftover [state: In Progress]" in log.stdout
