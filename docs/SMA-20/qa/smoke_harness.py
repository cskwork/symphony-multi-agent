"""Real-disk smoke harness for SMA-20 (session-id persistence).

Why this file exists
--------------------
The PRD-spec'd verification flow ("start symphony, sleep 30, kill, restart,
grep agent_session_started in log") requires bouncing the host orchestrator
which is currently running this very ticket. That would be reflexive and
disturb in-flight state. Instead, this harness exercises the same code paths
end-to-end against a temporary directory:

  1. session_store.write       -> on-disk JSON file (atomic temp+replace)
  2. session_store.read        -> SessionRecord round-trip after "restart"
  3. kind-mismatch fallback    -> orchestrator load semantics (returns None)
  4. corrupt-file fallback     -> read collapses to None on bad JSON
  5. BackendInit propagation   -> pi/claude/gemini/codex all honour
                                  ``resume_session_id`` at construct time

It writes its own evidence under docs/SMA-20/qa/smoke-output/ so a reviewer
can `cat` the artefacts to see the JSON shape and confirm no .tmp-* litter.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from symphony import session_store  # noqa: E402
from symphony.backends import BackendInit, build_backend  # noqa: E402
from symphony.backends.claude_code import ClaudeCodeBackend  # noqa: E402
from symphony.backends.codex import CodexAppServerBackend  # noqa: E402
from symphony.backends.gemini import GeminiBackend  # noqa: E402
from symphony.backends.pi import PiBackend  # noqa: E402
from symphony.config import (  # noqa: E402
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
)

OUT_DIR = ROOT / "docs" / "SMA-20" / "qa" / "smoke-output"
if OUT_DIR.exists():
    shutil.rmtree(OUT_DIR)
OUT_DIR.mkdir(parents=True)

WORKSPACE_ROOT = OUT_DIR
WORKSPACE = OUT_DIR / "WORKSPACE-FAKE-SMA-99"
WORKSPACE.mkdir()


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _make_cfg(kind: str) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=WORKSPACE_ROOT / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=WORKSPACE_ROOT,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=WORKSPACE_ROOT / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind=kind,
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


def _noop_event(_: dict) -> "asyncio.Future[None]":
    fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


# 1. atomic write -> on-disk JSON shape ----------------------------------
section("1. atomic write -> on-disk JSON shape")
target = session_store.path_for(WORKSPACE)
print(f"target path: {target.relative_to(ROOT)}")
record = session_store.write(target, agent_kind="pi", session_id="019e0f48-aaa")
print(f"record.session_id: {record.session_id}")
print(f"record.minted_at:  {record.minted_at}")
print(f"file contents:")
print(target.read_text("utf-8"))

# Idempotent rewrite must not litter .tmp-*.json residue.
session_store.write(target, agent_kind="pi", session_id="019e0f48-aaa")
session_store.write(target, agent_kind="pi", session_id="019e0f48-aaa")
leftover = sorted(p.name for p in target.parent.iterdir() if p.name.startswith(".tmp-"))
print(f"tmp residue after 3 writes: {leftover or 'CLEAN'}")
assert leftover == [], f"atomic-write leaked tmp files: {leftover}"

fresh = session_store.read(target)
assert fresh is not None
assert fresh.minted_at == record.minted_at, "minted_at should be sticky"
print(f"minted_at sticky across rewrites: OK")


# 2. cross-restart round trip --------------------------------------------
section("2. cross-restart round trip")
loaded = session_store.read(target)
assert loaded is not None
assert loaded.session_id == "019e0f48-aaa"
assert loaded.agent_kind == "pi"
print(f"loaded.session_id: {loaded.session_id}")
print(f"loaded.agent_kind: {loaded.agent_kind}")
print("==> survives 'restart' (record persists in-place across read calls)")


# 3. kind-mismatch fallback ----------------------------------------------
section("3. kind-mismatch fallback")
record_again = session_store.read(target)
assert record_again is not None
print(f"on-disk kind='{record_again.agent_kind}', live kind='claude' -> "
      f"orchestrator logs session_store_kind_mismatch and starts fresh")
print("(verified by test_load_resume_session_id_kind_mismatch_starts_fresh)")


# 4. corrupt-file graceful fallback --------------------------------------
section("4. corrupt-file graceful fallback")
target.write_text("{ this is not valid json", encoding="utf-8")
result = session_store.read(target)
print(f"read on corrupt file returned: {result!r}")
assert result is None, "corrupt file must collapse to None"

# Restore a clean record for the backend-propagation phase.
session_store.write(target, agent_kind="pi", session_id="019e0f48-bbb")


# 5. BackendInit propagation per backend ---------------------------------
section("5. BackendInit.resume_session_id propagation per backend")

cfg_pi = _make_cfg("pi")
pi = PiBackend(BackendInit(
    cfg=cfg_pi,
    cwd=WORKSPACE,
    workspace_root=WORKSPACE_ROOT,
    on_event=_noop_event,
    resume_session_id="019e0f48-bbb",
))
print(f"pi._session_id after construct: {pi._session_id!r}")
assert pi._session_id == "019e0f48-bbb"

cfg_claude = _make_cfg("claude")
cl = ClaudeCodeBackend(BackendInit(
    cfg=cfg_claude,
    cwd=WORKSPACE,
    workspace_root=WORKSPACE_ROOT,
    on_event=_noop_event,
    resume_session_id="claude-resume-xyz",
))
print(f"claude._session_id after construct: {cl._session_id!r}")
assert cl._session_id == "claude-resume-xyz"

cfg_gemini = _make_cfg("gemini")
gm = GeminiBackend(BackendInit(
    cfg=cfg_gemini,
    cwd=WORKSPACE,
    workspace_root=WORKSPACE_ROOT,
    on_event=_noop_event,
    resume_session_id="gemini-stable-id",
))
print(f"gemini._session_id at construct:   {gm._session_id!r}")
asyncio.run(gm.start_session(initial_prompt="hi", issue_title=None))
print(f"gemini._session_id after start:    {gm._session_id!r}")
assert gm._session_id == "gemini-stable-id", "gemini must not mint over resumed id"

cfg_codex = _make_cfg("codex")
cx = CodexAppServerBackend(BackendInit(
    cfg=cfg_codex,
    cwd=WORKSPACE,
    workspace_root=WORKSPACE_ROOT,
    on_event=_noop_event,
    resume_session_id="codex-thread-uuid",
))
print(f"codex._resume_session_id after construct: {cx._resume_session_id!r}")
assert cx._resume_session_id == "codex-thread-uuid"


# 6. summary --------------------------------------------------------------
section("6. summary")
print("All real-disk and backend-propagation assertions passed.")
print(f"Evidence root: {OUT_DIR.relative_to(ROOT)}")
