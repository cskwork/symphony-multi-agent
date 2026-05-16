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
DEFAULT_TERMINAL_STATES = ("Closed", "Cancelled", "Canceled", "Duplicate", "Done", "Archive")
DEFAULT_BOARD_ROOT_NAME = "board"
DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 1
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_TOTAL_TURNS = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_CODEX_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_CODEX_READ_TIMEOUT_MS = 5_000
DEFAULT_CODEX_STALL_TIMEOUT_MS = 300_000
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "high"
DEFAULT_WORKSPACE_REUSE_POLICY = "preserve"
SUPPORTED_WORKSPACE_REUSE_POLICIES = {"preserve", "refresh"}

DEFAULT_PROMPT = "You are working on an issue from Linear."

SUPPORTED_AGENT_KINDS = {"codex", "claude", "gemini", "pi"}
DEFAULT_AGENT_KIND = "codex"
DEFAULT_CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --include-partial-messages --verbose"
)
# `gemini -p` (no argument) prints help and exits in Gemini CLI 0.39+ — the
# `-p`/`--prompt` flag now requires a string. We pass an empty string so
# stdin alone is the prompt (Gemini documents stdin as "Appended to input on
# stdin (if any).").
DEFAULT_GEMINI_COMMAND = 'gemini -p ""'
# Pi (https://pi.dev) print mode: `-p ""` lets stdin carry the full prompt;
# `--mode json` switches stdout to JSONL events so we can parse session id,
# turn boundaries, and per-message token usage.
DEFAULT_PI_COMMAND = 'pi --mode json -p ""'
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
    # Optional one-line description rendered as a legend under each column
    # title in the TUI. Keys are state names (case-insensitive match against
    # active_states / terminal_states); values are short human-readable
    # explanations of what work happens in that lane.
    state_descriptions: dict[str, str] = field(default_factory=dict)
    # Auto-archive sweep — every poll tick, terminal-state issues whose
    # `updated_at` is older than `archive_after_days` get moved to the
    # `archive_state` lane. Set `archive_after_days` to 0 to disable
    # sweep entirely (the manual TUI hotkey still works). The `archive_state`
    # name must also appear in `terminal_states` so the lane renders.
    archive_state: str = "Archive"
    archive_after_days: int = 30


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None
    before_run: str | None
    after_run: str | None
    before_remove: str | None
    timeout_ms: int
    # Fires once per ticket immediately after `commit_workspace_on_done`
    # succeeds AND the ticket reached `Done`. Receives the standard hook
    # env plus `SYMPHONY_ISSUE_ID` and `SYMPHONY_ISSUE_TITLE`. Lenient —
    # failures only log a warning and never block worker cleanup. Default
    # None preserves legacy behaviour and keeps existing positional
    # `HooksConfig(...)` callers source-compatible.
    after_done: str | None = None


