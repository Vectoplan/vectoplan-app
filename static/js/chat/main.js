// services/vectoplan-app/static/js/chat/main.js
// Orchestrator for the VECTOPLAN project/workspace shell.
// Responsibilities:
// - initialize the project sidebar
// - switch workspace iframe between Project, Map, Editor, 2D, LV and Admin
// - start project-first, not editor-first
// - keep Map/3D/2D/LV gated until the project is configured
// - keep version dropdown usable as a neutral list
// - avoid any legacy 3D backend calls
// - never use Docker-internal URLs as browser iframe targets
// - no visible chat UI, no composer, no transcript, no chat drawer

import { $, uiState } from "./core.js";
import { getConfig } from "./api.js";
import { loadVersions } from "./versions.js";


/* ───────────────────────── Constants ───────────────────────── */

const WORKSPACE_IFRAME_ALLOW = "fullscreen; pointer-lock; clipboard-read; clipboard-write";
const WORKSPACE_IFRAME_REFERRER_POLICY = "no-referrer";

const DEFAULT_PROJECT_ROUTE = "/ui/project/new";

const PROJECT_SIDEBAR_ROOT_SELECTOR = "[data-vp-project-sidebar]";

const LEGACY_OR_INTERNAL_BROWSER_TARGETS = [
  "http://localhost:8090",
  "http://127.0.0.1:8090",
  "http://openlayer:8090",
  "http://vectoplan-openlayer:8090",
  "http://server-openlayer:8090",
  "http://vectoplan-editor:5000",
  "http://editor:5000",
];

const MODE_TO_BUTTON_ID = {
  project: "modeProjectBtn",
  map: "modeMapBtn",
  "3d": "mode3dBtn",
  "2d": "mode2dBtn",
  lv: "modeLvBtn",
  admin: "modeAdminBtn",
};

const MODE_TITLE = {
  project: "Projekt",
  "3d": "VECTOPLAN Editor",
  editor: "VECTOPLAN Editor",
  map: "Karte",
  "2d": "2D Ansicht",
  cad2d: "2D Ansicht",
  lv: "Leistungsverzeichnis",
  admin: "Admin",
};

const MODES_REQUIRING_CONFIGURED_PROJECT = new Set(["map", "3d", "2d", "lv"]);


/* ───────────────────────── Boot safety ───────────────────────── */

function safeCall(label, fn) {
  try {
    return fn();
  } catch (error) {
    try {
      console.warn(`[boot] ${label} failed`, error);
    } catch (_) {}
    return undefined;
  }
}

async function safeAwait(label, fn) {
  try {
    return await fn();
  } catch (error) {
    try {
      console.warn(`[boot] ${label} failed`, error);
    } catch (_) {}
    return undefined;
  }
}

function safeOn(el, eventName, handler, options) {
  try {
    if (!el || !eventName || typeof handler !== "function") return false;
    el.addEventListener(eventName, handler, options);
    return true;
  } catch (_) {
    return false;
  }
}

function nowString() {
  try {
    return String(Date.now());
  } catch (_) {
    return String(new Date().getTime());
  }
}


/* ───────────────────────── Config helpers ───────────────────────── */

function cfg() {
  try {
    return {
      ...(window.APP_CONFIG || {}),
      ...(getConfig ? getConfig() : {}),
    };
  } catch (_) {
    try {
      return window.APP_CONFIG || {};
    } catch (__) {
      return {};
    }
  }
}

function appRoot() {
  try {
    return document.querySelector(".app-wrap") || document.body || null;
  } catch (_) {
    return null;
  }
}

function dataValue(key, fallback = "") {
  try {
    const root = appRoot();
    if (!root || !root.dataset) return String(fallback || "").trim();

    const value = root.dataset[key];
    if (value == null || value === "") return String(fallback || "").trim();

    return String(value).trim();
  } catch (_) {
    return String(fallback || "").trim();
  }
}

function cfgValue(key, fallback = "") {
  try {
    const c = cfg();
    const value = c && c[key] != null ? c[key] : fallback;
    return String(value == null ? "" : value).trim();
  } catch (_) {
    return String(fallback || "").trim();
  }
}

function cfgObject(key, fallback = {}) {
  try {
    const c = cfg();
    const value = c && c[key] != null ? c[key] : fallback;
    return value && typeof value === "object" && !Array.isArray(value) ? value : fallback;
  } catch (_) {
    return fallback;
  }
}

function boolFromValue(value, fallback = false) {
  try {
    if (value === true || value === false) return value;
    if (value === 1 || value === "1") return true;
    if (value === 0 || value === "0") return false;

    const text = String(value == null ? "" : value).trim().toLowerCase();

    if (["true", "yes", "y", "on", "ja", "enabled"].includes(text)) return true;
    if (["false", "no", "n", "off", "nein", "disabled"].includes(text)) return false;

    return !!fallback;
  } catch (_) {
    return !!fallback;
  }
}

function cfgBool(key, fallback = false) {
  try {
    const c = cfg();

    if (c && Object.prototype.hasOwnProperty.call(c, key)) {
      return boolFromValue(c[key], fallback);
    }

    const datasetValue = dataValue(key, "");
    if (datasetValue !== "") {
      return boolFromValue(datasetValue, fallback);
    }

    return !!fallback;
  } catch (_) {
    return !!fallback;
  }
}

function conversationId() {
  try {
    return (
      cfgValue("conversationId", "") ||
      cfgValue("chatId", dataValue("chatId", "")) ||
      dataValue("conversationId", "")
    );
  } catch (_) {
    return "";
  }
}

function encodePathPart(value) {
  try {
    return encodeURIComponent(String(value || ""));
  } catch (_) {
    return "";
  }
}

function appPaths() {
  try {
    return {
      ...(cfgObject("paths", {}) || {}),
      ...(cfgObject("workspacePaths", {}) || {}),
    };
  } catch (_) {
    return {};
  }
}

function pathValue(key, fallback = "") {
  try {
    const paths = appPaths();

    if (paths && paths[key] != null && String(paths[key]).trim()) {
      return String(paths[key]).trim();
    }

    const direct = cfgValue(key, "");
    if (direct) return direct;

    const dataset = dataValue(key, "");
    if (dataset) return dataset;

    return String(fallback || "").trim();
  } catch (_) {
    return cfgValue(key, fallback);
  }
}

function projectPublicId() {
  try {
    const p = currentProject();

    return String(
      p.public_id ||
        p.publicId ||
        p.project_public_id ||
        p.projectPublicId ||
        cfgValue("projectPublicId", dataValue("projectPublicId", "new")) ||
        "new"
    ).trim();
  } catch (_) {
    return "new";
  }
}

function projectId() {
  try {
    const p = currentProject();

    return String(
      p.id ||
        p.project_id ||
        cfgValue("projectId", dataValue("projectId", "")) ||
        ""
    ).trim();
  } catch (_) {
    return "";
  }
}

