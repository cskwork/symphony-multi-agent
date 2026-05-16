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


def test_header_has_branch_controls_without_summary_slot() -> None:
    html = Path("tools/board-viewer/index.html").read_text(encoding="utf-8")
    css = Path("tools/board-viewer/src/css/style.css").read_text(encoding="utf-8")

    assert 'id="feature-base-branch"' in html
    assert 'id="merge-target-branch"' in html
    assert 'id="branch-policy"' not in html

    header_left_block = css.split(".header-left", 1)[1].split("}", 1)[0]
    header_right_block = css.split(".header-right", 1)[1].split("}", 1)[0]
    for block in (header_left_block, header_right_block):
        assert "flex-wrap: nowrap;" in block
        assert "overflow-x: auto;" in block


def test_card_renderer_has_agent_badge_slot() -> None:
    js = Path("tools/board-viewer/src/js/ticket.js").read_text(encoding="utf-8")

    assert "agent-badges" in js
    assert "done by" in js
    assert "working" in js
    assert "agentKindFromTicket(ticket) || normalizeAgentKind(options.defaultAgentKind" in js
    assert 'String(ticket.state || "").toLowerCase() === "done" ? agentKindFromTicket(ticket)' in js


def test_card_renderer_has_done_archive_action() -> None:
    ticket_js = Path("tools/board-viewer/src/js/ticket.js").read_text(encoding="utf-8")
    board_js = Path("tools/board-viewer/src/js/board.js").read_text(encoding="utf-8")
    api_js = Path("tools/board-viewer/src/js/api.js").read_text(encoding="utf-8")

    assert 'String(ticket.state || "").trim().toLowerCase() === "done"' in ticket_js
    assert 'makeActionBtn("Archive", "card-btn archive"' in ticket_js
    assert "onArchive" in ticket_js
    assert "archiveTicket" in board_js
    assert "onArchive" in board_js
    assert "/api/kanban/${encodeURIComponent(id)}/archive" in api_js


def test_board_viewer_fallback_states_and_policy_mode() -> None:
    js = Path("tools/board-viewer/src/js/board.js").read_text(encoding="utf-8")

    assert "const FALLBACK_STATES" in js
    assert '"Plan"' in js
    assert "branchPolicyEl" not in js
    assert "`branch:" not in js
    assert "merge off" not in js
    assert "fetchGitBranches" in js
    assert "saveBranchPolicy" in js
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


def test_archive_kanban_ticket_moves_done_to_archive(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    board = tmp_path / "kanban"
    board.mkdir()
    ticket = board / "TASK-3.md"
    ticket.write_text(
        """---
id: TASK-3
identifier: TASK-3
title: Finished task
state: Done
updated_at: 2026-05-01T00:00:00Z
---
body stays
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KANBAN_DIR", board)

    result = server.archive_kanban_ticket("TASK-3")
    front, body = server.parse_frontmatter(ticket.read_text(encoding="utf-8"))

    assert result["changed"] is True
    assert result["previous_state"] == "Done"
    assert front["state"] == "Archive"
    assert front["updated_at"] != "2026-05-01T00:00:00Z"
    assert body == "body stays\n"


def test_archive_kanban_ticket_refuses_active_ticket(tmp_path, monkeypatch) -> None:
    server = _load_server_module()
    board = tmp_path / "kanban"
    board.mkdir()
    ticket = board / "TASK-4.md"
    ticket.write_text(
        """---
id: TASK-4
title: Active task
state: Todo
---
body
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "KANBAN_DIR", board)

    try:
        server.archive_kanban_ticket("TASK-4")
    except ValueError as exc:
        assert "only Done tickets" in str(exc)
    else:
        raise AssertionError("expected active ticket archive to fail")
    front, _body = server.parse_frontmatter(ticket.read_text(encoding="utf-8"))
    assert front["state"] == "Todo"


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


def test_theme_switcher_static_contract() -> None:
    html = Path("tools/board-viewer/index.html").read_text(encoding="utf-8")
    js = Path("tools/board-viewer/src/js/board.js").read_text(encoding="utf-8")
    css = Path("tools/board-viewer/src/css/style.css").read_text(encoding="utf-8")

    # 1) Theme switcher markup
    assert 'id="theme-controls"' in html
    assert 'data-theme="default"' in html
    assert 'data-theme="focus"' in html
    assert 'data-theme="command"' in html

    # 2) Theme JS contract
    assert 'THEME_STORAGE_KEY = "boardViewer.theme"' in js
    assert "function readTheme(" in js
    assert "function applyTheme(" in js
    assert "function setTheme(" in js
    assert "function bindThemeControls(" in js
    assert "applyTheme(readTheme())" in js

    # 3) CSS theme overrides re-declare the key variables
    for selector in ('[data-theme="focus"]', '[data-theme="command"]'):
        assert f":root{selector}" in css, f"missing block: :root{selector}"
    focus_block = css.split(':root[data-theme="focus"]', 1)[1].split("}", 1)[0]
    cmd_block = css.split(':root[data-theme="command"]', 1)[1].split("}", 1)[0]
    for var in ("--bg", "--fg", "--accent"):
        assert var in focus_block, f"focus block missing {var}"
        assert var in cmd_block, f"command block missing {var}"

    # 4) Prep: --code-fg variable replaces hardcoded literal at modal code style
    assert "--code-fg:" in css
    assert "color: var(--code-fg);" in css
