"""Auto-archive helpers.

Pure functions split from the orchestrator so they're trivially unit-testable.
The orchestrator's tick loop calls `select_archivable` once per poll, hands
each returned `Issue` to `TrackerClient.update_state(issue, archive_state)`,
and logs failures without crashing the tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .issue import Issue, normalize_state


def select_archivable(
    issues: Iterable[Issue],
    *,
    terminal_states: Iterable[str],
    archive_state: str,
    archive_after_days: int,
    now: datetime | None = None,
) -> list[Issue]:
    """Issues that should be auto-archived this tick.

    An issue qualifies when:
      1. Its current state is terminal but NOT already the archive state.
      2. `updated_at` is at least `archive_after_days` old.
      3. Auto-archive is enabled (`archive_after_days > 0`).

    `updated_at` resetting on any tracker activity (comment, edit) is
    intentional — "stale" means "no one has touched this in N days".
    """
    if archive_after_days <= 0:
        return []
    archive_key = normalize_state(archive_state)
    terminal_keys = {normalize_state(s) for s in terminal_states}
    if archive_key in terminal_keys:
        terminal_keys.discard(archive_key)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=archive_after_days)
    out: list[Issue] = []
    for issue in issues:
        state_key = normalize_state(issue.state)
        if state_key not in terminal_keys:
            continue
        if issue.updated_at is None:
            continue
        # Naive datetimes get treated as UTC so file-tracker timestamps
        # (which we serialize as `Z`-suffixed) compare cleanly with the
        # tz-aware cutoff. Linear always returns tz-aware values.
        ts = issue.updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= cutoff:
            out.append(issue)
    return out
