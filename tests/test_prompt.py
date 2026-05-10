"""SPEC §17.1 — strict prompt template behavior."""

from __future__ import annotations

import pytest

from symphony.errors import TemplateRenderError, TemplateParseError
from symphony.issue import BlockerRef, Issue
from symphony.i18n import doc_language_preamble
from symphony.prompt import (
    build_continuation_prompt,
    build_first_turn_prompt,
    build_prompt_env,
    render,
)


def _issue() -> Issue:
    return Issue(
        id="abc123",
        identifier="MT-649",
        title="Fix bug",
        description="Detail",
        priority=2,
        state="In Progress",
        labels=("backend", "bug"),
        blocked_by=(BlockerRef(id="z", identifier="MT-1", state="Done"),),
    )


def test_render_basic():
    env = build_prompt_env(_issue(), attempt=None)
    out = render("ID={{ issue.identifier }} title={{ issue.title }}", env)
    assert out == "ID=MT-649 title=Fix bug"


def test_render_attempt_null_and_int():
    env_first = build_prompt_env(_issue(), attempt=None)
    env_retry = build_prompt_env(_issue(), attempt=2)
    template = "{% if attempt %}retry={{ attempt }}{% else %}first{% endif %}"
    assert render(template, env_first) == "first"
    assert render(template, env_retry) == "retry=2"


def test_render_unknown_variable_strict():
    env = build_prompt_env(_issue(), attempt=None)
    with pytest.raises(TemplateRenderError):
        render("{{ unknown_var }}", env)


def test_render_unknown_filter_strict():
    env = build_prompt_env(_issue(), attempt=None)
    with pytest.raises(TemplateRenderError):
        render("{{ issue.title | bogus }}", env)


def test_render_for_loop_labels():
    env = build_prompt_env(_issue(), attempt=None)
    out = render("[{% for l in issue.labels %}{{ l }} {% endfor %}]", env).strip()
    assert out.startswith("[backend bug")


def test_render_filter_default_and_join():
    env = build_prompt_env(_issue(), attempt=None)
    out = render("{{ issue.labels | join: \" / \" }}", env)
    assert out == "backend / bug"


def test_render_blockers_iteration():
    env = build_prompt_env(_issue(), attempt=None)
    out = render(
        "{% for b in issue.blocked_by %}{{ b.identifier }}={{ b.state }};{% endfor %}",
        env,
    )
    assert out == "MT-1=Done;"


def test_template_parse_error_on_unclosed_tag():
    env = build_prompt_env(_issue(), attempt=None)
    with pytest.raises(TemplateParseError):
        render("{% if attempt %}forever", env)


def test_build_prompt_env_default_language_is_english():
    """Callers that pass no language get the safe EN default — preserves
    behavior for any existing test/code that calls build_prompt_env(...)
    with the original two-argument signature."""
    env = build_prompt_env(_issue(), attempt=None)
    assert env["language"] == "en"


def test_build_prompt_env_explicit_korean():
    env = build_prompt_env(_issue(), attempt=None, language="ko")
    assert env["language"] == "ko"


def test_build_prompt_env_unknown_language_falls_back_to_english():
    """A misconfigured `tui.language: fr` must NOT crash worker dispatch —
    silently coerce to EN so the agent still gets a coherent prompt."""
    env = build_prompt_env(_issue(), attempt=None, language="fr")
    assert env["language"] == "en"


def test_build_prompt_env_alias_korean():
    # Aliases (Korean / KO_KR / kr) all collapse to the canonical 'ko'.
    env = build_prompt_env(_issue(), attempt=None, language="Korean")
    assert env["language"] == "ko"


def test_render_can_branch_on_language():
    """WORKFLOW.md authors can write {% if language == 'ko' %}…{% endif %}
    to keep one bilingual file instead of forking the prompt template."""
    template = "{% if language == 'ko' %}안녕{% else %}hi{% endif %}"
    env_en = build_prompt_env(_issue(), attempt=None, language="en")
    env_ko = build_prompt_env(_issue(), attempt=None, language="ko")
    assert render(template, env_en) == "hi"
    assert render(template, env_ko) == "안녕"


# ---------------------------------------------------------------------------
# build_first_turn_prompt / build_continuation_prompt — wired by orchestrator
# ---------------------------------------------------------------------------


def test_first_turn_prompt_prepends_english_preamble_by_default():
    body = "Body for {{ issue.identifier }}, turn {{ turn_number }}/{{ max_turns }}."
    prompt, env = build_first_turn_prompt(
        prompt_template=body,
        issue=_issue(),
        attempt=None,
        language="en",
        max_turns=20,
    )
    assert prompt.startswith(doc_language_preamble("en"))
    # Two newlines visually separate preamble from rendered body.
    assert "\n\n" in prompt
    assert "Body for MT-649, turn 1/20." in prompt
    assert env["language"] == "en"
    assert env["turn_number"] == 1
    assert env["max_turns"] == 20


def test_first_turn_prompt_prepends_korean_preamble():
    body = "본문 {{ issue.identifier }}"
    prompt, env = build_first_turn_prompt(
        prompt_template=body,
        issue=_issue(),
        attempt=None,
        language="ko",
        max_turns=10,
    )
    # Korean preamble is concrete enough to spot-check without coupling
    # the test to the full sentence.
    assert prompt.startswith(doc_language_preamble("ko"))
    assert "한국어" in prompt
    assert "본문 MT-649" in prompt
    assert env["language"] == "ko"


def test_first_turn_prompt_unknown_language_falls_back_to_english():
    """A misconfigured `tui.language: fr` must NOT halt dispatch — agent
    still gets a coherent EN preamble + rendered body."""
    prompt, env = build_first_turn_prompt(
        prompt_template="Hi",
        issue=_issue(),
        attempt=None,
        language="fr",
        max_turns=5,
    )
    assert prompt.startswith(doc_language_preamble("en"))
    assert env["language"] == "en"


def test_continuation_prompt_prepends_preamble_and_includes_turn_count():
    out = build_continuation_prompt(language="ko", turn_number=3, max_turns=20)
    assert out.startswith(doc_language_preamble("ko"))
    assert "turn 3 of up to 20" in out
    assert "Continue working on the issue" in out


def test_continuation_prompt_default_english_path():
    out = build_continuation_prompt(language="en", turn_number=2, max_turns=20)
    assert out.startswith(doc_language_preamble("en"))
    # Continuation body itself stays English regardless of operator
    # language — this is the orchestrator's own glue text, not the
    # WORKFLOW.md body. Only artefact language is operator-controlled.
    assert "turn 2 of up to 20" in out


def test_first_turn_prompt_exposes_is_rewind_to_template():
    """`is_rewind` must reach WORKFLOW templates so the retry preamble
    can branch on in-flight Review/QA → In Progress rewinds (which the
    dispatch-level `attempt` counter does NOT cover)."""
    body = "rewind={{ is_rewind }} attempt={% if attempt %}{{ attempt }}{% else %}none{% endif %}"
    rewind_prompt, env = build_first_turn_prompt(
        prompt_template=body,
        issue=_issue(),
        attempt=None,
        language="en",
        max_turns=5,
        is_rewind=True,
    )
    assert "rewind=True" in rewind_prompt
    assert env["is_rewind"] is True

    forward_prompt, env = build_first_turn_prompt(
        prompt_template=body,
        issue=_issue(),
        attempt=None,
        language="en",
        max_turns=5,
    )
    # Default is False so existing WORKFLOW templates that newly add a
    # `{% if is_rewind %}` branch render cleanly without a rewind.
    assert "rewind=False" in forward_prompt
    assert env["is_rewind"] is False
