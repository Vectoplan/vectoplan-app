// services/app/static/js/chat/cards.js
// Karten/Renderer + Template-Registry + Formularaktionen.
// Robustheitsziele:
// - Templates Index mit best-effort Cache (sessionStorage/localStorage) gegen temporäre API-Ausfälle
// - Defensive DOM- und Netzwerk-Guards (try/catch)
// - Projektinformationen sind bewusst entfernt/deaktiviert (Legacy-Cards werden read-only dargestellt)
// - Nach state-/message-Aktionen: window.dispatchEvent(new CustomEvent('chat:refresh'))

import { $, fetchJSON, uiState, safeHtml } from "./core.js";
import { getConfig } from "./api.js";

/* ───────────────────────── Templates Index (mit Cache) ───────────────────────── */

const TPL_CACHE_KEY = "vectoai.templatesIndex.v1";

/** Best-effort Cache lesen (erst sessionStorage, dann localStorage). */
function _readTplCache() {
  try {
    const s = sessionStorage.getItem(TPL_CACHE_KEY);
    if (s) return JSON.parse(s);
  } catch {}
  try {
    const s = localStorage.getItem(TPL_CACHE_KEY);
    if (s) return JSON.parse(s);
  } catch {}
  return null;
}

/** Best-effort Cache schreiben (erst sessionStorage, dann localStorage). */
function _writeTplCache(obj) {
  try { sessionStorage.setItem(TPL_CACHE_KEY, JSON.stringify(obj || {})); } catch {}
  try { localStorage.setItem(TPL_CACHE_KEY, JSON.stringify(obj || {})); } catch {}
}

/** Lädt /v1/templates, baut Index key->meta. Fallback auf Cache bei Fehlern. */
export async function loadTemplatesIndex() {
  const cfg = getConfig();

  // 1) Sofort cache anwenden (falls vorhanden), damit UI nicht leer startet
  try {
    const cached = _readTplCache();
    if (cached && typeof cached === "object") {
      uiState.templatesIndex = cached;
    } else if (!uiState.templatesIndex || typeof uiState.templatesIndex !== "object") {
      uiState.templatesIndex = {};
    }
  } catch {
    uiState.templatesIndex = uiState.templatesIndex || {};
  }

  // 2) Dann online refresh versuchen
  try {
    if (!cfg.templatesPath) return;

    const r = await fetchJSON(cfg.templatesPath, {
      cache: "no-store",
      credentials: "same-origin",
      timeoutMs: 20000,
    });

    if (!r.ok) return;

    const items = r.json?.items || [];
    const idx = {};
    for (const t of items) {
      try {
        const k = String(t.key || "");
        if (!k) continue;
        idx[k] = t;
      } catch {}
    }

    uiState.templatesIndex = idx;
    _writeTplCache(idx);
  } catch {
    // bei Fehlern: cache bleibt aktiv
  }
}

/* ───────────────────────── Helpers ───────────────────────── */

function _emitRefresh() {
  try { window.dispatchEvent(new CustomEvent("chat:refresh")); } catch {}
}

async function _headStateEtag() {
  try {
    const cfg = getConfig();
    if (!cfg.stateGetPath) return;

    const r = await fetchJSON(cfg.stateGetPath, {
      method: "HEAD",
      cache: "no-store",
      credentials: "same-origin",
      timeoutMs: 12000,
    });

    if (r?.etag) uiState.stateEtag = r.etag;
  } catch {}
}

