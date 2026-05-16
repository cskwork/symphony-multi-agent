// settings.js — config.yaml + .env editor modal.
// XSS 가드: utils.el() 만 사용해 createTextNode 경로로만 사용자 입력 렌더.

import { el } from "./utils.js";
import { fetchSettings, saveSettings } from "./api.js";

const DOGRAH_FIELDS = [
  { key: "base_url", label: "Base URL", placeholder: "http://localhost:8000", type: "text" },
  { key: "mcp_url", label: "MCP URL", placeholder: "http://localhost:8000/api/v1/mcp/", type: "text" },
  { key: "ui_url", label: "UI URL", placeholder: "http://localhost:3010", type: "text" },
  { key: "api_key", label: "API Key", placeholder: "dgr_...", type: "password" },
];

const DB_FIELDS = [
  { key: "driver", label: "Driver", type: "select" },
  { key: "host", label: "Host", type: "text", placeholder: "localhost" },
  { key: "port", label: "Port", type: "text", placeholder: "3306" },
  { key: "user", label: "User", type: "text", placeholder: "root" },
  { key: "password", label: "Password", type: "password" },
  { key: "database", label: "Database", type: "text", placeholder: "mydb" },
];

const FALLBACK_DRIVERS = ["mysql", "postgres", "sqlite", "mssql", "oracle"];

const state = {
  drivers: FALLBACK_DRIVERS.slice(),
  dograh: { base_url: "", mcp_url: "", ui_url: "", api_key: "" },
  databases: {}, // { name: { driver, host, port, user, password, database } }
  configPath: "",
  envPath: "",
  prevFocus: null,
};

function emptyDb() {
  return { driver: "mysql", host: "", port: "", user: "", password: "", database: "" };
}

function uniqueDbName(base) {
  let n = base;
  let i = 1;
  while (state.databases[n]) {
    n = `${base}_${i}`;
    i += 1;
  }
  return n;
}

function setStatus(text, kind) {
  const node = document.getElementById("settings-status");
  if (!node) return;
  node.textContent = text || "";
  node.dataset.kind = kind || "";
}

function renderDograh() {
  const wrap = document.getElementById("settings-dograh-fields");
  if (!wrap) return;
  wrap.replaceChildren();
  for (const f of DOGRAH_FIELDS) {
    const inputId = `settings-dograh-${f.key}`;
    const label = el(
      "label",
      { class: "settings-label", for: inputId },
      f.label
    );
    const input = el("input", {
      id: inputId,
      class: "settings-input",
      type: f.type,
      placeholder: f.placeholder || "",
      value: state.dograh[f.key] || "",
      autocomplete: "off",
      spellcheck: "false",
      oninput: (ev) => {
        state.dograh[f.key] = ev.target.value;
        renderEnvPreview();
      },
    });
    const cell = el("div", { class: "settings-cell" }, label, input);
    wrap.appendChild(cell);
  }
}

function renderDriverSelect(currentValue, onChange) {
  const select = el("select", {
    class: "settings-input settings-select",
    onchange: (ev) => onChange(ev.target.value),
  });
  for (const drv of state.drivers) {
    const opt = el("option", { value: drv }, drv);
    if (drv === currentValue) opt.selected = true;
    select.appendChild(opt);
  }
  return select;
}

function renderDatabases() {
  const list = document.getElementById("settings-db-list");
  if (!list) return;
  list.replaceChildren();
  const names = Object.keys(state.databases).sort();
  if (names.length === 0) {
    list.appendChild(
      el("p", { class: "settings-empty" }, "데이터베이스 없음 — 우측 상단의 + 추가로 시작")
    );
    return;
  }
  for (const name of names) {
    list.appendChild(renderDbCard(name));
  }
}

