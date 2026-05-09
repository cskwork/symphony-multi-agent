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
