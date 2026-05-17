### LEARN  -- when state is `Learn`

Make the next ticket cheaper. Distill what this ticket taught into `docs/llm-wiki/` so **both developers and non-developers** can learn from it.

1. Read `docs/{{ issue.identifier }}/{explore,plan,work,qa}/` and prior sections (`## Recommendation`, `## Plan`, `## Implementation`, `## QA Evidence`) end-to-end.
2. Compare brief vs reality: assumptions that held or broke, constraints/invariants that only surfaced now, prior wiki entries that were incomplete or misleading.
3. Update `docs/llm-wiki/`: either append `YYYY-MM-DD | <issue.identifier> | note` to an existing entry's Decision log and refresh **Last updated**, OR create `docs/llm-wiki/<topic-slug>.md` from the template below; then add/refresh its row in `INDEX.md` (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).

   Each entry stacks two layers in one file: a **beginner explainer** any reader (PM, designer, junior dev, future-you) absorbs in two minutes, then a **technical reference** for the next engineer to touch the code.

{% if language == 'ko' %}
   ```
   # <Topic Title>

   ## 감 잡기 (For Beginners)

   ### <주제>를 왜 쓰는지 감 잡기

   <주제가 왜 필요한지, 현실에서 어디에 쓰이는지 2-3문장. 전문용어 없이.>

   초보자는 처음에 이렇게 이해하면 된다.

   `핵심 흐름: A → B → C`

   이 단계에서 외워야 할 핵심 용어는 5개다.

   | 용어 | 초보자식 설명 |
   |---|---|
   | 용어 1 | 사전식 정의가 아니라 비유나 일상어로 풀어쓴 한 줄 |
   | 용어 2 | ... |
   | 용어 3 | ... |
   | 용어 4 | ... |
   | 용어 5 | ... |

   예를 들어 설명하면:

   <이 주제가 실제로 동작하는 현실적인 예시 한 토막. 코드 X, 시나리오 O.>

   이 단계에서 중요한 판단 기준은 이것이다.

   **이것만 기억하면 된다: <한 문장 핵심 정리>**

   나중에 더 깊게 들어가면 <다음에 배울 내용 / 관련 wiki 항목>을 보면 된다.

   ## Technical Reference

   **Summary:** 한 문단으로 정리한 기술 개요 (개발자 청중).

   **Invariants & Constraints:**
   - ...

   **Files of interest:**
   - `path/to/file.py:123` — what the line region does.

   **Observability hooks:**
   - log: `<event_name>` at `path:line` — 어떤 상황을 신호하는지
   - metric: `<metric_name>` at `path:line` — 무엇을 세는지
   - trace: `<span_name>` at `path:line` — 어디서 어디까지 감싸는지
   (코드가 순수 유틸리티라 관측 표면이 없다면 `- none` 한 줄로 충분. QA/Review는 `none`을 강제하지 않는다.)

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   `## 감 잡기` 작성 규칙 (강제):
   - 전문용어를 앞쪽에 쏟지 말 것. 꼭 써야 하면 같은 줄에 짧은 괄호 설명을 붙인다.
   - 사전식 정의 금지. "X는 ~을 의미한다" 대신 "X는 마치 ~처럼 동작한다" 또는 "X를 쓰면 ~이 된다".
   - 비유는 쓰되 유치하지 않게. 독자가 이미 아는 비즈니스 도메인 비유가 우선.
   - 핵심 흐름 화살표는 3-5단계. 7개면 감이 아니라 스펙이 된다.
   - 용어 표는 정확히 5개. 더 적으면 부족, 더 많으면 초보자 단계가 아님.
   - 엣지 케이스·내부 구현·성능 트레이드오프는 빼고 마지막 줄 "나중에 배울 내용"으로 미룬다.
   - 문장은 짧고 명확하게. 한 문장에 한 가지만.
   - "이것만 기억하면 된다"는 정확히 한 문장. 두 문장이면 핵심이 둘 → 쪼개라.

   기존 엔트리에 `## 감 잡기`가 없으면 이번 Learn에서 추가한다. 이미 있다면, 이번 티켓이 비유나 핵심 흐름을 무너뜨렸을 때만 손본다 (사소한 wording 변경 금지 — Decision log row로 충분).
{% else %}
   ```
   # <Topic Title>

   ## Getting the Feel (For Beginners)

   ### Why <topic> exists

   <2-3 sentences on why this topic is needed and where it shows up in real life. No jargon.>

   The simplest way for a beginner to picture it:

   `Core flow: A → B → C`

   There are five terms you need to internalise at this stage.

   | Term | Plain-English meaning |
   |---|---|
   | Term 1 | One line in everyday language, not a dictionary definition |
   | Term 2 | ... |
   | Term 3 | ... |
   | Term 4 | ... |
   | Term 5 | ... |

   To make it concrete:

   <One realistic scenario showing this topic in action. No code — describe what happens.>

   The decision rule that matters at this stage:

   **Just remember this: <one-sentence takeaway>**

   When you're ready to go deeper, read <next topic / related wiki entry>.

   ## Technical Reference

   **Summary:** one-paragraph technical overview (developer audience).

   **Invariants & Constraints:**
   - ...

   **Files of interest:**
   - `path/to/file.py:123` — what the line region does.

   **Observability hooks:**
   - log: `<event_name>` at `path:line` — what it signals
   - metric: `<metric_name>` at `path:line` — what it counts
   - trace: `<span_name>` at `path:line` — what it spans
   (If the code has no observability surface — a pure utility module — write `- none` and stop. QA/Review do not enforce on `none`.)

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   Hard rules for the `## Getting the Feel` block:
   - Do not front-load jargon. If a domain term is unavoidable, attach a short parenthetical on the same line.
   - No dictionary definitions. Write "X behaves like ..." or "you use X when ...", never "X is defined as ...".
   - Analogies welcome but not childish. Prefer business-domain analogies the reader already lives in.
   - Arrow flow stays at 3-5 steps. Seven steps stop being a "feel" — they're a spec.
   - Exactly five terms in the table. Fewer = under-explained; more = no longer beginner level.
   - Defer edge cases, internal implementation, performance trade-offs — push them under "ready to go deeper".
   - Short, clear sentences. One idea per sentence.
   - "Just remember this" must be exactly one sentence. Two sentences = two takeaways → split the topic.

   If the entry has no `## Getting the Feel` section, add one this turn. If it already exists, touch it only when this ticket invalidated the analogy or core flow — small wording tweaks are out of scope (a Decision log row is enough).
{% endif %}