DEFAULT_AUTO_MERGE_EXCLUDE_PATHS: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    kind: str
    max_concurrent_agents: int
    max_turns: int
    max_retry_backoff_ms: int
    max_concurrent_agents_by_state: dict[str, int]
    max_total_turns: int = DEFAULT_MAX_TOTAL_TURNS
    # Soft cap for Review/QA rewinds back into In Progress. 0 disables.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # When a ticket reaches the Done state cleanly, snapshot the workspace
    # into a single git commit (`git init` if no enclosing repo found).
    # Default ON so a fresh `pip install symphony-multi-agent` plus a
    # WORKFLOW.md is enough to get a per-ticket commit trail without
    # wiring an after_run hook. Set to false in WORKFLOW.md when the
    # workspace is e.g. an existing repo with strict commit-style rules.
    auto_commit_on_done: bool = True
    # After auto-commit on Done, optionally fold the symphony/<ID> branch
    # back into the host repo's main development branch with an explicit
    # `git merge --no-ff` commit. Paths in `auto_merge_exclude_paths` are
    # guardrails: if any of them changed on the branch, the merge is
    # blocked because those roots are workspace plumbing, not deliverables.
    # Keep docs branch-local so reports/wiki updates ride with the merge.
    # Safe-by-default: a dirty host working tree that overlaps branch
    # changes or any git error skips the merge and logs an event — no
    # exception propagates.
    auto_merge_on_done: bool = True
    # Target branch in the host repo. Empty string ("") = use whatever
    # branch is currently checked out in the host repo at fire time.
    auto_merge_target_branch: str = ""
    # Workspace-only roots that must not differ on the ticket branch.
    # File-board workflows usually set this to `["kanban"]`; add `prompt`
    # only if your hook symlinks it from the host. Do not list `docs`
    # unless you intentionally made docs host-owned and accept that docs
    # will not be branch deliverables.
    auto_merge_exclude_paths: tuple[str, ...] = DEFAULT_AUTO_MERGE_EXCLUDE_PATHS
    # Legacy escape hatch for workflows that intentionally keep a report
    # tree host-owned. Prefer branch-local docs instead. Captured files are
    # added to the same `--no-ff` merge commit.
    auto_merge_capture_untracked: tuple[str, ...] = ()
    # What to do when the `after_done` hook fails. "warn" (default,
    # legacy) just logs `hook_after_done_failed` and the orchestrator
    # removes the workspace as usual — a failed dev/prod-apply script
    # then looks like a clean Done. "block" preserves the workspace,
    # marks the ticket with `last_error`, and skips workspace removal so
    # an operator can investigate before the worktree is reaped. Pair
    # with a production-critical `after_done` (deploy / apply-to-host)
    # to avoid silent partial completions.
    after_done_failure_policy: str = "warn"
    # Hard cap on cumulative `total_tokens` per dispatched ticket
    # (input + output across all turns). 0 = disabled (legacy). When set,
    # `_on_codex_event` cancels the worker the moment the running total
    # crosses the cap and marks `last_error="token budget exceeded"` so
    # an operator sees the brake reason without log-diving. Pair with a
    # generous `max_turns` to catch runaway-reasoning loops (e.g. codex
    # accumulating 1.6M tokens / turn over a dozen turns) that the
    # progress-timestamp stall predicate can't see because turns ARE
    # completing.
    max_total_tokens: int = 0
    # Optional per-state override for `max_total_tokens`. Keys are state
    # names lowercased by the parser, e.g. "review" or "in progress".
    max_total_tokens_by_state: dict[str, int] = field(default_factory=dict)
    # Target tracker state to transition the ticket to when
    # `max_total_turns` is exhausted. Empty string (default, legacy) =
    # no transition; the in-memory `_turn_budget_exhausted` guard alone
    # suppresses re-dispatch within this process — a service restart
    # then clears the guard and the same ticket can run again. Set this
    # to a non-active state name (e.g. "Blocked" or your tracker's
    # equivalent) to persist the exhaustion via the tracker, so the
    # decision survives restart and reaches operators reviewing the
    # board. Must match a state your tracker.kind backend can write to.
    budget_exhausted_state: str = ""


@dataclass(frozen=True)
class CodexConfig:
    command: str
    approval_policy: Any
    thread_sandbox: Any
    turn_sandbox_policy: Any
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT


@dataclass(frozen=True)
class ClaudeConfig:
    """`agent.kind: claude` — driving Claude Code CLI in print/stream mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, turns 2+ within one worker attempt add `--resume <session_id>`
    # so Claude rejoins the prior session instead of starting fresh. Cross-
    # attempt resume (after a worker error / retry) is intentionally NOT
    # supported — each retry attempt builds a new backend instance, so the
    # captured session id is discarded with the prior worker.
    resume_across_turns: bool


@dataclass(frozen=True)
class GeminiConfig:
    """`agent.kind: gemini` — driving Gemini CLI as one-shot per turn."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int


@dataclass(frozen=True)
class PiConfig:
    """`agent.kind: pi` — driving the Pi coding-agent CLI in print/json mode."""

    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    # When True, turns 2+ within one worker attempt add `--session <id>` so Pi
    # rejoins the prior session. Cross-attempt resume is intentionally not
    # supported — each retry attempt builds a new backend instance.
    resume_across_turns: bool


@dataclass(frozen=True)
class ServerConfig:
    """§13.7 optional HTTP extension."""

    port: int | None


