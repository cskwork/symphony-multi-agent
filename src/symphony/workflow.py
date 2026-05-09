"""SPEC §5, §6 — WORKFLOW.md loader, parser, typed config view."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import (
    ConfigValidationError,
    MissingTrackerApiKey,
    MissingTrackerProjectSlug,
    MissingWorkflowFile,
    UnsupportedTrackerKind,
    WorkflowFrontMatterNotAMap,
    WorkflowParseError,
)


SUPPORTED_TRACKER_KINDS = {"linear", "file"}
LINEAR_DEFAULT_ENDPOINT = "https://api.linear.app/graphql"
LINEAR_API_KEY_ENV = "LINEAR_API_KEY"

DEFAULT_ACTIVE_STATES = ("Todo", "In Progress")
DEFAULT_TERMINAL_STATES = ("Closed", "Cancelled", "Canceled", "Duplicate", "Done")
DEFAULT_BOARD_ROOT_NAME = "board"
DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 10
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_CODEX_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_CODEX_READ_TIMEOUT_MS = 5_000
DEFAULT_CODEX_STALL_TIMEOUT_MS = 300_000

DEFAULT_PROMPT = "You are working on an issue from Linear."

SUPPORTED_AGENT_KINDS = {"codex", "claude", "gemini"}
DEFAULT_AGENT_KIND = "codex"
DEFAULT_CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --include-partial-messages --verbose"
)
DEFAULT_GEMINI_COMMAND = "gemini -p"
DEFAULT_BACKEND_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_BACKEND_READ_TIMEOUT_MS = 5_000
DEFAULT_BACKEND_STALL_TIMEOUT_MS = 300_000

_VAR_PATTERN = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


# ---------------------------------------------------------------------------
# §5.2 — file format parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowDefinition:
    """§4.1.2 — parsed WORKFLOW.md payload."""

    config: dict[str, Any]
    prompt_template: str
    source_path: Path

    def base_dir(self) -> Path:
        return self.source_path.parent


def parse_workflow_text(text: str, source_path: Path) -> WorkflowDefinition:
    """§5.2 — front-matter delimited by `---` lines, trim body."""
    lines = text.splitlines()
    config: dict[str, Any] = {}
    body_lines = lines

    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration as exc:
            raise WorkflowParseError(
                "front matter not terminated", source=str(source_path)
            ) from exc
        front_text = "\n".join(lines[1:end])
        body_lines = lines[end + 1 :]
        if front_text.strip():
            try:
                parsed = yaml.safe_load(front_text)
            except yaml.YAMLError as exc:
                raise WorkflowParseError(
                    "invalid YAML front matter", source=str(source_path), error=str(exc)
                ) from exc
            if parsed is None:
                config = {}
            elif not isinstance(parsed, dict):
                raise WorkflowFrontMatterNotAMap(
                    "front matter must decode to a map", source=str(source_path)
                )
            else:
                config = parsed
    body = "\n".join(body_lines).strip()
    return WorkflowDefinition(config=config, prompt_template=body, source_path=source_path)


def load_workflow(path: str | Path) -> WorkflowDefinition:
    """§5.1 — read WORKFLOW.md from explicit path."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MissingWorkflowFile("workflow file not found", path=str(p)) from exc
    except OSError as exc:
        raise MissingWorkflowFile(
            "workflow file unreadable", path=str(p), error=str(exc)
        ) from exc
    return parse_workflow_text(text, p.resolve())


