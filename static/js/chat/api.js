// services/app/static/js/chat/api.js
// Robust client API helpers for chat app.
// - Tolerant response parsing
// - Unified upload result mapping
// - Safe defaults and input guards
// - Adds admin/lv page paths to config
// - Consistent fetch options (timeout via AbortController, cache defaults)
// - Defensive JSON parsing + error mapping

/** ---------- Logging ---------- */
const _log = (...a) => { try { console.info("[api]", ...a); } catch {} };
const _warn = (...a) => { try { console.warn("[api]", ...a); } catch {} };

/** ---------- Config ---------- */
export function getConfig() {
  const d = (typeof window !== "undefined" && window.APP_CONFIG) ? window.APP_CONFIG : {};
  const baseUrl = String(d.baseUrl || (typeof location !== "undefined" ? location.origin : "")).replace(/\/+$/, "");
  const chatId = d.chatId || null;
  const encId = chatId ? encodeURIComponent(chatId) : null;

  const uiPath = (suffix) => (encId ? `/ui/chat/${encId}/${suffix}` : "");
  const v1Path = (suffix) => (encId ? `/v1/chats/${encId}/${suffix}` : "");

  return {
    baseUrl,
    chatId,

    // Chat
    chatPath: d.chatPath || "/v1/chat",
    streamPath: d.streamPath || "/v1/chat/stream",

    // Viewer + Versions
    viewerJsonPath: d.viewerJsonPath || uiPath("viewer.json"),
    versionsPath: d.versionsPath || uiPath("versions.json"),

    // 2D Viewer (Raw + Legacy)
    plan2dJsonPath: d.plan2dJsonPath || uiPath("plan2d.json"),
    cad2dPagePath: d.cad2dPagePath || uiPath("cad2d"),

    // 2D Viewer (CAD Microservice)
    cadEmbedJsonPath: d.cadEmbedJsonPath || uiPath("cad-embed.json"),
    cadEmbedBase: d.cadEmbedBase || "",

    // MAP (OpenLayers)
    mapPagePath: d.mapPagePath || uiPath("map"),
    mapJsonPath: d.mapJsonPath || uiPath("map.json"),

    // Admin / LV
    adminPagePath: d.adminPagePath || uiPath("admin"),
    lvPagePath: d.lvPagePath || uiPath("lv"),

    // Uploads
    uiUploadPath: d.uiUploadPath || uiPath("upload"),
    apiUploadPath: d.apiUploadPath || v1Path("speckle/upload"),

    // Templates + Messages
    templatesPath: d.templatesPath || "/v1/templates",
    postMessagePath: d.postMessagePath || v1Path("messages"),

    // State (abschaltbar via "__DISABLED__")
    stateGetPath: (d.stateGetPath === "__DISABLED__") ? "" : (d.stateGetPath || v1Path("state")),
    statePutPath: (d.statePutPath === "__DISABLED__") ? "" : (d.statePutPath || v1Path("state")),
    stateClearPath: d.stateClearPath || v1Path("state/clear"),
  };
}

/** ---------- Helpers ---------- */
function _isObj(x){ return x && typeof x === "object"; }

function _safeStr(x, d = "") {
  try { return String(x ?? d); } catch { return d; }
}

function _safeInt(x, d = 0) {
  try {
    const n = Number(x);
    return Number.isFinite(n) ? Math.trunc(n) : d;
  } catch { return d; }
}

async function tryParseJson(res) {
  try {
    const ct = (res.headers.get("content-type") || "").toLowerCase();

    if (ct.includes("application/json")) {
      try { return await res.json(); } catch { return null; }
    }

    // Fallback: Text lesen und JSON versuchen
    const txt = await res.text();
    try { return JSON.parse(txt); } catch { return null; }
  } catch { return null; }
}

function withTimeout(signal, timeoutMs = 15000) {
  // Returns { signal, cleanup }
  try {
    const ms = Math.max(1000, _safeInt(timeoutMs, 15000));
    const ctrl = new AbortController();

    // If caller provided a signal, mirror abort
    const onAbort = () => { try { ctrl.abort(); } catch {} };
    if (signal && typeof signal.addEventListener === "function") {
      signal.addEventListener("abort", onAbort, { once: true });
    }

    const t = setTimeout(() => { try { ctrl.abort(); } catch {} }, ms);

    return {
      signal: ctrl.signal,
      cleanup: () => {
        try { clearTimeout(t); } catch {}
        try {
          if (signal && typeof signal.removeEventListener === "function") {
            signal.removeEventListener("abort", onAbort);
          }
        } catch {}
      }
    };
  } catch {
    return { signal, cleanup: () => {} };
  }
}

