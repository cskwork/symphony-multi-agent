# Production pipeline

**Summary:** Symphony drives every ticket through a seven-stage gated
pipeline (Todo -> Explore -> In Progress -> Review -> QA -> Learn ->
Done) implemented in the prompt, not the Python core. Three artefacts
must stay synchronized: the prompt body in `WORKFLOW.example.md` /
`WORKFLOW.file.example.md`, the operator guide in `docs/PIPELINE.md`,
and the assertion list in `tests/test_workflow_pipeline_prompt.py`.
Changing one without the others produces silent drift the orchestrator
cannot detect (states are just opaque strings to the Python core).

**Invariants & Constraints:**
- `tracker.active_states` in both example WORKFLOW files must equal
  `[Todo, Explore, "In Progress", Review, QA, Learn]` in that order.
  `Done`, `Cancelled`, `Blocked` are terminal states, not active.
- `docs/PIPELINE.md` is an operator guide, not a second source of truth.
  Its diagram, stage table, and `active_states` quote must mirror
  `WORKFLOW.file.example.md` verbatim. The acceptance test for any
  pipeline-shape change is "every claim in PIPELINE.md matches the
  active_states ordering and stage rules in the WORKFLOW example".
- `tests/test_workflow_pipeline_prompt.py` parametrizes both example
  WORKFLOW files and asserts: (a) every state in the seven-stage list
  appears in `active_states`, (b) every stage heading (`TRIAGE`,
  `EXPLORE`, `IMPLEMENT`, `REVIEW`, `QA`, `LEARN`, `DONE`) renders for
  every state, (c) Explore mentions `llm-wiki` + `git log` + the brief
  sections, (d) Learn mentions `llm-wiki` + `INDEX.md` + `Decision log`
  + `Wiki Updates`, (e) QA carries the literal phrase
  `THIS STAGE MUST EXECUTE REAL CODE` and `QA Evidence`, (f) Done
  carries the `## As-Is -> To-Be Report` shape with the four `### As-Is
  / ### To-Be / ### Reasoning / ### Evidence` subsections.
- `docs/llm-wiki/` lives under the workspace's `docs/` tree alongside
  per-ticket evidence roots (post-IB-004 layout; previously sat at the
  workspace root). The first Learn that runs creates the directory and
  seeds `INDEX.md`.
- The diagram in `WORKFLOW.file.example.md:96-102` (and its mirror in
  PIPELINE.md) shows two `+-> Blocked` branches, drawn after Review and
  after QA. The QA failure path actually rewinds to `In Progress`, not
  `Blocked`; the diagram's second Blocked branch represents Review's
  out-of-scope escape, not a QA-driven Blocked. Treat the diagram as a
  loose visual; the stage rules under each `### STAGE` heading are
  authoritative.
- Six-stage residue to grep for if you suspect drift: the literal string
  `Todo / In Progress  ->  Review` (old diagram) and
  `[Todo, "In Progress", Review, QA]` (old active_states quote).

**Files of interest:**
- `WORKFLOW.file.example.md:5` -- `active_states` for the file tracker.
- `WORKFLOW.file.example.md:96-102` -- canonical seven-stage diagram.
- `WORKFLOW.file.example.md:117-249` -- per-stage rules (TRIAGE / EXPLORE
  / IMPLEMENT / REVIEW / QA / LEARN / DONE).
- `WORKFLOW.example.md:6` -- same `active_states` list, Linear variant.
- `WORKFLOW.example.md:110-302` -- Linear variant of the stage rules
  (transitions via `linear_graphql`, stage notes via comments instead
  of in-body markdown sections).
- `docs/PIPELINE.md` -- operator guide. Diagram, stage table, llm-wiki
  pointer, adoption checklist.
- `docs/PIPELINE-DEMO.md` -- worked-example ticket. Note: still uses the
  six-stage section names (`## Plan` instead of `## Domain Brief` +
  `## Plan Candidates` + `## Recommendation`); only the Plan/Implementation/
  Review/QA Evidence/As-Is->To-Be sections are asserted by the test
  suite, so the demo is a partial example, not a full one.
- `tests/test_workflow_pipeline_prompt.py:38-81` -- the literal phrase
  list (`STAGE_HEADINGS`, `EXPLORE_HARD_RULES`, `LEARN_HARD_RULES`,
  `QA_HARD_RULES`, `DONE_REPORT_SHAPE`) that any pipeline-prompt change
  must keep satisfying.

**Decision log:**
- 2026-05-09 | SMK-1 | Brought `docs/PIPELINE.md` from 6 stages to 7
  (added Explore + Learn rows, llm-wiki pointer, refreshed adoption
  checklist). Did NOT update `docs/PIPELINE-DEMO.md` -- demo's section
  names are still the six-stage set but the test suite only asserts
  the subset that survived the rename, so the demo is a known partial
  example. Leaving its full conversion to a separate ticket avoids
  drive-by scope creep.
- 2026-05-09 | (this ticket) | Absorbed evidence-first ideas from cskwork/backend-dev-skills (MIT): bug-label reproduce sub-block in Triage, durable HTTP/e2e proofs in Review/QA, per-ticket docs/<id>/<stage>/ artefact root, LLM_WIKI_PATH env override.

**Last updated:** 2026-05-09 by (this ticket).
