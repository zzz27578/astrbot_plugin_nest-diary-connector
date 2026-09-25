const APP_VERSION = "0.6.0";
const PLUGIN_PAGE_BRIDGE = window.AstrBotPluginPage || null;
const PLUGIN_PAGE_MODULE_URL = new URL(import.meta.url);
let pluginPageContext = null;
let pluginWebuiBase = "";
let pluginAvatarDataUrl = "";
const pluginBridgeReady = PLUGIN_PAGE_BRIDGE
  ? PLUGIN_PAGE_BRIDGE.ready().then((context) => {
      pluginPageContext = context || null;
      return context;
    })
  : Promise.resolve(null);


const DIARY_T2I_TEMPLATES = [
  {
    id: "plain_note",
    name: "清简便签",
    tone: "浅色、易读、适合日常推送",
    template: `<div style="width:760px;padding:44px;font-family:'Microsoft YaHei',sans-serif;background:#fffdf8;color:#242830;border:2px solid #242830;">
  <p style="margin:0 0 12px;color:#176f66;font-size:18px;font-weight:800;">{{ date }} · {{ notebook_name }}</p>
  <h1 style="margin:0 0 22px;font-size:34px;line-height:1.2;">{{ title }}</h1>
  <div style="white-space:pre-wrap;font-size:20px;line-height:1.75;">{{ body }}</div>
</div>`,
  },
  {
    id: "terminal_report",
    name: "终端报告",
    tone: "冷灰信息卡，适合群聊日报",
    template: `<div style="width:820px;padding:38px;font-family:'Microsoft YaHei',sans-serif;background:#f1f4f2;color:#1f2527;border:1px solid #2c3b3b;">
  <div style="display:flex;justify-content:space-between;gap:18px;border-bottom:3px solid #2c3b3b;padding-bottom:14px;margin-bottom:24px;">
    <strong style="font-size:18px;">小窝日记</strong><span style="color:#58706b;font-weight:800;">{{ date }} / {{ notebook_name }}</span>
  </div>
  <h1 style="margin:0 0 20px;font-size:32px;line-height:1.18;">{{ title }}</h1>
  <div style="white-space:pre-wrap;font-size:19px;line-height:1.72;">{{ body }}</div>
</div>`,
  },
  {
    id: "magazine_page",
    name: "杂志页",
    tone: "留白更大，适合私聊推送",
    template: `<div style="width:760px;padding:52px 48px;font-family:'Microsoft YaHei',sans-serif;background:#fbfaf5;color:#202124;">
  <div style="width:64px;height:5px;background:#d25f45;margin-bottom:28px;"></div>
  <p style="margin:0 0 16px;color:#6a756f;font-size:17px;font-weight:800;">{{ date }} · {{ notebook_name }}</p>
  <h1 style="margin:0 0 26px;font-size:38px;line-height:1.16;">{{ title }}</h1>
  <div style="white-space:pre-wrap;font-size:20px;line-height:1.86;">{{ body }}</div>
</div>`,
  },
];

const app = document.getElementById("app");
const state = {
  view: initialViewFromLocation(),
  selectedDate: initialDateFromLocation(),
  editingDate: initialEditDateFromLocation(),
  selectedImpressionName: initialImpressionFromLocation(),
  bootstrap: null,
  notebooks: [],
  diary: {
    items: [],
    archive: [],
    selected: null,
    loaded: false,
    loadedNotebookId: null,
    composerOpen: initialComposerFromLocation(),
    composerDate: initialComposeDateFromLocation(),
    filters: initialDiaryFilters(),
  },
  search: { query: initialSearchFromLocation(), notebook_id: initialNotebookFromLocation(), results: [], backend: "" },
  impressions: [],
  selectedImpression: null,
  media: [],
  mediaStorage: { bytes: 0, count: 0, label: "0 B" },
  mediaOrganization: { folders: [], asset_locations: {}, trash: [] },
  selectedMedia: null,
  mediaFloating: false,
  mediaFloatPreferred: false,
  mediaFloatResumePending: false,
  mediaMode: "main",
  activeMediaFolderId: "",
  expandedMediaFolderIds: [],
  mediaSuppressClickUntil: 0,
  mediaFloatPositions: {},
  mediaFolderModalOpen: false,
  mediaFolderEditingId: "",
  mediaNoteEditing: false,
  memos: [],
  memoSummary: { count: 0, pinned: 0, archived: 0, sensitive: 0 },
  selectedMemoId: initialMemoFromLocation(),
  memoEditorOpen: false,
  memoDrag: null,
  memoSuppressClickUntil: 0,
  memoIncludeArchived: false,
  memoRevealSensitive: false,
  settings: null,
  notice: "",
  toast: "",
  error: "",
  noticeTimer: null,
  rendered: new Set(),
  settingsMenuOpen: initialViewFromLocation() === "settings",
  settingsSection: initialSettingsSectionFromLocation(),
  settingsModuleDetail: initialSettingsModuleFromLocation(),
  moduleFilter: "all",
  moduleInstallOpen: false,
  moduleInstallBusy: false,
  moduleUninstallBusy: "",
  moduleInstallMessage: "",
  versionBusy: false,
  onboardingOpen: false,
  onboardingStep: 0,
  onboardingChecked: false,
  t2iTemplateDialogOpen: false,
  t2iCustomOpen: false,
  notebookDeleteIds: [],
  moduleMounts: {},
  moduleMountError: "",
};

// 官方基础入口。自定义模块入口不写在这里，由 module_catalog.nav_entries 生成。
const navItems = [
  ["dashboard", "首页"],
  ["diary", "日记"],
  ["search", "查找"],
  ["impressions", "印象"],
  ["media", "媒体"],
  ["memos", "备忘录"],
  ["settings", "设置"],
];

const MODULE_VIEW_PREFIX = "module:";

function isModuleView(view = state.view) {
  return String(view || "").startsWith(MODULE_VIEW_PREFIX);
}

function moduleIdFromView(view = state.view) {
  return isModuleView(view) ? String(view).slice(MODULE_VIEW_PREFIX.length) : "";
}

function moduleViewFor(moduleId) {
  return `${MODULE_VIEW_PREFIX}${moduleId}`;
}

/** 已启用、声明了入口且通过框架验真的自定义模块入口。 */
function moduleNavEntries() {
  const catalog = state.bootstrap?.module_catalog || state.settings?.module_catalog || {};
  const entries = catalog.nav_entries;
  return Array.isArray(entries) ? entries : [];
}

function moduleNavEntry(moduleId) {
  return moduleNavEntries().find((entry) => entry.id === moduleId) || null;
}

function initialViewFromLocation() {
  const path = window.location.pathname;
  if (path.startsWith("/m/")) {
    const moduleId = decodeURIComponent(path.slice(3).split("/")[0] || "");
    if (moduleId) return moduleViewFor(moduleId);
  }
  if (path.startsWith("/diary")) return "diary";
  if (path === "/write") return "diary";
  if (path === "/search") return "search";
  if (path === "/impressions") return "impressions";
  if (path === "/media") return "media";
  if (path === "/memos") return "memos";
  if (path === "/settings") return "settings";
  return "dashboard";
}

function initialDateFromLocation() {
  const path = window.location.pathname;
  if (!path.startsWith("/diary/")) return "";
  return decodeURIComponent(path.split("/").filter(Boolean).pop() || "");
}

function initialDiaryFilters() {
  const date = initialDateFromLocation();
  const notebook_id = initialNotebookFromLocation();
  return date ? { notebook_id, year: date.slice(0, 4), month: date.slice(0, 7), date } : { notebook_id, year: "", month: "", date: "" };
}

function initialNotebookFromLocation() {
  return new URLSearchParams(window.location.search).get("notebook_id") || "";
}

function initialEditDateFromLocation() {
  if (window.location.pathname !== "/write") return "";
  return new URLSearchParams(window.location.search).get("date") || "";
}

function localDateString(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function initialComposerFromLocation() {
  return window.location.pathname === "/write";
}

function initialComposeDateFromLocation() {
  if (window.location.pathname !== "/write") return localDateString();
  return new URLSearchParams(window.location.search).get("date") || localDateString();
}

function initialSearchFromLocation() {
  if (window.location.pathname !== "/search") return "";
  return new URLSearchParams(window.location.search).get("q") || "";
}

function initialImpressionFromLocation() {
  if (window.location.pathname !== "/impressions") return "";
  return new URLSearchParams(window.location.search).get("name") || "";
}

function initialMemoFromLocation() {
  if (window.location.pathname !== "/memos") return "";
  return new URLSearchParams(window.location.search).get("memo") || "";
}

function initialSettingsSectionFromLocation() {
  if (window.location.pathname !== "/settings") return "appearance";
  const section = new URLSearchParams(window.location.search).get("section") || "appearance";
  return ["appearance", "modules", "access", "backup"].includes(section) ? section : "appearance";
}

function initialSettingsModuleFromLocation() {
  if (window.location.pathname !== "/settings") return "";
  return new URLSearchParams(window.location.search).get("module") || "";
}

function applyRouteStateFromLocation() {
  state.view = initialViewFromLocation();
  state.selectedDate = initialDateFromLocation();
  state.editingDate = initialEditDateFromLocation();
  state.selectedImpressionName = initialImpressionFromLocation();
  state.selectedMemoId = initialMemoFromLocation();
  state.memoEditorOpen = Boolean(state.selectedMemoId);
  state.memoIncludeArchived = new URLSearchParams(window.location.search).get("archived") === "1";
  state.memoRevealSensitive = new URLSearchParams(window.location.search).get("reveal") === "1";
  state.diary.composerOpen = initialComposerFromLocation();
  state.diary.composerDate = initialComposeDateFromLocation();
  state.diary.filters = initialDiaryFilters();
  state.search.query = initialSearchFromLocation();
  state.search.notebook_id = initialNotebookFromLocation();
  state.settingsMenuOpen = state.view === "settings";
  state.settingsSection = initialSettingsSectionFromLocation();
  state.settingsModuleDetail = initialSettingsModuleFromLocation();
}

function queryString(params) {
  const value = params.toString();
  return value ? `?${value}` : "";
}

function routeForState(view = state.view) {
  const params = new URLSearchParams();
  if (isModuleView(view)) return `/m/${encodeURIComponent(moduleIdFromView(view))}`;
  if (view === "dashboard") return "/";
  if (view === "media") return "/media";
  if (view === "memos") {
    if (state.selectedMemoId) params.set("memo", state.selectedMemoId);
    if (state.memoIncludeArchived) params.set("archived", "1");
    if (state.memoRevealSensitive) params.set("reveal", "1");
    return `/memos${queryString(params)}`;
  }
  if (view === "settings") {
    params.set("section", state.settingsSection || "appearance");
    if (state.settingsModuleDetail) params.set("module", state.settingsModuleDetail);
    return `/settings${queryString(params)}`;
  }
  if (view === "search") {
    if (state.search.query) params.set("q", state.search.query);
    if (state.search.notebook_id) params.set("notebook_id", state.search.notebook_id);
    return `/search${queryString(params)}`;
  }
  if (view === "impressions") {
    if (state.selectedImpressionName) params.set("name", state.selectedImpressionName);
    return `/impressions${queryString(params)}`;
  }
  if (view === "diary") {
    const notebookId = state.diary.filters.notebook_id || state.diary.selected?.notebook_id || "";
    if (state.diary.composerOpen) {
      const date = state.diary.composerDate || state.editingDate || state.selectedDate || "";
      if (date) params.set("date", date);
      if (notebookId && notebookId !== "default") params.set("notebook_id", notebookId);
      return `/write${queryString(params)}`;
    }
    const date = state.selectedDate || state.diary.selected?.date || state.diary.filters.date || "";
    if (notebookId && notebookId !== "default") params.set("notebook_id", notebookId);
    if (date) return `/diary/${encodeURIComponent(date)}${queryString(params)}`;
    return `/diary${queryString(params)}`;
  }
  return "/";
}

function syncRouteForState(view = state.view, replace = false) {
  if (PLUGIN_PAGE_BRIDGE) return;
  const next = routeForState(view);
  const current = `${window.location.pathname}${window.location.search}`;
  if (next === current) return;
  window.history[replace ? "replaceState" : "pushState"]({ view }, "", next);
}

function pluginPageAssetUrl(relativePath) {
  const url = new URL(relativePath, PLUGIN_PAGE_MODULE_URL);
  PLUGIN_PAGE_MODULE_URL.searchParams.forEach((value, key) => {
    if (!url.searchParams.has(key)) url.searchParams.set(key, value);
  });
  return url.href;
}

function pluginPageHost(host) {
  const normalized = String(host || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  const publicHost = !normalized || ["0.0.0.0", "::", "::0", "localhost", "127.0.0.1", "::1"].includes(normalized)
    ? window.location.hostname
    : String(host).trim();
  return publicHost.includes(":") && !publicHost.startsWith("[") ? `[${publicHost}]` : publicHost;
}

function updatePluginWebuiBase(result = {}) {
  if (!PLUGIN_PAGE_BRIDGE || !result.web_port) return;
  pluginWebuiBase = `http://${pluginPageHost(result.web_host)}:${Number(result.web_port)}`;
}

function webuiAssetUrl(value = "") {
  const url = String(value || "").trim();
  if (!PLUGIN_PAGE_BRIDGE || !url || /^(?:data:|blob:|https?:\/\/)/i.test(url)) return url;
  if (url.startsWith("/api/ui/avatar")) return pluginAvatarDataUrl;
  return pluginWebuiBase && url.startsWith("/") ? `${pluginWebuiBase}${url}` : url;
}

async function refreshPluginAvatarData(force = false) {
  if (!PLUGIN_PAGE_BRIDGE || (pluginAvatarDataUrl && !force)) return pluginAvatarDataUrl;
  await pluginBridgeReady;
  const result = normalizePluginBridgeResult(await PLUGIN_PAGE_BRIDGE.apiGet("ui/avatar"));
  if (!result.ok) return "";
  pluginAvatarDataUrl = String(result.data?.avatar_data_url || "");
  return pluginAvatarDataUrl;
}

function normalizePluginBridgeResult(value) {
  if (
    value &&
    typeof value === "object" &&
    "ok" in value &&
    ("data" in value || "detail" in value || "status_code" in value || "web_port" in value)
  ) {
    return value;
  }
  if (value?.data && typeof value.data === "object" && "ok" in value.data) return value.data;
  return { ok: true, data: value };
}

async function pluginApi(path, options = {}) {
  await pluginBridgeReady;
  const method = String(options.method || "GET").toUpperCase();
  let body = options.body;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch (_) {}
  }
  const result = normalizePluginBridgeResult(await PLUGIN_PAGE_BRIDGE.apiPost("ui/proxy", { path, method, body }));
  if (!result.ok) throw new Error(result.detail || `WebUI request failed (${result.status_code || "unknown"})`);
  updatePluginWebuiBase(result);
  const avatarUrl = result.data?.settings?.brand_avatar_url;
  if (String(avatarUrl || "").startsWith("/api/ui/avatar")) await refreshPluginAvatarData();
  return result.data;
}

async function api(path, options = {}) {
  if (PLUGIN_PAGE_BRIDGE) return pluginApi(path, options);
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    location.href = "/login";
    return null;
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function confirmAction(message) {
  if (!PLUGIN_PAGE_BRIDGE) return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "plugin-confirm-overlay";
    overlay.innerHTML = `
      <section class="plugin-confirm" role="dialog" aria-modal="true" aria-labelledby="plugin-confirm-title">
        <p class="eyebrow">请确认</p>
        <h2 id="plugin-confirm-title">确认操作</h2>
        <p>${escapeHtml(message)}</p>
        <div class="actions">
          <button type="button" data-confirm-cancel>取消</button>
          <button class="primary" type="button" data-confirm-ok>确认</button>
        </div>
      </section>
    `;
    const finish = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.querySelector("[data-confirm-cancel]").addEventListener("click", () => finish(false));
    overlay.querySelector("[data-confirm-ok]").addEventListener("click", () => finish(true));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(false);
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") finish(false);
    });
    document.body.appendChild(overlay);
    overlay.querySelector("[data-confirm-ok]").focus();
  });
}