@dataclass(frozen=True)
class TuiConfig:
    """Display-time TUI tweaks. Affects rendering only; orchestrator ignores."""

    # ISO-639-1 language code used to look up localized chrome strings
    # (column placeholder, header / footer field labels, card meta verbs).
    # Tracker state names, ticket titles, and `state_descriptions` come from
    # user data and are never translated. Defaults to "en".
    language: str = "en"

    # How many Kanban lanes show simultaneously in the board. The remaining
    # lanes are paged off-screen — `t` cycles to the next window of lanes,
    # `shift+t` to the previous, `+`/`-` grow/shrink the window at runtime.
    # Default 5 keeps each card column wide enough to read on a 120-col
    # terminal even with the default detail pane visible. The TUI clamps
    # values <1 up to 1 so a malformed config doesn't blank the board.
    visible_lanes: int = 5


@dataclass(frozen=True)
class ProgressConfig:
    """Optional WORKFLOW-PROGRESS.md mirror written by the orchestrator.

    `path` defaults to `WORKFLOW-PROGRESS.md` next to WORKFLOW.md when the
    user enables progress without specifying a path. `enabled=True` is the
    out-of-the-box default; the CLI's `--no-progress-md` flag flips it off
    without editing the workflow file.
    """

    enabled: bool = True
    path: Path | None = None
    max_transitions: int = 20


