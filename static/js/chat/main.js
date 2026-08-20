// services/vectoplan-app/static/js/chat/main.js
// Orchestrator for the VECTOPLAN project/workspace shell.
// Responsibilities:
// - initialize the project sidebar
// - switch workspace iframe between Project, Map, Editor, 2D, LV and project services
// - start project-first, not editor-first
// - keep Map/3D/2D/LV gated until the project is configured
// - connect project-scoped service areas to the workspace iframe
// - avoid any legacy 3D backend calls
// - never use Docker-internal URLs as browser iframe targets
// - no visible chat UI, no composer, no transcript, no chat drawer

import { $, uiState } from "./core.js";
import { getConfig } from "./api.js";
import { loadVersions } from "./versions.js";


/* ───────────────────────── Constants ───────────────────────── */

const WORKSPACE_IFRAME_ALLOW = "fullscreen; pointer-lock; clipboard-read; clipboard-write";
const WORKSPACE_IFRAME_REFERRER_POLICY = "no-referrer";

const DEFAULT_PROJECT_ROUTE = "/ui/project/new/project";

const PROJECT_SIDEBAR_ROOT_SELECTOR = "[data-vp-project-sidebar]";

const LEGACY_OR_INTERNAL_BROWSER_TARGETS = [
  "http://localhost:8090",
  "http://127.0.0.1:8090",
  "http://openlayer:8090",
  "http://vectoplan-openlayer:8090",
  "http://server-openlayer:8090",
  "http://vectoplan-editor:5000",
  "http://editor:5000",
  "http://server-editor:5000",
];

const INTERNAL_HOSTNAMES = new Set([
  "openlayer",
  "vectoplan-openlayer",
  "server-openlayer",
  "vectoplan-editor",
  "editor",
  "server-editor",
  "vectoplan-chunk",
  "chunk",
  "server-chunk",
]);

const MODE_TO_BUTTON_ID = {
  project: "modeProjectBtn",
  map: "modeMapBtn",
  "3d": "mode3dBtn",
  "2d": "mode2dBtn",
  lv: "modeLvBtn",
  files: "modeFilesBtn",
  structural_calculation: "modeStructuralCalculationBtn",
  energy_calculation: "modeEnergyCalculationBtn",
  sound_protection_calculation: "modeSoundProtectionCalculationBtn",
};

const MODE_TITLE = {
  project: "Einstellungen",
  "3d": "VECTOPLAN Editor",
  editor: "VECTOPLAN Editor",
  editor3d: "VECTOPLAN Editor",
  map: "Karte",
  "2d": "CAD",
  cad2d: "CAD",
  lv: "Leistungsverzeichnis",
  files: "Dateien",
  structural_calculation: "Tragwerksberechnung",
  energy_calculation: "Energieberechnung",
  sound_protection_calculation: "Schallschutzberechnung",
};

const MODES_REQUIRING_CONFIGURED_PROJECT = new Set([
  "map", "3d", "2d", "lv", "files", "structural_calculation", "energy_calculation", "sound_protection_calculation",
]);
const MODES_REQUIRING_EXISTING_PROJECT = new Set(MODES_REQUIRING_CONFIGURED_PROJECT);
const PROJECT_PUBLIC_ID_RE = /^(?:prj|demo)_[A-Za-z0-9_-]{8,160}$/;
const PLATFORM_PARENT_ORIGIN_SESSION_KEY = "vectoplan.platform.parentOrigin.v1";


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

function canManageProject() {
  try {
    if (cfgBool("canManageProject", false)) return true;

    const p = currentProject();
    const access = p.access && typeof p.access === "object" ? p.access : {};
    const permissions = access.permissions && typeof access.permissions === "object" ? access.permissions : {};

    return boolFromValue(
      access.can_manage ||
        access.canManage ||
        permissions.manage ||
        permissions.manage_settings ||
        permissions.manageSettings,
      false
    );
  } catch (_) {
    return false;
  }
}


/* ───────────────────────── Mode / tab helpers ───────────────────────── */

function normalizeMode(mode) {
  try {
    const value = String(mode || "").trim().toLowerCase().replace(/-/g, "_");

    if (["project", "projekt", "meta", "metadata", "info", "overview"].includes(value)) return "project";

    if (["editor", "editor3d", "editor_3d", "viewer", "viewer3d", "model", "3d"].includes(value)) return "3d";

    if (["cad", "cad2d", "cad_2d", "plan", "plan2d", "2d"].includes(value)) return "2d";

    if (["map", "karte", "openlayer", "openlayers"].includes(value)) return "map";

    if (["lv", "boq", "leistungsverzeichnis"].includes(value)) return "lv";

    if (["files", "dateien", "filecloud"].includes(value)) return "files";

    if (["structural_calculation", "tragwerksberechnung", "tragwerk", "statik"].includes(value)) return "structural_calculation";

    if (["energy_calculation", "energieberechnung", "energie"].includes(value)) return "energy_calculation";

    if (["sound_protection_calculation", "schallschutzberechnung", "schallschutz"].includes(value)) return "sound_protection_calculation";

    if (["versions", "version", "versionen", "history"].includes(value)) return "versions";

    if (["admin", "settings", "team", "permissions", "rechte"].includes(value)) return "admin";

    return "project";
  } catch (_) {
    return "project";
  }
}

function canonicalTabKey(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "3d") return "editor3d";
    if (normalized === "2d") return "cad2d";

    return normalized;
  } catch (_) {
    return "project";
  }
}

function workspaceTabs() {
  try {
    const tabs = cfgObject("workspaceTabs", {});
    return tabs && typeof tabs === "object" && !Array.isArray(tabs) ? tabs : {};
  } catch (_) {
    return {};
  }
}

function tabConfig(mode) {
  try {
    const tabs = workspaceTabs();
    const normalized = normalizeMode(mode);
    const key = canonicalTabKey(normalized);

    const candidates = [
      key,
      normalized,
      mode,
      String(mode || "").trim(),
    ];

    for (const candidate of candidates) {
      if (candidate && tabs[candidate] && typeof tabs[candidate] === "object") {
        return tabs[candidate];
      }
    }

    return {};
  } catch (_) {
    return {};
  }
}

function tabEnabled(mode, fallback = false) {
  try {
    const tab = tabConfig(mode);

    if (tab && Object.prototype.hasOwnProperty.call(tab, "enabled")) {
      return boolFromValue(tab.enabled, fallback);
    }

    return !!fallback;
  } catch (_) {
    return !!fallback;
  }
}

function buttonForMode(mode) {
  try {
    const normalized = normalizeMode(mode);
    const id = MODE_TO_BUTTON_ID[normalized];
    return id ? $(id) : null;
  } catch (_) {
    return null;
  }
}

function buttonWorkspacePath(mode) {
  try {
    const btn = buttonForMode(mode);
    if (!btn || !btn.dataset) return "";

    return String(btn.dataset.workspacePath || "").trim();
  } catch (_) {
    return "";
  }
}


/* ───────────────────────── URL safety ───────────────────────── */

