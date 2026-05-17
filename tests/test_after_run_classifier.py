"""B1: live-coverage for the WORKFLOW.md `after_run` marker classifier.

The hook is a bash heredoc, so we exercise it through `bash -c` against a
temp git repo. Without this, the only verification was indirect (a real
claude/codex turn happens to omit a test file). The live 2026-05-17 demo
showed claude doing strict TDD — adding src + test together — which
means the marker never naturally fires and the prompt half (review.md
scans wip subjects for `[no-test]`) could regress without anyone
noticing.

The classifier under test is the section inside WORKFLOW.md's `after_run`
that picks a wip commit subject prefix. We extract it into a self-contained
shell snippet so the test is hermetic.
"""

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

# A trimmed copy of the WORKFLOW.md classifier — same logic, no I/O on the
# staged diff (we feed STAGED_FILES directly). If you change the classifier
# in WORKFLOW.md, mirror that change here.
_CLASSIFIER = textwrap.dedent(
    r"""
    set -uo pipefail
    PROD_CHANGED=0
    TESTS_CHANGED=0
    SCOPE_EXPAND=0
    SCOPE_FILES=""
    if [ -n "${SYMPHONY_REWIND_SCOPE:-}" ]; then
      SCOPE_FILES="$(printf '%s' "$SYMPHONY_REWIND_SCOPE" \
        | tr ',' '\n' \
        | sed -n 's/.*"file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    fi
    NL=$(printf '\nx'); NL=${NL%x}
    OLDIFS="$IFS"
    IFS="$NL"
    for f in $STAGED_FILES; do
      [ -n "$f" ] || continue
      case "$f" in
        tests/*|*_test.py|*.test.ts|*.test.tsx|*_test.go) TESTS_CHANGED=1 ;;
      esac
      case "$f" in
        tests/*|docs/*|kanban*|.symphony/*|*.md|LICENSE|LICENSE.*|NOTICE|CHANGELOG*|README*|AGENTS.md|GEMINI.md) : ;;
        *) PROD_CHANGED=1 ;;
      esac
      if [ -n "$SCOPE_FILES" ] && [ "$SCOPE_EXPAND" = 0 ]; then
        in_scope=0
        for s in $SCOPE_FILES; do
          [ "$f" = "$s" ] && in_scope=1 && break
        done
        [ "$in_scope" = 0 ] && SCOPE_EXPAND=1
      fi
    done
    IFS="$OLDIFS"
    PREFIX=""
    [ "$PROD_CHANGED" = 1 ] && [ "$TESTS_CHANGED" = 0 ] && PREFIX="${PREFIX}[no-test]"
    [ -n "${SYMPHONY_REWIND_SCOPE:-}" ] && [ "$SCOPE_EXPAND" = 1 ] && PREFIX="${PREFIX}[scope-expand]"
    printf '%s' "$PREFIX"
    """
)


def _classify(staged: str, *, rewind_scope: str | None = None) -> str:
    env = {**os.environ, "STAGED_FILES": staged}
    if rewind_scope is not None:
        env["SYMPHONY_REWIND_SCOPE"] = rewind_scope
    out = subprocess.run(
        ["bash", "-c", _CLASSIFIER],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_prod_only_change_gets_no_test_marker():
    """A production-code change without a paired test → `[no-test]`."""
    assert _classify("src/foo.py") == "[no-test]"


def test_paired_test_clears_marker():
    """Adding src + matching test in the same diff → no marker (TDD)."""
    assert _classify("src/foo.py\ntests/test_foo.py") == ""


def test_test_in_root_pattern_clears_marker():
    """`*_test.go` / `*.test.ts` / `*_test.py` also count as tests."""
    for staged in (
        "internal/foo.go\ninternal/foo_test.go",
        "src/foo.ts\nsrc/foo.test.ts",
        "lib/foo.py\nlib/foo_test.py",
    ):
        assert _classify(staged) == "", f"failed for: {staged!r}"


def test_docs_only_carve_out_clears_marker():
    """README / CHANGELOG / *.md / LICENSE never count as production."""
    for staged in (
        "README.md",
        "CHANGELOG.md",
        "docs/feature.md",
        "LICENSE",
        "LICENSE.MIT",
        "NOTICE",
        "AGENTS.md",
        "docs/symphony-prompts/file/stages/plan.md",
    ):
        assert _classify(staged) == "", f"failed for: {staged!r}"


def test_scope_expand_marker_fires_on_out_of_scope_edit():
    """Rewind dispatch + diff touches a file outside scope → `[scope-expand]`."""
    scope = '[{"file": "src/auth.py", "line": 10, "fix": "validate", "severity": "HIGH"}]'
    assert _classify(
        "src/auth.py\ntests/test_auth.py\nsrc/leaked.py",
        rewind_scope=scope,
    ) == "[scope-expand]"


def test_markers_stack():
    """Prod-only + rewind-out-of-scope can stack both prefixes."""
    scope = '[{"file": "src/only.py", "line": 1, "fix": "x", "severity": "HIGH"}]'
    out = _classify("src/elsewhere.py", rewind_scope=scope)
    assert "[no-test]" in out and "[scope-expand]" in out


def test_no_staged_files_yields_empty_prefix():
    assert _classify("") == ""


@pytest.mark.parametrize(
    "path",
    [
        "scripts/symphony-setup-worktree.sh",
        "pyproject.toml",
        "src/symphony/cli.py",
    ],
)
def test_production_code_without_test_pairs(path):
    """Various production paths trigger the marker absent a paired test."""
    assert _classify(path) == "[no-test]"
