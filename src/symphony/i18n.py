"""TUI string localization.

A minimal i18n layer for the Kanban TUI. Tracker state names, ticket titles,
and labels in `tracker.state_descriptions` come from user data and are not
translated — only the chrome (column placeholder, header / footer field
labels, card meta verbs) is.

Add a new language by appending a key to `STRINGS` and adding the same
keys you find under `STRINGS["en"]`. Keys that go untranslated fall back
to the English value silently — never raise on a missing key, since a
missing string would crash the TUI on every render.

Tech-y tokens that look the same across locales (`in=`, `out=`, `total=`,
`agent=`, `P1`, `#label`) deliberately do not pass through this layer.
"""

from __future__ import annotations

import os
from typing import Final

DEFAULT_LANGUAGE: Final[str] = "en"

# Environment variable that overrides `tui.language` from WORKFLOW.md.
# Useful for flipping the chrome locale per-session without editing the
# shared workflow file (e.g. one operator on a multi-operator board).
LANGUAGE_ENV_VAR: Final[str] = "SYMPHONY_LANG"

# All keys must exist in the English map. Other locales may omit keys,
# in which case `t()` falls back to English.
STRINGS: Final[dict[str, dict[str, str]]] = {
    "en": {
        # header
        "header.agent": "agent=",
        "header.tracker": "tracker=",
        "header.workflow": "workflow=",
        "header.running": "running=",
        "header.retrying": "retrying=",
        "header.generated_at": "generated_at",
        # footer
        "footer.tokens": "tokens",
        "footer.runtime": "runtime=",
        "footer.rate_limits": "rate-limits=",
        # column
        "column.empty": "— empty —",
        # card meta
        "card.turn": "turn",
        "card.retry": "retry #",
        "card.blocked_by": "blocked by",
        # language switch hint shown in the header
        "header.lang": "lang=",
        "header.lang_hint": "(SYMPHONY_LANG=ko or set tui.language in WORKFLOW.md)",
    },
    "ko": {
        # header
        "header.agent": "에이전트=",
        "header.tracker": "트래커=",
        "header.workflow": "워크플로=",
        "header.running": "실행=",
        "header.retrying": "재시도=",
        "header.generated_at": "생성시각",
        # footer
        "footer.tokens": "토큰",
        "footer.runtime": "실행시간=",
        "footer.rate_limits": "API제한=",
        # column
        "column.empty": "— 비어있음 —",
        # card meta
        "card.turn": "턴",
        "card.retry": "재시도 #",
        "card.blocked_by": "차단:",
        # language switch hint shown in the header
        "header.lang": "언어=",
        "header.lang_hint": "(SYMPHONY_LANG=en 또는 WORKFLOW.md tui.language 수정 후 재시작)",
    },
}

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = tuple(STRINGS.keys())


def t(key: str, language: str | None = None) -> str:
    """Look up a localized string. Falls back to English on missing key."""
    lang = (language or DEFAULT_LANGUAGE).lower()
    if lang not in STRINGS:
        lang = DEFAULT_LANGUAGE
    table = STRINGS[lang]
    if key in table:
        return table[key]
    # Fall back to English; if even that is missing, return the raw key
    # so a missing translation surfaces obviously rather than blanking out.
    return STRINGS[DEFAULT_LANGUAGE].get(key, key)


def normalize_language(value: str | None) -> str:
    """Coerce a config value into a supported language code, with EN fallback."""
    if not isinstance(value, str):
        return DEFAULT_LANGUAGE
    candidate = value.strip().lower()
    if candidate in STRINGS:
        return candidate
    # Common aliases so `Korean` / `KO_KR` / `kr` all map to `ko`.
    aliases = {
        "english": "en",
        "en_us": "en",
        "en-us": "en",
        "korean": "ko",
        "kr": "ko",
        "ko_kr": "ko",
        "ko-kr": "ko",
    }
    return aliases.get(candidate, DEFAULT_LANGUAGE)


def resolve_language(config_value: str | None) -> str:
    """Effective TUI language. ENV var (SYMPHONY_LANG) wins over config."""
    env_value = os.environ.get(LANGUAGE_ENV_VAR, "").strip()
    if env_value:
        return normalize_language(env_value)
    return normalize_language(config_value)