async function _putStatePatch(patch) {
  try {
    const cfg = getConfig();
    if (!cfg.statePutPath) return false;

    await _headStateEtag();

    // 1) Patch-Body (präferiert)
    let r = await fetchJSON(cfg.statePutPath, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...(uiState.stateEtag ? { "If-Match": uiState.stateEtag } : {}) },
      body: JSON.stringify({ patch }),
      cache: "no-store",
      credentials: "same-origin",
      timeoutMs: 20000,
    });

    // 412 -> retry ohne If-Match
    if (!r.ok && r.status === 412) {
      r = await fetchJSON(cfg.statePutPath, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch }),
        cache: "no-store",
        credentials: "same-origin",
        timeoutMs: 20000,
      });
    }

    // 2) Legacy-Fallback: manche Server akzeptieren nur { ... } (ohne patch)
    if (!r.ok && (r.status === 400 || r.status === 415 || r.status === 422)) {
      r = await fetchJSON(cfg.statePutPath, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch || {}),
        cache: "no-store",
        credentials: "same-origin",
        timeoutMs: 20000,
      });
    }

    return !!r.ok;
  } catch {
    return false;
  }
}

export async function postTemplate(template_key, payload = {}) {
  try {
    const cfg = getConfig();
    if (!cfg.postMessagePath) return false;

    const r = await fetchJSON(cfg.postMessagePath, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_key, payload, role: "service", trace: ["UI"] }),
      cache: "no-store",
      credentials: "same-origin",
      timeoutMs: 25000,
    });

    return !!r.ok;
  } catch {
    return false;
  }
}

/* ───────────────────────── Card-Renderer ───────────────────────── */

function _jsonSafe(v) {
  try { return JSON.stringify(v); } catch { return String(v); }
}

function _mkBtn(label, title) {
  const b = document.createElement("button");
  b.className = "btn";
  b.type = "button";
  b.textContent = label;
  if (title) b.title = title;
  return b;
}

function _mkLink(label, href, title) {
  const a = document.createElement("a");
  a.className = "btn btn-link";
  a.textContent = label;
  a.href = href || "#";
  a.target = "_blank";
  a.rel = "noopener";
  if (title) a.title = title;
  return a;
}

/**
 * renderCard(container, msg)
 * Erwartet msg.meta: { type:"card", template:"...", payload:{...} }
 */
