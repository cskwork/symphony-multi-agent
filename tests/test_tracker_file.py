"""File-based Kanban tracker conformance against §11.1, §11.3, §17.3."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.errors import SymphonyError
from symphony.tracker_file import (
    FileBoardTracker,
    issue_from_file,
    parse_ticket_file,
    serialize_ticket,
    write_ticket_atomic,
)
from symphony.workflow import TrackerConfig


def _tracker(root: Path, **kwargs) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=kwargs.get("active", ("Todo", "In Progress")),
        terminal_states=kwargs.get("terminal", ("Done", "Cancelled")),
        board_root=root.resolve(),
    )


def _write(root: Path, name: str, content: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_ticket_file_basic(tmp_path):
    path = _write(
        tmp_path,
        "DEV-1.md",
        textwrap.dedent(
            """\
            ---
            id: DEV-1
            title: Hello
            state: Todo
            priority: 2
            labels: [backend, bug]
            ---
            Description body.
            """
        ),
    )
    front, body = parse_ticket_file(path)
    assert front["id"] == "DEV-1"
    assert front["state"] == "Todo"
    assert "Description body." in body


def test_issue_from_file_normalizes(tmp_path):
    path = _write(
        tmp_path,
        "DEV-2.md",
        textwrap.dedent(
            """\
            ---
            id: DEV-2
            title: Fix
            state: In Progress
            priority: 1
            labels: [Backend, Bug]
            blocked_by:
              - identifier: DEV-99
                state: Done
              - DEV-77
            ---
            body
            """
        ),
    )
    issue = issue_from_file(path)
    assert issue is not None
    assert issue.identifier == "DEV-2"
    assert issue.priority == 1
    # Labels lowercased per §11.3.
    assert issue.labels == ("backend", "bug")
    assert len(issue.blocked_by) == 2
    assert issue.blocked_by[0].identifier == "DEV-99"
    assert issue.blocked_by[0].state == "Done"
    assert issue.blocked_by[1].identifier == "DEV-77"


def test_issue_from_file_returns_none_when_required_missing(tmp_path):
    path = _write(tmp_path, "broken.md", "---\nfoo: bar\n---\nbody")
    assert issue_from_file(path) is None


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "bad.md", "---\nthis: : invalid : yaml\n---\nbody")
    with pytest.raises(SymphonyError):
        parse_ticket_file(path)


def test_unterminated_front_matter_raises(tmp_path):
    path = _write(tmp_path, "no-end.md", "---\nid: X\nstate: Todo\nbody continues")
    with pytest.raises(SymphonyError):
        parse_ticket_file(path)


def test_parse_ticket_file_auto_heals_markdown_inside_front_matter(tmp_path):
    path = _write(
        tmp_path,
        "DEV-1.md",
        textwrap.dedent(
            """\
            ---
            id: DEV-1
            title: Heal misplaced triage
            state: In Progress

            ## Triage

            ticket is actionable; routing to Explore.
            labels: [product, api]
            blocked_by:
              - identifier: DEV-0
                state: Done
            ---
            Existing body.
            """
        ),
    )

    front, body = parse_ticket_file(path)

    assert front["id"] == "DEV-1"
    assert front["labels"] == ["product", "api"]
    assert front["blocked_by"][0]["identifier"] == "DEV-0"
    assert body.startswith("## Triage\n\nticket is actionable")
    assert "Existing body." in body

    healed_text = path.read_text(encoding="utf-8")
    front_text = healed_text.split("---", 2)[1]
    assert "## Triage" not in front_text
    assert "labels:" in front_text


def test_fetch_candidate_filters_by_active(tmp_path):
    root = tmp_path / "board"
    _write(root, "A.md", "---\nid: A\ntitle: a\nstate: Todo\n---\n")
    _write(root, "B.md", "---\nid: B\ntitle: b\nstate: Done\n---\n")
    _write(root, "C.md", "---\nid: C\ntitle: c\nstate: In Progress\n---\n")
    fbt = FileBoardTracker(_tracker(root))
    ids = sorted(i.identifier for i in fbt.fetch_candidate_issues())
    assert ids == ["A", "C"]


def test_fetch_candidate_raises_on_invalid_ticket_yaml(tmp_path):
    root = tmp_path / "board"
    _write(root, "A.md", "---\nid: A\ntitle: a\nstate: Todo\n---\n")
    _write(root, "B.md", "---\nid: B\ntitle: b\nstate: Todo\nbroken\n---\n")
    fbt = FileBoardTracker(_tracker(root))

    with pytest.raises(SymphonyError, match="invalid YAML front matter"):
        fbt.fetch_candidate_issues()


def test_fetch_candidate_resolves_blocker_state_from_current_board(tmp_path):
    """Stale blocker state embedded in a ticket must not make it eligible."""
    root = tmp_path / "board"
    _write(root, "A.md", "---\nid: A\ntitle: blocker\nstate: Review\n---\n")
    _write(
        root,
        "B.md",
        textwrap.dedent(
            """\
            ---
            id: B
            title: dependent
            state: Todo
            blocked_by:
              - identifier: A
                state: Done
            ---
            """
        ),
    )

    fbt = FileBoardTracker(_tracker(root, active=("Todo", "Review")))
    issues = {i.identifier: i for i in fbt.fetch_candidate_issues()}

    assert issues["B"].blocked_by[0].state == "Review"


def test_fetch_issues_by_states(tmp_path):
    root = tmp_path / "board"
    _write(root, "A.md", "---\nid: A\ntitle: a\nstate: Done\n---\n")
    _write(root, "B.md", "---\nid: B\ntitle: b\nstate: Done\n---\n")
    _write(root, "C.md", "---\nid: C\ntitle: c\nstate: Todo\n---\n")
    fbt = FileBoardTracker(_tracker(root))
    ids = sorted(i.identifier for i in fbt.fetch_issues_by_states(["Done"]))
    assert ids == ["A", "B"]
    assert fbt.fetch_issues_by_states([]) == []


def test_fetch_states_by_ids(tmp_path):
    root = tmp_path / "board"
    _write(root, "A.md", "---\nid: A\ntitle: a\nstate: Todo\n---\n")
    _write(root, "B.md", "---\nid: B\ntitle: b\nstate: Done\n---\n")
    fbt = FileBoardTracker(_tracker(root))
    out = fbt.fetch_issue_states_by_ids(["A", "Z"])
    assert [(i.id, i.state) for i in out] == [("A", "Todo")]
    # description/priority intentionally absent on minimal records.
    assert out[0].description is None
    assert out[0].priority is None


def test_create_and_transition_round_trip(tmp_path):
    root = tmp_path / "board"
    fbt = FileBoardTracker(_tracker(root))
    path = fbt.create(
        identifier="X-1",
        title="Title",
        state="Todo",
        priority=3,
        labels=["alpha"],
        description="hello world",
    )
    assert path.exists()
    issue = issue_from_file(path)
    assert issue is not None and issue.state == "Todo"
    # Cannot create twice.
    with pytest.raises(SymphonyError):
        fbt.create(identifier="X-1", title="dup")
    # Transition.
    fbt.transition("X-1", "In Progress")
    issue2 = issue_from_file(path)
    assert issue2 is not None and issue2.state == "In Progress"
    # find_path falls back to scanning when name and id diverge.
    odd = root / "weird-name.md"
    odd.write_text(
        "---\nid: X-2\ntitle: t\nstate: Todo\n---\nbody\n", encoding="utf-8"
    )
    assert fbt.find_path("X-2") == odd


def test_update_state_protocol_hook(tmp_path):
    """`update_state` is the TrackerClient mutation surface — proxies to transition."""
    from symphony.issue import Issue

    root = tmp_path / "board"
    fbt = FileBoardTracker(_tracker(root))
    path = fbt.create(identifier="X-9", title="t", state="Done")
    issue = issue_from_file(path)
    assert issue is not None
    # Pass an Issue object — adapter pulls `identifier` itself.
    fbt.update_state(
        Issue(
            id=issue.id,
            identifier="X-9",
            title="t",
            description=None,
            priority=None,
            state="Done",
        ),
        "Archive",
    )
    after = issue_from_file(path)
    assert after is not None and after.state == "Archive"


def test_serialize_round_trip(tmp_path):
    front = {"id": "X-1", "title": "t", "state": "Todo", "priority": 1}
    body = "hello"
    text = serialize_ticket(front, body)
    assert text.startswith("---\nid: X-1")
    assert "hello" in text
    path = tmp_path / "x.md"
    write_ticket_atomic(path, front, body)
    parsed_front, parsed_body = parse_ticket_file(path)
    assert parsed_front["id"] == "X-1"
    assert parsed_body == "hello"
