// services/app/static/js/chat/main.js
// Orchestrator: verdrahtet Layout, Viewer, Versionen, Transcript, Composer.
// Robustheitsziele:
// - Doppelinits verhindern
// - Jeder Schritt isoliert (try/catch), damit ein Fehler nicht alles stoppt
// - Defensive DOM-Checks (Template-Varianten / fehlende Elemente)
// - Dropdown-Verhalten stabil (Outside-Click, ESC, Blur)
// - Viewer-Version-Auswahl + Persistenz (Selection) robust
// - Admin/LV: als lokale Iframe-Seiten, inkl. Cache-Bust + ohne Polling-Überschreiben
//
// Hinweis:
// - Chat-Layout (Drawer/Collapse) ist ausgelagert nach ./layout.js (auto-init + export initChatLayout).
// - Projektinfo ist bewusst entfernt (kein Button, keine Orchestrator-Logik).

import { initChatLayout } from "./layout.js";

import { $, uiState } from "./core.js";
import { getConfig } from "./api.js";
import {
  showViewer,
  wireViewerToolbar,
  initViewerModeFromServer,
  ensureInitialViewer,
  stopViewerPolling,
} from "./viewer.js";
import { wireVersionsToolbar, loadVersions } from "./versions.js";
import { loadTranscript, refreshTranscriptIncremental, wireTranscriptRefresh } from "./transcript.js";
import { wireComposer } from "./composer.js";
import { loadTemplatesIndex } from "./cards.js";

/* ───────────────────────── Boot-Safety Helpers ───────────────────────── */

function safeCall(label, fn) {
  try { return fn(); }
  catch (e) {
    try { console.warn(`[boot] ${label} failed`, e); } catch {}
    return undefined;
  }
}
async function safeAwait(label, fn) {
  try { return await fn(); }
  catch (e) {
    try { console.warn(`[boot] ${label} failed`, e); } catch {}
    return undefined;
  }
}
function safeOn(el, ev, handler, opts) {
  try {
    if (!el) return false;
    el.addEventListener(ev, handler, opts);
    return true;
  } catch { return false; }
}

/* ───────────────────────── Local URL Cache-Busting ───────────────────────── */

function cacheBustLocalUrl(u) {
  try {
    const s = String(u || "").trim();
    if (!s) return s;
    if (!s.startsWith("/")) return s; // only same-origin relative

    const url = new URL(s, location.origin);
    url.searchParams.set("r", String(Date.now()));
    return url.pathname + "?" + url.searchParams.toString() + (url.hash || "");
  } catch {
    return u;
  }
}

/* ───────────────────────── Viewer-Kontext & Persistenz (Version-Selection) ─────────────────────────
   Steuert:
   - Speckle Model/Version-URL Bau (für 3D Viewer)
   - Speichern/Laden der Auswahl über stateGetPath/statePutPath

   WICHTIG:
   - Der Viewer-Mode (3D/2D/Map) bleibt in viewer.js.
   - Admin/LV werden hier als "local iframe swap" gehandhabt.
------------------------------------------------------------------- */

const VIEW = {
  rawModelUrl: "",          // Model-Viewer-URL (neueste Version)
  origin: "",               // https://vectoplan.com
  token: "",                // "?token=..."
  embed: "",                // "#embed=..."

  // Persistierte Auswahl (nur für 3D relevant)
  selectedProjectId: "",
  selectedModelId: "",
  selectedCommitId: null,   // versionId/commitId oder null (Model)
};

async function initViewerCtx() {
  try {
    const cfg = getConfig();
    const p = cfg.viewerJsonPath || window.APP_CONFIG?.viewerJsonPath;
    if (!p) return;

    const r = await fetch(p, { credentials: "same-origin", cache: "no-store" }).then(x => x.json()).catch(() => null);
    const raw = r?.raw_viewer_url || "";
    VIEW.rawModelUrl = raw;

    if (!raw) return;

    const u = new URL(raw, location.href);
    VIEW.origin = u.origin;
    VIEW.token  = u.search || "";
    VIEW.embed  = u.hash   || "";

    const a = $("viewerOpenRawBtn");
    if (a) a.href = raw;
  } catch (e) {
    try { console.warn("[viewerCtx] init failed", e); } catch {}
  }
}

