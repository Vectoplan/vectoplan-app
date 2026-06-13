// services/app/static/js/chat/core.js
// Kern-Helfer: DOM, Fetch, Sanitizing, globaler UI-State.
// Robustheitsziele:
// - Einheitliche safeCall/safeAwait/safeOn Utilities (reduziert Duplikate in Modulen)
// - fetchJSON mit Timeout/Abort, robustem Parse, ETag, konsistenten Defaults
// - Minimale, stabile uiState-Struktur (kein Business-State)
// - Statusanzeige: defensiv, keine Exceptions
//
// Hinweis: Dieses Modul ist "low-level". Keine Imports aus anderen Chat-Modulen.

export const $ = (id) => {
  try { return document.getElementById(id); } catch { return null; }
};

/* ───────────────────────── Safe Utils ───────────────────────── */

export function safeCall(fn, fallback = undefined) {
  try { return fn(); } catch { return fallback; }
}

export async function safeAwait(fn, fallback = undefined) {
  try { return await fn(); } catch { return fallback; }
}

export function safeOn(el, ev, handler, opts) {
  try {
    if (!el) return false;
    el.addEventListener(ev, handler, opts);
    return true;
  } catch { return false; }
}

export function safeHtml(str) {
  try {
    return String(str || "").replace(/[&<>"']/g, (m)=>({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[m]));
  } catch { return ""; }
}

/** Querystring-Helfer */
export function qs(params = {}) {
  try {
    const usp = new URLSearchParams();
    Object.entries(params).forEach(([k,v])=>{
      if (v === undefined || v === null) return;
      usp.append(String(k), String(v));
    });
    return usp.toString();
  } catch { return ""; }
}

/** Sleep */
export const sleep = (ms)=>new Promise(r=>setTimeout(r, Math.max(0, Number(ms)||0)));

/* ───────────────────────── Fetch Helpers ───────────────────────── */

function _toInt(x, d = 0) {
  try {
    const n = Number(x);
    return Number.isFinite(n) ? Math.trunc(n) : d;
  } catch { return d; }
}

function _withTimeout(signal, timeoutMs = 15000) {
  // Returns { signal, cleanup }
  try {
    const ms = Math.max(1000, _toInt(timeoutMs, 15000));
    const ctrl = new AbortController();

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

async function _tryParseBody(res) {
  try {
    const ct = String(res.headers.get("content-type") || "").toLowerCase();
    const isJson = ct.includes("application/json");

    if (isJson) {
      const js = await res.json().catch(() => null);
      return { json: js, text: null };
    }
    const txt = await res.text().catch(() => "");
    // Fallback: manche APIs liefern JSON ohne ct
    const maybe = (() => { try { return JSON.parse(txt); } catch { return null; } })();
    if (maybe && typeof maybe === "object") return { json: maybe, text: null };
    return { json: null, text: txt };
  } catch {
    return { json: null, text: null };
  }
}

/**
 * fetchJSON(url, opts)
 * opts:
 * - method, headers, body, credentials, cache, signal, timeoutMs
 *
 * Return:
 * { ok, status, json, text, etag, res }
 */
export async function fetchJSON(url, opts = {}) {
  const timeoutMs = Math.max(8000, _toInt(opts.timeoutMs ?? 15000, 15000));

  const { signal, cleanup } = _withTimeout(opts.signal, timeoutMs);

  try {
    const res = await fetch(url, {
      headers: { "Accept": "application/json", ...(opts.headers || {}) },
      cache: opts.cache ?? "no-store",                 // robust default
      method: opts.method ?? "GET",
      body: opts.body,
      signal,
      credentials: opts.credentials || "same-origin",
    });

    const etag = res.headers.get("etag") || "";
    const parsed = await _tryParseBody(res);

    return {
      ok: res.ok,
      status: res.status,
      json: parsed.json,
      text: parsed.text,
      etag,
      res
    };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      json: null,
      text: String(e && e.message ? e.message : e),
      etag: "",
      res: null
    };
  } finally {
    cleanup();
  }
}

/* ───────────────────────── Globaler UI-State (keine Businessdaten) ───────────────────────── */

export const uiState = {
  // polling
  pollTimer: null,

  // viewer
  lastViewerUrl: "",
  rawViewerUrl: "",
  viewerMode: "3d",                         // '3d' | '2d' | 'map' | 'admin' | 'lv'
  _cad2d: { dxfUrl: "", embedUrl: "", status: "init" },

  // transcript rendering
  templatesIndex: {},                       // key -> template meta
  renderedIds: new Set(),                   // msg ids
  stateEtag: "",                            // If-Match support (optional)

  // events
  last2DSelection: null,
  last2DHover: null,
};

/* ───────────────────────── Polling ───────────────────────── */

export function stopPolling() {
  safeCall(() => {
    if (uiState.pollTimer) {
      clearInterval(uiState.pollTimer);
      uiState.pollTimer = null;
    }
  });
}

export function startPolling(fn, { intervalMs = 5000, maxTries = 120 } = {}) {
  stopPolling();
  let tries = 0;

  uiState.pollTimer = setInterval(async () => {
    tries++;
    try { await fn(); } catch {}
    if (tries >= maxTries) stopPolling();
  }, Math.max(300, _toInt(intervalMs, 5000)));
}

/* ───────────────────────── System-Statusanzeige oben im Chat ───────────────────────── */

export function setStatus(text, { ok = true } = {}) {
  safeCall(() => {
    const el = $("systemAlert");
    if (!el) return;

    const msg = String(text || "");
    el.textContent = msg;
    el.classList.toggle("hidden", !msg);

    // Colors: best-effort (wir verwenden vorhandene Dark-Panel-Farben)
    el.style.borderColor = ok ? "#2a3561" : "#7a2b2b";
    el.style.background = ok ? "#111b3f" : "#2b1111";
  });
}