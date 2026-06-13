// services/app/static/js/chat/composer.js
// Composer: Nachricht schreiben, Dateien auswählen/drag&drop, Preview, Upload, Senden.
// Robustheitsziele:
// - Idempotentes Wiring (kein Doppel-Binding)
// - Abbruch laufender Streams bei neuem Senden (AbortController)
// - Stabiler Busy-State (UI konsistent sperren/entsperren)
// - Defensive Upload-Pipeline (DXF via UI-Upload, Rest via /v1/files)
// - Viewer/Polling nur dort beeinflussen, wo sinnvoll (Admin/LV nicht ungefragt überschreiben)
// - Best-effort Cache/try-catch überall an I/O Grenzen

import { $, setStatus, uiState } from "./core.js";
import { uploadFiles, streamChat, getConfig } from "./api.js";
import {
  appendMessage,
  appendAssistantThinking,
  updateAssistantThinking,
  finalizeAssistantThinking
} from "./render.js";
import { refreshTranscriptIncremental } from "./transcript.js";
import {
  showViewer,
  refreshViewerFromServer,
  startViewerPolling,
  stopViewerPolling,
  switchTo2D,
} from "./viewer.js";
import { loadVersions } from "./versions.js";

/* ───────────────────────── Debug ───────────────────────── */

const _log  = (...a) => { try { console.info("[composer]", ...a); } catch {} };
const _warn = (...a) => { try { console.warn("[composer]", ...a); } catch {} };

/* ───────────────────────── Interner Zustand ───────────────────────── */

const selected = [];            // [{ file, name, type, size, preview }]
let lastAttachIds = [];         // zuletzt gesendete Blob-IDs (für "Angaben senden")

let currentThinkId = null;      // Thinking bubble id (render.js)
let _streamCtrl = null;         // AbortController für streamChat
let _sending = false;           // Guard gegen Parallel-Sends

/* ───────────────────────── Helpers ───────────────────────── */

function extOf(name = "") {
  try { return (name.lastIndexOf(".") >= 0 ? name.slice(name.lastIndexOf(".")).toLowerCase() : ""); }
  catch { return ""; }
}
function isCAD2D(f) { const e = extOf(f?.name || ""); return e === ".dxf"; }
function isCAD3D(f) { const e = extOf(f?.name || ""); return e === ".ifc" || e === ".obj" || e === ".stl"; }
function isCADAny(f) { return isCAD2D(f) || isCAD3D(f); }

function setComposerBusy(on) {
  try {
    const form = $("composer");
    const ta = $("message");
    const send = $("sendBtn");
    const attach = $("attachBtn");

    form?.classList?.toggle("busy", !!on);

    if (ta) ta.disabled = !!on;
    if (send) send.disabled = !!on;
    if (attach) attach.disabled = !!on;

    // optional: ARIA busy
    if (form) form.setAttribute("aria-busy", String(!!on));
  } catch {}
}

function showUploadProgress(show) {
  try {
    const p = $("uploadProgress");
    if (!p) return;
    p.classList.toggle("hidden", !show);
    p.setAttribute("aria-hidden", String(!show));
    // Da fetch keine echten Progress-Events liefert: best-effort "Indeterminate"
    const bar = p.querySelector("div");
    if (bar) bar.style.width = show ? "35%" : "0%";
  } catch {}
}

function abortActiveStream(reason = "aborted") {
  try {
    if (_streamCtrl) {
      _streamCtrl.abort(reason);
      _streamCtrl = null;
    }
  } catch {
    _streamCtrl = null;
  }
}

/** Aufräumen: ObjectURLs revoken + Auswahl leeren */
function clearSelectedAndRevoke() {
  try {
    selected.forEach(s => { try { if (s?.preview) URL.revokeObjectURL(s.preview); } catch {} });
  } catch {}
  try { selected.splice(0); } catch {}
  try { renderPreview(); } catch {}
}

/* ───────────────────────── UI Upload (DXF) ───────────────────────── */

async function uiUploadCadFiles(files) {
  // POST /ui/chat/<id>/upload (multipart) mit Feld 'files' – NUR für DXF
  const cfg = getConfig();
  const url = cfg.uiUploadPath;

  if (!url) throw new Error("UI-Upload-Route fehlt (uiUploadPath)");

  const fd = new FormData();
  (files || []).forEach(f => { try { fd.append("files", f, f?.name || "file"); } catch {} });

  try {
    const res = await fetch(url, {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store",
    });

    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    return data || { status: "ok", items: [], errors: [] };
  } catch (e) {
    throw new Error(`UI-Upload fehlgeschlagen: ${e?.message || e}`);
  }
}

