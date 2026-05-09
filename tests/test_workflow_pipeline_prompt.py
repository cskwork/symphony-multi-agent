"""Coverage for the production-pipeline prompt template shipped in
WORKFLOW.md / WORKFLOW.file.example.md / WORKFLOW.example.md.

These tests assert the prompt: (1) parses + renders for every active state,
(2) carries the stage-specific instructions the agent needs at each stage,
(3) renders the retry and blocked_by branches, and (4) preserves the
fixed `## As-Is -> To-Be Report` shape required at Done.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue
from symphony.prompt import build_prompt_env, render
from symphony.workflow import build_service_config, load_workflow


REPO_ROOT = Path(__file__).resolve().parent.parent

# `WORKFLOW.md` is gitignored — every operator customizes it for their own
# board (different prompt body, different agent.kind, different hooks). The
# pipeline prompt is shipped via the *example* files; those are the
# canonical reference users copy from. We deliberately do not assert the
# pipeline prompt lives in `WORKFLOW.md` itself, since requiring that
# would break any non-pipeline workflow people legitimately run.
WORKFLOW_FILES = (
    "WORKFLOW.file.example.md",
    "WORKFLOW.example.md",
)

# Phrases that must appear in every render. The template is not state-
# branched in the parser sense; it ships every stage rule and the agent
# selects the matching one. So whatever the issue's state, all stage
# headings must be present (and the issue's state must be echoed).
STAGE_HEADINGS = (
    "IMPLEMENT",
    "REVIEW",
    "QA",
    "DONE",
)

# File-tracker variants record QA via a `## QA Evidence` markdown section in
# the ticket body; the Linear variant records it as a "QA Evidence comment".
# Both must mention `QA Evidence` and demand real execution.
QA_HARD_RULES = (
    "THIS STAGE MUST EXECUTE REAL CODE",
    "QA Evidence",
)

DONE_REPORT_SHAPE = (
    "## As-Is -> To-Be Report",
    "### As-Is",
    "### To-Be",
    "### Reasoning",
    "### Evidence",
)


def _load(name: str):
    cfg = build_service_config(load_workflow(REPO_ROOT / name))
    return cfg


def _issue(state: str, **overrides) -> Issue:
    base = dict(
        id="DEMO-1",
        identifier="DEMO-1",
        title="t",
        description="d",
        priority=2,
        state=state,
        labels=(),
    )
    base.update(overrides)
    return Issue(**base)


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_active_states_include_review_and_qa(workflow: str) -> None:
    cfg = _load(workflow)
    assert "Review" in cfg.tracker.active_states
    assert "QA" in cfg.tracker.active_states


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
@pytest.mark.parametrize("state", ["Todo", "In Progress", "Review", "QA", "Done"])
def test_prompt_renders_for_every_stage(workflow: str, state: str) -> None:
    cfg = _load(workflow)
    rendered = render(cfg.prompt_template, build_prompt_env(_issue(state), attempt=None))
    assert f"Current state: {state}." in rendered
    for heading in STAGE_HEADINGS:
        assert heading in rendered, f"missing stage heading {heading!r} at state={state}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_qa_stage_demands_real_execution(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(cfg.prompt_template, build_prompt_env(_issue("QA"), attempt=None))
    for phrase in QA_HARD_RULES:
        assert phrase in rendered, f"QA stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_done_stage_carries_as_is_to_be_report_shape(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(cfg.prompt_template, build_prompt_env(_issue("Done"), attempt=None))
    for heading in DONE_REPORT_SHAPE:
        assert heading in rendered, f"Done report missing section: {heading!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_retry_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template, build_prompt_env(_issue("In Progress"), attempt=2)
    )
    assert "retry attempt 2" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_blocked_by_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    issue = _issue(
        "Todo",
        blocked_by=(BlockerRef(id="b1", identifier="B-1", state="Todo"),),
    )
    rendered = render(cfg.prompt_template, build_prompt_env(issue, attempt=None))
    assert "B-1 (Todo)" in rendered


def test_pipeline_demo_ticket_is_a_complete_worked_example() -> None:
    """The shipped reference ticket must demonstrate every artefact the
    pipeline expects, so users can copy its structure.

    Lives under ``docs/`` (not ``kanban/``) so it is tracked in git;
    ``kanban/`` is gitignored as the user-local board directory.
    """
    body = (REPO_ROOT / "docs" / "PIPELINE-DEMO.md").read_text(encoding="utf-8")
    for required in (
        "## Plan",
        "## Implementation",
        "## Review",
        "## QA Evidence",
        "## As-Is -> To-Be Report",
        "### As-Is",
        "### To-Be",
        "### Reasoning",
        "### Evidence",
    ):
        assert required in body, f"PIPELINE-DEMO missing section {required!r}"
