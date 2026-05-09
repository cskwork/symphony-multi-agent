"""SPEC §17.1 — strict prompt template behavior."""

from __future__ import annotations

import pytest

from symphony.errors import TemplateRenderError, TemplateParseError
from symphony.issue import BlockerRef, Issue
from symphony.prompt import build_prompt_env, render


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
