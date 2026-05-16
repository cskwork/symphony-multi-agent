"""SPEC §4.1.1 — normalized Issue domain model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class BlockerRef:
    id: str | None
    identifier: str | None
    state: str | None


@dataclass(frozen=True)
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None
    priority: int | None
    state: str
    branch_name: str | None = None
    url: str | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)
    blocked_by: tuple[BlockerRef, ...] = field(default_factory=tuple)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    agent_kind: str | None = None

    def to_template_dict(self) -> dict[str, Any]:
        """§12.2 — convert keys to strings, preserve nested arrays/maps."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description or "",
            "priority": self.priority,
            "state": self.state,
            "branch_name": self.branch_name or "",
            "url": self.url or "",
            "labels": list(self.labels),
            "blocked_by": [
                {"id": b.id, "identifier": b.identifier, "state": b.state}
                for b in self.blocked_by
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "agent_kind": self.agent_kind or "",
        }


_WORKSPACE_KEY_INVALID = re.compile(r"[^A-Za-z0-9._-]")
_IDENTIFIER_REGISTRATION_SUFFIX = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")


def workspace_key(identifier: str) -> str:
    """§4.2, §9.5 — sanitize identifier for filesystem use."""
    return _WORKSPACE_KEY_INVALID.sub("_", identifier)


def normalize_state(state: str | None) -> str:
    """§4.2 — compare states after lowercase."""
    return (state or "").lower()


def parse_iso_timestamp(value: Any) -> datetime | None:
    """§11.3 — parse ISO-8601 timestamps."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def coerce_priority(value: Any) -> int | None:
    """§11.3 — non-integers become null."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def registration_order_key(issue: Issue) -> tuple[int, str, int, float, str]:
    """Stable ticket-registration order.

    Trackers commonly expose human IDs like ``OLV-061`` / ``OLV-131`` where
    the trailing number is the registration sequence. Use that before mutable
    fields such as priority or updated_at, so editing an old ticket cannot let
    newer work jump the queue.
    """
    identifier = issue.identifier or issue.id
    created_ts = (
        issue.created_at.timestamp()
        if issue.created_at is not None
        else float("inf")
    )
    match = _IDENTIFIER_REGISTRATION_SUFFIX.match(identifier)
    if match is not None:
        return (
            0,
            match.group("prefix").casefold(),
            int(match.group("number")),
            created_ts,
            identifier.casefold(),
        )
    return (1, "", 0, created_ts, identifier.casefold())


def normalize_labels(labels: Any) -> tuple[str, ...]:
    """§11.3 — labels lowercased."""
    if not isinstance(labels, list):
        return ()
    out: list[str] = []
    for item in labels:
        if isinstance(item, str):
            out.append(item.lower())
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            out.append(item["name"].lower())
    return tuple(out)


def sort_for_dispatch(issues: list[Issue]) -> list[Issue]:
    """§8.2 — ticket-registration order, with created_at fallback."""

    return sorted(issues, key=registration_order_key)