@dataclass(frozen=True)
class PromptConfig:
    """External prompt files configured from WORKFLOW.md.

    `base_template` is shared across all states. `stage_templates` is keyed
    by normalized tracker state and contains only the current-stage rule body.
    """

    base_template: str = ""
    base_path: Path | None = None
    stage_templates: dict[str, str] = field(default_factory=dict)
    stage_paths: dict[str, Path] = field(default_factory=dict)

    def has_stage_prompts(self) -> bool:
        return bool(self.stage_templates)


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
    pi: PiConfig
    server: ServerConfig
    tui: TuiConfig = field(default_factory=TuiConfig)
    progress: ProgressConfig = field(default_factory=ProgressConfig)
    prompts: PromptConfig = field(default_factory=PromptConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    prompt_template: str = ""
    workspace_reuse_policy: str = DEFAULT_WORKSPACE_REUSE_POLICY

    def prompt_template_for_state(self, state: str) -> str:
        """Return the runtime prompt template for one tracker state."""
        key = _normalize_state_key(state)
        stage_template = self.prompts.stage_templates.get(key)
        if stage_template is None:
            return self.prompt_template
        parts = [self.prompts.base_template, stage_template]
        return "\n\n".join(part for part in parts if part)

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
        if kind == "pi":
            return (
                self.pi.turn_timeout_ms,
                self.pi.read_timeout_ms,
                self.pi.stall_timeout_ms,
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


def _normalize_state_key(value: str) -> str:
    return value.strip().lower()


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


def _normalize_state_description_map(value: Any) -> dict[str, str]:
    """tracker.state_descriptions — keys lowercased, non-string entries dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        out[key.lower()] = text
    return out


def _resolve_config_path(base_dir: Path, value: str) -> Path:
    resolved = resolve_var_indirection(value) if value.startswith("$") else value
    if not isinstance(resolved, str) or not resolved:
        return base_dir
    path = Path(expand_path_value(resolved))
    if not path.is_absolute():
        return (base_dir / path).resolve()
    return path.resolve()


def _read_prompt_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ConfigValidationError("prompt file not found", path=str(path)) from exc
    except OSError as exc:
        raise ConfigValidationError(
            "prompt file unreadable", path=str(path), error=str(exc)
        ) from exc


def _build_prompt_config(raw: Any, base_dir: Path) -> PromptConfig:
    if not isinstance(raw, dict):
        return PromptConfig()

    base_template = ""
    base_path: Path | None = None
    raw_base = raw.get("base")
    if isinstance(raw_base, str) and raw_base.strip():
        base_path = _resolve_config_path(base_dir, raw_base.strip())
        base_template = _read_prompt_file(base_path)

    stage_templates: dict[str, str] = {}
    stage_paths: dict[str, Path] = {}
    raw_stages = raw.get("stages")
    if isinstance(raw_stages, dict):
        for raw_state, raw_path in raw_stages.items():
            if not isinstance(raw_state, str):
                continue
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            key = _normalize_state_key(raw_state)
            path = _resolve_config_path(base_dir, raw_path.strip())
            stage_paths[key] = path
            stage_templates[key] = _read_prompt_file(path)

    return PromptConfig(
        base_template=base_template,
        base_path=base_path,
        stage_templates=stage_templates,
        stage_paths=stage_paths,
    )


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

    archive_after_raw = tracker_raw.get("archive_after_days")
    if archive_after_raw is None:
        archive_after_days = 30
    elif isinstance(archive_after_raw, bool) or not isinstance(archive_after_raw, int):
        # Reject bools (which `int` accepts) and non-int types up front so
        # `archive_after_days: true` doesn't silently mean 1 day.
        raise ConfigValidationError(
            "tracker.archive_after_days must be a non-negative integer",
            value=archive_after_raw,
        )
    elif archive_after_raw < 0:
        raise ConfigValidationError(
            "tracker.archive_after_days must be a non-negative integer",
            value=archive_after_raw,
        )
    else:
        archive_after_days = archive_after_raw

    archive_state_raw = tracker_raw.get("archive_state")
    archive_state = (
        archive_state_raw.strip()
        if isinstance(archive_state_raw, str) and archive_state_raw.strip()
        else "Archive"
    )

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
        state_descriptions=_normalize_state_description_map(
            tracker_raw.get("state_descriptions")
        ),
        archive_state=archive_state,
        archive_after_days=archive_after_days,
    )

    polling_raw = cfg.get("polling") or {}
    if not isinstance(polling_raw, dict):
        polling_raw = {}
    poll_interval_ms = _validated_positive_or_default(
        polling_raw.get("interval_ms"), DEFAULT_POLL_INTERVAL_MS, name="polling.interval_ms"
    )

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
    workspace_reuse_policy = _as_str(
        workspace_raw.get("reuse_policy"), DEFAULT_WORKSPACE_REUSE_POLICY
    ).strip().lower() or DEFAULT_WORKSPACE_REUSE_POLICY
    if workspace_reuse_policy not in SUPPORTED_WORKSPACE_REUSE_POLICIES:
        raise ConfigValidationError(
            "workspace.reuse_policy must be one of "
            f"{sorted(SUPPORTED_WORKSPACE_REUSE_POLICIES)}",
            value=workspace_reuse_policy,
        )

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
        after_done=hooks_raw.get("after_done") if isinstance(hooks_raw.get("after_done"), str) else None,
    )

    agent_raw = cfg.get("agent") or {}
    if not isinstance(agent_raw, dict):
        agent_raw = {}
    max_turns = _validated_positive_or_default(
        agent_raw.get("max_turns"), DEFAULT_MAX_TURNS, name="agent.max_turns"
    )
    max_total_turns = _validated_positive_or_default(
        agent_raw.get("max_total_turns"),
        DEFAULT_MAX_TOTAL_TURNS,
        name="agent.max_total_turns",
    )
    agent_kind = _as_str(agent_raw.get("kind"), DEFAULT_AGENT_KIND).strip().lower() or DEFAULT_AGENT_KIND
    if agent_kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}",
            value=agent_kind,
        )
    agent = AgentConfig(
        kind=agent_kind,
        max_concurrent_agents=_validated_positive_or_default(
            agent_raw.get("max_concurrent_agents"),
            DEFAULT_MAX_CONCURRENT_AGENTS,
            name="agent.max_concurrent_agents",
        ),
        max_turns=max_turns,
        max_retry_backoff_ms=_validated_positive_or_default(
            agent_raw.get("max_retry_backoff_ms"),
            DEFAULT_MAX_RETRY_BACKOFF_MS,
            name="agent.max_retry_backoff_ms",
        ),
        max_concurrent_agents_by_state=_normalize_state_map(
            agent_raw.get("max_concurrent_agents_by_state")
        ),
        max_total_turns=max_total_turns,
        max_attempts=_validated_nonnegative_or_default(
            agent_raw.get("max_attempts"),
            DEFAULT_MAX_ATTEMPTS,
            name="agent.max_attempts",
        ),
        auto_commit_on_done=bool(
            agent_raw.get("auto_commit_on_done", True)
        ),
        auto_merge_on_done=bool(
            agent_raw.get("auto_merge_on_done", True)
        ),
        auto_merge_target_branch=_as_str(
            agent_raw.get("auto_merge_target_branch"), ""
        ) or "",
        auto_merge_exclude_paths=_as_str_list(
            agent_raw.get("auto_merge_exclude_paths"),
            DEFAULT_AUTO_MERGE_EXCLUDE_PATHS,
        ),
        auto_merge_capture_untracked=_as_str_list(
            agent_raw.get("auto_merge_capture_untracked"),
            (),
        ),
        after_done_failure_policy=_validated_after_done_failure_policy(
            agent_raw.get("after_done_failure_policy"),
        ),
        max_total_tokens=_validated_nonnegative_or_default(
            agent_raw.get("max_total_tokens"),
            0,
            name="agent.max_total_tokens",
        ),
        max_total_tokens_by_state=_normalize_state_map(
            agent_raw.get("max_total_tokens_by_state")
        ),
        budget_exhausted_state=_as_str(
            agent_raw.get("budget_exhausted_state"), ""
        ) or "",
    )

    codex_raw = cfg.get("codex") or {}
    if not isinstance(codex_raw, dict):
        codex_raw = {}
    codex = CodexConfig(
        command=_as_str(codex_raw.get("command"), DEFAULT_CODEX_COMMAND) or DEFAULT_CODEX_COMMAND,
        approval_policy=codex_raw.get("approval_policy"),
        thread_sandbox=codex_raw.get("thread_sandbox"),
        turn_sandbox_policy=codex_raw.get("turn_sandbox_policy"),
        turn_timeout_ms=_validated_positive_or_default(
            codex_raw.get("turn_timeout_ms"), DEFAULT_CODEX_TURN_TIMEOUT_MS, name="codex.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            codex_raw.get("read_timeout_ms"), DEFAULT_CODEX_READ_TIMEOUT_MS, name="codex.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            codex_raw.get("stall_timeout_ms"), DEFAULT_CODEX_STALL_TIMEOUT_MS, name="codex.stall_timeout_ms"
        ),
        model=_as_str(codex_raw.get("model"), DEFAULT_CODEX_MODEL) or DEFAULT_CODEX_MODEL,
        reasoning_effort=_as_str(
            codex_raw.get("reasoning_effort"), DEFAULT_CODEX_REASONING_EFFORT
        ) or DEFAULT_CODEX_REASONING_EFFORT,
    )

    claude_raw = cfg.get("claude") or {}
    if not isinstance(claude_raw, dict):
        claude_raw = {}
    claude = ClaudeConfig(
        command=_as_str(claude_raw.get("command"), DEFAULT_CLAUDE_COMMAND) or DEFAULT_CLAUDE_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            claude_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="claude.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            claude_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="claude.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            claude_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="claude.stall_timeout_ms"
        ),
        resume_across_turns=bool(claude_raw.get("resume_across_turns", True)),
    )

    gemini_raw = cfg.get("gemini") or {}
    if not isinstance(gemini_raw, dict):
        gemini_raw = {}
    gemini = GeminiConfig(
        command=_as_str(gemini_raw.get("command"), DEFAULT_GEMINI_COMMAND) or DEFAULT_GEMINI_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="gemini.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="gemini.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            gemini_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="gemini.stall_timeout_ms"
        ),
    )

    pi_raw = cfg.get("pi") or {}
    if not isinstance(pi_raw, dict):
        pi_raw = {}
    pi = PiConfig(
        command=_as_str(pi_raw.get("command"), DEFAULT_PI_COMMAND) or DEFAULT_PI_COMMAND,
        turn_timeout_ms=_validated_positive_or_default(
            pi_raw.get("turn_timeout_ms"), DEFAULT_BACKEND_TURN_TIMEOUT_MS, name="pi.turn_timeout_ms"
        ),
        read_timeout_ms=_validated_positive_or_default(
            pi_raw.get("read_timeout_ms"), DEFAULT_BACKEND_READ_TIMEOUT_MS, name="pi.read_timeout_ms"
        ),
        stall_timeout_ms=_validated_positive_or_default(
            pi_raw.get("stall_timeout_ms"), DEFAULT_BACKEND_STALL_TIMEOUT_MS, name="pi.stall_timeout_ms"
        ),
        resume_across_turns=bool(pi_raw.get("resume_across_turns", True)),
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

    tui_raw = cfg.get("tui") or {}
    if not isinstance(tui_raw, dict):
        tui_raw = {}
    from .i18n import resolve_language
    # SYMPHONY_LANG env var takes precedence over WORKFLOW.md so a single
    # operator can flip without editing the shared workflow file.
    # `_as_int(..., allow_zero=False)` rejects 0/negative as invalid → falls
    # back to default 5. Belt-and-suspenders `max(1, ...)` covers the case
    # where a user sets `visible_lanes: 0` deliberately and the helper still
    # returns it through the allow_zero path elsewhere.
    visible_lanes = max(1, _as_int(tui_raw.get("visible_lanes"), 5, allow_zero=False))
    tui = TuiConfig(
        language=resolve_language(tui_raw.get("language")),
        visible_lanes=visible_lanes,
    )

    progress_raw = cfg.get("progress") or {}
    if not isinstance(progress_raw, dict):
        progress_raw = {}
    raw_enabled = progress_raw.get("enabled", True)
    if isinstance(raw_enabled, bool):
        progress_enabled = raw_enabled
    else:
        # Mirror archive_after_days: refuse silent coercions of 0/1/"true".
        raise ConfigValidationError(
            "progress.enabled must be a boolean", value=raw_enabled
        )
    raw_path = progress_raw.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        resolved_path = (
            resolve_var_indirection(raw_path) if raw_path.startswith("$") else raw_path
        )
        candidate = Path(expand_path_value(str(resolved_path)))
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        progress_path: Path | None = candidate
    else:
        progress_path = (base_dir / "WORKFLOW-PROGRESS.md").resolve()
    raw_max_transitions = progress_raw.get("max_transitions")
    if raw_max_transitions is None:
        max_transitions = 20
    elif isinstance(raw_max_transitions, bool) or not isinstance(raw_max_transitions, int):
        raise ConfigValidationError(
            "progress.max_transitions must be a non-negative integer",
            value=raw_max_transitions,
        )
    elif raw_max_transitions < 0:
        raise ConfigValidationError(
            "progress.max_transitions must be a non-negative integer",
            value=raw_max_transitions,
        )
    else:
        max_transitions = raw_max_transitions
    progress = ProgressConfig(
        enabled=progress_enabled,
        path=progress_path,
        max_transitions=max_transitions,
    )

    prompt_template = workflow.prompt_template or DEFAULT_PROMPT
    prompts = _build_prompt_config(cfg.get("prompts"), base_dir)

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
        pi=pi,
        server=server,
        tui=tui,
        progress=progress,
        prompts=prompts,
        raw=dict(cfg),
        prompt_template=prompt_template,
        workspace_reuse_policy=workspace_reuse_policy,
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


def _validated_nonnegative_or_default(value: Any, default: int, *, name: str) -> int:
    """Validate counters where 0 is an explicit off switch."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigValidationError(f"{name} must be a non-negative integer", value=value)
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(
            f"{name} must be a non-negative integer", value=value
        ) from exc
    if ivalue < 0:
        raise ConfigValidationError(f"{name} must be a non-negative integer", value=value)
    return ivalue


_AFTER_DONE_FAILURE_POLICIES = ("warn", "block")


def _validated_after_done_failure_policy(value: Any) -> str:
    """Accept 'warn' (default) or 'block'. Anything else is a config error."""
    if value is None:
        return "warn"
    if not isinstance(value, str) or value not in _AFTER_DONE_FAILURE_POLICIES:
        raise ConfigValidationError(
            "agent.after_done_failure_policy must be one of "
            f"{list(_AFTER_DONE_FAILURE_POLICIES)}",
            value=value,
        )
    return value


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
    elif kind == "pi":
        if not config.pi.command.strip():
            raise ConfigValidationError("pi.command must be non-empty")


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