function renderDbCard(name) {
  const entry = state.databases[name];
  const card = el("div", { class: "settings-db-card", dataset: { name } });

  const nameInput = el("input", {
    class: "settings-input settings-db-name",
    type: "text",
    value: name,
    pattern: "^[a-z][a-z0-9_]*$",
    "aria-label": "DB 이름 (소문자/숫자/_)",
    onchange: (ev) => {
      const newName = (ev.target.value || "").trim();
      if (!/^[a-z][a-z0-9_]{0,63}$/.test(newName)) {
        setStatus(`이름은 [a-z][a-z0-9_]* 패턴이어야 합니다: ${newName}`, "error");
        ev.target.value = name;
        return;
      }
      if (newName !== name) {
        if (state.databases[newName]) {
          setStatus(`이미 존재하는 이름: ${newName}`, "error");
          ev.target.value = name;
          return;
        }
        state.databases[newName] = state.databases[name];
        delete state.databases[name];
        setStatus("", "");
        renderDatabases();
        renderEnvPreview();
      }
    },
  });

  const deleteBtn = el(
    "button",
    {
      class: "settings-db-delete",
      type: "button",
      title: `${name} 삭제`,
      onclick: () => {
        delete state.databases[name];
        renderDatabases();
        renderEnvPreview();
      },
    },
    "삭제"
  );

  const head = el(
    "div",
    { class: "settings-db-head" },
    el("span", { class: "settings-db-label" }, "이름"),
    nameInput,
    deleteBtn
  );

  const grid = el("div", { class: "settings-grid" });
  for (const f of DB_FIELDS) {
    const inputId = `settings-db-${name}-${f.key}`;
    const label = el("label", { class: "settings-label", for: inputId }, f.label);
    let input;
    if (f.type === "select") {
      input = renderDriverSelect(entry[f.key] || "mysql", (v) => {
        entry.driver = v;
        renderEnvPreview();
      });
      input.id = inputId;
    } else {
      input = el("input", {
        id: inputId,
        class: "settings-input",
        type: f.type,
        placeholder: f.placeholder || "",
        value: entry[f.key] || "",
        autocomplete: "off",
        spellcheck: "false",
        oninput: (ev) => {
          entry[f.key] = ev.target.value;
          renderEnvPreview();
        },
      });
    }
    grid.appendChild(el("div", { class: "settings-cell" }, label, input));
  }

  card.appendChild(head);
  card.appendChild(grid);
  return card;
}

function buildEnvPreview() {
  const lines = [];
  const d = state.dograh;
  if (d.base_url) lines.push(`DOGRAH_BASE_URL=${quote(d.base_url)}`);
  if (d.mcp_url) lines.push(`DOGRAH_MCP_URL=${quote(d.mcp_url)}`);
  if (d.ui_url) lines.push(`DOGRAH_UI_URL=${quote(d.ui_url)}`);
  if (d.api_key) lines.push(`DOGRAH_API_KEY=${quote(d.api_key)}`);
  const names = Object.keys(state.databases).sort();
  for (const n of names) {
    const e = state.databases[n];
    const u = n.toUpperCase();
    lines.push(`${u}_DRIVER=${quote(e.driver || "")}`);
    lines.push(`${u}_HOST=${quote(e.host || "")}`);
    lines.push(`${u}_PORT=${quote(e.port || "")}`);
    lines.push(`${u}_USER=${quote(e.user || "")}`);
    lines.push(`${u}_PASSWORD=${quote(e.password || "")}`);
    lines.push(`${u}_DATABASE=${quote(e.database || "")}`);
    lines.push(`${u}_URL=${quote(buildDbUrl(e))}`);
  }
  return lines.join("\n");
}

