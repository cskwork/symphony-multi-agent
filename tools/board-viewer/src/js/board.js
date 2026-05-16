// board.js — 칸반 보드 렌더링 + polling 루프 + 키보드 단축키

import { el, formatTime } from "./utils.js";
import {
  fetchSymphonyState,
  fetchKanbanIndex,
  fetchGitBranches,
  archiveTicket,
  pauseTicket,
  resumeTicket,
  refreshSymphony,
  saveBranchPolicy,
} from "./api.js";
import { renderCard, openTicketDetail, closeTicketDetail } from "./ticket.js";

const POLL_INTERVAL_MS = 5000;
const FALLBACK_STATES = [
  "Todo", "Explore", "Plan", "In Progress", "Review", "QA", "Learn",
  "Done", "Cancelled", "Blocked", "Archive",
];

// ---- UI Zoom ----------------------------------------------------------
const ZOOM_STORAGE_KEY = "boardViewer.uiZoom";
const ZOOM_MIN = 0.7;
const ZOOM_MAX = 1.8;
const ZOOM_STEP = 0.05;
const ZOOM_DEFAULT = 0.9;

function readZoom() {
  try {
    const raw = localStorage.getItem(ZOOM_STORAGE_KEY);
    const n = raw == null ? NaN : parseFloat(raw);
    if (Number.isFinite(n)) return clampZoom(n);
  } catch {
    /* localStorage 차단 환경(privacy mode 등) — 기본값 사용 */
  }
  return ZOOM_DEFAULT;
}

function clampZoom(n) {
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(n * 100) / 100));
}

function applyZoom(z) {
  document.documentElement.style.setProperty("--ui-zoom", String(z));
  const valEl = document.getElementById("zoom-value");
  if (valEl) valEl.textContent = Math.round(z * 100) + "%";
  const outBtn = document.getElementById("zoom-out");
  const inBtn = document.getElementById("zoom-in");
  if (outBtn) outBtn.disabled = z <= ZOOM_MIN + 1e-6;
  if (inBtn) inBtn.disabled = z >= ZOOM_MAX - 1e-6;
}

function setZoom(z) {
  const clamped = clampZoom(z);
  applyZoom(clamped);
  try {
    localStorage.setItem(ZOOM_STORAGE_KEY, String(clamped));
  } catch {
    /* persistence 실패는 무시 — UI는 정상 동작 */
  }
}

function nudgeZoom(delta) {
  const cur = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue("--ui-zoom"),
  );
  setZoom((Number.isFinite(cur) ? cur : ZOOM_DEFAULT) + delta);
}

function bindZoomControls() {
  const outBtn = document.getElementById("zoom-out");
  const inBtn = document.getElementById("zoom-in");
  const resetBtn = document.getElementById("zoom-reset");
  outBtn?.addEventListener("click", () => nudgeZoom(-ZOOM_STEP));
  inBtn?.addEventListener("click", () => nudgeZoom(+ZOOM_STEP));
  resetBtn?.addEventListener("click", () => setZoom(ZOOM_DEFAULT));
}

const state = {
  tickets: [],
  states: [],
  active_states: [],
  terminal_states: [],
  runningById: new Map(), // ticket_id -> running info
  defaultAgentKind: "",
  branchPolicy: null,
  symphonyAlive: false,
  lastPollAt: null,
  pollTimer: null,
  pollInFlight: false, // race 방지: 두 사이클이 state.tickets를 동시에 덮어쓰지 못하게
  branchRefreshInFlight: false,
  branchSaveInFlight: false,
  pollStopped: false,  // start/stop 토글용
  focusedId: null,
};

const boardEl = document.getElementById("board");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const lastPollEl = document.getElementById("last-poll");
const ticketCountEl = document.getElementById("ticket-count");
const refreshBtn = document.getElementById("refresh-btn");
const searchInput = document.getElementById("search-input");
const modalBackdrop = document.getElementById("modal-backdrop");
const modalCloseBtn = document.getElementById("modal-close");
const branchPolicyEl = document.getElementById("branch-policy");
const branchControlsEl = document.getElementById("branch-controls");
const featureBaseSelect = document.getElementById("feature-base-branch");
const mergeTargetSelect = document.getElementById("merge-target-branch");

