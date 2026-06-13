// services/app/static/js/chat/viewer.js
// Viewer-Logik: Iframe steuern, 3D/2D/Map/Admin/LV-Modus, Polling, Toolbar-Wiring, Messaging.
// Robustheitsziele:
// - Defensive DOM-Checks (Template-Varianten / fehlende Buttons)
// - try/catch überall an I/O Grenzen (fetch, postMessage, localStorage)
// - Cache-Busting für lokale Seiten (Map/Admin/LV), um "alte" Templates leichter zu umgehen
// - Persistenz des viewer_mode best-effort ohne versehentlich komplette Selection zu überschreiben
//
// Hinweis:
// - showViewer() ist rückwärtskompatibel (2. Parameter optional).
// - Admin/LV werden als lokale Iframe-Seiten geladen (same-origin), ohne /embed/frame Proxy.

import { $, fetchJSON, uiState, startPolling, stopPolling as _stop } from "./core.js";
import { getConfig } from "./api.js";

/* ───────────────────────── Safe Helpers ───────────────────────── */

function safe(fn, fallback) {
  try { return fn(); } catch { return fallback; }
}
function safeStr(x, d = "") {
  return safe(() => String(x ?? d), d);
}
function nowTs() {
  return safe(() => Date.now(), 0);
}

/* ───────────────────────── State ───────────────────────── */

uiState.lastViewerUrl = uiState.lastViewerUrl || "";
uiState.rawViewerUrl  = uiState.rawViewerUrl  || "";
uiState._cad2d        = uiState._cad2d        || { dxfUrl: "", embedUrl: "", status: "init" };

// viewerMode: '3d' | '2d' | 'map' | 'admin' | 'lv'
if (!uiState.viewerMode) {
  // Best-effort: initial aus DOM ableiten (passt zu aria-pressed im Template), sonst fallback 3d
  const domMode = safe(() => {
    const pressed = (id) => {
      const b = $(id);
      return b && String(b.getAttribute("aria-pressed") || "") === "true";
    };
    if (pressed("modeAdminBtn")) return "admin";
    if (pressed("modeLvBtn"))    return "lv";
    if (pressed("modeMapBtn"))   return "map";
    if (pressed("mode2dBtn"))    return "2d";
    if (pressed("mode3dBtn"))    return "3d";
    return "";
  }, "");
  uiState.viewerMode = domMode || "3d";
}

/* ───────────────────────── Link / UI helpers ───────────────────────── */

function setLinkRaw(href) {
  safe(() => {
    const a = $("viewerOpenRawBtn");
    if (a) a.href = href || "#";
  });
}

function setModePressed() {
  safe(() => {
    const set = (id, on) => {
      const b = $(id);
      if (b) b.setAttribute("aria-pressed", String(!!on));
    };
    set("mode3dBtn",    uiState.viewerMode === "3d");
    set("mode2dBtn",    uiState.viewerMode === "2d");
    set("modeMapBtn",   uiState.viewerMode === "map");
    set("modeAdminBtn", uiState.viewerMode === "admin");
    set("modeLvBtn",    uiState.viewerMode === "lv");
  });
}

/* ───────────────────────── Persistenz: viewer_mode (best-effort) ─────────────────────────
   WICHTIG:
   Dein statePutPath wird auch für Selection genutzt. Um nichts "wegzuputten", versuchen wir:
   1) PUT { patch: { viewer_mode: ... } }  (wie cards.js)
   2) Fallback: PUT { viewer_mode: ... }   (legacy)
------------------------------------------------------------------- */
async function persistMode() {
  const cfg = getConfig();
  const url = cfg.statePutPath;
  if (!url || url === "__DISABLED__") return;

  const mode = uiState.viewerMode;

  // 1) Patch-Form
  const r1 = await fetchJSON(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patch: { viewer_mode: mode } }),
    credentials: "same-origin",
  });

  if (r1 && r1.ok) return;

  // 2) Legacy-Form als Fallback
  // Nur bei "typischen" Schemafehlern erneut versuchen
  const s = Number(r1?.status || 0);
  if (s && ![400, 404, 405, 415, 422].includes(s)) return;

  await fetchJSON(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewer_mode: mode }),
    credentials: "same-origin",
  });
}

/* ───────────────────────── URL / Cache handling ───────────────────────── */

function isProbablyLocal(u) {
  return safe(() => {
    const s = String(u || "").trim();
    if (!s) return false;
    // relative same-origin routes
    if (s.startsWith("/")) return true;
    // absolute same-origin
    try {
      const abs = new URL(s, location.href);
      return abs.origin === location.origin;
    } catch { return false; }
  }, false);
}