/* ───────────────────────── Preview ───────────────────────── */

function renderPreview() {
  const box = $("preview");
  if (!box) return;

  // alte Chips löschen
  try { [...box.querySelectorAll(".chip")].forEach(n => n.remove()); } catch {}

  selected.forEach((a, i) => {
    try {
      const chip = document.createElement("div");
      chip.className = "chip";

      if (a.preview && String(a.type || "").startsWith("image/")) {
        const img = document.createElement("img");
        img.src = a.preview;
        img.alt = a.name || "preview";
        img.loading = "lazy";
        img.style.maxHeight = "48px";
        img.style.maxWidth = "64px";
        chip.append(img);
      } else {
        const icon = document.createElement("div");
        const e = extOf(a.name);
        icon.textContent = (e === ".dxf" ? "📐" : (e === ".ifc" ? "🧊" : "📄"));
        chip.append(icon);
      }

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = a.name + (a.size ? ` (${(a.size/1048576).toFixed(1)} MB)` : "");

      const rm = document.createElement("button");
      rm.className = "remove";
      rm.type = "button";
      rm.textContent = "✕";
      rm.title = "Entfernen";

      rm.onclick = () => {
        try { if (a.preview) URL.revokeObjectURL(a.preview); } catch {}
        try { selected.splice(i, 1); } catch {}
        renderPreview();
      };

      chip.append(meta, rm);
      box.append(chip);
    } catch {}
  });
}

function addFiles(files) {
  try {
    const list = Array.from(files || []);
    if (!list.length) return;

    // Simple dedupe: name+size+lastModified
    const keyOf = (f) => `${f?.name || ""}::${f?.size || 0}::${f?.lastModified || 0}`;
    const existing = new Set(selected.map(s => keyOf(s.file)));

    list.forEach(f => {
      try {
        if (!f) return;
        const k = keyOf(f);
        if (existing.has(k)) return;

        const o = { file: f, type: f.type || "", name: f.name || "file", size: f.size || 0 };
        try { o.preview = URL.createObjectURL(f); } catch {}
        selected.push(o);
        existing.add(k);
      } catch {}
    });

    renderPreview();
    _log("addFiles", list.length);
  } catch {}
}

/** Preview-Zeile sofort leeren, ohne selected[] zu löschen. */
function clearPreviewBarImmediately() {
  try {
    const box = $("preview");
    if (!box) return;
    [...box.querySelectorAll(".chip")].forEach(n => n.remove());
  } catch {}
}

/* ───────────────────────── Drag & Drop ───────────────────────── */

function wireDropzone() {
  const dz = $("preview");   // Preview-Zeile als Drop-Ziel
  const fi = $("fileInput");
  if (!dz || !fi) return;

  if (dz._dropWired) return;
  dz._dropWired = true;

  const onFiles = (files) => { try { addFiles(files); } catch {} };

  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => {
    try { e.preventDefault(); } catch {}
    try { dz.classList.add("drag"); } catch {}
  }));

  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => {
    try { e.preventDefault(); } catch {}
    try { dz.classList.remove("drag"); } catch {}
  }));

  dz.addEventListener("drop", e => {
    try {
      const files = e.dataTransfer?.files;
      if (files && files.length) onFiles(files);
    } catch {}
  });

  // Optional: verhindern, dass Drop außerhalb die Seite navigiert
  if (!window.__VECTOAI_GLOBAL_DROP_GUARD__) {
    window.__VECTOAI_GLOBAL_DROP_GUARD__ = true;
    document.addEventListener("dragover", (e) => { try { e.preventDefault(); } catch {} }, { passive: false });
    document.addEventListener("drop", (e) => { try { e.preventDefault(); } catch {} }, { passive: false });
  }
}

/* ───────────────────────── Streaming Send ───────────────────────── */

function shouldAutoShowViewerUrlOnDone() {
  // Admin/LV nicht ungefragt überschreiben
  const mode = String(uiState.viewerMode || "");
  if (mode === "admin" || mode === "lv") return false;
  return true;
}

