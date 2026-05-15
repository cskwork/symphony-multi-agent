"""Linear `update_state` mutation — focused on the archive flow.

Exercises the three-call sequence (issue→team, team→states, issueUpdate)
plus the per-client cache so a second archive in the same session skips
the resolution queries.
"""

from __future__ import annotations

import json

import httpx
import pytest

from symphony.errors import LinearUnknownPayload
from symphony.issue import Issue
from symphony.tracker_linear import LinearClient
from symphony.workflow import TrackerConfig


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kind="linear",
        endpoint="https://example.test/graphql",
        api_key="tok",
        project_slug="proj",
        active_states=("Todo",),
        terminal_states=("Done", "Archive"),
    )


def _issue(uuid: str = "uuid-1") -> Issue:
    return Issue(
        id=uuid,
        identifier="SMA-1",
        title="t",
        description=None,
        priority=None,
        state="Done",
    )


def _route(payloads: dict[str, dict]) -> httpx.MockTransport:
    """Map operation name → response body. Records call order in `calls`."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body.get("query") or ""
        # Naively pick the operation by the first `query`/`mutation` token.
        if "IssueTeam" in query:
            op = "IssueTeam"
        elif "WorkflowStates" in query:
            op = "WorkflowStates"
        elif "IssueUpdate" in query:
            op = "IssueUpdate"
        else:
            op = "?"
        calls.append(op)
        return httpx.Response(200, json=payloads.get(op, {"data": {}}))

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def test_update_state_resolves_state_id_then_calls_issueUpdate() -> None:
    payloads = {
        "IssueTeam": {"data": {"issue": {"id": "uuid-1", "team": {"id": "team-A"}}}},
        "WorkflowStates": {
            "data": {
                "workflowStates": {
                    "nodes": [
                        {"id": "state-done", "name": "Done"},
                        {"id": "state-archive", "name": "Archive"},
                    ]
                }
            }
        },
        "IssueUpdate": {
            "data": {
                "issueUpdate": {
                    "success": True,
                    "issue": {"id": "uuid-1", "state": {"name": "Archive"}},
                }
            }
        },
    }
    transport = _route(payloads)
    client = LinearClient(_cfg(), http_client=httpx.Client(transport=transport))
    client.update_state(_issue(), "Archive")
    assert transport.calls == ["IssueTeam", "WorkflowStates", "IssueUpdate"]


def test_update_state_caches_subsequent_calls() -> None:
    """Second archive in the same session should skip both resolution queries."""
    payloads = {
        "IssueTeam": {"data": {"issue": {"id": "uuid-1", "team": {"id": "team-A"}}}},
        "WorkflowStates": {
            "data": {
                "workflowStates": {
                    "nodes": [{"id": "state-archive", "name": "Archive"}]
                }
            }
        },
        "IssueUpdate": {
            "data": {"issueUpdate": {"success": True, "issue": {"id": "u", "state": {"name": "Archive"}}}}
        },
    }
    transport = _route(payloads)
    client = LinearClient(_cfg(), http_client=httpx.Client(transport=transport))
    client.update_state(_issue("uuid-1"), "Archive")
    client.update_state(_issue("uuid-1"), "Archive")
    # Second call hits IssueUpdate only — both lookups served from cache.
    assert transport.calls == [
        "IssueTeam",
        "WorkflowStates",
        "IssueUpdate",
        "IssueUpdate",
    ]


def test_update_state_raises_when_workflow_state_missing() -> None:
    payloads = {
        "IssueTeam": {"data": {"issue": {"id": "uuid-1", "team": {"id": "team-A"}}}},
        "WorkflowStates": {
            "data": {"workflowStates": {"nodes": [{"id": "s", "name": "Done"}]}}
        },
    }
    transport = _route(payloads)
    client = LinearClient(_cfg(), http_client=httpx.Client(transport=transport))
    with pytest.raises(LinearUnknownPayload):
        client.update_state(_issue(), "Archive")


def test_update_state_raises_when_success_false() -> None:
    payloads = {
        "IssueTeam": {"data": {"issue": {"id": "uuid-1", "team": {"id": "team-A"}}}},
        "WorkflowStates": {
            "data": {"workflowStates": {"nodes": [{"id": "s-arch", "name": "Archive"}]}}
        },
        "IssueUpdate": {"data": {"issueUpdate": {"success": False}}},
    }
    transport = _route(payloads)
    client = LinearClient(_cfg(), http_client=httpx.Client(transport=transport))
    with pytest.raises(LinearUnknownPayload):
        client.update_state(_issue(), "Archive")
