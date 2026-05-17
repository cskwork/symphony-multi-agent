"""C5 — unit tests for `symphony.wiki_sweep`."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from symphony import wiki_sweep
from symphony.wiki_sweep import (
    STALE_AFTER_DAYS,
    STALE_MARKER,
    DuplicateSlug,
    StaleEntry,
    sweep,
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _write_entry(
    root: Path,
    slug: str,
    *,
    title: str,
    last_updated: str | None = None,
    body: str = "",
) -> Path:
    """Write a wiki entry shaped like the real `docs/llm-wiki/*.md` files."""
    parts = [f"# {title}", "", body]
    if last_updated is not None:
        parts.extend(["", f"**Last updated:** {last_updated} by TEST."])
    path = root / f"{slug}.md"
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return path


def _write_index(root: Path, rows: list[tuple[str, str, str]]) -> Path:
    """Write `INDEX.md` with a header + a separator + the given rows."""
    lines = [
        "# docs/llm-wiki index",
        "",
        "| topic-slug | summary | last touched |",
        "|------------|---------|--------------|",
    ]
    for slug, summary, last in rows:
        lines.append(f"| {slug} | {summary} | {last} |")
    path = root / "INDEX.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Missing / empty roots are no-ops, not errors
# ---------------------------------------------------------------------------


def test_missing_root_returns_clean_empty_report(tmp_path: Path) -> None:
    """`sweep` on a path that doesn't exist must not raise."""
    report = sweep(tmp_path / "does-not-exist")
    assert report.is_clean()
    assert report.index_present is False
    assert report.duplicates == ()
    assert report.orphans == ()


def test_empty_root_with_no_index(tmp_path: Path) -> None:
    """Empty root: clean, INDEX absent flag set."""
    report = sweep(tmp_path)
    assert report.is_clean()
    assert report.index_present is False


# ---------------------------------------------------------------------------
# Duplicate slug (collision on `# Title`)
# ---------------------------------------------------------------------------