function routeForProject(pathSuffix = "project") {
  try {
    const publicId = projectPublicId();
    if (!publicId || publicId === "new") {
      return pathSuffix === "project" ? DEFAULT_PROJECT_ROUTE : "";
    }

    if (pathSuffix === "project") return `/ui/project/${encodePathPart(publicId)}/project`;
    if (pathSuffix === "editor") return `/ui/project/${encodePathPart(publicId)}/editor`;
    if (pathSuffix === "map") return `/ui/project/${encodePathPart(publicId)}/map`;
    if (pathSuffix === "cad2d") return `/ui/project/${encodePathPart(publicId)}/cad2d`;
    if (pathSuffix === "lv") return `/ui/project/${encodePathPart(publicId)}/lv`;
    if (pathSuffix === "admin") return `/ui/project/${encodePathPart(publicId)}/admin`;
    if (pathSuffix === "plan2d") return `/ui/project/${encodePathPart(publicId)}/plan2d.json`;
    if (pathSuffix === "cad-embed") return `/ui/project/${encodePathPart(publicId)}/cad-embed.json`;
    if (pathSuffix === "map-json") return `/ui/project/${encodePathPart(publicId)}/map.json`;

    return `/ui/project/${encodePathPart(publicId)}/project`;
  } catch (_) {
    return pathSuffix === "project" ? DEFAULT_PROJECT_ROUTE : "";
  }
}

function normalizeRelativeUrl(input) {
  try {
    const raw = String(input || "").trim();

    if (!raw) return "";

    if (raw.startsWith("/")) {
      const url = new URL(raw, location.origin);
      return url.pathname + (url.search ? url.search : "") + (url.hash || "");
    }

    return raw;
  } catch (_) {
    return String(input || "").trim();
  }
}

function cacheBustLocalUrl(input) {
  try {
    const raw = String(input || "").trim();
    if (!raw) return raw;

    if (!raw.startsWith("/")) return raw;

    const url = new URL(raw, location.origin);
    url.searchParams.set("r", nowString());

    return url.pathname + "?" + url.searchParams.toString() + (url.hash || "");
  } catch (_) {
    return input;
  }
}

function stableLocalUrl(input) {
  try {
    const raw = String(input || "").trim();
    if (!raw) return raw;

    if (!raw.startsWith("/")) return raw;

    const url = new URL(raw, location.origin);
    url.searchParams.delete("r");

    const query = url.searchParams.toString();
    return url.pathname + (query ? "?" + query : "") + (url.hash || "");
  } catch (_) {
    return input;
  }
}