async function doStreamSend({ text, attachIds, defer_upload = true, include_attachments } = {}) {
  const cfg = getConfig();

  lastAttachIds = Array.isArray(attachIds) ? attachIds.slice() : [];
  const inc = typeof include_attachments === "boolean" ? include_attachments : (lastAttachIds.length > 0);

  // Abbrechen falls noch ein Stream läuft
  abortActiveStream("new_send");

  _streamCtrl = new AbortController();

  try {
    await streamChat(
      {
        chat_id: cfg.chatId,
        message: text || "",
        attachments: lastAttachIds,
        defer_upload,
        include_attachments: inc
      },
      async (ev) => {
        try {
          if (!ev || !ev.event) return;

          if (ev.event === "upload") {
            // Server meldet, dass Upload/Verarbeitung startet/abgeschlossen ist
            try { await loadVersions(); } catch {}
            // Nur im 3D-Modus sinnvoll (sonst würde refreshViewerFromServer ggf. Viewer überschreiben)
            if (String(uiState.viewerMode || "") === "3d") {
              try { await refreshViewerFromServer(); } catch {}
            }
          }

          else if (ev.event === "delta") {
            if (!currentThinkId) currentThinkId = appendAssistantThinking("Vectoplan denkt nach");
            updateAssistantThinking(currentThinkId, ev.text || "");
          }

          else if (ev.event === "done") {
            // finalize bubble
            try { finalizeAssistantThinking(currentThinkId, ev.assistant_msg || {}); } catch {}

            // viewer_url handling
            try {
              const vurl = ev.viewer_url;
              if (vurl) {
                if (shouldAutoShowViewerUrlOnDone()) {
                  showViewer(vurl);
                } else {
                  // Admin/LV: nur "Öffnen"-Link aktualisieren, Viewer im iframe bleibt
                  const a = $("viewerOpenRawBtn");
                  if (a) a.href = vurl;
                }
                try { stopViewerPolling(); } catch {}
              }
            } catch {}

            // Transcript nachziehen
            try { await refreshTranscriptIncremental(); } catch {}

            // Busy off
            setComposerBusy(false);
            showUploadProgress(false);
            setStatus("");

            currentThinkId = null;
            return;
          }

          else if (ev.event === "error") {
            // Optionaler Server-Event
            const msg = String(ev.message || ev.error || "Unbekannter Fehler");
            setStatus(`Fehler: ${msg}`, { ok: false });
          }

        } catch {}
      },
      { signal: _streamCtrl.signal, timeoutMs: 180000 }
    );
  } catch (e) {
    const isAbort = String(e?.message || e).toLowerCase().includes("abort");
    if (!isAbort) {
      setStatus(`Streaming-Fehler: ${e?.message || e}`, { ok: false });
    }
    // Thinking bubble entfernen/finalisieren best-effort
    try {
      if (currentThinkId) {
        finalizeAssistantThinking(currentThinkId, { text: "" });
        currentThinkId = null;
      }
    } catch {}
    throw e;
  } finally {
    _streamCtrl = null;
  }
}

/* ───────────────────────── Senden Handler ───────────────────────── */

async function handleSend(e) {
  try { e?.preventDefault?.(); } catch {}

  if (_sending) return;
  if ($("composer")?.classList?.contains("busy")) return;

  _sending = true;

  try {
    const cfg = getConfig();
    const ta = $("message");

    const text = (ta?.value || "").trim();
    const filesAll = selected.map(a => a.file).filter(Boolean);

    if (!text && filesAll.length === 0) return;

    // Partitionieren
    const dxfFiles   = filesAll.filter(isCAD2D);
    const meshFiles  = filesAll.filter(isCAD3D);
    const otherFiles = filesAll.filter(f => !isCADAny(f));

    const hasDXF = dxfFiles.length > 0;
    const has3D  = meshFiles.length > 0;

    _log("handleSend", { textLen: text.length, dxfFiles: dxfFiles.length, meshFiles: meshFiles.length, otherFiles: otherFiles.length });

    // Optimistische User-Bubble
    appendMessage({
      role: "user",
      text,
      pending: true,
      attachments: selected.map(a => ({ name: a.name, type: a.type, preview: a.preview, size: a.size }))
    });

    // UI sofort leeren
    if (ta) ta.value = "";
    try { $("fileInput").value = ""; } catch {}
    clearPreviewBarImmediately();
    setComposerBusy(true);
    showUploadProgress(true);
    setStatus("");

    // Upload IDs sammeln
    let idsDXF = [];
    let idsOthers = [];

    // DXF über UI-Upload
    if (hasDXF) {
      try {
        const r = await uiUploadCadFiles(dxfFiles);
        _log("uiUpload DXF result", r);

        const arr = Array.isArray(r?.items) ? r.items : (Array.isArray(r?.results) ? r.results : []);
        idsDXF = arr.map(it => it?.file_id || it?.id).filter(Boolean);

        // Nach DXF-Upload: 2D Modus aktivieren und Versionen neu laden
        try { await switchTo2D(); } catch {}
        try { await loadVersions(); } catch {}

      } catch (ex) {
        setStatus(`DXF-Upload fehlgeschlagen: ${ex?.message || ex}`, { ok: false });
      }
    }

    // Rest (IFC/OBJ/STL + sonstige) via /v1/files
    const nonDXF = [...meshFiles, ...otherFiles];
    if (nonDXF.length > 0) {
      try {
        const uploaded = await uploadFiles(nonDXF);
        idsOthers = (uploaded || []).map(x => x.file_id || x.id).filter(Boolean);
        _log("uploaded non-DXF", uploaded);
      } catch (ex) {
        setStatus(`Upload fehlgeschlagen: ${ex?.message || ex}`, { ok: false });
      }
    }

    // Nach Uploads: objectURLs freigeben + Auswahl leeren
    clearSelectedAndRevoke();

    const attachIds = [...idsDXF, ...idsOthers];

    // Thinking bubble
    currentThinkId = appendAssistantThinking("Vectoplan denkt nach");

    // 3D: Polling starten (wird in viewer.js nur bei viewerMode===3d aktiv)
    // Wichtig: wenn User gerade Admin/LV offen hat, startet Polling ohnehin nicht.
    if (has3D) {
      try { startViewerPolling(); } catch {}
    }

    // Stream senden
    await doStreamSend({ text, attachIds, defer_upload: true, include_attachments: attachIds.length > 0 });

  } catch (err) {
    const msg = String(err?.message || err || "Unbekannter Fehler");
    setStatus(`Fehler: ${msg}`, { ok: false });
    setComposerBusy(false);
    showUploadProgress(false);
  } finally {
    _sending = false;
  }
}

