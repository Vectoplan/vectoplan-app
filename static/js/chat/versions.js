// services/app/static/js/chat/versions.js
// Versionen-UI: laden, rendern, löschen, DXF direkt öffnen.
// Robust: defensive DOM-Checks, try/catch, Abbruch laufender Requests, Cache-bust für lokale Viewer-Seiten.
// Kompatibel zu:
// - alt:  #versionsPanel (Bottom-Panel)
// - neu:  #versionsDropdown (Dropdown/Popover unter Toolbar)
//
// Zuständigkeit:
// - Öffnen/Schließen (Dropdown) wird primär in main.js orchestriert.
// - Dieses Modul fokussiert auf Rendern/Actions/Refresh und bietet Toggle-Fallback nur für alte Templates.

import { $, fetchJSON, safeHtml } from "./core.js";
import { getConfig, deleteVersion } from "./api.js";
import { showViewer, switchTo2D } from "./viewer.js";

/* ───────────────────────── Modulzustand ───────────────────────── */

let currentFilterKind = null;              // optionaler Filter, z.B. "BPA_DXF"
let _loadAbort = null;                     // AbortController für loadVersions
let _lastLoadSeq = 0;                      // Sequenz zur Race-Vermeidung

/* ───────────────────────── Safe helpers ───────────────────────── */

function safeCall(fn, fallback) {
  try { return fn(); } catch { return fallback; }
}
function safeStr(x, d = "") {
  try { return String(x ?? d); } catch { return d; }
}
function nowTs() {
  try { return Date.now(); } catch { return 0; }
}

/* ───────────────────────── Container (Dropdown/Panel) ───────────────────────── */

function getVersionsContainer() {
  return $("versionsDropdown") || $("versionsPanel") || null;
}

function isContainerVisible(el) {
  return safeCall(() => {
    if (!el) return false;
    if (el.classList.contains("hidden")) return false;
    if (String(el.getAttribute("aria-hidden") || "") === "true") return false;
    return true;
  }, false);
}

function setContainerVisible(show) {
  safeCall(() => {
    const box = getVersionsContainer();
    if (!box) return;

    box.classList.toggle("hidden", !show);
    box.setAttribute("aria-hidden", String(!show));

    const btn = $("versionsToggleBtn");
    if (btn) {
      btn.setAttribute("aria-expanded", String(show));
      if (box.id) btn.setAttribute("aria-controls", box.id);
    }
  }, undefined);
}

/* ───────────────────────── Local-only Helpers ───────────────────────── */