function isUnsafeLegacyTarget(input) {
  try {
    const raw = String(input || "").trim();

    if (!raw) return false;

    const normalized = raw.replace(/[?#].*$/, "");

    for (const bad of LEGACY_OR_INTERNAL_BROWSER_TARGETS) {
      if (normalized === bad || normalized.startsWith(bad + "/")) {
        return true;
      }
    }

    try {
      const url = new URL(raw, location.origin);
      const host = String(url.hostname || "").toLowerCase();

      if (
        [
          "openlayer",
          "vectoplan-openlayer",
          "server-openlayer",
          "vectoplan-editor",
          "editor",
        ].includes(host)
      ) {
        return true;
      }
    } catch (_) {}

    return false;
  } catch (_) {
    return false;
  }
}

function sanitizeWorkspaceUrl(input, fallback) {
  try {
    const raw = normalizeRelativeUrl(input);

    if (!raw || isUnsafeLegacyTarget(raw)) {
      return normalizeRelativeUrl(fallback);
    }

    return raw;
  } catch (_) {
    return normalizeRelativeUrl(fallback);
  }
}


/* ───────────────────────── Project state helpers ───────────────────────── */

function currentProject() {
  try {
    const c = cfg();
    const project =
      c.currentProject ||
      c.project ||
      window.__VECTOPLAN_CURRENT_PROJECT__ ||
      {};

    if (project && typeof project === "object" && !Array.isArray(project)) {
      return project;
    }

    return {};
  } catch (_) {
    return {};
  }
}

function projectExists() {
  try {
    if (cfgBool("projectExists", false)) return true;

    const publicId = projectPublicId();
    const isNew = cfgBool("projectIsNew", dataValue("projectIsNew", "true") === "true");

    return !!publicId && publicId !== "new" && !isNew;
  } catch (_) {
    return false;
  }
}

function projectConfigured() {
  try {
    if (cfgBool("projectConfigured", false)) return true;

    const p = currentProject();
    const status = String(
      p.setup_status ||
        p.setupStatus ||
        cfgValue("projectSetupStatus", dataValue("projectSetupStatus", "draft")) ||
        "draft"
    ).trim().toLowerCase();

    return (
      boolFromValue(p.is_configured || p.isConfigured, false) ||
      status === "configured" ||
      dataValue("projectConfigured", "false") === "true"
    );
  } catch (_) {
    return false;
  }
}

function projectToolsEnabled() {
  try {
    return projectExists() && projectConfigured();
  } catch (_) {
    return false;
  }
}

function setProjectDataset(project, detail = {}) {
  try {
    const root = appRoot();
    if (!root || !root.dataset) return;

    const p = project && typeof project === "object" ? project : {};
    const publicId =
      p.public_id ||
      p.publicId ||
      p.project_public_id ||
      p.projectPublicId ||
      detail.projectPublicId ||
      detail.public_id ||
      detail.publicId ||
      projectPublicId();

    const id = p.id || p.project_id || detail.projectId || detail.id || "";

    const configured = boolFromValue(
      detail.isConfigured ??
        detail.is_configured ??
        p.is_configured ??
        p.isConfigured,
      projectConfigured()
    );

    const setupStatus = String(
      p.setup_status ||
        p.setupStatus ||
        detail.setup_status ||
        (configured ? "configured" : "draft")
    ).trim();

    root.dataset.projectId = String(id || "");
    root.dataset.projectPublicId = String(publicId || "new");
    root.dataset.projectIsNew = publicId && publicId !== "new" ? "false" : "true";
    root.dataset.projectConfigured = configured ? "true" : "false";
    root.dataset.projectToolsEnabled = configured && publicId && publicId !== "new" ? "true" : "false";
    root.dataset.projectSetupStatus = setupStatus || "draft";

    const paths = p.paths && typeof p.paths === "object" ? p.paths : {};

    if (paths.projectPagePath || paths.projectUrl) {
      root.dataset.projectPagePath = String(paths.projectPagePath || paths.projectUrl);
    }

    if (paths.editorPagePath) {
      root.dataset.editorPagePath = String(paths.editorPagePath);
    }

    if (paths.mapPagePath) {
      root.dataset.mapPagePath = String(paths.mapPagePath);
    }
  } catch (_) {}
}

function updateAppConfigProject(project, detail = {}) {
  try {
    if (!window.APP_CONFIG || typeof window.APP_CONFIG !== "object") {
      window.APP_CONFIG = {};
    }

    const p = project && typeof project === "object" ? project : {};
    const paths = p.paths && typeof p.paths === "object" ? p.paths : {};
    const publicId =
      p.public_id ||
      p.publicId ||
      p.project_public_id ||
      p.projectPublicId ||
      detail.public_id ||
      detail.publicId ||
      detail.projectPublicId ||
      window.APP_CONFIG.projectPublicId ||
      "new";

    window.APP_CONFIG.project = p;
    window.APP_CONFIG.currentProject = p;
    window.APP_CONFIG.projectId = p.id || p.project_id || window.APP_CONFIG.projectId || "";
    window.APP_CONFIG.projectPublicId = publicId;
    window.APP_CONFIG.currentProjectId = publicId;
    window.APP_CONFIG.projectName = p.name || p.display_name || p.displayName || window.APP_CONFIG.projectName || "";
    window.APP_CONFIG.projectIsNew = publicId === "new";

    const configured = boolFromValue(
      detail.isConfigured ??
        detail.is_configured ??
        p.is_configured ??
        p.isConfigured,
      false
    );

    window.APP_CONFIG.projectExists = publicId !== "new";
    window.APP_CONFIG.projectConfigured = configured;
    window.APP_CONFIG.projectToolsEnabled = window.APP_CONFIG.projectExists && configured;
    window.APP_CONFIG.projectSetupStatus = p.setup_status || p.setupStatus || (configured ? "configured" : "draft");

    if (!window.APP_CONFIG.workspacePaths || typeof window.APP_CONFIG.workspacePaths !== "object") {
      window.APP_CONFIG.workspacePaths = {};
    }

    const workspacePaths = window.APP_CONFIG.workspacePaths;

    if (paths.projectPagePath || paths.projectUrl) {
      window.APP_CONFIG.projectPagePath = paths.projectPagePath || paths.projectUrl;
      window.APP_CONFIG.projectUrl = paths.projectPagePath || paths.projectUrl;
      workspacePaths.projectPagePath = paths.projectPagePath || paths.projectUrl;
      workspacePaths.projectUrl = paths.projectPagePath || paths.projectUrl;
    } else if (publicId && publicId !== "new") {
      workspacePaths.projectPagePath = routeForProject("project");
      workspacePaths.projectUrl = routeForProject("project");
      window.APP_CONFIG.projectPagePath = workspacePaths.projectPagePath;
      window.APP_CONFIG.projectUrl = workspacePaths.projectUrl;
    }

    if (paths.projectPublicUrl) {
      window.APP_CONFIG.projectPublicUrl = paths.projectPublicUrl;
      workspacePaths.projectPublicUrl = paths.projectPublicUrl;
    } else if (publicId && publicId !== "new") {
      workspacePaths.projectPublicUrl = `/project=${encodePathPart(publicId)}`;
      window.APP_CONFIG.projectPublicUrl = workspacePaths.projectPublicUrl;
    }

    if (paths.editorPagePath) {
      window.APP_CONFIG.editorPagePath = paths.editorPagePath;
      workspacePaths.editorPagePath = paths.editorPagePath;
    } else if (publicId && publicId !== "new") {
      workspacePaths.editorPagePath = routeForProject("editor");
      window.APP_CONFIG.editorPagePath = workspacePaths.editorPagePath;
    }

    if (paths.initialEditorUrl) {
      window.APP_CONFIG.initialEditorUrl = paths.initialEditorUrl;
      workspacePaths.initialEditorUrl = paths.initialEditorUrl;
    } else if (workspacePaths.editorPagePath) {
      window.APP_CONFIG.initialEditorUrl = workspacePaths.editorPagePath;
      workspacePaths.initialEditorUrl = workspacePaths.editorPagePath;
    }

    if (paths.mapPagePath) {
      window.APP_CONFIG.mapPagePath = paths.mapPagePath;
      workspacePaths.mapPagePath = paths.mapPagePath;
    } else if (publicId && publicId !== "new") {
      workspacePaths.mapPagePath = routeForProject("map");
      window.APP_CONFIG.mapPagePath = workspacePaths.mapPagePath;
    }

    if (paths.cad2dPagePath) {
      window.APP_CONFIG.cad2dPagePath = paths.cad2dPagePath;
      workspacePaths.cad2dPagePath = paths.cad2dPagePath;
    } else if (publicId && publicId !== "new") {
      workspacePaths.cad2dPagePath = routeForProject("cad2d");
      window.APP_CONFIG.cad2dPagePath = workspacePaths.cad2dPagePath;
    }

    if (paths.lvPagePath) {
      window.APP_CONFIG.lvPagePath = paths.lvPagePath;
      workspacePaths.lvPagePath = paths.lvPagePath;
    } else if (publicId && publicId !== "new") {
      workspacePaths.lvPagePath = routeForProject("lv");
      window.APP_CONFIG.lvPagePath = workspacePaths.lvPagePath;
    }

    if (paths.adminPagePath) {
      window.APP_CONFIG.adminPagePath = paths.adminPagePath;
      workspacePaths.adminPagePath = paths.adminPagePath;
    } else if (publicId && publicId !== "new") {
      workspacePaths.adminPagePath = routeForProject("admin");
      window.APP_CONFIG.adminPagePath = workspacePaths.adminPagePath;
    }

    if (window.APP_CONFIG.projectSidebar && typeof window.APP_CONFIG.projectSidebar === "object") {
      window.APP_CONFIG.projectSidebar.currentProjectId = publicId;
      window.APP_CONFIG.projectSidebar.current_project_id = publicId;
      window.APP_CONFIG.projectSidebar.currentTitle = window.APP_CONFIG.projectName || "Aktuelles Projekt";
      window.APP_CONFIG.projectSidebar.currentSubtitle = configured ? "Projekt aktiv" : "Projekt definieren";
    }

    window.__VECTOPLAN_CURRENT_PROJECT__ = p;
  } catch (_) {}
}

function extractProjectFromDetail(detail) {
  try {
    if (!detail || typeof detail !== "object") return null;

    if (detail.project && typeof detail.project === "object") return detail.project;
    if (detail.payload && detail.payload.project && typeof detail.payload.project === "object") return detail.payload.project;
    if (detail.item && typeof detail.item === "object") return detail.item;

    return null;
  } catch (_) {
    return null;
  }
}

function handleProjectSaved(detail = {}, eventType = "vectoplan:project:saved") {
  try {
    const project = extractProjectFromDetail(detail);

    if (project) {
      updateAppConfigProject(project, detail);
      setProjectDataset(project, detail);
    }

    syncWorkspaceGating({ fallbackToProject: false });

    try {
      refreshProjectSidebar();
    } catch (_) {}

    try {
      void refreshVersionsUI();
    } catch (_) {}

    try {
      uiState.lastProjectEvent = {
        type: eventType,
        detail,
        project,
        at: Date.now(),
      };
    } catch (_) {}

    showStatus("");
  } catch (error) {
    try {
      console.warn("[project] saved event handling failed", error);
    } catch (_) {}
  }
}


/* ───────────────────────── Workspace URL resolution ───────────────────────── */

function projectUrl() {
  try {
    const candidate =
      pathValue("projectPagePath") ||
      pathValue("projectUrl") ||
      cfgValue("projectPagePath") ||
      cfgValue("projectUrl") ||
      dataValue("projectPagePath") ||
      routeForProject("project") ||
      DEFAULT_PROJECT_ROUTE;

    return sanitizeWorkspaceUrl(candidate, DEFAULT_PROJECT_ROUTE);
  } catch (_) {
    return DEFAULT_PROJECT_ROUTE;
  }
}

function editorUrl() {
  try {
    const fallback = routeForProject("editor") || projectUrl();

    const candidate =
      pathValue("editorPagePath") ||
      pathValue("initialEditorUrl") ||
      cfgValue("editorPagePath") ||
      cfgValue("initialEditorUrl") ||
      dataValue("editorPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("editor") || projectUrl();
  }
}

function mapUrl() {
  try {
    const fallback = routeForProject("map") || projectUrl();

    const candidate =
      pathValue("mapPagePath") ||
      cfgValue("mapPagePath") ||
      dataValue("mapPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("map") || projectUrl();
  }
}

function twoDPageUrl() {
  try {
    const fallback = routeForProject("cad2d") || projectUrl();

    const candidate =
      pathValue("cad2dPagePath") ||
      cfgValue("cad2dPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("cad2d") || projectUrl();
  }
}

function adminUrl() {
  try {
    const fallback = routeForProject("admin") || projectUrl();

    const candidate =
      pathValue("adminPagePath") ||
      cfgValue("adminPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("admin") || projectUrl();
  }
}

function lvUrl() {
  try {
    const fallback = routeForProject("lv") || projectUrl();

    const candidate =
      pathValue("lvPagePath") ||
      cfgValue("lvPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("lv") || projectUrl();
  }
}

async function resolve2dUrl() {
  try {
    const jsonPath =
      pathValue("cadEmbedJsonPath") ||
      cfgValue("cadEmbedJsonPath") ||
      routeForProject("cad-embed");

    if (!jsonPath) return twoDPageUrl();

    const data = await fetch(jsonPath, {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then((response) => response.json())
      .catch(() => null);

    const candidates = [
      data?.iframe_url,
      data?.embed_url,
      data?.viewer_url,
      data?.url,
      data?.src,
    ];

    for (const candidate of candidates) {
      const value = String(candidate || "").trim();
      if (value && !isUnsafeLegacyTarget(value)) return value;
    }

    return twoDPageUrl();
  } catch (_) {
    return twoDPageUrl();
  }
}


/* ───────────────────────── Workspace fallback UI ───────────────────────── */

function workspaceFallback() {
  try {
    return $("workspaceFallback") || document.getElementById("workspaceFallback");
  } catch (_) {
    return document.getElementById("workspaceFallback");
  }
}

function workspaceFallbackText() {
  try {
    return $("workspaceFallbackText") || document.getElementById("workspaceFallbackText");
  } catch (_) {
    return document.getElementById("workspaceFallbackText");
  }
}

function showWorkspaceFallback(message) {
  try {
    const box = workspaceFallback();
    const text = workspaceFallbackText();

    if (text) {
      text.textContent = String(message || "Bitte öffne die Ansicht in einem neuen Tab oder lade die Seite neu.");
    }

    if (box) {
      box.classList.remove("hidden");
      box.setAttribute("aria-hidden", "false");
    }
  } catch (_) {}
}

function hideWorkspaceFallback() {
  try {
    const box = workspaceFallback();
    if (!box) return;

    box.classList.add("hidden");
    box.setAttribute("aria-hidden", "true");
  } catch (_) {}
}


/* ───────────────────────── Workspace iframe control ───────────────────────── */

function viewerFrame() {
  try {
    return $("viewer-frame");
  } catch (_) {
    return document.getElementById("viewer-frame");
  }
}

function setRawOpenUrl(url) {
  try {
    const a = $("viewerOpenRawBtn");
    if (!a) return;

    const stable = stableLocalUrl(url || "#");
    a.href = stable || "#";
  } catch (_) {}
}

function setFrameTitle(mode) {
  try {
    const frame = viewerFrame();
    if (!frame) return;

    frame.setAttribute("title", MODE_TITLE[mode] || "Arbeitsbereich");
  } catch (_) {}
}

function setFrameDataset(frame, data) {
  try {
    if (!frame || !data || typeof data !== "object") return;

    for (const [key, value] of Object.entries(data)) {
      try {
        frame.dataset[key] = String(value == null ? "" : value);
      } catch (_) {}
    }
  } catch (_) {}
}

function applyIframeCapabilities(frame) {
  try {
    if (!frame) return;

    frame.setAttribute("allow", WORKSPACE_IFRAME_ALLOW);
    frame.setAttribute("referrerpolicy", WORKSPACE_IFRAME_REFERRER_POLICY);
    frame.setAttribute("loading", "eager");
    frame.setAttribute("frameborder", "0");

    try {
      frame.removeAttribute("sandbox");
    } catch (_) {}
  } catch (_) {}
}

function makeIframeLoadHandlers(frame, rawSrc, mode) {
  let loaded = false;
  let timeoutId = null;

  const normalizedMode = normalizeMode(mode);

  const clear = () => {
    try {
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = null;
    } catch (_) {}
  };

  const onLoad = () => {
    try {
      loaded = true;
      clear();
      hideWorkspaceFallback();

      try {
        uiState.lastWorkspaceLoad = {
          mode: normalizedMode,
          src: rawSrc,
          loadedAt: Date.now(),
        };
      } catch (_) {}
    } catch (_) {}
  };

  const onError = () => {
    try {
      loaded = false;
      clear();

      try {
        uiState.lastWorkspaceError = {
          mode: normalizedMode,
          src: rawSrc,
          errorAt: Date.now(),
        };
      } catch (_) {}
    } catch (_) {}
  };

  safeOn(frame, "load", onLoad);
  safeOn(frame, "error", onError);

  timeoutId = setTimeout(() => {
    try {
      if (!loaded) {
        showWorkspaceFallback("Arbeitsbereich lädt noch oder wurde vom Browser blockiert. Prüfe Console, CSP und X-Frame-Options.");
      }
    } catch (_) {}
  }, normalizedMode === "map" ? 6000 : 8000);

  return { onLoad, onError, clear };
}

function hardSwapIframe(nextSrc, options = {}) {
  try {
    const oldFrame = viewerFrame();
    const rawSrc = String(nextSrc || "").trim();

    if (!oldFrame || !rawSrc) return false;

    const mode = String(options.mode || "").trim();
    const normalizedMode = normalizeMode(mode || "project");
    const title = String(options.title || MODE_TITLE[normalizedMode] || oldFrame.getAttribute("title") || "Arbeitsbereich");

    if (isUnsafeLegacyTarget(rawSrc)) {
      const fallback =
        normalizedMode === "map"
          ? mapUrl()
          : normalizedMode === "project"
            ? projectUrl()
            : editorUrl();

      try {
        console.warn("[workspace] blocked unsafe iframe target", rawSrc, "fallback", fallback);
      } catch (_) {}

      return hardSwapIframe(cacheBustLocalUrl(fallback), { ...options, mode: normalizedMode });
    }

    hideWorkspaceFallback();

    try {
      uiState.lastViewerUrl = "";
      uiState.rawViewerUrl = rawSrc;
      uiState.workspaceMode = normalizedMode;
      uiState.viewerMode = normalizedMode;
    } catch (_) {}

    const fresh = document.createElement("iframe");
    fresh.id = oldFrame.id;
    fresh.className = oldFrame.className;

    fresh.setAttribute("title", title);
    applyIframeCapabilities(fresh);

    setFrameDataset(fresh, {
      mode: normalizedMode,
      target: rawSrc,
      swappedAt: Date.now(),
    });

    makeIframeLoadHandlers(fresh, rawSrc, normalizedMode);

    oldFrame.replaceWith(fresh);
    fresh.src = rawSrc;

    setRawOpenUrl(rawSrc);

    setTimeout(() => {
      try {
        if (!fresh.src || fresh.src === "about:blank") {
          fresh.src = rawSrc;
        }
      } catch (_) {
        try {
          fresh.src = rawSrc;
        } catch (__) {}
      }
    }, 800);

    return true;
  } catch (error) {
    try {
      console.warn("[workspace] iframe swap failed", error);
    } catch (_) {}
    showWorkspaceFallback("Arbeitsbereich konnte nicht initialisiert werden.");
    return false;
  }
}

function normalizeMode(mode) {
  try {
    const value = String(mode || "").trim().toLowerCase();

    if (value === "project") return "project";
    if (value === "projekt") return "project";
    if (value === "meta") return "project";
    if (value === "metadata") return "project";

    if (value === "editor") return "3d";
    if (value === "cad") return "2d";
    if (value === "cad2d") return "2d";
    if (value === "viewer") return "3d";
    if (value === "viewer3d") return "3d";
    if (value === "model") return "3d";
    if (value === "version") return "3d";

    if (["3d", "2d", "map", "lv", "admin"].includes(value)) return value;

    return "project";
  } catch (_) {
    return "project";
  }
}

function setModePressed(activeMode) {
  try {
    const mode = normalizeMode(activeMode);

    for (const [m, id] of Object.entries(MODE_TO_BUTTON_ID)) {
      const btn = $(id);
      if (!btn) continue;

      btn.setAttribute("aria-pressed", String(m === mode));
      btn.classList.toggle("is-active", m === mode);
    }
  } catch (_) {}
}

function isWorkspaceModeAllowed(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "project") return true;
    if (normalized === "admin") return projectExists();

    if (MODES_REQUIRING_CONFIGURED_PROJECT.has(normalized)) {
      return projectToolsEnabled();
    }

    return false;
  } catch (_) {
    return false;
  }
}

function workspaceModeDisabledMessage(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "admin" && !projectExists()) {
      return "Projekt zuerst erstellen. Danach ist Admin verfügbar.";
    }

    if (MODES_REQUIRING_CONFIGURED_PROJECT.has(normalized) && !projectToolsEnabled()) {
      return "Projekt zuerst speichern und konfigurieren. Danach sind Map, 3D, 2D und LV verfügbar.";
    }

    return "Dieser Arbeitsbereich ist aktuell nicht verfügbar.";
  } catch (_) {
    return "Dieser Arbeitsbereich ist aktuell nicht verfügbar.";
  }
}

function syncWorkspaceGating(options = {}) {
  try {
    const fallbackToProject = options.fallbackToProject !== false;
    const root = appRoot();
    const configured = projectConfigured();
    const exists = projectExists();
    const toolsEnabled = projectToolsEnabled();

    if (root && root.dataset) {
      root.dataset.projectConfigured = configured ? "true" : "false";
      root.dataset.projectToolsEnabled = toolsEnabled ? "true" : "false";
      root.dataset.projectIsNew = exists ? "false" : "true";
    }

    for (const [mode, id] of Object.entries(MODE_TO_BUTTON_ID)) {
      const btn = $(id);
      if (!btn) continue;

      const allowed = isWorkspaceModeAllowed(mode);

      btn.disabled = !allowed;
      btn.setAttribute("aria-disabled", allowed ? "false" : "true");
      btn.classList.toggle("is-disabled", !allowed);

      if (!allowed) {
        btn.title = workspaceModeDisabledMessage(mode);
      } else if (mode === "project") {
        btn.title = "Projekt";
      } else {
        btn.title = MODE_TITLE[mode] || "Arbeitsbereich";
      }
    }

    const versionsToggle = $("versionsToggleBtn");
    if (versionsToggle) {
      versionsToggle.disabled = !exists;
      versionsToggle.setAttribute("aria-disabled", exists ? "false" : "true");
      versionsToggle.classList.toggle("is-disabled", !exists);
      if (!exists) {
        versionsToggle.title = "Projekt zuerst erstellen. Danach sind Versionen verfügbar.";
      }
    }

    const openBtn = $("viewerOpenRawBtn");
    if (openBtn) {
      openBtn.setAttribute("aria-disabled", "false");
      openBtn.classList.remove("is-disabled");
    }

    const currentMode = normalizeMode(uiState.workspaceMode || dataValue("workspaceMode", "project"));
    if (fallbackToProject && !isWorkspaceModeAllowed(currentMode)) {
      void setWorkspaceMode("project", {
        persist: false,
        reason: "gating-fallback",
      });
    }
  } catch (_) {}
}

function setUiMode(mode) {
  try {
    const normalized = normalizeMode(mode);

    uiState.viewerMode = normalized;
    uiState.workspaceMode = normalized;

    const root = appRoot();
    if (root) {
      root.dataset.workspaceMode = normalized;
      root.dataset.viewerMode = normalized;
    }

    setModePressed(normalized);
    setFrameTitle(normalized);

    return normalized;
  } catch (_) {
    return normalizeMode(mode);
  }
}

async function persistWorkspaceMode(mode) {
  try {
    const statePath = pathValue("statePutPath") || cfgValue("statePutPath");
    if (!statePath || statePath === "__DISABLED__") return;

    const normalized = normalizeMode(mode);

    await fetch(statePath, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({
        mode: normalized === "3d" ? "editor" : normalized,
        workspace_mode: normalized,
        legacy_3d_backend: false,
        legacy_speckle: false,
        updated_at: Date.now(),
      }),
    }).catch(() => {});
  } catch (_) {}
}

async function setWorkspaceMode(mode, options = {}) {
  const requested = normalizeMode(mode);
  const shouldPersist = options.persist !== false;

  try {
    if (!isWorkspaceModeAllowed(requested)) {
      showStatus(workspaceModeDisabledMessage(requested));

      if (requested !== "project" && options.fallback !== false) {
        await setWorkspaceMode("project", {
          persist: false,
          reason: "mode-blocked",
          fallback: false,
        });
      }

      return false;
    }

    const normalized = setUiMode(requested);
    let target = "";

    if (normalized === "project") {
      target = projectUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "project",
        title: "Projekt",
      });
    } else if (normalized === "3d") {
      target = editorUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "3d",
        title: "VECTOPLAN Editor",
      });
    } else if (normalized === "map") {
      target = mapUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "map",
        title: "Karte",
      });
    } else if (normalized === "2d") {
      target = await resolve2dUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "2d",
        title: "2D Ansicht",
      });
    } else if (normalized === "lv") {
      target = lvUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "lv",
        title: "Leistungsverzeichnis",
      });
    } else if (normalized === "admin") {
      target = adminUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "admin",
        title: "Admin",
      });
    }

    if (shouldPersist) {
      await persistWorkspaceMode(normalized);
    }

    try {
      versionsClose();
    } catch (_) {}

    showStatus("");

    return true;
  } catch (error) {
    try {
      console.warn("[workspace] setWorkspaceMode failed", requested, error);
    } catch (_) {}
    showWorkspaceFallback("Arbeitsbereich konnte nicht gewechselt werden.");
    return false;
  }
}

