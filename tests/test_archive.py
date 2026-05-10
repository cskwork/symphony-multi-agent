"""Unit tests for `symphony.archive.select_archivable`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from symphony.archive import select_archivable
from symphony.issue import Issue


def _issue(
    identifier: str,
    *,
    state: str = "Done",
    updated_at: datetime | None = None,
) -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"title {identifier}",
        description=None,
        priority=None,
        state=state,
        updated_at=updated_at,
    )


def test_returns_empty_when_disabled() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    issues = [_issue("A", updated_at=now - timedelta(days=365))]
    assert select_archivable(
        issues,
        terminal_states=["Done", "Archive"],
        archive_state="Archive",
        archive_after_days=0,
        now=now,
    ) == []


def test_archives_done_older_than_threshold() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    fresh = _issue("FRESH", updated_at=now - timedelta(days=10))
    stale = _issue("STALE", updated_at=now - timedelta(days=31))
    out = select_archivable(
        [fresh, stale],
        terminal_states=["Done", "Archive"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    )
    assert [i.identifier for i in out] == ["STALE"]


def test_skips_already_archived() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    archived = _issue(
        "OLD", state="Archive", updated_at=now - timedelta(days=400)
    )
    assert select_archivable(
        [archived],
        terminal_states=["Done", "Archive"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    ) == []


def test_skips_active_state_issues() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    in_progress = _issue(
        "ACTIVE", state="In Progress", updated_at=now - timedelta(days=400)
    )
    assert select_archivable(
        [in_progress],
        terminal_states=["Done", "Archive"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    ) == []


def test_skips_when_updated_at_missing() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    no_ts = _issue("NOTS", updated_at=None)
    assert select_archivable(
        [no_ts],
        terminal_states=["Done"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    ) == []


def test_naive_datetime_treated_as_utc() -> None:
    """File-tracker writes naive `Z`-suffixed strings; ensure they compare cleanly."""
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    naive_old = datetime(2025, 1, 1)  # ~16 months ago, naive
    issue = _issue("NAIVE", updated_at=naive_old)
    out = select_archivable(
        [issue],
        terminal_states=["Done"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    )
    assert [i.identifier for i in out] == ["NAIVE"]


def test_boundary_exactly_at_threshold_archives() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    edge = _issue("EDGE", updated_at=now - timedelta(days=30))
    out = select_archivable(
        [edge],
        terminal_states=["Done"],
        archive_state="Archive",
        archive_after_days=30,
        now=now,
    )
    assert [i.identifier for i in out] == ["EDGE"]
