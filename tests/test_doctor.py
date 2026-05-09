"""Preflight checks emitted by `symphony doctor`."""

from __future__ import annotations

import socket
import textwrap
from pathlib import Path

from symphony.doctor import (
    check_after_create_hook,
    check_agent_cli,
    check_pi_auth,
    check_port,
    check_tracker,
    check_workspace_root,
    format_results,
    run_checks,
)
from symphony.workflow import ServiceConfig, build_service_config, load_workflow


def _write_workflow(tmp_path: Path, body: str) -> Path:
    """Drop a YAML frontmatter workflow file at tmp_path/WORKFLOW.md."""
    path = tmp_path / "WORKFLOW.md"
    path.write_text(body)
    return path


def _build_cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "---\nbody"
    path = _write_workflow(tmp_path, text)
    return build_service_config(load_workflow(path))


def test_after_create_flags_placeholder(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: |
            git clone --depth=1 git@github.com:my-org/my-repo.git .
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_after_create_hook(cfg)
    assert result.status == "fail"
    assert "my-org/my-repo" in result.message


def test_after_create_passes_when_customized(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: ": noop"
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_after_create_hook(cfg).status == "pass"


def test_agent_cli_pass_for_python_mock(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: python -m symphony.mock_codex }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "pass"
    assert "python" in result.message


def test_agent_cli_fail_for_missing_binary(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: definitely-not-a-real-binary-xyz123 }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "fail"
    assert "not on $PATH" in result.message


def test_port_pass_when_unconfigured(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    # No `server: { port: ... }` block → port is None → check passes trivially.
    assert check_port(cfg).status == "pass"


def test_port_fail_when_already_bound(tmp_path: Path) -> None:
    # Bind an ephemeral port and feed it back to the doctor.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bound_port = sock.getsockname()[1]
    try:
        cfg = _build_cfg(
            tmp_path,
            f"""
            tracker: {{ kind: file, board_root: ./kanban }}
            agent: {{ kind: codex }}
            codex: {{ command: codex app-server }}
            server: {{ port: {bound_port} }}
            """,
        )
        result = check_port(cfg)
        assert result.status == "fail"
        # Doctor wraps the OSError as `cannot bind <host>:<port> — <exc>`.
        # Avoid asserting on the OSError text — Windows OSes return a
        # localized "Address already in use" (e.g. Korean "주소 …") that does
        # not contain the English substring.
        assert "cannot bind" in result.message
        assert f"127.0.0.1:{bound_port}" in result.message
    finally:
        sock.close()


def test_workspace_root_creates_and_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: ./kanban }}
        workspace: {{ root: {workspace} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_workspace_root(cfg)
    assert result.status == "pass"
    assert workspace.exists()


def test_tracker_file_warns_on_missing_board_root(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: {missing} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_tracker(cfg)
    assert result.status == "fail"
    assert "does not exist" in result.message
    assert "symphony board init" in result.message


def test_tracker_file_passes_with_tickets(tmp_path: Path) -> None:
    board = tmp_path / "kanban"
    board.mkdir()
    (board / "X-1.md").write_text("---\nidentifier: X-1\ntitle: t\nstate: Todo\n---\n")
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: {board} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_tracker(cfg)
    assert result.status == "pass"
    assert "1 ticket" in result.message


def test_run_checks_returns_one_result_per_check(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    results = run_checks(cfg)
    # port + shell + agent + pi_auth + after_create + workspace + tracker = 7
    assert len(results) == 7
    assert {r.name.split("=")[0].split(".")[0] for r in results} >= {
        "agent",
        "hooks",
        "workspace",
        "tracker",
    }


def test_pi_auth_skipped_for_non_pi(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def test_pi_auth_warns_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # ~ resolves to a clean dir
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: pi }
        pi: { command: 'pi --mode json -p \"\"' }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "warn"
    assert "auth.json" in result.message


def test_pi_auth_passes_when_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    auth = tmp_path / ".pi" / "agent" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: pi }
        pi: { command: 'pi --mode json -p \"\"' }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "pass"
    assert "auth.json" in result.message


def test_format_results_includes_all_statuses() -> None:
    from symphony.doctor import CheckResult

    text = format_results(
        [
            CheckResult("a", "pass", "ok"),
            CheckResult("b", "warn", "careful"),
            CheckResult("c", "fail", "broken"),
        ],
        color=False,
    )
    assert "PASS" in text and "WARN" in text and "FAIL" in text
