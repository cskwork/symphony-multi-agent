# Recommended .gitignore for Symphony-driven projects

The reference `hooks.after_create` symlinks the host repo's `docs/`,
`kanban/`, `prompt/`, and `docs/llm-wiki/` into each per-ticket workspace.
Agents write reports through those symlinks, so all per-ticket
documentation lands in the host repo's `docs/` tree as untracked
files. Without an opinionated `.gitignore`, large binary artefacts
(Playwright traces, e2e videos, vendored node_modules) ride along into
the auto-merge commits and bloat history fast.

For the workspace itself, the linked roots are plumbing. Do not commit
them as `120000` symlink blobs. The file workflow hook hides them with
`skip-worktree`, worktree-local `info/exclude`, and `git add -A`
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

## Pairs with v0.4.3+ `agent.auto_merge_capture_untracked`

When you set `agent.auto_merge_capture_untracked: ["docs"]` in your
`WORKFLOW.md`, Symphony will `git add` the untracked files under that
path into the auto-merge commit on Done. Combined with the .gitignore
above, the captured set is automatically markdown-only — binary noise
stays local.

```yaml
agent:
  auto_merge_on_done: true
  auto_merge_capture_untracked: ["docs"]   # opt-in
```