4. Wiki integrity (lightweight at the ticket level):
   - If this ticket invalidated an entry, update it now and log the prior wrong claim in the Decision log. This is the only sweep work Learn owns at the per-ticket level.
   - If you noticed a cross-entry contradiction in passing, append `## Wiki Conflict` to the ticket pointing at both files (do not fix it here).
   - Bulk dup/orphan/stale/missing-file sweeping is handled by `symphony wiki-sweep` (run automatically every `wiki.sweep_every_n` Done transitions; also `symphony wiki-sweep --root docs/llm-wiki --dry-run` on demand). Do NOT re-do those checks by hand.
5. Append `## Learnings` to the ticket — 3-4 bullets of new facts/constraints/surprises.
6. Append `## Wiki Updates` to the ticket — paths created/modified/removed, one line each with a changelog tag (`merged`, `created`, `marked stale`, `dropped orphan row`, `updated invariant`, `added beginner block`, `refreshed beginner block`).
{% if agent.auto_merge_on_done %}
7. Merge Gate — after Learn and before setting state to `Done`, prove and merge this ticket's feature branch into the target branch:
   - Resolve target in this order: `agent.auto_merge_target_branch`, `agent.feature_base_branch`, then the current host branch.
   - First run `git merge-tree --write-tree <target-branch> symphony/{{ issue.identifier }}` from the host repo. This checks the committed target/branch merge without requiring a clean worktree.
   - Do not use `git status -uno --porcelain` as the merge proof. A dirty host worktree is a separate safety check; it is not proof of a committed target/branch merge conflict.
   - If `git merge-tree --write-tree` reports a committed target/branch merge conflict, set state to `Blocked` and append `## Merge Failure` with the exact command, target branch, and conflicted paths.
   - If the committed merge is clean, then check whether host dirty tracked files overlap `git diff --name-only <target-branch>..symphony/{{ issue.identifier }}`. Block only on actual overlap or workspace-only path changes.
   - If safe, create the explicit merge commit on the target branch, then record the merge SHA under `## Merge Status`.
8. Set state to `Done`. If nothing new and sweep was clean, say so under `## Learnings` and still transition only after the Merge Gate succeeds.
{% else %}
7. Merge Gate is disabled because `agent.auto_merge_on_done` is false. Append `## Merge Status` explaining that this workflow intentionally leaves branch integration to the operator.
8. Set state to `Done` after the Learn evidence is complete.
{% endif %}