// ---- 액션 핸들러: pause / resume / archive / orchestrator refresh ----
// 모두 optimistic update 없이, 호출 직후 즉시 poll로 정확한 상태 반영.
// 진행 중인 버튼은 disabled로 잠가서 더블 클릭 방지.
async function withButtonLock(btn, fn) {
  if (btn) btn.disabled = true;
  try {
    return await fn();
  } finally {
    if (btn) btn.disabled = false;
  }
}

const cardHandlers = {
  onPause: (id, btn) => {
    withButtonLock(btn, async () => {
      const res = await pauseTicket(id);
      if (!res.ok) {
        flashError(`Pause 실패 (${res.status || "network"})`);
      }
      // 결과는 다음 poll에서 카드에 반영
      poll();
    });
  },
  onResume: (id, btn) => {
    withButtonLock(btn, async () => {
      const res = await resumeTicket(id);
      if (!res.ok) {
        flashError(`Resume 실패 (${res.status || "network"})`);
      }
      poll();
    });
  },
  onArchive: (id, btn) => {
    withButtonLock(btn, async () => {
      const res = await archiveTicket(id);
      if (!res.ok) {
        flashError(`Archive 실패 (${res.status || "network"})`);
      }
      poll();
    });
  },
};

// 작은 toast 한 줄. CSS .flash-toast 가 있을 때만 시각적, 없어도 console에는 남음.
function flashError(msg) {
  console.warn("[board-viewer]", msg);
  const host = document.body;
  if (!host) return;
  const node = el("div", { class: "flash-toast", role: "status" }, msg);
  host.appendChild(node);
  setTimeout(() => node.remove(), 3000);
}

// 컬럼 frame 1회 빌드 (poll마다 reuse)
function buildColumns() {
  boardEl.innerHTML = "";
  for (const s of state.states) {
    const col = el(
      "section",
      { class: "column", dataset: { state: s } },
      el(
        "header",
        { class: "column-header" },
        el("span", { class: "column-name" }, s),
        el("span", { class: "column-count", "data-count": s }, "0")
      ),
      el("div", { class: "column-body", "data-body": s })
    );
    boardEl.appendChild(col);
  }
}

function renderBoard() {
  // body 비우기
  for (const s of state.states) {
    const body = boardEl.querySelector(`[data-body="${cssEscape(s)}"]`);
    if (body) body.innerHTML = "";
  }

  // ticket id → ticket map (Symphony running 정보의 fallback id 매칭)
  const byState = new Map(state.states.map((s) => [s, []]));
  for (const t of state.tickets) {
    const s = t.state || "Todo";
    if (!byState.has(s)) byState.set(s, []);
    byState.get(s).push(t);
  }

  // 렌더
  const filter = (searchInput.value || "").trim().toLowerCase();
  for (const [s, tickets] of byState.entries()) {
    const body = boardEl.querySelector(`[data-body="${cssEscape(s)}"]`);
    const countEl = boardEl.querySelector(`[data-count="${cssEscape(s)}"]`);
    if (!body) continue;
    let visibleN = 0;
    if (tickets.length === 0) {
      body.appendChild(el("div", { class: "column-empty" }, "—"));
    } else {
      for (const t of tickets) {
        const running = state.runningById.get(t.id) || null;
        const card = renderCard(t, running, cardHandlers, {
          defaultAgentKind: state.defaultAgentKind,
        });
        if (filter && !cardMatches(t, filter)) {
          card.classList.add("hidden");
        } else {
          visibleN += 1;
        }
        if (state.focusedId && t.id === state.focusedId) {
          card.classList.add("focused");
        }
        card.addEventListener("click", () => {
          state.focusedId = t.id;
          openTicketDetail(t.id, state.runningById.get(t.id) || null);
        });
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openTicketDetail(t.id, state.runningById.get(t.id) || null);
          }
        });
        body.appendChild(card);
      }
    }
    if (countEl) countEl.textContent = String(tickets.length);
  }

  ticketCountEl.textContent = `tickets: ${state.tickets.length}`;
}