function buildVersionUrl(projectId, modelId, versionId) {
  try {
    if (!(VIEW.origin && projectId && modelId && versionId)) return "";

    const base = `${VIEW.origin}/projects/${encodeURIComponent(projectId)}/models/${encodeURIComponent(modelId)}`;
    const q = new URLSearchParams();

    // Speckle v3: versionId (nicht commitId)
    q.set("versionId", String(versionId));
    q.set("r", String(Date.now())); // cache-bust (Viewer-Seite)

    // Token-Query berücksichtigen
    const tokenStr = (VIEW.token || "").trim();
    if (tokenStr) {
      const t = tokenStr.startsWith("?") ? tokenStr.slice(1) : tokenStr;
      try {
        const add = new URLSearchParams(t);
        add.forEach((v, k) => q.append(k, v));
      } catch {
        const mt = t.match(/(?:^|&)token=([^&]+)/);
        if (mt && mt[1]) q.append("token", mt[1]);
      }
    }

    return `${base}?${q.toString()}${VIEW.embed || ""}`;
  } catch {
    return "";
  }
}

function hardSwapIframe(nextSrc) {
  const old = $("viewer-frame");
  if (!old || !nextSrc) return;

  try {
    // Wichtig: viewer.js nutzt uiState.lastViewerUrl als Guard. Reset, da wir iframe ersetzen.
    try { uiState.lastViewerUrl = ""; } catch {}

    const fresh = document.createElement("iframe");
    fresh.id = old.id;
    fresh.className = old.className;
    fresh.setAttribute("title", old.getAttribute("title") || "Viewer");
    fresh.setAttribute("frameborder", "0");

    if (old.getAttribute("allow")) fresh.setAttribute("allow", old.getAttribute("allow"));
    if (old.getAttribute("referrerpolicy")) fresh.setAttribute("referrerpolicy", old.getAttribute("referrerpolicy"));
    if (old.getAttribute("sandbox")) fresh.setAttribute("sandbox", old.getAttribute("sandbox"));
    if (old.getAttribute("loading")) fresh.setAttribute("loading", old.getAttribute("loading"));

    old.replaceWith(fresh);

    // Quelle setzen + best-effort Retry
    fresh.src = nextSrc;
    setTimeout(() => {
      try {
        // Wenn iframe "blank" bleibt, nochmal setzen
        const blank = !fresh.contentWindow || !fresh.contentDocument || fresh.contentDocument.location.href === "about:blank";
        if (blank) fresh.src = nextSrc;
      } catch {
        try { fresh.src = nextSrc; } catch {}
      }
    }, 1200);
  } catch {}
}

/** Setzt 3D Viewer (rawUrl), iframe bekommt Proxy nur für vectoplan.com (wie viewer.js). */
function setViewerRawUrl(rawUrl) {
  try {
    const raw = String(rawUrl || "").trim();
    if (!raw) return;

    // Relative Speckle-Pfade unterstützen
    const abs = raw.startsWith("/projects/") ? `https://vectoplan.com${raw}` : raw;

    let nextSrc = abs;

    // Proxy nur für vectoplan.com (kompatibel zu viewer.js)
    try {
      const host = new URL(abs, location.href).hostname.toLowerCase();
      if (host.endsWith("vectoplan.com")) {
        nextSrc = `/embed/frame?url=${encodeURIComponent(abs)}`;
      }
    } catch {}

    hardSwapIframe(nextSrc);

    const a = $("viewerOpenRawBtn");
    if (a) a.href = abs;
  } catch {}
}