function withCacheBust(u) {
  return safe(() => {
    const s = String(u || "").trim();
    if (!s) return s;

    // Nur bei lokalen URLs (same-origin). Externe Services lassen wir unangetastet.
    if (!isProbablyLocal(s)) return s;

    const url = new URL(s, location.origin);
    url.searchParams.set("r", String(nowTs()));
    // keep relative if input was relative
    if (s.startsWith("/")) return url.pathname + "?" + url.searchParams.toString() + (url.hash || "");
    return url.toString();
  }, u);
}

/** Immer über unseren Proxy einbetten – nie direkt zu vectoplan.com in den Iframe. */
function normalizeViewerUrl(url) {
  try {
    const u = String(url || "").trim();
    if (!u) return "";

    // Bereits proxied?
    if (u.startsWith("/embed/frame?url=")) return u;

    // Absolute/relative Ziel-URL normalisieren
    const abs = u.startsWith("http")
      ? u
      : (u.startsWith("/projects/") ? `https://vectoplan.com${u}` : u);

    const host = new URL(abs, location.href).hostname.toLowerCase();

    // VectoPlan-Viewer immer durch unseren Frame-Proxy schicken (PAT / Header etc.)
    if (host.endsWith("vectoplan.com")) {
      return `/embed/frame?url=${encodeURIComponent(abs)}`;
    }
    return abs;
  } catch {
    return url;
  }
}

function setIframeSrc(src, { force = false } = {}) {
  const frame = $("viewer-frame");
  if (!frame || !src) return false;

  // Guard gegen unnötige reloads
  if (!force && src === uiState.lastViewerUrl) return false;

  safe(() => {
    if (force) {
      // best-effort "kick" um hard reload zu erzwingen
      try { frame.src = "about:blank"; } catch {}
      setTimeout(() => { try { frame.src = src; } catch {} }, 20);
    } else {
      frame.src = src;
    }
  });

  uiState.lastViewerUrl = src;
  return true;
}

/* ───────────────────────── Public: Viewer setzen ───────────────────────── */

function showViewer(url, opts = {}) {
  try {
    if (!url) return;

    const o = (opts && typeof opts === "object") ? opts : {};
    const force = !!o.force;
    const cacheBust = !!o.cacheBust;

    let next = normalizeViewerUrl(url);

    // cacheBust nur für lokale Seiten (Admin/LV/Map), damit Templates schneller "aktuell" wirken
    if (cacheBust) next = withCacheBust(next);

    setIframeSrc(next, { force });

    // Link-Update nur, wenn explizit gewünscht (default: ja für rawViewerUrl nicht, weil proxied)
    if (o.setRawLink === true) setLinkRaw(url);
  } catch {}
}
export { showViewer };

/* ───────────────────────── Versionen-Panel Ping ───────────────────────── */

function pingVersions() {
  safe(() => window.dispatchEvent(new CustomEvent("versions:refresh")));
}

/* ───────────────────────── 3D-Flow (Speckle) ───────────────────────── */

export async function refreshViewerFromServer() {
  try {
    const cfg = getConfig();
    const r = await fetchJSON(cfg.viewerJsonPath);
    if (!r.ok) return false;

    const vurl = safeStr(r.json?.viewer_url || "");
    uiState.rawViewerUrl = safeStr(r.json?.raw_viewer_url || "");

    if (vurl) {
      // Speckle Viewer via proxy
      showViewer(vurl, { force: false, cacheBust: false });
      _stop();
      setLinkRaw(uiState.rawViewerUrl || "#");
      pingVersions();
      return true;
    }
    return false;
  } catch { return false; }
}

export function startViewerPolling({ intervalMs = 5000, maxTries = 120 } = {}) {
  if (uiState.viewerMode !== "3d") return;
  pingVersions();
  startPolling(async () => {
    try {
      const ok = await refreshViewerFromServer();
      pingVersions();
      if (ok) _stop();
    } catch {}
  }, { intervalMs, maxTries });
}

export function stopViewerPolling() { _stop(); }

/* ───────────────────────── 2D-Flow (CAD Microservice) ───────────────────────── */

function getCadConfig() {
  const cfg = getConfig();
  const chatId = safeStr(cfg.chatId || "");
  const cadEmbedJsonPath =
    cfg.cadEmbedJsonPath ||
    (chatId ? `/ui/chat/${encodeURIComponent(chatId)}/cad-embed.json` : "");
  const cadEmbedBase = cfg.cadEmbedBase || "http://localhost:8050";
  return { cadEmbedJsonPath, cadEmbedBase };
}

async function resolveCadEmbedFromServer() {
  const { cadEmbedJsonPath } = getCadConfig();
  if (!cadEmbedJsonPath) return { ok: false, error: "no cadEmbedJsonPath" };

  const r = await fetchJSON(cadEmbedJsonPath);
  if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };

  const embed = safeStr(r.json?.embed_url || "");
  const raw   = safeStr(r.json?.dxf_url || "");
  return { ok: !!embed, embedUrl: embed, rawUrl: raw };
}