async function fetchJSON(url, opts = {}) {
  const method = _safeStr(opts.method, "GET");
  const cache = opts.cache ?? "no-store"; // robust default for DEV + correctness
  const credentials = opts.credentials || "same-origin";
  const timeoutMs = opts.timeoutMs ?? 20000;

  const { signal, cleanup } = withTimeout(opts.signal, timeoutMs);

  try {
    const res = await fetch(url, {
      headers: { "Accept": "application/json", ...(opts.headers || {}) },
      cache,
      method,
      body: opts.body,
      signal,
      credentials,
    });

    const json = await tryParseJson(res);
    return {
      ok: res.ok,
      status: res.status,
      json,
      res,
      etag: res.headers.get("etag") || ""
    };
  } catch (e) {
    const msg = _safeStr(e && e.message ? e.message : e, "fetch failed");
    return { ok: false, status: 0, json: { error: msg } };
  } finally {
    cleanup();
  }
}

function uniq(arr){
  try { return Array.from(new Set(arr || [])).filter(Boolean); } catch { return []; }
}

function toId(x){
  try { return x?.file_id || x?.id || null; } catch { return null; }
}

function normalizeAttachmentIds(attList){
  try {
    if (!Array.isArray(attList)) return [];
    return attList.map(x => (x && typeof x === "object") ? (x.file_id || x.id) : x).filter(Boolean);
  } catch { return []; }
}

/** Canonicalize upload items from /v1/files into a stable shape. */
export function normalizeUploadItems(items){
  const { baseUrl } = getConfig();
  const out = [];
  (items || []).forEach(it=>{
    try{
      const id = toId(it);
      if (!id) return;
      const filename = it.filename || it.name || "file";
      out.push({
        file_id: id,
        id,
        filename,
        name: filename, // compat
        mime: it.mime || it.type || "application/octet-stream",
        size: typeof it.size === "number" ? it.size : (Number(it.size) || 0),
        sha256: it.sha256 || null,
        content_url: it.content_url || `${baseUrl}/v1/files/${encodeURIComponent(id)}/content`,
        download_url: it.download_url || `${baseUrl}/v1/files/${encodeURIComponent(id)}/download`,
        meta_url: it.meta_url || `${baseUrl}/v1/files/${encodeURIComponent(id)}`,
      });
    } catch { /* ignore single item */ }
  });
  return out;
}

/** ---------- Chat (sync) ---------- */
export async function sendChat(body) {
  const { baseUrl, chatPath } = getConfig();
  const payload = _isObj(body) ? { ...body } : {};

  try {
    payload.attachments = normalizeAttachmentIds(payload.attachments);
  } catch { payload.attachments = []; }

  _log("sendChat payload", payload);

  return await fetchJSON(baseUrl + chatPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 30000,
  });
}

/** ---------- Chat (fetch transcript) ---------- */
export async function getChat(chatId) {
  const { baseUrl } = getConfig();
  const id = chatId || getConfig().chatId;
  if (!id) return null;

  const r = await fetchJSON(`${baseUrl}/v1/chats/${encodeURIComponent(id)}`, {
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 25000,
  });

  return r.ok ? (r.json || null) : null;
}

/** ---------- Streaming über SSE (Fetch + ReadableStream) ---------- */
/**
 * streamChat(payload, onEvent, opts?)
 *  - payload: { chat_id, message, attachments }
 *  - onEvent: function(evtObj)
 *  - opts.signal: AbortSignal (optional)
 */