/** Setzt lokale Viewer-Seiten (Map/2D/Admin/LV) direkt im iframe; mit Cache-Bust gegen „alte“ Inhalte. */
function setViewerLocalUrl(localPath, { cacheBust = true } = {}) {
  try {
    const s = String(localPath || "").trim();
    if (!s) return;

    const src = cacheBust ? cacheBustLocalUrl(s) : s;
    hardSwapIframe(src);

    // "Öffnen" zeigt die stabile URL (ohne r=)
    const a = $("viewerOpenRawBtn");
    if (a) a.href = s;
  } catch {}
}

async function fetchViewerSelection() {
  const cfg = getConfig();
  if (!cfg.stateGetPath || cfg.stateGetPath === "__DISABLED__") return null;
  try {
    return await fetch(cfg.stateGetPath, { credentials: "same-origin", cache: "no-store" }).then(r => r.json()).catch(() => null);
  } catch {
    return null;
  }
}

async function saveViewerSelection(sel) {
  const cfg = getConfig();
  if (!cfg.statePutPath || cfg.statePutPath === "__DISABLED__") return;
  try {
    await fetch(cfg.statePutPath, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(sel || {}),
    }).catch(() => {});
  } catch {}
}

/** Nur anwenden, wenn wir im 3D-Kontext sind (sonst Admin/LV/2D/Map nicht überschreiben). */
function canApply3dSelectionNow() {
  const mode = String(uiState.viewerMode || "");
  return mode === "3d" || mode === ""; // defensiv: "" behandeln wir als 3d-kontext
}

async function applySavedSelection() {
  const saved = await fetchViewerSelection();

  // Auswahl merken (auch wenn wir nicht im 3D sind)
  if (saved && saved.mode === "version" && saved.version_id && saved.project_id && saved.model_id) {
    VIEW.selectedProjectId = String(saved.project_id || "");
    VIEW.selectedModelId   = String(saved.model_id || "");
    VIEW.selectedCommitId  = String(saved.version_id || "");
  } else {
    VIEW.selectedProjectId = "";
    VIEW.selectedModelId   = "";
    VIEW.selectedCommitId  = null;
  }

  // Nicht im 3D-Modus? Dann hier nicht ins iframe schreiben.
  if (!canApply3dSelectionNow()) {
    try { markSelectedVersion(VIEW.selectedCommitId); } catch {}
    return;
  }

  if (VIEW.selectedCommitId && VIEW.selectedProjectId && VIEW.selectedModelId) {
    const u = buildVersionUrl(VIEW.selectedProjectId, VIEW.selectedModelId, VIEW.selectedCommitId);
    if (u) {
      setViewerRawUrl(u);
      try { markSelectedVersion(VIEW.selectedCommitId); } catch {}
      return;
    }
  }

  // Fallback: Model
  if (VIEW.rawModelUrl) setViewerRawUrl(VIEW.rawModelUrl);
  try { markSelectedVersion(null); } catch {}
}

async function selectLatestModel() {
  VIEW.selectedCommitId = null;
  VIEW.selectedProjectId = "";
  VIEW.selectedModelId = "";

  // User-Aktion → wir dürfen in 3D gehen
  try { uiState.viewerMode = "3d"; } catch {}
  setModePressed("mode3dBtn");

  setViewerRawUrl(VIEW.rawModelUrl || "");
  try { markSelectedVersion(null); } catch {}
  await saveViewerSelection({ mode: "model", project_id: null, model_id: null, version_id: null });
}

/* ───────────────────────── Theme ───────────────────────── */

function themeGet() {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {}
  const attr = document.documentElement.getAttribute("data-theme");
  return (attr === "dark" || attr === "light") ? attr : "light";
}

function themeApply(t = "light") {
  try {
    const root = document.documentElement;
    const next = (t === "dark") ? "dark" : "light";

    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch {}

    const btn = $("themeToggleBtn");
    if (btn) {
      const isDark = (next === "dark");
      btn.setAttribute("aria-pressed", String(isDark));
      btn.textContent = isDark ? "☀️ Hell" : "🌙 Dunkel";
      btn.title = isDark ? "Auf helles Theme umschalten" : "Auf dunkles Theme umschalten";
    }
  } catch {}
}

