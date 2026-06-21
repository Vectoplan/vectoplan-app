// services/vectoplan-app/static/js/chat/main.js
// Orchestrator for the VECTOPLAN app shell.
// Responsibilities:
// - initialize layout, transcript, composer and templates
// - switch workspace iframe between Editor, Map, 2D, LV and Admin
// - keep version dropdown usable as a neutral list
// - avoid any legacy 3D backend calls
// - never use Docker-internal URLs as browser iframe targets
// - isolate each boot step with try/catch so one failing module does not stop the shell

import { initChatLayout } from "./layout.js";

import { $, uiState } from "./core.js";
import { getConfig } from "./api.js";
import { loadVersions } from "./versions.js";
import {
  loadTranscript,
  refreshTranscriptIncremental,
  wireTranscriptRefresh,
} from "./transcript.js";
import { wireComposer } from "./composer.js";
import { loadTemplatesIndex } from "./cards.js";


/* ───────────────────────── Constants ───────────────────────── */

const WORKSPACE_IFRAME_ALLOW = "fullscreen; pointer-lock; clipboard-read; clipboard-write";
const WORKSPACE_IFRAME_REFERRER_POLICY = "no-referrer";

const DEFAULT_EDITOR_ROUTE_TEMPLATE = "/ui/chat/{{chat_id}}/editor";
const DEFAULT_MAP_ROUTE_TEMPLATE = "/ui/chat/{{chat_id}}/map";
const DEFAULT_2D_ROUTE_TEMPLATE = "/ui/chat/{{chat_id}}/cad2d";
const DEFAULT_ADMIN_ROUTE_TEMPLATE = "/ui/chat/{{chat_id}}/admin";
const DEFAULT_LV_ROUTE_TEMPLATE = "/ui/chat/{{chat_id}}/lv";

const LEGACY_OR_INTERNAL_BROWSER_TARGETS = [
  "http://localhost:8090",
  "http://127.0.0.1:8090",
  "http://openlayer:8090",
  "http://vectoplan-openlayer:8090",
  "http://localhost:5100/",
  "http://127.0.0.1:5100/",
];

const MODE_TO_BUTTON_ID = {
  map: "modeMapBtn",
  "3d": "mode3dBtn",
  "2d": "mode2dBtn",
  lv: "modeLvBtn",
  admin: "modeAdminBtn",
};

const MODE_TITLE = {
  "3d": "VECTOPLAN Editor",
  editor: "VECTOPLAN Editor",
  map: "Karte",
  "2d": "2D Ansicht",
  cad2d: "2D Ansicht",
  lv: "Leistungsverzeichnis",
  admin: "Admin",
};


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
    return value && typeof value === "object" ? value : fallback;
  } catch (_) {
    return fallback;
  }
}

function chatId() {
  return cfgValue("chatId", "");
}

function encodePathPart(value) {
  try {
    return encodeURIComponent(String(value || ""));
  } catch (_) {
    return "";
  }
}

function withChatPath(templatePath) {
  try {
    const id = chatId();
    const p = String(templatePath || "");
    return p.replaceAll("{{chat_id}}", encodePathPart(id));
  } catch (_) {
    return templatePath;
  }
}

function pathFromConfig(keys, fallbackTemplate) {
  try {
    for (const key of keys) {
      const value = cfgValue(key, "");
      if (value) return withChatPath(value);
    }

    return withChatPath(fallbackTemplate);
  } catch (_) {
    return withChatPath(fallbackTemplate);
  }
}

function appPaths() {
  try {
    const paths = cfgObject("paths", {});
    return paths && typeof paths === "object" ? paths : {};
  } catch (_) {
    return {};
  }
}

function pathValue(key, fallback = "") {
  try {
    const paths = appPaths();
    const value = paths && paths[key] != null ? paths[key] : cfgValue(key, fallback);
    return String(value == null ? "" : value).trim();
  } catch (_) {
    return cfgValue(key, fallback);
  }
}

function isAbsoluteUrl(input) {
  try {
    const raw = String(input || "").trim();
    return /^https?:\/\//i.test(raw);
  } catch (_) {
    return false;
  }
}