function routeForProject(pathSuffix = "project") {
  try {
    const publicId = projectPublicId();
    const suffix = String(pathSuffix || "project").trim().toLowerCase().replace(/-/g, "_");

    if (!publicId || publicId === "new") {
      return suffix === "project" ? DEFAULT_PROJECT_ROUTE : "";
    }

    const id = encodePathPart(publicId);

    if (suffix === "project") return `/ui/project/${id}/project`;
    if (["editor", "editor3d", "editor_3d", "3d", "viewer", "viewer3d"].includes(suffix)) return `/ui/project/${id}/editor3d`;
    if (suffix === "map") return `/ui/project/${id}/map`;
    if (["cad2d", "cad_2d", "2d"].includes(suffix)) return `/ui/project/${id}/cad2d`;
    if (suffix === "lv") return `/ui/project/${id}/lv`;
    if (suffix === "files") return `/ui/project/${id}/files`;
    if (suffix === "structural_calculation") return `/ui/project/${id}/structural-calculation`;
    if (suffix === "energy_calculation") return `/ui/project/${id}/energy-calculation`;
    if (suffix === "sound_protection_calculation") return `/ui/project/${id}/sound-protection-calculation`;
    if (suffix === "versions") return `/ui/project/${id}/versions`;
    if (suffix === "admin") return `/ui/project/${id}/admin`;
    if (suffix === "plan2d") return `/ui/project/${id}/plan2d.json`;
    if (suffix === "cad_embed" || suffix === "cad-embed") return `/ui/project/${id}/cad-embed.json`;
    if (suffix === "map_json" || suffix === "map-json") return `/ui/project/${id}/map.json`;
    if (suffix === "context") return `/ui/project/${id}/context.json`;

    return `/ui/project/${id}/project`;
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

      if (INTERNAL_HOSTNAMES.has(host)) {
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
    const fallbackRaw = normalizeRelativeUrl(fallback);

    if (!raw || isUnsafeLegacyTarget(raw)) {
      if (fallbackRaw && fallbackRaw !== raw && !isUnsafeLegacyTarget(fallbackRaw)) {
        return fallbackRaw;
      }

      return projectUrl();
    }

    return raw;
  } catch (_) {
    return normalizeRelativeUrl(fallback) || projectUrl();
  }
}


/* ───────────────────────── Workspace URL resolution ───────────────────────── */

function projectUrl() {
  try {
    const fallback = DEFAULT_PROJECT_ROUTE;

    const candidate =
      buttonWorkspacePath("project") ||
      pathValue("projectPagePath") ||
      pathValue("projectUrl") ||
      cfgValue("projectPagePath") ||
      cfgValue("projectUrl") ||
      dataValue("projectPagePath") ||
      routeForProject("project") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return DEFAULT_PROJECT_ROUTE;
  }
}

function editorUrl() {
  try {
    const fallback = routeForProject("editor3d") || projectUrl();

    const candidate =
      buttonWorkspacePath("3d") ||
      pathValue("editor3dPagePath") ||
      pathValue("editorPagePath") ||
      pathValue("initialEditorUrl") ||
      cfgValue("editor3dPagePath") ||
      cfgValue("editorPagePath") ||
      cfgValue("initialEditorUrl") ||
      dataValue("editor3dPagePath") ||
      dataValue("editorPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("editor3d") || projectUrl();
  }
}

function mapUrl() {
  try {
    const fallback = routeForProject("map") || projectUrl();

    const candidate =
      buttonWorkspacePath("map") ||
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
      buttonWorkspacePath("2d") ||
      pathValue("cad2dPagePath") ||
      cfgValue("cad2dPagePath") ||
      dataValue("cad2dPagePath") ||
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
      buttonWorkspacePath("admin") ||
      pathValue("adminPagePath") ||
      cfgValue("adminPagePath") ||
      dataValue("adminPagePath") ||
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
      buttonWorkspacePath("lv") ||
      pathValue("lvPagePath") ||
      cfgValue("lvPagePath") ||
      dataValue("lvPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("lv") || projectUrl();
  }
}

function versionsPageUrl() {
  try {
    const fallback = routeForProject("versions") || projectUrl();

    const candidate =
      buttonWorkspacePath("versions") ||
      pathValue("versionsPagePath") ||
      cfgValue("versionsPagePath") ||
      dataValue("versionsPagePath") ||
      fallback;

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject("versions") || projectUrl();
  }
}

function versionsApiUrl() {
  try {
    const candidate =
      pathValue("versionsPath") ||
      cfgValue("versionsPath") ||
      cfgValue("versionsApiPath") ||
      "";

    return sanitizeWorkspaceUrl(candidate, "");
  } catch (_) {
    return "";
  }
}

function projectServiceUrl(mode, pathKey, routeSuffix) {
  try {
    const fallback = routeForProject(routeSuffix) || projectUrl();
    const candidate =
      buttonWorkspacePath(mode) ||
      pathValue(pathKey) ||
      cfgValue(pathKey) ||
      dataValue(pathKey.charAt(0).toLowerCase() + pathKey.slice(1)) ||
      fallback;
    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return routeForProject(routeSuffix) || projectUrl();
  }
}

function filesUrl() {
  return projectServiceUrl("files", "filesPagePath", "files");
}

function structuralCalculationUrl() {
  return projectServiceUrl("structural_calculation", "structuralCalculationPagePath", "structural_calculation");
}

function energyCalculationUrl() {
  return projectServiceUrl("energy_calculation", "energyCalculationPagePath", "energy_calculation");
}

function soundProtectionCalculationUrl() {
  return projectServiceUrl("sound_protection_calculation", "soundProtectionCalculationPagePath", "sound_protection_calculation");
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


/* ───────────────────────── Project state updates ───────────────────────── */

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
        detail.setupStatus ||
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
    } else if (publicId && publicId !== "new") {
      root.dataset.projectPagePath = `/ui/project/${encodePathPart(publicId)}/project`;
    }

    if (paths.editor3dPagePath || paths.editorPagePath || paths.initialEditorUrl) {
      root.dataset.editor3dPagePath = String(paths.editor3dPagePath || paths.editorPagePath || paths.initialEditorUrl);
      root.dataset.editorPagePath = String(paths.editor3dPagePath || paths.editorPagePath || paths.initialEditorUrl);
    } else if (publicId && publicId !== "new") {
      root.dataset.editor3dPagePath = `/ui/project/${encodePathPart(publicId)}/editor3d`;
      root.dataset.editorPagePath = `/ui/project/${encodePathPart(publicId)}/editor3d`;
    }

    if (paths.mapPagePath) {
      root.dataset.mapPagePath = String(paths.mapPagePath);
    } else if (publicId && publicId !== "new") {
      root.dataset.mapPagePath = `/ui/project/${encodePathPart(publicId)}/map`;
    }

    if (paths.cad2dPagePath) {
      root.dataset.cad2dPagePath = String(paths.cad2dPagePath);
    } else if (publicId && publicId !== "new") {
      root.dataset.cad2dPagePath = `/ui/project/${encodePathPart(publicId)}/cad2d`;
    }

    if (paths.lvPagePath) {
      root.dataset.lvPagePath = String(paths.lvPagePath);
    } else if (publicId && publicId !== "new") {
      root.dataset.lvPagePath = `/ui/project/${encodePathPart(publicId)}/lv`;
    }

    if (paths.versionsPagePath) {
      root.dataset.versionsPagePath = String(paths.versionsPagePath);
    } else if (publicId && publicId !== "new") {
      root.dataset.versionsPagePath = `/ui/project/${encodePathPart(publicId)}/versions`;
    }

    if (paths.adminPagePath) {
      root.dataset.adminPagePath = String(paths.adminPagePath);
    } else if (publicId && publicId !== "new") {
      root.dataset.adminPagePath = `/ui/project/${encodePathPart(publicId)}/admin`;
    }
  } catch (_) {}
}

function ensureWorkspaceTabConfig(key, patch) {
  try {
    if (!window.APP_CONFIG || typeof window.APP_CONFIG !== "object") {
      window.APP_CONFIG = {};
    }

    if (!window.APP_CONFIG.workspaceTabs || typeof window.APP_CONFIG.workspaceTabs !== "object") {
      window.APP_CONFIG.workspaceTabs = {};
    }

    const tabs = window.APP_CONFIG.workspaceTabs;
    const existing = tabs[key] && typeof tabs[key] === "object" ? tabs[key] : {};
    tabs[key] = {
      ...existing,
      ...(patch || {}),
    };

    return tabs[key];
  } catch (_) {
    return {};
  }
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

    const encodedPublicId = encodePathPart(publicId);
    const configured = boolFromValue(
      detail.isConfigured ??
        detail.is_configured ??
        p.is_configured ??
        p.isConfigured,
      false
    );

    window.APP_CONFIG.project = p;
    window.APP_CONFIG.currentProject = p;
    window.APP_CONFIG.projectId = p.id || p.project_id || window.APP_CONFIG.projectId || "";
    window.APP_CONFIG.projectPublicId = publicId;
    window.APP_CONFIG.currentProjectId = publicId;
    window.APP_CONFIG.projectName = p.name || p.display_name || p.displayName || window.APP_CONFIG.projectName || "";
    window.APP_CONFIG.projectIsNew = publicId === "new";
    window.APP_CONFIG.projectExists = publicId !== "new";
    window.APP_CONFIG.projectConfigured = configured;
    window.APP_CONFIG.projectToolsEnabled = window.APP_CONFIG.projectExists && configured;
    window.APP_CONFIG.projectSetupStatus = p.setup_status || p.setupStatus || (configured ? "configured" : "draft");

    if (!window.APP_CONFIG.workspacePaths || typeof window.APP_CONFIG.workspacePaths !== "object") {
      window.APP_CONFIG.workspacePaths = {};
    }

    const workspacePaths = window.APP_CONFIG.workspacePaths;

    const projectPath = paths.projectPagePath || paths.projectUrl || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/project` : DEFAULT_PROJECT_ROUTE);
    const editorPath = paths.editor3dPagePath || paths.editorPagePath || paths.initialEditorUrl || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/editor3d` : "");
    const mapPath = paths.mapPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/map` : "");
    const cad2dPath = paths.cad2dPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/cad2d` : "");
    const lvPath = paths.lvPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/lv` : "");
    const filesPath = paths.filesPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/files` : "");
    const structuralCalculationPath = paths.structuralCalculationPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/structural-calculation` : "");
    const energyCalculationPath = paths.energyCalculationPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/energy-calculation` : "");
    const soundProtectionCalculationPath = paths.soundProtectionCalculationPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/sound-protection-calculation` : "");
    const versionsPagePath = paths.versionsPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/versions` : "");
    const versionsPath = paths.versionsPath || (publicId && publicId !== "new" ? `/v1/projects/${encodedPublicId}/versions` : "");
    const adminPath = paths.adminPagePath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/admin` : "");
    const contextPath = paths.contextPath || (publicId && publicId !== "new" ? `/ui/project/${encodedPublicId}/context.json` : "/ui/project/new/context.json");

    window.APP_CONFIG.projectPagePath = projectPath;
    window.APP_CONFIG.projectUrl = projectPath;
    workspacePaths.projectPagePath = projectPath;
    workspacePaths.projectUrl = projectPath;

    workspacePaths.contextPath = contextPath;

    if (paths.projectPublicUrl) {
      window.APP_CONFIG.projectPublicUrl = paths.projectPublicUrl;
      workspacePaths.projectPublicUrl = paths.projectPublicUrl;
    } else if (publicId && publicId !== "new") {
      workspacePaths.projectPublicUrl = `/project=${encodedPublicId}`;
      window.APP_CONFIG.projectPublicUrl = workspacePaths.projectPublicUrl;
    }

    if (editorPath) {
      window.APP_CONFIG.editorPagePath = editorPath;
      window.APP_CONFIG.editor3dPagePath = editorPath;
      window.APP_CONFIG.initialEditorUrl = editorPath;
      workspacePaths.editorPagePath = editorPath;
      workspacePaths.editor3dPagePath = editorPath;
      workspacePaths.initialEditorUrl = editorPath;
    }

    if (mapPath) {
      window.APP_CONFIG.mapPagePath = mapPath;
      workspacePaths.mapPagePath = mapPath;
    }

    if (cad2dPath) {
      window.APP_CONFIG.cad2dPagePath = cad2dPath;
      workspacePaths.cad2dPagePath = cad2dPath;
    }

    if (lvPath) {
      window.APP_CONFIG.lvPagePath = lvPath;
      workspacePaths.lvPagePath = lvPath;
    }

    if (filesPath) {
      window.APP_CONFIG.filesPagePath = filesPath;
      workspacePaths.filesPagePath = filesPath;
    }

    if (structuralCalculationPath) {
      window.APP_CONFIG.structuralCalculationPagePath = structuralCalculationPath;
      workspacePaths.structuralCalculationPagePath = structuralCalculationPath;
    }

    if (energyCalculationPath) {
      window.APP_CONFIG.energyCalculationPagePath = energyCalculationPath;
      workspacePaths.energyCalculationPagePath = energyCalculationPath;
    }

    if (soundProtectionCalculationPath) {
      window.APP_CONFIG.soundProtectionCalculationPagePath = soundProtectionCalculationPath;
      workspacePaths.soundProtectionCalculationPagePath = soundProtectionCalculationPath;
    }

    if (versionsPagePath) {
      window.APP_CONFIG.versionsPagePath = versionsPagePath;
      workspacePaths.versionsPagePath = versionsPagePath;
    }

    if (versionsPath) {
      window.APP_CONFIG.versionsPath = versionsPath;
      workspacePaths.versionsPath = versionsPath;
    }

    if (adminPath) {
      window.APP_CONFIG.adminPagePath = adminPath;
      workspacePaths.adminPagePath = adminPath;
    }

    const enabled = publicId !== "new" && configured;
    const adminEnabled = publicId !== "new" && canManageProject();
    const projectAccess = p.access && typeof p.access === "object" ? p.access : {};
    const projectPermissions = projectAccess.permissions && typeof projectAccess.permissions === "object"
      ? projectAccess.permissions
      : {};
    const currentUser = window.APP_CONFIG.currentUser && typeof window.APP_CONFIG.currentUser === "object"
      ? window.APP_CONFIG.currentUser
      : {};
    const currentUserAuth = currentUser.auth && typeof currentUser.auth === "object" ? currentUser.auth : currentUser;
    const currentUserAccess = currentUserAuth.access && typeof currentUserAuth.access === "object"
      ? currentUserAuth.access
      : {};
    const entitlements = Array.isArray(currentUserAuth.entitlements)
      ? currentUserAuth.entitlements
      : Array.isArray(currentUserAccess.entitlements)
        ? currentUserAccess.entitlements
        : [];
    const authenticated = boolFromValue(window.APP_CONFIG.authenticated, false);
    const persistentUser = boolFromValue(window.APP_CONFIG.persistent, false);
    const demoUser = boolFromValue(window.APP_CONFIG.demoMode, false);
    const projectCanView = boolFromValue(projectAccess.can_view ?? projectAccess.canView ?? projectPermissions.view, false);
    const publicViewerAccess = boolFromValue(
      projectAccess.public_viewer ?? projectAccess.publicViewer ?? projectAccess.is_public_viewer,
      false
    );
    const cloudEnabled = boolFromValue(
      currentUserAuth.can_use_cloud ?? currentUserAuth.canUseCloud ?? currentUserAccess.cloud_access,
      entitlements.map((value) => String(value)).includes("cloud_access")
    );
    const memberServiceEnabled = enabled && authenticated && persistentUser && projectCanView && !publicViewerAccess && !demoUser;
    const filesEnabled = memberServiceEnabled && cloudEnabled;

    ensureWorkspaceTabConfig("project", {
      key: "project",
      mode: "project",
      enabled: true,
      configuredRequired: false,
      path: projectPath,
      title: "Projekt",
    });

    ensureWorkspaceTabConfig("editor3d", {
      key: "editor3d",
      mode: "3d",
      enabled,
      configuredRequired: true,
      path: editorPath,
      title: "3D",
    });

    ensureWorkspaceTabConfig("3d", {
      key: "editor3d",
      mode: "3d",
      enabled,
      configuredRequired: true,
      path: editorPath,
      title: "3D",
    });

    ensureWorkspaceTabConfig("map", {
      key: "map",
      mode: "map",
      enabled,
      configuredRequired: true,
      path: mapPath,
      title: "Map",
    });

    ensureWorkspaceTabConfig("cad2d", {
      key: "cad2d",
      mode: "2d",
      enabled,
      configuredRequired: true,
      path: cad2dPath,
      title: "2D",
    });

    ensureWorkspaceTabConfig("2d", {
      key: "cad2d",
      mode: "2d",
      enabled,
      configuredRequired: true,
      path: cad2dPath,
      title: "2D",
    });

    ensureWorkspaceTabConfig("lv", {
      key: "lv",
      mode: "lv",
      enabled,
      configuredRequired: true,
      path: lvPath,
      title: "LV",
    });

    ensureWorkspaceTabConfig("files", {
      key: "files",
      mode: "files",
      enabled: filesEnabled,
      configuredRequired: true,
      path: filesPath,
      title: "Dateien",
    });

    ensureWorkspaceTabConfig("structural_calculation", {
      key: "structural_calculation",
      mode: "structural_calculation",
      enabled: memberServiceEnabled,
      configuredRequired: true,
      path: structuralCalculationPath,
      title: "Tragwerksberechnung",
    });

    ensureWorkspaceTabConfig("energy_calculation", {
      key: "energy_calculation",
      mode: "energy_calculation",
      enabled: memberServiceEnabled,
      configuredRequired: true,
      path: energyCalculationPath,
      title: "Energieberechnung",
    });

    ensureWorkspaceTabConfig("sound_protection_calculation", {
      key: "sound_protection_calculation",
      mode: "sound_protection_calculation",
      enabled: memberServiceEnabled,
      configuredRequired: true,
      path: soundProtectionCalculationPath,
      title: "Schallschutzberechnung",
    });

    ensureWorkspaceTabConfig("versions", {
      key: "versions",
      mode: "versions",
      enabled: publicId !== "new",
      configuredRequired: false,
      path: versionsPagePath,
      apiPath: versionsPath,
      title: "Versionen",
    });

    ensureWorkspaceTabConfig("admin", {
      key: "admin",
      mode: "admin",
      enabled: adminEnabled,
      configuredRequired: false,
      path: adminPath,
      title: "Admin",
    });

    if (window.APP_CONFIG.projectSidebar && typeof window.APP_CONFIG.projectSidebar === "object") {
      window.APP_CONFIG.projectSidebar.currentProjectId = publicId;
      window.APP_CONFIG.projectSidebar.current_project_id = publicId;
      window.APP_CONFIG.projectSidebar.currentTitle = window.APP_CONFIG.projectName || "Aktuelles Projekt";
      window.APP_CONFIG.projectSidebar.currentSubtitle = configured ? "Projekt aktiv" : "Projekt definieren";
    }

    window.__VECTOPLAN_CURRENT_PROJECT__ = p;

    syncWorkspaceButtonPaths();
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

function projectPublicIdFromDetail(detail = {}) {
  try {
    const project = extractProjectFromDetail(detail) || {};
    const candidate = String(
      project.public_id ||
        project.publicId ||
        project.project_public_id ||
        project.projectPublicId ||
        detail.project_public_id ||
        detail.projectPublicId ||
        detail.public_id ||
        detail.publicId ||
        projectPublicId() ||
        ""
    ).trim();

    return PROJECT_PUBLIC_ID_RE.test(candidate) ? candidate : "";
  } catch (_) {
    return "";
  }
}

function normalizePlatformParentOrigin(value) {
  try {
    const parsed = new URL(String(value || ""), window.location.href);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    if (parsed.origin === window.location.origin) return "";

    return parsed.origin;
  } catch (_) {
    return "";
  }
}

function platformParentOrigin(candidateOrigin = "") {
  try {
    if (!window.parent || window.parent === window) return "";

    const candidate = normalizePlatformParentOrigin(candidateOrigin);
    const referrerOrigin = normalizePlatformParentOrigin(document.referrer);

    if (referrerOrigin) {
      try {
        window.sessionStorage.setItem(PLATFORM_PARENT_ORIGIN_SESSION_KEY, referrerOrigin);
      } catch (_) {}

      return !candidate || candidate === referrerOrigin ? referrerOrigin : "";
    }

    let storedOrigin = "";
    try {
      storedOrigin = normalizePlatformParentOrigin(
        window.sessionStorage.getItem(PLATFORM_PARENT_ORIGIN_SESSION_KEY)
      );
    } catch (_) {}

    return storedOrigin && (!candidate || candidate === storedOrigin) ? storedOrigin : "";
  } catch (_) {
    return "";
  }
}

function relayProjectNavigation(detail = {}, source = "shell") {
  try {
    const publicId = projectPublicIdFromDetail(detail);
    const targetOrigin = platformParentOrigin();
    const requestedWorkspace =
      detail.workspace ||
      detail.workspaceMode ||
      detail.workspace_mode ||
      detail.mode ||
      "";
    const normalizedWorkspace = requestedWorkspace ? normalizeMode(requestedWorkspace) : "";
    const suffixByWorkspace = {
      map: "map",
      "3d": "3d",
      "2d": "2d",
      lv: "lv",
      files: "files",
      structural_calculation: "structural-calculation",
      energy_calculation: "energy-calculation",
      sound_protection_calculation: "sound-protection-calculation",
    };
    let routePrefix = "";
    try {
      const currentPath = String(window.location.pathname || "");
      const markerIndex = currentPath.indexOf("/project=");
      if (markerIndex >= 0) routePrefix = currentPath.slice(0, markerIndex);
    } catch (_) {}
    const shellBasePath = `${routePrefix}/project=${encodeURIComponent(publicId)}`;
    const shellSuffix = suffixByWorkspace[normalizedWorkspace] || "";
    const workspacePath = String(
      detail.workspacePath
      || detail.workspace_path
      || (shellSuffix ? `${shellBasePath}/${shellSuffix}` : shellBasePath)
    ).trim();

    if (!publicId) return false;

    if (!targetOrigin) {
      try {
        if (workspacePath && window.parent === window) {
          window.history.pushState({ projectPublicId: publicId, workspace: normalizedWorkspace }, "", workspacePath);
          return true;
        }
      } catch (_) {}
      return false;
    }

    window.parent.postMessage(
      {
        type: "vectoplan:project:navigation",
        kind: "vectoplan:project:navigation",
        source: "vectoplan-app.shell",
        detail: {
          projectPublicId: publicId,
          workspace: normalizedWorkspace,
          workspacePath: workspacePath,
          source: String(source || "shell"),
        },
      },
      targetOrigin
    );
    return true;
  } catch (_) {
    return false;
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

    relayProjectNavigation(detail, eventType);
    showStatus("");
  } catch (error) {
    try {
      console.warn("[project] saved event handling failed", error);
    } catch (_) {}
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

function editorPreloadFrame() {
  try {
    return $("editor-preload-frame");
  } catch (_) {
    return document.getElementById("editor-preload-frame");
  }
}

function prepareParcelCatalogFrame() {
  try {
    const currentFrame = viewerFrame();
    const rawTarget = String(mapUrl() || "").trim();
    if (!currentFrame || !rawTarget || isUnsafeLegacyTarget(rawTarget)) return false;

    const currentTarget = currentFrame.dataset.target || currentFrame.getAttribute("src") || "";
    if (stableLocalUrl(currentTarget) === stableLocalUrl(rawTarget)) {
      currentFrame.dataset.parcelCatalogSource = "true";
      return true;
    }

    // 3D and the normal workspaces intentionally use separate frames. While
    // 3D is visible, keep the hidden workspace frame on Map so OpenLayers can
    // stream the nearby cadastral catalogue to the shared parcel bridge. If
    // the frame still contains CAD/LV/etc., the editor only knows already
    // selected parcels and can therefore remove but never add one.
    const fresh = document.createElement("iframe");
    fresh.id = currentFrame.id;
    fresh.className = currentFrame.className;
    fresh.classList.remove("is-workspace-active");
    fresh.setAttribute("title", "Karte · Flurstückkatalog");
    fresh.setAttribute("aria-hidden", "true");
    applyIframeCapabilities(fresh);
    setFrameDataset(fresh, {
      mode: "map",
      key: canonicalTabKey("map"),
      target: rawTarget,
      workspaceActive: "false",
      parcelCatalogSource: "true",
      swappedAt: Date.now(),
    });
    currentFrame.replaceWith(fresh);
    fresh.src = cacheBustLocalUrl(rawTarget);
    return true;
  } catch (_) {
    return false;
  }
}

function requestEditorRuntimeStatus(frame = editorPreloadFrame()) {
  try {
    if (!frame || !frame.contentWindow) return false;

    frame.contentWindow.postMessage(
      {
        type: "vectoplan-app:editor-status-request",
        kind: "vectoplan-app:editor-status-request",
        source: "vectoplan-app",
      },
      "*"
    );
    return true;
  } catch (_) {
    return false;
  }
}

function setWorkspaceFrameActive(frame, active) {
  try {
    if (!frame) return;
    frame.classList.toggle("is-workspace-active", Boolean(active));
    frame.setAttribute("aria-hidden", active ? "false" : "true");
    frame.dataset.workspaceActive = active ? "true" : "false";
  } catch (_) {}
}

function activateWorkspaceFrame(mode) {
  try {
    const editorActive = normalizeMode(mode) === "3d";
    if (editorActive) prepareParcelCatalogFrame();
    setWorkspaceFrameActive(viewerFrame(), !editorActive);
    setWorkspaceFrameActive(editorPreloadFrame(), editorActive);
  } catch (_) {}
}

function prepareEditorPreloadFrame(nextSrc) {
  try {
    const frame = editorPreloadFrame();
    const rawSrc = String(nextSrc || "").trim();
    if (!frame || !rawSrc || isUnsafeLegacyTarget(rawSrc)) return false;

    applyIframeCapabilities(frame);
    const currentTarget = frame.dataset.target || frame.getAttribute("src") || "";
    if (stableLocalUrl(currentTarget) === stableLocalUrl(rawSrc)) {
      requestEditorRuntimeStatus(frame);
      return true;
    }

    uiState.editorReady = false;
    frame.dataset.target = rawSrc;
    frame.dataset.preloaded = "true";
    frame.dataset.editorReady = "false";
    makeIframeLoadHandlers(frame, rawSrc, "3d");
    frame.src = rawSrc;
    return true;
  } catch (_) {
    return false;
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
    const normalized = normalizeMode(mode);
    const frame = normalized === "3d" ? editorPreloadFrame() : viewerFrame();
    if (!frame) return;

    frame.setAttribute("title", MODE_TITLE[normalized] || "Arbeitsbereich");
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

function iframeTimeoutForMode(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "3d") return 15000;
    if (normalized === "map") return 10000;

    return 8000;
  } catch (_) {
    return 8000;
  }
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

      if (normalizedMode === "3d") {
        requestEditorRuntimeStatus(frame);
      }

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
  }, iframeTimeoutForMode(normalizedMode));

  return { onLoad, onError, clear };
}

function safeFallbackForMode(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "project") return projectUrl();
    if (normalized === "map") return mapUrl();
    if (normalized === "2d") return twoDPageUrl();
    if (normalized === "lv") return lvUrl();
    if (normalized === "admin") return adminUrl();

    const editor = editorUrl();
    if (editor && !isUnsafeLegacyTarget(editor)) return editor;

    return projectUrl();
  } catch (_) {
    return projectUrl();
  }
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
      const fallback = safeFallbackForMode(normalizedMode);

      try {
        console.warn("[workspace] blocked unsafe iframe target", rawSrc, "fallback", fallback);
      } catch (_) {}

      if (!fallback || fallback === rawSrc || isUnsafeLegacyTarget(fallback)) {
        showWorkspaceFallback("Unsicheres iframe-Ziel wurde blockiert.");
        return false;
      }

      return hardSwapIframe(cacheBustLocalUrl(fallback), { ...options, mode: normalizedMode });
    }

    hideWorkspaceFallback();
    const currentTarget = oldFrame.dataset.target || oldFrame.getAttribute("src") || "";
    if (
      options.force !== true &&
      stableLocalUrl(currentTarget) === stableLocalUrl(rawSrc)
    ) {
      setFrameDataset(oldFrame, {
        mode: normalizedMode,
        key: canonicalTabKey(normalizedMode),
        target: rawSrc,
      });
      setRawOpenUrl(rawSrc);
      return true;
    }

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
      key: canonicalTabKey(normalizedMode),
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

    if (normalized === "admin") {
      return projectExists() && canManageProject() && tabEnabled("admin", true);
    }

    if (normalized === "versions") {
      return projectExists() && tabEnabled("versions", true);
    }

    if (MODES_REQUIRING_CONFIGURED_PROJECT.has(normalized)) {
      const tabFallback = projectToolsEnabled();
      return projectToolsEnabled() && tabEnabled(normalized, tabFallback);
    }

    return false;
  } catch (_) {
    return false;
  }
}

function workspaceModeDisabledMessage(mode) {
  try {
    const normalized = normalizeMode(mode);

    if (normalized === "admin" && !canManageProject()) {
      return "Admin ist nur für Projektverwalter verfügbar.";
    }

    if (normalized === "admin" && !projectExists()) {
      return "Projekt zuerst erstellen. Danach ist Admin verfügbar.";
    }

    if (normalized === "versions" && !projectExists()) {
      return "Projekt zuerst erstellen. Danach sind Versionen verfügbar.";
    }

    if (MODES_REQUIRING_CONFIGURED_PROJECT.has(normalized) && !projectToolsEnabled()) {
      return "Projekt zuerst speichern und konfigurieren. Danach sind die Projektbereiche verfügbar.";
    }

    return "Dieser Arbeitsbereich ist aktuell nicht verfügbar.";
  } catch (_) {
    return "Dieser Arbeitsbereich ist aktuell nicht verfügbar.";
  }
}

function syncWorkspaceButtonPaths() {
  try {
    const paths = {
      project: projectUrl(),
      map: mapUrl(),
      "3d": editorUrl(),
      "2d": twoDPageUrl(),
      lv: lvUrl(),
      files: filesUrl(),
      structural_calculation: structuralCalculationUrl(),
      energy_calculation: energyCalculationUrl(),
      sound_protection_calculation: soundProtectionCalculationUrl(),
    };

    for (const [mode, id] of Object.entries(MODE_TO_BUTTON_ID)) {
      const btn = $(id);
      if (!btn || !btn.dataset) continue;

      const path = paths[mode] || "";
      if (path) {
        btn.dataset.workspacePath = path;
      }

      btn.dataset.workspaceKey = canonicalTabKey(mode);
    }
  } catch (_) {}
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

    syncWorkspaceButtonPaths();

    for (const [mode, id] of Object.entries(MODE_TO_BUTTON_ID)) {
      const btn = $(id);
      if (!btn) continue;

      const allowed = mode === "versions"
        ? projectExists()
        : isWorkspaceModeAllowed(mode);

      btn.disabled = !allowed;
      btn.setAttribute("aria-disabled", allowed ? "false" : "true");
      btn.classList.toggle("is-disabled", !allowed);

      if (!allowed) {
        btn.title = workspaceModeDisabledMessage(mode);
      } else if (mode === "project") {
        btn.title = "Einstellungen";
      } else {
        btn.title = MODE_TITLE[mode] || "Arbeitsbereich";
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

    if (requested !== "3d") {
      uiState.pendingEditorWorkspace = null;
    }

    if (
      requested === "3d"
      && options.editorReadyActivation !== true
      && uiState.editorReady !== true
    ) {
      const pendingTarget = editorUrl();
      prepareEditorPreloadFrame(pendingTarget);
      uiState.pendingEditorWorkspace = {
        persist: shouldPersist,
        requestedAt: Date.now(),
        reason: options.reason || "editor-preload",
      };

      setUiMode(requested);
      // Show the editor immediately while its mandatory boot gate is active.
      // The editor owns the white loading screen and the live chunk/mesh
      // progress, so keeping this iframe hidden would leave an empty workspace
      // and conceal the actual loading state from the user.
      activateWorkspaceFrame(requested);
      setRawOpenUrl(pendingTarget);

      // Keep the outer Platform route in sync as soon as the user selects 3D.
      // Waiting for vectoplan-editor:ready made /2d remain in the address bar
      // throughout the (potentially long) world warmup.
      relayProjectNavigation(
        {
          projectPublicId: projectPublicId(),
          workspace: requested,
        },
        options.reason || "editor-preload"
      );

      showStatus("3D-Welt und Chunks werden vorbereitet …");

      try {
        versionsClose();
      } catch (_) {}

      return true;
    }

    const normalized = setUiMode(requested);
    let target = "";

    if (normalized === "project") {
      activateWorkspaceFrame(normalized);
      target = projectUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "project",
        title: "Projekt",
      });
    } else if (normalized === "3d") {
      target = editorUrl();
      prepareEditorPreloadFrame(target);
      activateWorkspaceFrame(normalized);
      setRawOpenUrl(target);
    } else if (normalized === "map") {
      activateWorkspaceFrame(normalized);
      target = mapUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "map",
        title: "Karte",
      });
    } else if (normalized === "2d") {
      activateWorkspaceFrame(normalized);
      target = await resolve2dUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "2d",
        title: "CAD",
      });
    } else if (normalized === "lv") {
      activateWorkspaceFrame(normalized);
      target = lvUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "lv",
        title: "Leistungsverzeichnis",
      });
    } else if (normalized === "files") {
      activateWorkspaceFrame(normalized);
      target = filesUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "files",
        title: "Dateien",
      });
    } else if (normalized === "structural_calculation") {
      activateWorkspaceFrame(normalized);
      target = structuralCalculationUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "structural_calculation",
        title: "Tragwerksberechnung",
      });
    } else if (normalized === "energy_calculation") {
      activateWorkspaceFrame(normalized);
      target = energyCalculationUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "energy_calculation",
        title: "Energieberechnung",
      });
    } else if (normalized === "sound_protection_calculation") {
      activateWorkspaceFrame(normalized);
      target = soundProtectionCalculationUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "sound_protection_calculation",
        title: "Schallschutzberechnung",
      });
    } else if (normalized === "versions") {
      versionsOpen();
      target = versionsPageUrl() || projectUrl();
      setRawOpenUrl(target);
    } else if (normalized === "admin") {
      activateWorkspaceFrame(normalized);
      target = adminUrl();
      hardSwapIframe(cacheBustLocalUrl(target), {
        mode: "admin",
        title: "Admin",
      });
    }

    if (shouldPersist) {
      await persistWorkspaceMode(normalized);
    }

    if (normalized !== "versions") {
      try {
        versionsClose();
      } catch (_) {}
    }
    relayProjectNavigation(
      {
        projectPublicId: projectPublicId(),
        workspace: normalized,
      },
      options.reason || "workspace_mode"
    );


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
    const editorFrame = editorPreloadFrame();
    if (editorFrame) {
      applyIframeCapabilities(editorFrame);
      const editorTarget = editorUrl();
      if (editorTarget) {
        editorFrame.dataset.target = editorTarget;
        makeIframeLoadHandlers(editorFrame, editorTarget, "3d");
      }
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
    ["modeFilesBtn", "files"],
    ["modeStructuralCalculationBtn", "structural_calculation"],
    ["modeEnergyCalculationBtn", "energy_calculation"],
    ["modeSoundProtectionCalculationBtn", "sound_protection_calculation"],
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
        const trustedParentOrigin = platformParentOrigin(event?.origin);
        if (
          type === "vectoplan:platform:request-project" &&
          window.parent &&
          window.parent !== window &&
          event?.source === window.parent &&
          !!trustedParentOrigin &&
          event?.origin === trustedParentOrigin
        ) {
          relayProjectNavigation(
            {
              projectPublicId: projectPublicId(),
              project: currentProject(),
              workspace: uiState.workspaceMode || dataValue("workspaceMode", "project"),
            },
            "platform_request"
          );
          return;
        }

        const frame = viewerFrame();
        if (
          !frame ||
          !frame.contentWindow ||
          event?.source !== frame.contentWindow ||
          event?.origin !== window.location.origin
        ) {
          return;
        }

        if (!type.startsWith("vectoplan:project:")) return;

        handleProjectSaved(data.detail || data, type);
      } catch (_) {}
    });

    safeOn(window, "vectoplan:workspace:navigate", (event) => {
      try {
        const detail = event?.detail || {};
        void setWorkspaceMode(detail.mode || detail.workspace || "project");
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
        const detail = event?.detail || {};
        uiState.lastProjectSidebarSelection = {
          item: detail.item || null,
          href: detail.href || "",
          selectedAt: Date.now(),
        };
        relayProjectNavigation(detail, "project_sidebar");
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
    const path = versionsApiUrl() || pathValue("versionsPath") || cfgValue("versionsPath");
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

      if (!projectExists()) {
        showStatus("Projekt zuerst erstellen. Danach sind Versionen verfügbar.");
        return;
      }

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


/* Project-scoped parcel selection bridge: Map <-> App state <-> 3D editor. */

function wireParcelSelectionBridge() {
  try {
    if (window.__VECTOPLAN_PARCEL_SELECTION_BRIDGE_WIRED__) return;
    window.__VECTOPLAN_PARCEL_SELECTION_BRIDGE_WIRED__ = true;

    const initialProjectCoordinate = () => {
      try {
        const project = currentProject();
        let longitude = Number(project.longitude ?? project.lng ?? project.lon);
        let latitude = Number(project.latitude ?? project.lat);
        if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
          const parsed = new URL(mapUrl(), window.location.href);
          longitude = Number(parsed.searchParams.get("lon"));
          latitude = Number(parsed.searchParams.get("lat"));
        }
        return Number.isFinite(longitude) && Number.isFinite(latitude)
          ? { longitude, latitude }
          : null;
      } catch (_) {
        return null;
      }
    };

    const emptySelection = () => ({
      projectPublicId: projectPublicId(),
      coordinateSpace: "wgs84",
      coveragePolicy: "cell-center",
      revision: 0,
      projectCoordinate: initialProjectCoordinate(),
      projectCoordinateManualOverride: false,
      gridRotationDegrees: 0,
      parcels: [],
      adjacentParcels: [],
      parcelGridState: null,
    });

    let currentSelection = emptySelection();
    let hydrated = false;
    let availableParcels = [];
    let localMutationSerial = 0;
    let selectionPersistQueue = Promise.resolve();
    let coordinatePersistQueue = Promise.resolve(false);

    const normalizeGridRotation = (value) => {
      let result = Number.isFinite(Number(value)) ? Number(value) : 0;
      while (result >= 90) result -= 180;
      while (result < -90) result += 180;
      return Math.abs(result) < 1e-8 ? 0 : result;
    };

    const normalizeParcelList = (value, maximum = 64) => (Array.isArray(value) ? value : [])
      .slice(0, Math.max(1, Math.min(512, Number(maximum) || 64)))
      .map((entry) => {
        const item = entry && typeof entry === "object" ? entry : {};
        const parcelId = String(item.parcelId || item.parcel_id || item.id || "").trim();
        const geometry = item.geometry && typeof item.geometry === "object" ? item.geometry : {};
        const geometryType = String(geometry.type || "");
        if (!parcelId || !["Polygon", "MultiPolygon"].includes(geometryType)) return null;
        return {
          parcelId,
          datasetId: String(item.datasetId || item.dataset_id || "").trim(),
          geometry,
          properties: item.properties && typeof item.properties === "object" ? item.properties : {},
        };
      })
      .filter(Boolean);

    const normalizeParcelGridState = (value) => {
      const state = value && typeof value === "object" ? value : null;
      if (!state || String(state.schemaVersion || state.schema_version || "") !== "vectoplan-parcel-grid-state.v1") {
        return null;
      }
      const mode = String(state.mode || "boundary") === "setback" ? "setback" : "boundary";
      const guides = (Array.isArray(state.guides) ? state.guides : []).slice(0, 64).map((value) => {
        const guide = value && typeof value === "object" ? value : {};
        const start = guide.startLonLat || guide.start_lon_lat;
        const end = guide.endLonLat || guide.end_lon_lat;
        const parcelId = String(guide.parcelId || guide.parcel_id || "").trim();
        if (!parcelId || !Array.isArray(start) || !Array.isArray(end)) return null;
        const coordinates = [Number(start[0]), Number(start[1]), Number(end[0]), Number(end[1])];
        if (!coordinates.every(Number.isFinite)) return null;
        return {
          parcelId,
          startLonLat: coordinates.slice(0, 2),
          endLonLat: coordinates.slice(2, 4),
          depthMeters: Math.max(1, Math.min(6, Math.round(Number(guide.depthMeters || guide.depth_meters) || 3))),
        };
      }).filter(Boolean);
      return {
        schemaVersion: "vectoplan-parcel-grid-state.v1",
        mode,
        setbackMeters: Math.max(0, Math.min(20, Number(state.setbackMeters ?? state.setback_meters) || 0)),
        influenceMeters: Math.max(1, Math.min(6, Math.round(Number(state.influenceMeters ?? state.influence_meters) || 3))),
        activeParcelId: String(state.activeParcelId || state.active_parcel_id || "").trim() || null,
        guides,
      };
    };

    const polygonCoordinates = (parcel) => {
      const geometry = parcel?.geometry || {};
      if (geometry.type === "Polygon") return [geometry.coordinates || []];
      return geometry.type === "MultiPolygon" && Array.isArray(geometry.coordinates)
        ? geometry.coordinates
        : [];
    };

    const dominantGridRotation = (parcels, latitude = 0) => {
      const metresPerDegreeLat = 111320;
      const metresPerDegreeLon = Math.max(1, metresPerDegreeLat * Math.cos(Number(latitude || 0) * Math.PI / 180));
      let longestSquared = 0;
      let angle = 0;
      for (const parcel of parcels || []) {
        for (const polygon of polygonCoordinates(parcel)) {
          const ring = Array.isArray(polygon?.[0]) ? polygon[0] : [];
          for (let index = 1; index < ring.length; index += 1) {
            const first = ring[index - 1] || [];
            const second = ring[index] || [];
            const east = (Number(second[0]) - Number(first[0])) * metresPerDegreeLon;
            const north = (Number(second[1]) - Number(first[1])) * metresPerDegreeLat;
            const squared = east * east + north * north;
            if (!Number.isFinite(squared) || squared <= longestSquared) continue;
            longestSquared = squared;
            angle = Math.atan2(north, east) * 180 / Math.PI;
          }
        }
      }
      return normalizeGridRotation(angle);
    };

    const parcelBounds = (parcel) => {
      const coordinates = polygonCoordinates(parcel).flat(2);
      const points = coordinates.filter((point) => Array.isArray(point)
        && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])));
      if (!points.length) return null;
      const longitudes = points.map((point) => Number(point[0]));
      const latitudes = points.map((point) => Number(point[1]));
      return {
        minLon: Math.min(...longitudes), maxLon: Math.max(...longitudes),
        minLat: Math.min(...latitudes), maxLat: Math.max(...latitudes),
      };
    };

    const ringCentroid = (ring) => {
      const points = (Array.isArray(ring) ? ring : [])
        .map((point) => [Number(point?.[0]), Number(point?.[1])])
        .filter((point) => point.every(Number.isFinite));
      if (points.length < 3) return null;
      const origin = points[0];
      let crossSum = 0;
      let longitudeSum = 0;
      let latitudeSum = 0;
      for (let index = 0; index < points.length; index += 1) {
        const current = points[index];
        const next = points[(index + 1) % points.length];
        const currentLongitude = current[0] - origin[0];
        const currentLatitude = current[1] - origin[1];
        const nextLongitude = next[0] - origin[0];
        const nextLatitude = next[1] - origin[1];
        const cross = currentLongitude * nextLatitude - nextLongitude * currentLatitude;
        crossSum += cross;
        longitudeSum += (currentLongitude + nextLongitude) * cross;
        latitudeSum += (currentLatitude + nextLatitude) * cross;
      }
      if (!Number.isFinite(crossSum) || Math.abs(crossSum) < 1e-18) return null;
      const longitude = origin[0] + longitudeSum / (3 * crossSum);
      const latitude = origin[1] + latitudeSum / (3 * crossSum);
      if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null;
      return { coordinate: { longitude, latitude }, area: Math.abs(crossSum) / 2 };
    };

    const parcelCenterCoordinate = (parcel) => {
      let largest = null;
      for (const polygon of polygonCoordinates(parcel)) {
        const centroid = ringCentroid(Array.isArray(polygon?.[0]) ? polygon[0] : []);
        if (centroid && (!largest || centroid.area > largest.area)) largest = centroid;
      }
      if (largest) return largest.coordinate;
      const bounds = parcelBounds(parcel);
      if (!bounds) return null;
      return {
        longitude: (bounds.minLon + bounds.maxLon) / 2,
        latitude: (bounds.minLat + bounds.maxLat) / 2,
      };
    };

    const automaticCoordinateForSelection = (previous, next) => {
      if (previous?.projectCoordinateManualOverride || next?.projectCoordinateManualOverride) return null;
      const previousIds = new Set((previous?.parcels || []).map((parcel) => parcel.parcelId));
      const added = (next?.parcels || []).filter((parcel) => !previousIds.has(parcel.parcelId));
      return added.length ? parcelCenterCoordinate(added[added.length - 1]) : null;
    };

    const adjacentParcelsFor = (selected, available) => {
      if (!selected.length || !available.length) return [];
      const selectedIds = new Set(selected.map((parcel) => parcel.parcelId));
      const selectedBounds = selected.map(parcelBounds).filter(Boolean);
      const latitude = currentSelection?.projectCoordinate?.latitude
        ?? selectedBounds[0]?.minLat
        ?? 0;
      const metresPerDegreeLat = 111320;
      const metresPerDegreeLon = Math.max(1, metresPerDegreeLat * Math.cos(Number(latitude) * Math.PI / 180));
      return available.filter((parcel) => {
        if (selectedIds.has(parcel.parcelId)) return false;
        const bounds = parcelBounds(parcel);
        if (!bounds) return false;
        return selectedBounds.some((reference) => {
          const lonGap = Math.max(0, reference.minLon - bounds.maxLon, bounds.minLon - reference.maxLon);
          const latGap = Math.max(0, reference.minLat - bounds.maxLat, bounds.minLat - reference.maxLat);
          return Math.hypot(lonGap * metresPerDegreeLon, latGap * metresPerDegreeLat) <= 2.0;
        });
      }).slice(0, 128);
    };

    const normalizedSelection = (value, maxParcels = 64) => {
      try {
        const root = value && typeof value === "object" ? value : {};
        const source = root.detail || root.selection || root.last_map_selection || root;
        const activeProjectId = projectPublicId();
        const incomingProjectId = String(
          source.projectPublicId || source.project_public_id || activeProjectId || ""
        ).trim();
        if (activeProjectId && activeProjectId !== "new" && incomingProjectId && incomingProjectId !== activeProjectId) {
          return emptySelection();
        }
        const parcels = normalizeParcelList(source.parcels || source.features, maxParcels);
        const adjacentParcels = normalizeParcelList(
          source.adjacentParcels || source.adjacent_parcels,
          128,
        );
        const coordinateSource = source.projectCoordinate || source.project_coordinate || {};
        const longitude = Number(coordinateSource.longitude ?? coordinateSource.lon ?? coordinateSource.lng);
        const latitude = Number(coordinateSource.latitude ?? coordinateSource.lat);
        const manualOverrideValue = source.projectCoordinateManualOverride
          ?? source.project_coordinate_manual_override;
        const projectCoordinateManualOverride = currentSelection?.projectCoordinateManualOverride === true
          || (manualOverrideValue != null && boolFromValue(manualOverrideValue, false));
        const parcelGridState = normalizeParcelGridState(
          source.parcelGridState
          || source.parcel_grid_state
          || currentSelection?.parcelGridState
        );
        return {
          projectPublicId: activeProjectId && activeProjectId !== "new" ? activeProjectId : incomingProjectId,
          coordinateSpace: "wgs84",
          coveragePolicy: "cell-center",
          revision: Number.isFinite(Number(source.revision)) ? Number(source.revision) : 0,
          projectCoordinate: Number.isFinite(longitude) && Number.isFinite(latitude)
            ? { longitude, latitude }
            : (currentSelection?.projectCoordinate || initialProjectCoordinate()),
          projectCoordinateManualOverride,
          gridRotationDegrees: dominantGridRotation(
            parcels,
            Number.isFinite(latitude) ? latitude : (currentSelection?.projectCoordinate?.latitude || 0),
          ),
          parcels,
          adjacentParcels,
          parcelGridState,
        };
      } catch (_) {
        return emptySelection();
      }
    };

    const broadcastSelection = () => {
      // Persist every known selected geometry in the in-memory catalogue as
      // well. This keeps a parcel immediately re-selectable even before the
      // hidden Map frame has finished loading its viewport catalogue.
      availableParcels = normalizeParcelList([
        ...currentSelection.parcels,
        ...availableParcels,
      ], 512);
      const root = appRoot();
      if (root) {
        root.dataset.parcelCatalogCount = String(availableParcels.length);
        root.dataset.parcelSelectionCount = String(currentSelection.parcels.length);
      }
      const message = {
        type: "vectoplan-app:parcel-selection-sync",
        kind: "vectoplan-app:parcel-selection-sync",
        source: "vectoplan-app",
        detail: { ...currentSelection, availableParcels },
      };
      for (const frame of [viewerFrame(), editorPreloadFrame()]) {
        try { frame?.contentWindow?.postMessage(message, "*"); } catch (_) {}
      }
      uiState.lastParcelSelection = currentSelection;
      uiState.lastParcelSelectionTs = Date.now();
    };

    const persistSelection = () => {
      try {
        const statePath = pathValue("statePutPath") || cfgValue("statePutPath");
        if (!statePath || statePath === "__DISABLED__") return Promise.resolve();
        const selectionSnapshot = JSON.parse(JSON.stringify(currentSelection));
        const selectionTimestamp = Date.now();
        selectionPersistQueue = selectionPersistQueue.catch(() => undefined).then(async () => {
          try {
            await fetch(statePath, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              cache: "no-store",
              body: JSON.stringify({
                last_map_selection: selectionSnapshot,
                last_map_selection_ts: selectionTimestamp,
                legacy_3d_backend: false,
              }),
            });
          } catch (_) {}
        });
        return selectionPersistQueue;
      } catch (_) {
        return Promise.resolve();
      }
    };

    const persistProjectCoordinate = (coordinate) => {
      try {
        const endpoint = pathValue("projectApiPath") || cfgValue("projectApiPath");
        if (!endpoint || endpoint === "__DISABLED__") return Promise.resolve(false);
        const coordinateSnapshot = {
          longitude: Number(coordinate.longitude),
          latitude: Number(coordinate.latitude),
        };
        coordinatePersistQueue = coordinatePersistQueue.catch(() => false).then(async () => {
          try {
            const response = await fetch(endpoint, {
              method: "PATCH",
              headers: { "Content-Type": "application/json", "Accept": "application/json" },
              credentials: "same-origin",
              cache: "no-store",
              body: JSON.stringify(coordinateSnapshot),
            });
            if (!response.ok) return false;
            const project = currentProject();
            project.longitude = coordinateSnapshot.longitude;
            project.latitude = coordinateSnapshot.latitude;
            project.lng = coordinateSnapshot.longitude;
            project.lat = coordinateSnapshot.latitude;
            return true;
          } catch (_) {
            return false;
          }
        });
        return coordinatePersistQueue;
      } catch (_) {
        return Promise.resolve(false);
      }
    };

    safeOn(window, "message", (event) => {
      try {
        const data = event?.data;
        if (!data || typeof data !== "object") return;
        const type = String(data.type || data.kind || "");
        const mapFrame = viewerFrame();
        const editorFrame = editorPreloadFrame();
        const fromMap = !!mapFrame?.contentWindow && event.source === mapFrame.contentWindow;
        const fromEditor = !!editorFrame?.contentWindow && event.source === editorFrame.contentWindow;
        if (!fromMap && !fromEditor) return;

        if (
          (type === "vectoplan-map:parcel-selection-changed" && fromMap)
          || (type === "vectoplan-editor:parcel-selection-changed" && fromEditor)
        ) {
          localMutationSerial += 1;
          const previousSelection = currentSelection;
          const normalized = normalizedSelection(data.detail || data);
          const automaticCoordinate = automaticCoordinateForSelection(previousSelection, normalized);
          const nextSelection = {
            ...normalized,
            revision: Math.max(
              Number(previousSelection.revision || 0) + 1,
              Number(normalized.revision || 0),
            ),
            projectCoordinate: automaticCoordinate
              || (previousSelection.projectCoordinateManualOverride === true
                ? previousSelection.projectCoordinate
                : normalized.projectCoordinate),
          };
          currentSelection = {
            ...nextSelection,
            adjacentParcels: adjacentParcelsFor(nextSelection.parcels, availableParcels),
          };
          broadcastSelection();
          void persistSelection();
          if (automaticCoordinate) void persistProjectCoordinate(automaticCoordinate);
          return;
        }

        if (type === "vectoplan-map:project-coordinate-changed" && fromMap) {
          const detail = data.detail && typeof data.detail === "object" ? data.detail : data;
          const longitude = Number(detail.longitude ?? detail.lon ?? detail.lng);
          const latitude = Number(detail.latitude ?? detail.lat);
          const incomingProjectId = String(detail.projectPublicId || detail.project_public_id || "").trim();
          if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return;
          if (incomingProjectId && incomingProjectId !== projectPublicId()) return;
          localMutationSerial += 1;
          currentSelection = {
            ...currentSelection,
            projectCoordinate: { longitude, latitude },
            projectCoordinateManualOverride: detail.projectCoordinateManualOverride == null
              ? true
              : boolFromValue(detail.projectCoordinateManualOverride, true),
            revision: Number(currentSelection.revision || 0) + 1,
          };
          broadcastSelection();
          void persistSelection();
          void persistProjectCoordinate(currentSelection.projectCoordinate);
          return;
        }

        if (type === "vectoplan-map:parcel-catalog-changed" && fromMap) {
          const detail = data.detail && typeof data.detail === "object" ? data.detail : data;
          const incomingProjectId = String(detail.projectPublicId || detail.project_public_id || "").trim();
          if (incomingProjectId && incomingProjectId !== projectPublicId()) return;
          localMutationSerial += 1;
          availableParcels = normalizedSelection({
            ...currentSelection,
            parcels: detail.availableParcels || detail.available_parcels || [],
          }, 512).parcels;
          const coordinate = detail.projectCoordinate || detail.project_coordinate;
          if (
            currentSelection.projectCoordinateManualOverride !== true
            && coordinate
            && typeof coordinate === "object"
          ) {
            const longitude = Number(coordinate.longitude ?? coordinate.lon ?? coordinate.lng);
            const latitude = Number(coordinate.latitude ?? coordinate.lat);
            if (Number.isFinite(longitude) && Number.isFinite(latitude)) {
              currentSelection = { ...currentSelection, projectCoordinate: { longitude, latitude } };
            }
          }
          currentSelection = {
            ...currentSelection,
            adjacentParcels: adjacentParcelsFor(currentSelection.parcels, availableParcels),
            gridRotationDegrees: dominantGridRotation(
              currentSelection.parcels,
              currentSelection.projectCoordinate?.latitude || 0,
            ),
          };
          broadcastSelection();
          void persistSelection();
          return;
        }

        if (
          type === "vectoplan-map:parcel-selection-request"
          || type === "vectoplan-editor:parcel-selection-request"
          || type === "vectoplan-cad:parcel-selection-request"
        ) {
          if (hydrated) broadcastSelection();
        }
      } catch (_) {}
    });

    void (async () => {
      const hydrationMutationSerial = localMutationSerial;
      try {
        const statePath = pathValue("stateGetPath") || cfgValue("stateGetPath");
        if (!statePath || statePath === "__DISABLED__") {
          hydrated = true;
          broadcastSelection();
          return;
        }
        const response = await fetch(statePath, {
          method: "GET",
          headers: { "Accept": "application/json" },
          credentials: "same-origin",
          cache: "no-store",
        });
        if (response.ok) {
          const payload = await response.json();
          const selectionState = payload?.selection || payload?.viewer_selection || payload || {};
          if (localMutationSerial === hydrationMutationSerial) {
            currentSelection = normalizedSelection(selectionState.last_map_selection || selectionState);
          }
        }
      } catch (_) {}
      hydrated = true;
      broadcastSelection();
    })();
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

        const frame = editorPreloadFrame();
        if (!frame || !frame.contentWindow || event.source !== frame.contentWindow) return;

        const data = event.data;
        const type = String(data.type || data.kind || "").toLowerCase();

        if (
          String(data.source || "").toLowerCase() !== "vectoplan-editor"
          || (!type.includes("editor") && !type.includes("vectoplan"))
        ) {
          return;
        }

        uiState.lastEditorMessage = data;
        uiState.lastEditorMessageTs = Date.now();

        if (type === "vectoplan-editor:ready") {
          uiState.editorReady = true;
          frame.dataset.editorReady = "true";
          hideWorkspaceFallback();

          const pending = uiState.pendingEditorWorkspace;
          uiState.pendingEditorWorkspace = null;

          if (pending && normalizeMode(uiState.workspaceMode) === "3d") {
            void setWorkspaceMode("3d", {
              persist: pending.persist !== false,
              reason: "editor-preload-ready",
              editorReadyActivation: true,
            });
          }
        }

        if (type === "vectoplan-editor:exit-requested") {
          void setWorkspaceMode("project", {
            persist: true,
            reason: "editor-exit-requested",
          });
          return;
        }

        if (type.includes("destroyed") || type.includes("failed")) {
          uiState.editorReady = false;
          frame.dataset.editorReady = "false";
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

    requestEditorRuntimeStatus();
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
        canManage: canManageProject,
        url: projectUrl,
        syncGating: syncWorkspaceGating,
        updateConfig: updateAppConfigProject,
      },
      routes: {
        forProject: routeForProject,
        project: projectUrl,
        editor: editorUrl,
        map: mapUrl,
        cad2d: twoDPageUrl,
        lv: lvUrl,
        versionsPage: versionsPageUrl,
        versionsApi: versionsApiUrl,
        admin: adminUrl,
      },
      modes: {
        normalize: normalizeMode,
        canonicalTabKey,
        allowed: isWorkspaceModeAllowed,
        set: setWorkspaceMode,
      },
      iframe: {
        frame: viewerFrame,
        swap: hardSwapIframe,
        unsafe: isUnsafeLegacyTarget,
        stableLocalUrl,
        cacheBustLocalUrl,
      },
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


  safeCall("wireEditorEventBridge", wireEditorEventBridge);
  safeCall("wireParcelSelectionBridge", wireParcelSelectionBridge);
  safeCall("prepareEditorPreloadFrame", () => {
    if (!projectToolsEnabled()) return false;
    return prepareEditorPreloadFrame(editorUrl());
  });
  safeCall("initProjectSidebar", initProjectSidebar);

  safeCall("wireGlobalStatus", wireGlobalStatus);
  safeCall("wireHotkeys", wireHotkeys);

  safeCall("wireProjectEventBridge", wireProjectEventBridge);
  safeCall("announceCurrentProject", () => {
    relayProjectNavigation(
      {
        projectPublicId: projectPublicId(),
        project: currentProject(),
      },
      "shell_ready"
    );
  });
  safeCall("wireWorkspaceToolbar", wireWorkspaceToolbar);
  safeCall("wireVersionsDropdown", wireVersionsDropdown);

  safeCall("wire2dEventBridge", wire2dEventBridge);
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