function themeToggle() {
  try {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    themeApply(cur === "dark" ? "light" : "dark");
  } catch {}
}

function wireThemeToggle() {
  if (window.__THEME_WIRED__) return;
  window.__THEME_WIRED__ = true;

  const btn = $("themeToggleBtn");
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.addEventListener("click", themeToggle);
  }

  themeApply(themeGet());
}

/* ───────────────────────── Versionen: Dropdown/Panel Helpers ───────────────────────── */

function getVersionsContainer() {
  return $("versionsDropdown") || $("versionsPanel") || null;
}

function isHidden(el) {
  try {
    if (!el) return true;
    if (el.classList.contains("hidden")) return true;
    if (String(el.getAttribute("aria-hidden") || "") === "true") return true;
    return false;
  } catch {
    return true;
  }
}

function versionsOpen() {
  try {
    const toggle = $("versionsToggleBtn");
    const box = getVersionsContainer();
    if (!toggle || !box) return false;

    box.classList.remove("hidden");
    box.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");

    setTimeout(() => {
      try {
        const firstBtn = box.querySelector("button, [href], [tabindex]:not([tabindex='-1'])");
        if (firstBtn && typeof firstBtn.focus === "function") firstBtn.focus();
      } catch {}
    }, 0);

    return true;
  } catch {
    return false;
  }
}

function versionsClose() {
  try {
    const toggle = $("versionsToggleBtn");
    const box = getVersionsContainer();
    if (!toggle || !box) return false;

    box.classList.add("hidden");
    box.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");
    return true;
  } catch {
    return false;
  }
}

function versionsToggle() {
  try {
    const box = getVersionsContainer();
    if (!box) return false;
    if (isHidden(box)) return versionsOpen();
    versionsClose();
    return false;
  } catch {
    return false;
  }
}

function wireVersionsDropdown() {
  const toggle = $("versionsToggleBtn");
  const box = getVersionsContainer();
  if (!toggle || !box) return;

  if (toggle._versionsDropdownWired) return;
  toggle._versionsDropdownWired = true;

  try { if (box.id) toggle.setAttribute("aria-controls", box.id); } catch {}

  safeOn(toggle, "click", (e) => {
    try {
      e.preventDefault();
      const nowOpen = versionsToggle();
      if (nowOpen) setTimeout(() => { void refreshVersionsUI(); }, 0);
    } catch {}
  });

  // Outside click/tap
  const onOutside = (ev) => {
    try {
      const box2 = getVersionsContainer();
      if (!box2 || isHidden(box2)) return;

      const t = ev?.target;
      if (!t) return;
      if (box2.contains(t)) return;
      if (toggle.contains(t)) return;

      versionsClose();
    } catch {}
  };

  safeOn(document, "mousedown", onOutside, { capture: true, passive: true });
  safeOn(document, "touchstart", onOutside, { capture: true, passive: true });

  // Escape closes dropdown
  safeOn(document, "keydown", (e) => {
    try {
      if (e.key !== "Escape") return;
      const box2 = getVersionsContainer();
      if (!box2 || isHidden(box2)) return;
      versionsClose();
    } catch {}
  });

  // Optional: beim Tab-Wechsel schließen
  safeOn(window, "blur", () => { try { versionsClose(); } catch {} }, { passive: true });
}

/* ───────────────────────── Versionsliste: Auswahl-Button + Markierung ───────────────────────── */

async function fetchVersionsRaw() {
  try {
    const p = window.APP_CONFIG?.versionsPath || getConfig().versionsPath;
    if (!p) return [];

    const r = await fetch(p, { credentials: "same-origin", cache: "no-store" }).then(x => x.json()).catch(() => null);
    return Array.isArray(r?.items) ? r.items : [];
  } catch {
    return [];
  }
}