export async function streamChat(body, onEvent, opts = {}) {
  const { baseUrl, streamPath, chatId } = getConfig();
  const payload = _isObj(body) ? { ...body } : {};

  if (!payload.chat_id && chatId) payload.chat_id = chatId;
  try { payload.attachments = normalizeAttachmentIds(payload.attachments); }
  catch { payload.attachments = []; }

  _log("streamChat payload", payload);

  // Streaming soll nicht gecacht werden
  const { signal, cleanup } = withTimeout(opts.signal, opts.timeoutMs ?? 120000);

  let res;
  try {
    res = await fetch(baseUrl + streamPath, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(payload),
      signal,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (e) {
    cleanup();
    throw new Error(_safeStr(e?.message || e, "stream fetch failed"));
  }

  if (!res.ok || !res.body) {
    cleanup();
    throw new Error(`stream failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();

  let buf = "";
  let dataLines = [];

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });

      let lineEnd;
      while ((lineEnd = buf.indexOf("\n")) >= 0) {
        let line = buf.slice(0, lineEnd);
        buf = buf.slice(lineEnd + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);

        if (line === "") {
          if (dataLines.length) {
            const data = dataLines.join("\n");
            dataLines = [];
            try {
              const obj = JSON.parse(data);
              try { onEvent && onEvent(obj); } catch {}
            } catch {
              // unparsable payload ignorieren
            }
          }
          continue;
        }

        if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    }

    // Restflush
    if (dataLines.length) {
      try {
        const obj = JSON.parse(dataLines.join("\n"));
        try { onEvent && onEvent(obj); } catch {}
      } catch {}
    }
  } finally {
    cleanup();
    try { reader.releaseLock(); } catch {}
  }
}

/** ---------- Files ---------- */
export async function uploadFiles(files) {
  try {
    const list = Array.from(files || []);
    _log("uploadFiles start", list.length);
    if (list.length === 0) return [];

    const { baseUrl } = getConfig();
    const fd = new FormData();
    for (const f of list) {
      try { fd.append("files", f, f.name || "file"); } catch {}
    }

    const res = await fetch(baseUrl + "/v1/files", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store",
    });

    if (!res.ok) {
      _warn("uploadFiles http", res.status);
      return [];
    }

    const json = await res.json().catch(() => null);
    const arr = Array.isArray(json) ? json : (json?.items || []);
    const norm = (arr || []).map(it => ({
      file_id: it?.file_id || it?.id,
      filename: it?.filename || it?.name,
      mime: it?.mime,
      size: it?.size
    })).filter(x => x.file_id);

    _log("uploadFiles norm", norm);
    return norm;
  } catch (e) {
    _warn("uploadFiles error", e?.message || e);
    return [];
  }
}

export function fileContentUrl(fileId) {
  const { baseUrl } = getConfig();
  return `${baseUrl}/v1/files/${encodeURIComponent(fileId)}/content`;
}

export function fileDownloadUrl(fileId) {
  const { baseUrl } = getConfig();
  return `${baseUrl}/v1/files/${encodeURIComponent(fileId)}/download`;
}

/** ---------- Templates ---------- */
export async function getTemplates() {
  const { templatesPath } = getConfig();
  const r = await fetchJSON(templatesPath, {
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 20000,
  });
  return r.ok ? (r.json?.items || []) : [];
}

/** ---------- Structured messages ---------- */
export async function postCardMessage({ template_key, payload = {}, role = "service", trace = [], validate = true } = {}) {
  const { baseUrl, postMessagePath } = getConfig();
  return await fetchJSON(baseUrl + postMessagePath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_key, payload, role, trace, validate }),
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 25000,
  });
}

export async function postTextMessage({ text = "", role = "assistant", trace = [] } = {}) {
  const { baseUrl, postMessagePath } = getConfig();
  return await fetchJSON(baseUrl + postMessagePath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, role, trace }),
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 25000,
  });
}

/** ---------- State ---------- */
export async function getState() {
  const { baseUrl, stateGetPath } = getConfig();
  if (!stateGetPath) return {};
  const r = await fetchJSON(baseUrl + stateGetPath, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 20000,
  });
  return r.ok ? (r.json?.state || {}) : {};
}

export async function putState({ patch = null, state = null, replace = false } = {}) {
  const { baseUrl, statePutPath } = getConfig();
  if (!statePutPath) return { ok: true, status: 204, json: {} };

  const body = replace ? { state, replace: true } : (patch ? { patch } : {});
  return await fetchJSON(baseUrl + statePutPath + (replace ? "?replace=1" : ""), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 25000,
  });
}

export async function clearState() {
  const { baseUrl, stateClearPath, statePutPath } = getConfig();
  if (!statePutPath) return { ok: true, status: 204, json: {} };
  return await fetchJSON(baseUrl + stateClearPath, {
    method: "PATCH",
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 20000,
  });
}

/** ---------- Versions (optional helpers) ---------- */
export async function deleteVersion(versionId) {
  const { baseUrl, chatId } = getConfig();
  if (!chatId || !versionId) return { ok: false, status: 400, json: { error: "missing params" } };

  return await fetchJSON(`${baseUrl}/v1/chats/${encodeURIComponent(chatId)}/versions/${encodeURIComponent(versionId)}`, {
    method: "DELETE",
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 20000,
  });
}

/** ---------- Speckle API upload (optional) ---------- */
export async function apiUploadFile({ file_id, model_name = null, source_message_id = null } = {}) {
  const { baseUrl, apiUploadPath } = getConfig();
  return await fetchJSON(baseUrl + apiUploadPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id, model_name, source_message_id }),
    credentials: "same-origin",
    cache: "no-store",
    timeoutMs: 45000,
  });
}