async function applyInitialWorkspaceMode() {
  try {
    const currentFrame = viewerFrame();
    if (currentFrame) {
      applyIframeCapabilities(currentFrame);
      makeIframeLoadHandlers(
        currentFrame,
        currentFrame.getAttribute("src") || currentFrame.src || "",
        normalizeMode(cfgValue("defaultMode", dataValue("defaultMode", "project")))
      );
    }
  } catch (_) {}

  try {
    syncWorkspaceGating({ fallbackToProject: false });

    const queryMode = new URLSearchParams(location.search).get("mode");
    if (queryMode) {
      await setWorkspaceMode(queryMode, { persist: false });
      return;
    }

    const initialMode = cfgValue("defaultMode", dataValue("defaultMode", "project")) || "project";
    await setWorkspaceMode(initialMode || "project", { persist: false });
  } catch (_) {
    await setWorkspaceMode("project", { persist: false });
  }
}

function wireWorkspaceToolbar() {
  const bindings = [
    ["modeProjectBtn", "project"],
    ["modeMapBtn", "map"],
    ["mode3dBtn", "3d"],
    ["mode2dBtn", "2d"],
    ["modeLvBtn", "lv"],
    ["modeAdminBtn", "admin"],
  ];

  for (const [id, mode] of bindings) {
    const btn = $(id);
    if (!btn || btn._workspaceModeWired) continue;

    btn._workspaceModeWired = true;

    safeOn(btn, "click", (event) => {
      try {
        event.preventDefault();
      } catch (_) {}

      if (!isWorkspaceModeAllowed(mode)) {
        showStatus(workspaceModeDisabledMessage(mode));
        return;
      }

      void setWorkspaceMode(mode);
    });
  }

  syncWorkspaceGating({ fallbackToProject: false });
}


