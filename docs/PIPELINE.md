# Production pipeline

Symphony's default task drives every ticket through seven gated stages.
`WORKFLOW.md` remains the orchestration manifest, while the default agent
instructions live in `docs/symphony-prompts/`. The harness picks the
ticket up on each poll tick; the agent moves it from one stage to the next
by editing the ticket file's `state` field. The orchestrator only reads, so
this stage machine is intentionally implemented in prompt files rather
than the Python core.

```
  Todo  ->  Explore  ->  In Progress  ->  Review  ->  QA  ->  Learn  ->  Done
                              \                       \                    ^
                               +-> Blocked             +-> Blocked          |
                                                                            |
                              (QA failure rewinds to In Progress)
```

| State          | Owner of this turn          | Output it must produce                                                |
|----------------|-----------------------------|-----------------------------------------------------------------------|
| Todo           | triager                     | `## Triage` line, route to Explore (or `Blocked`)                     |
| Explore        | researcher (3 lenses)       | `## Domain Brief` + `## Plan Candidates` + `## Recommendation`        |
| In Progress    | implementer                 | `## Implementation` (TDD), transition to Review                       |
| Review         | reviewer                    | `## Review`, fix CRITICAL/HIGH, transition to QA                      |
| QA             | qa runner (executes code)   | `## QA Evidence` (real exit codes), -> Learn                          |
| Learn          | distiller                   | `llm-wiki/` updates + `## Learnings` + `## Wiki Updates`              |
| Done           | reporter                    | `## As-Is -> To-Be Report` (structured)                               |
| Blocked        | -                           | `## Blocker` describing what is needed                                |

## Why an Explore stage

Implementation that starts from whatever stale context the agent happens to
have produces drive-by refactors and missed invariants. Explore forces a
single, structured pass over three sources before any code changes:

- **`llm-wiki/`**
- **git history** — `git log --oneline -- <path>` for files the ticket
  likely touches, then `git show <sha>` on the relevant commits to
  recover the *why* behind prior changes.
- **the source files themselves**, end-to-end, so the brief reflects
  current state and not stale memory.