def resolve_workflow_path(explicit: str | Path | None) -> Path:
    """§5.1 — explicit path else `./WORKFLOW.md`."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path.cwd() / "WORKFLOW.md"


# ---------------------------------------------------------------------------
# §6.1 — value coercion / $VAR / ~ expansion
# ---------------------------------------------------------------------------


def resolve_var_indirection(value: Any) -> Any:
    """§5.3.1, §6.1 — only resolve `$VAR_NAME` form. Empty env -> empty string."""
    if isinstance(value, str):
        m = _VAR_PATTERN.match(value)
        if m:
            return os.environ.get(m.group(1), "")
    return value


def expand_path_value(value: str) -> str:
    """§6.1 — apply ~ then $VAR for path-like fields."""
    expanded = os.path.expanduser(value)
    expanded = os.path.expandvars(expanded)
    return expanded


# ---------------------------------------------------------------------------
# §4.1.3, §6.4 — typed config view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackerConfig:
    kind: str
    endpoint: str
    api_key: str
    project_slug: str
    active_states: tuple[str, ...]
    terminal_states: tuple[str, ...]
    # tracker.kind=file: absolute path to the board directory.
    board_root: Path | None = None


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None
    before_run: str | None
    after_run: str | None
    before_remove: str | None
    timeout_ms: int


@dataclass(frozen=True)
class AgentConfig:
    kind: str
    max_concurrent_agents: int
    max_turns: int
    max_retry_backoff_ms: int
    max_concurrent_agents_by_state: dict[str, int]


@dataclass(frozen=True)
class CodexConfig:
    command: str
    approval_policy: Any
    thread_sandbox: Any
    turn_sandbox_policy: Any
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int


@dataclass(frozen=True)
class ClaudeConfig:
    """`agent.kind: claude` — driving Claude Code CLI in print/stream mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, second and later turns add `--resume <session_id>` so Claude
    # rejoins the prior session instead of starting fresh.
    resume_across_turns: bool


@dataclass(frozen=True)
class GeminiConfig:
    """`agent.kind: gemini` — driving Gemini CLI as one-shot per turn."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int


@dataclass(frozen=True)
class ServerConfig:
    """§13.7 optional HTTP extension."""

    port: int | None


@dataclass(frozen=True)
class ServiceConfig:
    workflow_path: Path
    poll_interval_ms: int
    workspace_root: Path
    tracker: TrackerConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    claude: ClaudeConfig
    gemini: GeminiConfig
    server: ServerConfig
    raw: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""

    def backend_timeouts(self) -> tuple[int, int, int]:
        """Return `(turn_ms, read_ms, stall_ms)` for the active backend."""
        kind = self.agent.kind
        if kind == "codex":
            return (
                self.codex.turn_timeout_ms,
                self.codex.read_timeout_ms,
                self.codex.stall_timeout_ms,
            )
        if kind == "claude":
            return (
                self.claude.turn_timeout_ms,
                self.claude.read_timeout_ms,
                self.claude.stall_timeout_ms,
            )
        return (
            self.gemini.turn_timeout_ms,
            self.gemini.read_timeout_ms,
            self.gemini.stall_timeout_ms,
        )


def _as_int(value: Any, default: int, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    if not allow_zero and ivalue <= 0:
        return default
    return ivalue


def _as_str_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return default
    out = tuple(item for item in value if isinstance(item, str) and item)
    return out or default


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _normalize_state_map(value: Any) -> dict[str, int]:
    """§5.3.5 — keys lowercased, invalid entries dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(raw, bool):
            continue
        try:
            ivalue = int(raw)
        except (TypeError, ValueError):
            continue
        if ivalue <= 0:
            continue
        out[key.lower()] = ivalue
    return out


