// ticket.js — 카드 컴포넌트 + 상세 modal 렌더

import { el, formatTokens, escapeHtml } from "./utils.js";
import { fetchKanbanRaw, rawKanbanUrl } from "./api.js";

// frontmatter 파서 — server.py와 동일 룰(scalar + simple list)
function parseFrontmatterClient(text) {
  const m = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n?/);
  if (!m) return { fm: {}, body: text };
  const block = m[1];
  const body = text.slice(m[0].length);
  const data = {};
  let currentListKey = null;
  for (const rawLine of block.split("\n")) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line.trim()) {
      currentListKey = null;
      continue;
    }
    const listMatch = line.match(/^\s*-\s+(.*)$/);
    if (listMatch && currentListKey !== null) {
      data[currentListKey].push(coerce(listMatch[1]));
      continue;
    }
    const kv = line.match(/^([A-Za-z0-9_\-]+)\s*:\s*(.*)$/);
    if (!kv) continue;
    const key = kv[1];
    const rest = kv[2].trim();
    if (rest === "") {
      data[key] = [];
      currentListKey = key;
    } else {
      data[key] = coerce(rest);
      currentListKey = null;
    }
  }
  return { fm: data, body };
}

function coerce(s) {
  const v = s.trim();
  if (!v) return "";
  if (
    (v.startsWith("'") && v.endsWith("'")) ||
    (v.startsWith('"') && v.endsWith('"'))
  ) {
    return v.slice(1, -1);
  }
  if (/^-?\d+$/.test(v)) return parseInt(v, 10);
  const low = v.toLowerCase();
  if (low === "true" || low === "yes") return true;
  if (low === "false" || low === "no") return false;
  if (low === "null" || low === "~") return null;
  return v;
}

// 카드 DOM 생성
export function renderCard(ticket, runningInfo) {
  const cls = ["card"];
  if (runningInfo) cls.push("running");

  const id = ticket.identifier || ticket.id;

  const priority = ticket.priority;
  const priorityNode =
    priority !== null && priority !== undefined
      ? el("span", { class: "card-priority", dataset: { p: String(priority) } }, `P${priority}`)
      : null;

  const labels = (ticket.labels || []).slice(0, 6).map((l) =>
    el("span", { class: "label-chip" }, String(l))
  );

  let runningBadge = null;
  if (runningInfo) {
    const turn = runningInfo.turn_count ?? 0;
    const tok = runningInfo?.tokens?.total_tokens;
    const tokStr = typeof tok === "number" ? formatTokens(tok) : "—";
    runningBadge = el(
      "div",
      { class: "running-badge", title: "실행 중" },
      el("span", { class: "pulse-dot" }),
      `turn ${turn} · tok ${tokStr}`
    );
  }

  const card = el(
    "div",
    {
      class: cls.join(" "),
      tabindex: "0",
      role: "button",
      "aria-label": `${id}: ${ticket.title}`,
      dataset: { id, state: ticket.state || "Todo" },
    },
    runningBadge,
    el(
      "div",
      { class: "card-head" },
      el("span", { class: "card-id" }, id),
      priorityNode
    ),
    el("div", { class: "card-title" }, ticket.title || ""),
    labels.length ? el("div", { class: "card-labels" }, ...labels) : null
  );

  return card;
}

// 상세 modal 렌더
export async function openTicketDetail(ticketId, runningInfo) {
  const backdrop = document.getElementById("modal-backdrop");
  const titleEl = document.getElementById("modal-title");
  const tableEl = document.getElementById("modal-meta-table");
  const contentEl = document.getElementById("modal-content");
  const rawLink = document.getElementById("modal-raw-link");

  titleEl.textContent = `${ticketId} · 로딩 중…`;
  tableEl.innerHTML = "";
  contentEl.innerHTML = '<p style="color:var(--fg-muted)">불러오는 중…</p>';
  rawLink.href = rawKanbanUrl(ticketId);
  backdrop.hidden = false;

  const res = await fetchKanbanRaw(ticketId);
  if (!res.ok) {
    contentEl.innerHTML = `<p style="color:var(--danger)">로드 실패 (status ${res.status}).</p>`;
    titleEl.textContent = ticketId;
    return;
  }
  const { fm, body } = parseFrontmatterClient(res.text);
  titleEl.textContent = `${fm.identifier || fm.id || ticketId} · ${fm.title || ""}`;

  // meta table
  const metaRows = [
    ["state", fm.state],
    ["priority", fm.priority !== undefined ? `P${fm.priority}` : "—"],
    ["labels", Array.isArray(fm.labels) ? fm.labels.join(", ") : (fm.labels || "")],
    ["created_at", fm.created_at || "—"],
    ["updated_at", fm.updated_at || "—"],
  ];
  if (runningInfo) {
    metaRows.push(["running", "yes"]);
    metaRows.push(["turn", String(runningInfo.turn_count ?? "—")]);
    metaRows.push([
      "tokens",
      runningInfo?.tokens?.total_tokens
        ? formatTokens(runningInfo.tokens.total_tokens)
        : "—",
    ]);
    if (runningInfo.started_at) metaRows.push(["started_at", runningInfo.started_at]);
    if (runningInfo.last_event_at) metaRows.push(["last_event_at", runningInfo.last_event_at]);
  }

  tableEl.innerHTML = metaRows
    .map(
      ([k, v]) =>
        `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v ?? "—")}</td></tr>`
    )
    .join("");

  // body markdown
  // 보안: kanban .md 본문은 외부 에이전트가 작성하므로 prompt injection으로
  //   <script>, <iframe>, on*= 같은 위험한 HTML이 들어올 수 있다.
  //   - marked는 v12부터 sanitize 옵션이 제거됨 → DOMPurify 별도 사용
  //   - DOMPurify가 로드되지 않은 fallback에서는 raw markdown을 <pre>로만 표시
  let safeHtml = "";
  try {
    const hasMarked =
      window.marked && typeof window.marked.parse === "function";
    const hasDOMPurify =
      window.DOMPurify && typeof window.DOMPurify.sanitize === "function";
    if (hasMarked && hasDOMPurify) {
      window.marked.setOptions({ breaks: false, gfm: true });
      const dirty = window.marked.parse(body || "");
      safeHtml = window.DOMPurify.sanitize(dirty, {
        // FORBID 명시 — script/iframe/object/embed/form 등은 절대 통과 X.
        // 기본 DOMPurify 정책으로도 막히지만, 이름을 코드에 박아 의도 명시.
        FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form"],
        FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "onfocus"],
      });
    } else {
      safeHtml = `<pre>${escapeHtml(body || "")}</pre>`;
    }
  } catch (e) {
    safeHtml = `<pre>${escapeHtml(body || "")}</pre>`;
  }
  contentEl.innerHTML = safeHtml;
  contentEl.scrollTop = 0;
}

export function closeTicketDetail() {
  document.getElementById("modal-backdrop").hidden = true;
}