function isLocalPath(u) {
  return safeCall(() => {
    if (!u) return false;
    const s = String(u).trim();
    if (!s.startsWith("/")) return false;      // same-origin only
    if (s.startsWith("//")) return false;      // schema-relative blocken
    if (/^https?:\/\//i.test(s)) return false; // absolute blocken
    if (/[\r\n]/.test(s)) return false;
    return true;
  }, false);
}

function isLocalDxf(u) {
  return safeCall(() => {
    if (!isLocalPath(u)) return false;
    const path = String(u).split("?")[0].toLowerCase();
    return path.endsWith(".dxf");
  }, false);
}

/** Cache-bust für lokale Seiten (z.B. /ui/chat/.../cad2d) */
function cacheBustLocalUrl(u) {
  return safeCall(() => {
    const s = String(u || "").trim();
    if (!s) return s;
    if (!isLocalPath(s)) return s;

    const url = new URL(s, location.origin);
    url.searchParams.set("r", String(nowTs()));
    return url.pathname + "?" + url.searchParams.toString() + (url.hash || "");
  }, u);
}

/** Robuste Extraktion von Speckle-IDs aus verschiedenen Feldern. */
function extractSpeckle(it) {
  return safeCall(() => {
    const meta = it?.meta || {};
    const sp  = it?.speckle || meta?.speckle || {};

    const pid = sp.project_id || it.speckle_project_id || meta.speckle_project_id || it.project_id || "";
    const mid = sp.model_id   || it.speckle_model_id   || meta.speckle_model_id   || it.model_id   || "";
    const vid = sp.version_id || sp.commit_id ||
                it.speckle_version_id || meta.speckle_version_id ||
                it.version_id || it.commit_id || it.rev || it.id || "";

    return {
      project_id: String(pid || ""),
      model_id:   String(mid || ""),
      version_id: String(vid || ""),
    };
  }, { project_id: "", model_id: "", version_id: "" });
}

/* ───────────────────────── Public: Toggle + Filter ───────────────────────── */

/**
 * Fallback-Toggle für alte Templates.
 * Im neuen Layout (Dropdown) wird Toggle typischerweise durch main.js gehandhabt.
 */
export function toggleVersionsPanel(force) {
  safeCall(() => {
    const box = getVersionsContainer();
    if (!box) return;

    const show = typeof force === "boolean" ? force : !isContainerVisible(box);
    setContainerVisible(show);

    if (show) void loadVersions(); // nutzt default Filter (currentFilterKind)
  }, undefined);
}

export function setVersionsFilterKind(kind = null) {
  safeCall(() => {
    currentFilterKind = kind ? String(kind) : null;
    const box = getVersionsContainer();
    if (box && isContainerVisible(box)) {
      void loadVersions(); // nutzt default Filter
    }
  }, undefined);
}

/* ───────────────────────── Render ───────────────────────── */

function renderEmpty() {
  safeCall(() => {
    const ul = $("versionsList");
    const cnt = $("versCount");
    if (cnt) cnt.textContent = "0";
    if (ul) ul.innerHTML = "";
  }, undefined);
}

function isDXFItem(it) {
  return safeCall(() => {
    const k = String(it?.kind || "").toUpperCase();
    if (k.includes("DXF")) return true;
    const meta = it?.meta || {};
    const fname = String(meta?.filename || it?.label || "").toLowerCase();
    return fname.endsWith(".dxf");
  }, false);
}

function readableDate(v) {
  return safeCall(() => {
    const d = v ? new Date(v) : null;
    return d && !isNaN(d) ? d.toLocaleString() : "";
  }, "");
}

function computeDxfUrl(it) {
  return safeCall(() => {
    const cfg = getConfig();
    const meta = it?.meta || {};
    let u = "";

    if (meta.dxf_url) u = meta.dxf_url;
    else if (meta.url && String(meta.url).toLowerCase().endsWith(".dxf")) u = meta.url;
    else {
      const blobId = it?.input_blob_id || it?.blob_id;
      if (blobId && cfg.chatId) {
        u = `/ui/chat/${encodeURIComponent(cfg.chatId)}/file/${encodeURIComponent(blobId)}.dxf`;
      }
    }

    return isLocalDxf(u) ? u : "";
  }, "");
}

function versionIdOf(it) {
  return safeCall(() => it?.version_id || it?.id || it?.rev || null, null);
}

function renderItem(it) {
  const li = document.createElement("li");
  li.className = "vers-item";

  // Speckle IDs als data-Attribute
  const sp = extractSpeckle(it);
  safeCall(() => {
    if (sp.project_id) li.setAttribute("data-project-id", sp.project_id);
    if (sp.model_id)   li.setAttribute("data-model-id",   sp.model_id);
    if (sp.version_id) li.setAttribute("data-commit-id",  sp.version_id);
  }, undefined);

  // Left block
  const left = document.createElement("div");

  const title = document.createElement("div");
  title.textContent = it?.label || `${it?.kind || "BGA"} v${it?.version_idx ?? ""}`;

  const meta = document.createElement("div");
  meta.className = "vers-meta";
  const created = readableDate(it?.created_at || it?.created || it?.ts);
  meta.innerHTML =
    `<span class="pill">${safeHtml(it?.kind || "-")}</span>` +
    ` · v${safeHtml(it?.version_idx ?? "-")}` +
    (created ? ` · ${safeHtml(created)}` : "");

  left.append(title, meta);

  // Right actions
  const right = document.createElement("div");
  right.className = "vers-actions";

  // „Version auswählen“ (main.js delegiert)
  const selectBtn = document.createElement("button");
  selectBtn.className = "btn";
  selectBtn.type = "button";
  selectBtn.setAttribute("data-action", "select-version");
  selectBtn.textContent = "Version auswählen";
  selectBtn.title = "Diese Version im Viewer öffnen";

  selectBtn.setAttribute("data-project-id", sp.project_id || "");
  selectBtn.setAttribute("data-model-id",   sp.model_id   || "");
  selectBtn.setAttribute("data-commit-id",  sp.version_id || "");

  const selectable = !!(sp.project_id && sp.model_id && sp.version_id);
  selectBtn.disabled = !selectable;
  selectBtn._disabledOrig = !selectable;
  if (!selectable) selectBtn.title = "IDs fehlen (noch nicht bereit)";
  right.append(selectBtn);

  // DXF -> 2D öffnen
  if (isDXFItem(it)) {
    const open2d = document.createElement("button");
    open2d.className = "btn";
    open2d.type = "button";
    open2d.title = "Diesen DXF-Plan im 2D-Viewer öffnen";
    open2d.textContent = "📐 Öffnen";

    open2d.addEventListener("click", async () => {
      open2d.disabled = true;
      try {
        const cfg = getConfig();
        const dxfUrl = computeDxfUrl(it);
        if (!dxfUrl) return;

        // Setzt Mode + Persistenz (lädt ggf. CAD-Embed). Danach überschreiben wir bewusst mit lokaler cad2d-Seite.
        // (Später kann das in viewer.js als "local 2D page" sauberer konsolidiert werden.)
        try { await switchTo2D(); } catch {}

        const base = cfg.cad2dPagePath || (cfg.chatId ? `/ui/chat/${encodeURIComponent(cfg.chatId)}/cad2d` : "");
        if (!base) return;

        let page = `${base}?file=${encodeURIComponent(dxfUrl)}`;
        page = cacheBustLocalUrl(page);

        // local page -> cacheBust/force hilfreich gegen "alte" Templates/JS im iframe
        try { showViewer(page, { cacheBust: true, force: true }); }
        catch { showViewer(page); }

        // "Öffnen" Link zeigt die DXF-Datei (lokal)
        const a = $("viewerOpenRawBtn");
        if (a) a.href = dxfUrl;
      } catch {}
      finally { open2d.disabled = false; }
    });

    right.append(open2d);

    // DXF im neuen Tab
    const ext = document.createElement("a");
    ext.className = "btn btn-link";
    ext.target = "_blank";
    ext.rel = "noopener";
    ext.title = "DXF im neuen Tab öffnen";
    ext.textContent = "🔗";
    const local = computeDxfUrl(it);
    ext.href = local || "#";
    right.append(ext);
  }

  // Löschen
  const del = document.createElement("button");
  del.className = "btn delete";
  del.type = "button";
  del.setAttribute("data-action", "delete-version");
  del.title = "Version löschen";
  del.textContent = "🗑";

  del.addEventListener("click", async () => {
    del.disabled = true;
    try {
      const id = versionIdOf(it);
      if (!id) return;
      const ok = await removeVersion(id);
      if (ok) await loadVersions(); // refresh list
    } finally {
      del.disabled = false;
    }
  });

  right.append(del);

  li.append(left, right);
  return li;
}

/** Nach dem Rendern: „Version auswählen“-Buttons aktivieren, wenn IDs komplett sind. */
function enableSelectButtons() {
  safeCall(() => {
    const ul = $("versionsList");
    if (!ul) return;

    ul.querySelectorAll("li.vers-item").forEach(li => {
      try {
        const pid = li.getAttribute("data-project-id") || "";
        const mid = li.getAttribute("data-model-id") || "";
        const vid = li.getAttribute("data-commit-id") || "";
        const btn = li.querySelector('[data-action="select-version"]');
        if (!btn) return;

        const ok = !!(pid && mid && vid);
        btn.disabled = !ok;
        btn._disabledOrig = !ok;
        btn.title = ok ? "Diese Version im Viewer öffnen" : "IDs fehlen (noch nicht bereit)";

        btn.setAttribute("data-project-id", pid);
        btn.setAttribute("data-model-id", mid);
        btn.setAttribute("data-commit-id", vid);
      } catch {}
    });
  }, undefined);
}

function renderList(items) {
  safeCall(() => {
    const ul = $("versionsList");
    const cnt = $("versCount");
    if (!ul) return;

    ul.innerHTML = "";
    if (cnt) cnt.textContent = String(items.length);

    items.forEach((it) => {
      try { ul.append(renderItem(it)); } catch {}
    });

    enableSelectButtons();

    // Optionaler Hook für andere Module
    try {
      window.dispatchEvent(new CustomEvent("versions:loaded", { detail: { count: items.length } }));
    } catch {}
  }, undefined);
}

/* ───────────────────────── Data ───────────────────────── */

export async function loadVersions({ kind } = {}) {
  const seq = ++_lastLoadSeq;

  // Abort previous request
  safeCall(() => {
    if (_loadAbort) _loadAbort.abort();
  }, undefined);
  _loadAbort = safeCall(() => new AbortController(), null);

  try {
    const cfg = getConfig();

    const effectiveKind = (kind === undefined)
      ? currentFilterKind
      : (kind ? String(kind) : null);

    const base = cfg.versionsPath;
    const url = effectiveKind ? `${base}?kind=${encodeURIComponent(effectiveKind)}` : base;

    const r = await fetchJSON(url, {
      cache: "no-store",
      credentials: "same-origin",
      signal: _loadAbort?.signal,
      timeoutMs: 20000,
    });

    // Falls ein neuerer Request gestartet wurde, Ergebnis ignorieren
    if (seq !== _lastLoadSeq) return;

    if (!r?.ok) { renderEmpty(); return; }

    const items = Array.isArray(r.json?.items) ? r.json.items : [];
    renderList(items);
  } catch {
    // Abort darf still sein
    if (seq !== _lastLoadSeq) return;
    renderEmpty();
  }
}

async function removeVersion(versionId) {
  try {
    if (!versionId) return false;
    const r = await deleteVersion(versionId);
    return !!r?.ok;
  } catch { return false; }
}

/* ───────────────────────── Wiring ───────────────────────── */

export function wireVersionsToolbar() {
  // Toggle nur für alte Templates ohne Dropdown
  safeCall(() => {
    const toggle = $("versionsToggleBtn");
    const hasDropdown = !!$("versionsDropdown");
    const hasPanel = !!$("versionsPanel");

    if (toggle && !toggle._wired && hasPanel && !hasDropdown) {
      toggle._wired = true;
      toggle.addEventListener("click", () => toggleVersionsPanel());
    }

    const box = getVersionsContainer();
    if (toggle && box?.id) toggle.setAttribute("aria-controls", box.id);
  }, undefined);

  safeCall(() => {
    const refresh = $("versRefreshBtn");
    if (refresh && !refresh._wired) {
      refresh._wired = true;
      refresh.addEventListener("click", () => loadVersions());
    }
  }, undefined);

  // Externe Trigger erlauben
  safeCall(() => {
    if (!window.__VERSIONS_EVENTS_WIRED__) {
      window.__VERSIONS_EVENTS_WIRED__ = true;

      window.addEventListener("versions:refresh", () => { void loadVersions(); });
      window.addEventListener("upload:done", () => { void loadVersions(); });
    }
  }, undefined);
}