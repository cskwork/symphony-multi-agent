"""C5 — wiki integrity sweep extracted from the Learn prompt.

The sweep walks `docs/llm-wiki/` (or any caller-supplied root) and surfaces
the same four classes of drift the Learn agent used to check by hand:

1. Duplicate slugs (`# Title` heading collides across files).
2. Orphan files (`docs/llm-wiki/*.md` with no matching `INDEX.md` row).
3. Missing files (INDEX rows pointing at files that no longer exist).
4. Stale entries (`**Last updated:** YYYY-MM-DD` more than 90 days old).

The first three are reported only — auto-merging duplicates is the kind of
edit that needs a human in the loop. Stale entries trigger an idempotent
append of ` (stale?)` to the matching INDEX summary cell so the human-
facing INDEX makes the rot visible.

Pure logic — file IO is concentrated in `_load_entries` and `_apply_*` so
unit tests can drive `sweep` against a temp tree without monkeypatching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

INDEX_FILENAME = "INDEX.md"
STALE_AFTER_DAYS = 90
STALE_MARKER = " (stale?)"

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_LAST_UPDATED_RE = re.compile(
    r"\*\*Last updated:\*\*\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)
# Match `| slug | summary | last touched |` rows; require at least one
# dash-only separator row to anchor onto the table. We accept the slug
# column verbatim — the wiki convention is `<topic-slug>` matching the
# file stem under docs/llm-wiki/.
_INDEX_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$"
)
_INDEX_SEPARATOR_RE = re.compile(r"^\|\s*-+\s*\|")


@dataclass(frozen=True)
class WikiEntry:
    """File-on-disk view of a wiki page."""

    path: Path
    slug: str  # filename stem
    title: str | None  # `# Title` heading text, None if missing
    last_updated: date | None  # parsed `**Last updated:** YYYY-MM-DD`


@dataclass(frozen=True)
class IndexRow:
    """One parsed row of `INDEX.md`."""

    line_no: int  # 1-based line index in INDEX.md (for stable mutations)
    slug: str
    summary: str
    last_touched: str
    raw_line: str


@dataclass(frozen=True)
class DuplicateSlug:
    title: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class StaleEntry:
    slug: str
    last_updated: date
    age_days: int


@dataclass(frozen=True)
class SweepReport:
    """Aggregated sweep findings.

    `mutations` records what the sweep wrote (only stale markers in this
    revision); empty in dry-run mode. Counts give CLI / log lines a quick
    summary without recomputing list lengths.
    """

    duplicates: tuple[DuplicateSlug, ...] = ()
    orphans: tuple[Path, ...] = ()
    missing_files: tuple[str, ...] = ()
    stale_entries: tuple[StaleEntry, ...] = ()
    mutations: tuple[tuple[Path, str], ...] = ()
    # Root scanned — useful in CLI output / structured logs.
    root: Path | None = None
    # Whether `INDEX.md` was present. False when the wiki root exists but
    # has no index yet (treated as no rows + every file is an orphan).
    index_present: bool = True

    def is_clean(self) -> bool:
        """True when no errors fired. Stale entries alone don't count."""
        return not (self.duplicates or self.orphans or self.missing_files)

    def summary_lines(self) -> list[str]:
        """Human-readable lines for CLI / log output."""
        out: list[str] = []
        root = str(self.root) if self.root is not None else "<unknown>"
        out.append(f"wiki-sweep: root={root}")
        if not self.index_present:
            out.append("  INDEX.md: missing")
        out.append(
            "  duplicates={d} orphans={o} missing_files={m} stale={s}".format(
                d=len(self.duplicates),
                o=len(self.orphans),
                m=len(self.missing_files),
                s=len(self.stale_entries),
            )
        )
        for dup in self.duplicates:
            joined = ", ".join(str(p) for p in dup.paths)
            out.append(f"  duplicate slug '{dup.title}': {joined}")
        for orphan in self.orphans:
            out.append(f"  orphan file (no INDEX row): {orphan}")
        for missing in self.missing_files:
            out.append(f"  missing file (INDEX row points nowhere): {missing}")
        for stale in self.stale_entries:
            out.append(
                f"  stale entry: {stale.slug} last updated "
                f"{stale.last_updated.isoformat()} ({stale.age_days} days)"
            )
        for path, action in self.mutations:
            out.append(f"  mutation: {action} -> {path}")
        return out


def sweep(
    root: Path,
    *,
    dry_run: bool = False,
    today: date | None = None,
) -> SweepReport:
    """Run all four checks against `root`. Pure orchestration.

    `today` is injectable to keep tests deterministic; defaults to
    `datetime.now(UTC).date()`.
    """
    today = today or datetime.now(timezone.utc).date()
    if not root.exists() or not root.is_dir():
        # Empty / missing root is a clean report — Symphony's auto-sweep
        # call site treats this as "nothing to do" without escalating.
        return SweepReport(root=root, index_present=False)

    entries = _load_entries(root)
    index_path = root / INDEX_FILENAME
    index_present = index_path.is_file()
    index_rows = _load_index_rows(index_path) if index_present else ()

    duplicates = _detect_duplicate_slugs(entries)
    orphans = _detect_orphans(entries, index_rows)
    missing_files = _detect_missing_files(root, index_rows)
    stale_entries = _detect_stale(entries, today=today)

    mutations: list[tuple[Path, str]] = []
    if not dry_run and stale_entries and index_present:
        mutated = _apply_stale_markers(index_path, index_rows, stale_entries)
        if mutated:
            mutations.append((index_path, f"marked {mutated} stale row(s)"))

    return SweepReport(
        duplicates=tuple(duplicates),
        orphans=tuple(orphans),
        missing_files=tuple(missing_files),
        stale_entries=tuple(stale_entries),
        mutations=tuple(mutations),
        root=root,
        index_present=index_present,
    )


