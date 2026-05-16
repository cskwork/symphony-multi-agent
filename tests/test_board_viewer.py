"""Board viewer static chrome regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "tools" / "board-viewer" / "server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("board_viewer_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_header_has_branch_policy_slot() -> None:
    html = Path("tools/board-viewer/index.html").read_text(encoding="utf-8")

    assert 'id="branch-policy"' in html
    assert 'id="feature-base-branch"' in html
    assert 'id="merge-target-branch"' in html


def test_card_renderer_has_agent_badge_slot() -> None:
    js = Path("tools/board-viewer/src/js/ticket.js").read_text(encoding="utf-8")

    assert "agent-badges" in js
    assert "done by" in js
    assert "working" in js
    assert "agentKindFromTicket(ticket) || normalizeAgentKind(options.defaultAgentKind" in js
    assert 'String(ticket.state || "").toLowerCase() === "done" ? agentKindFromTicket(ticket)' in js


def test_board_viewer_fallback_states_and_policy_mode() -> None:
    js = Path("tools/board-viewer/src/js/board.js").read_text(encoding="utf-8")

    assert "const FALLBACK_STATES" in js
    assert '"Plan"' in js
    assert "policy.auto_merge_enabled === false" in js
    assert "merge off" in js
    assert "fetchGitBranches" in js
    assert "saveBranchPolicy" in js
    assert "updateBranchPolicyFromGit" in js
    assert "repo_root" in js


def test_kanban_index_exposes_nested_agent_kind(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    board = tmp_path / "kanban"
    board.mkdir()
    (board / "TASK-1.md").write_text(
        """---
id: TASK-1
title: Build UI
state: Todo
agent:
  kind: Claude
---
body
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KANBAN_DIR", board)

    tickets = server.list_kanban_tickets()

    assert tickets[0]["agent_kind"] == "claude"


def test_done_ticket_reports_done_agent_kind(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    board = tmp_path / "kanban"
    board.mkdir()
    (board / "TASK-2.md").write_text(
        """---
id: TASK-2
title: Finish logic
state: Done
agent_kind: Codex
---
body
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KANBAN_DIR", board)

    tickets = server.list_kanban_tickets()

    assert tickets[0]["agent_kind"] == "codex"
    assert tickets[0]["done_agent_kind"] == "codex"


def test_workflow_branch_policy_update_preserves_body(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        """---
tracker:
  kind: file
agent:
  kind: codex
  auto_merge_target_branch: "main"
---
Body stays here
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "WORKFLOW_PATH", workflow)

    policy = server.update_workflow_branch_policy(
        feature_base_branch="dev",
        merge_target_branch="release",
    )
    text = workflow.read_text(encoding="utf-8")

    assert policy == {
        "feature_base_branch": "dev",
        "auto_merge_target_branch": "release",
    }
    assert 'feature_base_branch: "dev"' in text
    assert 'auto_merge_target_branch: "release"' in text
    assert "Body stays here" in text


def test_git_branch_list_reads_real_local_branches(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo, check=True, capture_output=True)
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\nagent:\n  feature_base_branch: dev\n---\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "WORKFLOW_PATH", workflow)

    branches = server.list_git_branches()

    assert branches["ok"] is True
    assert "main" in branches["branches"]
    assert "dev" in branches["branches"]
    assert branches["current_branch"] == "dev"
    assert branches["repo_root"] == str(repo)
    assert branches["workflow_path"] == str(workflow)
    assert branches["feature_base_branch"] == "dev"