function isLocalAppRoute(input) {
  try {
    const raw = String(input || "").trim();
    return raw.startsWith("/");
  } catch (_) {
    return false;
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

    // Only cache-bust relative same-origin URLs.
    // This keeps external Editor/OpenLayer public URLs stable if they ever appear.
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

    const normalized = raw.endsWith("/") && !raw.includes("?", raw.length - 1)
      ? raw
      : raw.replace(/[?#].*$/, "");

    for (const bad of LEGACY_OR_INTERNAL_BROWSER_TARGETS) {
      if (normalized === bad || raw.startsWith(bad + "?") || raw.startsWith(bad + "#")) {
        return true;
      }
    }

    // Explicitly block Docker-internal host names in browser iframe targets.
    try {
      const url = new URL(raw, location.origin);
      const host = String(url.hostname || "").toLowerCase();
      if (["openlayer", "vectoplan-openlayer", "server-openlayer", "vectoplan-editor", "editor"].includes(host)) {
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


/* ───────────────────────── Workspace URL resolution ───────────────────────── */

function editorUrl() {
  try {
    const fallback = `/ui/chat/${encodePathPart(chatId())}/editor`;

    // Editor must always be reached through the local app route.
    // Do not use viewer_url/raw external URL as primary 3D source.
    const candidate =
      pathValue("editorPagePath") ||
      pathValue("initialEditorUrl") ||
      cfgValue("editorPagePath") ||
      cfgValue("initialEditorUrl") ||
      withChatPath(DEFAULT_EDITOR_ROUTE_TEMPLATE);

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return `/ui/chat/${encodePathPart(chatId())}/editor`;
  }
}

function mapUrl() {
  try {
    const fallback = `/ui/chat/${encodePathPart(chatId())}/map`;

    // Map must always be reached through the local app route.
    // The app route redirects to the browser-facing OpenLayer public URL.
    const candidate =
      pathValue("mapPagePath") ||
      cfgValue("mapPagePath") ||
      withChatPath(DEFAULT_MAP_ROUTE_TEMPLATE);

    return sanitizeWorkspaceUrl(candidate, fallback);
  } catch (_) {
    return `/ui/chat/${encodePathPart(chatId())}/map`;
  }
}

function twoDPageUrl() {
  try {
    return pathFromConfig(["cad2dPagePath"], DEFAULT_2D_ROUTE_TEMPLATE);
  } catch (_) {
    return `/ui/chat/${encodePathPart(chatId())}/cad2d`;
  }
}

function adminUrl() {
  try {
    return pathFromConfig(["adminPagePath"], DEFAULT_ADMIN_ROUTE_TEMPLATE);
  } catch (_) {
    return `/ui/chat/${encodePathPart(chatId())}/admin`;
  }
}

function lvUrl() {
  try {
    return pathFromConfig(["lvPagePath"], DEFAULT_LV_ROUTE_TEMPLATE);
  } catch (_) {
    return `/ui/chat/${encodePathPart(chatId())}/lv`;
  }
}

async function resolve2dUrl() {
  try {
    const jsonPath = pathValue("cadEmbedJsonPath") || cfgValue("cadEmbedJsonPath");
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

    // Do not set sandbox here. A sandbox without all needed flags can break
    // Editor pointer lock, module loading, storage and OpenLayer behavior.
    try {
      frame.removeAttribute("sandbox");
    } catch (_) {}
  } catch (_) {}
}

function makeIframeLoadHandlers(frame, rawSrc, mode) {
  let loaded = false;
  let timeoutId = null;

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
          mode,
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
      showWorkspaceFallback("Arbeitsbereich konnte nicht geladen werden. Prüfe, ob der Zielservice läuft und iframe-Header erlaubt sind.");

      try {
        uiState.lastWorkspaceError = {
          mode,
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
  }, mode === "map" ? 6000 : 8000);

  return { onLoad, onError, clear };
}

function hardSwapIframe(nextSrc, options = {}) {
  try {
    const oldFrame = viewerFrame();
    const rawSrc = String(nextSrc || "").trim();

    if (!oldFrame || !rawSrc) return false;

    const mode = String(options.mode || "").trim();
    const normalizedMode = normalizeMode(mode || "3d");
    const title = String(options.title || MODE_TITLE[normalizedMode] || oldFrame.getAttribute("title") || "Arbeitsbereich");

    if (isUnsafeLegacyTarget(rawSrc)) {
      const fallback = normalizedMode === "map" ? mapUrl() : editorUrl();
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

    // Best-effort retry for browsers/extensions that occasionally leave iframes blank.
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
    }
  } catch (_) {}
}

function normalizeMode(mode) {
  try {
    const value = String(mode || "").trim().toLowerCase();

    if (value === "editor") return "3d";
    if (value === "cad") return "2d";
    if (value === "cad2d") return "2d";
    if (value === "viewer") return "3d";
    if (value === "viewer3d") return "3d";
    if (value === "model") return "3d";
    if (value === "version") return "3d";

    if (["3d", "2d", "map", "lv", "admin"].includes(value)) return value;

    return "3d";
  } catch (_) {
    return "3d";
  }
}

function setUiMode(mode) {
  try {
    const normalized = normalizeMode(mode);

    uiState.viewerMode = normalized;
    uiState.workspaceMode = normalized;

    const root = document.querySelector(".app-wrap");
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

async function fetchWorkspaceState() {
  try {
    const statePath = pathValue("stateGetPath") || cfgValue("stateGetPath");
    if (!statePath || statePath === "__DISABLED__") return null;

    return await fetch(statePath, {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then((response) => response.json())
      .catch(() => null);
  } catch (_) {
    return null;
  }
}

function extractModeFromState(state) {
  try {
    if (!state || typeof state !== "object") return "";

    const candidates = [
      state.workspace_mode,
      state.viewer_mode,
      state.mode,
      state?.viewer_selection?.workspace_mode,
      state?.viewer_selection?.mode,
      state?.selection?.workspace_mode,
      state?.selection?.mode,
      state?.payload?.workspace_mode,
      state?.payload?.mode,
    ];

    for (const candidate of candidates) {
      const value = String(candidate || "").trim();
      if (value) return normalizeMode(value);
    }

    return "";
  } catch (_) {
    return "";
  }
}

async function setWorkspaceMode(mode, options = {}) {
  const normalized = setUiMode(mode);
  const shouldPersist = options.persist !== false;

  try {
    let target = "";

    if (normalized === "3d") {
      target = editorUrl();
      hardSwapIframe(target, {
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

    return true;
  } catch (error) {
    try {
      console.warn("[workspace] setWorkspaceMode failed", normalized, error);
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
      makeIframeLoadHandlers(currentFrame, currentFrame.getAttribute("src") || currentFrame.src || "", "3d");
    }
  } catch (_) {}

  try {
    const queryMode = new URLSearchParams(location.search).get("mode");
    if (queryMode) {
      await setWorkspaceMode(queryMode, { persist: false });
      return;
    }

    const saved = await fetchWorkspaceState();
    const savedMode = extractModeFromState(saved);

    if (savedMode) {
      await setWorkspaceMode(savedMode, { persist: false });
      return;
    }

    const configured = cfgValue("defaultMode", "3d");
    await setWorkspaceMode(configured || "3d", { persist: false });
  } catch (_) {
    await setWorkspaceMode("3d", { persist: false });
  }
}

function wireWorkspaceToolbar() {
  const bindings = [
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

      void setWorkspaceMode(mode);
    });
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

    if (!toggle || !box) return false;

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
    if (!path) return [];

    const data = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then((response) => response.json())
      .catch(() => null);

    if (Array.isArray(data?.items)) return data.items;

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
    if (!box) return;

    const text = String(message || "");
    box.textContent = text;
    box.classList.toggle("hidden", !text);
  } catch (_) {}
}

function wireGlobalStatus() {
  try {
    const alertBox = $("systemAlert");
    if (!alertBox) return;

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
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          const send = $("sendBtn");
          if (send) {
            event.preventDefault();
            send.click();
          }
        }

        if (event.altKey && event.key === "1") {
          event.preventDefault();
          void setWorkspaceMode("3d");
        }

        if (event.altKey && event.key === "2") {
          event.preventDefault();
          void setWorkspaceMode("2d");
        }

        if (event.altKey && event.key === "3") {
          event.preventDefault();
          void setWorkspaceMode("map");
        }

        if (event.key === "Escape") {
          versionsClose();
        }
      } catch (_) {}
    });
  } catch (_) {}
}


/* ───────────────────────── Upload / refresh glue ───────────────────────── */

function wirePostUploadRefresh() {
  try {
    if (window.__VECTOPLAN_POST_UPLOAD_REFRESH_WIRED__) return;
    window.__VECTOPLAN_POST_UPLOAD_REFRESH_WIRED__ = true;

    window.addEventListener("chat:uploaded", () => {
      try {
        void refreshVersionsUI();
        void refreshTranscriptIncremental();
      } catch (_) {}
    });

    window.addEventListener("chat:refresh", () => {
      try {
        void refreshTranscriptIncremental();
      } catch (_) {}
    });

    window.addEventListener("versions:refresh", () => {
      try {
        void refreshVersionsUI();
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
      editorUrl,
      mapUrl,
      setWorkspaceMode,
      viewerFrame,
      isUnsafeLegacyTarget,
      stableLocalUrl,
      cacheBustLocalUrl,
    };
  } catch (_) {}
}


/* ───────────────────────── Boot ───────────────────────── */

async function boot() {
  if (window.__VECTOPLAN_APP_BOOTED__) return;
  window.__VECTOPLAN_APP_BOOTED__ = true;

  safeCall("exposeWorkspaceDebugApi", exposeWorkspaceDebugApi);

  safeCall("initChatLayout", () => initChatLayout({ breakpointPx: 900 }));

  safeCall("wireThemeToggle", wireThemeToggle);
  safeCall("wireGlobalStatus", wireGlobalStatus);
  safeCall("wireHotkeys", wireHotkeys);

  safeCall("wireWorkspaceToolbar", wireWorkspaceToolbar);
  safeCall("wireVersionsDropdown", wireVersionsDropdown);

  safeCall("wireComposer", wireComposer);
  safeCall("wireTranscriptRefresh", wireTranscriptRefresh);
  safeCall("wire2dEventBridge", wire2dEventBridge);
  safeCall("wireEditorEventBridge", wireEditorEventBridge);
  safeCall("wirePostUploadRefresh", wirePostUploadRefresh);

  await safeAwait("loadTemplatesIndex", loadTemplatesIndex);
  await safeAwait("loadTranscript", loadTranscript);

  await safeAwait("applyInitialWorkspaceMode", applyInitialWorkspaceMode);

  safeCall("initialVersionsCount", () => {
    void (async () => {
      const items = await fetchVersionsRaw();
      setVersionCount(items.length);
      neutralizeVersionViewerActions();
    })();
  });
}

void boot();