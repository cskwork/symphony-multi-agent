"""File-based Kanban tracker (SPEC §11 — non-Linear adapter).

Each ticket is one Markdown file under `tracker.board_root` with YAML front
matter that holds tracker fields. The Markdown body is the description.

Format:

    ---
    id: DEV-001
    title: Fix the foo
    state: Todo
    priority: 2
    labels: [backend, bug]
    blocked_by:
      - identifier: DEV-099
        state: Todo
    created_at: 2026-05-08T10:00:00Z
    updated_at: 2026-05-08T10:00:00Z
    ---

    Description body in Markdown...

Conventions:
- `id` and `identifier` are the same value (filesystem-friendly key).
- `state` strings are matched against `tracker.active_states` /
  `tracker.terminal_states` after lower-casing (per §4.2 normalization).
- File names SHOULD be `<id>.md`, but any `*.md` file is scanned.
- The orchestrator only reads. Ticket writes (state transitions, comments)
  are done by the coding agent — see §11.5 — typically by overwriting the
  ticket file via its built-in shell/file tools.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import LinearUnknownPayload, SymphonyError
from .issue import (
    BlockerRef,
    Issue,
    coerce_priority,
    normalize_labels,
    normalize_state,
    parse_iso_timestamp,
)
from .workflow import TrackerConfig


_FRONT_MATTER_DELIM = "---"


# ---------------------------------------------------------------------------
# Parsing / serialization
# ---------------------------------------------------------------------------


def parse_ticket_file(path: Path) -> tuple[dict[str, Any], str]:
    """Return (front_matter_dict, body_text)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIM:
        return {}, text.rstrip()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FRONT_MATTER_DELIM)
    except StopIteration as exc:
        raise SymphonyError(
            "ticket front matter not terminated", path=str(path)
        ) from exc
    front_text = "\n".join(lines[1:end])
    try:
        parsed = yaml.safe_load(front_text)
    except yaml.YAMLError as exc:
        raise SymphonyError(
            "invalid YAML front matter", path=str(path), error=str(exc)
        ) from exc
    if parsed is None:
        front: dict[str, Any] = {}
    elif not isinstance(parsed, dict):
        raise SymphonyError("ticket front matter must be a map", path=str(path))
    else:
        front = parsed
    body = "\n".join(lines[end + 1 :]).strip()
    return front, body


def issue_from_file(path: Path) -> Issue | None:
    """Return None when the file lacks the required fields."""
    front, body = parse_ticket_file(path)
    raw_id = front.get("id") or front.get("identifier")
    title = front.get("title")
    state = front.get("state")
    if not (raw_id and title and state):
        return None
    identifier = str(raw_id)
    blockers = _parse_blockers(front.get("blocked_by"))
    return Issue(
        id=identifier,
        identifier=identifier,
        title=str(title),
        description=body or None,
        priority=coerce_priority(front.get("priority")),
        state=str(state),
        branch_name=str(front.get("branch_name") or "") or None,
        url=str(front.get("url") or "") or None,
        labels=normalize_labels(front.get("labels") or []),
        blocked_by=tuple(blockers),
        created_at=parse_iso_timestamp(front.get("created_at"))
        or parse_iso_timestamp(_file_ctime_iso(path)),
        updated_at=parse_iso_timestamp(front.get("updated_at"))
        or parse_iso_timestamp(_file_mtime_iso(path)),
    )


def _parse_blockers(value: Any) -> list[BlockerRef]:
    if not isinstance(value, list):
        return []
    out: list[BlockerRef] = []
    for entry in value:
        if isinstance(entry, str):
            out.append(BlockerRef(id=entry, identifier=entry, state=None))
        elif isinstance(entry, dict):
            ident = entry.get("identifier") or entry.get("id")
            if ident is None:
                continue
            out.append(
                BlockerRef(
                    id=str(entry.get("id") or ident),
                    identifier=str(ident),
                    state=(str(entry["state"]) if isinstance(entry.get("state"), str) else None),
                )
            )
    return out


