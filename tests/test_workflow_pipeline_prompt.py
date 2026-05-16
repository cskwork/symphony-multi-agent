"""Coverage for the production-pipeline prompt templates shipped in
WORKFLOW.file.example.md / WORKFLOW.example.md plus docs/symphony-prompts/.

These tests assert the prompt: (1) parses + renders for every active state,
(2) carries only the current stage-specific instructions the agent needs,
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

STAGE_HEADINGS_BY_STATE = {
    "Todo": "### TRIAGE",
    "Explore": "### EXPLORE",
    "In Progress": "### IMPLEMENT",
    "Review": "### REVIEW",
    "QA": "### QA",
    "Learn": "### LEARN",
    "Done": "### DONE",
}

# Phrases the EXPLORE stage must reference so the agent actually consults
# the three sources of domain knowledge (wiki, history, code) and produces
# the structured brief / candidate plans / recommendation.
EXPLORE_HARD_RULES = (
    "llm-wiki",
    "git log",
    "Domain Brief",
    "Plan Candidates",
    "Recommendation",
)

# Phrases the LEARN stage must reference so wiki updates are not optional.
LEARN_HARD_RULES = (
    "llm-wiki",
    "INDEX.md",
    "Decision log",
    "Wiki Updates",
)

# File-tracker variants record QA via a `## QA Evidence` markdown section in
# the ticket body; the Linear variant records it as a "QA Evidence comment".
# Both must mention `QA Evidence` and demand real execution.
QA_HARD_RULES = (
    "THIS STAGE MUST EXECUTE REAL CODE",
    "QA Evidence",
)

REVIEW_REWIND_RULES = (
    "CRITICAL, HIGH, or MEDIUM finding",
    "Review Findings",
)

QA_REWIND_RULES = (
    "server-reported HIGH",
    "QA Failure",
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
def test_active_states_cover_full_pipeline(workflow: str) -> None:
    cfg = _load(workflow)
    for required in ("Todo", "Explore", "In Progress", "Review", "QA", "Learn"):
        assert required in cfg.tracker.active_states, (
            f"{workflow} active_states missing {required!r} — TUI lane will not render"
        )
        assert required.lower() in cfg.prompts.stage_templates, (
            f"{workflow} prompts.stages missing {required!r}"
        )
    assert "done" in cfg.prompts.stage_templates, (
        f"{workflow} prompts.stages missing terminal Done report prompt"
    )


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
@pytest.mark.parametrize(
    "state", ["Todo", "Explore", "In Progress", "Review", "QA", "Learn", "Done"]
)
def test_prompt_renders_for_every_stage(workflow: str, state: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state(state),
        build_prompt_env(_issue(state), attempt=None),
    )
    assert f"Current state: {state}." in rendered
    current_heading = STAGE_HEADINGS_BY_STATE[state]
    assert current_heading in rendered, (
        f"missing current stage heading {current_heading!r} at state={state}"
    )
    for other_state, heading in STAGE_HEADINGS_BY_STATE.items():
        if other_state == state:
            continue
        assert heading not in rendered, (
            f"unexpected stage heading {heading!r} in render for state={state}"
        )


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_qa_stage_demands_real_execution(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None),
    )
    for phrase in QA_HARD_RULES:
        assert phrase in rendered, f"QA stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_review_high_findings_rewind_to_in_progress(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Review"),
        build_prompt_env(_issue("Review"), attempt=None),
    )
    for phrase in REVIEW_REWIND_RULES:
        assert phrase in rendered, f"Review stage missing rewind rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_qa_server_reported_high_issues_rewind_to_in_progress(
    workflow: str,
) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None),
    )
    for phrase in QA_REWIND_RULES:
        assert phrase in rendered, f"QA stage missing server-high rewind rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_explore_stage_consults_wiki_history_and_code(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Explore"),
        build_prompt_env(_issue("Explore"), attempt=None),
    )
    for phrase in EXPLORE_HARD_RULES:
        assert phrase in rendered, f"Explore stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_writes_back_to_wiki(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )
    for phrase in LEARN_HARD_RULES:
        assert phrase in rendered, f"Learn stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_done_stage_carries_as_is_to_be_report_shape(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Done"),
        build_prompt_env(_issue("Done"), attempt=None),
    )
    for heading in DONE_REPORT_SHAPE:
        assert heading in rendered, f"Done report missing section: {heading!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_retry_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("In Progress"),
        build_prompt_env(_issue("In Progress"), attempt=2),
    )
    assert "retry attempt 2" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_blocked_by_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    issue = _issue(
        "Todo",
        blocked_by=(BlockerRef(id="b1", identifier="B-1", state="Todo"),),
    )
    rendered = render(
        cfg.prompt_template_for_state("Todo"), build_prompt_env(issue, attempt=None)
    )
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
