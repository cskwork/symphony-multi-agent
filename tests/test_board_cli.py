"""Tests for the file-board helper CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony import board_cli
from symphony.workflow import SUPPORTED_AGENT_KINDS


@pytest.mark.parametrize("agent_kind", sorted(SUPPORTED_AGENT_KINDS))
def test_board_new_root_overrides_existing_workflow(
    tmp_path: Path, agent_kind: str
) -> None:
    workflow_board = tmp_path / "workflow-board"
    override_board = tmp_path / "override-board"
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "\n".join(
            [
                "---",
                "tracker:",
                "  kind: file",
                "  board_root: ./workflow-board",
                "---",
                "prompt",
            ]
        ),
        encoding="utf-8",
    )

    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "--root",
            str(override_board),
            "CLI-ROOT",
            "Root override",
            "--agent-kind",
            agent_kind,
        ]
    )

    assert rc == 0
    ticket = override_board / "CLI-ROOT.md"
    assert ticket.exists()
    assert f"kind: {agent_kind}" in ticket.read_text(encoding="utf-8")
    assert not (workflow_board / "CLI-ROOT.md").exists()
