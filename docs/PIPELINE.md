# Production pipeline

Symphony's default task (the prompt template in `WORKFLOW.md`) drives every
ticket through seven gated stages. The harness picks the ticket up on each
poll tick; the agent moves it from one stage to the next by editing the
ticket file's `state` field. The orchestrator only reads, so this stage
machine is intentionally implemented in the prompt rather than the Python
core.

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

- **`llm-wiki/`** — domain knowledge written by prior tickets (read first).
- **git history** — `git log --oneline -- <path>` for files the ticket
  likely touches, then `git show <sha>` on the relevant commits to
  recover the *why* behind prior changes.
- **the source files themselves**, end-to-end, so the brief reflects
  current state and not stale memory.

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

- If a relevant entry exists, it is edited in place. A new line is
  appended to its **Decision log** (`YYYY-MM-DD | <issue.identifier> |
  note`) and **Last updated** is refreshed.
- Otherwise a new `llm-wiki/<topic-slug>.md` is created with a fixed
  shape (Summary / Invariants & Constraints / Files of interest /
  Decision log / Last updated) and a row is added to `llm-wiki/INDEX.md`.

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

## Stage transitions and the orchestrator

`active_states` in `WORKFLOW.md` is `[Todo, Explore, "In Progress", Review, QA, Learn]`.
The orchestrator dispatches a worker for any ticket whose state is in
that set, regardless of which stage it is on. Each turn the agent reads
`{{ issue.state }}` from the prompt and applies the matching stage rule.
That keeps the Python core simple: states are just strings; the pipeline
is policy expressed in the prompt.

`max_concurrent_agents_by_state` lets you cap how many tickets can sit
in each stage in parallel — useful when QA runs are expensive (real
browsers, real backends) or when Explore should stay serialized so the
brief reflects a quiet workspace.

## Adopting the pipeline

1. Copy `WORKFLOW.file.example.md` (file tracker) or `WORKFLOW.example.md`
   (Linear) to `WORKFLOW.md` and customize.
2. Confirm `tracker.active_states` includes `Explore`, `Review`, `QA`,
   and `Learn` (in addition to `Todo` and `In Progress`).
3. Make sure your `before_run` / `after_create` hooks land the agent in
   a workspace where the test suite, target API, or browser harness is
   actually runnable. The QA stage is only as good as the workspace it
   runs in.
4. Decide whether `llm-wiki/` lives in the same repo as the source (the
   default; Learn commits wiki edits onto the ticket's branch) or in a
   sibling repo. Either works; keep it adjacent to `kanban/` so Explore
   can reach it without extra configuration.
5. Run `symphony doctor ./WORKFLOW.md` before launching to catch the
   common first-run failures (port collision, missing CLI on PATH,
   placeholder clone URL).

## Reference ticket

A complete worked example lives at [`docs/PIPELINE-DEMO.md`](./PIPELINE-DEMO.md).
It carries every section a finished pipeline ticket should have
(`## Plan`, `## Implementation`, `## Review`, `## QA Evidence`, and the
`## As-Is -> To-Be Report` block). Copy its structure when authoring a
real ticket — the test suite asserts this exact shape stays consistent.