function cardMatches(ticket, filter) {
  const id = (ticket.identifier || ticket.id || "").toLowerCase();
  const title = (ticket.title || "").toLowerCase();
  const labels = (ticket.labels || []).join(" ").toLowerCase();
  return id.includes(filter) || title.includes(filter) || labels.includes(filter);
}

function cssEscape(s) {
  // attribute selector 용
  return String(s).replace(/(["\\])/g, "\\$1");
}

function updateStatus() {
  if (state.symphonyAlive) {
    statusDot.dataset.state = "alive";
    statusText.textContent = "symphony: alive";
  } else {
    statusDot.dataset.state = "down";
    statusText.textContent = "symphony: down (file-only)";
  }
  lastPollEl.textContent = `last poll: ${formatTime(state.lastPollAt)}`;
}

function updateBranchPolicy(policy) {
  if (!branchPolicyEl) return;
  if (!policy) {
    branchPolicyEl.hidden = true;
    branchPolicyEl.textContent = "";
    return;
  }
  const base = policy.base_branch || "current branch";
  const target = policy.merge_target_branch || base;
  const timing = policy.merge_timing || "after Learn, before Done";
  const mode = policy.auto_merge_enabled === false ? "merge off" : timing;
  branchPolicyEl.textContent = `branch: ${base} -> ${target} (${mode})`;
  branchPolicyEl.dataset.enabled = policy.auto_merge_enabled === false ? "false" : "true";
  branchPolicyEl.hidden = false;
}

function updateBranchPolicyFromGit(gitInfo) {
  if (!state.branchPolicy || !gitInfo?.ok) return;
  const current = gitInfo.current_branch || "current branch";
  const repo = repoNameFromGitInfo(gitInfo);
  const baseLabel = state.branchPolicy.base_branch === "current branch"
    ? `${repo ? repo + "/" : ""}${current}`
    : state.branchPolicy.base_branch;
  const targetLabel = state.branchPolicy.merge_target_branch === "current branch"
    ? current
    : state.branchPolicy.merge_target_branch;
  updateBranchPolicy({
    ...state.branchPolicy,
    base_branch: baseLabel,
    merge_target_branch: targetLabel || baseLabel,
  });
  if (branchPolicyEl && gitInfo.repo_root) {
    branchPolicyEl.title = gitInfo.repo_root;
  }
}

function repoNameFromGitInfo(gitInfo) {
  return gitInfo?.repo_root ? gitInfo.repo_root.split("/").filter(Boolean).pop() : "";
}

async function refreshBranchControls() {
  if (!branchControlsEl || !featureBaseSelect || !mergeTargetSelect) return;
  if (state.branchRefreshInFlight || state.branchSaveInFlight) return;
  state.branchRefreshInFlight = true;
  try {
    const res = await fetchGitBranches();
    if (!res.ok || !res.data?.ok) {
      branchControlsEl.hidden = true;
      return;
    }
    const repoSlash = repoNameFromGitInfo(res.data) ? repoNameFromGitInfo(res.data) + "/" : "";
    // emptyOpt(value="") = 정책에 base 명시 안 함 → runtime current 사용.
    // list에도 같은 브랜치가 있으니 (default) 표기로 시각적 구분.
    const currentFull = res.data.current_branch
      ? `${repoSlash}${res.data.current_branch} (default)`
      : "current branch (default)";
    renderBranchSelect(featureBaseSelect, res.data.branches || [], {
      current: res.data.current_branch || "",
      repo: repoNameFromGitInfo(res.data),
      selected: res.data.feature_base_branch || "",
      emptyLabel: currentFull,
    });
    renderBranchSelect(mergeTargetSelect, res.data.branches || [], {
      current: res.data.current_branch || "",
      repo: repoNameFromGitInfo(res.data),
      selected: res.data.merge_target_branch || "",
      emptyLabel: currentFull,
    });
    updateBranchPolicyFromGit(res.data);
    branchControlsEl.hidden = false;
  } finally {
    state.branchRefreshInFlight = false;
  }
}

function renderBranchSelect(select, branches, { current, repo, selected, emptyLabel }) {
  select.innerHTML = "";
  const emptyOpt = el("option", { value: "", title: emptyLabel }, emptyLabel);
  select.appendChild(emptyOpt);
  for (const branch of branches) {
    if (branch.startsWith("symphony/")) continue;
    // list 안에서는 repo prefix만 붙이고 (current) suffix는 emptyOpt의 (default)와 분리.
    const label = branch === current && repo ? `${repo}/${branch}` : branch;
    select.appendChild(el("option", { value: branch, title: label }, label));
  }
  select.value = selected || "";
  if (selected && select.value !== selected) {
    select.appendChild(el("option", { value: selected, title: selected }, selected));
    select.value = selected;
  }
  // 닫힌 select에 hover하면 현재 선택된 옵션의 풀텍스트를 tooltip으로 보여준다.
  const selectedOpt = select.options[select.selectedIndex];
  select.title = selectedOpt ? selectedOpt.textContent : "";
  select.onchange = (e) => {
    const opt = select.options[select.selectedIndex];
    select.title = opt ? opt.textContent : "";
  };
}

async function saveSelectedBranchPolicy(changedSelect) {
  if (!featureBaseSelect || !mergeTargetSelect) return;
  state.branchSaveInFlight = true;
  if (changedSelect) changedSelect.disabled = true;
  try {
    const res = await saveBranchPolicy({
      feature_base_branch: featureBaseSelect.value,
      merge_target_branch: mergeTargetSelect.value,
    });
    if (!res.ok) {
      const detail = res.error?.branch ? `: ${res.error.branch}` : "";
      flashError(`Branch 저장 실패 (${res.status || "network"})${detail}`);
      state.branchSaveInFlight = false;
      await refreshBranchControls();
      return;
    }
    state.branchSaveInFlight = false;
    await refreshSymphony();
    await Promise.all([refreshBranchControls(), poll()]);
  } finally {
    if (changedSelect) changedSelect.disabled = false;
    state.branchSaveInFlight = false;
  }
}

// 한 사이클: kanban index + symphony state 병렬 호출.
// race 방지: in-flight 가드 — 이전 poll이 5초 안에 끝나지 못하면
// 다음 사이클은 그냥 skip한다 (state.tickets 동시 덮어쓰기 방지).
async function poll() {
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    await pollOnce();
  } finally {
    state.pollInFlight = false;
  }
}

async function pollOnce() {
  const [idxRes, symRes] = await Promise.all([
    fetchKanbanIndex(),
    fetchSymphonyState(),
  ]);

  if (idxRes.ok && idxRes.data) {
    state.tickets = idxRes.data.tickets || [];
    // 헤더의 project name — server.py가 kanban_dir의 parent 이름을 노출.
    // 빈 값이거나 누락되면 헤더에 아무것도 안 그림.
    const repoNameEl = document.getElementById("repo-name");
    if (repoNameEl) {
      repoNameEl.textContent = idxRes.data.project_name || "";
    }
    if (Array.isArray(idxRes.data.states) && state.states.length === 0) {
      state.states = idxRes.data.states;
      state.active_states = idxRes.data.active_states || [];
      state.terminal_states = idxRes.data.terminal_states || [];
      buildColumns();
    } else if (state.states.length === 0) {
      // fallback
      state.states = FALLBACK_STATES;
      buildColumns();
    }
  } else if (state.states.length === 0) {
    // index 실패 + 컬럼 없음 → 기본 컬럼
    state.states = FALLBACK_STATES;
    buildColumns();
  }

  state.runningById.clear();
  if (symRes.ok && symRes.data) {
    state.symphonyAlive = true;
    state.defaultAgentKind = symRes.data.workflow?.default_agent_kind || "";
    state.branchPolicy = symRes.data.workflow?.branch_policy || null;
    updateBranchPolicy(state.branchPolicy);
    const running = symRes.data.running || [];
    for (const r of running) {
      const id = r.issue_identifier || r.issue_id;
      if (id) state.runningById.set(id, r);
    }
    // running 상태로 ticket의 state도 갱신 (orchestrator가 더 최신)
    for (const r of running) {
      const id = r.issue_identifier || r.issue_id;
      const t = state.tickets.find((x) => x.id === id || x.identifier === id);
      if (t && r.state) t.state = r.state;
    }
  } else {
    state.symphonyAlive = false;
    state.defaultAgentKind = "";
    state.branchPolicy = null;
    updateBranchPolicy(null);
  }

  state.lastPollAt = new Date().toISOString();
  updateStatus();
  renderBoard();
  refreshBranchControls();
}

// ---- 키보드 단축키 ----
function visibleCardsInDomOrder() {
  return Array.from(boardEl.querySelectorAll(".card:not(.hidden)"));
}

function moveFocus(direction) {
  const cards = visibleCardsInDomOrder();
  if (cards.length === 0) return;
  let idx = cards.findIndex((c) => c.dataset.id === state.focusedId);
  if (idx < 0) {
    idx = 0;
  } else {
    idx = (idx + direction + cards.length) % cards.length;
  }
  // remove old focus
  boardEl.querySelectorAll(".card.focused").forEach((c) => c.classList.remove("focused"));
  const target = cards[idx];
  target.classList.add("focused");
  state.focusedId = target.dataset.id;
  target.focus({ preventScroll: false });
  target.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function bindShortcuts() {
  document.addEventListener("keydown", (e) => {
    // modal 열려 있을 때
    if (!modalBackdrop.hidden) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeTicketDetail();
      }
      return;
    }
    // 검색창 포커스 중에는 글자 입력 허용
    if (document.activeElement === searchInput) {
      if (e.key === "Escape") {
        searchInput.blur();
        searchInput.value = "";
        renderBoard();
      }
      return;
    }
    switch (e.key) {
      case "r":
      case "R":
        e.preventDefault();
        // 헤더 버튼과 동일 동작: orchestrator refresh → local poll
        refreshBtn.click();
        break;
      case "/":
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
        break;
      case "j":
        e.preventDefault();
        moveFocus(1);
        break;
      case "k":
        e.preventDefault();
        moveFocus(-1);
        break;
      case "Enter":
        if (state.focusedId) {
          e.preventDefault();
          openTicketDetail(state.focusedId, state.runningById.get(state.focusedId) || null);
        }
        break;
      case "Escape":
        // 검색 필터 클리어
        if (searchInput.value) {
          searchInput.value = "";
          renderBoard();
        }
        break;
      case "[":
        e.preventDefault();
        nudgeZoom(-ZOOM_STEP);
        break;
      case "]":
        e.preventDefault();
        nudgeZoom(+ZOOM_STEP);
        break;
      case "\\":
        e.preventDefault();
        setZoom(ZOOM_DEFAULT);
        break;
      default:
        break;
    }
  });
}

function bindUi() {
  // 새로고침: orchestrator에 즉시 reconcile 요청 → 그 후 local poll 1회.
  // orchestrator down이면 refresh는 실패해도 local poll은 계속 진행.
  refreshBtn.addEventListener("click", async () => {
    await withButtonLock(refreshBtn, async () => {
      await refreshSymphony();
      await poll();
    });
  });
  searchInput.addEventListener("input", () => renderBoard());
  modalCloseBtn.addEventListener("click", () => closeTicketDetail());
  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) closeTicketDetail();
  });
  featureBaseSelect?.addEventListener("change", () => {
    saveSelectedBranchPolicy(featureBaseSelect);
  });
  mergeTargetSelect?.addEventListener("change", () => {
    saveSelectedBranchPolicy(mergeTargetSelect);
  });
}

async function start() {
  // zoom은 첫 paint 전에 복원해서 깜박임(FOUC) 방지
  applyZoom(readZoom());
  bindZoomControls();
  bindUi();
  bindShortcuts();
  schedulePoll(0); // 즉시 1회 → 이후 자체 재예약
}

// setTimeout 재귀 — 이전 poll이 종료된 후에만 다음 사이클 예약.
// setInterval과 달리 시간이 오래 걸린 사이클이 다음 사이클과 겹치지 않는다.
function schedulePoll(delay) {
  if (state.pollStopped) return;
  state.pollTimer = setTimeout(async () => {
    try {
      await poll();
    } catch (e) {
      // 단발 실패는 무시 — 다음 사이클에서 회복 시도
    } finally {
      schedulePoll(POLL_INTERVAL_MS);
    }
  }, delay);
}

// DOM ready 후 시작
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