function markSelectedVersion(commitIdOrNull) {
  try {
    const ul = $("versionsList");
    if (!ul) return;

    [...ul.querySelectorAll("li")].forEach(li => {
      const cid = li.getAttribute("data-commit-id") || "";
      const isSel = !!commitIdOrNull && cid === commitIdOrNull;

      li.classList.toggle("is-selected", isSel);
      li.setAttribute("aria-selected", isSel ? "true" : "false");

      const selBtn = li.querySelector("[data-action='select-version']");
      if (selBtn) {
        selBtn.disabled = isSel ? true : (selBtn._disabledOrig || false);
        selBtn.textContent = isSel ? "Ausgewählt" : "Version auswählen";
      }
    });
  } catch {}
}

function extractSpeckle(it) {
  try {
    const meta = it?.meta || {};
    const sp  = it?.speckle || meta?.speckle || {};
    const pid = sp.project_id || it.speckle_project_id || meta.speckle_project_id || it.project_id || "";
    const mid = sp.model_id   || it.speckle_model_id   || meta.speckle_model_id   || it.model_id   || "";
    const vid = sp.version_id || sp.commit_id ||
                it.speckle_version_id || meta.speckle_version_id ||
                it.version_id || it.commit_id || it.rev || it.id || "";
    return { project_id: String(pid || ""), model_id: String(mid || ""), version_id: String(vid || "") };
  } catch {
    return { project_id: "", model_id: "", version_id: "" };
  }
}

function enhanceVersionsListDOM(versions) {
  const ul = $("versionsList");
  if (!ul) return;

  const lis = [...ul.children];
  for (let i = 0; i < lis.length; i++) {
    const li = lis[i];
    const v  = versions[i] || null;
    if (!v) continue;

    let pid = li.getAttribute("data-project-id") || "";
    let mid = li.getAttribute("data-model-id")   || "";
    let cid = li.getAttribute("data-commit-id")  || "";

    if (!(pid && mid && cid)) {
      const sp = extractSpeckle(v);
      pid = pid || sp.project_id;
      mid = mid || sp.model_id;
      cid = cid || sp.version_id;

      if (pid) li.setAttribute("data-project-id", pid);
      if (mid) li.setAttribute("data-model-id",   mid);
      if (cid) li.setAttribute("data-commit-id",  cid);
    }

    if (!li.classList.contains("vers-item")) li.classList.add("vers-item");

    let btn = li.querySelector("[data-action='select-version']");
    if (!btn) {
      const actions = li.querySelector(".vers-actions") || li;
      btn = document.createElement("button");
      btn.className = "btn";
      btn.type = "button";
      btn.setAttribute("data-action", "select-version");
      actions.appendChild(btn);
    }

    btn.setAttribute("data-project-id", pid || "");
    btn.setAttribute("data-model-id",   mid || "");
    btn.setAttribute("data-commit-id",  cid || "");
    btn.textContent = "Version auswählen";

    const ready = !!(pid && mid && cid);
    btn.disabled = !ready;
    btn._disabledOrig = !ready;
  }

  markSelectedVersion(VIEW.selectedCommitId);
}

async function refreshVersionsUI() {
  try { await loadVersions(); } catch {}
  const items = await fetchVersionsRaw();
  enhanceVersionsListDOM(items);
}

