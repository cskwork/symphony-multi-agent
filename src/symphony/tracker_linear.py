"""SPEC §11 — Linear GraphQL tracker adapter."""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from .errors import (
    LinearApiRequestError,
    LinearApiStatusError,
    LinearGraphQLErrors,
    LinearMissingEndCursor,
    LinearUnknownPayload,
)
from .issue import (
    BlockerRef,
    Issue,
    coerce_priority,
    normalize_labels,
    parse_iso_timestamp,
)
from .workflow import TrackerConfig


PAGE_SIZE = 50  # §11.2
NETWORK_TIMEOUT_SECONDS = 30.0  # §11.2


_CANDIDATE_QUERY = """
query Candidates($projectSlug: String!, $states: [String!], $first: Int!, $after: String) {
  issues(
    first: $first,
    after: $after,
    filter: {
      project: { slugId: { eq: $projectSlug } },
      state: { name: { in: $states } }
    }
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      priority
      branchName
      url
      createdAt
      updatedAt
      state { name }
      labels { nodes { name } }
      inverseRelations(filter: { type: { eq: "blocks" } }) {
        nodes {
          type
          issue { id identifier state { name } }
        }
      }
    }
  }
}
"""

_BY_STATE_QUERY = """
query ByState($projectSlug: String!, $states: [String!], $first: Int!, $after: String) {
  issues(
    first: $first,
    after: $after,
    filter: {
      project: { slugId: { eq: $projectSlug } },
      state: { name: { in: $states } }
    }
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      updatedAt
      state { name }
    }
  }
}
"""

_BY_IDS_QUERY = """
query ByIds($ids: [ID!]) {
  issues(filter: { id: { in: $ids } }, first: 250) {
    nodes {
      id
      identifier
      title
      state { name }
    }
  }
}
"""

# Used by `update_state` to translate a state name → state UUID.
# Linear's `issueUpdate` mutation requires the state's UUID, not its name.
# We narrow by team via the issue first, then list workflow states for that
# team and match by case-insensitive name. Cached per-(team, name) on the
# client so repeated archives don't re-hit this endpoint.
_ISSUE_TEAM_QUERY = """
query IssueTeam($id: String!) {
  issue(id: $id) { id team { id } }
}
"""

_WORKFLOW_STATES_QUERY = """
query WorkflowStates($teamId: ID!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }, first: 100) {
    nodes { id name }
  }
}
"""

_ISSUE_UPDATE_MUTATION = """
mutation IssueUpdate($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id state { name } }
  }
}
"""


def _normalize_node(node: dict[str, Any], minimal: bool = False) -> Issue:
    state_obj = node.get("state") or {}
    state_name = state_obj.get("name") or ""
    if minimal:
        return Issue(
            id=node["id"],
            identifier=node.get("identifier", ""),
            title=node.get("title", ""),
            description=None,
            priority=None,
            state=state_name,
            updated_at=parse_iso_timestamp(node.get("updatedAt")),
        )
    label_nodes = ((node.get("labels") or {}).get("nodes")) or []
    labels = normalize_labels([n.get("name") for n in label_nodes if isinstance(n, dict)])
    inverse_nodes = ((node.get("inverseRelations") or {}).get("nodes")) or []
    blockers: list[BlockerRef] = []
    for rel in inverse_nodes:
        if not isinstance(rel, dict):
            continue
        if rel.get("type") != "blocks":
            continue
        b_issue = rel.get("issue") or {}
        b_state = (b_issue.get("state") or {}).get("name")
        blockers.append(
            BlockerRef(
                id=b_issue.get("id"),
                identifier=b_issue.get("identifier"),
                state=b_state,
            )
        )
    return Issue(
        id=node["id"],
        identifier=node.get("identifier", ""),
        title=node.get("title", ""),
        description=node.get("description"),
        priority=coerce_priority(node.get("priority")),
        state=state_name,
        branch_name=node.get("branchName"),
        url=node.get("url"),
        labels=labels,
        blocked_by=tuple(blockers),
        created_at=parse_iso_timestamp(node.get("createdAt")),
        updated_at=parse_iso_timestamp(node.get("updatedAt")),
    )


