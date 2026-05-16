"""SPEC §17.1 — workflow and config parsing conformance."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.errors import (
    MissingWorkflowFile,
    MissingTrackerApiKey,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
    WorkflowFrontMatterNotAMap,
    WorkflowParseError,
    ConfigValidationError,
)
from symphony.workflow import (
    DEFAULT_ACTIVE_STATES,
    DEFAULT_TERMINAL_STATES,
    DEFAULT_POLL_INTERVAL_MS,
    build_service_config,
    load_workflow,
    parse_workflow_text,
    resolve_var_indirection,
    resolve_workflow_path,
    validate_for_dispatch,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_resolve_workflow_path_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_workflow_path(None) == tmp_path / "WORKFLOW.md"


def test_resolve_workflow_path_explicit(tmp_path):
    explicit = tmp_path / "alt.md"
    assert resolve_workflow_path(str(explicit)) == explicit.expanduser().resolve()


def test_missing_workflow_file(tmp_path):
    with pytest.raises(MissingWorkflowFile):
        load_workflow(tmp_path / "nope.md")


def test_parse_no_front_matter():
    wf = parse_workflow_text("Hello body\nmore", Path("/tmp/W.md"))
    assert wf.config == {}
    assert wf.prompt_template == "Hello body\nmore"


def test_parse_with_front_matter():
    text = textwrap.dedent(
        """\
        ---
        tracker:
          kind: linear
          project_slug: demo
        polling:
          interval_ms: 5000
        ---

        Prompt body for {{ issue.identifier }}
        """
    )
    wf = parse_workflow_text(text, Path("/tmp/W.md"))
    assert wf.config["tracker"]["kind"] == "linear"
    assert wf.config["polling"]["interval_ms"] == 5000
    assert wf.prompt_template.startswith("Prompt body for")


def test_parse_invalid_yaml():
    text = "---\nthis: : invalid : yaml\n---\nBody"
    with pytest.raises(WorkflowParseError):
        parse_workflow_text(text, Path("/tmp/W.md"))


def test_parse_front_matter_not_a_map():
    text = "---\n- just\n- a\n- list\n---\nBody"
    with pytest.raises(WorkflowFrontMatterNotAMap):
        parse_workflow_text(text, Path("/tmp/W.md"))


def test_var_indirection(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    assert resolve_var_indirection("$MY_TOKEN") == "secret-value"
    monkeypatch.delenv("MY_TOKEN", raising=False)
    assert resolve_var_indirection("$MY_TOKEN") == ""
    # Non-$ prefixed strings are passed through unchanged.
    assert resolve_var_indirection("$VAR more text") == "$VAR more text"


def test_build_service_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test_token")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: my-proj
            ---
            Hello {{ issue.identifier }}
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.poll_interval_ms == DEFAULT_POLL_INTERVAL_MS
    assert cfg.tracker.active_states == DEFAULT_ACTIVE_STATES
    assert cfg.tracker.terminal_states == DEFAULT_TERMINAL_STATES
    assert cfg.tracker.api_key == "lin_test_token"
    assert cfg.tracker.project_slug == "my-proj"
    assert cfg.codex.command == "codex app-server"
    assert cfg.agent.max_concurrent_agents == 1
    assert cfg.agent.max_attempts == 3
    assert cfg.prompt_template_for_state("Todo") == "Hello {{ issue.identifier }}"


def test_build_service_config_reads_agent_max_attempts(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              max_attempts: 3
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_attempts == 3


def test_build_service_config_allows_zero_agent_max_attempts(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            agent:
              max_attempts: 0
            ---
            Hello
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_attempts == 0


def test_stage_prompt_files_are_loaded_relative_to_workflow(tmp_path):
    prompt_dir = tmp_path / "docs" / "prompts" / "stages"
    prompt_dir.mkdir(parents=True)
    (tmp_path / "docs" / "prompts" / "base.md").write_text(
        "BASE for {{ issue.identifier }}", encoding="utf-8"
    )
    (prompt_dir / "todo.md").write_text(
        "TODO rules for {{ issue.state }}", encoding="utf-8"
    )
    (prompt_dir / "explore.md").write_text(
        "EXPLORE rules for {{ issue.state }}", encoding="utf-8"
    )
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
              active_states: [Todo, Explore]
            prompts:
              base: ./docs/prompts/base.md
              stages:
                Todo: ./docs/prompts/stages/todo.md
                Explore: ./docs/prompts/stages/explore.md
            ---
            LEGACY all-stage prompt
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    todo_prompt = cfg.prompt_template_for_state("Todo")
    explore_prompt = cfg.prompt_template_for_state("explore")
    assert todo_prompt == "BASE for {{ issue.identifier }}\n\nTODO rules for {{ issue.state }}"
    assert "EXPLORE rules" not in todo_prompt
    assert explore_prompt == (
        "BASE for {{ issue.identifier }}\n\nEXPLORE rules for {{ issue.state }}"
    )
    assert "LEGACY all-stage prompt" not in todo_prompt


def test_missing_stage_prompt_file_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./board
            prompts:
              stages:
                Todo: ./docs/prompts/stages/missing.md
            ---
            legacy prompt
            """
        ),
    )

    with pytest.raises(ConfigValidationError, match="prompt file not found"):
        build_service_config(load_workflow(path))


