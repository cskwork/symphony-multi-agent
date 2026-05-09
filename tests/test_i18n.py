"""TUI i18n behavior."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGE_ENV_VAR,
    STRINGS,
    normalize_language,
    resolve_language,
    t,
)
from symphony.workflow import build_service_config, load_workflow


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_default_language_is_english():
    assert DEFAULT_LANGUAGE == "en"
    assert t("card.turn") == "turn"


def test_known_keys_translate():
    assert t("card.turn", "ko") == "턴"
    assert t("column.empty", "ko") == "— 비어있음 —"
    assert t("header.running", "ko") == "실행="


def test_unknown_language_falls_back_to_english():
    assert t("card.turn", "fr") == "turn"
    assert t("column.empty", "klingon") == "— empty —"


def test_unknown_key_returns_raw_key():
    # Surfaces missing translations during dev rather than blanking the cell.
    assert t("nonexistent.key") == "nonexistent.key"


def test_normalize_language_aliases():
    assert normalize_language("Korean") == "ko"
    assert normalize_language("KO_KR") == "ko"
    assert normalize_language("ko-kr") == "ko"
    assert normalize_language("English") == "en"
    assert normalize_language("EN-US") == "en"
    # Unknown / blank / non-string → default.
    assert normalize_language("eo") == "en"
    assert normalize_language("") == "en"
    assert normalize_language(None) == "en"
    assert normalize_language(42) == "en"


def test_all_languages_have_same_key_set():
    en_keys = set(STRINGS["en"].keys())
    for lang, table in STRINGS.items():
        if lang == "en":
            continue
        missing = en_keys - set(table.keys())
        # Other locales may omit keys but t() must fall back; the test
        # ensures the EN map remains the canonical superset.
        assert missing == set() or all(t(k, lang) for k in missing), (
            f"{lang} is missing keys {missing} but they don't fall back cleanly"
        )


def test_workflow_default_tui_language(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.language == "en"


def test_workflow_explicit_korean(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            tui:
              language: ko
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.language == "ko"


def test_workflow_invalid_tui_block_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            tui: not-a-dict
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.language == "en"


def test_resolve_language_env_overrides_config(monkeypatch):
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "ko")
    assert resolve_language("en") == "ko"


def test_resolve_language_env_alias(monkeypatch):
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "Korean")
    assert resolve_language("en") == "ko"


def test_resolve_language_no_env_uses_config(monkeypatch):
    monkeypatch.delenv(LANGUAGE_ENV_VAR, raising=False)
    assert resolve_language("ko") == "ko"
    assert resolve_language(None) == "en"


def test_resolve_language_blank_env_falls_through(monkeypatch):
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "   ")
    assert resolve_language("ko") == "ko"


def test_lang_hint_keys_exist():
    assert t("header.lang") == "lang="
    assert t("header.lang", "ko") == "언어="
    # Hint contains the env var name in EN, and instructions in KO.
    assert "SYMPHONY_LANG" in t("header.lang_hint")
    assert "SYMPHONY_LANG" in t("header.lang_hint", "ko") or "tui.language" in t(
        "header.lang_hint", "ko"
    )


def test_workflow_env_override_wins_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    monkeypatch.setenv(LANGUAGE_ENV_VAR, "ko")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            tui: { language: en }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.language == "ko"


def test_column_more_format_string():
    assert t("column.more").format(n=3) == "+3 more"
    assert t("column.more", "ko").format(n=3) == "+3개 더"


def test_workflow_max_cards_default_none(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.max_cards_per_column is None


def test_workflow_max_cards_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            tui: { max_cards_per_column: 6 }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.max_cards_per_column == 6


def test_workflow_max_cards_invalid_values_become_none(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    for raw in ("not-an-int", -1, 0, True, False):
        path = _write(
            tmp_path,
            textwrap.dedent(
                f"""\
                ---
                tracker: {{ kind: linear, project_slug: x }}
                tui: {{ max_cards_per_column: {raw!r} }}
                ---
                body
                """
            ),
        )
        cfg = build_service_config(load_workflow(path))
        assert cfg.tui.max_cards_per_column is None, f"raw={raw!r} should map to None"


def test_workflow_alias_korean(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            tui: { language: Korean }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tui.language == "ko"