class LinearClient:
    """§11 — adapter that exposes the three required operations.

    All methods are synchronous; the orchestrator runs them in an executor
    so the asyncio event loop is not blocked.
    """

    def __init__(self, tracker: TrackerConfig, http_client: httpx.Client | None = None) -> None:
        self._tracker = tracker
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=NETWORK_TIMEOUT_SECONDS,
            headers={
                "Authorization": tracker.api_key,
                "Content-Type": "application/json",
                "User-Agent": "symphony-reference/0.1",
            },
        )
        # (team_id, lower-cased state name) → workflow state UUID.
        # Avoids re-querying workflow states on every archive call.
        self._state_id_cache: dict[tuple[str, str], str] = {}
        # Issue UUID → team UUID. Issue→team is immutable, so cache it.
        self._issue_team_cache: dict[str, str] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LinearClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # §11.1.1
    def fetch_candidate_issues(self) -> list[Issue]:
        return self._paginate(
            _CANDIDATE_QUERY,
            states=list(self._tracker.active_states),
            normalizer=lambda node: _normalize_node(node, minimal=False),
        )

    # §11.1.2
    def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        states = [s for s in state_names if s]
        if not states:
            return []
        return self._paginate(
            _BY_STATE_QUERY,
            states=states,
            normalizer=lambda node: _normalize_node(node, minimal=True),
        )

    # §11.1.3
    def fetch_issue_states_by_ids(self, ids: Iterable[str]) -> list[Issue]:
        id_list = [i for i in ids if i]
        if not id_list:
            return []
        payload = self._post({"query": _BY_IDS_QUERY, "variables": {"ids": id_list}})
        nodes = self._extract_nodes(payload)
        return [_normalize_node(n, minimal=True) for n in nodes]

    def update_state(self, issue: Issue, target_state: str) -> None:
        """Move `issue` to the workflow state named `target_state`.

        Linear's `issueUpdate` mutation requires the state's UUID, so we
        first resolve `target_state` (case-insensitive) against the issue's
        team workflow states. Both lookups are cached on the client.
        """
        if not issue.id:
            raise LinearUnknownPayload("issue.id is empty; cannot update state")
        team_id = self._team_id_for_issue(issue.id)
        state_id = self._state_id_for(team_id, target_state)
        payload = self._post(
            {
                "query": _ISSUE_UPDATE_MUTATION,
                "variables": {"id": issue.id, "stateId": state_id},
            }
        )
        data = payload.get("data") or {}
        result = data.get("issueUpdate") or {}
        if not result.get("success"):
            raise LinearUnknownPayload(
                "issueUpdate did not report success", payload_preview=str(result)[:200]
            )

    def _team_id_for_issue(self, issue_id: str) -> str:
        cached = self._issue_team_cache.get(issue_id)
        if cached:
            return cached
        payload = self._post(
            {"query": _ISSUE_TEAM_QUERY, "variables": {"id": issue_id}}
        )
        node = ((payload.get("data") or {}).get("issue") or {})
        team_id = ((node.get("team") or {}).get("id"))
        if not isinstance(team_id, str) or not team_id:
            raise LinearUnknownPayload(
                "could not resolve team for issue", issue_id=issue_id
            )
        self._issue_team_cache[issue_id] = team_id
        return team_id

    def _state_id_for(self, team_id: str, state_name: str) -> str:
        key = (team_id, state_name.lower())
        cached = self._state_id_cache.get(key)
        if cached:
            return cached
        payload = self._post(
            {"query": _WORKFLOW_STATES_QUERY, "variables": {"teamId": team_id}}
        )
        nodes = (((payload.get("data") or {}).get("workflowStates") or {}).get("nodes") or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            sid = node.get("id")
            if isinstance(name, str) and isinstance(sid, str) and name.lower() == state_name.lower():
                self._state_id_cache[key] = sid
                return sid
        raise LinearUnknownPayload(
            "no workflow state matched name",
            team_id=team_id,
            state_name=state_name,
        )

    # ------------------------------------------------------------------

    def _paginate(
        self,
        query: str,
        *,
        states: list[str],
        normalizer,
    ) -> list[Issue]:
        out: list[Issue] = []
        after: str | None = None
        while True:
            payload = self._post(
                {
                    "query": query,
                    "variables": {
                        "projectSlug": self._tracker.project_slug,
                        "states": states,
                        "first": PAGE_SIZE,
                        "after": after,
                    },
                }
            )
            issues_payload = self._extract_issues_payload(payload)
            for node in issues_payload.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                out.append(normalizer(node))
            page_info = issues_payload.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                raise LinearMissingEndCursor("missing endCursor while paginating")
            after = cursor
        return out

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(self._tracker.endpoint, json=body)
        except httpx.HTTPError as exc:
            raise LinearApiRequestError("transport failure", error=str(exc)) from exc
        if response.status_code != 200:
            raise LinearApiStatusError(
                "non-200 response",
                status=response.status_code,
                body_preview=response.text[:200],
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LinearUnknownPayload("invalid JSON in response") from exc
        if not isinstance(payload, dict):
            raise LinearUnknownPayload("payload is not an object")
        if payload.get("errors"):
            raise LinearGraphQLErrors(
                "graphql errors", errors=payload.get("errors")
            )
        return payload

    @staticmethod
    def _extract_issues_payload(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearUnknownPayload("missing data field")
        issues = data.get("issues")
        if not isinstance(issues, dict):
            raise LinearUnknownPayload("data.issues missing")
        return issues

    @staticmethod
    def _extract_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
        issues = LinearClient._extract_issues_payload(payload)
        nodes = issues.get("nodes") or []
        if not isinstance(nodes, list):
            raise LinearUnknownPayload("data.issues.nodes is not a list")
        return [n for n in nodes if isinstance(n, dict)]

    # §10.5 linear_graphql tool extension support.
    def execute_raw(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._post({"query": query, "variables": variables or {}})
