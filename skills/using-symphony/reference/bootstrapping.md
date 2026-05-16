# Bootstrapping Symphony into another project

Use this when introducing Symphony to a repo that does not already carry the
standard operator bundle.

## Copy the full operator bundle

From inside the `symphony-multi-agent` checkout:

```bash
TARGET=/path/to/target-project
cp tui-open.sh tui-open.bat "$TARGET/"
cp WORKFLOW.example.md "$TARGET/WORKFLOW.md"              # then edit
mkdir -p "$TARGET/docs"
cp -R docs/symphony-prompts "$TARGET/docs/"
cp -R skills "$TARGET/"
cp AGENTS.md GEMINI.md "$TARGET/"
mkdir -p "$TARGET/.claude/skills"
ln -s ../../skills/using-symphony "$TARGET/.claude/skills/using-symphony"
ln -s ../../skills/symphony-oneshot "$TARGET/.claude/skills/symphony-oneshot"
chmod +x "$TARGET/tui-open.sh"
```

Copy `tui-open.sh` and `tui-open.bat` even for headless-first setups. The
launcher carries safety behavior that plain `symphony tui` does not: port
collision checks, doctor preflight, venv-first binary lookup, and real terminal
window spawning.

If the target project has no virtualenv, either install Symphony globally or
prepare a local one so the launcher can find it:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e <symphony-multi-agent>
```

## Why these files matter

| File or directory | Purpose |
| --- | --- |
| `WORKFLOW.md` | Runtime config and prompt entrypoint |
| `docs/symphony-prompts/` | Worker prompts; dispatched agents read these |
| `skills/` | Canonical operator skills |
| `.claude/skills/<name>` | Claude Code discovery symlinks to `skills/<name>` |
| `AGENTS.md` | Codex entrypoint pointing to repo skills |
| `GEMINI.md` | Gemini entrypoint pointing to repo skills |
| `tui-open.sh`, `tui-open.bat` | One-shot board launchers |

`skills/<name>/` is the source of truth. Edit only the canonical files under
`skills/`; platform entrypoints should point at them.

## Preserve the default pipeline

`WORKFLOW.example.md` ships with the supported production flow:

```text
Todo -> Explore -> Plan -> In Progress -> Review -> QA -> Learn -> Done
```

Do not trim it to a smaller lane set unless the user explicitly asks. The base
prompt names these stages, QA evidence is part of the Done gate, and Learn
writes back to `docs/llm-wiki/` for future tickets.

If the target project truly needs a different workflow, edit these together:

- `tracker.active_states`
- `tracker.terminal_states`
- `prompts.stages`
- the matching stage files under `docs/symphony-prompts/<flavor>/stages/`

Use `reference/customization.md` for lane and prompt changes.

## Pick the prompt flavor

- `tracker.kind: file` uses `docs/symphony-prompts/file/...`; the agent writes
  stage notes into the ticket file body.
- `tracker.kind: linear` uses `docs/symphony-prompts/linear/...`; the agent
  writes stage notes as Linear comments.

Copy only the flavor you need if you want a smaller target repo. Copying both
is fine when simplicity matters more than disk hygiene.

## First launch

Foreground board view:

```bash
./tui-open.sh
./tui-open.sh path/to/WORKFLOW.md
tui-open.bat
```

For managed headless operation and viewer commands, use
`reference/operations.md`.
