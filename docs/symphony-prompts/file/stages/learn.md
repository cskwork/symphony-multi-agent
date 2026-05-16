### LEARN  -- when state is `Learn`

Make the next ticket cheaper. Distill what this ticket taught and write it back into `docs/llm-wiki/` in a form **both developers and non-developers can learn from**.

1. Read `docs/{{ issue.identifier }}/{explore,work,qa}/` and prior sections (`## Recommendation`, `## Implementation`, `## QA Evidence`) end-to-end.
2. Compare brief vs reality: which assumptions held or were wrong, which constraint/invariant only surfaced now, which prior wiki entry was incomplete or misleading.
3. Update `docs/llm-wiki/`: edit existing entry by appending a `YYYY-MM-DD | <issue.identifier> | note` Decision log row and refreshing **Last updated**, OR create `docs/llm-wiki/<topic-slug>.md` using the exact template below; then add/refresh its row in `INDEX.md` (`| topic-slug | one-line summary | YYYY-MM-DD (<issue.identifier>) |`).

   Every wiki entry has two layers stacked top-to-bottom: a **beginner explainer** that any reader (PM, designer, junior dev, future-you with no context) can absorb in two minutes, then a **technical reference** for the engineer who will touch the code next. Both layers live in the same file so the file owns the full picture of the topic.

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

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   `## 감 잡기` 작성 규칙 (강제):
   - 처음부터 전문용어를 쏟지 말 것. 어쩔 수 없이 써야 하면 같은 줄에 짧은 괄호 설명을 붙인다.
   - 사전식 정의 금지. "X는 ~을 의미한다" 대신 "X는 마치 ~처럼 동작한다" 또는 "X를 쓰면 ~이 된다".
   - 비유는 사용하되 너무 유치하지 않게. 비즈니스 도메인이 친숙한 비유가 있으면 그걸 우선.
   - 핵심 흐름 화살표는 3-5단계로 제한. 7개를 늘어놓으면 감이 잡히지 않는다.
   - 용어 표는 정확히 5개. 더 적으면 부족, 더 많으면 초보자 단계가 아님.
   - 아직 깊게 들어가면 헷갈리는 내용(엣지 케이스, 내부 구현, 성능 트레이드오프)은 일부러 빼고 마지막 줄에 "나중에 배울 내용"으로 미룬다.
   - 문장은 짧고 명확하게. 한 문장에 한 가지만.
   - 마지막 "이것만 기억하면 된다"는 정확히 한 문장. 두 문장이면 핵심이 두 개라는 뜻 → 다시 쪼개라.

   기존 wiki 엔트리에 `## 감 잡기` 섹션이 없다면 이번 Learn 단계에서 추가한다. 이미 있다면, 이번 티켓이 새로 알게 된 사실로 인해 비유나 핵심 흐름이 어긋났을 때만 손본다 (사소한 wording 변경은 금지 — Decision log row 추가로 충분).
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

   **Decision log:**
   - YYYY-MM-DD | <issue.identifier> | what changed and why.

   **Last updated:** YYYY-MM-DD by <issue.identifier>.
   ```

   Hard rules for the `## Getting the Feel` block:
   - Do not front-load jargon. If you must use a domain term, attach a short parenthetical on the same line.
   - No dictionary definitions. Write "X behaves like ..." or "you use X when ...", not "X is defined as ...".
   - Analogies are welcome but not childish. Prefer business-domain analogies the reader already lives in.
   - The arrow flow stays at 3-5 steps. Seven steps stop being a "feel" — they're a spec.
   - Exactly five terms in the table. Fewer = under-explained; more = no longer beginner level.
   - Defer edge cases, internal implementation, performance trade-offs — list them under "ready to go deeper" instead.
   - Short, clear sentences. One idea per sentence.
   - "Just remember this" must be exactly one sentence. Two sentences = two takeaways → split the topic.

   If the existing wiki entry has no `## Getting the Feel` section, add one this Learn turn. If it already exists, touch it only when this ticket invalidated the analogy or the core flow — small wording tweaks are out of scope (a Decision log row is enough).
{% endif %}

4. Wiki integrity sweep before transitioning:
   - Duplicates: merge same-slug rows into the entry with newer Last updated, absorb distinct Invariants/Decision log rows, `git rm` loser file and drop its INDEX row.
   - Orphans: every `docs/llm-wiki/*.md` (except `INDEX.md`) has an INDEX row; every INDEX row has a file. Reconcile both directions.
   - Stale: if Last updated > 90 days, append ` (stale?)` to the INDEX summary cell (idempotent).
   - Contradictions: if this ticket disproves an entry, update it and log the prior wrong claim; for cross-entry conflicts noticed in passing, append a `## Wiki Conflict` section to the ticket pointing at both files.
   - Beginner block sanity: every entry's `## 감 잡기` / `## Getting the Feel` section, if present, still has 3-5 flow steps, exactly five terms, one-sentence takeaway. Fix only obvious shape violations; rewriting prose is out of scope unless this ticket changed the underlying truth.
5. Append `## Learnings` to the ticket — 3-4 bullets of new facts/constraints/surprises.
6. Append `## Wiki Updates` to the ticket — paths created/modified/removed, one line each with changelog (`merged`, `created`, `marked stale`, `dropped orphan row`, `updated invariant`, `added beginner block`, `refreshed beginner block`).
7. Set state to `Done`. If nothing new and sweep was clean, say so explicitly under `## Learnings` and still transition.
