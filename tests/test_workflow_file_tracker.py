"""Workflow validation when tracker.kind=file."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.errors import (
    MissingTrackerApiKey,
    UnsupportedTrackerKind,
)
from symphony.workflow import (
    build_service_config,
    load_workflow,
    validate_for_dispatch,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_file_kind_does_not_require_api_key(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    # Should not raise.
    validate_for_dispatch(cfg)
    assert cfg.tracker.kind == "file"
    assert cfg.tracker.board_root == (tmp_path / "board").resolve()


def test_file_kind_uses_default_board_root_when_omitted(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.board_root == (tmp_path / "board").resolve()
    validate_for_dispatch(cfg)


def test_file_kind_explicit_root_resolution(tmp_path):
    explicit = tmp_path / "custom-board"
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker:
              kind: file
              board_root: {explicit}
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.board_root == explicit.resolve()


def test_unsupported_kind_still_rejected(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: jira
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(UnsupportedTrackerKind):
        validate_for_dispatch(cfg)


def test_linear_kind_still_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerApiKey):
        validate_for_dispatch(cfg)
