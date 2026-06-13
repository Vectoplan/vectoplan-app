/* /services/app/static/js/admin.js */

(function () {
  "use strict";

  // ───────────────────────── Utilities ─────────────────────────
  function safe(fn, fallback) {
    try { return fn(); } catch (e) { return fallback; }
  }
  function qs(sel, root) {
    return safe(() => (root || document).querySelector(sel), null);
  }
  function qsa(sel, root) {
    return safe(() => Array.from((root || document).querySelectorAll(sel)), []);
  }
  function post(msg) {
    safe(() => window.parent && window.parent.postMessage(msg, "*"));
  }
  function nowISO() {
    return safe(() => new Date().toISOString(), "");
  }
  function asStr(v, dflt) {
    return safe(() => (v === undefined || v === null) ? (dflt || "") : String(v), dflt || "");
  }

  // ───────────────────────── Config ─────────────────────────
  const CHAT_ID = safe(() => String((window.ADMIN_CONFIG && window.ADMIN_CONFIG.chatId) || ""), "");
  const STORAGE_ACTIVE_TAB = `vectoai.admin.activeTab${CHAT_ID ? ":" + CHAT_ID : ""}`;

  // Theme-Key ist global (wie in chat_viewer.html)
  const STORAGE_THEME_KEY = "theme";

  // Migration/Aliases (robust bei umbenannten Tabs)
  // Alt -> Neu (damit alte Hashes/localStorage nicht "kaputt" sind)
  const TAB_ALIASES = new Map([
    ["placeholder-4", "data-analyse"],
    ["placeholder-1", "database"],
    ["placeholder-2", "sales"],
    ["placeholder-3", "wiki"],

    // toleranter: alternative Schreibweisen
    ["dataanalyse", "data-analyse"],
    ["data_analyse", "data-analyse"],
    ["data-analysis", "data-analyse"],
    ["dataanalysis", "data-analyse"],

    ["data-mining", "datamining"],
    ["data_mining", "datamining"],
  ]);

  // ───────────────────────── DOM ─────────────────────────
  const root = qs("#root");
  const tablist = qs(".admin-tabs");
  const tabs = qsa(".admin-tab[data-tab]");
  const panels = qsa(".admin-panel[data-panel]");

  const tabsByKey = new Map();
  const panelsByKey = new Map();

  safe(() => tabs.forEach((b) => {
    const k = safe(() => String(b.dataset.tab || "").trim(), "");
    if (k) tabsByKey.set(k.toLowerCase(), b);
  }));

  safe(() => panels.forEach((p) => {
    const k = safe(() => String(p.dataset.panel || "").trim(), "");
    if (k) panelsByKey.set(k.toLowerCase(), p);
  }));

  // DOM-Reihenfolge behalten (Tabs/Keys in Reihenfolge)
  const knownKeys = safe(
    () => tabs.map(t => String((t.dataset && t.dataset.tab) || "").trim().toLowerCase()).filter(Boolean),
    []
  );
  const knownKeysSet = new Set(knownKeys);

  // ───────────────────────── Guards / Early exits ─────────────────────────
  if (!tablist || tabs.length === 0 || panels.length === 0 || knownKeys.length === 0) {
    post({ t: "admin.status", chatId: CHAT_ID, status: "error", error: "admin.js: missing tab/panel structure" });
    return;
  }

  // ───────────────────────── Theme Sync ─────────────────────────
  function normalizeTheme(v) {
    const s = String(v || "").trim().toLowerCase();
    return (s === "dark" || s === "light") ? s : "";
  }

  function applyTheme(themeRaw) {
    safe(() => {
      const theme =
        normalizeTheme(themeRaw) ||
        normalizeTheme(safe(() => localStorage.getItem(STORAGE_THEME_KEY), ""));

      if (theme) {
        document.documentElement.setAttribute("data-theme", theme);
        return;
      }

      // Fallback: wenn nichts gesetzt ist, bevorzugtes Systemtheme nutzen
      const hasAttr = !!document.documentElement.getAttribute("data-theme");
      if (!hasAttr) {
        const preferDark = safe(
          () => window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches,
          false
        );
        document.documentElement.setAttribute("data-theme", preferDark ? "dark" : "light");
      }
    });
  }

  // initial apply (admin.html setzt schon vorab, hier nur Robustheit)
  applyTheme("");

  // Live theme updates aus Parent (storage event in iframe, same-origin)
  safe(() => window.addEventListener("storage", (e) => {
    if (!e) return;
    if (e.key === STORAGE_THEME_KEY) {
      applyTheme(e.newValue);
    }
  }));

  // ───────────────────────── Tab State ─────────────────────────
  function decodeMaybe(s) {
    return safe(() => decodeURIComponent(String(s || "")), String(s || ""));
  }

  function canonicalKey(raw) {
    const s0 = String(raw || "").trim().toLowerCase();
    if (!s0) return "";

    // Support: "tab=xyz" / "tab:xyz" / "#xyz"
    let s = s0;
    if (s.startsWith("tab=")) s = s.slice(4).trim();
    if (s.startsWith("tab:")) s = s.slice(4).trim();
    s = decodeMaybe(s).trim().toLowerCase();

    // Apply aliases/migration
    const aliased = TAB_ALIASES.get(s);
    if (aliased) return String(aliased).trim().toLowerCase();

    return s;
  }

  function isValidKey(key) {
    const k = canonicalKey(key);
    return !!(k && tabsByKey.has(k) && panelsByKey.has(k));
  }

  function storageGet(key) {
    return safe(() => localStorage.getItem(key) || "", "");
  }
  function storageSet(key, value) {
    safe(() => localStorage.setItem(key, String(value || "")));
  }

  function getKeyFromQuery() {
    return safe(() => {
      const p = new URLSearchParams(location.search || "");
      const q = p.get("tab") || p.get("view") || p.get("panel") || "";
      return canonicalKey(q);
    }, "");
  }

  function getKeyFromHash() {
    return safe(() => {
      const h = String(location.hash || "").replace(/^#/, "").trim();
      if (!h) return "";
      // akzeptiere: #dashboard oder #tab=dashboard oder #tab:dashboard
      return canonicalKey(h);
    }, "");
  }

  function writeHash(key) {
    safe(() => {
      const k = canonicalKey(key);
      if (!k) return;

      // replaceState ist robuster (vermeidet Scroll/History spam)
      if (history && history.replaceState) {
        const url = new URL(location.href);
        url.hash = "tab=" + encodeURIComponent(k);
        history.replaceState(null, "", url.toString());
        return;
      }

      // fallback
      location.hash = "tab=" + encodeURIComponent(k);
    });
  }

  function getDefaultKey() {
    // first tab in DOM order
    const first = safe(() => canonicalKey(tabs[0] && tabs[0].dataset && tabs[0].dataset.tab), "");
    if (isValidKey(first)) return first;

    // fallback: first knownKey that is valid
    for (const k of knownKeys) {
      if (isValidKey(k)) return k;
    }
    return "";
  }

  function currentSelectedKey() {
    return safe(() => {
      const selected = tabs.find((b) => b.getAttribute("aria-selected") === "true");
      return canonicalKey(selected && selected.dataset && selected.dataset.tab);
    }, "");
  }

  // ───────────────────────── Embed / Iframe Integration ─────────────────────────
  // Ziel: Crawlab/Superset erst laden, wenn der Tab aktiv ist (Lazy Load),
  //       plus "Neu laden" Button robust unterstützen.

  const EMBED_STATE = {
    // target -> { lastUrl, lastLoadTs, timerId }
    items: new Map(),
  };

  function _getEmbedState(target) {
    const t = canonicalEmbedTarget(target);
    if (!t) return null;
    if (!EMBED_STATE.items.has(t)) {
      EMBED_STATE.items.set(t, { lastUrl: "", lastLoadTs: 0, timerId: 0 });
    }
    return EMBED_STATE.items.get(t);
  }

  function canonicalEmbedTarget(raw) {
    const s = String(raw || "").trim().toLowerCase();
    if (!s) return "";
    if (s === "crawlab") return "crawlab";
    if (s === "superset") return "superset";
    return s; // erlaubt spätere Erweiterungen
  }

  function getEmbedNodes(target, rootEl) {
    const t = canonicalEmbedTarget(target);
    if (!t) return { target: "", iframe: null, fallback: null, openLink: null };

    const scope = rootEl || document;
    const iframe = qs(`#embed-${t}`, scope) || qs(`iframe.embed-iframe[data-target="${t}"]`, scope);
    const fallback = qs(`[data-fallback="${t}"]`, scope);
    // open link: in header vorhanden (data-action="open-external")
    // pro Tab gibt es einen Link; wir suchen den nächsten innerhalb desselben embed-shell
    let openLink = null;
    safe(() => {
      const shell = iframe ? iframe.closest(".embed-shell") : qs(`.embed-shell[data-embed="${t}"]`, scope);
      openLink = shell ? qs(`a[data-action="open-external"]`, shell) : null;
    });

    return { target: t, iframe, fallback, openLink };
  }

  function setFallbackVisible(fallbackEl, visible) {
    safe(() => {
      if (!fallbackEl) return;
      if (visible) fallbackEl.removeAttribute("hidden");
      else fallbackEl.setAttribute("hidden", "");
    });
  }

  function appendQueryParam(url, key, value) {
    return safe(() => {
      if (!url) return "";
      const u = new URL(url, location.href);
      u.searchParams.set(String(key), String(value));
      return u.toString();
    }, url);
  }

  function isProbablyLoaded(iframe) {
    // Heuristik:
    // - Wenn Zugriff auf contentWindow.location.href möglich und == about:blank -> NICHT geladen / blockiert
    // - Wenn Zugriff wirft (cross-origin) -> wahrscheinlich geladen (oder zumindest navigiert)
    // - Wenn href != about:blank -> geladen
    return safe(() => {
      if (!iframe) return false;
      const cw = iframe.contentWindow;
      if (!cw) return false;

      try {
        const href = String(cw.location && cw.location.href ? cw.location.href : "");
        if (!href) return false;
        if (href === "about:blank") return false;
        return true;
      } catch (e) {
        // cross-origin access -> treat as loaded/navigation succeeded
        return true;
      }
    }, false);
  }

  function hookIframeOnce(iframe, target) {
    safe(() => {
      if (!iframe) return;
      if (iframe.dataset && iframe.dataset.hooked === "1") return;
      if (iframe.dataset) iframe.dataset.hooked = "1";

      iframe.addEventListener("load", () => {
        const t = canonicalEmbedTarget(target);
        const nodes = getEmbedNodes(t, document);
        const ok = isProbablyLoaded(nodes.iframe);

        // wenn blockiert (about:blank), Fallback sichtbar lassen
        setFallbackVisible(nodes.fallback, !ok);

        // Status an Parent (optional für Logging)
        post({
          t: "admin.embed",
          chatId: CHAT_ID,
          target: t,
          status: ok ? "loaded" : "blocked",
          ts: nowISO(),
        });
      }, { passive: true });

      iframe.addEventListener("error", () => {
        const t = canonicalEmbedTarget(target);
        const nodes = getEmbedNodes(t, document);
        setFallbackVisible(nodes.fallback, true);
        post({
          t: "admin.embed",
          chatId: CHAT_ID,
          target: t,
          status: "error",
          ts: nowISO(),
        });
      }, { passive: true });
    });
  }

  function loadEmbed(target, opts) {
    const options = opts || {};
    const force = !!options.force;
    const source = asStr(options.source, force ? "force" : "auto");

    const t = canonicalEmbedTarget(target);
    if (!t) return false;

    const nodes = getEmbedNodes(t, document);
    const iframe = nodes.iframe;
    if (!iframe) return false;

    hookIframeOnce(iframe, t);

    // data-src ist die stabile interne Gateway-Route
    const baseUrl = asStr(safe(() => iframe.dataset && iframe.dataset.src, ""), "") || asStr(iframe.getAttribute("data-src"), "");
    if (!baseUrl) {
      setFallbackVisible(nodes.fallback, true);
      post({ t: "admin.embed", chatId: CHAT_ID, target: t, status: "missing-src", ts: nowISO() });
      return false;
    }

    const st = _getEmbedState(t);
    const cacheBust = Date.now();
    const url = appendQueryParam(baseUrl, "r", cacheBust);

    // Optional: Open-Link auch aktualisieren (zeigt immer auf Gateway)
    safe(() => {
      if (nodes.openLink) nodes.openLink.href = baseUrl;
    });

    // Wenn bereits geladen und nicht force -> nichts tun
    const alreadyLoaded = safe(() => String(iframe.dataset.loaded || "0") === "1", false);
    if (alreadyLoaded && !force) {
      // Fallback ggf. verstecken, falls iframe tatsächlich geladen ist
      const ok = isProbablyLoaded(iframe);
      setFallbackVisible(nodes.fallback, !ok);
      return true;
    }

    // UI: Fallback sichtbar lassen, bis "loaded" heuristisch bestätigt ist
    setFallbackVisible(nodes.fallback, true);

    // Force reload: erst auf about:blank, dann neu setzen (hilft bei manchen Browsern)
    safe(() => {
      if (force) {
        iframe.dataset.loaded = "0";
        try { iframe.src = "about:blank"; } catch (e) {}
      }
    });

    // minimal delay für force, damit about:blank "greift"
    const delayMs = force ? 60 : 0;

    safe(() => {
      window.setTimeout(() => {
        safe(() => {
          iframe.src = url;
          iframe.dataset.loaded = "1";
        });

        if (st) {
          st.lastUrl = url;
          st.lastLoadTs = Date.now();
        }

        post({
          t: "admin.embed",
          chatId: CHAT_ID,
          target: t,
          status: "loading",
          source,
          ts: nowISO(),
        });

        // Safety timer: wenn nach X Sekunden noch about:blank -> fallback bleibt sichtbar
        safe(() => {
          const timeoutMs = 5500;
          if (!st) return;
          if (st.timerId) {
            try { clearTimeout(st.timerId); } catch (e) {}
            st.timerId = 0;
          }
          st.timerId = window.setTimeout(() => {
            const ok = isProbablyLoaded(iframe);
            setFallbackVisible(nodes.fallback, !ok);
            post({
              t: "admin.embed",
              chatId: CHAT_ID,
              target: t,
              status: ok ? "loaded" : "blocked",
              source: "timeout-check",
              ts: nowISO(),
            });
          }, timeoutMs);
        });
      }, delayMs);
    });

    return true;
  }

  function ensureEmbedsInPanel(panelEl, opts) {
    const options = opts || {};
    const force = !!options.force;
    const source = asStr(options.source, force ? "force" : "auto");

    if (!panelEl) return;

    // Alle embed-iframes in diesem Panel laden (Skalierung: später weitere Integrationen)
    const iframes = qsa("iframe.embed-iframe[data-src]", panelEl);
    safe(() => iframes.forEach((ifr) => {
      // target aus id "embed-xxx"
      const id = asStr(ifr.id, "");
      let t = "";
      if (id.startsWith("embed-")) t = id.slice("embed-".length);
      else t = asStr(ifr.dataset && ifr.dataset.target, "");
      if (!t) return;
      loadEmbed(t, { force, source });
    }));
  }

  // ───────────────────────── setActive (mit Embed-Hook) ─────────────────────────
  function setActive(key, opts) {
    const options = opts || {};
    const source = String(options.source || "unknown");
    const focus = !!options.focus;

    let k = canonicalKey(key);
    if (!isValidKey(k)) k = "";

    if (!k) {
      k = getDefaultKey();
      if (!isValidKey(k)) return;
    }

    const prev = currentSelectedKey();
    if (prev === k && source !== "force") {
      if (focus) safe(() => tabsByKey.get(k).focus({ preventScroll: true }));
      safe(() => tabsByKey.get(k).scrollIntoView({ block: "nearest", inline: "nearest" }));

      // falls ein Embed-Panel aktiv ist, stelle sicher, dass iframe geladen ist
      safe(() => {
        const panel = panelsByKey.get(k);
        if (panel && !panel.hasAttribute("hidden")) {
          ensureEmbedsInPanel(panel, { force: false, source: "reselect" });
        }
      });
      return;
    }

    // Update aria/tabindex on tabs
    safe(() => tabs.forEach((btn) => {
      const btnKey = canonicalKey(btn.dataset.tab);
      const selected = (btnKey === k);

      btn.setAttribute("aria-selected", selected ? "true" : "false");
      btn.tabIndex = selected ? 0 : -1;

      if (selected) btn.dataset.active = "1";
      else delete btn.dataset.active;
    }));

    // Update panels
    let activePanelEl = null;
    safe(() => panels.forEach((panel) => {
      const pk = canonicalKey(panel.dataset.panel);
      const show = (pk === k);
      if (show) {
        panel.removeAttribute("hidden");
        activePanelEl = panel;
      } else {
        panel.setAttribute("hidden", "");
      }
    }));

    // Mark root for debugging/CSS hooks
    safe(() => { if (root) root.dataset.activeTab = k; });

    // Persist
    storageSet(STORAGE_ACTIVE_TAB, k);
    writeHash(k);

    // Ensure visible in scrollable tab bar
    safe(() => {
      const btn = tabsByKey.get(k);
      if (btn && btn.scrollIntoView) btn.scrollIntoView({ block: "nearest", inline: "nearest" });
    });

    if (focus) {
      safe(() => {
        const btn = tabsByKey.get(k);
        if (btn && btn.focus) btn.focus({ preventScroll: true });
      });
    }

    // Lazy-load embeds (Crawlab/Superset) sobald Panel aktiv ist
    safe(() => {
      if (activePanelEl) ensureEmbedsInPanel(activePanelEl, { force: false, source: "tab-activate" });
    });

    // Notify parent
    post({
      t: "admin.tab",
      chatId: CHAT_ID,
      tab: k,
      prev: prev || "",
      source,
      ts: nowISO()
    });
  }

  // ───────────────────────── Events: Click / Keyboard / Hash ─────────────────────────
  function onTabClick(ev) {
    safe(() => {
      const target = ev && ev.target;
      const btn = target && target.closest ? target.closest(".admin-tab[data-tab]") : null;
      if (!btn) return;

      ev.preventDefault();
      const k = canonicalKey(btn.dataset.tab);
      setActive(k, { source: "click", focus: false });
    });
  }

  function focusTabByIndex(idx) {
    safe(() => {
      const n = tabs.length;
      if (!n) return;

      let i = idx;
      if (i < 0) i = n - 1;
      if (i >= n) i = 0;

      const btn = tabs[i];
      if (!btn) return;

      // "Automatic activation": Fokuswechsel aktiviert Tab sofort
      const k = canonicalKey(btn.dataset.tab);
      setActive(k, { source: "kbd", focus: true });
    });
  }

  function onTabKeydown(ev) {
    safe(() => {
      if (!ev) return;
      const key = ev.key;

      const activeEl = document.activeElement;
      const rawIndex = tabs.indexOf(activeEl);
      const curIndex = (rawIndex >= 0) ? rawIndex : Math.max(0, tabs.findIndex(b => b.getAttribute("aria-selected") === "true"));

      if (key === "ArrowRight" || key === "Right") {
        ev.preventDefault();
        focusTabByIndex(curIndex + 1);
        return;
      }
      if (key === "ArrowLeft" || key === "Left") {
        ev.preventDefault();
        focusTabByIndex(curIndex - 1);
        return;
      }
      if (key === "Home") {
        ev.preventDefault();
        focusTabByIndex(0);
        return;
      }
      if (key === "End") {
        ev.preventDefault();
        focusTabByIndex(tabs.length - 1);
        return;
      }

      // Manual activation fallback (Enter/Space)
      if (key === "Enter" || key === " " || key === "Spacebar") {
        const btn = activeEl && activeEl.classList && activeEl.classList.contains("admin-tab") ? activeEl : null;
        if (!btn) return;
        ev.preventDefault();
        const k = canonicalKey(btn.dataset.tab);
        setActive(k, { source: "kbd-activate", focus: true });
      }
    });
  }

  function onHashChange() {
    safe(() => {
      const k = getKeyFromHash();
      if (isValidKey(k)) setActive(k, { source: "hash", focus: false });
    });
  }

  // Sync active tab if another context changes localStorage (optional)
  safe(() => window.addEventListener("storage", (e) => {
    if (!e) return;
    if (e.key === STORAGE_ACTIVE_TAB) {
      const k = canonicalKey(e.newValue);
      if (isValidKey(k)) setActive(k, { source: "storage", focus: false });
    }
  }));

  // Parent -> iframe command channel (optional)
  safe(() => window.addEventListener("message", (ev) => {
    // Defensive: nur gleiche Origin akzeptieren (wenn möglich).
    // In Dev-Setups kann origin abweichen; ggf. später lockern.
    if (ev && ev.origin && ev.origin !== location.origin) return;

    const msg = ev && ev.data;
    if (!msg || typeof msg !== "object") return;

    if (msg.t === "admin.setTab") {
      const k = canonicalKey(msg.tab || msg.key || msg.value);
      if (isValidKey(k)) setActive(k, { source: "message", focus: !!msg.focus });
      return;
    }

    if (msg.t === "admin.getState") {
      post({
        t: "admin.state",
        chatId: CHAT_ID,
        tab: currentSelectedKey() || "",
        knownTabs: knownKeys.slice(0, 32),
        theme: safe(() => document.documentElement.getAttribute("data-theme") || "", ""),
        ts: nowISO()
      });
      return;
    }

    if (msg.t === "admin.setTheme") {
      applyTheme(msg.theme);
      return;
    }

    if (msg.t === "admin.reloadEmbed") {
      const t = canonicalEmbedTarget(msg.target || msg.name || "");
      if (t) loadEmbed(t, { force: true, source: "message" });
      return;
    }
  }));

  // ───────────────────────── Embed actions (Reload buttons) ─────────────────────────
  function onDocClick(ev) {
    safe(() => {
      const el = ev && ev.target;
      if (!el || !el.closest) return;

      const btn = el.closest('button[data-action="reload-embed"]');
      if (btn) {
        ev.preventDefault();
        const t = canonicalEmbedTarget(btn.dataset.target || btn.getAttribute("data-target") || "");
        if (!t) return;

        // sicherstellen, dass entsprechendes Panel sichtbar ist oder wird
        loadEmbed(t, { force: true, source: "button" });
        return;
      }
    });
  }

  // ───────────────────────── Boot ─────────────────────────
  function boot() {
    safe(() => tablist.addEventListener("click", onTabClick));
    safe(() => tablist.addEventListener("keydown", onTabKeydown));
    safe(() => window.addEventListener("hashchange", onHashChange));
    safe(() => document.addEventListener("click", onDocClick, { passive: false }));

    // initial tab resolution order:
    // 1) query (?tab=...)
    // 2) hash (#tab=...)
    // 3) localStorage
    // 4) default first tab
    let initial = "";
    initial = getKeyFromQuery();
    if (!isValidKey(initial)) initial = getKeyFromHash();
    if (!isValidKey(initial)) initial = canonicalKey(storageGet(STORAGE_ACTIVE_TAB));
    if (!isValidKey(initial)) initial = getDefaultKey();

    setActive(initial, { source: "boot", focus: false });

    post({ t: "admin.status", chatId: CHAT_ID, status: "ready", tab: currentSelectedKey() || "", ts: nowISO() });
  }

  // Global error guards (zusätzlich zu admin.html)
  safe(() => window.addEventListener("error", (e) => {
    const msg = (e && e.message) ? String(e.message).slice(0, 240) : "Unbekannter Fehler";
    post({ t: "admin.status", chatId: CHAT_ID, status: "error", error: msg, ts: nowISO() });
  }));
  safe(() => window.addEventListener("unhandledrejection", (e) => {
    const reason = (e && e.reason) ? (e.reason.message || String(e.reason)) : "Unhandled Promise Rejection";
    post({ t: "admin.status", chatId: CHAT_ID, status: "error", error: String(reason).slice(0, 240), ts: nowISO() });
  }));

  boot();

  // Optional: kleine API für Debug/Tests
  safe(() => {
    window.ADMIN_UI = {
      setTab: (k) => setActive(k, { source: "api", focus: true }),
      getTab: () => currentSelectedKey() || "",
      knownTabs: () => knownKeys.slice(),
      applyTheme: (t) => applyTheme(t),

      // Embed helpers
      loadEmbed: (t) => loadEmbed(t, { force: false, source: "api" }),
      reloadEmbed: (t) => loadEmbed(t, { force: true, source: "api" }),
    };
  });
})();