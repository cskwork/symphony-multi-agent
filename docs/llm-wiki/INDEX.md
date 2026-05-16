# docs/llm-wiki index

This directory (`docs/llm-wiki/`) is Symphony's domain knowledge base.
Each row points to a topic-scoped Markdown entry. Explore reads these
before any new ticket; Learn writes back to them after QA passes.

| topic-slug | summary | last touched |
|------------|---------|--------------|
| production-pipeline | Eight-stage pipeline + non-LLM Todo auto-triage + docs/<id>/<stage>/ artefact convention + WORKFLOW/PIPELINE sync invariant | 2026-05-17 (Todo auto-triage) |
| agent-observability | Headless event log signal set + cache token split + stall signatures + cross-refs to orchestrator/doctor/workspace | 2026-05-17 |
| session-persistence | Per-workspace `.symphony-session.json` + load on dispatch + save on session_started + per-backend honor-points + codex `thread/resume` fallback | 2026-05-10 (SMA-20) |
| tui-rendering | Textual `KanbanApp` widget tree + diff-mount card refresh + heartbeat / observer / tracker poll cadence + invariants the helpers preserve | 2026-05-10 (Textual migration) |
