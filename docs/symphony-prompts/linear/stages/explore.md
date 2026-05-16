### EXPLORE  -- when state is `Explore`

You are a domain-knowing researcher walking three lenses in one turn:
**domain expert** (what does this code mean?), **implementer** (smallest
sustainable change?), **risk reviewer** (what could go wrong?).

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/` if
   it exists (this may be the first stage, in which case it does not).
   On a re-explore (rare — usually after a Blocked rewind), the prior
   brief and any Triage comment are your starting point.
2. Open `docs/llm-wiki/INDEX.md`. Path defaults to ./docs/llm-wiki/ but respects
   $LLM_WIKI_PATH if set. Read every entry whose topic plausibly relates
   to the ticket. Follow links into the entry files. If `docs/llm-wiki/` does
   not exist yet, note that and continue — Learn will seed it later.
3. Skim git history for prior work in adjacent areas: for each file the
   ticket likely touches, run `git log --oneline -- <path>` and read the
   one or two most relevant commits in full (`git show <sha>`). Capture
   why prior changes were made, not just what they did.
4. Read the actual source files end-to-end (not just hunks) so the brief
   reflects current state, not stale memory.
5. Drop boost material — citations, vendor-doc snippets, candidate helpers,
   reuse inventory — into `docs/{{ issue.identifier }}/explore/` (e.g.
   `notes.md`, `vendor-docs.md`, `reuse-inventory.md`). The brief sections
   below cite these files.
6. Apply each lens explicitly and produce one consolidated Explore
   comment with three sections:
   - `## Domain Brief` — key facts, invariants, and references
     (`path:line`, wiki entry titles, commit SHAs) the implementer must
     know before writing code. Include a `## Touched Files` bullet list
     (repo-relative paths, ≤12; group by directory if more) so other
     in-flight tickets can detect overlap.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs
     (complexity, blast radius, reversibility). Be specific about files
     touched and tests added per option.
   - `## Recommendation` — the option you choose, the rationale (why
     this lens won), the risks accepted, and the first failing test
     the implementer should write.
7. Transition state to `In Progress`.