def build_service_config(workflow: WorkflowDefinition) -> ServiceConfig:
    """§6.1 — apply defaults and resolve typed values."""
    cfg = workflow.config
    base_dir = workflow.base_dir()

    tracker_raw = cfg.get("tracker") or {}
    if not isinstance(tracker_raw, dict):
        tracker_raw = {}

    tracker_kind = _as_str(tracker_raw.get("kind")).strip()
    endpoint_default = (
        LINEAR_DEFAULT_ENDPOINT if tracker_kind == "linear" else _as_str(tracker_raw.get("endpoint"))
    )
    tracker_endpoint = _as_str(tracker_raw.get("endpoint"), endpoint_default)
    raw_api_key = tracker_raw.get("api_key")
    if raw_api_key is None and tracker_kind == "linear":
        # Canonical env when literal not provided.
        raw_api_key = "$" + LINEAR_API_KEY_ENV
    tracker_api_key = _as_str(resolve_var_indirection(raw_api_key))

    tracker_project_slug = _as_str(resolve_var_indirection(tracker_raw.get("project_slug")))

    raw_board_root = tracker_raw.get("board_root")
    if isinstance(raw_board_root, str) and raw_board_root:
        resolved_board = (
            resolve_var_indirection(raw_board_root)
            if raw_board_root.startswith("$")
            else raw_board_root
        )
        if isinstance(resolved_board, str) and resolved_board:
            board_path = Path(expand_path_value(resolved_board))
            if not board_path.is_absolute():
                board_path = (base_dir / board_path).resolve()
            else:
                board_path = board_path.resolve()
        else:
            board_path = None
    else:
        board_path = (base_dir / DEFAULT_BOARD_ROOT_NAME).resolve() if tracker_kind == "file" else None

    tracker = TrackerConfig(
        kind=tracker_kind,
        endpoint=tracker_endpoint,
        api_key=tracker_api_key,
        project_slug=tracker_project_slug,
        active_states=_as_str_list(tracker_raw.get("active_states"), DEFAULT_ACTIVE_STATES),
        terminal_states=_as_str_list(
            tracker_raw.get("terminal_states"), DEFAULT_TERMINAL_STATES
        ),
        board_root=board_path,
    )

    polling_raw = cfg.get("polling") or {}
    if not isinstance(polling_raw, dict):
        polling_raw = {}
    poll_interval_ms = _as_int(polling_raw.get("interval_ms"), DEFAULT_POLL_INTERVAL_MS)

    workspace_raw = cfg.get("workspace") or {}
    if not isinstance(workspace_raw, dict):
        workspace_raw = {}
    raw_root = workspace_raw.get("root")
    if isinstance(raw_root, str) and raw_root:
        # §5.3.3 — $VAR for env-backed path values, then ~ expansion.
        resolved = resolve_var_indirection(raw_root) if raw_root.startswith("$") else raw_root
        if isinstance(resolved, str) and resolved:
            workspace_root = Path(expand_path_value(resolved))
        else:
            workspace_root = Path(tempfile.gettempdir()) / "symphony_workspaces"
    else:
        workspace_root = Path(tempfile.gettempdir()) / "symphony_workspaces"

    if not workspace_root.is_absolute():
        workspace_root = (base_dir / workspace_root).resolve()
    else:
        workspace_root = workspace_root.resolve()

    hooks_raw = cfg.get("hooks") or {}
    if not isinstance(hooks_raw, dict):
        hooks_raw = {}
    hooks = HooksConfig(
        after_create=hooks_raw.get("after_create") if isinstance(hooks_raw.get("after_create"), str) else None,
        before_run=hooks_raw.get("before_run") if isinstance(hooks_raw.get("before_run"), str) else None,
        after_run=hooks_raw.get("after_run") if isinstance(hooks_raw.get("after_run"), str) else None,
        before_remove=hooks_raw.get("before_remove") if isinstance(hooks_raw.get("before_remove"), str) else None,
        timeout_ms=_validated_positive_or_default(
            hooks_raw.get("timeout_ms"), DEFAULT_HOOK_TIMEOUT_MS, name="hooks.timeout_ms"
        ),
    )

    agent_raw = cfg.get("agent") or {}
    if not isinstance(agent_raw, dict):
        agent_raw = {}
    max_turns = _validated_positive_or_default(
        agent_raw.get("max_turns"), DEFAULT_MAX_TURNS, name="agent.max_turns"
    )
    agent_kind = _as_str(agent_raw.get("kind"), DEFAULT_AGENT_KIND).strip().lower() or DEFAULT_AGENT_KIND
    if agent_kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}",
            value=agent_kind,
        )
    agent = AgentConfig(
        kind=agent_kind,
        max_concurrent_agents=_as_int(
            agent_raw.get("max_concurrent_agents"), DEFAULT_MAX_CONCURRENT_AGENTS
        ),
        max_turns=max_turns,
        max_retry_backoff_ms=_as_int(
            agent_raw.get("max_retry_backoff_ms"), DEFAULT_MAX_RETRY_BACKOFF_MS
        ),
        max_concurrent_agents_by_state=_normalize_state_map(
            agent_raw.get("max_concurrent_agents_by_state")
        ),
    )

    codex_raw = cfg.get("codex") or {}
    if not isinstance(codex_raw, dict):
        codex_raw = {}
    codex = CodexConfig(
        command=_as_str(codex_raw.get("command"), DEFAULT_CODEX_COMMAND) or DEFAULT_CODEX_COMMAND,
        approval_policy=codex_raw.get("approval_policy"),
        thread_sandbox=codex_raw.get("thread_sandbox"),
        turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
        turn_timeout_ms=_as_int(codex_raw.get("turn_timeout_ms"), DEFAULT_CODEX_TURN_TIMEOUT_MS),
        read_timeout_ms=_as_int(codex_raw.get("read_timeout_ms"), DEFAULT_CODEX_READ_TIMEOUT_MS),
        stall_timeout_ms=_as_int(codex_raw.get("stall_timeout_ms"), DEFAULT_CODEX_STALL_TIMEOUT_MS),
    )

    claude_raw = cfg.get("claude") or {}
    if not isinstance(claude_raw, dict):
        claude_raw = {}
    claude = ClaudeConfig(
        command=_as_str(claude_raw.get("command"), DEFAULT_CLAUDE_COMMAND) or DEFAULT_CLAUDE_COMMAND,
        turn_timeout_ms=_as_int(claude_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS),
        read_timeout_ms=_as_int(claude_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS),
        stall_timeout_ms=_as_int(claude_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS),
        resume_across_turns=bool(claude_raw.get("resume_across_turns", True)),
    )

    gemini_raw = cfg.get("gemini") or {}
    if not isinstance(gemini_raw, dict):
        gemini_raw = {}
    gemini = GeminiConfig(
        command=_as_str(gemini_raw.get("command"), DEFAULT_GEMINI_COMMAND) or DEFAULT_GEMINI_COMMAND,
        turn_timeout_ms=_as_int(gemini_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS),
        read_timeout_ms=_as_int(gemini_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS),
        stall_timeout_ms=_as_int(gemini_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS),
    )

    server_raw = cfg.get("server") or {}
    if not isinstance(server_raw, dict):
        server_raw = {}
    raw_port = server_raw.get("port")
    if isinstance(raw_port, bool):
        port = None
    elif isinstance(raw_port, int):
        port = raw_port
    else:
        port = None
    server = ServerConfig(port=port)

    prompt_template = workflow.prompt_template or DEFAULT_PROMPT

    return ServiceConfig(
        workflow_path=workflow.source_path,
        poll_interval_ms=poll_interval_ms,
        workspace_root=workspace_root,
        tracker=tracker,
        hooks=hooks,
        agent=agent,
        codex=codex,
        claude=claude,
        gemini=gemini,
        server=server,
        raw=dict(cfg),
        prompt_template=prompt_template,
    )