function wireVersionSelection() {
  const ul = $("versionsList");
  if (!ul || ul._selectWired) return;
  ul._selectWired = true;

  ul.addEventListener("click", async (e) => {
    const btn = e.target && e.target.closest?.("[data-action='select-version']");
    if (!btn) return;

    const pid = btn.getAttribute("data-project-id") || "";
    const mid = btn.getAttribute("data-model-id")   || "";
    const cid = btn.getAttribute("data-commit-id")  || "";
    if (!(pid && mid && cid)) return;

    // Version selection ist 3D-Kontext
    try { uiState.viewerMode = "3d"; } catch {}
    setModePressed("mode3dBtn");

    VIEW.selectedProjectId = pid;
    VIEW.selectedModelId   = mid;
    VIEW.selectedCommitId  = cid;

    const url = buildVersionUrl(pid, mid, cid);
    if (!url) return;

    // Dropdown schließen (UX) + Polling stoppen (wir setzen URL manuell)
    try { versionsClose(); } catch {}
    try { stopViewerPolling(); } catch {}

    setViewerRawUrl(url);
    markSelectedVersion(cid);

    await saveViewerSelection({ mode: "version", project_id: pid, model_id: mid, version_id: cid });
  });

  const refreshBtn = $("versRefreshBtn");
  if (refreshBtn && !refreshBtn._refreshWired) {
    refreshBtn._refreshWired = true;
    refreshBtn.addEventListener("click", () => { void refreshVersionsUI(); });
  }

  const mo = new MutationObserver(() => {
    void (async () => {
      const items = await fetchVersionsRaw();
      enhanceVersionsListDOM(items);
    })();
  });
  mo.observe(ul, { childList: true, subtree: false });
}

/* ───────────────────────── Viewer: Extra Modes (Admin/LV) ───────────────────────── */

function setModePressed(activeId) {
  try {
    const ids = ["modeMapBtn", "mode3dBtn", "mode2dBtn", "modeAdminBtn", "modeLvBtn"];
    for (const id of ids) {
      const b = $(id);
      if (!b) continue;
      b.setAttribute("aria-pressed", String(id === activeId));
    }
  } catch {}
}

function wireExtraViewerModes() {
  const cfg = getConfig();

  const btnAdmin = $("modeAdminBtn");
  const btnLv    = $("modeLvBtn");

  if (btnAdmin && !btnAdmin._wired) {
    btnAdmin._wired = true;
    btnAdmin.addEventListener("click", () => {
      try {
        const p = cfg.adminPagePath || window.APP_CONFIG?.adminPagePath || "";
        if (!p) return;

        // Modus setzen, Polling stoppen, lokale Seite cache-busten
        try { uiState.viewerMode = "admin"; } catch {}
        try { stopViewerPolling(); } catch {}

        setModePressed("modeAdminBtn");
        setViewerLocalUrl(p, { cacheBust: true });

        // Versionen-Dropdown schließen
        try { versionsClose(); } catch {}
      } catch {}
    });
  }

  if (btnLv && !btnLv._wired) {
    btnLv._wired = true;
    btnLv.addEventListener("click", () => {
      try {
        const p = cfg.lvPagePath || window.APP_CONFIG?.lvPagePath || "";
        if (!p) return;

        try { uiState.viewerMode = "lv"; } catch {}
        try { stopViewerPolling(); } catch {}

        setModePressed("modeLvBtn");
        setViewerLocalUrl(p, { cacheBust: true });

        try { versionsClose(); } catch {}
      } catch {}
    });
  }

  // Wenn Map/3D/2D geklickt wird, Admin/LV visuell zurücksetzen.
  const clearAdminLv = () => {
    try {
      const a = $("modeAdminBtn"); if (a) a.setAttribute("aria-pressed", "false");
      const l = $("modeLvBtn");    if (l) l.setAttribute("aria-pressed", "false");
    } catch {}
  };

  ["modeMapBtn", "mode3dBtn", "mode2dBtn"].forEach((id) => {
    const b = $(id);
    if (b && !b._clearExtraWired) {
      b._clearExtraWired = true;
      b.addEventListener("click", () => { clearAdminLv(); });
    }
  });

  // Wenn 3D geklickt wird: ggf. gespeicherte Version-Auswahl wieder anwenden (nach kurzer Verzögerung)
  const b3d = $("mode3dBtn");
  if (b3d && !b3d._applySelWired) {
    b3d._applySelWired = true;
    b3d.addEventListener("click", () => {
      try {
        setTimeout(() => {
          try { uiState.viewerMode = "3d"; } catch {}
          // Wenn eine Version ausgewählt ist: direkt setzen
          if (VIEW.selectedCommitId && VIEW.selectedProjectId && VIEW.selectedModelId) {
            const u = buildVersionUrl(VIEW.selectedProjectId, VIEW.selectedModelId, VIEW.selectedCommitId);
            if (u) {
              try { stopViewerPolling(); } catch {}
              setViewerRawUrl(u);
              markSelectedVersion(VIEW.selectedCommitId);
            }
          }
        }, 300);
      } catch {}
    });
  }
}

