// services/app/static/js/chat/transcript.js
// Transcript laden, inkrementell nachziehen, Nachrichten rendern.
// Robustheitsziele:
// - Deduping stabil (Set + optional prune)
// - Weniger Doppel-Wiring (nur einmalige Event-Registrierung)
// - Ghost/Thinking Cleanup defensiv und selektiv
// - Attachment-Mapping robust (base64 -> data:, content/download URLs)
// - Viewer-URL nur dann setzen/pollen, wenn sinnvoll (Mode-abhängig)

import { $, uiState } from "./core.js";
import { getChat, getConfig, fileContentUrl, fileDownloadUrl } from "./api.js";
import { appendMessage, removePending } from "./render.js";
import { renderCard, loadTemplatesIndex } from "./cards.js";
import { showViewer, refreshViewerFromServer, startViewerPolling } from "./viewer.js";

/* ───────────────────────── Helpers ───────────────────────── */

function _ensureState() {
  try {
    if (!uiState.renderedIds || !(uiState.renderedIds instanceof Set)) {
      uiState.renderedIds = new Set();
    }
  } catch {
    // no-op
  }
}

function _isStr(x){ return typeof x === "string"; }
function _isObj(x){ return x && typeof x === "object"; }

function _asDataUrlIfBase64(a){
  try{
    const mime = String(a?.mime || a?.type || "application/octet-stream");
    const b64 = String(a?.base64 || "");
    if (!b64) return null;
    if (b64.startsWith("data:")) return b64;
    if (/\s/.test(b64.slice(0, 64))) return null;
    return `data:${mime};base64,${b64}`;
  }catch{ return null; }
}

/** Prune Dedup-Set, damit es nicht unbegrenzt wächst. */
function _pruneRenderedIds(max = 4000) {
  try {
    _ensureState();
    const n = uiState.renderedIds.size;
    if (n <= max) return;

    // Set in Array (Insertion Order), dann älteste entfernen
    const arr = Array.from(uiState.renderedIds);
    const drop = Math.max(0, n - max);
    for (let i = 0; i < drop; i++) uiState.renderedIds.delete(arr[i]);
  } catch {}
}

/* ───────────────────────── Attachment-Mapping ───────────────────────── */

function mapAttachment(a){
  try {
    if (_isStr(a)) return { name: a };

    const fid = a?.file_id || a?.id || null;
    const mime = String(a?.mime || a?.type || "");
    const name = String(a?.filename || a?.name || "");

    const existingContent = a?.content_url || a?.url || null;
    const existingDownload = a?.download_url || null;

    const content = existingContent || (fid ? fileContentUrl(fid) : null);
    const download = existingDownload || (fid ? fileDownloadUrl(fid) : null);

    const dataUrl = _asDataUrlIfBase64(a);
    const preview = dataUrl || ((mime.startsWith("image/") || mime.startsWith("video/")) ? content : null);

    const out = {
      ...a,
      file_id: fid || undefined,
      name,
      filename: name || a?.filename,
      type: mime,
      mime,
      url: download || content || a?.url || undefined,
      content_url: content || undefined,
      download_url: download || undefined,
    };
    if (preview) out.preview = preview;

    return out;
  } catch {
    return a;
  }
}

/* ───────────────────────── Einzel-Nachricht rendern ───────────────────────── */

function renderChatItem(msg) {
  try {
    const box = $("msgs");
    if (!box || !msg) return;

    _ensureState();

    // Dedup per msg.id (falls vorhanden)
    if (msg.id && uiState.renderedIds.has(msg.id)) return;

    // Karten
    if (msg.meta && msg.meta.type === "card") {
      renderCard(box, msg);
      if (msg.id) uiState.renderedIds.add(msg.id);
      _pruneRenderedIds();
      return;
    }

    // Standard-Text
    const atts = Array.isArray(msg.attachments) ? msg.attachments.map(mapAttachment) : [];
    appendMessage({ id: msg.id || null, role: msg.role, text: msg.text, attachments: atts });

    if (msg.id) uiState.renderedIds.add(msg.id);
    _pruneRenderedIds();
  } catch {}
}

/* ───────────────────────── Ghost/Thinking Cleanup ─────────────────────────
   Strategie:
   - Nicht aggressiv alles löschen, sondern nur "hängende" thinking-bubbles.
   - Entfernen optional auch nur, wenn es keine aktive Streaming-Session gibt.
   - Da composer.js thinking-bubbles bewusst nutzt, entfernen wir nur jene, die älter sind.
------------------------------------------------------------------- */

function _clearGhostThinking({ maxAgeMs = 3 * 60 * 1000 } = {}) {
  try {
    const nodes = document.querySelectorAll(".msg.assistant.thinking");
    const now = Date.now();

    nodes.forEach(n => {
      try {
        // If no timestamp exists, set one (first time seen)
        if (!n.dataset.ts) n.dataset.ts = String(now);
        const ts = Number(n.dataset.ts || 0);

        if (!ts || (now - ts) > maxAgeMs) {
          n.remove();
        }
      } catch {}
    });
  } catch {}
}

/* ───────────────────────── Vollständiges Transcript laden ───────────────────────── */

export async function loadTranscript(){
  const cfg = getConfig();
  _ensureState();

  // Templates Index (mit Cache in cards.js)
  try { await loadTemplatesIndex(); } catch {}

  try {
    const conv = await getChat(cfg.chatId);

    // Viewer initial setzen (nur wenn 3D und URL vorhanden)
    // (viewer.js selbst normalisiert/proxied)
    try {
      const v = conv?.viewer_url;
      if (v && (uiState.viewerMode === "3d" || !uiState.viewerMode)) showViewer(v);
    } catch {}

    // Nachrichten rendern
    (conv?.transcript || []).forEach((m) => renderChatItem(m));
  } catch {}

  // Viewer-Status aktualisieren und Polling starten (nur relevant im 3D-Modus)
  try {
    if (uiState.viewerMode === "3d") {
      void refreshViewerFromServer();
      startViewerPolling();
    }
  } catch {}
}

/* ───────────────────────── Inkrementelles Nachladen ───────────────────────── */

let _refreshInFlight = false;
let _lastRefreshTs = 0;

export async function refreshTranscriptIncremental(){
  // Simple throttle, um "chat:refresh"-Stürme zu vermeiden
  const now = Date.now();
  if (_refreshInFlight) return;
  if (now - _lastRefreshTs < 250) return;
  _lastRefreshTs = now;

  _refreshInFlight = true;
  try {
    // Optimistische User-Bubbles weg
    removePending("user");

    // Hängende Thinking-Bubbles nur selektiv
    _clearGhostThinking({ maxAgeMs: 2 * 60 * 1000 });

    const cfg = getConfig();
    const conv = await getChat(cfg.chatId);

    (conv?.transcript || []).forEach((m) => renderChatItem(m));
  } catch {
    // ignore
  } finally {
    _refreshInFlight = false;
  }
}

/* ───────────────────────── Event-Wiring ───────────────────────── */

export function wireTranscriptRefresh() {
  try {
    if (window.__VECTOAI_TRANSCRIPT_WIRED__) return;
    window.__VECTOAI_TRANSCRIPT_WIRED__ = true;

    window.addEventListener("chat:refresh", () => { void refreshTranscriptIncremental(); });
  } catch {}
}