def test_build_service_config_workspace_root_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            workspace:
              root: ./ws
            ---
            body
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.workspace_root == (tmp_path / "ws").resolve()


def test_build_service_config_state_concurrency_normalization(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x }
            agent:
              max_concurrent_agents_by_state:
                "Todo": 3
                "In Progress": "not-an-int"
                "Bad": -2
            ---
            body
            """
        ),
    )
    wf = load_workflow(path)
    cfg = build_service_config(wf)
    assert cfg.agent.max_concurrent_agents_by_state == {"todo": 3}


def test_validate_for_dispatch_unsupported_tracker(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: jira, project_slug: x, api_key: xx }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(UnsupportedTrackerKind):
        validate_for_dispatch(cfg)


def test_validate_for_dispatch_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
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
    with pytest.raises(MissingTrackerApiKey):
        validate_for_dispatch(cfg)


def test_validate_for_dispatch_missing_project_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear }
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    with pytest.raises(MissingTrackerProjectSlug):
        validate_for_dispatch(cfg)


def test_state_descriptions_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.state_descriptions == {}


def test_state_descriptions_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
              state_descriptions:
                Todo: "  Triage incoming work  "
                "In Progress": Code + tests
                Review: Self-review the diff
                Empty: ""
                42: "non-string key dropped"
                Bogus: 123
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    # Keys lowercased, blank/non-string values dropped, non-string keys dropped,
    # leading/trailing whitespace stripped.
    assert cfg.tracker.state_descriptions == {
        "todo": "Triage incoming work",
        "in progress": "Code + tests",
        "review": "Self-review the diff",
    }


def test_state_descriptions_invalid_root_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "tok")
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: linear
              project_slug: x
              state_descriptions: not-a-dict
            ---
            body
            """
        ),
    )
    cfg = build_service_config(load_workflow(path))
    assert cfg.tracker.state_descriptions == {}


def test_invalid_max_turns_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            agent: { max_turns: 0 }
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


def test_default_max_total_turns_is_sixty(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: file, board_root: ./kanban }
            ---
            body
            """
        ),
    )

    cfg = build_service_config(load_workflow(path))

    assert cfg.agent.max_total_turns == 60


# --- positive-int validation tightened in improve/observability-and-doctor ---

@pytest.mark.parametrize("field,raw_value", [
    ("max_concurrent_agents", 0),
    ("max_concurrent_agents", -1),
    ("max_retry_backoff_ms", 0),
    ("max_retry_backoff_ms", -100),
    ("max_attempts", -1),
])
def test_invalid_agent_int_fields_fail_validation(tmp_path, field, raw_value):
    """Regression: previously these silently accepted 0/negative via
    `_as_int`, leading to footguns (max_concurrent_agents=0 dispatches
    nothing; max_retry_backoff_ms=0 produces a tight retry loop)."""
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            agent: {{ {field}: {raw_value} }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


@pytest.mark.parametrize("kind,field", [
    ("pi", "turn_timeout_ms"),
    ("pi", "read_timeout_ms"),
    ("claude", "turn_timeout_ms"),
    ("codex", "turn_timeout_ms"),
    ("gemini", "stall_timeout_ms"),
])
def test_invalid_backend_timeouts_fail_validation(tmp_path, kind, field):
    path = _write(
        tmp_path,
        textwrap.dedent(
            f"""\
            ---
            tracker: {{ kind: linear, project_slug: x, api_key: xx }}
            agent: {{ kind: {kind} }}
            {kind}: {{ {field}: 0 }}
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))


def test_invalid_polling_interval_fails_validation(tmp_path):
    path = _write(
        tmp_path,
        textwrap.dedent(
            """\
            ---
            tracker: { kind: linear, project_slug: x, api_key: xx }
            polling: { interval_ms: 0 }
            ---
            body
            """
        ),
    )
    with pytest.raises(ConfigValidationError):
        build_service_config(load_workflow(path))
