# Recommended .gitignore for Symphony-driven projects

The reference file-board `hooks.after_create` symlinks only host-owned
board roots such as `kanban/` into each per-ticket workspace. `docs/`
stays branch-local by default: per-ticket reports, QA evidence, and
`docs/llm-wiki/` updates are reviewable deliverables that merge back with
the `symphony/<ID>` branch.

For the workspace itself, linked roots are plumbing. Do not commit them
as `120000` symlink blobs. The file workflow hook hides host-owned roots
with `skip-worktree`, worktree-local `info/exclude`, and `git add -A`
excludes; keep those guardrails when customizing the hook.

## Recommended pattern (copy verbatim)

```gitignore
# Symphony runtime logs + pid — keep local, never commit.
log/

# Symphony agent-generated docs: keep markdown reports only.
# Binary artefacts (per-ticket node_modules, e2e traces/videos,
# screenshots, zips) stay local — too heavy for git history.
docs/**
!docs/
!docs/**/
!docs/**/*.md
# Re-ignore vendored / generated subtrees so their internal *.md
# (e.g. Playwright README.md, package READMEs) don't sneak back in.
docs/**/node_modules/
docs/**/traces/
docs/**/report/data/
docs/**/coverage/
```

## What gets kept vs dropped

| Path pattern                          | Kept?  | Why                                              |
|---------------------------------------|--------|--------------------------------------------------|
| `docs/<ID>/explore/notes.md`          | ✅     | small text report                                |
| `docs/<ID>/work/feature.md`           | ✅     | small text report                                |
| `docs/<ID>/qa/details.md`             | ✅     | small text report                                |
| `docs/llm-wiki/*.md`                  | ✅     | wiki entries — long-lived reference material     |
| `docs/<ID>/qa/node_modules/`          | ❌     | per-ticket Playwright deps, regenerable          |
| `docs/<ID>/qa/traces/*.zip`           | ❌     | binary, large; use LFS if you must share         |
| `docs/<ID>/qa/report/data/`           | ❌     | binary HTML report blobs                         |
| `docs/<ID>/evidence/*.webm,*.png`     | ❌     | by default; pin specific ones with `!path` rules |
| `log/*` (runtime logs + pid)          | ❌     | noise; the orchestrator regenerates these        |

## Legacy host-linked docs

Prefer branch-local docs. If you intentionally link `docs/` from the host,
you must add a separate capture policy because those files will not appear
in the `symphony/<ID>` branch diff. Combined with the .gitignore above,
the captured set can stay markdown-only.

```yaml
agent:
  auto_merge_on_done: true
  auto_merge_capture_untracked: ["docs"]   # legacy opt-in
```