def _file_ctime_iso(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()


def _file_mtime_iso(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


def serialize_ticket(front: dict[str, Any], body: str) -> str:
    """Render a ticket file with stable key order."""
    ordered_keys = [
        "id",
        "identifier",
        "title",
        "state",
        "priority",
        "branch_name",
        "url",
        "labels",
        "blocked_by",
        "created_at",
        "updated_at",
    ]
    ordered = {k: front[k] for k in ordered_keys if k in front and front[k] is not None}
    for k, v in front.items():
        if k not in ordered and v is not None:
            ordered[k] = v
    yaml_text = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).rstrip()
    body_text = (body or "").rstrip()
    parts = [_FRONT_MATTER_DELIM, yaml_text, _FRONT_MATTER_DELIM]
    if body_text:
        parts.append("")
        parts.append(body_text)
    return "\n".join(parts) + "\n"


def write_ticket_atomic(path: Path, front: dict[str, Any], body: str) -> None:
    """Atomic write: temp file in same dir + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialize_ticket(front, body))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# TrackerClient implementation
# ---------------------------------------------------------------------------


class FileBoardTracker:
    """Adapter over a directory of Markdown ticket files."""

    def __init__(self, tracker: TrackerConfig) -> None:
        if tracker.board_root is None:
            raise LinearUnknownPayload("board_root not configured")
        self._root = tracker.board_root.resolve()
        self._active = {s.lower() for s in tracker.active_states}
        self._terminal = {s.lower() for s in tracker.terminal_states}
        self._root.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        return None

    def __enter__(self) -> "FileBoardTracker":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @property
    def board_root(self) -> Path:
        return self._root

    # §11.1.1
    def fetch_candidate_issues(self) -> list[Issue]:
        return [
            i
            for i in self._scan_all()
            if normalize_state(i.state) in self._active
            and normalize_state(i.state) not in self._terminal
        ]

    # §11.1.2
    def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        wanted = {s.lower() for s in state_names if s}
        if not wanted:
            return []
        return [i for i in self._scan_all() if normalize_state(i.state) in wanted]

    # §11.1.3
    def fetch_issue_states_by_ids(self, ids: Iterable[str]) -> list[Issue]:
        targets = {i for i in ids if i}
        if not targets:
            return []
        out: list[Issue] = []
        for issue in self._scan_all():
            if issue.id in targets:
                out.append(
                    Issue(
                        id=issue.id,
                        identifier=issue.identifier,
                        title=issue.title,
                        description=None,
                        priority=None,
                        state=issue.state,
                    )
                )
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _scan_all(self) -> list[Issue]:
        out: list[Issue] = []
        for path in sorted(self._root.glob("*.md")):
            try:
                issue = issue_from_file(path)
            except SymphonyError:
                continue
            if issue is not None:
                out.append(issue)
        return _hydrate_blocker_states(out)

    # ------------------------------------------------------------------
    # convenience helpers used by board CLI / agent tool
    # ------------------------------------------------------------------

    def find_path(self, identifier: str) -> Path | None:
        candidate = self._root / f"{identifier}.md"
        if candidate.exists():
            return candidate
        for path in self._root.glob("*.md"):
            try:
                front, _ = parse_ticket_file(path)
            except SymphonyError:
                continue
            raw_id = front.get("id") or front.get("identifier")
            if raw_id and str(raw_id) == identifier:
                return path
        return None

    def transition(self, identifier: str, new_state: str) -> Path:
        path = self.find_path(identifier)
        if path is None:
            raise SymphonyError("ticket not found", identifier=identifier)
        front, body = parse_ticket_file(path)
        front["state"] = new_state
        front["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_ticket_atomic(path, front, body)
        return path

    def update_state(self, issue: Issue, target_state: str) -> None:
        """TrackerClient protocol mutation hook (delegates to `transition`)."""
        self.transition(issue.identifier, target_state)

    def create(
        self,
        *,
        identifier: str,
        title: str,
        state: str = "Todo",
        priority: int | None = None,
        labels: list[str] | None = None,
        description: str = "",
    ) -> Path:
        path = self._root / f"{identifier}.md"
        if path.exists():
            raise SymphonyError("ticket already exists", identifier=identifier)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        front: dict[str, Any] = {
            "id": identifier,
            "identifier": identifier,
            "title": title,
            "state": state,
            "priority": priority,
            "labels": list(labels or []),
            "created_at": now,
            "updated_at": now,
        }
        write_ticket_atomic(path, front, description)
        return path


def _hydrate_blocker_states(issues: list[Issue]) -> list[Issue]:
    current_state_by_id = {issue.identifier: issue.state for issue in issues}
    hydrated: list[Issue] = []
    for issue in issues:
        blockers: list[BlockerRef] = []
        changed = False
        for blocker in issue.blocked_by:
            key = blocker.identifier or blocker.id
            current_state = current_state_by_id.get(key or "")
            if current_state is not None and current_state != blocker.state:
                blockers.append(replace(blocker, state=current_state))
                changed = True
            else:
                blockers.append(blocker)
        if changed:
            hydrated.append(replace(issue, blocked_by=tuple(blockers)))
        else:
            hydrated.append(issue)
    return hydrated