Source 1 (llm-wiki) is documented in [The llm-wiki/ knowledge base](#the-llm-wiki-knowledge-base) below.

The agent applies three lenses in one turn — domain expert, implementer,
risk reviewer — and writes `## Domain Brief`, `## Plan Candidates`, and
`## Recommendation` into the ticket. Implement reads the recommendation
and follows it; it does not re-plan unless the brief got a fact wrong.

## Why a QA stage

A code review confirms intent; only execution confirms behaviour. The QA
stage forces the agent to run real commands and capture the output:

- **Tests** — the project's full suite (`pytest`, `npm test`, `go test`, ...).
- **HTTP APIs** — curl / httpie against the baseline (As-Is) and against
  the new build (To-Be); diff the two responses.
- **Web UIs** — a Playwright or Cypress script that walks the flow
  end-to-end. Screenshots and traces land in `qa-artifacts/`.
- **CLIs** — run the command, assert exit code and observable output.

A QA pass that only inspects code is a failed QA. The agent must record
the exact commands it ran, their exit codes, and a short excerpt of the
output under `## QA Evidence` in the ticket.

If anything fails the agent rewinds the ticket to `In Progress` and writes
a `## QA Failure` section. It must not silence, retry, or skip the failing
check.

## Why a Learn stage

QA proves the change works; Learn makes the *next* ticket cheaper. After
QA passes, the agent compares the Explore brief against reality — which
assumptions held, which were wrong, what only became visible during
implementation — and persists the delta to `llm-wiki/`:

See the LEARN stage rule in WORKFLOW.file.example.md for the canonical wiki-entry template.

The agent then writes `## Learnings` (bullets of new facts and
surprises) and `## Wiki Updates` (paths created or modified) into the
ticket before transitioning to Done. If nothing genuinely new emerged,
the agent says so explicitly ("no new wiki entries; existing coverage
was correct") and still transitions.

## The `llm-wiki/` knowledge base

`llm-wiki/` lives at the workspace root next to the source code (parallel
to `kanban/`). It is one Markdown entry per topic plus an `INDEX.md`
that lists them. Treat it as a living memory that future tickets depend
on: Explore reads it before any new work, Learn writes back to it after
QA passes, and the first Learn stage to run creates the directory if it
does not yet exist. The wiki is the project's institutional knowledge in
prompt-friendly form — keep entries focused on invariants, constraints,
and decision history, not transient task state.

## The Done report

`Done` is not a "stop" signal — it is a "produce a report" signal. Every
completed ticket carries an `## As-Is -> To-Be Report` block that captures:

- **As-Is** — prior behaviour, evidence-backed.
- **To-Be** — new behaviour, evidence-backed (same shape as As-Is so they
  can be diffed visually).
- **Reasoning** — why this approach over alternatives, trade-offs accepted,
  follow-ups intentionally deferred.
- **Evidence** — commands run during QA, test names, artefact paths.

The block lives in the ticket body so future readers (and future agent
runs) get the full story without spelunking through git or chat history.

## Stage prompts

The example workflows use a stage-specific prompt manifest:

```yaml
prompts:
  base: ./docs/symphony-prompts/file/base.md
  stages:
    Todo: ./docs/symphony-prompts/file/stages/todo.md
    Explore: ./docs/symphony-prompts/file/stages/explore.md
    "In Progress": ./docs/symphony-prompts/file/stages/in-progress.md
    Review: ./docs/symphony-prompts/file/stages/review.md
    QA: ./docs/symphony-prompts/file/stages/qa.md
    Learn: ./docs/symphony-prompts/file/stages/learn.md
    Done: ./docs/symphony-prompts/file/stages/done.md
```

At runtime Symphony assembles `base` plus the one file matching
`{{ issue.state }}`. A `Todo` ticket receives the triage prompt, an
`Explore` ticket receives the Explore prompt, and so on; unrelated stage
rules are not sent on that turn. If `prompts.stages` is omitted, Symphony
falls back to the legacy inline body of `WORKFLOW.md`.

Use `docs/symphony-prompts/file/` for the Markdown-file Kanban tracker and
`docs/symphony-prompts/linear/` for Linear. Customize those files directly
when a board needs different agent behavior.

## Stage transitions and the orchestrator

`active_states` in `WORKFLOW.md` is `[Todo, Explore, "In Progress", Review, QA, Learn]`.
The orchestrator dispatches a worker for any ticket whose state is in
that set, regardless of which stage it is on. Each phase starts with a
fresh first-turn prompt assembled from `prompts.base` and the current
state's `prompts.stages` file. That keeps the Python core simple: states
are just strings; the pipeline policy lives in editable Markdown.

`max_concurrent_agents_by_state` lets you cap how many tickets can sit
in each stage in parallel — useful when QA runs are expensive (real
browsers, real backends) or when Explore should stay serialized so the
brief reflects a quiet workspace.

## Adopting the pipeline

1. Copy `WORKFLOW.file.example.md` (file tracker) or `WORKFLOW.example.md`
   (Linear) to `WORKFLOW.md` and customize.
2. Confirm `tracker.active_states` includes `Explore`, `Review`, `QA`,
   and `Learn` (in addition to `Todo` and `In Progress`).
3. Confirm the `prompts:` block points at the prompt directory you want
   to customize. The shipped examples use `docs/symphony-prompts/file/`
   and `docs/symphony-prompts/linear/`.
4. Make sure your `before_run` / `after_create` hooks land the agent in
   a workspace where the test suite, target API, or browser harness is
   actually runnable. The QA stage is only as good as the workspace it
   runs in.
5. Decide whether `llm-wiki/` lives in the same repo as the source (the
   default; Learn commits wiki edits onto the ticket's branch) or in a
   sibling repo. Either works; keep it adjacent to `kanban/` so Explore
   can reach it without extra configuration.
6. Run `symphony doctor ./WORKFLOW.md` before launching to catch the
   common first-run failures (port collision, missing CLI on PATH,
   placeholder clone URL).

## Per-ticket artefact root

Every artefact a pipeline ticket produces lives under a single root:
`docs/<TICKET-ID>/<stage>/`. Triage drops bug reproductions into
`reproduce/` (bug-labeled tickets only); Explore drops citations and
reuse inventory into `explore/`; Implement writes user-facing docs into
`work/`; Review writes HTTP baseline/PR/diff/curl logs into `verify/`;
QA writes durable e2e specs and traces/videos/HAR into `qa/`. Workers
create folders themselves with `mkdir -p`. Learn is the only stage that
writes outside this root — its target is `${LLM_WIKI_PATH:-./llm-wiki}/`.

## Reference ticket

A complete worked example lives at [`docs/PIPELINE-DEMO.md`](./PIPELINE-DEMO.md).
It carries every section a finished pipeline ticket should have
(`## Plan`, `## Implementation`, `## Review`, `## QA Evidence`, and the
`## As-Is -> To-Be Report` block). Copy its structure when authoring a
real ticket — the test suite asserts this exact shape stays consistent.

Evidence-first stage rules (reproduce/work/verify/qa-engineer) adapt ideas from cskwork/backend-dev-skills (MIT).