function quote(v) {
  if (v === "" || v === undefined || v === null) return "";
  if (/[\s"'\\$`#=]/.test(String(v))) {
    return `"${String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return String(v);
}

function buildDbUrl(e) {
  const driver = e.driver || "";
  const scheme = driver === "postgres" ? "postgresql" : driver;
  if (driver === "sqlite") {
    return e.database ? `sqlite:///${e.database}` : "sqlite://";
  }
  let auth = "";
  if (e.user) {
    auth = e.password
      ? `${encodeURIComponent(e.user)}:${encodeURIComponent(e.password)}@`
      : `${encodeURIComponent(e.user)}@`;
  }
  const netloc = e.port ? `${e.host || ""}:${e.port}` : e.host || "";
  const path = e.database ? `/${e.database}` : "";
  return `${scheme}://${auth}${netloc}${path}`;
}

function renderEnvPreview() {
  const pre = document.getElementById("settings-env-preview");
  if (!pre) return;
  const text = buildEnvPreview();
  pre.textContent = text || "—";
}

function renderPaths() {
  const node = document.getElementById("settings-paths");
  if (!node) return;
  node.textContent = state.configPath
    ? `${state.configPath} ↔ ${state.envPath}`
    : "";
}

async function loadAndRender() {
  setStatus("불러오는 중…", "");
  const r = await fetchSettings();
  if (!r.ok) {
    setStatus(`설정 로드 실패: ${r.error?.message || r.status}`, "error");
    return;
  }
  const cfg = r.data?.config || {};
  state.dograh = {
    base_url: cfg.dograh?.base_url || "",
    mcp_url: cfg.dograh?.mcp_url || "",
    ui_url: cfg.dograh?.ui_url || "",
    api_key: cfg.dograh?.api_key || "",
  };
  const dbs = cfg.databases || {};
  state.databases = {};
  for (const [name, entry] of Object.entries(dbs)) {
    state.databases[name] = {
      driver: entry?.driver || "mysql",
      host: entry?.host || "",
      port: entry?.port || "",
      user: entry?.user || "",
      password: entry?.password || "",
      database: entry?.database || "",
    };
  }
  if (Array.isArray(r.data?.drivers) && r.data.drivers.length > 0) {
    state.drivers = r.data.drivers.slice();
  }
  state.configPath = r.data?.config_path || "";
  state.envPath = r.data?.env_path || "";
  setStatus("", "");
  renderDograh();
  renderDatabases();
  renderEnvPreview();
  renderPaths();
}

async function handleSave() {
  setStatus("저장 중…", "");
  const payload = {
    dograh: { ...state.dograh },
    databases: {},
  };
  for (const [name, entry] of Object.entries(state.databases)) {
    payload.databases[name] = { ...entry };
  }
  const r = await saveSettings(payload);
  if (!r.ok) {
    const msg = r.error?.message
      ? `${r.error.error || "error"}: ${r.error.message}`
      : `HTTP ${r.status}`;
    setStatus(`저장 실패 — ${msg}`, "error");
    return;
  }
  setStatus("저장 완료. .env 와 config.yaml 이 갱신됐습니다.", "ok");
}

function openModal() {
  const backdrop = document.getElementById("settings-backdrop");
  if (!backdrop) return;
  state.prevFocus = document.activeElement;
  backdrop.hidden = false;
  document.body.classList.add("settings-open");
  loadAndRender().then(() => {
    const first = document.querySelector("#settings-dograh-fields input");
    if (first) first.focus();
  });
}

function closeModal() {
  const backdrop = document.getElementById("settings-backdrop");
  if (!backdrop) return;
  backdrop.hidden = true;
  document.body.classList.remove("settings-open");
  if (state.prevFocus && typeof state.prevFocus.focus === "function") {
    try { state.prevFocus.focus(); } catch { /* noop */ }
  }
}

function isModalOpen() {
  const backdrop = document.getElementById("settings-backdrop");
  return backdrop && !backdrop.hidden;
}

function wire() {
  const openBtn = document.getElementById("settings-btn");
  const closeBtn = document.getElementById("settings-close");
  const cancelBtn = document.getElementById("settings-cancel");
  const saveBtn = document.getElementById("settings-save");
  const addBtn = document.getElementById("settings-add-db");
  const backdrop = document.getElementById("settings-backdrop");

  if (openBtn) openBtn.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
  if (saveBtn) saveBtn.addEventListener("click", handleSave);
  if (addBtn) {
    addBtn.addEventListener("click", () => {
      const name = uniqueDbName("db");
      state.databases[name] = emptyDb();
      renderDatabases();
      renderEnvPreview();
    });
  }
  if (backdrop) {
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) closeModal();
    });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && isModalOpen()) {
      ev.preventDefault();
      closeModal();
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", wire, { once: true });
} else {
  wire();
}