def test_detects_duplicate_titles(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Same Topic")
    _write_entry(tmp_path, "beta", title="Same Topic")
    _write_index(tmp_path, [("alpha", "a", "2026-05-17"), ("beta", "b", "2026-05-17")])

    report = sweep(tmp_path, dry_run=True)
    assert len(report.duplicates) == 1
    dup = report.duplicates[0]
    assert isinstance(dup, DuplicateSlug)
    assert dup.title == "Same Topic"
    assert {p.stem for p in dup.paths} == {"alpha", "beta"}
    assert not report.is_clean()


def test_single_title_is_not_a_duplicate(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")
    _write_entry(tmp_path, "beta", title="Beta")
    _write_index(tmp_path, [("alpha", "a", "2026-05-17"), ("beta", "b", "2026-05-17")])

    report = sweep(tmp_path, dry_run=True)
    assert report.duplicates == ()


# ---------------------------------------------------------------------------
# Orphan files (no INDEX row)
# ---------------------------------------------------------------------------


def test_detects_orphan_file_without_index_row(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")
    _write_entry(tmp_path, "orphan-topic", title="Orphan")
    _write_index(tmp_path, [("alpha", "a", "2026-05-17")])

    report = sweep(tmp_path, dry_run=True)
    assert [p.stem for p in report.orphans] == ["orphan-topic"]
    assert not report.is_clean()


def test_no_index_means_every_file_is_orphan(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")
    _write_entry(tmp_path, "beta", title="Beta")

    report = sweep(tmp_path, dry_run=True)
    assert report.index_present is False
    assert {p.stem for p in report.orphans} == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Missing files (INDEX row, no file)
# ---------------------------------------------------------------------------


def test_detects_missing_files(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")
    _write_index(
        tmp_path,
        [("alpha", "a", "2026-05-17"), ("ghost", "missing", "2026-05-17")],
    )

    report = sweep(tmp_path, dry_run=True)
    assert report.missing_files == ("ghost",)
    assert not report.is_clean()


# ---------------------------------------------------------------------------
# Stale entries — idempotent ` (stale?)` append
# ---------------------------------------------------------------------------


def test_stale_appends_marker_idempotently(tmp_path: Path) -> None:
    today = date(2026, 5, 17)
    very_old = (today - timedelta(days=STALE_AFTER_DAYS + 10)).isoformat()
    fresh = today.isoformat()
    _write_entry(tmp_path, "old-topic", title="Old", last_updated=very_old)
    _write_entry(tmp_path, "new-topic", title="New", last_updated=fresh)
    _write_index(
        tmp_path,
        [("old-topic", "old summary", "2026-01-01"), ("new-topic", "fresh summary", "2026-05-17")],
    )

    report = sweep(tmp_path, dry_run=False, today=today)
    assert len(report.stale_entries) == 1
    stale = report.stale_entries[0]
    assert isinstance(stale, StaleEntry)
    assert stale.slug == "old-topic"
    assert stale.age_days > STALE_AFTER_DAYS
    # stale alone is not an error
    assert report.is_clean()
    # marker was written
    text = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert f"| old-topic | old summary{STALE_MARKER} |" in text
    assert "| new-topic | fresh summary |" in text

    # Second run: idempotent — no further mutation, but stale_entries still reported.
    report2 = sweep(tmp_path, dry_run=False, today=today)
    text2 = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert text == text2
    assert len(report2.stale_entries) == 1
    assert report2.mutations == ()


def test_dry_run_does_not_write_stale_marker(tmp_path: Path) -> None:
    today = date(2026, 5, 17)
    very_old = (today - timedelta(days=STALE_AFTER_DAYS + 5)).isoformat()
    _write_entry(tmp_path, "old-topic", title="Old", last_updated=very_old)
    _write_index(tmp_path, [("old-topic", "summary", "2024-01-01")])
    original = (tmp_path / "INDEX.md").read_text(encoding="utf-8")

    report = sweep(tmp_path, dry_run=True, today=today)
    assert len(report.stale_entries) == 1
    assert report.mutations == ()
    assert (tmp_path / "INDEX.md").read_text(encoding="utf-8") == original


def test_no_last_updated_is_not_stale(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")  # no Last updated line
    _write_index(tmp_path, [("alpha", "a", "2026-05-17")])
    report = sweep(tmp_path, dry_run=True, today=date(2030, 1, 1))
    assert report.stale_entries == ()


# ---------------------------------------------------------------------------
# Aggregate behaviour
# ---------------------------------------------------------------------------


def test_summary_lines_includes_root_and_counts(tmp_path: Path) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha")
    _write_index(tmp_path, [("alpha", "a", "2026-05-17"), ("ghost", "g", "2026-05-17")])
    report = sweep(tmp_path, dry_run=True)
    lines = report.summary_lines()
    assert any(str(tmp_path) in line for line in lines)
    assert any("missing_files=1" in line for line in lines)


def test_is_clean_distinguishes_stale_from_errors(tmp_path: Path) -> None:
    today = date(2026, 5, 17)
    _write_entry(
        tmp_path,
        "old",
        title="Old",
        last_updated=(today - timedelta(days=STALE_AFTER_DAYS + 1)).isoformat(),
    )
    _write_index(tmp_path, [("old", "summary", "2024-01-01")])
    report = sweep(tmp_path, dry_run=True, today=today)
    assert report.stale_entries  # stale found
    assert report.is_clean()  # but no errors


@pytest.mark.parametrize("dry_run", [False, True])
def test_sweep_does_not_mutate_index_when_clean(tmp_path: Path, dry_run: bool) -> None:
    _write_entry(tmp_path, "alpha", title="Alpha", last_updated="2026-05-17")
    _write_index(tmp_path, [("alpha", "summary", "2026-05-17")])
    original = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    report = sweep(tmp_path, dry_run=dry_run, today=date(2026, 5, 17))
    assert report.is_clean()
    assert (tmp_path / "INDEX.md").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# CLI exit codes — make sure errors propagate, stale-only does not.
# ---------------------------------------------------------------------------


def test_cli_wiki_sweep_exit_code_for_error(tmp_path: Path) -> None:
    """Run the CLI directly. Error condition (orphan) → exit 1."""
    from symphony import cli

    _write_entry(tmp_path, "orphan", title="Orphan")  # no INDEX

    rc = cli.main(["wiki-sweep", "--root", str(tmp_path), "--dry-run"])
    assert rc == 1


def test_cli_wiki_sweep_exit_code_clean(tmp_path: Path) -> None:
    from symphony import cli

    _write_entry(tmp_path, "alpha", title="Alpha", last_updated="2026-05-17")
    _write_index(tmp_path, [("alpha", "summary", "2026-05-17")])
    rc = cli.main(["wiki-sweep", "--root", str(tmp_path), "--dry-run"])
    assert rc == 0


def test_cli_wiki_sweep_handles_missing_root(tmp_path: Path) -> None:
    from symphony import cli

    rc = cli.main(["wiki-sweep", "--root", str(tmp_path / "does-not-exist")])
    # Empty root is clean per sweep contract.
    assert rc == 0


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


def test_public_api_exports() -> None:
    """Guard against accidental API removals."""
    for name in (
        "sweep",
        "SweepReport",
        "DuplicateSlug",
        "StaleEntry",
        "WikiEntry",
        "IndexRow",
        "STALE_AFTER_DAYS",
        "STALE_MARKER",
    ):
        assert hasattr(wiki_sweep, name), f"missing public name: {name}"