function splitWords(value = "") {
  return String(value)
    .replace(/[，、；;]/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function ensureShell() {
  if (document.getElementById("view-diary")) return;
  app.innerHTML = `
    <div class="app" data-app-version="${APP_VERSION}">
      <aside class="nav">
        <button class="brand" data-view="dashboard" type="button">
          <span class="brand-mark" id="brand-mark">窝</span>
          <span><strong id="brand-title">小窝</strong><small>私有空间</small></span>
        </button>
        <nav class="nav-links" id="nav-links">
          ${renderNavLinks()}
        </nav>
      </aside>
      <main class="main" id="view">
        <div id="notice-slot"></div>
        <section class="view-panel" id="view-dashboard" data-panel="dashboard"></section>
        <section class="view-panel" id="view-diary" data-panel="diary"></section>
        <section class="view-panel" id="view-search" data-panel="search"></section>
        <section class="view-panel" id="view-impressions" data-panel="impressions"></section>
        <section class="view-panel" id="view-media" data-panel="media"></section>
        <section class="view-panel" id="view-memos" data-panel="memos"></section>
        <section class="view-panel" id="view-settings" data-panel="settings"></section>
      </main>
      <div id="global-dialog-root"></div>
    </div>
  `;
}

/** 自定义模块面板按需创建，卸载或停用后会被移除。 */
function ensureModulePanel(moduleId) {
  ensureShell();
  const main = document.getElementById("view");
  if (!main) return null;
  const panelId = `view-module-${moduleId}`;
  let node = document.getElementById(panelId);
  if (!node) {
    node = document.createElement("section");
    node.className = "view-panel";
    node.id = panelId;
    node.dataset.panel = moduleViewFor(moduleId);
    node.dataset.moduleHost = moduleId;
    main.appendChild(node);
  }
  return node;
}

function panel(name) {
  if (isModuleView(name)) return ensureModulePanel(moduleIdFromView(name));
  return document.getElementById(`view-${name}`);
}

function updateShell() {
  ensureShell();
  const siteTitle = currentSiteTitle();
  const avatarUrl = currentAvatarUrl();
  document.title = siteTitle;
  const brandTitle = document.getElementById("brand-title");
  if (brandTitle) brandTitle.textContent = siteTitle;
  const brandMark = document.getElementById("brand-mark");
  if (brandMark) {
    brandMark.innerHTML = avatarUrl
      ? `<img src="${escapeHtml(avatarUrl)}" alt="${escapeHtml(siteTitle)}">`
      : `${escapeHtml(siteTitle.slice(0, 1) || "窝")}`;
  }
  const navLinks = document.getElementById("nav-links");
  if (navLinks) navLinks.innerHTML = renderNavLinks();
  document.querySelectorAll("[data-nav]").forEach((node) => node.classList.toggle("active", node.dataset.nav === state.view));
  document.querySelectorAll("[data-settings-toggle]").forEach((node) => {
    node.classList.toggle("open", state.settingsMenuOpen);
    node.setAttribute("aria-expanded", String(state.settingsMenuOpen));
  });
  document.querySelectorAll("[data-panel]").forEach((node) => {
    node.hidden = node.dataset.panel !== state.view;
  });
  const notice = document.getElementById("notice-slot");
  notice.innerHTML = `
    <div class="notification-stack" aria-live="polite">
      ${state.notice ? `<div class="notice">${escapeHtml(state.notice)}</div>` : ""}
      ${state.error ? `<div class="notice error">${escapeHtml(state.error)}</div>` : ""}
      ${state.toast ? `<div class="toast">${escapeHtml(state.toast)}</div>` : ""}
    </div>
  `;
  if ((state.notice || state.toast) && !state.noticeTimer) {
    state.noticeTimer = window.setTimeout(() => {
      state.notice = "";
      state.toast = "";
      state.noticeTimer = null;
      updateShell();
    }, 2600);
  }
}

async function refreshThemeStylesheet() {
  const target = document.getElementById("theme-stylesheet") || document.querySelector('link[rel="stylesheet"][href*="/theme.css"]');
  if (!target) return;
  if (PLUGIN_PAGE_BRIDGE) {
    try {
      target.textContent = await pluginApi("/theme.css");
    } catch (error) {
      console.error("Failed to load Nest theme in plugin page", error);
    }
    return;
  }
  const href = new URL(target.getAttribute("href") || "/theme.css", window.location.origin);
  href.searchParams.set("v", String(Date.now()));
  target.setAttribute("href", `${href.pathname}${href.search}`);
}

function activeT2iTemplate(settings = {}) {
  const custom = String(settings.diary_t2i_template || "").trim();
  const name = settings.diary_t2i_template_name || "";
  const customItem = customT2iTemplates(settings).find((item) => item.id === name);
  if (customItem) return customItem;
  const builtin = DIARY_T2I_TEMPLATES.find((item) => item.id === name);
  if (name === "custom" && custom && !custom.startsWith("{")) return { id: "custom", name: "自定义模板", tone: "使用你添加的模板", template: custom };
  if (builtin) return builtin;
  return DIARY_T2I_TEMPLATES[0];
}

function t2iTemplateById(id) {
  return DIARY_T2I_TEMPLATES.find((item) => item.id === id) || DIARY_T2I_TEMPLATES[0];
}

function customT2iTemplates(settings = {}) {
  const raw = String(settings.diary_t2i_template || "").trim();
  if (!raw) return [];
  if (!raw.startsWith("{")) {
    return settings.diary_t2i_template_name === "custom"
      ? [{ id: "custom", name: "自定义模板", tone: "你添加的模板", template: raw }]
      : [];
  }
  try {
    const parsed = JSON.parse(raw);
    const templates = Array.isArray(parsed.templates) ? parsed.templates : [];
    return templates
      .map((item) => ({
        id: String(item.id || `custom_${Date.now()}`).trim(),
        name: String(item.name || "自定义模板").trim(),
        tone: String(item.tone || "自定义").trim(),
        template: String(item.template || "").trim(),
      }))
      .filter((item) => item.id && item.template);
  } catch (_) {
    return [];
  }
}

function t2iTemplateStore(templates = []) {
  return JSON.stringify({ templates: templates.map((item) => ({ id: item.id, name: item.name, tone: item.tone, template: item.template })) });
}

function t2iPreviewHtml(template) {
  return String(template || "")
    .replaceAll("{{ date }}", "2026-05-20")
    .replaceAll("{{ notebook_name }}", "主群日记本")
    .replaceAll("{{ title }}", "今天的小窝被认真整理了一遍")
    .replaceAll("{{ body }}", "今天把日记本、推送和权限重新分清了。重要的是，群聊和私聊不会混在一起，写日记也会先看证据，再决定要不要记录。");
}

/** 官方基础入口的排序权重。自定义模块默认 500，落在备忘录与设置之间。 */
const NAV_ORDER = {
  dashboard: 10,
  diary: 20,
  search: 30,
  impressions: 40,
  media: 50,
  memos: 60,
  settings: 900,
};

function officialNavLinks() {
  return navItems
    .filter(([key]) => {
      if (key === "media") return isMediaEnabled();
      if (key === "impressions") return isImpressionsEnabled();
      if (key === "memos") return isMemosEnabled();
      return true;
    })
    .map(([key, label]) => ({
      view: key,
      label,
      icon: navIcon(key),
      iconUrl: "",
      order: NAV_ORDER[key] ?? 500,
      official: true,
    }));
}

function customNavLinks() {
  return moduleNavEntries().map((entry) => ({
    view: moduleViewFor(entry.id),
    label: entry.label || entry.id,
    icon: entry.icon || "modules",
    // 插件页里模块资源不能直接当 <img src> 用，缺少独立 WebUI 的 Cookie，
    // 所以那种情况退回内置图标。
    iconUrl: PLUGIN_PAGE_BRIDGE ? "" : entry.icon_url || "",
    order: Number.isFinite(entry.order) ? entry.order : 500,
    official: false,
  }));
}

function renderNavLinks() {
  const links = [...officialNavLinks(), ...customNavLinks()].sort(
    (left, right) => left.order - right.order || String(left.label).localeCompare(String(right.label))
  );
  return links
    .map((link) => {
      const children =
        link.view === "settings" && state.settingsMenuOpen
          ? `<div class="nav-submenu">
              ${settingsTab("appearance", "外观设置")}
              ${settingsTab("modules", "模块控制台")}
              ${settingsTab("access", "访问密钥")}
              ${settingsTab("backup", "导入导出")}
            </div>`
          : "";
      const attrs = link.view === "settings" ? `data-settings-toggle aria-expanded="${state.settingsMenuOpen}"` : "";
      const icon = link.iconUrl
        ? `<img class="ui-icon" src="${escapeHtml(link.iconUrl)}" alt="${escapeHtml(link.label)}" loading="lazy">`
        : iconImg(link.icon, link.label);
      return `<div class="nav-link-group"><button class="nav-link" data-nav="${escapeHtml(link.view)}" data-view="${escapeHtml(link.view)}" data-tour-target="nav-${escapeHtml(link.view)}" ${attrs} type="button">${icon}<span>${escapeHtml(link.label)}</span></button>${children}</div>`;
    })
    .join("");
}

function iconImg(name, label = "") {
  const source = PLUGIN_PAGE_BRIDGE
    ? pluginPageAssetUrl(`./icons/${encodeURIComponent(name)}.svg`)
    : `/app-assets/icons/${encodeURIComponent(name)}.svg`;
  return `<img class="ui-icon" src="${escapeHtml(source)}" alt="${escapeHtml(label)}" loading="lazy">`;
}

function navIcon(key) {
  return {
    dashboard: "home",
    diary: "diary",
    search: "search",
    impressions: "impressions",
    media: "media",
    memos: "memos",
    settings: "settings",
  }[key] || "settings";
}

function isMediaEnabled() {
  const settings = state.bootstrap?.settings || state.settings?.settings || {};
  return settings.enable_media_module !== false && (settings.enabled_official_modules || []).includes("media");
}

function isImpressionsEnabled() {
  const settings = state.bootstrap?.settings || state.settings?.settings || {};
  return settings.enable_impressions_module !== false && (settings.enabled_official_modules || []).includes("impressions");
}

function isMemosEnabled() {
  const settings = state.bootstrap?.settings || state.settings?.settings || {};
  return settings.enable_memos_module !== false && (settings.enabled_official_modules || []).includes("memos");
}

function currentSiteTitle() {
  return state.bootstrap?.settings?.site_title || state.settings?.settings?.site_title || "小窝";
}

function currentSiteSubtitle() {
  return state.bootstrap?.settings?.site_subtitle || state.settings?.settings?.site_subtitle || "把今天安放好，旧事也能被轻轻找回来";
}

function currentAvatarUrl() {
  return webuiAssetUrl(state.bootstrap?.settings?.brand_avatar_url || state.settings?.settings?.brand_avatar_url || "");
}

function pageHead(eyebrow, title, actions = "") {
  return `
    <header class="topbar">
      <div class="page-title">${eyebrow ? `<p>${escapeHtml(eyebrow)}</p>` : ""}<h1>${escapeHtml(title)}</h1></div>
      <div class="actions">${actions}</div>
    </header>
  `;
}

async function loadBootstrap() {
  if (!state.bootstrap) state.bootstrap = await api("/api/ui/bootstrap");
  state.notebooks = state.bootstrap?.notebooks || state.notebooks || [];
  await pruneModuleMounts();
  if (!state.onboardingChecked && state.bootstrap?.settings && state.bootstrap.settings.onboarding_completed === false) {
    state.onboardingOpen = true;
    state.onboardingStep = 0;
  }
  state.onboardingChecked = true;
}

async function setView(view, options = {}) {
  if (view !== "media") {
    if (state.view === "media" && state.mediaFloating) {
      state.mediaFloatPreferred = true;
      state.mediaFloating = false;
      state.mediaFloatResumePending = true;
    }
    stopMediaFloat();
    if (state.view === "media") state.mediaFloatPositions = {};
    state.selectedMedia = null;
    state.mediaFolderModalOpen = false;
    state.mediaFolderEditingId = "";
    state.t2iTemplateDialogOpen = false;
    state.t2iCustomOpen = false;
    renderGlobalDialogs();
  }
  state.view = view;
  state.notice = options.keepNotice ? state.notice : "";
  state.error = "";
  if (Object.prototype.hasOwnProperty.call(options, "date")) state.selectedDate = options.date || "";
  if (Object.prototype.hasOwnProperty.call(options, "notebook_id")) {
    state.diary.filters.notebook_id = options.notebook_id || "";
    state.search.notebook_id = options.notebook_id || "";
  }
  if (view === "diary" && !Object.prototype.hasOwnProperty.call(options, "notebook_id") && !Object.prototype.hasOwnProperty.call(options, "date") && !state.diary.composerOpen) {
    clearDiaryFilters();
    state.diary.selected = null;
    state.selectedDate = "";
  }
  if (Object.prototype.hasOwnProperty.call(options, "editDate")) state.editingDate = options.editDate || "";
  if (Object.prototype.hasOwnProperty.call(options, "compose")) state.diary.composerOpen = Boolean(options.compose);
  if (Object.prototype.hasOwnProperty.call(options, "query")) state.search.query = options.query || "";
  if (Object.prototype.hasOwnProperty.call(options, "memoId")) state.selectedMemoId = options.memoId || "";
  if (view !== "diary") {
    state.diary.composerOpen = false;
    state.editingDate = "";
  }
  if (!options.skipRoute) syncRouteForState(view, Boolean(options.replaceRoute));
  await loadView();
}

document.addEventListener(
  "click",
  (event) => {
    const target = event.target.closest("[data-view], [data-date], [data-open-write], [data-close-write], [data-edit-date], [data-search-query], [data-impression-name], [data-new-impression], [data-media-open], [data-media-close], [data-media-note-edit], [data-media-folder-create], [data-media-folder-edit], [data-media-folder-modal-close], [data-media-trash], [data-media-restore], [data-media-delete], [data-media-open-original], [data-media-toggle-float], [data-media-mode], [data-media-folder-collapse], [data-media-folder-expand], [data-media-folder-open], [data-media-folder-close], [data-media-dropdown], [data-memo-new], [data-memo-select], [data-memo-detail-back], [data-memo-pin], [data-memo-archive], [data-memo-delete], [data-memo-reveal], [data-memo-archived-toggle], [data-settings-section], [data-module-settings], [data-settings-back], [data-module-filter], [data-module-install-open], [data-module-install-close], [data-version-check], [data-version-update], [data-style-select], [data-onboarding-advance], [data-onboarding-next], [data-onboarding-prev], [data-onboarding-finish], [data-onboarding-close], [data-onboarding-replay], [data-notebook-add], [data-notebook-delete], [data-t2i-open], [data-t2i-close], [data-t2i-select], [data-t2i-custom-toggle], [data-t2i-custom-save], [data-module-uninstall]");
    if (!target) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (
      target.classList?.contains("media-dialog-backdrop") &&
      event.target !== target &&
      (
        target.dataset.mediaClose !== undefined ||
        target.dataset.mediaFolderModalClose !== undefined ||
        target.dataset.moduleInstallClose !== undefined ||
        target.dataset.t2iClose !== undefined
      )
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (target.dataset.view) {
      if (target.dataset.view === "settings") {
        if (state.view === "settings" && state.settingsMenuOpen) {
          state.settingsMenuOpen = false;
          updateShell();
          return;
        }
        state.settingsMenuOpen = true;
      } else {
        state.settingsMenuOpen = false;
      }
      if (target.dataset.view === "write") {
        openDiaryComposer();
        return;
      }
      if (target.dataset.view === "diary") {
        setView("diary", { compose: false });
        return;
      }
      setView(target.dataset.view);
      return;
    }
    if (target.dataset.openWrite !== undefined) {
      openDiaryComposer();
      return;
    }
    if (target.dataset.closeWrite !== undefined) {
      closeDiaryComposer();
      return;
    }
    if (target.dataset.date) {
      selectDiary(target.dataset.date, target.dataset.notebookId || "");
      return;
    }
    if (target.dataset.editDate) {
      openDiaryComposer(target.dataset.editDate, target.dataset.notebookId || "");
      return;
    }
    if (target.dataset.searchQuery) {
      setView("search", { query: target.dataset.searchQuery });
      return;
    }
    if (target.dataset.impressionName) {
      selectImpression(target.dataset.impressionName);
      return;
    }
    if (target.dataset.newImpression !== undefined) {
      newImpression();
      return;
    }
    if (target.dataset.mediaOpen) {
      if (Date.now() < state.mediaSuppressClickUntil && !target.closest("button, .button")) return;
      openMediaDetail(target.dataset.mediaOpen);
      return;
    }
    if (target.dataset.mediaClose !== undefined) {
      closeMediaDetail();
      return;
    }
    if (target.dataset.mediaNoteEdit !== undefined) {
      state.mediaNoteEditing = true;
      state.toast = "备注可以编辑了，写好后点保存备注";
      renderGlobalDialogs();
      updateShell();
      clearToastSoon();
      return;
    }
    if (target.dataset.mediaFolderCreate !== undefined) {
      openMediaFolderModal();
      return;
    }
    if (target.dataset.mediaFolderEdit) {
      openMediaFolderModal(target.dataset.mediaFolderEdit);
      return;
    }
    if (target.dataset.mediaFolderModalClose !== undefined) {
      closeMediaFolderModal();
      return;
    }
    if (target.dataset.mediaTrash) {
      trashMediaItem(target.dataset.mediaTrash, target.dataset.mediaId || target.dataset.mediaTrashId || "");
      return;
    }
    if (target.dataset.mediaRestore) {
      restoreMediaItem(target.dataset.mediaRestore, target.dataset.mediaId || target.dataset.mediaRestoreId || "");
      return;
    }
    if (target.dataset.mediaDelete) {
      deleteMediaItem(target.dataset.mediaDelete, target.dataset.mediaId || "");
      return;
    }
    if (target.dataset.mediaOpenOriginal !== undefined) {
      openMediaOriginal();
      return;
    }
    if (target.dataset.mediaToggleFloat !== undefined) {
      toggleMediaFloat();
      return;
    }
    if (target.dataset.mediaMode) {
      state.mediaMode = target.dataset.mediaMode;
      state.activeMediaFolderId = "";
      clearExpandedMediaFolders();
      renderMedia();
      return;
    }
    if (target.dataset.mediaFolderCollapse) {
      if (Date.now() < state.mediaSuppressClickUntil) return;
      captureMediaFloatPositions();
      collapseExpandedMediaFolder(target.dataset.mediaFolderCollapse);
      renderMedia();
      return;
    }
    if (target.dataset.mediaFolderExpand) {
      if (Date.now() < state.mediaSuppressClickUntil) return;
      captureMediaFloatPositions();
      toggleExpandedMediaFolder(target.dataset.mediaFolderExpand);
      renderMedia();
      return;
    }
    if (target.dataset.mediaFolderOpen) {
      if (Date.now() < state.mediaSuppressClickUntil) return;
      if (state.mediaFloating && !isMediaFolderExpanded(target.dataset.mediaFolderOpen)) {
        captureMediaFloatPositions();
        expandMediaFolder(target.dataset.mediaFolderOpen);
        renderMedia();
        return;
      }
      openMediaFolder(target.dataset.mediaFolderOpen);
      return;
    }
    if (target.dataset.mediaFolderClose !== undefined) {
      closeMediaFolder();
      return;
    }
    if (target.dataset.memoNew !== undefined) {
      newMemo();
      return;
    }
    if (target.dataset.memoSelect) {
      if (Date.now() < state.memoSuppressClickUntil) return;
      selectMemo(target.dataset.memoSelect);
      return;
    }
    if (target.dataset.memoDetailBack !== undefined) {
      closeMemoDetail();
      return;
    }
    if (target.dataset.memoPin) {
      updateMemoFlag(target.dataset.memoPin, "pinned", target.dataset.memoPinValue !== "true");
      return;
    }
    if (target.dataset.memoArchive) {
      updateMemoFlag(target.dataset.memoArchive, "archived", target.dataset.memoArchiveValue !== "true");
      return;
    }
    if (target.dataset.memoDelete) {
      deleteMemo(target.dataset.memoDelete);
      return;
    }
    if (target.dataset.memoReveal !== undefined) {
      state.memoRevealSensitive = !state.memoRevealSensitive;
      syncRouteForState("memos", true);
      renderMemos();
      return;
    }
    if (target.dataset.memoArchivedToggle !== undefined) {
      state.memoIncludeArchived = !state.memoIncludeArchived;
      state.selectedMemoId = "";
      syncRouteForState("memos", true);
      renderMemos();
      return;
    }
    if (target.dataset.settingsSection) {
      switchSettingsSection(target.dataset.settingsSection);
      return;
    }
    if (target.dataset.moduleFilter) {
      setModuleFilter(target.dataset.moduleFilter);
      return;
    }
    if (target.dataset.moduleInstallOpen !== undefined) {
      openModuleInstallDialog();
      return;
    }
    if (target.dataset.moduleInstallClose !== undefined) {
      closeModuleInstallDialog();
      return;
    }
    if (target.dataset.versionCheck !== undefined) {
      checkVersion();
      return;
    }
    if (target.dataset.versionUpdate !== undefined) {
      updateVersion();
      return;
    }
    if (target.dataset.moduleSettings) {
      openModuleSettings(target.dataset.moduleSettings);
      return;
    }
    if (target.dataset.styleSelect) {
      selectFrontendStyle(target.dataset.styleSelect, target);
      return;
    }
    if (target.dataset.onboardingReplay !== undefined) {
      void openOnboarding(0);
      return;
    }
    if (target.dataset.onboardingNext !== undefined) {
      void advanceOnboarding();
      return;
    }
    if (target.dataset.onboardingPrev !== undefined) {
      state.onboardingStep = Math.max(0, state.onboardingStep - 1);
      void updateOnboardingDialog({ animate: true });
      return;
    }
    if (target.dataset.onboardingFinish !== undefined || target.dataset.onboardingClose !== undefined) {
      finishOnboarding();
      return;
    }
    if (target.dataset.onboardingAdvance !== undefined) {
      void advanceOnboarding();
      return;
    }
    if (target.dataset.notebookAdd !== undefined) {
      addNotebookDraft();
      return;
    }
    if (target.dataset.notebookDelete) {
      deleteNotebookRow(target.dataset.notebookDelete);
      return;
    }
    if (target.dataset.t2iOpen !== undefined) {
      openT2iTemplateDialog();
      return;
    }
    if (target.dataset.t2iClose !== undefined) {
      closeT2iTemplateDialog();
      return;
    }
    if (target.dataset.t2iSelect) {
      selectT2iTemplate(target.dataset.t2iSelect);
      return;
    }
    if (target.dataset.t2iCustomToggle !== undefined) {
      state.t2iCustomOpen = !state.t2iCustomOpen;
      renderGlobalDialogs();
      return;
    }
    if (target.dataset.t2iCustomSave !== undefined) {
      saveCustomT2iTemplate();
      return;
    }
    if (target.dataset.settingsBack !== undefined) {
      closeModuleSettings();
      return;
    }
    if (target.dataset.moduleUninstall) {
      void uninstallModule(target.dataset.moduleUninstall, target.dataset.keepData === "1", target);
      return;
    }
  },
  true
);

document.addEventListener("pointerdown", startMemoDrag, true);
document.addEventListener("pointermove", moveMemoDrag, true);
document.addEventListener("pointerup", endMemoDrag, true);
document.addEventListener("pointercancel", endMemoDrag, true);

document.addEventListener("change", (event) => {
  const moduleTarget = event.target.closest("[data-module-toggle]");
  if (moduleTarget) {
    saveModuleToggle(moduleTarget);
    return;
  }
  const navTarget = event.target.closest("[data-module-nav-toggle]");
  if (navTarget) {
    saveModuleNavVisibility(navTarget.dataset.moduleNavToggle, navTarget.checked);
    return;
  }
  const exportTarget = event.target.closest('input[name="package_type"]');
  if (exportTarget) {
    syncExportChoices(exportTarget);
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="create-media-folder"], [data-action="save-media-note"], [data-action="install-module-from-link"], [data-action="save-memo"]');
  if (!form) return;
  if (form.dataset.action === "create-media-folder") createMediaFolder(event);
  if (form.dataset.action === "save-media-note") saveMediaNote(event);
  if (form.dataset.action === "install-module-from-link") installModuleFromLink(event);
  if (form.dataset.action === "save-memo") saveMemo(event);
});

async function loadView() {
  try {
    ensureShell();
    await loadBootstrap();
    if (state.view === "media" && !isMediaEnabled()) {
      state.view = "dashboard";
      syncRouteForState("dashboard", true);
    }
    if (state.view === "impressions" && !isImpressionsEnabled()) {
      state.view = "dashboard";
      syncRouteForState("dashboard", true);
    }
    if (state.view === "memos" && !isMemosEnabled()) {
      state.view = "dashboard";
      syncRouteForState("dashboard", true);
    }
    if (isModuleView() && !moduleNavEntry(moduleIdFromView())) {
      // 模块被停用、卸载或声明校验失败，入口已经不存在了。
      state.error = `模块 ${moduleIdFromView()} 没有可用页面，可能已停用或被卸载。`;
      state.view = "dashboard";
      syncRouteForState("dashboard", true);
    }
    if (state.view === "dashboard") renderDashboard();
    if (state.view === "diary") await renderDiary();
    if (state.view === "search") await renderSearch();
    if (state.view === "impressions") await renderImpressions();
    if (state.view === "media") await renderMedia();
    if (state.view === "memos") await renderMemos();
    if (state.view === "settings") await renderSettings();
    if (isModuleView()) await renderModulePage(moduleIdFromView());
    updateShell();
    renderGlobalDialogs();
  } catch (err) {
    state.error = err.message;
    updateShell();
    const target = panel(state.view);
    if (target) target.innerHTML = `<div class="loading">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// 自定义模块页面加载
// ---------------------------------------------------------------------------

/** 交给模块 mount() 的能力包。模块不需要自己处理鉴权、bridge 转发和主题。 */
function moduleContext(entry) {
  const moduleId = entry.id;
  const base = `/api/ui/modules/${encodeURIComponent(moduleId)}`;
  const storeBase = `${base}/store`;
  return {
    moduleId,
    assetBase: entry.asset_base || "",
    hasStore: Boolean(entry.store),
    insidePluginPage: Boolean(PLUGIN_PAGE_BRIDGE),
    assetUrl(relativePath = "") {
      const clean = String(relativePath || "").replace(/^\/+/, "");
      return clean ? `${entry.asset_base}/${clean}` : entry.asset_base || "";
    },
    async request(path, options = {}) {
      const clean = String(path || "").replace(/^\/+/, "");
      return api(clean ? `${base}/${clean}` : base, options);
    },
    store: {
      async keys() {
        const result = await api(storeBase);
        return result?.keys || [];
      },
      async get(key, fallback = null) {
        const result = await api(`${storeBase}?key=${encodeURIComponent(key)}`);
        return result?.exists ? result.value : fallback;
      },
      async set(key, value) {
        return api(storeBase, { method: "POST", body: JSON.stringify({ key, value }) });
      },
      async remove(key) {
        return api(`${storeBase}?key=${encodeURIComponent(key)}`, { method: "DELETE" });
      },
    },
    notify(message) {
      state.toast = String(message || "");
      updateShell();
      clearToastSoon();
    },
    reportError(message) {
      state.error = String(message || "");
      updateShell();
    },
    confirm(message) {
      return confirmAction(String(message || "确认执行这个操作吗？"));
    },
    escapeHtml,
    icon: iconImg,
    goHome() {
      setView("dashboard");
    },
    async refresh() {
      state.bootstrap = null;
      await loadView();
    },
  };
}

/**
 * 内置插件页不能直接请求小窝服务的受保护资源。入口脚本经 bridge 取回后，
 * 从内存 Blob 导入；独立 WebUI 保持浏览器原生的直接导入。
 */
async function importModulePage(entry) {
  if (!PLUGIN_PAGE_BRIDGE) return import(/* webpackIgnore: true */ entry.page_url);

  const source = await pluginApi(entry.page_url);
  if (typeof source !== "string") {
    throw new Error("模块入口未返回 JavaScript 文本。");
  }

  const sourceUrl = pluginWebuiBase ? `${pluginWebuiBase}${entry.page_url}` : entry.page_url;
  const blob = new Blob([`${source}\n//# sourceURL=${sourceUrl}\n`], { type: "text/javascript" });
  const blobUrl = URL.createObjectURL(blob);
  try {
    return await import(/* webpackIgnore: true */ blobUrl);
  } finally {
    URL.revokeObjectURL(blobUrl);
  }
}

async function renderModulePage(moduleId) {
  const entry = moduleNavEntry(moduleId);
  const target = panel(moduleViewFor(moduleId));
  if (!entry || !target) return;

  let mounted = state.moduleMounts[moduleId];
  if (mounted && mounted.pageUrl !== entry.page_url) {
    // 模块升级换了入口文件，旧实例必须先退场。
    await unmountModulePage(moduleId);
    mounted = null;
  }
  if (mounted) {
    if (typeof mounted.update === "function") {
      try {
        await mounted.update();
      } catch (err) {
        moduleFailure(target, entry, err);
      }
    }
    return;
  }

  target.innerHTML = `<div class="loading">正在加载 ${escapeHtml(entry.label || moduleId)}…</div>`;
  try {
    const namespace = await importModulePage(entry);
    const exportName = entry.page_export || "mount";
    const mount = typeof namespace[exportName] === "function" ? namespace[exportName] : namespace.default;
    if (typeof mount !== "function") {
      throw new Error(`页面没有导出可调用的 ${exportName}()`);
    }
    target.innerHTML = "";
    const context = moduleContext(entry);
    const handle = (await mount(target, context)) || {};
    state.moduleMounts[moduleId] = {
      pageUrl: entry.page_url,
      unmount: typeof handle.unmount === "function" ? handle.unmount : null,
      update: typeof handle.update === "function" ? handle.update : null,
    };
  } catch (err) {
    moduleFailure(target, entry, err);
  }
}

function moduleFailure(target, entry, err) {
  const label = entry.label || entry.id;
  const detail = err?.message || String(err);
  state.moduleMountError = `${entry.id}: ${detail}`;
  target.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>${escapeHtml(label)} 加载失败</h2></div>
      <div class="card-body">
        <p class="muted">这个模块的页面没能运行起来，小窝其余部分不受影响。</p>
        <pre class="module-error-detail">${escapeHtml(detail)}</pre>
        <p class="muted">请检查模块的 page.js 是否导出了 mount()，以及控制台里的具体报错。</p>
      </div>
    </section>
  `;
}

async function unmountModulePage(moduleId) {
  const mounted = state.moduleMounts[moduleId];
  if (!mounted) return;
  delete state.moduleMounts[moduleId];
  if (typeof mounted.unmount === "function") {
    try {
      await mounted.unmount();
    } catch (_) {}
  }
  const node = document.getElementById(`view-module-${moduleId}`);
  if (node) node.remove();
}

/** 停用或卸载模块后，清掉已经不该存在的面板与实例。 */
async function pruneModuleMounts() {
  const alive = new Set(moduleNavEntries().map((entry) => entry.id));
  for (const moduleId of Object.keys(state.moduleMounts)) {
    if (!alive.has(moduleId)) await unmountModulePage(moduleId);
  }
  document.querySelectorAll("[data-module-host]").forEach((node) => {
    const moduleId = node.dataset.moduleHost;
    if (moduleId && !alive.has(moduleId) && !state.moduleMounts[moduleId]) node.remove();
  });
}

function renderDashboard() {
  const target = panel("dashboard");
  const stats = state.bootstrap.stats;
  const recent = state.bootstrap.recent_entries || [];
  const siteTitle = currentSiteTitle();
  const siteSubtitle = currentSiteSubtitle();
  target.innerHTML = `
    <section class="home-hero">
      <div class="home-hero-copy">
        <h1>${escapeHtml(siteTitle)}</h1>
        <p class="home-lead">${escapeHtml(siteSubtitle)}</p>
        <div class="home-actions">
          <button class="button primary" data-open-write type="button">写日记</button>
          <button class="button" data-view="diary" type="button">看日记</button>
        </div>
      </div>
      <div class="home-status">
        <div class="home-stat"><span>日记</span><strong>${stats.entries}</strong></div>
        <div class="home-stat"><span>媒体</span><strong>${stats.media}</strong></div>
        <div class="home-stat"><span>人物印象</span><strong>${stats.people}</strong></div>
      </div>
    </section>
    <section class="home-grid">
      <article class="card">
        <div class="card-head"><h2>最近日记</h2><button class="text-button" data-view="diary" type="button">查看全部</button></div>
        <div class="list">${recent.map(entryRow).join("") || `<div class="card-body muted">还没有日记。</div>`}</div>
      </article>
      <article class="card home-side-card">
        <div class="card-head"><h2>小窝状态</h2></div>
        <div class="card-body home-quiet-list">
          <p><strong>归档</strong><span>日记按年月日保存，保留历史快照。</span></p>
          <p><strong>印象</strong><span>人物印象独立管理；日记后是否更新由策略和 bot 判断。</span></p>
          <p><strong>个性化</strong><span>外观、模块和拓展包都放在独立目录中。</span></p>
        </div>
      </article>
    </section>
  `;
}

function notebookOptions() {
  const items = state.notebooks?.length ? state.notebooks : state.bootstrap?.notebooks || [];
  return items.map((item) => ({ ...item, id: item.id || item.notebook_id || "default", name: item.name || item.id || "默认日记本" }));
}

function notebookLabel(notebookId = "") {
  const id = notebookId || "default";
  return notebookOptions().find((item) => item.id === id)?.name || (id === "default" ? "默认日记本" : id);
}

function findDiaryEntry(date, notebookId = "") {
  return state.diary.items.find((entry) => entry.date === date && (!notebookId || (entry.notebook_id || "default") === notebookId));
}

function entryRow(entry) {
  const notebookId = entry.notebook_id || "default";
  const active = state.diary.selected?.date === entry.date && (state.diary.selected?.notebook_id || "default") === notebookId;
  return `
    <button class="row ${active ? "active" : ""}" data-date="${escapeHtml(entry.date)}" data-notebook-id="${escapeHtml(notebookId)}" type="button">
      <span>${escapeHtml(entry.date)} · ${escapeHtml(notebookLabel(notebookId))}</span>
      <strong>${escapeHtml(entry.title || entry.date)}</strong>
    </button>
  `;
}

async function ensureDiaryList(force = false) {
  const notebookId = state.diary.filters.notebook_id || "";
  if (state.diary.loaded && state.diary.loadedNotebookId === notebookId && !force) return;
  if (!notebookId && state.diary.loadedNotebookId && !force) force = true;
  const payload = await api(`/api/ui/diary${notebookId ? `?notebook_id=${encodeURIComponent(notebookId)}` : ""}`);
  state.diary.items = payload.items;
  state.diary.archive = payload.archive;
  state.notebooks = payload.notebooks || state.notebooks || [];
  state.diary.loaded = true;
  state.diary.loadedNotebookId = notebookId;
}

async function loadDiaryEntry(date, notebookId = "") {
  await ensureDiaryList();
  const visibleItems = filteredDiaryItems();
  const candidateDate = date || state.diary.selected?.date || state.selectedDate;
  const candidateNotebook = notebookId || state.diary.selected?.notebook_id || state.diary.filters.notebook_id || "";
  const selectedEntry = candidateDate
    ? visibleItems.find((entry) => entry.date === candidateDate && (!candidateNotebook || (entry.notebook_id || "default") === candidateNotebook)) || visibleItems[0]
    : visibleItems[0];
  const selectedDate = selectedEntry?.date || "";
  const selectedNotebook = selectedEntry?.notebook_id || candidateNotebook || "default";
  state.selectedDate = selectedDate || "";
  state.diary.selected = selectedDate ? await api(`/api/ui/diary/${encodeURIComponent(selectedDate)}?notebook_id=${encodeURIComponent(selectedNotebook)}`) : null;
}

function filteredDiaryItems() {
  const filters = state.diary.filters;
  return state.diary.items.filter((entry) => {
    if (filters.notebook_id && (entry.notebook_id || "default") !== filters.notebook_id) return false;
    if (filters.date) return entry.date === filters.date;
    if (filters.month) return entry.date.startsWith(filters.month);
    if (filters.year) return entry.date.startsWith(filters.year);
    return true;
  });
}

function allDiaryDates() {
  return state.diary.items.map((entry) => entry.date).filter(Boolean);
}

function diaryFilterPrefix() {
  const filters = state.diary.filters;
  return filters.date || filters.month || filters.year || filters.notebook_id || "";
}

function clearDiaryFilters() {
  state.diary.filters = { notebook_id: "", year: "", month: "", date: "" };
}

async function renderDiary() {
  await ensureDiaryList();
  if (state.diary.composerOpen) {
    const composeDate = state.diary.composerDate || state.editingDate || localDateString();
    const existing = findDiaryEntry(composeDate, state.diary.filters.notebook_id || "default");
    if (existing && !state.editingDate) {
      state.editingDate = composeDate;
      state.notice = state.notice || "这天已有日记，已切换为编辑。";
    }
    if (state.editingDate) {
      await loadDiaryEntry(state.editingDate, existing?.notebook_id || state.diary.filters.notebook_id || "default");
    } else {
      await loadDiaryEntry(state.selectedDate);
    }
  } else {
    await loadDiaryEntry(state.selectedDate);
  }
  if (!state.rendered.has("diary")) {
    panel("diary").innerHTML = `
      ${pageHead("", "日记", `<button class="button primary" data-open-write type="button">写一篇</button>`)}
      <section class="diary-layout">
        <aside class="card diary-list">
          <div id="diary-archive"></div>
          <div class="list" id="diary-list"></div>
        </aside>
        <div class="diary-main">
          <section class="card diary-compose" id="diary-compose" hidden></section>
          <article class="card diary-article" id="diary-article"></article>
        </div>
      </section>
    `;
    state.rendered.add("diary");
  }
  updateDiaryArchive();
  updateDiaryList();
  await updateDiaryComposer();
  updateDiaryArticle({ preserveScroll: false });
}

async function selectDiary(date, notebookId = "") {
  if (state.view !== "diary") {
    clearDiaryFilters();
    await setView("diary", { date, notebook_id: notebookId });
    return;
  }
  state.error = "";
  state.notice = "";
  const article = document.getElementById("diary-article");
  const previousScroll = article ? article.scrollTop : 0;
  await loadDiaryEntry(date, notebookId);
  syncRouteForState("diary");
  updateDiaryList();
  updateDiaryArchive();
  updateDiaryArticle({ preserveScroll: true, previousScroll });
  updateShell();
}

function updateDiaryArchive() {
  const target = document.getElementById("diary-archive");
  if (!target) return;
  const filters = state.diary.filters;
  const dates = allDiaryDates();
  const notebooks = notebookOptions();
  const years = [...new Set(dates.map((date) => date.slice(0, 4)))];
  const months = [...new Set(dates.filter((date) => !filters.year || date.startsWith(filters.year)).map((date) => date.slice(0, 7)))];
  const dateOptions = dates.filter((date) => {
    if (filters.month) return date.startsWith(filters.month);
    if (filters.year) return date.startsWith(filters.year);
    return true;
  });
  const notebookSelect = notebooks.map((item) => `<option value="${escapeHtml(item.id)}" ${filters.notebook_id === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  const yearSelect = years.map((year) => `<option value="${year}" ${filters.year === year ? "selected" : ""}>${year}</option>`).join("");
  const monthSelect = months.map((month) => `<option value="${month}" ${filters.month === month ? "selected" : ""}>${month}</option>`).join("");
  const dateSelect = dateOptions.map((date) => `<option value="${date}" ${filters.date === date ? "selected" : ""}>${date}</option>`).join("");
  target.innerHTML = `
    <div class="archive-picker">
      <label class="archive-field archive-book-field"><span>日记本 / 群组</span><select data-filter-level="notebook"><option value="">全部日记本</option>${notebookSelect}</select></label>
      <div class="archive-date-strip">
        <label class="archive-field"><span>年</span><select data-filter-level="year"><option value="">全部</option>${yearSelect}</select></label>
        <label class="archive-field"><span>月</span><select data-filter-level="month"><option value="">全部</option>${monthSelect}</select></label>
        <label class="archive-field"><span>日期</span><select data-filter-level="date"><option value="">全部</option>${dateSelect}</select></label>
      </div>
    </div>
  `;
  target.querySelectorAll("[data-filter-level]").forEach((node) => node.addEventListener("change", applyDiaryFilterChange));
}

function updateDiaryList() {
  const target = document.getElementById("diary-list");
  if (!target) return;
  const items = filteredDiaryItems();
  const prefix = diaryFilterPrefix();
  target.innerHTML = items.map(entryRow).join("") || `<div class="card-body muted">${prefix ? "这个范围里没有日记。" : "还没有日记。"}</div>`;
}

async function applyDiaryFilterChange(event) {
  const level = event.currentTarget.dataset.filterLevel;
  const value = event.currentTarget.value || "";
  const filters = state.diary.filters;
  if (level === "notebook") {
    filters.notebook_id = value;
    filters.date = "";
    state.diary.loaded = false;
  }
  if (level === "year") {
    filters.year = value;
    if (!value || !filters.month.startsWith(value)) filters.month = "";
    if (!value || !filters.date.startsWith(value)) filters.date = "";
  }
  if (level === "month") {
    filters.month = value;
    filters.year = value ? value.slice(0, 4) : filters.year;
    if (!value || !filters.date.startsWith(value)) filters.date = "";
  }
  if (level === "date") {
    filters.date = value;
    if (value) {
      filters.year = value.slice(0, 4);
      filters.month = value.slice(0, 7);
    }
  }
  await applyDiaryFilters();
}

async function applyDiaryFilters() {
  await ensureDiaryList(true);
  await loadDiaryEntry("");
  syncRouteForState("diary");
  updateDiaryArchive();
  updateDiaryList();
  updateDiaryArticle({ preserveScroll: false });
  updateShell();
}

function updateDiaryArticle({ preserveScroll = false, previousScroll = 0 } = {}) {
  const target = document.getElementById("diary-article");
  if (!target) return;
  const selected = state.diary.selected;
  target.innerHTML = selected
    ? `<div class="card-head">
        <div><p class="eyebrow">${escapeHtml(selected.date)} · ${escapeHtml(notebookLabel(selected.notebook_id))}</p><h2>${escapeHtml(selected.title)}</h2></div>
        <div class="actions"><button class="button" data-edit-date="${escapeHtml(selected.date)}" data-notebook-id="${escapeHtml(selected.notebook_id || "default")}" type="button">编辑</button><button class="danger" data-delete="${escapeHtml(selected.date)}" data-notebook-id="${escapeHtml(selected.notebook_id || "default")}">删除</button></div>
      </div>
      <div class="card-body">
        <div class="meta">重要度 ${selected.importance} · ${escapeHtml(selected.source || "")}</div>
        <div class="chips">${[...(selected.mood || []), ...(selected.tags || []), ...(selected.people || [])].map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>
        <div class="article-body">${escapeHtml(selected.body)}</div>
        ${(selected.media_refs || []).length ? `<div class="media-refs"><h3>媒体引用</h3>${selected.media_refs.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>` : ""}
      </div>`
    : `<div class="card-body muted">选择一篇日记。</div>`;
  bindDiaryArticleActions();
  if (preserveScroll) target.scrollTop = Math.min(previousScroll, target.scrollHeight);
}

async function openDiaryComposer(date = "", notebookId = "") {
  await ensureDiaryList();
  const targetDate = date || localDateString();
  const targetNotebook = notebookId || state.diary.selected?.notebook_id || state.diary.filters.notebook_id || "default";
  const existing = findDiaryEntry(targetDate, targetNotebook);
  state.view = "diary";
  state.diary.composerOpen = true;
  state.diary.composerDate = targetDate;
  state.diary.filters.notebook_id = targetNotebook;
  state.editingDate = existing ? targetDate : date || "";
  if (existing) {
    state.diary.filters = { notebook_id: targetNotebook, year: targetDate.slice(0, 4), month: targetDate.slice(0, 7), date: targetDate };
    await loadDiaryEntry(targetDate, targetNotebook);
    state.notice = date ? "" : "这天已有日记，已切换为编辑。";
  }
  syncRouteForState("diary");
  await renderDiary();
  updateShell();
}

function closeDiaryComposer() {
  state.diary.composerOpen = false;
  state.editingDate = "";
  if (state.diary.filters.date) state.selectedDate = state.diary.filters.date;
  syncRouteForState("diary");
  updateDiaryComposer();
  updateShell();
}

async function updateDiaryComposer() {
  const target = document.getElementById("diary-compose");
  if (!target) return;
  if (!state.diary.composerOpen) {
    target.hidden = true;
    target.innerHTML = "";
    return;
  }
  target.hidden = false;
  const date = state.diary.composerDate || state.editingDate || localDateString();
  const selectedNotebook = state.diary.selected?.notebook_id || state.diary.filters.notebook_id || "default";
  const selected = state.editingDate && state.diary.selected?.date === state.editingDate && (state.diary.selected?.notebook_id || "default") === selectedNotebook ? state.diary.selected : null;
  const notebookSelect = notebookOptions().map((item) => `<option value="${escapeHtml(item.id)}" ${(selected?.notebook_id || selectedNotebook) === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  target.innerHTML = `
    <div class="card-head compact-head">
      <div><h2>${selected ? "编辑日记" : "写一篇"}</h2></div>
      <button class="text-button" data-close-write type="button">收起</button>
    </div>
    <form class="card-body form diary-compose-form" data-action="write-diary">
      <div class="form-grid compact">
        <label>日记本/群组<select name="notebook_id">${notebookSelect}</select></label>
        <label>日期<input name="date" type="date" value="${escapeHtml(date)}" required></label>
        <label>标题<input name="title" value="${escapeHtml(selected?.title || "")}" placeholder="给这天起一个真正的标题"></label>
        <label>情绪<input name="mood" value="${escapeHtml((selected?.mood || []).join(","))}"></label>
        <label>标签<input name="tags" value="${escapeHtml((selected?.tags || []).join(","))}"></label>
        <label>人物<input name="people" value="${escapeHtml((selected?.people || []).join(","))}"></label>
        <label>重要度<input name="importance" type="number" min="1" max="5" value="${selected?.importance || 3}"></label>
      </div>
      <label>正文<textarea name="body" required>${escapeHtml(selected?.body || "")}</textarea></label>
      <label>媒体引用<textarea name="media_refs" placeholder="每行一个图片、语音或附件引用">${escapeHtml((selected?.media_refs || []).join("\n"))}</textarea></label>
      <div class="actions"><button class="primary">保存日记</button></div>
    </form>
  `;
  target.querySelector('[data-action="write-diary"]').addEventListener("submit", saveDiary);
  target.querySelector('input[name="date"]').addEventListener("change", handleDiaryComposeDateChange);
  target.querySelector('select[name="notebook_id"]')?.addEventListener("change", handleDiaryComposeDateChange);
}

async function handleDiaryComposeDateChange(event) {
  const form = event.currentTarget.closest("form");
  const data = new FormData(form);
  const nextDate = data.get("date");
  const nextNotebook = data.get("notebook_id") || "default";
  state.diary.composerDate = nextDate;
  state.diary.filters.notebook_id = nextNotebook;
  const existing = findDiaryEntry(nextDate, nextNotebook);
  if (existing) {
    state.diary.filters = { notebook_id: nextNotebook, year: nextDate.slice(0, 4), month: nextDate.slice(0, 7), date: nextDate };
    state.editingDate = nextDate;
    await loadDiaryEntry(nextDate, nextNotebook);
    state.notice = "这天已有日记，已切换为编辑。";
  } else {
    state.editingDate = "";
    state.notice = "";
  }
  await updateDiaryComposer();
  updateDiaryList();
  updateShell();
}

function bindDiaryArticleActions() {
  document.querySelector("[data-delete]")?.addEventListener("click", async (event) => {
    const date = event.currentTarget.dataset.delete;
    const notebookId = event.currentTarget.dataset.notebookId || state.diary.selected?.notebook_id || "default";
    if (!(await confirmAction(`删除 ${date} 的日记？`))) return;
    await api(`/api/ui/diary/${encodeURIComponent(date)}?notebook_id=${encodeURIComponent(notebookId)}`, { method: "DELETE" });
    state.diary.loaded = false;
    state.diary.selected = null;
    state.selectedDate = "";
    state.notice = "日记已删除。";
    await renderDiary();
    updateShell();
  });
}

async function saveDiary(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = {
    notebook_id: form.get("notebook_id") || state.diary.filters.notebook_id || "default",
    date: form.get("date"),
    title: form.get("title"),
    body: form.get("body"),
    mood: splitWords(form.get("mood")),
    tags: splitWords(form.get("tags")),
    people: splitWords(form.get("people")),
    media_refs: String(form.get("media_refs") || "").split(/\n+/).map((item) => item.trim()).filter(Boolean),
    importance: Number(form.get("importance") || 3),
    source: "admin",
    reason: "web_app_update",
  };
  const result = await api("/api/ui/diary", { method: "POST", body: JSON.stringify(payload) });
  state.diary.loaded = false;
  state.diary.composerOpen = false;
  state.diary.composerDate = result.entry.date;
  state.diary.filters = { notebook_id: result.entry.notebook_id || payload.notebook_id || "default", year: result.entry.date.slice(0, 4), month: result.entry.date.slice(0, 7), date: result.entry.date };
  state.editingDate = "";
  state.notice = "日记已保存。";
  await setView("diary", { date: result.entry.date, notebook_id: result.entry.notebook_id || payload.notebook_id || "default", keepNotice: true });
}

async function loadSearch(query = "") {
  state.search.query = query || "";
  if (!state.search.query) {
    state.search.results = [];
    return;
  }
  const notebookId = state.search.notebook_id || "";
  const payload = await api(`/api/ui/search?q=${encodeURIComponent(state.search.query)}&top_k=8&notebook_id=${encodeURIComponent(notebookId)}`);
  state.search.results = payload.results;
  state.search.backend = payload.search.backend;
  state.notebooks = payload.notebooks || state.notebooks || [];
}

async function renderSearch() {
  await loadSearch(state.search.query);
  const scopeOptions = notebookOptions().map((item) => `<option value="${escapeHtml(item.id)}" ${state.search.notebook_id === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  panel("search").innerHTML = `
    ${pageHead("", "搜索")}
    <section class="card">
      <div class="card-body">
        <form class="searchbar" data-action="search">
          <label class="search-scope">范围<select name="notebook_id"><option value="">全部日记本</option>${scopeOptions}</select></label>
          <input name="q" value="${escapeHtml(state.search.query)}" placeholder="关键词、人物、事件或情绪" />
          <button class="primary">搜索</button>
        </form>
      </div>
      <div class="list">
        ${
          state.search.results.length
            ? state.search.results.map((item) => `<button class="row" data-date="${escapeHtml(item.date)}" data-notebook-id="${escapeHtml(item.notebook_id || "default")}" type="button"><span>${escapeHtml(item.date)} · ${escapeHtml(item.notebook_name || notebookLabel(item.notebook_id))}</span><strong>${escapeHtml(item.title)}</strong><em>${escapeHtml(item.snippet || "")}</em></button>`).join("")
            : `<div class="card-body muted">暂无结果。</div>`
        }
      </div>
    </section>
  `;
  panel("search").querySelector('[data-action="search"]').addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const q = form.get("q");
    state.search.notebook_id = form.get("notebook_id") || "";
    setView("search", { query: q });
  });
}

function bindQuickSearch() {
  panel("dashboard")?.querySelector('[data-action="quick-search"]')?.addEventListener("submit", (event) => {
    event.preventDefault();
    const q = new FormData(event.currentTarget).get("q");
    setView("search", { query: q });
  });
}

async function renderImpressions() {
  state.impressions = (await api("/api/ui/impressions")).items;
  if (state.selectedImpressionName) {
    state.selectedImpression = state.impressions.find((item) => item.name === state.selectedImpressionName) || null;
  } else {
    state.selectedImpression = state.impressions[0] || null;
    state.selectedImpressionName = state.selectedImpression?.name || "";
  }
  panel("impressions").innerHTML = `
    ${pageHead("", "人物印象", `<button class="button primary" data-new-impression type="button">新建人物</button>`)}
    <section class="impression-layout">
      <aside class="card impression-list">
        <div class="card-head"><h2>人物</h2><span class="meta">${state.impressions.length} 条</span></div>
        <div class="list">
          ${state.impressions.map(renderImpressionRow).join("") || `<div class="card-body muted">还没有人物印象。</div>`}
        </div>
      </aside>
      <article class="card impression-detail" id="impression-detail">
        ${renderImpressionDetail(state.selectedImpression)}
      </article>
    </section>
  `;
  bindImpressionForm();
}

function renderImpressionRow(item) {
  return `
    <button class="row ${state.selectedImpressionName === item.name ? "active" : ""}" data-impression-name="${escapeHtml(item.name)}" type="button">
      <span>${escapeHtml(item.updated_at ? item.updated_at.slice(0, 10) : "")}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <em>${escapeHtml(item.identity || item.relationship || item.summary || "")}</em>
    </button>
  `;
}

function renderImpressionDetail(item) {
  const empty = {
    name: "",
    summary: "",
    identity: "",
    traits: [],
    hobbies: [],
    interests: [],
    preferences: [],
    relationship: "",
    affinity: 3,
    special_comment: "",
    evidence_dates: [],
    confidence: 3,
    notes: "",
    qq_id: "",
    group_impressions: [],
  };
  const value = item || empty;
  return `
    <div class="card-head">
      <div><h2>${escapeHtml(item?.name || "新建人物印象")}</h2></div>
      ${item ? `<button class="danger" data-delete-impression="${escapeHtml(item.name)}" type="button">删除</button>` : ""}
    </div>
    <form class="card-body form impression-form" data-action="save-impression">
      <input type="hidden" name="previous_name" value="${escapeHtml(item?.name || "")}">
      <div class="form-grid compact">
        <label>名字<input name="name" value="${escapeHtml(value.name)}" required></label>
        <label>身份<input name="identity" value="${escapeHtml(value.identity || "")}" placeholder="身份、关系定位或长期角色"></label>
        <label>QQ 标识<input name="qq_id" value="${escapeHtml(value.qq_id || "")}" placeholder="统一/挂载策略用于命中原人物档案"></label>
        <label>关系<input name="relationship" value="${escapeHtml(value.relationship || "")}" placeholder="与 bot、项目或管理员的关系"></label>
        <label>喜爱程度<input name="affinity" type="number" min="1" max="5" value="${value.affinity || 3}"></label>
        <label>可信度<input name="confidence" type="number" min="1" max="5" value="${value.confidence || 3}"></label>
        <label>证据日期<input name="evidence_dates" value="${escapeHtml((value.evidence_dates || []).join(","))}" placeholder="2026-05-18,2026-05-19"></label>
      </div>
      <label>总结评价<textarea name="summary" required placeholder="稳定、可追溯的长期总结，不要只写一句标签。">${escapeHtml(value.summary || "")}</textarea></label>
      <div class="form-grid compact">
        <label>性格特征<input name="traits" value="${escapeHtml((value.traits || []).join(","))}" placeholder="多个用逗号分隔"></label>
        <label>爱好<input name="hobbies" value="${escapeHtml((value.hobbies || []).join(","))}" placeholder="多个用逗号分隔"></label>
        <label>兴趣<input name="interests" value="${escapeHtml((value.interests || []).join(","))}" placeholder="多个用逗号分隔"></label>
        <label>偏好<input name="preferences" value="${escapeHtml((value.preferences || []).join(","))}" placeholder="相处方式、表达偏好、边界"></label>
      </div>
      <label>特殊点评<textarea name="special_comment" placeholder="bot 按自己人设写出的主观点评，可以保留语气，但必须有依据。">${escapeHtml(value.special_comment || "")}</textarea></label>
      <label>备注<textarea name="notes" placeholder="其他情报、待验证观察、长期边界。">${escapeHtml(value.notes || "")}</textarea></label>
      ${renderGroupImpressions(value.group_impressions || [])}
      <div class="notice soft">日记写完后如果开启人物印象自检，bot 应先读取旧印象，再根据新证据决定是否更新；统一人物时必须使用同一 QQ 标识更新原档，summary 应重写完整总体总结，不能按新昵称建立候选档案。</div>
      <div class="actions"><button class="primary">保存人物印象</button></div>
    </form>
  `;
}

function renderGroupImpressions(items = []) {
  if (!items.length) return "";
  return `
    <div class="notice soft impression-group-list">
      <strong>不同群聊中的印象</strong>
      ${items.map((item) => `
        <section>
          <h3>${escapeHtml(item.source_chat || "未知群聊")} · ${escapeHtml(item.name || "")}</h3>
          <p>${escapeHtml(item.summary || "")}</p>
          ${(item.evidence_dates || []).length ? `<em>证据：${escapeHtml((item.evidence_dates || []).join("、"))}</em>` : ""}
        </section>
      `).join("")}
    </div>
  `;
}

async function selectImpression(name) {
  state.selectedImpressionName = name || "";
  state.selectedImpression = state.impressions.find((item) => item.name === state.selectedImpressionName) || null;
  if (state.view !== "impressions") {
    await setView("impressions");
    return;
  }
  syncRouteForState("impressions");
  await renderImpressions();
  updateShell();
}

function newImpression() {
  state.selectedImpressionName = "";
  state.selectedImpression = null;
  syncRouteForState("impressions");
  panel("impressions").querySelectorAll("[data-impression-name]").forEach((node) => node.classList.remove("active"));
  const target = document.getElementById("impression-detail");
  if (target) {
    target.innerHTML = renderImpressionDetail(null);
    bindImpressionForm();
  }
}

function bindImpressionForm() {
  panel("impressions").querySelector('[data-action="save-impression"]')?.addEventListener("submit", saveImpression);
  panel("impressions").querySelector("[data-delete-impression]")?.addEventListener("click", deleteImpression);
}

async function saveImpression(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = {
    previous_name: form.get("previous_name"),
    name: form.get("name"),
    identity: form.get("identity"),
    summary: form.get("summary"),
    traits: splitWords(form.get("traits")),
    hobbies: splitWords(form.get("hobbies")),
    interests: splitWords(form.get("interests")),
    preferences: splitWords(form.get("preferences")),
    relationship: form.get("relationship"),
    affinity: Number(form.get("affinity") || 3),
    special_comment: form.get("special_comment"),
    evidence_dates: splitWords(form.get("evidence_dates")),
    confidence: Number(form.get("confidence") || 3),
    notes: form.get("notes"),
    qq_id: form.get("qq_id") || "",
  };
  const result = await api("/api/ui/impressions", { method: "POST", body: JSON.stringify(payload) });
  state.notice = "人物印象已保存。";
  state.selectedImpressionName = result.item.name;
  state.bootstrap = null;
  syncRouteForState("impressions");
  await renderImpressions();
  updateShell();
}

async function deleteImpression(event) {
  const name = event.currentTarget.dataset.deleteImpression;
  if (!(await confirmAction(`删除 ${name} 的人物印象？`))) return;
  await api(`/api/ui/impressions/${encodeURIComponent(name)}`, { method: "DELETE" });
  state.notice = "人物印象已删除。";
  state.selectedImpressionName = "";
  state.selectedImpression = null;
  state.bootstrap = null;
  syncRouteForState("impressions");
  await renderImpressions();
  updateShell();
}

async function loadMemos() {
  const params = new URLSearchParams({
    q: "",
    include_archived: state.memoIncludeArchived ? "true" : "false",
    reveal_sensitive: state.memoRevealSensitive ? "true" : "false",
  });
  const payload = await api(`/api/ui/memos?${params.toString()}`);
  state.memos = payload.items || [];
  state.memoSummary = payload.summary || { count: 0, pinned: 0, archived: 0, sensitive: 0 };
  if (state.selectedMemoId && !state.memos.some((item) => item.id === state.selectedMemoId)) {
    state.selectedMemoId = "";
  }
}

async function renderMemos() {
  await loadMemos();
  const selected = state.memos.find((item) => item.id === state.selectedMemoId) || null;
  const showEditor = state.memoEditorOpen || Boolean(selected);
  panel("memos").innerHTML = `
    <section class="memos-page ${showEditor ? "detail-mode" : "board-mode"}">
      <header class="topbar memos-topbar">
        <div class="page-title">
          <p>纸条板</p>
          <h1>备忘录</h1>
        </div>
        <div class="actions">
          <button class="button ghost" data-memo-reveal type="button">${state.memoRevealSensitive ? "隐藏敏感" : "显示敏感"}</button>
          <button class="button ghost" data-memo-archived-toggle type="button">${state.memoIncludeArchived ? "隐藏归档" : "显示归档"}</button>
          <button class="button primary" data-memo-new type="button">新纸条</button>
        </div>
      </header>
      ${memoBoardPage(showEditor, selected)}
    </section>
  `;
}

function memoStat(label, value) {
  return `<div class="memo-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function memoNoteCard(item) {
  const content = item.content_hidden ? "敏感内容已隐藏" : (item.content || "");
  const tags = (item.tags || []).slice(0, 5);
  const preview = memoPreviewText(content);
  return `
    <article class="memo-note ${item.pinned ? "pinned" : ""} ${item.archived ? "archived" : ""} ${item.sensitive ? "sensitive" : ""}" data-memo-id="${escapeHtml(item.id)}" data-memo-select="${escapeHtml(item.id)}" style="${memoPlacementStyle(item.id)}">
      <span class="memo-pin" aria-hidden="true"></span>
      <button class="memo-note-open ${state.selectedMemoId === item.id ? "active" : ""}" data-memo-select="${escapeHtml(item.id)}" type="button">
        <strong>${escapeHtml(item.title || "无标题纸条")}</strong>
        <em>${escapeHtml(preview)}</em>
      </button>
      <div class="memo-note-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="memo-note-actions">
        <button class="icon-button memo-action-button ${item.pinned ? "active" : ""}" data-memo-pin="${escapeHtml(item.id)}" data-memo-pin-value="${item.pinned ? "true" : "false"}" title="${item.pinned ? "取消置顶" : "置顶"}" type="button"><span class="memo-action-icon memo-action-pin" aria-hidden="true"></span></button>
        <button class="icon-button memo-action-button ${item.archived ? "active" : ""}" data-memo-archive="${escapeHtml(item.id)}" data-memo-archive-value="${item.archived ? "true" : "false"}" title="${item.archived ? "取消归档" : "归档"}" type="button"><span class="memo-action-icon memo-action-archive" aria-hidden="true"></span></button>
      </div>
    </article>
  `;
}

function memoBoardPage(showEditor = false, selected = null) {
  return `
    <section class="memo-board ${showEditor ? "editor-open" : ""}" aria-label="备忘录图钉板">
      <div class="memo-board-rail" aria-hidden="true">
        ${memoStat("全部", state.memoSummary.count || 0)}
        ${memoStat("置顶", state.memoSummary.pinned || 0)}
        ${memoStat("归档", state.memoSummary.archived || 0)}
        ${memoStat("敏感", state.memoSummary.sensitive || 0)}
      </div>
      <div class="memo-note-grid">
        ${state.memos.length ? state.memos.map(memoNoteCard).join("") : `<div class="memo-empty">还没有纸条，点右上角新纸条钉第一张。</div>`}
      </div>
      ${showEditor ? memoEditorPage(selected) : ""}
    </section>
  `;
}

function memoEditorPage(selected) {
  const editing = selected || {};
  const created = editing.created_at ? formatDateTime(editing.created_at) : "";
  const updated = editing.updated_at && editing.updated_at !== editing.created_at ? formatDateTime(editing.updated_at) : "";
  return `
    <article class="memo-editor-page">
      <section class="memo-detail-paper">
        <div class="memo-detail-pin" aria-hidden="true"></div>
        <div class="memo-detail-head">
          <div>
            <p class="eyebrow">${selected ? "纸条详情" : "新建纸条"}</p>
            <h2>${escapeHtml(selected?.title || "把要记的事钉住")}</h2>
          </div>
          ${selected ? `<button class="button danger" data-memo-delete="${escapeHtml(selected.id)}" type="button">删除</button>` : ""}
        </div>
        <form class="form memo-form" data-action="save-memo">
          <input name="id" value="${escapeHtml(selected?.id || "")}" type="hidden">
          <div class="form-grid compact">
            <label>标题<input name="title" value="${escapeHtml(editing.title || "")}" placeholder="留空会自动取正文前几个字"></label>
            <label>来源聊天<input name="source_chat" value="${escapeHtml(editing.source_chat || "")}" placeholder="例如：主群 / 私聊 / 某个频道"></label>
            <label>标签<input name="tags" value="${escapeHtml((editing.tags || []).join(","))}" placeholder="账号, 名言, 待办"></label>
            <label>记录者<select name="recorder"><option value="human" ${(editing.recorder || "human") === "human" ? "selected" : ""}>人</option><option value="bot" ${editing.recorder === "bot" ? "selected" : ""}>bot</option></select></label>
          </div>
          <label>内容<textarea name="content" required placeholder="账号提示、聊天片段、名人名言、待办或你想让 bot 记住的话。">${escapeHtml(editing.content || "")}</textarea></label>
          <div class="memo-detail-note">
            <strong>备注</strong>
            <p>${escapeHtml(editing.source_chat || editing.recorder || "保存后会在这里显示来源、标签和时间。")}</p>
            <p>${created ? `创建：${escapeHtml(created)}` : "保存后会记录时间戳。"}${updated ? ` · 更新：${escapeHtml(updated)}` : ""}</p>
          </div>
          <div class="memo-meta-line">
            ${check("sensitive", "敏感内容", Boolean(editing.sensitive))}
            ${check("pinned", "置顶", Boolean(editing.pinned))}
            ${check("archived", "归档", Boolean(editing.archived))}
          </div>
          <div class="actions memo-detail-actions">
            <button class="button ghost" data-memo-detail-back type="button">返回板面</button>
            <button class="primary">保存纸条并退出</button>
          </div>
        </form>
      </section>
    </article>
  `;
}

function memoPreviewText(content) {
  const normalized = String(content || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const parts = normalized.match(/[^。！？!?；;]+[。！？!?；;]?/g) || [normalized];
  return parts.slice(0, 2).join("").trim();
}

function memoPlacementStyle(id) {
  const placement = memoPlacement(id);
  return `--memo-x:${placement.x}px;--memo-y:${placement.y}px;--memo-rot:${placement.rot}deg;`;
}

function memoPlacement(id) {
  const stored = memoStoredPlacements()[id];
  if (stored && Number.isFinite(stored.x) && Number.isFinite(stored.y)) {
    return { x: stored.x, y: stored.y, rot: Number.isFinite(stored.rot) ? stored.rot : memoDefaultRotation(id) };
  }
  return { x: 0, y: 0, rot: memoDefaultRotation(id) };
}

function memoDefaultRotation(id) {
  const text = String(id || "");
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 997;
  return ((hash % 9) - 4) * 0.55;
}

function memoStoredPlacements() {
  try {
    return JSON.parse(localStorage.getItem("nestMemoBoardPlacements") || "{}") || {};
  } catch {
    return {};
  }
}

function saveMemoPlacement(id, placement) {
  if (!id) return;
  const placements = memoStoredPlacements();
  placements[id] = {
    x: Math.round(placement.x),
    y: Math.round(placement.y),
    rot: Math.round(placement.rot * 100) / 100,
  };
  localStorage.setItem("nestMemoBoardPlacements", JSON.stringify(placements));
}

function startMemoDrag(event) {
  if (state.view !== "memos" || state.memoEditorOpen || state.selectedMemoId) return;
  if (event.button !== undefined && event.button !== 0) return;
  const note = event.target.closest(".memo-note[data-memo-id]");
  if (!note || event.target.closest(".memo-note-actions, .icon-button")) return;
  const id = note.dataset.memoId || "";
  const placement = memoPlacement(id);
  state.memoDrag = {
    id,
    note,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    originX: placement.x,
    originY: placement.y,
    rot: placement.rot,
    moved: false,
  };
  note.classList.add("dragging");
  note.setPointerCapture?.(event.pointerId);
}

function moveMemoDrag(event) {
  const drag = state.memoDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const dx = event.clientX - drag.startX;
  const dy = event.clientY - drag.startY;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  const next = { x: drag.originX + dx, y: drag.originY + dy, rot: drag.rot };
  drag.note.style.setProperty("--memo-x", `${next.x}px`);
  drag.note.style.setProperty("--memo-y", `${next.y}px`);
  drag.note.style.setProperty("--memo-rot", `${next.rot}deg`);
  event.preventDefault();
}

function endMemoDrag(event) {
  const drag = state.memoDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  drag.note.classList.remove("dragging");
  drag.note.releasePointerCapture?.(event.pointerId);
  if (drag.moved) {
    const next = {
      x: drag.originX + event.clientX - drag.startX,
      y: drag.originY + event.clientY - drag.startY,
      rot: drag.rot,
    };
    saveMemoPlacement(drag.id, next);
    state.memoSuppressClickUntil = Date.now() + 260;
  }
  state.memoDrag = null;
}

async function selectMemo(id) {
  state.selectedMemoId = id || "";
  state.memoEditorOpen = Boolean(id);
  syncRouteForState("memos");
  await renderMemos();
  updateShell();
}

async function newMemo() {
  state.selectedMemoId = "";
  state.memoEditorOpen = true;
  syncRouteForState("memos");
  await renderMemos();
  updateShell();
}

async function closeMemoDetail() {
  state.selectedMemoId = "";
  state.memoEditorOpen = false;
  syncRouteForState("memos", true);
  await renderMemos();
  updateShell();
}

async function saveMemo(event) {
  event.preventDefault();
  const memoForm = event.target.closest('[data-action="save-memo"]');
  if (!memoForm) return;
  const form = new FormData(memoForm);
  const id = String(form.get("id") || "").trim();
  const body = {
    title: form.get("title") || "",
    content: form.get("content") || "",
    tags: splitWords(form.get("tags")),
    source_chat: form.get("source_chat") || "",
    recorder: form.get("recorder") || "human",
    source: "web",
    sensitive: form.has("sensitive"),
    pinned: form.has("pinned"),
    archived: form.has("archived"),
  };
  const payload = id
    ? await api("/api/ui/memos/update", { method: "POST", body: JSON.stringify({ id, ...body }) })
    : await api("/api/ui/memos", { method: "POST", body: JSON.stringify(body) });
  state.selectedMemoId = "";
  state.memoEditorOpen = false;
  state.notice = "备忘录已保存。";
  state.bootstrap = null;
  syncRouteForState("memos", true);
  await renderMemos();
  updateShell();
}

async function updateMemoFlag(id, field, value) {
  await api("/api/ui/memos/update", { method: "POST", body: JSON.stringify({ id, [field]: value }) });
  state.notice = field === "pinned" ? (value ? "纸条已置顶。" : "纸条已取消置顶。") : (value ? "纸条已归档。" : "纸条已取消归档。");
  await renderMemos();
  updateShell();
}

async function deleteMemo(id) {
  if (!id || !(await confirmAction("删除这条备忘录？"))) return;
  await api(`/api/ui/memos/${encodeURIComponent(id)}`, { method: "DELETE" });
  state.selectedMemoId = "";
  state.memoEditorOpen = false;
  state.notice = "备忘录已删除。";
  state.bootstrap = null;
  syncRouteForState("memos", true);
  await renderMemos();
  updateShell();
}

async function renderMedia() {
  const payload = await api("/api/ui/media");
  state.media = payload.items || [];
  state.mediaStorage = payload.storage || { bytes: 0, count: 0, label: "0 B" };
  state.mediaOrganization = payload.organization || { folders: [], asset_locations: {}, trash: [] };
  const folders = visibleMediaFolders();
  const visibleFolderIds = new Set(folders.map((folder) => folder.id));
  state.expandedMediaFolderIds = (state.expandedMediaFolderIds || []).filter((id) => visibleFolderIds.has(id));
  const currentFolder = activeMediaFolder();
  if (state.mediaMode === "folder" && !currentFolder) {
    state.mediaMode = "main";
    state.activeMediaFolderId = "";
  }
  const assets = currentMediaAssets();
  const trashed = trashedMediaItems();
  const itemCount = state.mediaMode === "trash" ? trashed.length : assets.length + (state.mediaMode === "main" ? folders.length : 0);
  panel("media").innerHTML = `
    ${mediaHead(assets, folders, trashed)}
    ${state.mediaMode === "folder" ? mediaFolderHeader(currentFolder) : ""}
    <section class="media-workspace ${state.mediaFloating ? "floating" : ""} ${state.mediaMode === "folder" ? "folder-mode" : ""} ${state.mediaMode === "trash" ? "trash-mode" : ""}" data-media-workspace>
      <div class="media-gallery ${state.mediaFloating ? "floating" : ""}" data-media-gallery style="${state.mediaFloating ? `min-height:${mediaFloatHeight(itemCount)}px` : ""}">
        ${state.mediaMode === "trash" ? trashed.map(mediaTrashCard).join("") : ""}
        ${state.mediaMode === "main" ? folders.map(mediaFolderCard).join("") : ""}
        ${state.mediaMode !== "trash" ? assets.map(mediaCard).join("") : ""}
        ${mediaEmptyState(assets, folders)}
      </div>
    </section>
  `;
  renderGlobalDialogs();
  bindMediaInteractions();
  if (state.mediaFloating) startMediaFloat();
  else stopMediaFloat();
  if (state.mediaFloatPreferred && state.mediaFloatResumePending && !state.mediaFloating) {
    state.mediaFloatResumePending = false;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (state.view !== "media" || !state.mediaFloatPreferred || state.mediaFloating) return;
        state.mediaFloatPositions = {};
        state.mediaFloating = true;
        renderMedia();
      });
    });
  }
}

async function toggleMediaFloat() {
  if (state.mediaFloating) {
    state.mediaFloatPreferred = false;
    state.mediaFloatResumePending = false;
    await animateFloatToGrid();
    state.mediaFloating = false;
  } else {
    state.mediaFloatPreferred = true;
    state.mediaFloatResumePending = false;
    state.mediaFloatPositions = {};
    state.mediaFloating = true;
  }
  renderMedia();
}

function allMediaAssets() {
  return state.media.flatMap((manifest) =>
    (manifest.assets || []).map((asset) => ({ ...asset, date: asset.date || manifest.date }))
  );
}

function visibleMediaAssets() {
  return allMediaAssets().filter((asset) => !asset.trashed);
}

function rootMediaAssets() {
  return visibleMediaAssets().filter((asset) => !asset.folder_id);
}

function folderMediaAssets(folderId) {
  return visibleMediaAssets().filter((asset) => asset.folder_id === folderId);
}

function currentMediaAssets() {
  if (state.mediaMode === "folder") return folderMediaAssets(state.activeMediaFolderId);
  if (state.mediaMode === "trash") return [];
  return rootMediaAssets();
}

function visibleMediaFolders() {
  return (state.mediaOrganization.folders || []).filter((folder) => !folder.trashed);
}

function activeMediaFolder() {
  return visibleMediaFolders().find((folder) => folder.id === state.activeMediaFolderId) || null;
}

function mediaPageTitle() {
  if (state.mediaMode === "trash") return "回收站";
  if (state.mediaMode === "folder") return activeMediaFolder()?.name || "文件夹";
  return "媒体";
}

function trashedMediaItems() {
  const trash = state.mediaOrganization.trash || [];
  const assets = allMediaAssets();
  const folders = state.mediaOrganization.folders || [];
  return trash.map((item) => {
    if (item.type === "asset") {
      const asset = assets.find((candidate) => candidate.sha256 === item.id);
      return asset ? { ...item, name: asset.original_name || asset.sha256 } : { ...item, name: item.id };
    }
    const folder = folders.find((candidate) => candidate.id === item.id);
    return { ...item, name: folder?.name || item.id };
  });
}

function mediaHead(assets, folders, trashed) {
  return `
    <header class="topbar media-topbar">
      <div class="page-title media-title">
        <h1>${escapeHtml(mediaPageTitle())}</h1>
        <span class="media-storage-inline">${escapeHtml(state.mediaStorage.label || formatBytes(state.mediaStorage.bytes || 0))} · 回收站 ${trashed.length}</span>
      </div>
      <div class="actions">${mediaToolbar(assets, folders, trashed)}</div>
    </header>
  `;
}

function mediaToolbar(assets, folders, trashed) {
  return `
    <div class="media-toolbar">
      <button class="media-float-toggle ${state.mediaFloating ? "active" : ""}" data-media-toggle-float type="button">
        ${iconImg("appearance", "漂浮模式")}<span>${state.mediaFloating ? "关闭漂浮" : "漂浮模式"}</span>
      </button>
      <div class="media-organize-tools">
        ${state.mediaMode === "main" ? `<button class="button ghost media-folder-create-button" data-media-folder-create type="button"><span class="folder-create-glyph" aria-hidden="true"><span></span></span><span>新建文件夹</span></button>` : `<button class="button ghost media-folder-create-button" data-media-mode="main" type="button">返回媒体</button>`}
        <button class="media-trash-drop ${state.mediaMode === "trash" ? "active" : ""}" data-media-trash-zone data-media-mode="trash" type="button" title="拖到这里回收">
          <span class="trash-symbol" aria-hidden="true"></span>
          <span>回收站 ${trashed.length}</span>
        </button>
      </div>
    </div>
  `;
}

function mediaStorageSubtitle(assets, folders, trashed) {
  if (state.mediaMode === "trash") return `${trashed.length} 个回收项目`;
  if (state.mediaMode === "folder") return `${assets.length} 个文件`;
  return `${assets.length} 个未归类文件 · ${folders.length} 个文件夹`;
}

function mediaCard(asset) {
  const id = asset.sha256 || asset.url || asset.path || asset.original_name;
  const name = asset.original_name || asset.sha256 || "未命名媒体";
  return `
    <article class="media-card" draggable="true" data-media-item="asset" data-media-id="${escapeHtml(asset.sha256)}" data-media-open="${escapeHtml(id)}">
      <span class="media-thumb ${asset.is_image ? "" : "file-thumb"}">
        ${asset.is_image ? `<img src="${escapeHtml(webuiAssetUrl(asset.url))}" alt="${escapeHtml(name)}" loading="lazy" draggable="false">` : iconImg("media", name)}
      </span>
      <span class="media-card-info">
        <strong>${escapeHtml(name)}</strong>
        <em>${escapeHtml(asset.folder_id && state.mediaMode !== "folder" ? folderName(asset.folder_id) : asset.date || "未归入文件夹")} · ${escapeHtml(formatBytes(asset.size_bytes || 0))}</em>
      </span>
    </article>
  `;
}

function mediaFolderCard(folder) {
  const count = visibleMediaAssets().filter((asset) => asset.folder_id === folder.id).length;
  const expanded = isMediaFolderExpanded(folder.id);
  const tags = Array.isArray(folder.tags) ? folder.tags.filter(Boolean) : [];
  return `
    <article class="media-card folder-card ${expanded ? "expanded" : ""}" draggable="true" data-media-item="folder" data-media-id="${escapeHtml(folder.id)}" data-media-folder-drop="${escapeHtml(folder.id)}" data-media-folder-expand="${escapeHtml(folder.id)}">
      <button class="media-thumb folder-thumb" data-media-folder-open="${escapeHtml(folder.id)}" type="button">
        ${iconImg("modules", folder.name)}
        <span class="folder-mouth">${expanded ? "打开文件夹，把图片拖到这里" : count ? "打开" : "空"}</span>
      </button>
      <span class="media-card-info">
        <strong>${escapeHtml(folder.name)}</strong>
        <em>${escapeHtml(folder.note || `${count} 个文件`)}</em>
      </span>
      ${tags.length ? `<span class="folder-tags">${tags.map((tag) => `<b>${escapeHtml(tag)}</b>`).join("")}</span>` : ""}
      ${expanded ? `<button class="folder-drop-mouth folder-collapse-button" data-media-folder-collapse="${escapeHtml(folder.id)}" type="button">取消</button>` : ""}
    </article>
  `;
}

function mediaTrashCard(item) {
  return `
    <article class="media-card trash-card" draggable="true" data-media-item="${escapeHtml(item.type)}" data-media-id="${escapeHtml(item.id)}">
      <span class="media-thumb file-thumb">${item.type === "folder" ? iconImg("modules", item.name || "文件夹") : iconImg("media", item.name || "图片")}</span>
      <span class="media-card-info">
        <strong>${escapeHtml(item.name || item.id)}</strong>
        <em>${escapeHtml(item.type === "folder" ? "文件夹" : "图片")} · 已回收</em>
      </span>
      <div class="trash-card-actions">
        <button class="button ghost" data-media-restore="${escapeHtml(item.type)}" data-media-id="${escapeHtml(item.id)}" type="button">恢复</button>
        <button class="button danger" data-media-delete="${escapeHtml(item.type)}" data-media-id="${escapeHtml(item.id)}" type="button">彻底删除</button>
      </div>
    </article>
  `;
}

function mediaFolderHeader(folder) {
  if (!folder) return "";
  return `
    <section class="media-folder-shell" data-folder-shell>
      <button class="button ghost" data-media-folder-close type="button">返回媒体</button>
      <div>
        <h2>${escapeHtml(folder.name)}</h2>
        <p class="muted">把图片拖出这个区域即可移出文件夹。</p>
      </div>
      <button class="button ghost folder-header-edit" data-media-folder-edit="${escapeHtml(folder.id)}" type="button">编辑文件夹</button>
    </section>
  `;
}

function mediaEmptyState(assets, folders) {
  if (state.mediaMode === "trash") {
    return trashedMediaItems().length ? "" : `<article class="card"><div class="card-body muted">回收站是空的。</div></article>`;
  }
  if (state.mediaMode === "folder") {
    return assets.length ? "" : `<article class="card"><div class="card-body muted">这个文件夹还没有图片。</div></article>`;
  }
  return !folders.length && !assets.length ? `<article class="card"><div class="card-body muted">还没有媒体归档。</div></article>` : "";
}

function mediaFloatHeight(count) {
  const rows = Math.max(2, Math.ceil(Math.max(1, count) / 3));
  return Math.max(window.innerHeight * 1.35, rows * 240 + 260);
}

function openMediaDetail(id) {
  state.selectedMedia = allMediaAssets().find((asset) => [asset.sha256, asset.url, asset.path, asset.original_name].includes(id)) || null;
  state.mediaNoteEditing = false;
  renderGlobalDialogs();
}

function closeMediaDetail() {
  state.selectedMedia = null;
  state.mediaNoteEditing = false;
  renderGlobalDialogs();
}

function renderGlobalDialogs() {
  const root = document.getElementById("global-dialog-root");
  if (!root) return;
  root.innerHTML = `
    ${state.selectedMedia ? mediaDialog(state.selectedMedia) : ""}
    ${state.mediaFolderModalOpen ? mediaFolderCreateDialog() : ""}
    ${state.moduleInstallOpen ? moduleInstallDialog() : ""}
    ${state.t2iTemplateDialogOpen ? t2iTemplateDialog() : ""}
    ${state.onboardingOpen ? onboardingDialog() : ""}
  `;
}

function onboardingSteps() {
  return [
    {
      title: "先看小窝的整体动线",
      body: "首页负责给你快速回到最近状态，左侧是所有功能入口，右侧内容区会随着入口切换。新手引导现在点任意地方都会前进，全部看完才会自动退出。",
      target: "首页 · 全局导航",
      selector: ".app-shell",
      view: "dashboard",
      checklist: ["左侧用于切换功能", "中间区域显示当前页面", "遇到遮罩时点任意位置继续"],
    },
    {
      title: "设置是小窝的总开关",
      body: "外观、模块、访问密钥、导入导出都从设置进入。需要调整小窝长相、开放访问、备份迁移，先来这里找。",
      target: "左侧 · 设置",
      selector: '[data-tour-target="nav-settings"]',
      view: "dashboard",
      checklist: ["进入设置后会展开二级菜单", "外观和模块分开管理", "访问密钥与导入导出也在这里"],
    },
    {
      title: "外观页只负责长相",
      body: "这里改小窝标题、副标题、头像和主题外观。玻璃小屋、纸庭、夜间工作室这类全局皮肤都归到外观页，保存后会影响整套 WebUI。",
      target: "设置 · 外观设置",
      selector: '[data-tour-target="settings-appearance"]',
      view: "settings",
      section: "appearance",
      checklist: ["修改标题、副标题、头像", "选择全局外观风格", "保存后刷新页面即可看到效果"],
    },
    {
      title: "模块页只负责功能",
      body: "这里控制日记、媒体、印象、备忘录和拓展包是否启用，也能进入每个模块自己的详细设置。关闭模块后，左侧对应入口会一起隐藏。",
      target: "设置 · 模块控制台",
      selector: '[data-tour-target="settings-modules"]',
      view: "settings",
      section: "modules",
      checklist: ["开关核心模块", "进入模块详情改规则", "从链接安装新的拓展包"],
    },
    {
      title: "日记页负责长期记录",
      body: "日记页按日记本和日期归档，适合沉淀完整事件、每日记录和 bot 写入的长内容。你可以筛选日记本、打开某一天，也可以手动补写。",
      target: "左侧 · 日记",
      selector: '[data-tour-target="nav-diary"]',
      view: "diary",
      checklist: ["按日期查看记录", "按日记本分类管理", "需要时手动新建或编辑日记"],
    },
    {
      title: "媒体页负责图片和附件",
      body: "媒体页用来归档图片、附件和外部文件。日记里的图片推送、原图查看、文件夹整理、回收站恢复，都围绕这里展开。",
      target: "左侧 · 媒体",
      selector: '[data-tour-target="nav-media"]',
      view: "media",
      checklist: ["按文件夹整理素材", "查看图片备注和原图", "误删后从回收站恢复"],
    },
    {
      title: "备忘录负责短而敏感的记忆",
      body: "备忘录适合账号提示、聊天片段、名言、待办和不值得写成长日记的小纸条。敏感内容默认隐藏，是否允许 bot 自主写入可以在模块设置里控制。",
      target: "左侧 · 备忘录",
      selector: '[data-tour-target="nav-memos"]',
      view: "memos",
      checklist: ["新建短纸条", "置顶或归档备忘录", "需要时临时显示敏感内容"],
    },
  ];
}

function onboardingStepRect(step) {
  const selector = step.selector || "";
  const target = selector ? document.querySelector(selector) : null;
  if (!target) {
    return {
      found: false,
      top: Math.max(18, window.innerHeight * 0.22),
      left: Math.max(18, window.innerWidth * 0.5 - 120),
      width: 240,
      height: 72,
    };
  }
  target.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  const rect = target.getBoundingClientRect();
  const pad = 8;
  return {
    found: true,
    top: Math.max(10, rect.top - pad),
    left: Math.max(10, rect.left - pad),
    width: Math.min(window.innerWidth - 20, rect.width + pad * 2),
    height: Math.min(window.innerHeight - 20, rect.height + pad * 2),
  };
}

function onboardingTooltipStyle(rect) {
  const width = Math.min(380, Math.max(288, window.innerWidth - 36));
  const gap = 18;
  const fitsRight = rect.left + rect.width + gap + width < window.innerWidth;
  const fitsLeft = rect.left - gap - width > 0;
  const fitsBelow = rect.top + rect.height + gap + 270 < window.innerHeight;
  let left = rect.left + rect.width + gap;
  let top = Math.max(18, rect.top + rect.height / 2 - 150);
  let placement = "right";
  if (!fitsRight && fitsLeft) {
    left = rect.left - gap - width;
    placement = "left";
  } else if (!fitsRight && fitsBelow) {
    left = Math.min(window.innerWidth - width - 18, Math.max(18, rect.left));
    top = rect.top + rect.height + gap;
    placement = "bottom";
  } else if (!fitsRight) {
    left = Math.min(window.innerWidth - width - 18, Math.max(18, rect.left));
    top = Math.max(18, rect.top - 292);
    placement = "top";
  }
  top = Math.min(window.innerHeight - 260, Math.max(18, top));
  return {
    style: `left:${Math.round(left)}px;top:${Math.round(top)}px;width:${Math.round(width)}px;`,
    placement,
  };
}

function onboardingActions(index, total) {
  return `
    <button class="button ghost" data-onboarding-prev ${index === 0 ? "disabled" : ""} type="button">上一步</button>
    ${
      index >= total - 1
        ? `<button class="primary" data-onboarding-finish type="button">完成引导</button>`
        : `<button class="primary" data-onboarding-next type="button">下一步</button>`
    }
  `;
}

function onboardingStepMarkup(step, index, total) {
  return `
    <div class="onboarding-copy">
      <p class="eyebrow">首次使用引导</p>
      <h2>${escapeHtml(step.title)}</h2>
      <p class="onboarding-body">${escapeHtml(step.body)}</p>
      <div class="onboarding-target">${escapeHtml(step.target)}</div>
      <ul class="onboarding-checklist">
        ${(step.checklist || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
      <div class="onboarding-dots">${Array.from({ length: total }, (_, i) => `<span class="${i === index ? "active" : ""}"></span>`).join("")}</div>
      <p class="onboarding-hint">${index >= total - 1 ? "这是最后一步，点任意地方完成并退出。" : "点任意地方继续下一步。"}</p>
    </div>
  `;
}

function onboardingDialog() {
  const steps = onboardingSteps();
  const index = Math.max(0, Math.min(state.onboardingStep || 0, steps.length - 1));
  const step = steps[index];
  const rect = onboardingStepRect(step);
  const tooltip = onboardingTooltipStyle(rect);
  return `
    <div class="onboarding-backdrop" data-onboarding-advance>
      <div class="onboarding-spotlight ${rect.found ? "" : "soft"}" style="left:${Math.round(rect.left)}px;top:${Math.round(rect.top)}px;width:${Math.round(rect.width)}px;height:${Math.round(rect.height)}px;"></div>
      <article class="nest-dialog onboarding-dialog placement-${tooltip.placement}" data-onboarding-dialog data-onboarding-advance role="dialog" aria-modal="true" aria-label="小窝首次使用引导" style="${tooltip.style}">
        <div class="onboarding-arrow" aria-hidden="true"></div>
        <div class="onboarding-stage" data-onboarding-stage>
          ${onboardingStepMarkup(step, index, steps.length)}
        </div>
        <div class="actions dialog-actions" data-onboarding-actions>
          ${onboardingActions(index, steps.length)}
        </div>
      </article>
    </div>
  `;
}

async function advanceOnboarding() {
  const steps = onboardingSteps();
  const index = Math.max(0, Math.min(state.onboardingStep || 0, steps.length - 1));
  if (index >= steps.length - 1) {
    await finishOnboarding();
    return;
  }
  state.onboardingStep = index + 1;
  await updateOnboardingDialog({ animate: true });
}

function waitForOnboardingFade(ms = 140) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function updateOnboardingDialog({ animate = false } = {}) {
  state.onboardingStep = Math.max(0, Math.min(state.onboardingStep || 0, onboardingSteps().length - 1));
  const backdrop = document.querySelector(".onboarding-backdrop");
  if (animate && backdrop) {
    backdrop.classList.add("transitioning");
    await waitForOnboardingFade();
  }
  await applyOnboardingStepRoute(onboardingSteps()[state.onboardingStep]);
  if (animate && document.querySelector(".onboarding-backdrop")) {
    patchOnboardingDialog();
  } else {
    renderGlobalDialogs();
  }
  if (animate) {
    const nextBackdrop = document.querySelector(".onboarding-backdrop");
    if (nextBackdrop) {
      requestAnimationFrame(() => nextBackdrop.classList.remove("transitioning"));
    }
  }
}

function patchOnboardingDialog() {
  const steps = onboardingSteps();
  const index = Math.max(0, Math.min(state.onboardingStep || 0, steps.length - 1));
  const step = steps[index];
  const rect = onboardingStepRect(step);
  const tooltip = onboardingTooltipStyle(rect);
  const spotlight = document.querySelector(".onboarding-spotlight");
  const dialog = document.querySelector("[data-onboarding-dialog]");
  const stage = document.querySelector("[data-onboarding-stage]");
  const actions = document.querySelector("[data-onboarding-actions]");
  if (!spotlight || !dialog || !stage || !actions) {
    renderGlobalDialogs();
    return;
  }
  spotlight.className = `onboarding-spotlight ${rect.found ? "" : "soft"}`.trim();
  spotlight.style.left = `${Math.round(rect.left)}px`;
  spotlight.style.top = `${Math.round(rect.top)}px`;
  spotlight.style.width = `${Math.round(rect.width)}px`;
  spotlight.style.height = `${Math.round(rect.height)}px`;
  dialog.className = `nest-dialog onboarding-dialog placement-${tooltip.placement}`;
  dialog.setAttribute("style", tooltip.style);
  stage.innerHTML = onboardingStepMarkup(step, index, steps.length);
  actions.innerHTML = onboardingActions(index, steps.length);
}

async function applyOnboardingStepRoute(step) {
  if (!step) return;
  if (step.view === "settings") {
    state.view = "settings";
    state.settingsMenuOpen = true;
    state.settingsSection = step.section || "appearance";
    state.settingsModuleDetail = "";
    syncRouteForState("settings", true);
    await renderSettings();
    updateShell();
    return;
  }
  if (step.view === "memos" && !isMemosEnabled()) {
    step = { ...step, view: isMediaEnabled() ? "media" : "dashboard" };
  }
  if (step.view && state.view !== step.view) {
    await setView(step.view, { replaceRoute: true });
  }
}

async function openOnboarding(step = 0) {
  state.onboardingOpen = true;
  state.onboardingStep = step;
  await applyOnboardingStepRoute(onboardingSteps()[state.onboardingStep]);
  renderGlobalDialogs();
}

async function finishOnboarding() {
  state.onboardingOpen = false;
  state.onboardingStep = 0;
  try {
    const payload = await api("/api/ui/onboarding", { method: "POST", body: JSON.stringify({ completed: true }) });
    if (payload?.settings) {
      if (state.bootstrap?.settings) state.bootstrap.settings.onboarding_completed = true;
      if (state.settings?.settings) state.settings.settings.onboarding_completed = true;
    }
  } catch (err) {
    state.toast = `引导状态保存失败：${err.message}`;
    updateShell();
    clearToastSoon();
  }
  renderGlobalDialogs();
}

function moduleInstallDialog() {
  return `
    <div class="media-dialog-backdrop soft" data-module-install-close>
      <article class="nest-dialog module-install-dialog" role="dialog" aria-modal="true" aria-label="从链接安装模块" onclick="event.stopPropagation()">
        <button class="media-dialog-close" data-module-install-close type="button" aria-label="关闭">×</button>
        <div class="settings-mini-head">
          <strong>从链接安装模块</strong>
          <span>支持 GitHub 仓库或 zip 包</span>
        </div>
        <form class="form" data-action="install-module-from-link">
          <label>模块链接<input name="source_url" type="url" placeholder="https://github.com/owner/nest-extension" required></label>
          <div class="choice-grid module-install-options">
            <label class="choice-card"><input name="enable_after_install" type="checkbox" checked><span><strong>安装后启用</strong><em>外观模块会切换为当前样式，其他模块会加入启用列表。</em></span></label>
            <label class="choice-card"><input name="overwrite" type="checkbox"><span><strong>覆盖同名模块</strong><em>覆盖前会自动备份旧目录。</em></span></label>
          </div>
          <details class="module-install-help">
            <summary>内置 skill 与常见问题</summary>
            <p>让 bot 使用 <code>nest-module-development</code> skill 创建或排查模块。手工创建或修改模块文件后，需要重启 AstrBot（或重载插件）再刷新页面。</p>
            <p>侧边栏入口需要同时声明 <code>nav</code> 和 <code>page</code>，并在模块控制台启用且未隐藏；详情页会显示未通过校验的原因。</p>
          </details>
          ${state.moduleInstallMessage ? `<div class="notice soft">${escapeHtml(state.moduleInstallMessage)}</div>` : ""}
          <div class="actions dialog-actions">
            <button class="button ghost" data-module-install-close type="button">取消</button>
            <button class="primary" ${state.moduleInstallBusy ? "disabled" : ""}>${state.moduleInstallBusy ? "安装中..." : "安装模块"}</button>
          </div>
        </form>
      </article>
    </div>
  `;
}

function openModuleInstallDialog() {
  state.moduleInstallOpen = true;
  state.moduleInstallMessage = "";
  renderGlobalDialogs();
}

function closeModuleInstallDialog() {
  if (state.moduleInstallBusy) return;
  state.moduleInstallOpen = false;
  state.moduleInstallMessage = "";
  renderGlobalDialogs();
}

async function installModuleFromLink(event) {
  event.preventDefault();
  if (state.moduleInstallBusy) return;
  const form = new FormData(event.currentTarget);
  state.moduleInstallBusy = true;
  state.moduleInstallMessage = "正在下载并检查模块包...";
  renderGlobalDialogs();
  try {
    const payload = await api("/api/ui/modules/install", {
      method: "POST",
      body: JSON.stringify({
        source_url: form.get("source_url"),
        overwrite: form.has("overwrite"),
        enable_after_install: form.has("enable_after_install"),
      }),
    });
    state.moduleInstallOpen = false;
    state.moduleInstallBusy = false;
    state.moduleInstallMessage = "";
    const installedName = payload.module?.name || payload.module?.id || "模块";
    const capabilityErrors = payload.capability_errors || [];
    if (capabilityErrors.length) {
      state.error = `${installedName} 已安装，但声明校验没通过：${capabilityErrors.join("；")}`;
    } else if (payload.requires_explicit_enable) {
      state.error = `${installedName} 自带后端代码，出于安全没有自动启用。请在模块卡片上手动开启。`;
    }
    state.toast = `已安装 ${installedName}`;
    state.bootstrap = null;
    await renderSettings();
    updateShell();
    clearToastSoon();
  } catch (err) {
    state.moduleInstallBusy = false;
    state.moduleInstallMessage = err.message;
    renderGlobalDialogs();
  }
}

/** 单独控制某个已启用模块要不要出现在侧边栏。 */
async function saveModuleNavVisibility(moduleId, visible) {
  const current = state.settings?.settings || state.bootstrap?.settings;
  if (!moduleId || !current) return;
  const hidden = new Set(current.hidden_module_nav_ids || []);
  if (visible) hidden.delete(moduleId);
  else hidden.add(moduleId);
  try {
    const saved = await api("/api/ui/settings", {
      method: "POST",
      body: JSON.stringify({ ...current, hidden_module_nav_ids: Array.from(hidden) }),
    });
    if (saved?.settings) state.settings = { ...(state.settings || {}), settings: saved.settings };
    state.toast = visible ? "已在侧边栏显示" : "已从侧边栏隐藏";
    state.bootstrap = null;
    await loadView();
    clearToastSoon();
  } catch (err) {
    state.error = err.message;
    updateShell();
  }
}

async function uninstallModule(moduleId, keepData = false, source = null) {
  if (!moduleId || state.moduleUninstallBusy) return;
  state.moduleUninstallBusy = moduleId;
  const message = keepData
    ? `卸载 ${moduleId}，但保留它的数据目录？模块文件会先备份再删除。`
    : `完全卸载 ${moduleId}？整个模块目录会先备份到 imports/module-uninstall-backups/ 再删除。`;
  const buttons = Array.from(document.querySelectorAll("[data-module-uninstall]")).filter(
    (button) => button.dataset.moduleUninstall === moduleId
  );

  try {
    if (!(await confirmAction(message))) return;
    for (const button of buttons) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    if (source) source.textContent = "正在卸载…";

    const result = await api("/api/ui/modules/uninstall", {
      method: "POST",
      body: JSON.stringify({ module_id: moduleId, keep_data: keepData }),
    });
    await unmountModulePage(moduleId);
    if (result?.settings) state.settings = { ...(state.settings || {}), settings: result.settings };
    state.settingsModuleDetail = "";
    state.toast = keepData ? `已卸载 ${moduleId}，数据仍保留` : `已卸载 ${moduleId}`;
    state.error = "";
    state.bootstrap = null;
    if (isModuleView() && moduleIdFromView() === moduleId) {
      await setView("settings");
    } else {
      await loadView();
    }
    clearToastSoon();
  } catch (err) {
    state.error = err.message;
    updateShell();
  } finally {
    state.moduleUninstallBusy = "";
    for (const button of buttons) {
      if (!button.isConnected) continue;
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
    if (source?.isConnected) source.textContent = keepData ? "卸载并保留数据" : "完全卸载";
  }
}

function selectFrontendStyle(styleId, source = null) {
  const form = source?.closest("form") || document.querySelector('[data-action="save-appearance"], [data-action="save-settings"]');
  const select = form?.querySelector('select[name="active_frontend_style"]');
  if (!select) return;
  select.value = styleId || "default";
  form.querySelectorAll("[data-style-select]").forEach((node) => {
    node.classList.toggle("active", node.dataset.styleSelect === select.value);
  });
  state.toast = "样式已选择，保存后生效";
  updateShell();
  clearToastSoon();
}

function t2iTemplateDialog() {
  const form = document.querySelector('[data-action="save-settings"]');
  const currentName = form?.querySelector('[name="diary_t2i_template_name"]')?.value || state.settings?.settings?.diary_t2i_template_name || "plain_note";
  const customValue = form?.querySelector('[name="diary_t2i_template"]')?.value || state.settings?.settings?.diary_t2i_template || "";
  const customSettings = { diary_t2i_template_name: currentName, diary_t2i_template: customValue };
  const customTemplates = customT2iTemplates(customSettings);
  const customCurrent = customTemplates.find((item) => item.id === currentName);
  const current = customCurrent || (currentName === "custom" && customValue && !customValue.startsWith("{") ? { id: "custom", template: customValue } : t2iTemplateById(currentName));
  const cards = [...DIARY_T2I_TEMPLATES, ...customTemplates];
  return `
    <div class="media-dialog-backdrop soft" data-t2i-close>
      <article class="nest-dialog t2i-dialog" role="dialog" aria-modal="true" aria-label="图片推送模板" onclick="event.stopPropagation()">
        <button class="media-dialog-close" data-t2i-close type="button" aria-label="关闭">×</button>
        <div class="settings-mini-head t2i-head"><strong>图片推送模板</strong><span>选择后会保存到日记模块设置</span></div>
        <div class="t2i-template-grid">
          ${cards.map((item) => `
            <button class="t2i-template-card ${currentName === item.id ? "active" : ""}" data-t2i-select="${escapeHtml(item.id)}" type="button">
              <span class="t2i-preview">${t2iPreviewHtml(item.template)}</span>
              <strong>${escapeHtml(item.name)}</strong>
              <em>${escapeHtml(item.tone)}</em>
            </button>
          `).join("")}
        </div>
        <button class="button ghost" data-t2i-custom-toggle type="button">${state.t2iCustomOpen ? "收起新增模板" : "新增自定义模板"}</button>
        <div class="t2i-custom ${state.t2iCustomOpen ? "open" : ""}">
          <div class="form-grid compact">
            <label>模板名称<input data-t2i-custom-name placeholder="例如：群聊日报"></label>
            <label>模板风格<input data-t2i-custom-tone placeholder="例如：轻量、适合长文"></label>
          </div>
          <label>模板内容<textarea data-t2i-custom-value rows="7" placeholder="粘贴 HTML 模板，支持 {{ date }}、{{ notebook_name }}、{{ title }}、{{ body }}"></textarea></label>
          <button class="primary" data-t2i-custom-save type="button">保存并使用这个模板</button>
        </div>
        <div class="t2i-current-preview">
          <strong>当前预览</strong>
          <div>${t2iPreviewHtml(current.template)}</div>
        </div>
      </article>
    </div>
  `;
}

function openT2iTemplateDialog() {
  state.t2iTemplateDialogOpen = true;
  renderGlobalDialogs();
}

function closeT2iTemplateDialog() {
  state.t2iTemplateDialogOpen = false;
  state.t2iCustomOpen = false;
  renderGlobalDialogs();
}

function selectT2iTemplate(id) {
  const form = document.querySelector('[data-action="save-settings"]');
  if (!form) return;
  const nameInput = form.querySelector('[name="diary_t2i_template_name"]');
  const templateInput = form.querySelector('[name="diary_t2i_template"]');
  if (!nameInput || !templateInput) return;
  const customTemplates = customT2iTemplates({ diary_t2i_template_name: nameInput.value, diary_t2i_template: templateInput.value });
  const customItem = customTemplates.find((item) => item.id === id);
  if (customItem) {
    nameInput.value = customItem.id;
    templateInput.value = t2iTemplateStore(customTemplates);
  } else {
    const item = t2iTemplateById(id);
    nameInput.value = item.id;
    templateInput.value = templateInput.value.startsWith("{") ? templateInput.value : "";
  }
  state.toast = "图片模板已选择，记得保存设置";
  closeT2iTemplateDialog();
  updateShell();
  clearToastSoon();
}

function saveCustomT2iTemplate() {
  const form = document.querySelector('[data-action="save-settings"]');
  const nameInput = form?.querySelector('[name="diary_t2i_template_name"]');
  const templateInput = form?.querySelector('[name="diary_t2i_template"]');
  if (!nameInput || !templateInput) return;
  const template = document.querySelector("[data-t2i-custom-value]")?.value.trim() || "";
  if (!template) {
    state.toast = "先填写模板内容";
    updateShell();
    clearToastSoon();
    return;
  }
  const templates = customT2iTemplates({ diary_t2i_template_name: nameInput.value, diary_t2i_template: templateInput.value });
  const id = `custom_${Date.now()}`;
  const item = {
    id,
    name: document.querySelector("[data-t2i-custom-name]")?.value.trim() || "自定义模板",
    tone: document.querySelector("[data-t2i-custom-tone]")?.value.trim() || "自定义",
    template,
  };
  templates.push(item);
  nameInput.value = id;
  templateInput.value = t2iTemplateStore(templates);
  state.toast = "自定义模板已保存，记得保存设置";
  closeT2iTemplateDialog();
  updateShell();
  clearToastSoon();
}

function mediaDialog(asset) {
  const wide = Number(asset.width || 0) >= Number(asset.height || 0);
  const layout = wide ? "landscape" : "portrait";
  const name = asset.original_name || asset.sha256 || "未命名媒体";
  const savedAt = formatDateTime(asset.saved_at) || asset.date || "";
  const note = asset.note || "";
  const noteEditing = Boolean(state.mediaNoteEditing);
  return `
    <div class="media-dialog-backdrop" data-media-close>
      <article class="media-dialog ${layout}" role="dialog" aria-modal="true" aria-label="${escapeHtml(name)}" onclick="event.stopPropagation()">
        <button class="media-dialog-close" data-media-close type="button" aria-label="关闭">×</button>
        <div class="media-dialog-visual">
          ${asset.is_image ? `<img src="${escapeHtml(webuiAssetUrl(asset.url))}" alt="${escapeHtml(name)}">` : iconImg("media", name)}
        </div>
        <div class="media-dialog-meta">
          <h2>${escapeHtml(name)}</h2>
          <dl>
            <div><dt>保存日期</dt><dd>${escapeHtml(savedAt || "未记录")}</dd></div>
            <div><dt>文件大小</dt><dd>${escapeHtml(formatBytes(asset.size_bytes || 0))}</dd></div>
          </dl>
          <form class="media-note-form ${noteEditing ? "editing" : ""}" data-action="save-media-note">
            <input type="hidden" name="sha256" value="${escapeHtml(asset.sha256 || "")}">
            <label>备注<textarea name="note" rows="3" placeholder="给这张图片补充备注" ${noteEditing ? "" : "readonly"}>${escapeHtml(note)}</textarea></label>
            <div class="actions dialog-actions">
              <button class="button ghost" data-media-open-original type="button">打开原图</button>
              ${noteEditing ? `<button class="primary" type="submit">保存备注</button>` : `<button class="primary" data-media-note-edit type="button">修改备注</button>`}
            </div>
          </form>
        </div>
      </article>
    </div>
  `;
}

function folderName(folderId) {
  return (state.mediaOrganization.folders || []).find((folder) => folder.id === folderId)?.name || "文件夹";
}

function expandedMediaFolderSet() {
  return new Set((state.expandedMediaFolderIds || []).filter(Boolean));
}

function isMediaFolderExpanded(folderId) {
  return expandedMediaFolderSet().has(folderId);
}

function expandMediaFolder(folderId) {
  if (!folderId) return;
  const folders = expandedMediaFolderSet();
  folders.add(folderId);
  state.expandedMediaFolderIds = Array.from(folders);
}

function collapseExpandedMediaFolder(folderId) {
  const folders = expandedMediaFolderSet();
  folders.delete(folderId);
  state.expandedMediaFolderIds = Array.from(folders);
}

function toggleExpandedMediaFolder(folderId) {
  if (isMediaFolderExpanded(folderId)) collapseExpandedMediaFolder(folderId);
  else expandMediaFolder(folderId);
}

function clearExpandedMediaFolders() {
  state.expandedMediaFolderIds = [];
}

function openMediaFolder(folderId) {
  state.mediaMode = "folder";
  state.activeMediaFolderId = folderId;
  clearExpandedMediaFolders();
  renderMedia();
}

function closeMediaFolder() {
  state.mediaMode = "main";
  state.activeMediaFolderId = "";
  clearExpandedMediaFolders();
  renderMedia();
}

function openMediaFolderModal(folderId = "") {
  state.mediaFolderEditingId = folderId || "";
  state.mediaFolderModalOpen = true;
  renderGlobalDialogs();
}

function closeMediaFolderModal() {
  state.mediaFolderModalOpen = false;
  state.mediaFolderEditingId = "";
  renderGlobalDialogs();
}

function mediaFolderCreateDialog() {
  const folder = state.mediaFolderEditingId
    ? (state.mediaOrganization.folders || []).find((item) => item.id === state.mediaFolderEditingId)
    : null;
  const tags = Array.isArray(folder?.tags) ? folder.tags.join("，") : "";
  const title = folder ? "编辑文件夹" : "新建文件夹";
  return `
    <div class="media-dialog-backdrop soft" data-media-folder-modal-close>
      <article class="nest-dialog folder-create-dialog" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}" onclick="event.stopPropagation()">
        <button class="media-dialog-close" data-media-folder-modal-close type="button" aria-label="关闭">×</button>
        <h2>${escapeHtml(title)}</h2>
        <form class="form" data-action="create-media-folder">
          <input type="hidden" name="folder_id" value="${escapeHtml(folder?.id || "")}">
          <label>文件夹名称<input name="name" value="${escapeHtml(folder?.name || "新建文件夹")}" maxlength="40" required></label>
          <label>标签<input name="tags" value="${escapeHtml(tags)}" placeholder="用逗号分开，例如：旅行，头像"></label>
          <label>备注 / 副标题<textarea name="note" rows="3" placeholder="给这个文件夹写一句说明">${escapeHtml(folder?.note || "")}</textarea></label>
          <div class="actions dialog-actions">
            <button class="button ghost" data-media-folder-modal-close type="button">取消</button>
            <button class="primary" type="submit">${folder ? "保存" : "创建"}</button>
          </div>
        </form>
      </article>
    </div>
  `;
}

async function createMediaFolder(event) {
  event.preventDefault();
  const formElement = event.target.closest('[data-action="create-media-folder"]');
  if (!formElement) return;
  const form = new FormData(formElement);
  const folderId = String(form.get("folder_id") || "");
  const payload = {
    name: form.get("name") || "新建文件夹",
    tags: splitWords(form.get("tags") || ""),
    note: form.get("note") || "",
  };
  try {
    if (folderId) {
      await api("/api/ui/media/folders/update", { method: "POST", body: JSON.stringify({ ...payload, folder_id: folderId }) });
    } else {
      await api("/api/ui/media/folders", { method: "POST", body: JSON.stringify(payload) });
    }
    state.mediaFolderModalOpen = false;
    state.mediaFolderEditingId = "";
    state.toast = folderId ? "文件夹已保存" : "文件夹已创建";
    await renderMedia();
    updateShell();
    clearToastSoon();
  } catch (err) {
    state.toast = `保存失败：${err.message}`;
    updateShell();
    clearToastSoon();
  }
}

async function saveMediaNote(event) {
  event.preventDefault();
  const formElement = event.target.closest('[data-action="save-media-note"]');
  if (!formElement) return;
  const form = new FormData(formElement);
  const sha256 = String(form.get("sha256") || "");
  const note = String(form.get("note") || "");
  try {
    const result = await api("/api/ui/media/note", { method: "POST", body: JSON.stringify({ sha256, note }) });
    const savedNote = result.asset.note ?? note;
    if (state.selectedMedia?.sha256 === sha256) state.selectedMedia = { ...state.selectedMedia, note: savedNote };
    state.media = state.media.map((manifest) => ({
      ...manifest,
      assets: (manifest.assets || []).map((asset) => asset.sha256 === sha256 ? { ...asset, note: savedNote } : asset),
    }));
    state.mediaNoteEditing = false;
    state.toast = "备注已保存";
    renderGlobalDialogs();
    updateShell();
    clearToastSoon();
  } catch (err) {
    state.toast = `保存失败：${err.message}`;
    updateShell();
    clearToastSoon();
  }
}

async function moveMediaToFolder(sha256, folderId) {
  await api("/api/ui/media/move", { method: "POST", body: JSON.stringify({ sha256, folder_id: folderId }) });
  state.toast = "已放入文件夹";
  await renderMedia();
  updateShell();
  clearToastSoon();
}

async function trashMediaItem(type, id, sourceNode = null) {
  if (!id) return;
  if (sourceNode) await animateTrashDrop(sourceNode);
  await api("/api/ui/media/trash", { method: "POST", body: JSON.stringify({ item_type: type, item_id: id }) });
  state.toast = "已放入回收站";
  if (state.mediaMode === "folder" && type === "folder") closeMediaFolder();
  await renderMedia();
  updateShell();
  clearToastSoon();
}

async function restoreMediaItem(type, id) {
  await api("/api/ui/media/restore", { method: "POST", body: JSON.stringify({ item_type: type, item_id: id }) });
  state.toast = "已恢复";
  await renderMedia();
  updateShell();
  clearToastSoon();
}

async function deleteMediaItem(type, id) {
  if (!id) return;
  const label = type === "folder" ? "这个文件夹和里面的图片" : "这张图片";
  if (!(await confirmAction(`彻底删除${label}？这个操作不能恢复。`))) return;
  await api("/api/ui/media/delete", { method: "POST", body: JSON.stringify({ item_type: type, item_id: id }) });
  state.toast = "已彻底删除";
  await renderMedia();
  updateShell();
  clearToastSoon();
}

async function openMediaOriginal() {
  if (!state.selectedMedia?.url) return;
  if (PLUGIN_PAGE_BRIDGE && state.selectedMedia.sha256) {
    await pluginBridgeReady;
    await PLUGIN_PAGE_BRIDGE.download(
      "ui/media",
      { digest: state.selectedMedia.sha256, filename: state.selectedMedia.original_name || state.selectedMedia.sha256 },
      state.selectedMedia.original_name || state.selectedMedia.sha256,
    );
    return;
  }
  window.open(state.selectedMedia.url, "_blank", "noopener,noreferrer");
}

function animateTrashDrop(sourceNode) {
  const trash = document.querySelector("[data-media-trash-zone]");
  if (!trash || !sourceNode) return Promise.resolve();
  const from = sourceNode.getBoundingClientRect();
  const to = trash.getBoundingClientRect();
  const clone = sourceNode.cloneNode(true);
  clone.classList.add("trash-fly-clone");
  clone.style.left = `${from.left}px`;
  clone.style.top = `${from.top}px`;
  clone.style.width = `${from.width}px`;
  clone.style.height = `${from.height}px`;
  document.body.appendChild(clone);
  const dx = to.left + to.width / 2 - (from.left + from.width / 2);
  const dy = to.top + to.height / 2 - (from.top + from.height / 2);
  return new Promise((resolve) => {
    clone.animate(
      [
        { transform: "translate(0, 0) scale(1)", opacity: 0.92 },
        { transform: `translate(${dx}px, ${dy}px) scale(0.12) rotate(12deg)`, opacity: 0.18 },
      ],
      { duration: 360, easing: "cubic-bezier(.2,.8,.2,1)" }
    ).addEventListener("finish", () => {
      clone.remove();
      resolve();
    });
  });
}

function clearToastSoon() {
  window.setTimeout(() => {
    state.toast = "";
    updateShell();
  }, 1800);
}

function bindMediaInteractions() {
  const workspace = panel("media");
  let dragPayload = null;
  workspace.querySelectorAll("[data-media-item]").forEach((node) => {
    node.draggable = !state.mediaFloating;
    node.querySelectorAll("img").forEach((img) => {
      img.draggable = false;
    });
    node.addEventListener("dragstart", (event) => {
      if (state.mediaFloating) {
        event.preventDefault();
        return;
      }
      dragPayload = { type: node.dataset.mediaItem, id: node.dataset.mediaId };
      event.dataTransfer.setData("application/json", JSON.stringify(dragPayload));
      event.dataTransfer.effectAllowed = "move";
      state.mediaSuppressClickUntil = Date.now() + 800;
      node.classList.add("dragging");
    });
    node.addEventListener("dragend", () => {
      state.mediaSuppressClickUntil = Date.now() + 450;
      node.classList.remove("dragging");
      clearMediaDropHighlights();
    });
  });
  workspace.querySelectorAll("[data-media-folder-drop]").forEach((folder) => {
    folder.addEventListener("dragover", (event) => {
      const payload = mediaDragPayload(event, dragPayload);
      if (payload?.type !== "asset" || !folder.classList.contains("expanded")) return;
      event.preventDefault();
      folder.classList.add("drop-ready");
    });
    folder.addEventListener("dragleave", () => folder.classList.remove("drop-ready"));
    folder.addEventListener("drop", async (event) => {
      const payload = mediaDragPayload(event, dragPayload);
      if (payload?.type !== "asset" || !folder.classList.contains("expanded")) return;
      event.preventDefault();
      event.stopPropagation();
      folder.classList.remove("drop-ready");
      await moveMediaToFolder(payload.id, folder.dataset.mediaFolderDrop);
    });
  });
  workspace.addEventListener("dragover", (event) => {
    const payload = mediaDragPayload(event, dragPayload);
    if (!payload) return;
    updateMediaDropHighlights({
      x: event.clientX,
      y: event.clientY,
      type: payload.type || "",
    });
    if (pointInsideTrashZone({ x: event.clientX, y: event.clientY })) event.preventDefault();
  });
  workspace.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget || !workspace.contains(event.relatedTarget)) clearMediaDropHighlights();
  });
  workspace.addEventListener("drop", async (event) => {
    if (event.defaultPrevented) return;
    const payload = mediaDragPayload(event, dragPayload);
    if (!payload) return;
    const point = { x: event.clientX, y: event.clientY };
    if (!pointInsideTrashZone(point)) return;
    event.preventDefault();
    clearMediaDropHighlights();
    const sourceNode = workspace.querySelector(`[data-media-item="${CSS.escape(payload.type || "")}"][data-media-id="${CSS.escape(payload.id || "")}"]`);
    await trashMediaItem(payload.type, payload.id, sourceNode);
  });
  workspace.querySelector("[data-media-trash-zone]")?.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.currentTarget.classList.add("drop-ready");
  });
  workspace.querySelector("[data-media-trash-zone]")?.addEventListener("dragleave", (event) => {
    event.currentTarget.classList.remove("drop-ready");
  });
  workspace.querySelector("[data-media-trash-zone]")?.addEventListener("drop", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.classList.remove("drop-ready");
    const payload = mediaDragPayload(event, dragPayload);
    const sourceNode = workspace.querySelector(`[data-media-item="${CSS.escape(payload?.type || "")}"][data-media-id="${CSS.escape(payload?.id || "")}"]`);
    if (payload) await trashMediaItem(payload.type, payload.id, sourceNode);
  });
  const mediaWorkspace = workspace.querySelector("[data-media-workspace]");
  mediaWorkspace?.addEventListener("dragover", (event) => {
    if (state.mediaMode !== "folder") return;
    event.preventDefault();
    maybeAutoLeaveFolder(event, dragPayload);
  });
  mediaWorkspace?.addEventListener("drop", async (event) => {
    if (state.mediaMode !== "folder") return;
    const shell = event.target.closest("[data-media-gallery]");
    const payload = mediaDragPayload(event, dragPayload);
    if (!payload || payload.type !== "asset") return;
    if (!shell || !shell.contains(event.target) || event.target === mediaWorkspace) {
      event.preventDefault();
      await moveMediaToFolder(payload.id, "");
      closeMediaFolder();
    }
  });
}

function mediaDragPayload(event, fallback) {
  try {
    return JSON.parse(event.dataTransfer.getData("application/json"));
  } catch (_) {
    return fallback;
  }
}

let mediaFolderLeaveTimer = 0;

function maybeAutoLeaveFolder(event, payload = null) {
  const gallery = document.querySelector("[data-media-gallery]");
  if (!gallery) return;
  const rect = gallery.getBoundingClientRect();
  const nearEdge =
    event.clientX < rect.left + 20 ||
    event.clientX > rect.right - 20 ||
    event.clientY < rect.top + 20 ||
    event.clientY > rect.bottom - 20;
  if (!nearEdge) {
    window.clearTimeout(mediaFolderLeaveTimer);
    mediaFolderLeaveTimer = 0;
    return;
  }
  if (mediaFolderLeaveTimer) return;
  mediaFolderLeaveTimer = window.setTimeout(async () => {
    if (state.mediaMode === "folder" && payload?.type === "asset") {
      await moveMediaToFolder(payload.id, "");
      closeMediaFolder();
    } else if (state.mediaMode === "folder") {
      closeMediaFolder();
    }
    mediaFolderLeaveTimer = 0;
  }, 650);
}

let mediaFloatFrame = 0;
let mediaFloatItems = [];
let mediaFloatDrag = null;
let mediaFloatRetry = 0;

function startMediaFloat() {
  stopMediaFloat();
  const gallery = document.querySelector("[data-media-gallery]");
  if (!gallery) return;
  const bounds = gallery.getBoundingClientRect();
  if (bounds.width < 80 || bounds.height < 80) {
    mediaFloatRetry = requestAnimationFrame(startMediaFloat);
    return;
  }
  mediaFloatItems = Array.from(gallery.querySelectorAll("[data-media-item]"))
    .filter((node) => !node.classList.contains("expanded"))
    .map((node, index) => {
    const rect = node.getBoundingClientRect();
    const key = mediaFloatKey(node);
    const saved = state.mediaFloatPositions[key] || null;
    const gridX = Math.max(0, rect.left - bounds.left);
    const gridY = Math.max(0, rect.top - bounds.top);
    const x = saved ? Number(saved.x || 0) : gridX;
    const y = saved ? Number(saved.y || 0) : gridY;
    const item = {
      node,
      key,
      x,
      y,
      vx: saved ? Number(saved.vx || 0.12) : (index % 2 ? 0.12 : -0.1),
      vy: saved ? Number(saved.vy || 0.1) : (index % 3 ? 0.09 : -0.08),
      w: rect.width || 210,
      h: rect.height || 180,
      locked: false,
    };
    item.x = Math.max(0, Math.min(item.x, Math.max(0, bounds.width - item.w)));
    item.y = Math.max(0, Math.min(item.y, Math.max(0, bounds.height - item.h)));
    node.dataset.floatReady = "true";
    node.style.transform = `translate(${item.x}px, ${item.y}px)`;
    node.addEventListener("pointerdown", startFloatDrag);
    return item;
  });
  gallery.querySelectorAll("[data-media-item].expanded").forEach((node) => {
    const key = mediaFloatKey(node);
    const saved = state.mediaFloatPositions[key] || null;
    const rect = node.getBoundingClientRect();
    const x = saved ? Number(saved.x || 0) : Math.max(0, rect.left - bounds.left);
    const y = saved ? Number(saved.y || 0) : Math.max(0, rect.top - bounds.top);
    node.dataset.floatReady = "locked";
    node.style.transform = `translate(${Math.max(0, Math.min(x, Math.max(0, bounds.width - rect.width)))}px, ${Math.max(0, y)}px)`;
  });
  mediaFloatFrame = requestAnimationFrame(tickMediaFloat);
}

function stopMediaFloat() {
  if (mediaFloatFrame) cancelAnimationFrame(mediaFloatFrame);
  if (mediaFloatRetry) cancelAnimationFrame(mediaFloatRetry);
  mediaFloatFrame = 0;
  mediaFloatRetry = 0;
  mediaFloatDrag = null;
  mediaFloatItems.forEach((item) => {
    item.node.removeEventListener("pointerdown", startFloatDrag);
    item.node.style.transform = "";
    item.node.dataset.floatReady = "false";
  });
  mediaFloatItems = [];
}

function tickMediaFloat() {
  const gallery = document.querySelector("[data-media-gallery]");
  if (!gallery) return;
  const bounds = gallery.getBoundingClientRect();
  if (bounds.width < 80 || bounds.height < 80) {
    mediaFloatFrame = requestAnimationFrame(tickMediaFloat);
    return;
  }
  for (const item of mediaFloatItems) {
    if (mediaFloatDrag?.item === item || item.locked) continue;
    item.x += item.vx;
    item.y += item.vy;
    item.vx *= 0.996;
    item.vy *= 0.996;
    if (Math.abs(item.vx) < 0.035) item.vx += item.vx >= 0 ? 0.008 : -0.008;
    if (Math.abs(item.vy) < 0.03) item.vy += item.vy >= 0 ? 0.007 : -0.007;
    if (item.x < 0 || item.x + item.w > bounds.width) {
      item.vx *= -0.88;
      item.x = Math.max(0, Math.min(item.x, bounds.width - item.w));
    }
    if (item.y < 0 || item.y + item.h > bounds.height) {
      item.vy *= -0.88;
      item.y = Math.max(0, Math.min(item.y, bounds.height - item.h));
    }
  }
  for (let i = 0; i < mediaFloatItems.length; i += 1) {
    for (let j = i + 1; j < mediaFloatItems.length; j += 1) {
      if (mediaFloatItems[i].locked || mediaFloatItems[j].locked) continue;
      collideFloatItems(mediaFloatItems[i], mediaFloatItems[j]);
    }
  }
  mediaFloatItems.forEach((item) => {
    item.node.dataset.floatX = String(item.x);
    item.node.dataset.floatY = String(item.y);
    state.mediaFloatPositions[item.key] = { x: item.x, y: item.y, vx: item.vx, vy: item.vy };
    item.node.style.transform = `translate(${item.x}px, ${item.y}px)`;
  });
  mediaFloatFrame = requestAnimationFrame(tickMediaFloat);
}

function mediaFloatKey(node) {
  return `${node.dataset.mediaItem || "item"}:${node.dataset.mediaId || ""}`;
}

function collideFloatItems(a, b) {
  const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (overlapX <= 0 || overlapY <= 0) return;
  const pushX = overlapX / 2 + 0.5;
  const pushY = overlapY / 2 + 0.5;
  if (overlapX < overlapY) {
    const dir = a.x < b.x ? -1 : 1;
    a.x += dir * pushX;
    b.x -= dir * pushX;
    const av = a.vx;
    a.vx = b.vx * 0.9;
    b.vx = av * 0.9;
  } else {
    const dir = a.y < b.y ? -1 : 1;
    a.y += dir * pushY;
    b.y -= dir * pushY;
    const av = a.vy;
    a.vy = b.vy * 0.9;
    b.vy = av * 0.9;
  }
}

function startFloatDrag(event) {
  if (!state.mediaFloating) return;
  if (event.target.closest("a, input, textarea, select, [data-media-restore], [data-media-delete]")) return;
  const item = mediaFloatItems.find((candidate) => candidate.node === event.currentTarget);
  if (!item) return;
  event.preventDefault();
  event.currentTarget.setPointerCapture(event.pointerId);
  mediaFloatDrag = {
    item,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    itemX: item.x,
    itemY: item.y,
    lastX: event.clientX,
    lastY: event.clientY,
    lastTime: performance.now(),
    moved: false,
  };
  item.vx = 0;
  item.vy = 0;
  event.currentTarget.classList.add("dragging");
  event.currentTarget.addEventListener("pointermove", moveFloatDrag);
  event.currentTarget.addEventListener("pointerup", endFloatDrag, { once: true });
  event.currentTarget.addEventListener("pointercancel", endFloatDrag, { once: true });
}

function moveFloatDrag(event) {
  if (!mediaFloatDrag) return;
  const drag = mediaFloatDrag;
  const now = performance.now();
  drag.item.x = drag.itemX + event.clientX - drag.startX;
  drag.item.y = drag.itemY + event.clientY - drag.startY;
  drag.item.vx = ((event.clientX - drag.lastX) / Math.max(16, now - drag.lastTime)) * 12;
  drag.item.vy = ((event.clientY - drag.lastY) / Math.max(16, now - drag.lastTime)) * 12;
  if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 6) {
    drag.moved = true;
    state.mediaSuppressClickUntil = Date.now() + 700;
  }
  if (state.mediaMode === "folder") {
    maybeAutoLeaveFolder(event, {
      type: drag.item.node.dataset.mediaItem || "",
      id: drag.item.node.dataset.mediaId || "",
    });
  }
  drag.overTrash = pointInsideAnyTrashZonePoint(
    { x: event.clientX, y: event.clientY },
    floatItemCenter(drag.item)
  ) || floatItemIntersectsTrashZone(drag.item);
  updateMediaDropHighlights({
    x: event.clientX,
    y: event.clientY,
    altX: floatItemCenter(drag.item)?.x,
    altY: floatItemCenter(drag.item)?.y,
    trashReady: drag.overTrash,
    type: drag.item.node.dataset.mediaItem || "",
  });
  drag.lastX = event.clientX;
  drag.lastY = event.clientY;
  drag.lastTime = now;
}

async function endFloatDrag(event) {
  event.currentTarget.removeEventListener("pointermove", moveFloatDrag);
  const drag = mediaFloatDrag;
  if (!drag?.item) {
    mediaFloatDrag = null;
    return;
  }
  event.currentTarget.classList.remove("dragging");
  try {
    event.currentTarget.releasePointerCapture(drag.pointerId);
  } catch (_) {}
  drag.item.vx = Math.max(-0.55, Math.min(0.55, drag.item.vx));
  drag.item.vy = Math.max(-0.48, Math.min(0.48, drag.item.vy));
  if (drag.moved) state.mediaSuppressClickUntil = Date.now() + 900;
  const node = drag.item.node;
  const type = node.dataset.mediaItem || "";
  const id = node.dataset.mediaId || "";
  const point = { x: event.clientX, y: event.clientY };
  mediaFloatDrag = null;
  if (!drag.moved) {
    state.mediaSuppressClickUntil = Date.now() + 450;
    if (type === "asset") openMediaDetail(id);
    if (type === "folder" && state.mediaFloating) {
      captureMediaFloatPositions();
      toggleExpandedMediaFolder(id);
      renderMedia();
    }
    return;
  }
  clearMediaDropHighlights();
  const itemCenter = floatItemCenter(drag.item);
  const lastPoint = { x: drag.lastX, y: drag.lastY };
  if (drag.overTrash || pointInsideAnyTrashZonePoint(point, lastPoint, itemCenter) || floatItemIntersectsTrashZone(drag.item)) {
    await trashMediaItem(type, id, node);
    return;
  }
  if (type === "asset") {
    const folderDrop = Array.from(document.querySelectorAll("[data-media-folder-drop].expanded")).find((folder) =>
      pointInsideElement(point, folder)
    );
    if (folderDrop?.dataset.mediaFolderDrop) {
      await moveMediaToFolder(id, folderDrop.dataset.mediaFolderDrop);
      return;
    }
    if (state.mediaMode === "folder") {
      const folderSpace = document.querySelector("[data-media-workspace]");
      if (folderSpace && !pointInsideElement(point, folderSpace)) {
        await moveMediaToFolder(id, "");
        closeMediaFolder();
      }
    }
  }
}

function pointInsideElement(point, element) {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  return point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom;
}

function pointInsideTrashZone(point) {
  const rect = trashZoneRect();
  if (!rect) return false;
  return point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom;
}

function trashZoneRect() {
  const zone = document.querySelector("[data-media-trash-zone]");
  if (!zone) return null;
  const bounds = zone.getBoundingClientRect();
  const pad = 26;
  return {
    left: bounds.left - pad,
    right: bounds.right + pad,
    top: bounds.top - pad,
    bottom: bounds.bottom + pad,
  };
}

function pointInsideAnyTrashZonePoint(...points) {
  return points.filter(Boolean).some((point) => pointInsideTrashZone(point));
}

function floatItemIntersectsTrashZone(item) {
  const trash = trashZoneRect();
  const itemRect = floatItemRect(item);
  if (!trash || !itemRect) return false;
  return itemRect.left <= trash.right && itemRect.right >= trash.left && itemRect.top <= trash.bottom && itemRect.bottom >= trash.top;
}

function floatItemRect(item) {
  const gallery = document.querySelector("[data-media-gallery]");
  if (!item || !gallery) return null;
  const rect = gallery.getBoundingClientRect();
  return {
    left: rect.left + item.x,
    right: rect.left + item.x + item.w,
    top: rect.top + item.y,
    bottom: rect.top + item.y + item.h,
  };
}

function floatItemCenter(item) {
  const gallery = document.querySelector("[data-media-gallery]");
  if (!item || !gallery) return null;
  const rect = gallery.getBoundingClientRect();
  return {
    x: rect.left + item.x + item.w / 2,
    y: rect.top + item.y + item.h / 2,
  };
}

function clearMediaDropHighlights() {
  document.querySelector("[data-media-trash-zone]")?.classList.remove("drop-ready");
  document.querySelectorAll("[data-media-folder-drop].drop-ready").forEach((node) => node.classList.remove("drop-ready"));
}

function updateMediaDropHighlights(point) {
  const trash = document.querySelector("[data-media-trash-zone]");
  const altPoint = Number.isFinite(point.altX) && Number.isFinite(point.altY) ? { x: point.altX, y: point.altY } : null;
  trash?.classList.toggle("drop-ready", Boolean(point.trashReady) || pointInsideAnyTrashZonePoint(point, altPoint));
  document.querySelectorAll("[data-media-folder-drop]").forEach((folder) => {
    const ready = point.type === "asset" && folder.classList.contains("expanded") && pointInsideElement(point, folder);
    folder.classList.toggle("drop-ready", ready);
  });
}

function captureMediaFloatPositions() {
  if (!state.mediaFloating) return;
  const gallery = document.querySelector("[data-media-gallery]");
  if (gallery) {
    const galleryRect = gallery.getBoundingClientRect();
    if (galleryRect.width < 80 || galleryRect.height < 80) return;
  }
  if (mediaFloatItems.length) {
    mediaFloatItems.forEach((item) => {
      state.mediaFloatPositions[item.key] = {
        x: item.x,
        y: item.y,
        vx: 0,
        vy: 0,
      };
    });
    return;
  }
  if (!gallery) return;
  const galleryRect = gallery.getBoundingClientRect();
  gallery.querySelectorAll("[data-media-item]").forEach((node) => {
    const key = mediaFloatKey(node);
    const rect = node.getBoundingClientRect();
    state.mediaFloatPositions[key] = {
      x: rect.left - galleryRect.left,
      y: rect.top - galleryRect.top,
      vx: 0,
      vy: 0,
    };
  });
}

async function animateFloatToGrid() {
  const gallery = document.querySelector("[data-media-gallery]");
  if (!gallery) return;
  const cards = Array.from(gallery.querySelectorAll("[data-media-item]"));
  if (!cards.length) return;
  if (mediaFloatFrame) cancelAnimationFrame(mediaFloatFrame);
  mediaFloatFrame = 0;
  const floating = cards.map((node) => ({ node, rect: node.getBoundingClientRect() }));
  gallery.classList.add("settling");
  gallery.classList.remove("floating");
  gallery.style.minHeight = "";
  cards.forEach((node) => {
    node.removeEventListener("pointerdown", startFloatDrag);
    node.style.transform = "";
    node.dataset.floatReady = "false";
  });
  const settled = new Map(cards.map((node) => [node, node.getBoundingClientRect()]));
  await Promise.all(
    floating.map(({ node, rect }) => {
      const target = settled.get(node);
      if (!target) return Promise.resolve();
      const dx = rect.left - target.left;
      const dy = rect.top - target.top;
      return node
        .animate(
          [
            { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.96 },
            { transform: "translate(0, 0)", opacity: 1 },
          ],
          { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" }
        )
        .finished.catch(() => {});
    })
  );
  gallery.classList.remove("settling");
  mediaFloatDrag = null;
  mediaFloatItems = [];
}

function formatBytes(value = 0) {
  let size = Number(value) || 0;
  const units = ["B", "KB", "MB", "GB"];
  for (const unit of units) {
    if (size < 1024 || unit === "GB") return unit === "B" ? `${Math.round(size)} B` : `${size.toFixed(1)} ${unit}`;
    size /= 1024;
  }
  return `${value} B`;
}

function formatDateTime(value = "") {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

async function renderSettings() {
  const payload = await api("/api/ui/settings");
  state.settings = payload;
  state.notebooks = payload.notebooks || state.notebooks || [];
  const settings = payload.settings;
  if (!["appearance", "modules", "access", "backup"].includes(state.settingsSection)) state.settingsSection = "appearance";
  panel("settings").innerHTML = `
    <section class="settings-layout settings-layout-single">
      <div class="settings-content">
        <section class="settings-panel ${settingsPanelClass("appearance")}" data-settings-panel="appearance">
          ${appearanceSettingsPage(payload)}
        </section>
        <section class="settings-panel ${settingsPanelClass("modules")}" data-settings-panel="modules">
          <form data-action="save-settings">
            ${state.settingsModuleDetail ? moduleDetailPage(payload, state.settingsModuleDetail) : moduleManagerPage(payload)}
          </form>
        </section>
        <section class="settings-panel ${settingsPanelClass("access")}" data-settings-panel="access">
          <section class="settings-page">
            <div class="settings-page-head">
              <h2>访问密钥</h2>
            </div>
            <form class="settings-collapse form" data-action="save-security">
              <div class="form-grid compact">
                <label>新管理员密码<input name="admin_password" type="password" placeholder="留空则不修改"></label>
                <label>外部接口密钥<input name="bot_api_token" value="${escapeHtml(payload.security.bot_api_token || "")}"></label>
              </div>
              <details><summary>外部接口选项</summary>${check("generate_bot_api_token", "保存时生成新的外部接口密钥", false)}${check("external_api_enabled", "启用外部接口", payload.security.external_api_enabled)}</details>
              <div class="actions"><button class="primary">保存访问密钥</button></div>
            </form>
          </section>
        </section>
        <section class="settings-panel ${settingsPanelClass("backup")}" data-settings-panel="backup">
          <section class="settings-page">
            <div class="settings-page-head">
              <h2>导入导出</h2>
            </div>
            <div class="settings-collapse form">
              ${versionMaintenance(payload)}
              <form class="backup-export-form" data-action="export-backup">
                <div>
                  <h3>导出范围</h3>
                  <div class="choice-grid choice-grid-export">
                    ${exportOptions(payload.module_catalog)}
                  </div>
                </div>
                <div class="form-grid compact backup-inline-fields">
                  <label>模块 ID<input name="module_id" placeholder="导出自定义模块或拓展包时填写"></label>
                </div>
                ${check("include_security", "包含管理员密码和接口密钥", false)}
                <div class="actions"><button class="primary">导出所选范围</button></div>
              </form>
              <form class="upload-zone" data-action="import-backup">
                <input name="backup_file" type="file" accept=".zip" required>
                <label class="compact-select-label">导入策略<select name="strategy"><option value="safe">安全合并：已有文件跳过</option><option value="overwrite">覆盖合并：先备份再覆盖</option></select></label>
                <div class="actions"><button class="primary">导入备份包</button></div>
                <p class="muted">导入会读取清单，自动识别完整备份、日记、人物印象、媒体、个性化前端、自定义模块或拓展包。</p>
              </form>
            </div>
          </section>
        </section>
      </div>
    </section>
  `;
  panel("settings").querySelector('[data-action="save-settings"]').addEventListener("submit", saveSettings);
  panel("settings").querySelector('[data-action="save-appearance"]')?.addEventListener("submit", saveSettings);
  panel("settings").querySelector('[data-action="save-security"]').addEventListener("submit", saveSecurity);
  panel("settings").querySelector('[data-action="export-backup"]').addEventListener("submit", exportBackup);
  panel("settings").querySelector('[data-action="import-backup"]').addEventListener("submit", importBackup);
  renderGlobalDialogs();
}

function settingsTab(id, label) {
  return `
    <button class="settings-tab ${state.settingsSection === id ? "active" : ""}" data-settings-section="${id}" data-tour-target="settings-${id}" type="button">
      <span>${escapeHtml(label)}</span>
    </button>
  `;
}

function settingsPanelClass(id) {
  return state.settingsSection === id ? "active" : "";
}

async function switchSettingsSection(id) {
  state.view = "settings";
  state.settingsMenuOpen = true;
  state.settingsSection = id;
  state.settingsModuleDetail = "";
  syncRouteForState("settings");
  await renderSettings();
  updateShell();
}

async function openModuleSettings(id) {
  if (id === "webui") {
    state.settingsSection = "appearance";
    state.settingsModuleDetail = "";
    syncRouteForState("settings");
    await renderSettings();
    updateShell();
    return;
  }
  state.settingsSection = "modules";
  state.settingsModuleDetail = id || "";
  syncRouteForState("settings");
  await renderSettings();
  updateShell();
}

function appearanceSettingsPage(payload) {
  const settings = payload.settings;
  return `
    <section class="settings-page appearance-page">
      <div class="settings-page-head">
        <div>
          <h2>外观设置</h2>
          <p class="muted">设置小窝标题、头像和页面风格。这里的选项都会真实保存。</p>
        </div>
        <button class="button ghost" data-onboarding-replay type="button">重新查看引导</button>
      </div>
      <form class="settings-collapse form" data-action="save-appearance">
        ${webuiAppearanceBody(payload, "page")}
        <div class="module-detail-actions appearance-actions">
          <button class="primary">保存外观</button>
        </div>
      </form>
    </section>
  `;
}

async function closeModuleSettings() {
  state.settingsModuleDetail = "";
  state.notebookDeleteIds = [];
  state.t2iTemplateDialogOpen = false;
  state.t2iCustomOpen = false;
  syncRouteForState("settings");
  await renderSettings();
  updateShell();
}

async function setModuleFilter(filter) {
  state.moduleFilter = filter || "all";
  await renderSettings();
  updateShell();
}

function moduleManagerPage(payload) {
  const settings = payload.settings;
  const modules = moduleCatalogItems(payload, settings);
  const visibleModules = state.moduleFilter === "all" ? modules : modules.filter((module) => module.category === state.moduleFilter);
  return `
    <section class="module-manager-page">
      <div class="module-page-head">
        <div>
          <h2>模块控制台</h2>
          <p class="muted">管理小窝里的功能模块、外观样式和拓展包。</p>
        </div>
        <button class="button primary" data-module-install-open type="button">从链接安装</button>
      </div>
      ${moduleWarnings(payload.module_catalog.conflicts || [])}
      ${moduleWarnings(payload.module_catalog.appearance_conflicts || [])}
      <div class="module-stats">
        ${moduleStat("全部模块", modules.length)}
        ${moduleStat("已启用", modules.filter((item) => item.enabled).length)}
        ${moduleStat("外观模块", modules.filter((item) => item.category === "appearance").length)}
      </div>
      <div class="module-filterbar">
        ${moduleFilterButton("all", "全部", modules.length)}
        ${moduleFilterButton("core", "功能模块", modules.filter((item) => item.category === "core").length)}
        ${moduleFilterButton("appearance", "外观模块", modules.filter((item) => item.category === "appearance").length)}
        ${moduleFilterButton("extension", "拓展包", modules.filter((item) => item.category === "extension").length)}
      </div>
      <div class="module-card-grid module-card-grid-standalone">
        ${moduleHiddenInputs(modules)}
        ${visibleModules.length ? visibleModules.map((module) => moduleCard(module, module.enabled ? [module.id] : [], module.inputName, module.groupKind)).join("") : `<p class="muted module-empty">暂无模块。</p>`}
      </div>
    </section>
  `;
}

function moduleCatalogItems(payload, settings) {
  const official = payload.module_catalog.official || [];
  const supplementalAppearance = (payload.module_catalog.appearance || []).filter((module) => module.appearance_mode !== "global");
  return [
    ...decorateModules(official, settings.enabled_official_modules, "enabled_official_modules", "official", "core"),
    ...decorateModules(payload.module_catalog.custom || [], settings.enabled_custom_modules, "enabled_custom_modules", "custom", "core"),
    ...decorateModules(payload.module_catalog.extensions || [], settings.enabled_custom_extensions, "enabled_custom_extensions", "extension", "extension"),
    ...decorateModules(supplementalAppearance, settings.enabled_appearance_modules || [], "enabled_appearance_modules", "appearance", "appearance"),
  ];
}

function decorateModules(modules, enabled = [], inputName, groupKind, category) {
  return modules.map((module) => ({ ...module, enabled: enabled.includes(module.id), inputName, groupKind, category }));
}

function moduleStat(label, value) {
  return `<div class="module-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function moduleFilterButton(id, label, count) {
  return `
    <button class="module-filter ${state.moduleFilter === id ? "active" : ""}" data-module-filter="${escapeHtml(id)}" type="button">
      <span>${escapeHtml(label)}</span><strong>${escapeHtml(count)}</strong>
    </button>
  `;
}

function moduleHiddenInputs(modules) {
  const names = Array.from(new Set(modules.map((module) => module.inputName)));
  const enabledByName = {};
  const visibleByName = {};
  for (const module of modules) {
    if (!enabledByName[module.inputName]) enabledByName[module.inputName] = [];
    if (!visibleByName[module.inputName]) visibleByName[module.inputName] = new Set();
    if (module.enabled) enabledByName[module.inputName].push(module.id);
    if (state.moduleFilter === "all" || module.category === state.moduleFilter) visibleByName[module.inputName].add(module.id);
  }
  return names
    .map((name) => {
      const visibleName = state.moduleFilter === "all" || modules.some((module) => module.inputName === name && module.category === state.moduleFilter);
      const present = visibleName ? `<input name="__module_group_present" value="${escapeHtml(name)}" type="hidden">` : "";
      const preserved = (enabledByName[name] || [])
        .filter((id) => !visibleByName[name]?.has(id))
        .map((id) => `<input name="${escapeHtml(name)}" value="${escapeHtml(id)}" type="hidden" data-preserved-module="${escapeHtml(id)}">`)
        .join("");
      return present + preserved;
    })
    .join("");
}

function moduleDetailPage(payload, detailKey) {
  const settings = payload.settings;
  const module = findModuleByDetailKey(payload.module_catalog, detailKey);
  const title = module?.name || moduleDetailTitle(detailKey);
  const description = module?.description || moduleDetailDescription(detailKey);
  return `
    <article class="module-detail-card">
      <div class="module-detail-head">
        <button class="button module-detail-back" data-settings-back type="button">返回模块管理</button>
        <div>
          <h2>${escapeHtml(title)}</h2>
          ${description ? `<p class="muted">${escapeHtml(description)}</p>` : ""}
        </div>
      </div>
      <div class="module-detail-body form">
        ${moduleSettingsBody(payload, detailKey)}
      </div>
      <div class="module-detail-actions">
        <button class="button" data-settings-back type="button">取消</button>
        <button class="primary" data-save-close>保存并关闭</button>
      </div>
    </article>
  `;
}

function notebookOriginParts(item = {}) {
  const origin = String(item.origin_umo || "");
  const parts = origin.split(":");
  return {
    platform_id: item.platform_id || parts[0] || "aiocqhttp",
    message_type: item.message_type || parts[1] || "group",
    session_id: item.session_id || parts.slice(2).join(":") || "",
  };
}

function notebookMessageTypeFamily(messageType = "") {
  const normalized = String(messageType || "").trim().toLowerCase();
  if (!normalized) return "group";
  if (normalized.includes("private") || normalized.includes("friend")) return "private";
  if (normalized.includes("group") || normalized.includes("guild") || normalized.includes("channel")) return "group";
  return normalized;
}

function notebookMessageTypeLabel(messageType = "") {
  const family = notebookMessageTypeFamily(messageType);
  if (family === "private") return "私聊";
  if (family === "group") return "群聊";
  return messageType || "自动识别";
}

function notebookRow(item, options = {}) {
  const id = item.id || item.notebook_id || options.id || `notebook_${Date.now()}`;
  const origin = notebookOriginParts(item);
  const isDefault = id === "default";
  const messageType = origin.message_type || "group";
  return `
    <div class="notebook-row" data-notebook-row="${escapeHtml(id)}">
      <input name="notebook_id" value="${escapeHtml(id)}" type="hidden">
      <input name="notebook_platform_${escapeHtml(id)}" value="${escapeHtml(origin.platform_id)}" type="hidden">
      <input name="notebook_message_type_${escapeHtml(id)}" value="${escapeHtml(messageType)}" type="hidden">
      <label>名称<input name="notebook_name_${escapeHtml(id)}" value="${escapeHtml(item.name || (options.draft ? "新日记本" : id))}" placeholder="例如：主群日记本"></label>
      <label>协议类型<input value="${escapeHtml(notebookMessageTypeLabel(messageType))}" readonly title="${escapeHtml(messageType)}"></label>
      <label>会话 ID<input name="notebook_session_${escapeHtml(id)}" value="${escapeHtml(origin.session_id)}" placeholder="建议在目标会话让 bot 绑定"></label>
      <label>写日记时间<input name="notebook_archive_time_${escapeHtml(id)}" type="time" value="${escapeHtml(item.archive_time || "03:00")}"></label>
      <label>推送目标<select name="notebook_push_target_${escapeHtml(id)}"><option value="none" ${item.push_target === "none" ? "selected" : ""}>不推送</option><option value="admin_private" ${item.push_target === "admin_private" ? "selected" : ""}>管理员私聊</option><option value="source" ${item.push_target === "source" ? "selected" : ""}>原会话</option><option value="both" ${item.push_target === "both" ? "selected" : ""}>两边都推送</option></select></label>
      <label class="check"><input name="notebook_enabled_${escapeHtml(id)}" type="checkbox" ${item.enabled !== false ? "checked" : ""}>启用</label>
      <label class="check"><input name="notebook_auto_archive_${escapeHtml(id)}" type="checkbox" ${item.auto_archive_enabled === true ? "checked" : ""}>自动写日记</label>
      <button class="button danger notebook-delete" data-notebook-delete="${escapeHtml(id)}" ${isDefault ? "disabled" : ""} type="button">${isDefault ? "默认" : "删除"}</button>
    </div>
  `;
}

function notebookManagement(notebooks = []) {
  const items = notebooks.length ? notebooks : notebookOptions();
  const rows = items.map((raw) => notebookRow({ ...raw, id: raw.id || raw.notebook_id || "default" })).join("");
  return `
    <div class="notebook-settings">
      <div class="settings-mini-head notebook-head">
        <div><strong>日记本管理</strong><span>协议类型自动识别；更推荐在目标会话让 bot 绑定日记本。</span></div>
        <button class="button primary" data-notebook-add type="button">新增日记本</button>
      </div>
      <div class="notebook-list" data-notebook-list>
        ${rows || `<div class="notice soft">还没有日记本。</div>`}
      </div>
    </div>
  `;
}

function permissionSettings(settings) {
  const selected = settings.non_admin_permissions || [];
  const item = (value, label) => `<label class="choice-card permission-choice"><input name="non_admin_permissions" value="${value}" type="checkbox" ${selected.includes(value) ? "checked" : ""}><span><strong>${label}</strong></span></label>`;
  return `
    <details class="permission-subpage">
      <summary>非管理员权限设置</summary>
      <div class="permission-grid">
        ${item("diary_read", "查看日记")}
        ${item("diary_search", "搜索日记")}
        ${item("diary_write", "写入日记")}
        ${item("diary_delete", "删除日记")}
        ${item("media_read", "查看媒体")}
        ${item("media_write", "保存媒体")}
        ${item("media_send", "发送媒体")}
        ${item("impression_read", "查看人物印象")}
        ${item("impression_write", "修改人物印象")}
        ${item("memo_read", "查看备忘录")}
        ${item("memo_write", "写入备忘录")}
        ${item("memo_delete", "删除备忘录")}
      </div>
    </details>
  `;
}

function addNotebookDraft() {
  const list = document.querySelector("[data-notebook-list]");
  if (!list) return;
  list.querySelector(".notice.soft")?.remove();
  const id = `notebook_${Date.now()}`;
  const wrapper = document.createElement("div");
  wrapper.innerHTML = notebookRow({ id, name: "新日记本", enabled: true, auto_archive_enabled: false, push_target: "none" }, { draft: true }).trim();
  list.appendChild(wrapper.firstElementChild);
}

function deleteNotebookRow(id) {
  if (!id || id === "default") return;
  const row = document.querySelector(`[data-notebook-row="${CSS.escape(id)}"]`);
  if (!row) return;
  const form = row.closest("form");
  if (form) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "notebook_delete_id";
    input.value = id;
    input.dataset.notebookDeleteInput = id;
    form.appendChild(input);
  }
  state.notebookDeleteIds = Array.from(new Set([...(state.notebookDeleteIds || []), id]));
  state.notebooks = (state.notebooks || []).filter((item) => (item.id || item.notebook_id) !== id);
  state.toast = "日记本已标记删除，保存后生效";
  row.remove();
  updateShell();
  clearToastSoon();
}

function notebookOriginFromForm(form, id) {
  const session = String(form.get(`notebook_session_${id}`) || "").trim();
  if (!session) return "";
  const platform = String(form.get(`notebook_platform_${id}`) || "aiocqhttp").trim() || "aiocqhttp";
  const messageType = String(form.get(`notebook_message_type_${id}`) || "group").trim() || "group";
  return `${platform}:${messageType}:${session}`;
}

function moduleSettingsBody(payload, detailKey) {
  const settings = payload.settings;
  if (detailKey === "diary") {
    const t2iTemplate = activeT2iTemplate(settings);
    return `
      <div class="setting-line"><div><strong>日记模块</strong><p class="muted">开启后可以记录和查看日记。</p></div>${switchControl("enable_diary_module", settings.enable_diary_module)}</div>
      <div class="setting-line"><div><strong>自动回想</strong><p class="muted">需要时让 bot 参考以前的日记。</p></div>${switchControl("memory_recall_enabled", settings.memory_recall_enabled)}</div>
      <div class="setting-line"><div><strong>自然语言管理员权限</strong><p class="muted">允许管理员用自然语言调整日记和小窝配置。</p></div>${switchControl("permissions_allow_admin_natural_language", settings.permissions_allow_admin_natural_language)}</div>
      <div class="form-grid compact">
        <label>回想方式<select name="memory_recall_policy"><option value="conservative" ${settings.memory_recall_policy === "conservative" ? "selected" : ""}>只在需要时</option><option value="active" ${settings.memory_recall_policy === "active" ? "selected" : ""}>更主动</option></select></label>
        <label>每次参考数量<input name="search_default_top_k" type="number" min="1" max="20" value="${settings.search_default_top_k}"></label>
        <label>摘要长度<input name="search_snippet_chars" type="number" min="80" max="360" value="${settings.search_snippet_chars}"></label>
        <label>推送格式<select name="diary_push_format"><option value="text" ${settings.diary_push_format !== "image" ? "selected" : ""}>文字</option><option value="image" ${settings.diary_push_format === "image" ? "selected" : ""}>图片</option></select></label>
        <label>图片失败重试次数<input name="diary_image_send_max_retries" type="number" min="0" max="10" value="${Number(settings.diary_image_send_max_retries ?? 3)}"></label>
        <label>小窝管理员 QQ<input name="nest_admin_ids" value="${escapeHtml((settings.nest_admin_ids || "").split(/\s+/)[0] || "")}" placeholder="只填一个管理员 QQ"></label>
        <label class="wide-field">写日记要求规范<textarea name="diary_write_prompt">${escapeHtml(settings.diary_write_prompt || "")}</textarea></label>
      </div>
      <div class="setting-line"><div><strong>图片失败提示</strong><p class="muted">图片推送重试后仍失败时，在目标会话发送一条简短提示。</p></div>${switchControl("diary_image_send_failure_notice", settings.diary_image_send_failure_notice ?? true)}</div>
      <input name="diary_t2i_template_name" type="hidden" value="${escapeHtml(settings.diary_t2i_template_name || t2iTemplate.id)}">
      <textarea name="diary_t2i_template" hidden>${escapeHtml(settings.diary_t2i_template || t2iTemplate.template)}</textarea>
      <div class="t2i-template-summary">
        <div><strong>图片推送模板</strong><span>${escapeHtml(t2iTemplate.name)} · ${escapeHtml(t2iTemplate.tone)}</span></div>
        <button class="button" data-t2i-open type="button">选择模板</button>
      </div>
      ${permissionSettings(settings)}
      ${notebookManagement(payload.notebooks || state.notebooks || [])}
    `;
  }
  if (detailKey === "impressions") {
    return `
      <div class="setting-line"><div><strong>人物印象模块</strong><p class="muted">记录人物关系和长期印象。</p></div>${switchControl("enable_impressions_module", settings.enable_impressions_module)}</div>
      <div class="setting-line"><div><strong>日记后自动整理</strong><p class="muted">写完日记后，让 bot 判断是否需要更新人物印象。</p></div>${switchControl("auto_impression_from_diary", settings.auto_impression_from_diary)}</div>
      <div class="form-grid compact">
        <label>写入强度<select name="impression_write_level">
          <option value="off" ${settings.impression_write_level === "off" ? "selected" : ""}>关闭：不自动写印象</option>
          <option value="light" ${settings.impression_write_level === "light" ? "selected" : ""}>轻量：只记录明确变化</option>
          <option value="balanced" ${settings.impression_write_level === "balanced" ? "selected" : ""}>均衡：推荐</option>
          <option value="deep" ${settings.impression_write_level === "deep" ? "selected" : ""}>深入：补充更多细节</option>
        </select></label>
        <label>更新策略<select name="impression_update_strategy">
          <option value="manual" ${settings.impression_update_strategy === "manual" ? "selected" : ""}>手动</option>
          <option value="existing_only" ${settings.impression_update_strategy === "existing_only" ? "selected" : ""}>只更新已有人物</option>
          <option value="evidence_only" ${settings.impression_update_strategy === "evidence_only" ? "selected" : ""}>有证据才更新</option>
          <option value="aggressive" ${settings.impression_update_strategy === "aggressive" ? "selected" : ""}>允许新建人物</option>
        </select></label>
        <label>跨群同人策略<select name="impression_identity_strategy">
          <option value="separate" ${(settings.impression_identity_strategy || "separate") === "separate" ? "selected" : ""}>不收束：按群昵称分开写</option>
          <option value="unified" ${settings.impression_identity_strategy === "unified" ? "selected" : ""}>统一人物：按 QQ 合成总体印象</option>
          <option value="nested" ${settings.impression_identity_strategy === "nested" ? "selected" : ""}>统一人物：按 QQ 挂载不同群印象</option>
        </select></label>
        <label>确认程度<input name="impression_min_confidence" type="number" min="1" max="5" value="${settings.impression_min_confidence || 3}"></label>
      </div>
      ${check("impression_allow_new_people", "允许自动新建人物", settings.impression_allow_new_people)}
      <label>印象写入规范<textarea name="impression_prompt">${escapeHtml(settings.impression_prompt || "")}</textarea></label>
    `;
  }
  if (detailKey === "webui") {
    return webuiAppearanceBody(payload, "module");
  }
  if (detailKey === "media") {
    return `
      <div class="setting-line"><div><strong>媒体模块</strong><p class="muted">开启后可以保存图片、语音和附件。</p></div>${switchControl("enable_media_module", settings.enable_media_module)}</div>
      <div class="setting-line"><div><strong>日记里插入图片</strong><p class="muted">允许日记引用已保存的图片或附件。</p></div>${switchControl("allow_media_refs", settings.allow_media_refs)}</div>
      <div class="setting-line"><div><strong>bot 自动导入媒体</strong><p class="muted">允许 bot 把图片或附件放进小窝。</p></div>${switchControl("media_allow_bot_import", settings.media_allow_bot_import)}</div>
      <div class="form-grid compact">
        <label>每天最多保存<input name="media_max_items_per_day" type="number" min="1" max="500" value="${settings.media_max_items_per_day || 80}"></label>
        <label>12小时图片上限<input name="media_auto_save_limit_12h" type="number" min="1" max="200" value="${settings.media_auto_save_limit_12h || 10}"></label>
        <label>写入限制策略<select name="media_auto_save_policy">
          <option value="admin_only" ${settings.media_auto_save_policy !== "bot_curated" ? "selected" : ""}>只允许管理员保存</option>
          <option value="bot_curated" ${settings.media_auto_save_policy === "bot_curated" ? "selected" : ""}>允许 bot 自主挑选</option>
        </select></label>
        <label>图片保存方式<select name="media_storage_strategy">
          <option value="copy" ${settings.media_storage_strategy !== "move" ? "selected" : ""}>复制：保留原文件</option>
          <option value="move" ${settings.media_storage_strategy === "move" ? "selected" : ""}>剪切：移入小窝</option>
        </select></label>
      </div>
    `;
  }
  if (detailKey === "memos") {
    return `
      <div class="setting-line"><div><strong>备忘录模块</strong><p class="muted">开启后左侧会出现备忘录入口，bot 和人都可以把琐碎但重要的内容记成纸条。</p></div>${switchControl("enable_memos_module", settings.enable_memos_module)}</div>
      <div class="setting-line"><div><strong>敏感内容默认隐藏</strong><p class="muted">账号、密码提示、私人片段等敏感纸条在页面和接口里默认遮住，需要主动显示。</p></div>${switchControl("memos_sensitive_default_hidden", settings.memos_sensitive_default_hidden ?? true)}</div>
      <div class="form-grid compact">
        <label>写入策略<select name="memos_write_policy">
          <option value="admin_only" ${settings.memos_write_policy === "admin_only" ? "selected" : ""}>只允许管理员写入</option>
          <option value="admin_allowed" ${settings.memos_write_policy === "admin_allowed" ? "selected" : ""}>管理员和授权用户写入</option>
          <option value="bot_curated" ${settings.memos_write_policy === "bot_curated" ? "selected" : ""}>允许 bot 自主挑选记录</option>
          <option value="review" ${settings.memos_write_policy === "review" ? "selected" : ""}>允许 bot 记录但标记待复核</option>
        </select></label>
        <label>12小时 bot 写入上限<input name="memos_auto_write_limit_12h" type="number" min="0" max="200" value="${Number(settings.memos_auto_write_limit_12h ?? 12)}"></label>
      </div>
      <div class="notice soft">备忘录适合保存账号提示、聊天片段、名言、待办和 bot 觉得值得留下的短记忆。真正的账号密码建议仍放在专业密码管理器里，小窝只做个人使用场景下的基础保护。</div>
      ${permissionSettings(settings)}
    `;
  }
  const customModule = findModuleByDetailKey(payload.module_catalog, detailKey);
  if (customModule && customModule.kind !== "official") {
    return customModuleSettingsBody(payload, customModule);
  }
  return `<div class="notice soft">这个模块暂时没有可调整的选项。</div>`;
}

/** 自定义模块详情：能力清单 + 侧边栏开关 + 卸载。 */
function customModuleSettingsBody(payload, module) {
  const settings = payload.settings || {};
  const capabilities = module.capabilities || {};
  const errors = module.capability_errors || [];
  const navDeclared = Boolean(capabilities.nav);
  const pageDeclared = Boolean(capabilities.page);
  const hidden = (settings.hidden_module_nav_ids || []).includes(module.id);
  const runtimeLabel = capabilities.runtime === "python" ? "自带后端代码" : "只用小窝前端与存储";

  const capabilityRows = [
    ["运行档位", runtimeLabel],
    ["页面", pageDeclared ? `声明了 ${capabilities.page.entry}` : "没有声明页面"],
    ["侧边栏入口", navDeclared ? `声明为「${capabilities.nav.label}」` : "没有声明（缺省即关闭）"],
    ["通用存储", capabilities.store ? `已开启，单文档上限 ${Math.floor(capabilities.store.max_bytes / 1024)} KB` : "没有声明"],
    ["工具", (module.tools || []).length ? (module.tools || []).join("、") : "没有声明"],
  ];

  return `
    ${errors.length ? `<div class="notice error"><strong>声明校验没通过：</strong>${escapeHtml(errors.join("；"))}</div>` : ""}
    <div class="module-capability-list">
      ${capabilityRows
        .map(([label, value]) => `<div class="setting-line"><div><strong>${escapeHtml(label)}</strong><p class="muted">${escapeHtml(value)}</p></div></div>`)
        .join("")}
    </div>
    ${
      navDeclared
        ? `<div class="setting-line"><div><strong>在侧边栏显示</strong><p class="muted">关掉之后模块仍然可用，只是不出现在左侧入口。</p></div>
             <label class="switch"><input type="checkbox" data-module-nav-toggle="${escapeHtml(module.id)}" ${hidden ? "" : "checked"}><span></span></label>
           </div>`
        : `<div class="notice soft">这个模块没有声明侧边栏入口。只提供工具或数据的模块就该是这样。</div>`
    }
    <div class="setting-line">
      <div><strong>卸载模块</strong><p class="muted">卸载前会把整个模块目录备份到 imports/module-uninstall-backups/。</p></div>
      <div class="actions">
        <button class="button" data-module-uninstall="${escapeHtml(module.id)}" data-keep-data="1" type="button">卸载并保留数据</button>
        <button class="button danger" data-module-uninstall="${escapeHtml(module.id)}" type="button">完全卸载</button>
      </div>
    </div>
  `;
}

function moduleDetailTitle(detailKey) {
  if (detailKey === "diary") return "日记模块";
  if (detailKey === "impressions") return "人物印象模块";
  if (detailKey === "media") return "媒体模块";
  if (detailKey === "memos") return "备忘录模块";
  if (detailKey === "webui") return "小窝 WebUI";
  return detailKey;
}

function moduleDetailDescription(detailKey) {
  if (detailKey === "webui") return "管理标题、头像和页面样式。";
  if (detailKey === "memos") return "管理纸条、敏感隐藏和 bot 自主记录策略。";
  return "";
}

function findModuleByDetailKey(catalog, detailKey) {
  const all = [
    ...(catalog.official || []).map((item) => ({ ...item, detailKey: item.id })),
    ...(catalog.custom || []).map((item) => ({ ...item, detailKey: `custom:${item.id}` })),
    ...(catalog.extensions || []).map((item) => ({ ...item, detailKey: `extension:${item.id}` })),
    ...(catalog.appearance || []).map((item) => ({ ...item, detailKey: `appearance:${item.id}` })),
  ];
  return all.find((item) => item.detailKey === detailKey || item.id === detailKey);
}

function check(name, label, checked) {
  return `<label class="check"><input name="${name}" type="checkbox" ${checked ? "checked" : ""}>${escapeHtml(label)}</label>`;
}

function switchControl(name, checked) {
  return `<label class="switch"><input name="${name}" type="checkbox" ${checked ? "checked" : ""}><span></span></label>`;
}

function moduleWarnings(conflicts) {
  if (!conflicts.length) return "";
  return `<div class="module-warnings">${conflicts.map((item) => `<div class="notice ${item.level === "danger" ? "error" : "soft"}"><strong>${escapeHtml(item.title)}：</strong>${escapeHtml(item.message)}</div>`).join("")}</div>`;
}

function moduleCard(module, enabled = [], inputName, groupKind = "") {
  const detailKey = groupKind === "custom" ? `custom:${module.id}` : groupKind === "extension" ? `extension:${module.id}` : groupKind === "appearance" ? `appearance:${module.id}` : module.id;
  const checked = enabled.includes(module.id);
  return `
    <article class="module-card ${checked ? "enabled" : ""}">
      <div class="module-card-icon">${iconImg(moduleIcon(module, groupKind), module.name || module.id)}</div>
      <div class="module-card-main">
        <div class="module-card-title">
          <strong>${escapeHtml(module.name || module.id)}</strong>
          <span class="module-status ${checked ? "on" : "off"}">${checked ? "运行中" : "已停用"}</span>
        </div>
        <em>${escapeHtml(module.description || "没有说明。")}</em>
        <span class="chips small">${moduleBadges(module, groupKind).join("")}</span>
      </div>
      <div class="module-card-actions">
        <button class="button" data-module-settings="${escapeHtml(detailKey)}" type="button">设置</button>
        <label class="module-card-toggle ${checked ? "on" : "off"}">
          <input name="${inputName}" value="${escapeHtml(module.id)}" type="checkbox" data-module-toggle data-module-id="${escapeHtml(module.id)}" data-module-input="${escapeHtml(inputName)}" ${checked ? "checked" : ""}>
          <span>${checked ? "已开启" : "已关闭"}</span>
        </label>
      </div>
    </article>
  `;
}

function moduleIcon(module, groupKind = "") {
  if (module.id === "diary") return "diary";
  if (module.id === "impressions") return "impressions";
  if (module.id === "media") return "media";
  if (module.id === "memos") return "memos";
  if (module.id === "webui") return "webui";
  if (groupKind === "appearance") return "appearance";
  if (groupKind === "extension") return "modules";
  return "modules";
}

function moduleBadges(module, groupKind = "") {
  const conflicts = module.conflicts_with || [];
  const isAppearance = groupKind === "appearance";
  const appearanceLabel = isAppearance ? (module.entry_label || (module.appearance_mode === "global" ? "全局模块" : "外观模块")) : "";
  const badges = [
    `<span class="chip">${escapeHtml(moduleSourceLabel(module, groupKind))}</span>`,
    module.id === "webui" ? `<span class="chip">外观设置入口</span>` : "",
    appearanceLabel ? `<span class="chip ${module.appearance_mode === "global" ? "danger-chip" : ""}">${escapeHtml(appearanceLabel)}</span>` : "",
    groupKind === "extension" ? `<span class="chip">拓展包</span>` : "",
    conflicts.length ? `<span class="chip danger-chip">有冲突</span>` : "",
  ];
  return badges.filter(Boolean);
}

function moduleSourceLabel(module, groupKind = "") {
  if (groupKind === "extension") return "补充拓展";
  if (module.kind === "official") return "官方模块";
  if (groupKind === "appearance") return "外观模块";
  if (module.kind === "custom") return "自定义模块";
  return moduleTypeLabel(module.type);
}

function moduleTypeLabel(type = "") {
  if (type === "extension") return "拓展包";
  if (type === "module") return "完整模块";
  if (type === "appearance") return "外观模块";
  return type || "模块";
}

function styleKindLabel(kind = "") {
  if (kind === "official") return "官方";
  if (kind === "appearance") return "外观";
  if (kind === "custom") return "自定义";
  if (kind === "missing") return "未找到";
  return kind || "样式";
}

function webuiAppearanceBody(payload, mode = "module") {
  const settings = payload.settings;
  const currentId = settings.active_frontend_style || "default";
  return `
    <div class="brand-settings">
      <div class="brand-preview">${webuiAssetUrl(settings.brand_avatar_url) ? `<img src="${escapeHtml(webuiAssetUrl(settings.brand_avatar_url))}" alt="${escapeHtml(settings.site_title || "小窝")}">` : `<span>${escapeHtml((settings.site_title || "小窝").slice(0, 1))}</span>`}</div>
      <div class="form-grid compact">
        <label>小窝标题<input name="site_title" value="${escapeHtml(settings.site_title || "小窝")}" placeholder="例如：某某的小窝"></label>
        <label>小窝副标题<input name="site_subtitle" value="${escapeHtml(settings.site_subtitle || "把今天安放好，旧事也能被轻轻找回来")}" placeholder="显示在首页标题下面"></label>
        <label>头像地址<input name="brand_avatar_url" value="${escapeHtml(settings.brand_avatar_url || "")}" placeholder="可填写图片地址，也可上传"></label>
        <label>上传头像<input name="brand_avatar_file" type="file" accept="image/png,image/jpeg,image/webp,image/gif"></label>
        <label>当前样式<select name="active_frontend_style">${payload.frontend_styles.map((style) => `<option value="${escapeHtml(style.id)}" ${currentId === style.id ? "selected" : ""}>${escapeHtml(style.name)} · ${escapeHtml(styleKindLabel(style.kind))}</option>`).join("")}</select></label>
        <label>自定义页面目录<input name="custom_webui_dir" value="${escapeHtml(settings.custom_webui_dir || "")}" placeholder="留空使用默认目录"></label>
      </div>
    </div>
    ${frontendStyleCards(payload.frontend_styles || [], currentId)}
    <div class="setting-line">
      <div>
        <strong>更新前备份自定义内容</strong>
        <p class="muted">插件更新前尽量保留用户自己做的页面、主题和模块。</p>
      </div>
      ${switchControl("backup_custom_before_update", settings.backup_custom_before_update)}
    </div>
    <input name="onboarding_completed" type="hidden" value="${settings.onboarding_completed ? "true" : "false"}">
    ${mode === "module" ? `<button class="button ghost" data-onboarding-replay type="button">重新查看新手引导</button>` : ""}
  `;
}

function frontendStyleCards(styles = [], currentId = "default") {
  const description = {
    default: "稳定、清晰，适合作为默认维护界面。",
    "nest-paper-garden": "纸感手账风，适合长时间阅读日记。",
    "nest-glass-cabin": "轻玻璃小屋，清爽现代，层次更通透。",
    "nest-night-atelier": "温柔深色工作室，适合夜间整理。"
  };
  return `
    <div class="style-chooser" data-style-chooser>
      ${styles.map((style) => `
        <button class="style-card ${currentId === style.id ? "active" : ""}" data-style-select="${escapeHtml(style.id)}" type="button">
          <span class="style-swatch style-${escapeHtml(style.id)}"></span>
          <span>
            <strong>${escapeHtml(style.name)}</strong>
            <em>${escapeHtml(style.description || description[style.id] || "可选前端样式。")}</em>
          </span>
        </button>
      `).join("")}
    </div>
  `;
}

function exportOptions(catalog) {
  const options = [
    ["full", "完整备份"],
    ["diary", "日记模块"],
    ["impressions", "人物印象"],
    ["media", "媒体归档"],
    ["memos", "备忘录"],
    ["webui_custom", "个性化前端"],
    ["security", "安全配置"],
    ["custom_module", "指定自定义模块"],
    ["extension", "指定拓展包"],
  ];
  return options
    .map(([value, label], index) => {
      const descriptions = {
        full: "框架、模块、导入记录",
        diary: "日记正文、快照和草稿",
        impressions: "人物印象资料",
        media: "图片、附件和相册",
        memos: "纸条、标签和敏感标记",
        webui_custom: "标题、头像和自定义页面",
        security: "管理员密码和接口密钥",
        custom_module: "填写模块 ID 后导出",
        extension: "填写拓展包 ID 后导出",
      };
      return `
        <label class="choice-card">
          <input name="package_type" value="${value}" type="checkbox" ${index === 0 ? "checked" : ""}>
          <span>
            <strong>${escapeHtml(label)}</strong>
            <em>${escapeHtml(descriptions[value] || "")}</em>
          </span>
        </label>
      `;
    })
    .join("");
}

function versionMaintenance(payload) {
  const runtime = payload.runtime || {};
  return `
    <section class="version-panel">
      <div>
        <h3>版本维护</h3>
        <p class="muted">当前版本 ${escapeHtml(APP_VERSION)}。检查会读取插件仓库元数据；更新会调用后端更新服务。</p>
        ${state.notice ? "" : `<p class="muted">自更新：${runtime.self_update ? "已启用" : "未启用，建议在 AstrBot 插件管理中更新"}</p>`}
      </div>
      <div class="actions">
        <button class="button" data-version-check type="button" ${state.versionBusy ? "disabled" : ""}>检查更新</button>
        <button class="button ghost" data-version-update type="button" ${state.versionBusy ? "disabled" : ""}>更新版本</button>
      </div>
    </section>
  `;
}

async function saveSettings(event) {
  event.preventDefault();
  const shouldClose = event.submitter?.dataset.saveClose !== undefined;
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  const current = state.settings?.settings || {};
  const hasField = (name) => formEl.querySelector(`[name="${CSS.escape(name)}"]`) !== null;
  const valueField = (name, fallback = "") => {
    const field = formEl.querySelector(`[name="${CSS.escape(name)}"]`);
    return field ? field.value : fallback;
  };
  const boolField = (name, fallback = false) => (hasField(name) ? form.has(name) : Boolean(fallback));
  const boolValueField = (name, fallback = false) => {
    if (!hasField(name)) return Boolean(fallback);
    const value = String(form.get(name) || "").trim().toLowerCase();
    return value ? ["1", "true", "yes", "on", "开"].includes(value) : form.has(name);
  };
  const numberField = (name, fallback = 0) => Number(valueField(name, fallback) || fallback || 0);
  const listField = (name, fallback = []) => {
    if (form.getAll("__module_group_present").includes(name)) return form.getAll(name);
    return hasField(name) ? form.getAll(name) : fallback;
  };
  let avatarUrl = String(valueField("brand_avatar_url", current.brand_avatar_url || ""));
  const avatarFile = form.get("brand_avatar_file");
  if (avatarFile && avatarFile.size) {
    avatarUrl = await uploadAvatar(avatarFile);
  }
  const payload = {
    site_title: valueField("site_title", current.site_title || "小窝"),
    site_subtitle: valueField("site_subtitle", current.site_subtitle || "把今天安放好，旧事也能被轻轻找回来"),
    brand_avatar_url: avatarUrl,
    search_default_top_k: numberField("search_default_top_k", current.search_default_top_k || 5),
    search_snippet_chars: numberField("search_snippet_chars", current.search_snippet_chars || 180),
    memory_recall_enabled: boolField("memory_recall_enabled", current.memory_recall_enabled),
    memory_recall_policy: valueField("memory_recall_policy", current.memory_recall_policy || "conservative"),
    enable_diary_module: boolField("enable_diary_module", current.enable_diary_module),
    diary_archive_granularity: "day",
    diary_display_mode: valueField("diary_display_mode", current.diary_display_mode || "grouped"),
    admin_private_diary_enabled: false,
    admin_private_push_enabled: false,
    diary_push_format: valueField("diary_push_format", current.diary_push_format || "text"),
    diary_push_target: "none",
    diary_t2i_template_name: valueField("diary_t2i_template_name", current.diary_t2i_template_name || "plain_note"),
    diary_image_send_max_retries: Math.max(0, Math.min(10, numberField("diary_image_send_max_retries", current.diary_image_send_max_retries ?? 3))),
    diary_image_send_failure_notice: boolField("diary_image_send_failure_notice", current.diary_image_send_failure_notice ?? true),
    permissions_allow_admin_natural_language: boolField("permissions_allow_admin_natural_language", current.permissions_allow_admin_natural_language ?? true),
    non_admin_permissions: hasField("non_admin_permissions") ? form.getAll("non_admin_permissions") : (current.non_admin_permissions || []),
    nest_admin_ids: valueField("nest_admin_ids", current.nest_admin_ids || ""),
    diary_write_prompt: valueField("diary_write_prompt", current.diary_write_prompt || ""),
    diary_t2i_template: valueField("diary_t2i_template", current.diary_t2i_template || ""),
    enable_media_module: boolField("enable_media_module", current.enable_media_module),
    allow_media_refs: boolField("allow_media_refs", current.allow_media_refs),
    media_max_items_per_day: numberField("media_max_items_per_day", current.media_max_items_per_day || 80),
    media_auto_save_policy: valueField("media_auto_save_policy", current.media_auto_save_policy || "admin_only"),
    media_auto_save_limit_12h: numberField("media_auto_save_limit_12h", current.media_auto_save_limit_12h || 10),
    media_auto_album_strategy: valueField("media_auto_album_strategy", current.media_auto_album_strategy || "confirm"),
    media_allow_bot_import: boolField("media_allow_bot_import", current.media_allow_bot_import),
    media_auto_album: boolField("media_auto_album", current.media_auto_album),
    media_storage_strategy: valueField("media_storage_strategy", current.media_storage_strategy || "copy"),
    enable_impressions_module: boolField("enable_impressions_module", current.enable_impressions_module),
    auto_impression_from_diary: boolField("auto_impression_from_diary", current.auto_impression_from_diary),
    impression_write_level: valueField("impression_write_level", current.impression_write_level || "balanced"),
    impression_update_strategy: valueField("impression_update_strategy", current.impression_update_strategy || "evidence_only"),
    impression_identity_strategy: valueField("impression_identity_strategy", current.impression_identity_strategy || "separate"),
    impression_allow_new_people: boolField("impression_allow_new_people", current.impression_allow_new_people),
    impression_min_confidence: numberField("impression_min_confidence", current.impression_min_confidence || 3),
    show_impression_prompt: boolField("show_impression_prompt", current.show_impression_prompt),
    enable_memos_module: boolField("enable_memos_module", current.enable_memos_module),
    memos_write_policy: valueField("memos_write_policy", current.memos_write_policy || "admin_only"),
    memos_auto_write_limit_12h: numberField("memos_auto_write_limit_12h", current.memos_auto_write_limit_12h ?? 12),
    memos_sensitive_default_hidden: boolField("memos_sensitive_default_hidden", current.memos_sensitive_default_hidden ?? true),
    active_frontend_style: valueField("active_frontend_style", current.active_frontend_style || "default"),
    enabled_official_modules: listField("enabled_official_modules", current.enabled_official_modules || []),
    enabled_custom_modules: listField("enabled_custom_modules", current.enabled_custom_modules || []),
    enabled_custom_extensions: listField("enabled_custom_extensions", current.enabled_custom_extensions || []),
    enabled_appearance_modules: listField("enabled_appearance_modules", current.enabled_appearance_modules || []),
    hidden_module_nav_ids: listField("hidden_module_nav_ids", current.hidden_module_nav_ids || []),
    appearance_modules_initialized: true,
    onboarding_completed: boolValueField("onboarding_completed", current.onboarding_completed),
    custom_webui_dir: valueField("custom_webui_dir", current.custom_webui_dir || ""),
    backup_custom_before_update: boolField("backup_custom_before_update", current.backup_custom_before_update),
    impression_prompt: valueField("impression_prompt", current.impression_prompt || ""),
  };
  if (hasField("active_frontend_style")) {
    const appearance = state.settings?.module_catalog?.appearance || [];
    const globalStyleIds = appearance.filter((item) => item.appearance_mode === "global").map((item) => item.id);
    const activeStyle = payload.active_frontend_style || "default";
    payload.enabled_appearance_modules = (payload.enabled_appearance_modules || []).filter(
      (item) => !globalStyleIds.includes(item) || item === activeStyle
    );
    if (activeStyle !== "default" && globalStyleIds.includes(activeStyle)) {
      payload.enabled_appearance_modules = syncEnabledModule(payload.enabled_appearance_modules, activeStyle, true);
    }
  }
  if (event.submitter?.dataset.moduleToggle !== undefined) {
    const moduleId = event.submitter.dataset.moduleId;
    const moduleInput = event.submitter.dataset.moduleInput;
    if (moduleInput === "enabled_official_modules") {
      payload.enabled_official_modules = syncEnabledModule(payload.enabled_official_modules, moduleId, event.submitter.checked);
      payload.enable_diary_module = payload.enabled_official_modules.includes("diary");
      payload.enable_media_module = payload.enabled_official_modules.includes("media");
      payload.enable_impressions_module = payload.enabled_official_modules.includes("impressions");
      payload.enable_memos_module = payload.enabled_official_modules.includes("memos");
    } else if (moduleInput === "enabled_custom_modules") {
      payload.enabled_custom_modules = syncEnabledModule(payload.enabled_custom_modules, moduleId, event.submitter.checked);
    } else if (moduleInput === "enabled_custom_extensions") {
      payload.enabled_custom_extensions = syncEnabledModule(payload.enabled_custom_extensions, moduleId, event.submitter.checked);
    } else if (moduleInput === "enabled_appearance_modules") {
      payload.enabled_appearance_modules = syncEnabledModule(payload.enabled_appearance_modules, moduleId, event.submitter.checked);
    }
  }
  if (form.getAll("__module_group_present").includes("enabled_official_modules")) {
    payload.enabled_official_modules = Array.from(new Set(payload.enabled_official_modules));
    payload.enable_diary_module = payload.enabled_official_modules.includes("diary");
    payload.enable_media_module = payload.enabled_official_modules.includes("media");
    payload.enable_impressions_module = payload.enabled_official_modules.includes("impressions");
    payload.enable_memos_module = payload.enabled_official_modules.includes("memos");
  } else {
    if (hasField("enable_diary_module")) {
      payload.enabled_official_modules = syncEnabledModule(payload.enabled_official_modules, "diary", payload.enable_diary_module);
    }
    if (hasField("enable_media_module")) {
      payload.enabled_official_modules = syncEnabledModule(payload.enabled_official_modules, "media", payload.enable_media_module);
    }
    if (hasField("enable_impressions_module")) {
      payload.enabled_official_modules = syncEnabledModule(payload.enabled_official_modules, "impressions", payload.enable_impressions_module);
    }
    if (hasField("enable_memos_module")) {
      payload.enabled_official_modules = syncEnabledModule(payload.enabled_official_modules, "memos", payload.enable_memos_module);
    }
  }
  const savedSettings = await api("/api/ui/settings", { method: "POST", body: JSON.stringify(payload) });
  if (savedSettings?.settings) {
    state.settings = { ...(state.settings || {}), settings: savedSettings.settings };
  }
  await saveNotebookSettings(formEl, form);
  refreshThemeStylesheet();
  state.toast = "设置已保存";
  state.error = "";
  state.bootstrap = null;
  if (shouldClose) state.settingsModuleDetail = "";
  await renderSettings();
  updateShell();
  window.setTimeout(() => {
    state.toast = "";
    updateShell();
  }, 2200);
}

async function saveNotebookSettings(formEl, form) {
  const ids = Array.from(new Set(form.getAll("notebook_id").map((item) => String(item || "").trim()).filter(Boolean)));
  const deleteIds = Array.from(
    new Set([
      ...(state.notebookDeleteIds || []),
      ...form.getAll("notebook_delete_id").map((item) => String(item || "").trim()),
    ])
  ).filter((id) => id && id !== "default");
  if (!ids.length && !deleteIds.length) return;
  const current = state.notebooks || [];
  const notebooks = ids.map((id) => {
    const existing = current.find((item) => (item.id || item.notebook_id) === id) || {};
    const origin_umo = notebookOriginFromForm(form, id);
    return {
      ...existing,
      id,
      name: form.get(`notebook_name_${id}`) || existing.name || id,
      origin_umo,
      platform_id: origin_umo ? origin_umo.split(":")[0] : "",
      message_type: origin_umo ? origin_umo.split(":")[1] : "",
      session_id: origin_umo ? origin_umo.split(":").slice(2).join(":") : "",
      enabled: form.has(`notebook_enabled_${id}`),
      auto_archive_enabled: form.has(`notebook_auto_archive_${id}`),
      archive_time: form.get(`notebook_archive_time_${id}`) || existing.archive_time || "03:00",
      push_enabled: (form.get(`notebook_push_target_${id}`) || existing.push_target || "none") !== "none",
      push_target: form.get(`notebook_push_target_${id}`) || existing.push_target || "none",
      push_format: form.get("diary_push_format") || existing.push_format || "text",
    };
  });
  const payload = await api("/api/ui/notebooks", { method: "POST", body: JSON.stringify({ notebooks, delete_ids: deleteIds, replace: true }) });
  state.notebooks = payload.items || notebooks;
  state.notebookDeleteIds = [];
  formEl.querySelectorAll('[name="notebook_delete_id"]').forEach((input) => input.remove());
}

async function saveModuleToggle(input) {
  const formEl = input.closest("form");
  if (!formEl) return;
  try {
    await saveSettings({ preventDefault() {}, currentTarget: formEl, submitter: input });
  } catch (err) {
    state.error = err.message || "保存失败";
    updateShell();
  }
}

function syncEnabledModule(items, moduleId, enabled) {
  const next = new Set(items || []);
  if (enabled) next.add(moduleId);
  else next.delete(moduleId);
  return Array.from(next);
}

async function uploadAvatar(file) {
  if (PLUGIN_PAGE_BRIDGE) {
    await pluginBridgeReady;
    const result = normalizePluginBridgeResult(await PLUGIN_PAGE_BRIDGE.upload("ui/upload/avatar", file));
    if (!result.ok) throw new Error(result.detail || "头像上传失败");
    updatePluginWebuiBase(result);
    await refreshPluginAvatarData(true);
    return result.data.avatar_url;
  }
  const payload = new FormData();
  payload.append("file", file);
  const response = await fetch("/api/ui/avatar", {
    method: "POST",
    credentials: "same-origin",
    body: payload,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  const data = await response.json();
  return data.avatar_url;
}

function exportBackup(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const selected = form.getAll("package_type");
  const params = new URLSearchParams({
    package_type: (selected.length ? selected : ["full"]).join(","),
    module_id: form.get("module_id") || "",
    include_security: form.has("include_security") ? "true" : "false",
  });
  if (PLUGIN_PAGE_BRIDGE) {
    void pluginBridgeReady.then(() => PLUGIN_PAGE_BRIDGE.download("ui/export", Object.fromEntries(params.entries()), "nest-backup.zip"));
    return;
  }
  window.location.href = `/api/ui/export?${params.toString()}`;
}

function syncExportChoices(target) {
  const form = target.closest("form");
  if (!form) return;
  const choices = Array.from(form.querySelectorAll('input[name="package_type"]'));
  const full = choices.find((item) => item.value === "full");
  if (!full) return;
  if (target.value === "full" && target.checked) {
    choices.forEach((item) => {
      if (item !== full) item.checked = false;
    });
    return;
  }
  if (target.value !== "full" && target.checked) {
    full.checked = false;
  }
  if (!choices.some((item) => item.checked)) {
    full.checked = true;
  }
}

function backupHealthLine(label, health = {}) {
  const latest = health.latest_diary_date || "无";
  const recent = (health.latest_diary_dates || []).slice(-3).join("、") || "无";
  return `${label}：日记 ${health.diary_count || 0} 篇，最近 ${latest}；近三篇 ${recent}；备忘录 ${health.memo_count || 0} 条，媒体 ${health.media_count || 0} 个。`;
}

function importWarningsText(warnings = []) {
  if (!warnings.length) return "";
  return warnings.map((item) => `${item.title || "导入警告"}：${item.message || ""}`).join("；");
}

async function importBackup(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const file = form.get("backup_file");
  if (!file || !file.size) throw new Error("请选择备份 zip 文件");

  let previewData;
  if (PLUGIN_PAGE_BRIDGE) {
    await pluginBridgeReady;
    const result = normalizePluginBridgeResult(await PLUGIN_PAGE_BRIDGE.upload("ui/upload/import-preview", file));
    if (!result.ok) throw new Error(result.detail || "备份预检失败");
    updatePluginWebuiBase(result);
    previewData = result.data;
  } else {
    const previewPayload = new FormData();
    previewPayload.append("backup_file", file);
    const previewResponse = await fetch("/api/ui/import/preview", {
      method: "POST",
      credentials: "same-origin",
      body: previewPayload,
    });
    if (!previewResponse.ok) {
      let detail = previewResponse.statusText;
      try {
        const data = await previewResponse.json();
        detail = data.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    previewData = await previewResponse.json();
  }

  const preview = previewData.preview || {};
  if ((preview.importable || 0) <= 0) {
    throw new Error("这个备份包里没有可导入的小窝数据。");
  }
  const manifestHealth = preview.manifest?.data_summary || {};
  const currentHealth = preview.current_health || {};
  if (
    manifestHealth.latest_diary_date &&
    currentHealth.latest_diary_date &&
    manifestHealth.latest_diary_date < currentHealth.latest_diary_date &&
    !(await confirmAction(`备份包里的最近日记是 ${manifestHealth.latest_diary_date}，当前小窝最近日记是 ${currentHealth.latest_diary_date}。继续导入前请确认你已经导出了最新数据。`))
  ) {
    return;
  }

  let data;
  if (PLUGIN_PAGE_BRIDGE) {
    const strategy = form.get("strategy") === "overwrite" ? "overwrite" : "safe";
    const result = normalizePluginBridgeResult(await PLUGIN_PAGE_BRIDGE.upload(`ui/upload/import-${strategy}`, file));
    if (!result.ok) throw new Error(result.detail || "备份导入失败");
    updatePluginWebuiBase(result);
    data = result.data;
  } else {
    const payload = new FormData();
    payload.append("backup_file", file);
    payload.append("strategy", form.get("strategy") || "safe");
    const response = await fetch("/api/ui/import", {
      method: "POST",
      credentials: "same-origin",
      body: payload,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const responseData = await response.json();
        detail = responseData.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    data = await response.json();
  }

  const result = data.result || {};
  const warnings = importWarningsText(result.warnings || []);
  const backupPath = result.backup_path ? `导入前快照：${result.backup_path}。` : "";
  state.notice = [
    `导入完成：${result.imported || 0} 个文件，跳过 ${result.skipped || 0} 个，覆盖 ${result.overwritten || 0} 个，重建索引 ${result.reindexed_diaries || 0} 篇。`,
    backupHealthLine("导入前", result.before || {}),
    backupHealthLine("导入后", result.after || {}),
    backupPath,
    warnings ? `需要注意：${warnings}` : "",
  ].filter(Boolean).join(" ");
  state.error = warnings ? "导入完成但健康检查发现风险，请核对最近日记和备忘录。" : "";
  state.bootstrap = null;
  await renderSettings();
  updateShell();
}

async function checkVersion() {
  if (state.versionBusy) return;
  state.versionBusy = true;
  state.notice = "正在检查版本...";
  state.error = "";
  updateShell();
  try {
    const result = await api("/api/ui/version/check");
    state.notice = `${result.message} 当前 ${result.current}，最新 ${result.latest}。`;
  } catch (err) {
    state.error = err.message || "版本检测失败";
    state.notice = "";
  } finally {
    state.versionBusy = false;
    await renderSettings();
    updateShell();
  }
}

async function updateVersion() {
  if (state.versionBusy) return;
  state.versionBusy = true;
  state.notice = "正在请求更新...";
  state.error = "";
  updateShell();
  try {
    const result = await api("/api/ui/version/update", { method: "POST" });
    const output = result.output ? `\n${result.output}` : "";
    if (result.ok) {
      state.notice = `${result.message}${output}`;
    } else {
      state.error = `${result.message}${output}`;
      state.notice = "";
    }
  } catch (err) {
    state.error = err.message || "更新失败";
    state.notice = "";
  } finally {
    state.versionBusy = false;
    await renderSettings();
    updateShell();
  }
}

async function saveSecurity(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await api("/api/ui/security", {
    method: "POST",
    body: JSON.stringify({
      admin_password: form.get("admin_password"),
      bot_api_token: form.get("bot_api_token"),
      generate_bot_api_token: form.has("generate_bot_api_token"),
      external_api_enabled: form.has("external_api_enabled"),
    }),
  });
  state.notice = "访问密钥已保存。";
  await renderSettings();
  updateShell();
}

ensureShell();
panel(state.view).innerHTML = `<div class="loading">正在进入小窝...</div>`;
if (!PLUGIN_PAGE_BRIDGE) {
  window.addEventListener("popstate", async () => {
    applyRouteStateFromLocation();
    await loadView();
  });
}
void refreshThemeStylesheet();
syncRouteForState(state.view, true);
loadView();