export function renderCard(container, msg) {
  try {
    if (!container || !msg) return;

    const meta = msg.meta || {};
    const tkey = String(meta.template || "");
    const payload = meta.payload || {};

    const wrap = document.createElement("div");
    wrap.className = "card card-" + safeHtml(tkey || "generic");
    wrap.dataset.msgId = msg.id || "";

    // Header
    const head = document.createElement("div");
    head.className = "card-head";
    const title = (uiState.templatesIndex?.[tkey]?.title || tkey || "Karte");
    head.innerHTML = `<strong>${safeHtml(title)}</strong>`;
    wrap.append(head);

    // Body
    const body = document.createElement("div");
    body.className = "card-body";

    const addRow = (label, value) => {
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<span class="lbl">${safeHtml(label)}:</span> <span class="val">${safeHtml(value)}</span>`;
      body.append(row);
    };

    // Actions
    const actions = document.createElement("div");
    actions.className = "card-actions";

    /* ───────────────────────── Template-Key spezifisch ───────────────────────── */

    // Projektinfo ist entfernt → Legacy/alte Keys werden read-only dargestellt
    if (tkey === "project_info_form" || tkey === "project_info_summary") {
      const p = document.createElement("p");
      p.innerHTML = `<strong>Hinweis:</strong> Projektinformationen wurden deaktiviert.`;
      body.append(p);

      // payload trotzdem anzeigen (read-only)
      for (const [k, v] of Object.entries(payload || {})) {
        addRow(k, typeof v === "string" ? v : _jsonSafe(v));
      }
    }

    // Willkommen / Start (ohne Projektinfo-Button)
    else if (tkey === "project_welcome") {
      const p = document.createElement("p");
      p.textContent = (payload.hint || "Dies ist eine offene Alpha-Testversion von Vectoplan. Alle Systeme können kostenlos genutzt werden. Hier bauen testen wir neue System zur BigData-Auswertung und automatischen Gebäudegenerierung");
      body.append(p);

      if (payload.wfs_url) {
        actions.append(_mkLink("WFS öffnen", payload.wfs_url, "WFS im neuen Tab öffnen"));
      }

      const confirm = _mkBtn("Alles klar :)", "Grundstücksauswahl bestätigen");
      confirm.onclick = async () => {
        confirm.disabled = true;
        try {
          const ok = await _putStatePatch({ project: { parcels_confirmed: true, confirmed_at: new Date().toISOString() } });
          if (ok) {
            await postTemplate("info_card", { title: "Projektstart", md: "Grundstücksauswahl bestätigt." });
            _emitRefresh();
          }
        } finally {
          confirm.disabled = false;
        }
      };
      actions.append(confirm);
    }

    // „Fehlende Angaben“ (bbox/address/ifc) – bleibt aktiv
    else if (tkey === "missing_slots") {
      const missing = Array.isArray(payload.missing) ? payload.missing : [];
      const list = document.createElement("ul");
      missing.forEach((k) => {
        const li = document.createElement("li");
        li.textContent = String(k);
        list.append(li);
      });
      body.append(list);

      const form = document.createElement("div");
      form.className = "form";

      const inBBox = document.createElement("input"); inBBox.placeholder = "bbox (minx,miny,maxx,maxy)";
      const inAddr = document.createElement("input"); inAddr.placeholder = "address";
      const inIfc  = document.createElement("input"); inIfc.placeholder = "ifc id/ref";

      if (missing.includes("bbox")) form.append(inBBox);
      if (missing.includes("address")) form.append(inAddr);
      if (missing.includes("ifc")) form.append(inIfc);

      body.append(form);

      const send = _mkBtn("Angaben senden", "Fehlende Angaben speichern");
      send.onclick = async () => {
        send.disabled = true;
        try {
          const patch = {};
          if (inBBox.value) patch["bbox"] = inBBox.value;
          if (inAddr.value) patch["address"] = inAddr.value;
          if (inIfc.value)  patch["ifc"] = inIfc.value;

          if (Object.keys(patch).length) {
            const ok = await _putStatePatch(patch);
            if (ok) {
              await postTemplate("info_card", { title: "Daten ergänzt", md: "Benutzer hat fehlende Angaben ergänzt." });
              _emitRefresh();
            }
          }
        } finally {
          send.disabled = false;
        }
      };
      actions.append(send);
    }

    // Speckle Viewer Card
    else if (tkey === "speckle_viewer") {
      const url = payload.url || uiState.rawViewerUrl || "";
      const p = document.createElement("p");
      p.textContent = payload.caption || "3D-Ansicht öffnen.";
      body.append(p);

      const a = _mkLink("Im Viewer öffnen", url || "#", "3D-Viewer im neuen Tab öffnen");
      actions.append(a);
    }

    // Download Card
    else if (tkey === "download_card") {
      const p = document.createElement("p");
      p.textContent = payload.label || "Download";
      body.append(p);

      const a = _mkLink("Herunterladen", payload.href || "#", "Download im neuen Tab öffnen");
      a.className = "btn"; // bewusst als primärer Button
      actions.append(a);
    }

    // Error Card
    else if (tkey === "error_card") {
      const p = document.createElement("p");
      p.innerHTML = `<strong>Fehler:</strong> ${safeHtml(payload.md || payload.message || "")}`;
      body.append(p);
    }

    // Generic fallback
    else {
      // Wenn payload "md" enthält: als Text anzeigen (kein Markdown-Rendering hier)
      if (typeof payload?.md === "string" && payload.md.trim()) {
        const p = document.createElement("p");
        p.textContent = payload.md;
        body.append(p);
      }

      for (const [k, v] of Object.entries(payload || {})) {
        addRow(k, typeof v === "string" ? v : _jsonSafe(v));
      }
    }

    wrap.append(body, actions);
    container.append(wrap);
  } catch (e) {
    try {
      const p = document.createElement("p");
      p.textContent = `[Card-Render-Fehler: ${e?.message || e}]`;
      container?.append(p);
    } catch {}
  }
}