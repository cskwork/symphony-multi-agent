"""SPEC §17.1, §17.4 — issue helpers + dispatch sort."""

from __future__ import annotations

from datetime import datetime, timezone

from symphony.issue import (
    Issue,
    coerce_priority,
    normalize_labels,
    parse_iso_timestamp,
    sort_for_dispatch,
    workspace_key,
)


def _i(identifier: str, priority: int | None, created_at: datetime | None) -> Issue:
    return Issue(
        id=identifier,
        identifier=identifier,
        title="t",
        description=None,
        priority=priority,
        state="Todo",
        created_at=created_at,
    )


def test_workspace_key_sanitizes():
    assert workspace_key("ABC-123") == "ABC-123"
    assert workspace_key("../../etc/passwd") == ".._.._etc_passwd"
    assert workspace_key("a b c") == "a_b_c"


def test_sort_priority_then_created_at():
    a = _i("A", 3, datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = _i("B", 1, datetime(2026, 2, 1, tzinfo=timezone.utc))
    c = _i("C", 1, datetime(2026, 1, 5, tzinfo=timezone.utc))
    d = _i("D", None, datetime(2025, 1, 1, tzinfo=timezone.utc))
    sorted_issues = [i.identifier for i in sort_for_dispatch([a, b, c, d])]
    # Priority 1 (C, B) before priority 3 (A); null priority (D) last.
    assert sorted_issues == ["C", "B", "A", "D"]


def test_priority_zero_treated_as_null():
    a = _i("A", 0, datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = _i("B", 4, datetime(2026, 2, 1, tzinfo=timezone.utc))
    out = [i.identifier for i in sort_for_dispatch([a, b])]
    assert out == ["B", "A"]


def test_coerce_priority():
    assert coerce_priority(2) == 2
    assert coerce_priority(2.0) == 2
    assert coerce_priority("nope") is None
    assert coerce_priority(True) is None


def test_normalize_labels_lowercases():
    assert normalize_labels(["Backend", {"name": "Bug"}, 42]) == ("backend", "bug")


def test_parse_iso_timestamp_handles_z():
    ts = parse_iso_timestamp("2026-02-24T20:15:30Z")
    assert ts is not None
    assert ts.tzinfo is not None
    assert parse_iso_timestamp(None) is None
    assert parse_iso_timestamp("not a timestamp") is None
