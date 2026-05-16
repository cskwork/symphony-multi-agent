// api.js — Symphony / kanban fetch 래퍼

const TIMEOUT_MS = 4000;

async function fetchWithTimeout(url, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url, { ...opts, signal: ctrl.signal });
    return resp;
  } finally {
    clearTimeout(t);
  }
}

export async function fetchSymphonyState() {
  try {
    const r = await fetchWithTimeout("/api/symphony/state");
    if (!r.ok) {
      // server.py가 599 status로 unreachable을 알려준다
      const data = await safeJson(r);
      return { ok: false, status: r.status, error: data };
    }
    return { ok: true, status: r.status, data: await r.json() };
  } catch (e) {
    return { ok: false, status: 0, error: { message: String(e) } };
  }
}

export async function fetchKanbanIndex() {
  try {
    const r = await fetchWithTimeout("/api/kanban/index");
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, data: await r.json() };
  } catch (e) {
    return { ok: false, status: 0, error: { message: String(e) } };
  }
}

export async function fetchKanbanRaw(id) {
  try {
    const r = await fetchWithTimeout(`/api/kanban/${encodeURIComponent(id)}.md`);
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, text: await r.text() };
  } catch (e) {
    return { ok: false, status: 0, error: { message: String(e) } };
  }
}

async function safeJson(r) {
  try {
    return await r.json();
  } catch {
    return null;
  }
}

export function rawKanbanUrl(id) {
  return `/api/kanban/${encodeURIComponent(id)}.md`;
}

// ---- mutating actions (whitelist: refresh / pause / resume) ----
//   - 모두 POST, payload 없음. orchestrator는 idempotent하게 처리.
//   - 응답 { ok, status, data | error }로 정규화. UI에서 분기.

async function postNoBody(url) {
  try {
    const r = await fetchWithTimeout(url, { method: "POST" });
    const data = await safeJson(r);
    if (!r.ok) return { ok: false, status: r.status, error: data };
    return { ok: true, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, error: { message: String(e) } };
  }
}

export function pauseTicket(id) {
  return postNoBody(`/api/symphony/${encodeURIComponent(id)}/pause`);
}

export function resumeTicket(id) {
  return postNoBody(`/api/symphony/${encodeURIComponent(id)}/resume`);
}

export function refreshSymphony() {
  return postNoBody("/api/symphony/refresh");
}