def _validated_positive_or_default(value: Any, default: int, *, name: str) -> int:
    """§5.3.4, §5.3.5 — invalid values fail validation."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a positive integer", value=value)
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(
            f"{name} must be a positive integer", value=value
        ) from exc
    if ivalue <= 0:
        raise ConfigValidationError(f"{name} must be a positive integer", value=value)
    return ivalue


# ---------------------------------------------------------------------------
# §6.3 — dispatch preflight validation
# ---------------------------------------------------------------------------


def validate_for_dispatch(config: ServiceConfig) -> None:
    if not config.tracker.kind:
        raise UnsupportedTrackerKind("tracker.kind is required")
    if config.tracker.kind not in SUPPORTED_TRACKER_KINDS:
        raise UnsupportedTrackerKind(
            "tracker kind not supported", kind=config.tracker.kind
        )
    if config.tracker.kind == "linear":
        if not config.tracker.api_key:
            raise MissingTrackerApiKey(
                "tracker.api_key missing or empty after $VAR resolution"
            )
        if not config.tracker.project_slug:
            raise MissingTrackerProjectSlug(
                "tracker.project_slug required for linear tracker"
            )
    if config.tracker.kind == "file":
        if config.tracker.board_root is None:
            raise ConfigValidationError(
                "tracker.board_root is required when tracker.kind=file"
            )
    kind = config.agent.kind
    if kind == "codex":
        if not config.codex.command.strip():
            raise ConfigValidationError("codex.command must be non-empty")
    elif kind == "claude":
        if not config.claude.command.strip():
            raise ConfigValidationError("claude.command must be non-empty")
    elif kind == "gemini":
        if not config.gemini.command.strip():
            raise ConfigValidationError("gemini.command must be non-empty")


# ---------------------------------------------------------------------------
# §6.2 — dynamic reload
# ---------------------------------------------------------------------------


class WorkflowState:
    """Last-known-good config holder for §6.2 reload semantics."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._config: ServiceConfig | None = None
        self._last_error: Exception | None = None
        self._lock = threading.RLock()

    def reload(self) -> tuple[ServiceConfig | None, Exception | None]:
        try:
            wf = load_workflow(self.path)
            cfg = build_service_config(wf)
        except Exception as exc:
            with self._lock:
                self._last_error = exc
            return None, exc
        with self._lock:
            self._config = cfg
            self._last_error = None
        return cfg, None

    def current(self) -> ServiceConfig | None:
        with self._lock:
            return self._config

    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error
