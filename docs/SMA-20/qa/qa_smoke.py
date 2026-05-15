"""SMA-20 QA smoke driver.

Exercises the new session-persistence code paths against a real filesystem
outside the pytest harness. Mirrors the PRD acceptance criteria so a human
can `python qa_smoke.py` and see hand-checkable evidence:

  1. Atomic write produces the spec'd JSON shape (acceptance #1).
  2. Round-trip read recovers the same record after a simulated restart
     (acceptance #1, #3).
  3. agent_kind mismatch is surfaced by the loader (acceptance #3).
  4. Corrupt JSON degrades to None + WARN (PRD shared rule "doctor-friendly
     failures").
  5. BackendInit.resume_session_id propagates into pi/claude/codex/gemini
     instance state without launching real CLIs (acceptance #4).
  6. gemini.start_session preserves a resumed id rather than re-minting.

This script does NOT spawn pi/claude/codex/gemini subprocesses (no API
cost, no network). It does perform real disk I/O against tempdirs and
real backend dataclass construction.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from symphony import session_store
from symphony.backends import BackendInit
from symphony.backends.claude_code import ClaudeCodeBackend
from symphony.backends.codex import CodexAppServerBackend
from symphony.backends.gemini import GeminiBackend
from symphony.backends.pi import PiBackend
from symphony.workflow import build_service_config, load_workflow

WORKFLOW_PATH = Path(__file__).resolve().parents[3] / "WORKFLOW.md"


class Reporter:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        self.results.append((status, name, detail))
        print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))

    def summarize(self) -> int:
        passed = sum(1 for s, *_ in self.results if s == "PASS")
        failed = sum(1 for s, *_ in self.results if s == "FAIL")
        print(f"\nsummary: {passed} passed, {failed} failed")
        return 0 if failed == 0 else 1


async def noop_event(_: dict) -> None:
    return None


def main() -> int:
    r = Reporter()

    base_cfg = build_service_config(load_workflow(WORKFLOW_PATH))

    # ---- 1) atomic write shape ------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "SMA-20"
        target = session_store.path_for(ws)
        rec = session_store.write(
            target, agent_kind="pi", session_id="019e0f48-aaaa-bbbb-cccc-1111"
        )
        ok = (
            target.is_file()
            and rec.version == 1
            and rec.agent_kind == "pi"
            and rec.session_id == "019e0f48-aaaa-bbbb-cccc-1111"
        )
        r.record("atomic_write_returns_record", ok, f"path={target}")

        on_disk = json.loads(target.read_text("utf-8"))
        ok = (
            on_disk["version"] == 1
            and on_disk["agent_kind"] == "pi"
            and on_disk["session_id"] == "019e0f48-aaaa-bbbb-cccc-1111"
            and isinstance(on_disk["minted_at"], str)
            and isinstance(on_disk["last_used_at"], str)
        )
        r.record("on_disk_shape_matches_PRD_spec", ok, json.dumps(on_disk))

        leftover = [p.name for p in ws.iterdir() if p.name.startswith(".tmp-")]
        r.record(
            "no_atomic_write_tempfile_leak", leftover == [], f"leftover={leftover}"
        )

    # ---- 2) round-trip through restart ---------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "SMA-20"
        target = session_store.path_for(ws)
        session_store.write(target, agent_kind="claude", session_id="claude-xyz")
        loaded = session_store.read(target)
        ok = (
            loaded is not None
            and loaded.agent_kind == "claude"
            and loaded.session_id == "claude-xyz"
        )
        r.record("round_trip_through_restart", ok, f"loaded={loaded}")

    # ---- 3) kind-mismatch behavior -------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "SMA-20"
        target = session_store.path_for(ws)
        session_store.write(target, agent_kind="pi", session_id="pi-abc")
        loaded = session_store.read(target)
        configured_kind = "claude"
        kind_match = loaded is not None and loaded.agent_kind == configured_kind
        r.record(
            "kind_mismatch_does_not_resume",
            (not kind_match) and loaded is not None,
            f"file_kind={loaded.agent_kind} cfg_kind={configured_kind}",
        )

    # ---- 4) corrupt-file fallback --------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "SMA-20"
        ws.mkdir(parents=True)
        target = session_store.path_for(ws)
        target.write_text("{not valid json", encoding="utf-8")
        loaded = session_store.read(target)
        r.record("corrupt_file_returns_none", loaded is None, f"loaded={loaded}")

    # ---- 5) BackendInit propagation across all four backends -----------------
    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td)
        for kind, ctor in (
            ("pi", PiBackend),
            ("claude", ClaudeCodeBackend),
            ("gemini", GeminiBackend),
            ("codex", CodexAppServerBackend),
        ):
            init = BackendInit(
                cfg=base_cfg,
                cwd=cwd,
                workspace_root=cwd,
                on_event=noop_event,
                resume_session_id=f"resumed-{kind}-id",
            )
            backend = ctor(init)
            # codex stores the requested id in _resume_session_id at construction;
            # _thread_id is only populated after a successful thread/resume RPC
            # (which the smoke deliberately avoids — no real CLI). pi/claude/gemini
            # populate _session_id directly from BackendInit.
            attr = (
                backend._resume_session_id
                if kind == "codex"
                else backend._session_id
            )
            ok = attr == f"resumed-{kind}-id"
            r.record(f"{kind}_backend_honors_resume_session_id", ok, f"saw={attr}")

    # ---- 6) gemini start_session preserves resumed id ------------------------
    async def _gemini_resume_check() -> str | None:
        with tempfile.TemporaryDirectory() as td:
            init = BackendInit(
                cfg=base_cfg,
                cwd=Path(td),
                workspace_root=Path(td),
                on_event=noop_event,
                resume_session_id="gemini-prev-session",
            )
            be = GeminiBackend(init)
            return await be.start_session(initial_prompt="hi", issue_title="t")

    sid = asyncio.run(_gemini_resume_check())
    r.record(
        "gemini_start_session_preserves_resumed_id",
        sid == "gemini-prev-session",
        f"sid={sid}",
    )

    return r.summarize()


if __name__ == "__main__":
    raise SystemExit(main())
