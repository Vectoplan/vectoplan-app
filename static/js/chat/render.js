// services/app/static/js/chat/render.js
// Robuste Chat-Renderer-Helfer
// - Sichere Escapes
// - Einheitliches Attachment-Rendering
// - Optimistische Bubbles
// - Thinking-Bubble mit Spinner links + animierten Punkten
// - Timestamp an Thinking-Bubbles (für Ghost-Cleanup in transcript.js)

const msgs = () => document.getElementById("msgs");

/* ───────────────────────── small utils ───────────────────────── */

function _safe(str) {
  try {
    return String(str ?? "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[m]);
  } catch { return ""; }
}

function _cssEscape(s){
  try {
    return (window.CSS && CSS.escape)
      ? CSS.escape(String(s))
      : String(s).replace(/"/g, '\\"');
  } catch {
    return String(s || "");
  }
}

function _ensureMsgs() {
  try {
    const el = msgs();
    if (el) return el;

    // Fallback: minimaler Container (sollte im Template existieren, aber robust bleiben)
    const fallback = document.createElement("div");
    fallback.id = "msgs";
    fallback.className = "msgs";
    document.body.append(fallback);
    return fallback;
  } catch { return null; }
}

function _append(el) {
  try {
    const box = _ensureMsgs();
    if (!box || !el) return;
    box.append(el);
    try { box.scrollTop = box.scrollHeight; } catch {}
  } catch {}
}

function _chip() {
  const d = document.createElement("div");
  d.className = "chip";
  return d;
}

function _fmtBytes(n) {
  try {
    const x = Number(n || 0);
    if (!isFinite(x) || x <= 0) return "";
    const k = 1024, units = ["B","KB","MB","GB","TB"];
    const i = Math.floor(Math.log(x)/Math.log(k));
    return `${(x/Math.pow(k,i)).toFixed(i ? 1 : 0)} ${units[i]}`;
  } catch { return ""; }
}

function _isLikelySafeUrl(href) {
  try {
    const s = String(href || "").trim();
    if (!s) return false;
    if (s.startsWith("javascript:")) return false;
    if (s.startsWith("data:")) return true; // für base64 previews ok
    if (s.startsWith("/")) return true;      // same-origin
    // absolute url
    const u = new URL(s, location.href);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/* ───────────────────────── attachment rendering ───────────────────────── */

function _renderAttachment(a = {}) {
  const chip = _chip();

  try {
    const mime = String(a.type || a.mime || "").toLowerCase();
    const name = _safe(a.name || a.filename || a.url || a.download_url || "Datei");
    const sizeStr = _fmtBytes(a.size);

    const previewSrc = a.preview || a.content_url || null;

    if (previewSrc && mime.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = previewSrc;
      img.alt = name || "Bild";
      img.loading = "lazy";
      chip.append(img);
    }

    else if (previewSrc && mime.startsWith("video/")) {
      const v = document.createElement("video");
      v.src = previewSrc;
      v.muted = true;
      v.loop = true;
      v.autoplay = true;
      v.playsInline = true;
      v.preload = "metadata";
      v.style.maxWidth = "140px";
      v.style.maxHeight = "90px";
      chip.append(v);
    }

    else {
      const icon = document.createElement("div");
      icon.textContent = "📄";
      chip.append(icon);
    }

    const meta = document.createElement("div");
    meta.className = "meta";

    // Link priorisieren: download_url > url > meta_url > content_url
    const linkHref = a.download_url || a.url || a.meta_url || a.content_url || null;

    if (linkHref && _isLikelySafeUrl(linkHref)) {
      const link = document.createElement("a");
      link.href = linkHref;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = name;
      meta.append(link);
    } else {
      meta.textContent = name;
    }

    if (sizeStr) {
      const sz = document.createElement("span");
      sz.style.opacity = ".8";
      sz.style.marginLeft = "6px";
      sz.textContent = `(${sizeStr})`;
      meta.append(sz);
    }

    chip.append(meta);
  } catch {}

  return chip;
}

function _renderAttachments(attachments = []) {
  try {
    if (!attachments || !attachments.length) return null;

    const wrap = document.createElement("div");
    wrap.style.marginTop = ".35rem";
    wrap.style.display = "flex";
    wrap.style.flexWrap = "wrap";
    wrap.style.gap = "8px";

    (Array.isArray(attachments) ? attachments : [attachments]).forEach((raw) => {
      try {
        if (typeof raw === "string") wrap.append(_renderAttachment({ name: raw }));
        else if (raw && typeof raw === "object") wrap.append(_renderAttachment(raw));
      } catch {}
    });

    return wrap;
  } catch {
    return null;
  }
}

/* ───────────────────────── public API: message bubbles ───────────────────────── */

export function appendMessage({ id = null, role, text, attachments = [], pending = false } = {}) {
  try {
    const box = _ensureMsgs();
    if (!box) return null;

    const clsRole =
      (role === "user" || role === "assistant" || role === "service" || role === "system")
        ? role
        : "assistant";

    const el = document.createElement("div");
    el.className = `msg ${clsRole}`;
    if (pending) el.classList.add("pending");
    if (id) el.dataset.id = String(id);

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = role === "user" ? "Du" : (role === "service" ? "System" : "KI");

    const body = document.createElement("div");
    body.className = "body";
    body.style.whiteSpace = "pre-wrap";
    body.textContent = String(text || "");

    el.append(meta, body);

    const attsEl = _renderAttachments(attachments);
    if (attsEl) el.append(attsEl);

    _append(el);
    return el;
  } catch {
    return null;
  }
}

export function removePending(role = "user") {
  try {
    document.querySelectorAll(`.msg.${role}.pending`).forEach(n => n.remove());
  } catch {}
}

export function removePendingSimilar({ role = "user", text = "" } = {}) {
  try {
    const needle = String(text || "").trim();
    const nodes = document.querySelectorAll(`.msg.${role}.pending`);
    for (const n of nodes) {
      const body = n.querySelector(":scope > .body");
      const val = (body?.textContent || "").trim();
      if (val === needle) { n.remove(); break; }
    }
  } catch {}
}

export function removeByMessageId(id) {
  try {
    if (!id) return;
    const n = document.querySelector(`.msg[data-id="${_cssEscape(String(id))}"]`);
    if (n) n.remove();
  } catch {}
}

/* ───────────────────────── thinking indicator with animated dots ───────────────────────── */

let _idSeq = 0;
const _thinkTimers = new Map(); // msgId -> interval handle

function _nextId(){
  try { return `m_${++_idSeq}`; }
  catch { return `m_${Date.now()}`; }
}
function _stopThinkingAnim(id){
  try {
    const h = _thinkTimers.get(id);
    if (h) {
      clearInterval(h);
      _thinkTimers.delete(id);
    }
  } catch {}
}

/**
 * Erstellt eine Assistant-Bubble im Thinking-Zustand.
 * Zeigt ⏳ links + Label mit animierten Punkten.
 * Rückgabe: clientseitige msgId, z.B. "m_1"
 */
export function appendAssistantThinking(text = "Vectoplan denkt nach") {
  const box = _ensureMsgs();
  if (!box) return null;

  const id = _nextId();

  const el = document.createElement("div");
  el.className = "msg assistant thinking";
  el.dataset.msgId = id;
  // Timestamp für Ghost-Cleanup
  try { el.dataset.ts = String(Date.now()); } catch {}

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = "KI";

  const body = document.createElement("div");
  body.className = "body";
  body.style.whiteSpace = "pre-wrap";
  body.dataset.placeholder = "1";

  // Zeile: Spinner + Label + Punkte
  const row = document.createElement("span");
  row.className = "thinking-wrap";

  const spinner = document.createElement("span");
  spinner.textContent = "⏳ ";

  const label = document.createElement("span");
  label.textContent = String(text || "Denkt");

  const dots = document.createElement("span");
  dots.className = "dots";
  dots.textContent = ".";

  row.append(spinner, label, dots);
  body.append(row);

  el.append(meta, body);
  _append(el);

  // Animation: ".", "..", "...", "..", "."
  let count = 1, dir = 1;
  const h = setInterval(() => {
    count += dir;
    if (count >= 3) dir = -1;
    if (count <= 1) dir = +1;
    try { dots.textContent = ".".repeat(count); } catch {}
  }, 420);

  _thinkTimers.set(id, h);

  return id;
}

/** Ersetzt den Thinking-Platzhalter durch Streaming-Text. */
export function updateAssistantThinking(id, delta = "") {
  try {
    if (!id) return;

    const sel = `.msg.assistant.thinking[data-msg-id="${_cssEscape(id)}"] .body`;
    const body = document.querySelector(sel);
    if (!body) return;

    // Beim ersten Delta: Platzhalter entfernen + Animation stoppen
    if (body.dataset.placeholder === "1") {
      try {
        const wrap = body.querySelector(".thinking-wrap");
        if (wrap) wrap.remove();
      } catch {}
      _stopThinkingAnim(id);
      body.textContent = "";
      try { delete body.dataset.placeholder; } catch {}
    }

    body.textContent = (body.textContent || "") + String(delta || "");

    const box = msgs();
    try { if (box) box.scrollTop = box.scrollHeight; } catch {}
  } catch {}
}

/** Finalisiert die Bubble: entfernt Thinking-Status, übernimmt server-id und Attachments. */
export function finalizeAssistantThinking(id, assistantMsg = {}) {
  try {
    if (!id) return;

    const el = document.querySelector(`.msg.assistant.thinking[data-msg-id="${_cssEscape(id)}"]`);
    if (!el) return;

    _stopThinkingAnim(id);

    el.classList.remove("thinking");

    const body = el.querySelector(":scope > .body");
    try {
      const wrap = body?.querySelector(".thinking-wrap");
      if (wrap) wrap.remove();
      if (body?.dataset?.placeholder === "1") delete body.dataset.placeholder;
    } catch {}

    if (assistantMsg && assistantMsg.id) {
      el.dataset.id = String(assistantMsg.id);
    }

    // Attachments (falls vorhanden)
    try {
      const atts = assistantMsg.attachments;
      if (Array.isArray(atts) && atts.length) {
        const attsEl = _renderAttachments(atts);
        if (attsEl) el.append(attsEl);
      }
    } catch {}

    const box = msgs();
    try { if (box) box.scrollTop = box.scrollHeight; } catch {}
  } catch {}
}