async function refresh2DFromServer() {
  try {
    const res = await resolveCadEmbedFromServer();
    if (!res.ok) {
      setLinkRaw("#");
      const alert = $("systemAlert");
      if (alert) {
        alert.textContent = "2D-Viewer konnte nicht vorbereitet werden. CAD-Service prüfen.";
        alert.classList.remove("hidden");
      }
      return false;
    }

    uiState._cad2d.embedUrl = res.embedUrl || "";
    uiState._cad2d.dxfUrl   = res.rawUrl || "";
    setLinkRaw(uiState._cad2d.dxfUrl || "#");

    const alert = $("systemAlert");
    if (alert) { alert.classList.add("hidden"); alert.textContent = ""; }

    return !!uiState._cad2d.embedUrl;
  } catch {
    setLinkRaw("#");
    const alert = $("systemAlert");
    if (alert) {
      alert.textContent = "2D-Viewer Fehler. Details in Konsole.";
      alert.classList.remove("hidden");
    }
    return false;
  }
}

export async function switchTo3D() {
  uiState.viewerMode = "3d";
  setModePressed();
  await persistMode();

  const ok = await refreshViewerFromServer();
  if (!ok) startViewerPolling();
}

export async function switchTo2D() {
  uiState.viewerMode = "2d";
  setModePressed();
  await persistMode();
  _stop();

  const ok = await refresh2DFromServer();
  if (!ok) return;

  const url = uiState._cad2d.embedUrl || "";
  // Extern -> kein cacheBust
  showViewer(url, { force: false, cacheBust: false });
}

/* ───────────────────────── Map-Flow (OpenLayers) ───────────────────────── */

function getMapPageUrl() {
  const cfg = getConfig();
  const chatId = safeStr(cfg.chatId || "");
  return cfg.mapPagePath || (chatId ? `/ui/chat/${encodeURIComponent(chatId)}/map` : "");
}

export async function switchToMap() {
  uiState.viewerMode = "map";
  setModePressed();
  await persistMode();
  _stop();

  const url = getMapPageUrl();
  setLinkRaw("#");
  if (url) showViewer(url, { force: false, cacheBust: true });
}

/* ───────────────────────── Admin/LV-Flow (lokale Placeholder-Seiten) ───────────────────────── */

function getAdminPageUrl() {
  const cfg = getConfig();
  // api.js liefert derzeit ggf. keine admin/lv pfade -> fallback auf APP_CONFIG
  const fromCfg = safeStr(cfg.adminPagePath || "");
  const fromWin = safeStr(window.APP_CONFIG?.adminPagePath || "");
  const chatId  = safeStr(cfg.chatId || window.APP_CONFIG?.chatId || "");
  return fromCfg || fromWin || (chatId ? `/ui/chat/${encodeURIComponent(chatId)}/admin` : "");
}

function getLvPageUrl() {
  const cfg = getConfig();
  const fromCfg = safeStr(cfg.lvPagePath || "");
  const fromWin = safeStr(window.APP_CONFIG?.lvPagePath || "");
  const chatId  = safeStr(cfg.chatId || window.APP_CONFIG?.chatId || "");
  return fromCfg || fromWin || (chatId ? `/ui/chat/${encodeURIComponent(chatId)}/lv` : "");
}

export async function switchToAdmin() {
  uiState.viewerMode = "admin";
  setModePressed();
  await persistMode();
  _stop();

  const url = getAdminPageUrl();
  setLinkRaw(url || "#");
  if (url) showViewer(url, { force: false, cacheBust: true });
}

export async function switchToLv() {
  uiState.viewerMode = "lv";
  setModePressed();
  await persistMode();
  _stop();

  const url = getLvPageUrl();
  setLinkRaw(url || "#");
  if (url) showViewer(url, { force: false, cacheBust: true });
}

/* ───────────────────────── Toolbar-Wiring ───────────────────────── */