/* ───────────────────────── „Angaben fehlen“ erneut senden ───────────────────────── */

function wireSendAttachmentsAction() {
  try {
    if (window.__VECTOAI_SEND_ATTACHMENTS_WIRED__) return;
    window.__VECTOAI_SEND_ATTACHMENTS_WIRED__ = true;

    document.addEventListener("click", async (e) => {
      try {
        const t = e.target;
        if (!t || !t.matches) return;

        if (!t.matches('[data-action="send-attachments"]')) return;

        e.preventDefault();

        if (!lastAttachIds || lastAttachIds.length === 0) return;
        if ($("composer")?.classList?.contains("busy")) return;

        // Busy + thinking
        setComposerBusy(true);
        showUploadProgress(false);
        currentThinkId = appendAssistantThinking("Sende Anhänge …");

        try {
          // Polling best-effort (viewer.js no-op wenn nicht 3d)
          try { startViewerPolling(); } catch {}

          await doStreamSend({
            text: "",
            attachIds: lastAttachIds,
            defer_upload: false,
            include_attachments: true
          });
        } catch (ex) {
          setStatus(`Fehler beim erneuten Senden: ${ex?.message || ex}`, { ok: false });
          setComposerBusy(false);
        }
      } catch {}
    });
  } catch {}
}

/* ───────────────────────── Wiring ───────────────────────── */

function wireInputs() {
  // Büroklammer → Datei-Dialog
  const attachBtn = $("attachBtn");
  if (attachBtn && !attachBtn._wired) {
    attachBtn._wired = true;
    attachBtn.addEventListener("click", () => { try { $("fileInput")?.click(); } catch {} });
  }

  // Datei-Auswahl
  const fileInput = $("fileInput");
  if (fileInput && !fileInput._wired) {
    fileInput._wired = true;
    fileInput.addEventListener("change", (e) => {
      try { addFiles(e.target?.files || []); } catch {}
    });
  }

  // Enter → Senden
  const msg = $("message");
  if (msg && !msg._wired) {
    msg._wired = true;
    msg.addEventListener("keydown", (e) => {
      try {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          if (!$("composer")?.classList?.contains("busy")) $("sendBtn")?.click();
        }
      } catch {}
    });
  }

  // Formular Submit
  const form = $("composer");
  if (form && !form._wired) {
    form._wired = true;
    form.addEventListener("submit", handleSend);
  }

  // Drag & Drop
  wireDropzone();

  // „Angaben senden“-Action
  wireSendAttachmentsAction();

  // Best-effort: Stream abbrechen beim Verlassen der Seite
  if (!window.__VECTOAI_STREAM_ABORT_ON_UNLOAD__) {
    window.__VECTOAI_STREAM_ABORT_ON_UNLOAD__ = true;
    window.addEventListener("beforeunload", () => { abortActiveStream("unload"); });
    window.addEventListener("pagehide", () => { abortActiveStream("pagehide"); });
  }
}

/* ───────────────────────── Export ───────────────────────── */

export function wireComposer() {
  try {
    if (window.__VECTOAI_COMPOSER_WIRED__) return;
    window.__VECTOAI_COMPOSER_WIRED__ = true;
    wireInputs();
  } catch {}
}