/* ───────────────────────── Project event bridge ───────────────────────── */

function wireProjectEventBridge() {
  try {
    if (window.__VECTOPLAN_PROJECT_BRIDGE_WIRED__) return;
    window.__VECTOPLAN_PROJECT_BRIDGE_WIRED__ = true;

    const eventNames = [
      "vectoplan:project:saved",
      "vectoplan:project:created",
      "vectoplan:project:updated",
      "vectoplan:project:configured",
    ];

    for (const eventName of eventNames) {
      safeOn(window, eventName, (event) => {
        try {
          handleProjectSaved(event?.detail || {}, eventName);
        } catch (_) {}
      });
    }

    safeOn(window, "message", (event) => {
      try {
        const data = event?.data;
        if (!data || typeof data !== "object") return;

        const type = String(data.type || data.kind || "").trim();

        if (!type.startsWith("vectoplan:project:")) return;

        handleProjectSaved(data.detail || data, type);
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Project sidebar bridge ───────────────────────── */

function projectSidebarRoot() {
  try {
    return document.querySelector(PROJECT_SIDEBAR_ROOT_SELECTOR);
  } catch (_) {
    return null;
  }
}

function projectSidebarApi() {
  try {
    return window.VectoplanProjectSidebar || window.__VECTOPLAN_PROJECT_SIDEBAR__ || null;
  } catch (_) {
    return null;
  }
}

function projectSidebarDataApi() {
  try {
    return window.VectoplanProjectSidebarData || window.__VECTOPLAN_PROJECT_SIDEBAR_DATA__ || null;
  } catch (_) {
    return null;
  }
}

function projectSidebarResizeApi() {
  try {
    return window.VectoplanProjectSidebarResize || window.__VECTOPLAN_PROJECT_SIDEBAR_RESIZE__ || null;
  } catch (_) {
    return null;
  }
}

function notifyShellLayoutChanged(reason = "project-sidebar") {
  try {
    uiState.lastLayoutChange = {
      reason,
      changedAt: Date.now(),
    };
  } catch (_) {}

  try {
    requestAnimationFrame(() => {
      try {
        window.dispatchEvent(new Event("resize"));
      } catch (_) {}
    });
  } catch (_) {
    try {
      window.dispatchEvent(new Event("resize"));
    } catch (__) {}
  }
}

function getProjectSidebarController() {
  try {
    const root = projectSidebarRoot();
    const api = projectSidebarApi();

    if (!root || !api || typeof api.getController !== "function") return null;

    return api.getController(root);
  } catch (_) {
    return null;
  }
}

function projectSidebarSnapshot() {
  try {
    const controller = getProjectSidebarController();
    if (controller && typeof controller.getSnapshot === "function") {
      return controller.getSnapshot();
    }

    const api = projectSidebarApi();
    if (api && typeof api.createDebugSnapshot === "function") {
      return api.createDebugSnapshot();
    }

    return null;
  } catch (_) {
    return null;
  }
}

function wireProjectSidebarEvents(root) {
  try {
    if (!root || root._vectoplanProjectSidebarEventsWired) return;
    root._vectoplanProjectSidebarEventsWired = true;

    safeOn(root, "vectoplan:project-sidebar:ready", (event) => {
      try {
        uiState.projectSidebarReady = true;
        uiState.projectSidebarSnapshot = event?.detail?.snapshot || projectSidebarSnapshot();
        notifyShellLayoutChanged("project-sidebar-ready");
      } catch (_) {}
    });

    safeOn(root, "vectoplan:project-sidebar:items-loaded", (event) => {
      try {
        uiState.projectSidebarItems = event?.detail?.items || [];
        uiState.projectSidebarCount = event?.detail?.count || 0;
        uiState.projectSidebarSource = event?.detail?.source || "";
      } catch (_) {}
    });

    safeOn(root, "vectoplan:project-sidebar:item-selected", (event) => {
      try {
        uiState.lastProjectSidebarSelection = {
          item: event?.detail?.item || null,
          href: event?.detail?.href || "",
          selectedAt: Date.now(),
        };
      } catch (_) {}
    });

    safeOn(root, "vectoplan:project-sidebar:collapsed-change", (event) => {
      try {
        uiState.projectSidebarCollapsed = !!event?.detail?.collapsed;
        uiState.projectSidebarSnapshot = projectSidebarSnapshot();
        notifyShellLayoutChanged("project-sidebar-collapsed-change");
      } catch (_) {
        notifyShellLayoutChanged("project-sidebar-collapsed-change");
      }
    });

    safeOn(root, "vectoplan:project-sidebar:resize", (event) => {
      try {
        uiState.projectSidebarWidth = event?.detail?.width || null;
        uiState.projectSidebarSnapshot = projectSidebarSnapshot();
        notifyShellLayoutChanged("project-sidebar-resize");
      } catch (_) {
        notifyShellLayoutChanged("project-sidebar-resize");
      }
    });

    safeOn(root, "vectoplan:project-sidebar:resize-end", (event) => {
      try {
        uiState.projectSidebarWidth = event?.detail?.width || null;
        uiState.projectSidebarSnapshot = projectSidebarSnapshot();
        notifyShellLayoutChanged("project-sidebar-resize-end");
      } catch (_) {
        notifyShellLayoutChanged("project-sidebar-resize-end");
      }
    });

    safeOn(root, "vectoplan:project-sidebar:error", (event) => {
      try {
        uiState.projectSidebarError = event?.detail?.error || true;
      } catch (_) {}
    });
  } catch (_) {}
}

function initProjectSidebar() {
  try {
    const root = projectSidebarRoot();
    if (!root) return null;

    const sidebarConfig = cfgObject("projectSidebar", {});
    const enabled = sidebarConfig && sidebarConfig.enabled !== false && sidebarConfig.enabled !== "false";

    if (!enabled) {
      try {
        root.classList.add("is-disabled");
        root.setAttribute("hidden", "");
        root.setAttribute("aria-hidden", "true");
      } catch (_) {}

      return null;
    }

    wireProjectSidebarEvents(root);

    const resizeApi = projectSidebarResizeApi();
    if (resizeApi && typeof resizeApi.init === "function") {
      safeCall("projectSidebarResize.init", () => resizeApi.init({ root }));
    }

    const api = projectSidebarApi();
    let controller = null;

    if (api && typeof api.init === "function") {
      controller = api.init({
        root,
        currentChatId: conversationId(),
        currentProjectId: projectPublicId(),
        items: Array.isArray(sidebarConfig.items) ? sidebarConfig.items : undefined,
      });
    }

    if (controller && typeof controller.rememberCurrent === "function") {
      safeCall("projectSidebar.rememberCurrent", () => controller.rememberCurrent());
    }

    try {
      uiState.projectSidebarReady = !!controller;
      uiState.projectSidebarSnapshot = projectSidebarSnapshot();
    } catch (_) {}

    notifyShellLayoutChanged("project-sidebar-init");

    return controller;
  } catch (error) {
    try {
      console.warn("[project-sidebar] init failed", error);
    } catch (_) {}
    return null;
  }
}

function refreshProjectSidebar() {
  try {
    const controller = getProjectSidebarController();

    if (controller && typeof controller.refresh === "function") {
      return controller.refresh({ source: "main-refresh" });
    }

    const api = projectSidebarApi();
    if (api && typeof api.refreshAll === "function") {
      return api.refreshAll();
    }

    return null;
  } catch (_) {
    return null;
  }
}


/* ───────────────────────── Theme ───────────────────────── */

function themeGet() {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch (_) {}

  try {
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr === "dark" || attr === "light") return attr;
  } catch (_) {}

  return "light";
}

function themeApply(theme = "light") {
  try {
    const next = theme === "dark" ? "dark" : "light";
    const root = document.documentElement;

    root.setAttribute("data-theme", next);

    try {
      localStorage.setItem("theme", next);
    } catch (_) {}

    const btn = $("themeToggleBtn");
    if (btn) {
      const isDark = next === "dark";
      btn.setAttribute("aria-pressed", String(isDark));
      btn.textContent = isDark ? "☀️ Hell" : "🌙 Dunkel";
      btn.title = isDark
        ? "Auf helles Theme umschalten"
        : "Auf dunkles Theme umschalten";
    }

    try {
      const frame = viewerFrame();
      if (frame && frame.contentWindow) {
        frame.contentWindow.postMessage(
          {
            type: "vectoplan:theme:update",
            theme: next,
          },
          "*"
        );
      }
    } catch (_) {}
  } catch (_) {}
}

function themeToggle() {
  try {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    themeApply(current === "dark" ? "light" : "dark");
  } catch (_) {}
}

function wireThemeToggle() {
  try {
    if (window.__VECTOPLAN_THEME_WIRED__) return;
    window.__VECTOPLAN_THEME_WIRED__ = true;

    const btn = $("themeToggleBtn");

    if (btn && !btn._themeWired) {
      btn._themeWired = true;
      safeOn(btn, "click", themeToggle);
    }

    themeApply(themeGet());
  } catch (_) {}
}


/* ───────────────────────── Versions dropdown ───────────────────────── */

function versionsContainer() {
  return $("versionsDropdown") || $("versionsPanel") || null;
}

function isHidden(el) {
  try {
    if (!el) return true;
    if (el.classList.contains("hidden")) return true;
    if (String(el.getAttribute("aria-hidden") || "") === "true") return true;
    return false;
  } catch (_) {
    return true;
  }
}

function versionsOpen() {
  try {
    const toggle = $("versionsToggleBtn");
    const box = versionsContainer();

    if (!toggle || !box || toggle.disabled) return false;

    box.classList.remove("hidden");
    box.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");

    setTimeout(() => {
      try {
        const first = box.querySelector("button, [href], [tabindex]:not([tabindex='-1'])");
        if (first && typeof first.focus === "function") first.focus();
      } catch (_) {}
    }, 0);

    void refreshVersionsUI();

    return true;
  } catch (_) {
    return false;
  }
}

function versionsClose() {
  try {
    const toggle = $("versionsToggleBtn");
    const box = versionsContainer();

    if (!toggle || !box) return false;

    box.classList.add("hidden");
    box.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");

    return true;
  } catch (_) {
    return false;
  }
}

function versionsToggle() {
  try {
    const box = versionsContainer();
    if (!box) return false;

    if (isHidden(box)) return versionsOpen();

    versionsClose();
    return false;
  } catch (_) {
    return false;
  }
}

async function fetchVersionsRaw() {
  try {
    const path = pathValue("versionsPath") || cfgValue("versionsPath");
    if (!path || !projectExists()) return [];

    const data = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then((response) => response.json())
      .catch(() => null);

    if (Array.isArray(data?.items)) return data.items;
    if (Array.isArray(data?.versions)) return data.versions;

    return [];
  } catch (_) {
    return [];
  }
}

function setVersionCount(count) {
  try {
    const el = $("versCount");
    if (el) el.textContent = String(Number.isFinite(count) ? count : 0);
  } catch (_) {}
}

function neutralizeVersionViewerActions() {
  try {
    const list = $("versionsList");
    if (!list) return;

    const actionSelectors = [
      "[data-action='select-version']",
      "[data-action='open-viewer']",
      "[data-action='open-model']",
      "[data-action='open-version']",
    ];

    for (const selector of actionSelectors) {
      const buttons = list.querySelectorAll(selector);
      buttons.forEach((btn) => {
        try {
          btn.disabled = true;
          btn.setAttribute("aria-disabled", "true");
          btn.title = "Diese Version ist gespeichert. Die Editor-Integration lädt keine alte 3D-Viewer-Version.";
          if (!String(btn.textContent || "").trim()) {
            btn.textContent = "Gespeichert";
          }
        } catch (_) {}
      });
    }

    list.querySelectorAll("li").forEach((li) => {
      try {
        li.classList.remove("is-selected");
        li.setAttribute("aria-selected", "false");
      } catch (_) {}
    });
  } catch (_) {}
}

async function refreshVersionsUI() {
  try {
    await loadVersions();
  } catch (error) {
    try {
      console.warn("[versions] loadVersions failed", error);
    } catch (_) {}
  }

  try {
    const items = await fetchVersionsRaw();
    setVersionCount(items.length);
  } catch (_) {
    setVersionCount(0);
  }

  neutralizeVersionViewerActions();
}

function wireVersionsDropdown() {
  try {
    const toggle = $("versionsToggleBtn");
    const box = versionsContainer();

    if (!toggle || !box) return;
    if (toggle._versionsDropdownWired) return;

    toggle._versionsDropdownWired = true;

    try {
      if (box.id) toggle.setAttribute("aria-controls", box.id);
    } catch (_) {}

    safeOn(toggle, "click", (event) => {
      try {
        event.preventDefault();
      } catch (_) {}
      versionsToggle();
    });

    const onOutside = (event) => {
      try {
        const currentBox = versionsContainer();
        if (!currentBox || isHidden(currentBox)) return;

        const target = event?.target;
        if (!target) return;
        if (currentBox.contains(target)) return;
        if (toggle.contains(target)) return;

        versionsClose();
      } catch (_) {}
    };

    safeOn(document, "mousedown", onOutside, {
      capture: true,
      passive: true,
    });

    safeOn(document, "touchstart", onOutside, {
      capture: true,
      passive: true,
    });

    safeOn(document, "keydown", (event) => {
      try {
        if (event.key === "Escape") versionsClose();
      } catch (_) {}
    });

    safeOn(window, "blur", () => {
      try {
        versionsClose();
      } catch (_) {}
    }, { passive: true });

    const refreshBtn = $("versRefreshBtn");
    if (refreshBtn && !refreshBtn._refreshWired) {
      refreshBtn._refreshWired = true;
      safeOn(refreshBtn, "click", (event) => {
        try {
          event.preventDefault();
        } catch (_) {}
        void refreshVersionsUI();
      });
    }

    const list = $("versionsList");
    if (list && !list._neutralObserverWired) {
      list._neutralObserverWired = true;

      const observer = new MutationObserver(() => {
        neutralizeVersionViewerActions();
      });

      observer.observe(list, {
        childList: true,
        subtree: true,
      });
    }
  } catch (_) {}
}


/* ───────────────────────── 2D event bridge ───────────────────────── */

function wire2dEventBridge() {
  try {
    if (window.__VECTOPLAN_2D_BRIDGE_WIRED__) return;
    window.__VECTOPLAN_2D_BRIDGE_WIRED__ = true;

    async function putState(patch) {
      try {
        const statePath = pathValue("statePutPath") || cfgValue("statePutPath");
        if (!statePath || statePath === "__DISABLED__") return;

        await fetch(statePath, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          cache: "no-store",
          body: JSON.stringify(patch || {}),
        }).catch(() => {});
      } catch (_) {}
    }

    window.addEventListener("cad2d-select", (event) => {
      try {
        const items = event?.detail?.items || [];
        uiState.last2DSelection = items;

        void putState({
          mode: "2d",
          workspace_mode: "2d",
          last_2d_selection: items,
          last_2d_selection_ts: Date.now(),
          legacy_3d_backend: false,
        });
      } catch (_) {}
    });

    window.addEventListener("cad2d-hover", (event) => {
      try {
        uiState.last2DHover = event?.detail?.item || null;
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Editor message bridge ───────────────────────── */

function wireEditorEventBridge() {
  try {
    if (window.__VECTOPLAN_EDITOR_BRIDGE_WIRED__) return;
    window.__VECTOPLAN_EDITOR_BRIDGE_WIRED__ = true;

    window.addEventListener("message", (event) => {
      try {
        if (!event || !event.data) return;

        const data = event.data;
        const type = String(data.type || data.kind || "").toLowerCase();

        if (!type.includes("editor") && !type.includes("vectoplan")) {
          return;
        }

        uiState.lastEditorMessage = data;
        uiState.lastEditorMessageTs = Date.now();

        if (type.includes("ready")) {
          uiState.editorReady = true;
          hideWorkspaceFallback();
        }

        if (type.includes("selection")) {
          uiState.lastEditorSelection = data.selection || data.payload || data;
        }

        if (type.includes("error")) {
          try {
            showStatus("Editor meldet einen Fehler. Details stehen in der Browser-Konsole.");
          } catch (_) {}
        }
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Status / hotkeys ───────────────────────── */

function showStatus(message) {
  try {
    const box = $("systemAlert");
    if (!box) {
      if (message) {
        try {
          console.info("[vectoplan]", message);
        } catch (_) {}
      }
      return;
    }

    const text = String(message || "");
    box.textContent = text;
    box.classList.toggle("hidden", !text);
  } catch (_) {}
}

function wireGlobalStatus() {
  try {
    safeOn(window, "offline", () => {
      showStatus("Offline erkannt. Vorgänge werden eventuell verzögert.");
    });

    safeOn(window, "online", () => {
      showStatus("");
    });
  } catch (_) {}
}

function wireHotkeys() {
  try {
    if (window.__VECTOPLAN_HOTKEYS_WIRED__) return;
    window.__VECTOPLAN_HOTKEYS_WIRED__ = true;

    safeOn(document, "keydown", (event) => {
      try {
        if (event.altKey && event.key.toLowerCase() === "p") {
          event.preventDefault();
          void setWorkspaceMode("project");
        }

        if (event.altKey && event.key === "1") {
          event.preventDefault();
          void setWorkspaceMode("project");
        }

        if (event.altKey && event.key === "2") {
          event.preventDefault();
          void setWorkspaceMode("map");
        }

        if (event.altKey && event.key === "3") {
          event.preventDefault();
          void setWorkspaceMode("3d");
        }

        if (event.altKey && event.key === "4") {
          event.preventDefault();
          void setWorkspaceMode("2d");
        }

        if (event.altKey && event.key === "5") {
          event.preventDefault();
          void setWorkspaceMode("lv");
        }

        if (event.key === "Escape") {
          versionsClose();
        }
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Refresh glue ───────────────────────── */

function wireRefreshEvents() {
  try {
    if (window.__VECTOPLAN_REFRESH_EVENTS_WIRED__) return;
    window.__VECTOPLAN_REFRESH_EVENTS_WIRED__ = true;

    window.addEventListener("versions:refresh", () => {
      try {
        void refreshVersionsUI();
      } catch (_) {}
    });

    window.addEventListener("project-sidebar:refresh", () => {
      try {
        refreshProjectSidebar();
      } catch (_) {}
    });

    window.addEventListener("vectoplan:workspace:refresh", () => {
      try {
        const mode = normalizeMode(uiState.workspaceMode || dataValue("workspaceMode", "project"));
        void setWorkspaceMode(mode, { persist: false });
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Diagnostics ───────────────────────── */

function exposeWorkspaceDebugApi() {
  try {
    if (window.__VECTOPLAN_WORKSPACE_DEBUG__) return;

    window.__VECTOPLAN_WORKSPACE_DEBUG__ = {
      cfg,
      appRoot,
      conversationId,
      project: {
        current: currentProject,
        id: projectId,
        publicId: projectPublicId,
        exists: projectExists,
        configured: projectConfigured,
        toolsEnabled: projectToolsEnabled,
        url: projectUrl,
        syncGating: syncWorkspaceGating,
        updateConfig: updateAppConfigProject,
      },
      projectUrl,
      editorUrl,
      mapUrl,
      twoDPageUrl,
      lvUrl,
      adminUrl,
      setWorkspaceMode,
      viewerFrame,
      isUnsafeLegacyTarget,
      stableLocalUrl,
      cacheBustLocalUrl,
      projectSidebar: {
        root: projectSidebarRoot,
        api: projectSidebarApi,
        dataApi: projectSidebarDataApi,
        resizeApi: projectSidebarResizeApi,
        controller: getProjectSidebarController,
        snapshot: projectSidebarSnapshot,
        refresh: refreshProjectSidebar,
      },
    };
  } catch (_) {}
}


/* ───────────────────────── Boot ───────────────────────── */

async function boot() {
  if (window.__VECTOPLAN_APP_BOOTED__) return;
  window.__VECTOPLAN_APP_BOOTED__ = true;

  safeCall("exposeWorkspaceDebugApi", exposeWorkspaceDebugApi);

  safeCall("initProjectSidebar", initProjectSidebar);

  safeCall("wireThemeToggle", wireThemeToggle);
  safeCall("wireGlobalStatus", wireGlobalStatus);
  safeCall("wireHotkeys", wireHotkeys);

  safeCall("wireProjectEventBridge", wireProjectEventBridge);
  safeCall("wireWorkspaceToolbar", wireWorkspaceToolbar);
  safeCall("wireVersionsDropdown", wireVersionsDropdown);

  safeCall("wire2dEventBridge", wire2dEventBridge);
  safeCall("wireEditorEventBridge", wireEditorEventBridge);
  safeCall("wireRefreshEvents", wireRefreshEvents);

  await safeAwait("applyInitialWorkspaceMode", applyInitialWorkspaceMode);

  safeCall("initialVersionsCount", () => {
    void (async () => {
      const items = await fetchVersionsRaw();
      setVersionCount(items.length);
      neutralizeVersionViewerActions();
      syncWorkspaceGating({ fallbackToProject: false });
    })();
  });
}

void boot();