export function wireViewerToolbar() {
  try {
    const b3d    = $("mode3dBtn");
    const b2d    = $("mode2dBtn");
    const bmap   = $("modeMapBtn");
    const bAdmin = $("modeAdminBtn");
    const bLv    = $("modeLvBtn");

    if (b3d && !b3d._wired)   { b3d._wired   = true; b3d.addEventListener("click", () => { void switchTo3D(); }); }
    if (b2d && !b2d._wired)   { b2d._wired   = true; b2d.addEventListener("click", () => { void switchTo2D(); }); }
    if (bmap && !bmap._wired) { bmap._wired  = true; bmap.addEventListener("click", () => { void switchToMap(); }); }

    if (bAdmin && !bAdmin._wired) { bAdmin._wired = true; bAdmin.addEventListener("click", () => { void switchToAdmin(); }); }
    if (bLv && !bLv._wired)       { bLv._wired    = true; bLv.addEventListener("click", () => { void switchToLv(); }); }

    setModePressed();

    const btn = $("viewerRefreshBtn");
    if (btn && !btn._wired) {
      btn._wired = true;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          if (uiState.viewerMode === "3d") {
            const ok = await refreshViewerFromServer();
            pingVersions();
            if (!ok) startViewerPolling();
          } else if (uiState.viewerMode === "2d") {
            const ok = await refresh2DFromServer();
            if (ok) showViewer(uiState._cad2d.embedUrl || "", { force: true });
          } else if (uiState.viewerMode === "map") {
            const base = getMapPageUrl();
            if (base) {
              uiState.lastViewerUrl = "";
              showViewer(base, { force: false, cacheBust: true });
            }
          } else if (uiState.viewerMode === "admin") {
            const u = getAdminPageUrl();
            if (u) { uiState.lastViewerUrl = ""; showViewer(u, { cacheBust: true }); }
          } else if (uiState.viewerMode === "lv") {
            const u = getLvPageUrl();
            if (u) { uiState.lastViewerUrl = ""; showViewer(u, { cacheBust: true }); }
          }
        } finally { btn.disabled = false; }
      });
    }

    wireCad2DIframeMessaging();
    wireMapIframeMessaging();
  } catch {}
}

/* ───────────────────────── Iframe-Messaging ───────────────────────── */

let _msgWired2D = false;
function wireCad2DIframeMessaging() {
  if (_msgWired2D) return;
  _msgWired2D = true;

  window.addEventListener("message", (ev) => {
    try {
      const data = ev?.data;
      if (!data || typeof data !== "object") return;

      if (data.t === "cad2d.status") {
        uiState._cad2d.status = data.status || uiState._cad2d.status;

        if (data.status === "ready" && data.url) setLinkRaw(data.url);

        const alert = $("systemAlert");
        if (alert) {
          if (data.status === "error") {
            alert.textContent = "2D-Viewer Fehler. Details in Konsole.";
            alert.classList.remove("hidden");
          } else if (data.status === "no_plan") {
            alert.textContent = "Kein 2D-Plan vorhanden.";
            alert.classList.remove("hidden");
          } else {
            alert.classList.add("hidden");
            alert.textContent = "";
          }
        }
        return;
      }

      if (data.t === "cad2d.select" && Array.isArray(data.items)) {
        window.dispatchEvent(new CustomEvent("cad2d-select", { detail: { items: data.items } }));
        return;
      }

      if (data.t === "cad2d.hover") {
        window.dispatchEvent(new CustomEvent("cad2d-hover", { detail: { item: data.item } }));
        return;
      }
    } catch {}
  });
}

let _msgWiredMap = false;
function wireMapIframeMessaging() {
  if (_msgWiredMap) return;
  _msgWiredMap = true;

  window.addEventListener("message", (ev) => {
    try {
      const data = ev?.data;
      if (!data || typeof data !== "object") return;

      if (data.t === "map.status") return;

      if (data.t === "map.select") {
        window.dispatchEvent(new CustomEvent("map-select", { detail: { feature: data.feature } }));
        return;
      }

      if (data.t === "map.hover") {
        window.dispatchEvent(new CustomEvent("map-hover", { detail: { feature: data.feature } }));
        return;
      }
    } catch {}
  });
}

/* ───────────────────────── Init-Helfer ───────────────────────── */

export async function initViewerModeFromServer() {
  try {
    const cfg = getConfig();
    if (!cfg.stateGetPath || cfg.stateGetPath === "__DISABLED__") return;

    const r = await fetchJSON(cfg.stateGetPath);
    if (!r.ok) return;

    const mode = safeStr(r.json?.viewer_mode || "");
    if (["2d", "3d", "map", "admin", "lv"].includes(mode)) {
      uiState.viewerMode = mode;
      setModePressed();
    }
  } catch {}
}

export async function ensureInitialViewer() {
  try {
    if (uiState.viewerMode === "3d") {
      const ok = await refreshViewerFromServer();
      if (!ok) startViewerPolling();
      return;
    }

    if (uiState.viewerMode === "2d") {
      await switchTo2D();
      return;
    }

    if (uiState.viewerMode === "map") {
      await switchToMap();
      return;
    }

    if (uiState.viewerMode === "admin") {
      await switchToAdmin();
      return;
    }

    if (uiState.viewerMode === "lv") {
      await switchToLv();
      return;
    }

    // Fallback
    await switchTo3D();
  } catch {}
}