"""TUI helper coverage \u2014 narrow tests for the silence-threshold logic.

Full TUI rendering is out of scope (rich.Live needs a TTY); these tests
exercise the pure helpers that decide the badge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from symphony.tui import (
    SILENT_THRESHOLD_S,
    _CardStatus,
    _parse_iso,
    _silent_seconds,
)


def test_parse_iso_handles_z_suffix() -> None:
    parsed = _parse_iso("2026-05-09T23:51:21Z")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_iso_handles_offset_form() -> None:
    parsed = _parse_iso("2026-05-09T23:51:21+00:00")
    assert parsed is not None


def test_parse_iso_returns_none_for_garbage() -> None:
    assert _parse_iso("not a timestamp") is None
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso(12345) is None


def test_silent_seconds_none_when_no_event() -> None:
    assert _silent_seconds(None) is None


def test_silent_seconds_grows_with_age() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    s = _silent_seconds(past)
    assert s is not None
    # Tolerance for clock skew + scheduling jitter.
    assert 119.0 <= s <= 121.5


def test_silent_seconds_clamped_at_zero_for_future_timestamps() -> None:
    """If the orchestrator clock is ahead, we clamp at 0 rather than
    rendering a negative duration."""
    future = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert _silent_seconds(future) == 0.0


def test_silent_threshold_is_above_typical_warmup() -> None:
    """Sanity: the threshold must outlive the longest expected agent
    warm-up so healthy runs never trip it. 30 s covers Opus-4 cold start."""
    assert SILENT_THRESHOLD_S >= 30.0


def test_card_status_carries_last_event_at() -> None:
    """The dataclass contract \u2014 silence rendering depends on this field
    being populated from the API snapshot."""
    when = datetime(2026, 5, 9, 23, 51, 21, tzinfo=timezone.utc)
    s = _CardStatus(runtime="running", last_event_at=when)
    assert s.last_event_at == when