/* ───────────────────────── Repair & Alignment ───────────────────────── */

async function runRepair(limit = 100) {
  try {
    const cfg = getConfig();
    const chatId = cfg.chatId || "";
    if (!chatId) return false;

    const url = `/v1/chats/${encodeURIComponent(chatId)}/vectoplan/repair`;
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({ limit }),
    });
    if (!r.ok) return false;

    await r.json().catch(() => ({}));
    await refreshVersionsUI();

    // Auswahl nachziehen
    const sel = await fetchViewerSelection();
    if (sel && sel.mode === "version" && sel.version_id && sel.project_id && sel.model_id) {
      VIEW.selectedProjectId = String(sel.project_id || "");
      VIEW.selectedModelId   = String(sel.model_id || "");
      VIEW.selectedCommitId  = String(sel.version_id || "");
      markSelectedVersion(VIEW.selectedCommitId);

      if (canApply3dSelectionNow()) {
        const u = buildVersionUrl(VIEW.selectedProjectId, VIEW.selectedModelId, VIEW.selectedCommitId);
        if (u) setViewerRawUrl(u);
      }
    }

    return true;
  } catch {
    return false;
  }
}

async function alignVersionsIfNeeded() {
  try {
    const cfg = getConfig();
    const chatId = cfg.chatId || "";
    if (!chatId) return;

    // 1) Repair versuchen
    const okRepair = await runRepair(100);
    if (okRepair) return;

    // 2) Fallback: ensure
    const ensureUrl = `/v1/chats/${encodeURIComponent(chatId)}/vectoplan/ensure`;
    const resp = await fetch(ensureUrl, { method: "POST", credentials: "same-origin", cache: "no-store" }).catch(() => null);
    if (resp && resp.ok) await refreshVersionsUI();
  } catch {}
}

function wireMaintenanceButtons() {
  const repairBtn = $("repairBtn");
  if (repairBtn && !repairBtn._wired) {
    repairBtn._wired = true;
    repairBtn.addEventListener("click", async () => {
      repairBtn.disabled = true;
      try { await runRepair(200); } finally { repairBtn.disabled = false; }
    });
  }

  const alignBtn = $("alignBtn");
  if (alignBtn && !alignBtn._wired) {
    alignBtn._wired = true;
    alignBtn.addEventListener("click", async () => {
      alignBtn.disabled = true;
      try { await alignVersionsIfNeeded(); } finally { alignBtn.disabled = false; }
    });
  }
}

/* ───────────────────────── Sonstige Helpers ───────────────────────── */

function initViewerFromServerHint() {
  try {
    const initUrl = (window.INIT_VIEWER_URL || "").trim();
    if (initUrl && (uiState.viewerMode === "3d" || !uiState.viewerMode)) {
      showViewer(initUrl);
      const a = $("viewerOpenRawBtn");
      if (a) a.href = uiState.rawViewerUrl || "#";
    }
  } catch {}
}

function wire2dEventBridge() {
  if (window._cad2dBridgeWired) return;
  window._cad2dBridgeWired = true;

  const cfg = getConfig();

  async function putState(patch) {
    try {
      if (!cfg.statePutPath || cfg.statePutPath === "__DISABLED__") return;
      await fetch(cfg.statePutPath, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify(patch || {}),
      }).catch(() => {});
    } catch {}
  }

  window.addEventListener("cad2d-select", (ev) => {
    try {
      const items = ev?.detail?.items || [];
      uiState.last2DSelection = items;
      void putState({ last_2d_selection: items, last_2d_selection_ts: Date.now() });
    } catch {}
  });

  window.addEventListener("cad2d-hover", (ev) => {
    try { uiState.last2DHover = ev?.detail?.item || null; } catch {}
  });
}