# ---------------------------------------------------------------------------
# Loading — file IO concentrated here so the checks below stay pure.
# ---------------------------------------------------------------------------


def _load_entries(root: Path) -> list[WikiEntry]:
    """Read every `*.md` under root (top-level only, excluding INDEX)."""
    out: list[WikiEntry] = []
    for path in sorted(root.glob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append(
            WikiEntry(
                path=path,
                slug=path.stem,
                title=_parse_title(text),
                last_updated=_parse_last_updated(text),
            )
        )
    return out


def _load_index_rows(index_path: Path) -> tuple[IndexRow, ...]:
    """Parse INDEX.md table rows; tolerates leading prose / headers."""
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    rows: list[IndexRow] = []
    seen_separator = False
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if _INDEX_SEPARATOR_RE.match(raw_line):
            seen_separator = True
            continue
        if not seen_separator:
            continue
        match = _INDEX_ROW_RE.match(raw_line)
        if not match:
            continue
        slug = match.group(1).strip()
        # Skip header rows ("topic-slug" or other column labels). Real
        # slugs in this codebase use lowercase + hyphen; column headers
        # like "topic-slug" still match that pattern, so we instead key
        # off the file existing on disk in the orphan/missing checks.
        rows.append(
            IndexRow(
                line_no=line_no,
                slug=slug,
                summary=match.group(2).strip(),
                last_touched=match.group(3).strip(),
                raw_line=raw_line,
            )
        )
    return tuple(rows)


def _parse_title(text: str) -> str | None:
    match = _TITLE_RE.search(text)
    return match.group(1).strip() if match else None


def _parse_last_updated(text: str) -> date | None:
    match = _LAST_UPDATED_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Checks — pure functions over the loaded model.
# ---------------------------------------------------------------------------


def _detect_duplicate_slugs(entries: list[WikiEntry]) -> list[DuplicateSlug]:
    """Same `# Title` text across two or more files → merge candidate."""
    by_title: dict[str, list[Path]] = {}
    for entry in entries:
        if not entry.title:
            continue
        # Normalise whitespace so trivial formatting differences don't
        # mask a real duplicate, but keep case (titles are human prose).
        key = " ".join(entry.title.split())
        by_title.setdefault(key, []).append(entry.path)
    out: list[DuplicateSlug] = []
    for title, paths in by_title.items():
        if len(paths) >= 2:
            out.append(DuplicateSlug(title=title, paths=tuple(sorted(paths))))
    out.sort(key=lambda d: d.title)
    return out


def _detect_orphans(
    entries: list[WikiEntry], index_rows: tuple[IndexRow, ...]
) -> list[Path]:
    """Files on disk that no INDEX row references."""
    indexed_slugs = {row.slug for row in index_rows}
    orphans = [entry.path for entry in entries if entry.slug not in indexed_slugs]
    orphans.sort()
    return orphans


def _detect_missing_files(
    root: Path, index_rows: tuple[IndexRow, ...]
) -> list[str]:
    """INDEX rows whose slug has no matching file on disk."""
    on_disk = {p.stem for p in root.glob("*.md") if p.name != INDEX_FILENAME}
    missing = [row.slug for row in index_rows if row.slug not in on_disk]
    # Stable, dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for slug in missing:
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _detect_stale(
    entries: list[WikiEntry], *, today: date
) -> list[StaleEntry]:
    out: list[StaleEntry] = []
    for entry in entries:
        if entry.last_updated is None:
            continue
        age = (today - entry.last_updated).days
        if age > STALE_AFTER_DAYS:
            out.append(
                StaleEntry(slug=entry.slug, last_updated=entry.last_updated, age_days=age)
            )
    out.sort(key=lambda s: (s.slug,))
    return out


# ---------------------------------------------------------------------------
# Mutations — append ` (stale?)` to INDEX rows idempotently.
# ---------------------------------------------------------------------------


def _apply_stale_markers(
    index_path: Path,
    index_rows: tuple[IndexRow, ...],
    stale_entries: list[StaleEntry],
) -> int:
    """Append ` (stale?)` to the summary cell of matching INDEX rows.

    Idempotent — only writes when at least one row needs the marker and
    doesn't already carry it. Returns the number of rows mutated.
    """
    stale_slugs = {s.slug for s in stale_entries}
    rows_by_line = {row.line_no: row for row in index_rows if row.slug in stale_slugs}
    if not rows_by_line:
        return 0
    try:
        original = index_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    lines = original.splitlines(keepends=True)
    mutated_count = 0
    for line_no, row in rows_by_line.items():
        if STALE_MARKER in row.summary:
            continue
        # `line_no` is 1-based; lines list is 0-based.
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            continue
        old_line = lines[idx]
        new_summary = f"{row.summary}{STALE_MARKER}"
        # Rebuild the row preserving trailing newline. We use the parsed
        # cells rather than regex-replacing inside the raw line so a
        # summary that happens to contain the slug doesn't get garbled.
        new_line = (
            f"| {row.slug} | {new_summary} | {row.last_touched} |"
        )
        if old_line.endswith("\n"):
            new_line += "\n"
        lines[idx] = new_line
        mutated_count += 1
    if mutated_count == 0:
        return 0
    index_path.write_text("".join(lines), encoding="utf-8")
    return mutated_count


__all__ = [
    "DuplicateSlug",
    "IndexRow",
    "STALE_AFTER_DAYS",
    "STALE_MARKER",
    "StaleEntry",
    "SweepReport",
    "WikiEntry",
    "sweep",
]
