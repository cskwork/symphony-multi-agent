# Contributing

Thanks for helping improve Symphony. This repo is public, and external pull
requests are welcome.

## Branch And PR Target

- Open pull requests against `dev` unless a maintainer asks for a different
  target.
- Keep `main` release-ready; it receives changes after they have been verified
  on `dev`.
- Prefer small, focused PRs. Separate backend behavior, UI, docs, and release
  metadata when they are not part of the same user-visible change.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Required Verification

Run the full test suite before opening or updating a PR:

```bash
pytest -q
```

For workflow, service, or board changes, also run:

```bash
symphony doctor ./WORKFLOW.md
```

For backend adapter changes, include the most relevant targeted test command
and any manual smoke evidence. Real Codex, Claude Code, Gemini, and Pi CLI
integration checks are useful when available, but CI only requires the local
test suite because contributors may not have every agent CLI installed.

## Development Standards

- Keep agent-specific protocol details inside `src/symphony/backends/`.
  The orchestrator should see normalized backend events, not raw CLI payloads.
- Preserve the existing `WORKFLOW.md` configuration shape unless the PR also
  updates examples, docs, and validation.
- Add or update tests for behavior changes. For bug fixes, include a regression
  test that would fail without the fix when practical.
- Do not commit secrets, local run state, logs, virtualenvs, or generated
  browser artifacts.
- Update README or `docs/` when the user-facing behavior or operator workflow
  changes.

## PR Checklist

Before requesting review, make sure the PR includes:

- A clear summary of the behavior change.
- Verification commands and results.
- Screenshots or terminal output for TUI or browser-visible changes.
- Notes about compatibility, migration, or follow-up work if applicable.

Maintainers merge after the PR targets `dev`, CI is green, and the verification
evidence is sufficient for the risk of the change.
