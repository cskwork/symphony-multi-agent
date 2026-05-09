# Production pipeline

Symphony's default task (the prompt template in `WORKFLOW.md`) drives every
ticket through six gated stages. The harness picks the ticket up on each poll
tick; the agent moves it from one stage to the next by editing the ticket
file's `state` field. The orchestrator only reads, so this stage machine is
intentionally implemented in the prompt rather than the Python core.

```
  Todo / In Progress  ->  Review  ->  QA  ->  Done
                              \                 ^
                               +-> Blocked      |
                                                |
              (QA failure rewinds to In Progress)
```

| State          | Owner of this turn         | Output it must produce                              |
|----------------|----------------------------|-----------------------------------------------------|
| Todo           | implementer                | `## Plan` + first failing test                      |
| In Progress    | implementer                | `## Implementation`, transition to Review           |
| Review         | reviewer                   | `## Review`, fix CRITICAL/HIGH, transition to QA    |
| QA             | qa runner (executes code)  | `## QA Evidence` (real exit codes), -> Done         |
| Done           | reporter                   | `## As-Is -> To-Be Report` (structured)             |
| Blocked        | -                          | `## Blocker` describing what is needed              |

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

`active_states` in `WORKFLOW.md` is `[Todo, "In Progress", Review, QA]`.
The orchestrator dispatches a worker for any ticket whose state is in
that set, regardless of which stage it is on. Each turn the agent reads
`{{ issue.state }}` from the prompt and applies the matching stage rule.
That keeps the Python core simple: states are just strings; the pipeline
is policy expressed in the prompt.

`max_concurrent_agents_by_state` lets you cap how many tickets can sit
in each stage in parallel — useful when QA runs are expensive (real
browsers, real backends).

## Adopting the pipeline

1. Copy `WORKFLOW.file.example.md` (file tracker) or `WORKFLOW.example.md`
   (Linear) to `WORKFLOW.md` and customize.
2. Confirm `tracker.active_states` includes `Review` and `QA`.
3. Make sure your `before_run` / `after_create` hooks land the agent in
   a workspace where the test suite, target API, or browser harness is
   actually runnable. The QA stage is only as good as the workspace it
   runs in.
4. Run `symphony doctor ./WORKFLOW.md` before launching to catch the
   common first-run failures (port collision, missing CLI on PATH,
   placeholder clone URL).

## Reference ticket

A complete worked example lives at [`docs/PIPELINE-DEMO.md`](./PIPELINE-DEMO.md).
It carries every section a finished pipeline ticket should have
(`## Plan`, `## Implementation`, `## Review`, `## QA Evidence`, and the
`## As-Is -> To-Be Report` block). Copy its structure when authoring a
real ticket — the test suite asserts this exact shape stays consistent.