function wireGlobalStatus() {
  try {
    const alertBox = $("systemAlert");
    if (!alertBox) return;

    const show = (msg) => {
      alertBox.textContent = msg || "";
      alertBox.classList.toggle("hidden", !msg);
    };

    window.addEventListener("offline", () => show("Offline erkannt. Vorgänge werden evtl. verzögert."));
    window.addEventListener("online", () => show(""));
  } catch {}
}

function wireHotkeys() {
  try {
    if (window.__VECTOAI_HOTKEYS_WIRED__) return;
    window.__VECTOAI_HOTKEYS_WIRED__ = true;

    document.addEventListener("keydown", (e) => {
      try {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
          const send = $("sendBtn");
          if (send) { e.preventDefault(); send.click(); }
        }
        if (e.altKey && (e.key === "l" || e.key === "L")) {
          e.preventDefault();
          void selectLatestModel();
        }
        // ESC schließt Versionen-Dropdown (layout.js nutzt ESC fürs Drawer)
        if (e.key === "Escape") {
          versionsClose();
        }
      } catch {}
    });
  } catch {}
}

/* ───────────────────────── Boot ───────────────────────── */

async function boot() {
  if (window.__VECTOAI_BOOTED__) return;
  window.__VECTOAI_BOOTED__ = true;

  // Layout init (idempotent; layout.js auto-init existiert ebenfalls)
  safeCall("initChatLayout", () => initChatLayout({ breakpointPx: 900 }));

  safeCall("wireThemeToggle", wireThemeToggle);
  safeCall("wireGlobalStatus", wireGlobalStatus);
  safeCall("wireHotkeys", wireHotkeys);

  safeCall("wireComposer", wireComposer);
  safeCall("wireViewerToolbar", wireViewerToolbar);

  // versions.js kann bei Template-Änderungen brechen → isoliert
  safeCall("wireVersionsToolbar", wireVersionsToolbar);

  safeCall("wireTranscriptRefresh", wireTranscriptRefresh);
  safeCall("wire2dEventBridge", wire2dEventBridge);
  safeCall("wireMaintenanceButtons", wireMaintenanceButtons);

  await safeAwait("loadTemplatesIndex", loadTemplatesIndex);
  await safeAwait("loadTranscript", loadTranscript);

  await safeAwait("initViewerModeFromServer", initViewerModeFromServer);
  safeCall("initViewerFromServerHint", initViewerFromServerHint);
  await safeAwait("ensureInitialViewer", ensureInitialViewer);

  await safeAwait("initViewerCtx", initViewerCtx);
  await safeAwait("applySavedSelection", applySavedSelection);

  // Dropdown + Version-Selection + Extra Modes
  safeCall("wireVersionsDropdown", wireVersionsDropdown);
  safeCall("wireVersionSelection", wireVersionSelection);
  safeCall("wireExtraViewerModes", wireExtraViewerModes);

  // Soft-Alignment beim Start
  await safeAwait("alignVersionsIfNeeded", alignVersionsIfNeeded);

  // Wenn Dropdown offen ist: initial refresh
  safeCall("initialVersionsRefreshIfOpen", () => {
    const box = getVersionsContainer();
    if (box && !isHidden(box)) { void refreshVersionsUI(); }
  });
}

// Globales Refresh-Signal (zusätzlicher Guard; transcript.js bindet ebenfalls)
safeCall("wireGlobalRefreshOnce", () => {
  if (window.__VECTOAI_REFRESH_WIRED__) return;
  window.__VECTOAI_REFRESH_WIRED__ = true;
  window.addEventListener("chat:refresh", () => { void refreshTranscriptIncremental(); });
});

// Start
void boot();