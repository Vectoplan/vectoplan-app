// services/app/static/js/chat/layout.js
// Layout controller for Chat+Viewer page:
// - Desktop/Tablet: chat column collapsible via data-chat-collapsed
// - Mobile (<= BREAKPOINT): chat as left drawer via data-chat-drawer
//
// Robustness goals:
// - Safe no-op if DOM elements missing (old templates)
// - Idempotent init (double-inits prevented)
// - Storage guards (try/catch wrappers)
// - Breakpoint change handling (desktop <-> mobile)
// - a11y basics: aria-expanded, aria-hidden, focus restore, basic focus trap on mobile drawer
//
// Expected DOM IDs (chat_viewer.html):
// - .app-wrap
// - #chatToggleBtn (toolbar toggle)
// - #chatCloseBtn  (close inside chat header)
// - #chatBackdrop  (drawer backdrop)
// - #chat-panel    (chat container)
//
// CSS expects:
// - .app-wrap[data-chat-collapsed="true"]  -> desktop collapse
// - .app-wrap[data-chat-drawer="open"]     -> mobile drawer open

export const DEFAULT_BREAKPOINT_PX = 900;

function safe(fn, fallback) {
  try { return fn(); } catch { return fallback; }
}

function $(id) {
  return safe(() => document.getElementById(id), null);
}

function q(sel, root = document) {
  return safe(() => root.querySelector(sel), null);
}

const storage = {
  get(key, def = null) {
    return safe(() => {
      const v = localStorage.getItem(key);
      return (v === null || v === undefined) ? def : v;
    }, def);
  },
  set(key, val) {
    safe(() => localStorage.setItem(key, String(val)));
  },
  del(key) {
    safe(() => localStorage.removeItem(key));
  }
};

function boolFromStorage(v) {
  const s = String(v ?? "").toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

function setAttr(el, name, val) {
  safe(() => { if (el) el.setAttribute(name, String(val)); });
}

function setDataset(el, key, val) {
  safe(() => { if (el && el.dataset) el.dataset[key] = String(val); });
}

function getDataset(el, key, def = "") {
  return safe(() => (el && el.dataset && (key in el.dataset) ? el.dataset[key] : def), def);
}

function isMobileNow(mq) {
  return safe(() => !!mq && !!mq.matches, false);
}

function getFocusable(root) {
  return safe(() => {
    if (!root) return [];
    const sel = [
      "textarea:not([disabled])",
      "input:not([disabled])",
      "button:not([disabled])",
      "a[href]",
      "[tabindex]:not([tabindex='-1'])"
    ].join(",");
    return Array.from(root.querySelectorAll(sel));
  }, []);
}

function firstFocusable(root) {
  return safe(() => getFocusable(root)[0] || null, null);
}

function focusEl(el) {
  safe(() => {
    if (!el || typeof el.focus !== "function") return;
    el.focus({ preventScroll: true });
  });
}

function nowTs() {
  return safe(() => Date.now(), 0);
}

/** Optional: cache-bust local URLs by adding ?r=... */
function cacheBustLocalUrl(u) {
  return safe(() => {
    const s = String(u || "").trim();
    if (!s) return s;

    // only same-origin / relative
    if (!s.startsWith("/")) return s;

    const url = new URL(s, location.origin);
    url.searchParams.set("r", String(nowTs()));
    return url.pathname + "?" + url.searchParams.toString() + (url.hash || "");
  }, u);
}

export function initChatLayout(opts = {}) {
  const BREAKPOINT = Number(opts.breakpointPx || DEFAULT_BREAKPOINT_PX);

  // global init guard
  if (safe(() => window.__VECTOAI_CHAT_LAYOUT_WIRED__ === true, false)) return;
  safe(() => { window.__VECTOAI_CHAT_LAYOUT_WIRED__ = true; });

  const wrap = q(".app-wrap");
  if (!wrap) return;

  const chatPanel = $("chat-panel") || q(".chat-wrap", wrap);
  const toggleBtn = $("chatToggleBtn");
  const closeBtn  = $("chatCloseBtn");
  const backdrop  = $("chatBackdrop");

  // Ensure basic defaults exist (avoid undefined dataset usage)
  if (getDataset(wrap, "chatCollapsed", "") === "") setDataset(wrap, "chatCollapsed", "false");
  if (getDataset(wrap, "chatDrawer", "") === "") setDataset(wrap, "chatDrawer", "closed");

  // Breakpoint media query
  const mq = safe(() => window.matchMedia ? window.matchMedia(`(max-width: ${BREAKPOINT}px)`) : null, null);

  // Persisted desktop collapse preference (do NOT apply on mobile)
  // Keep compatibility with your pre-init script key:
  const collapsedPrefKey = "chat_collapsed";

  // Focus restore
  let _lastFocus = null;

  // Focus trap (mobile drawer only)
  let _trapActive = false;

  function readCollapsedPref() {
    const raw = storage.get(collapsedPrefKey, "0");
    return boolFromStorage(raw);
  }
  function writeCollapsedPref(isCollapsed) {
    storage.set(collapsedPrefKey, isCollapsed ? "1" : "0");
  }

  function isDrawerOpen() {
    return getDataset(wrap, "chatDrawer", "closed") === "open";
  }
  function isCollapsedApplied() {
    return getDataset(wrap, "chatCollapsed", "false") === "true";
  }

  function applyA11yState() {
    const mobile = isMobileNow(mq);

    // expanded meaning:
    // - mobile: drawer open?
    // - desktop: not collapsed?
    const expanded = mobile ? isDrawerOpen() : !isCollapsedApplied();

    if (toggleBtn) setAttr(toggleBtn, "aria-expanded", expanded ? "true" : "false");
    if (toggleBtn && chatPanel?.id) setAttr(toggleBtn, "aria-controls", chatPanel.id);

    if (chatPanel) setAttr(chatPanel, "aria-hidden", expanded ? "false" : "true");

    // Backdrop is decorative
    if (backdrop) setAttr(backdrop, "aria-hidden", "true");
  }

  function enableTrap() {
    _trapActive = true;
  }
  function disableTrap() {
    _trapActive = false;
  }

  function closeDrawer({ focusReturn = true } = {}) {
    setDataset(wrap, "chatDrawer", "closed");
    disableTrap();
    applyA11yState();
    if (focusReturn) focusEl(_lastFocus || toggleBtn);
    _lastFocus = null;
  }

  function openDrawer({ focusChat = true } = {}) {
    // Ensure desktop-collapse isn't hiding chat via display:none rule
    setDataset(wrap, "chatCollapsed", "false");
    setDataset(wrap, "chatDrawer", "open");
    enableTrap();
    applyA11yState();

    if (focusChat) {
      // Prefer message input
      const msg = $("message") || firstFocusable(chatPanel);
      focusEl(msg);
    }
  }

  function setCollapsedDesktop(isCollapsed, { focus = false } = {}) {
    // Desktop-only
    setDataset(wrap, "chatCollapsed", isCollapsed ? "true" : "false");
    writeCollapsedPref(!!isCollapsed);

    // Drawer should be closed on desktop
    setDataset(wrap, "chatDrawer", "closed");
    disableTrap();

    applyA11yState();

    if (focus) {
      if (!isCollapsed) {
        const msg = $("message") || firstFocusable(chatPanel);
        focusEl(msg);
      } else {
        focusEl(toggleBtn);
      }
    }
  }

  function toggleDesktopCollapse() {
    const next = !isCollapsedApplied();
    setCollapsedDesktop(next, { focus: true });
  }

  function toggleMobileDrawer() {
    if (isDrawerOpen()) closeDrawer({ focusReturn: true });
    else openDrawer({ focusChat: true });
  }

  function onToggleClick(ev) {
    safe(() => ev?.preventDefault?.());
    _lastFocus = safe(() => document.activeElement, null);

    const mobile = isMobileNow(mq);
    if (mobile) toggleMobileDrawer();
    else toggleDesktopCollapse();
  }

  function onCloseClick(ev) {
    safe(() => ev?.preventDefault?.());
    _lastFocus = safe(() => document.activeElement, null);

    const mobile = isMobileNow(mq);
    if (mobile) closeDrawer({ focusReturn: true });
    else setCollapsedDesktop(true, { focus: true });
  }

  function onBackdropClick(ev) {
    safe(() => ev?.preventDefault?.());
    if (isMobileNow(mq) && isDrawerOpen()) closeDrawer({ focusReturn: true });
  }

  function onKeyDown(ev) {
    const key = safe(() => ev?.key, "");

    // ESC closes drawer (mobile)
    if (key === "Escape") {
      if (isMobileNow(mq) && isDrawerOpen()) {
        safe(() => ev.preventDefault());
        closeDrawer({ focusReturn: true });
      }
      return;
    }

    // Focus trap (mobile + drawer open)
    if (key === "Tab" && _trapActive && isMobileNow(mq) && isDrawerOpen()) {
      const focusables = getFocusable(chatPanel);
      if (!focusables.length) return;

      const active = safe(() => document.activeElement, null);
      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      if (ev.shiftKey) {
        if (active === first || !chatPanel.contains(active)) {
          safe(() => ev.preventDefault());
          focusEl(last);
        }
      } else {
        if (active === last) {
          safe(() => ev.preventDefault());
          focusEl(first);
        }
      }
    }
  }

  // Breakpoint changes:
  // - Entering mobile: force chatCollapsed=false, keep drawer closed (unless already open)
  // - Leaving mobile: close drawer, restore desktop collapsed preference
  function applyForBreakpointChange() {
    const mobile = isMobileNow(mq);

    if (mobile) {
      setDataset(wrap, "chatCollapsed", "false");
      if (getDataset(wrap, "chatDrawer", "") !== "open") setDataset(wrap, "chatDrawer", "closed");
    } else {
      setDataset(wrap, "chatDrawer", "closed");
      disableTrap();
      setCollapsedDesktop(readCollapsedPref(), { focus: false });
    }

    applyA11yState();
  }

  // Debounced resize
  let resizeTimer = null;
  function onResize() {
    safe(() => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        resizeTimer = null;
        applyForBreakpointChange();
      }, 120);
    });
  }

  // Wire events (idempotent per element)
  safe(() => {
    if (toggleBtn && !toggleBtn._layoutWired) {
      toggleBtn._layoutWired = true;
      toggleBtn.addEventListener("click", onToggleClick);
    }
    if (closeBtn && !closeBtn._layoutWired) {
      closeBtn._layoutWired = true;
      closeBtn.addEventListener("click", onCloseClick);
    }
    if (backdrop && !backdrop._layoutWired) {
      backdrop._layoutWired = true;
      backdrop.addEventListener("click", onBackdropClick, { passive: false });
    }
  });

  // Global listeners once
  safe(() => {
    if (!window.__VECTOAI_CHAT_LAYOUT_KEYDOWN__) {
      window.__VECTOAI_CHAT_LAYOUT_KEYDOWN__ = true;
      document.addEventListener("keydown", onKeyDown);
    }
    if (!window.__VECTOAI_CHAT_LAYOUT_RESIZE__) {
      window.__VECTOAI_CHAT_LAYOUT_RESIZE__ = true;
      window.addEventListener("resize", onResize, { passive: true });
    }
  });

  // MQ change
  safe(() => {
    if (!mq) return;
    const handler = () => applyForBreakpointChange();
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", handler, { passive: true });
    else if (typeof mq.addListener === "function") mq.addListener(handler);
  });

  // Initial apply:
  // Respect pre-init dataset on desktop, but fix mobile immediately.
  applyForBreakpointChange();

  // Provide tiny debug surface
  safe(() => {
    window.__VECTOAI_CHAT_LAYOUT__ = {
      breakpointPx: BREAKPOINT,
      openDrawer: () => openDrawer({ focusChat: true }),
      closeDrawer: () => closeDrawer({ focusReturn: true }),
      toggle: () => onToggleClick({ preventDefault(){} }),
      apply: () => applyForBreakpointChange(),
      cacheBustLocalUrl,
    };
  });
}

/* ───────────────────────── Optional auto-init ─────────────────────────
   Dieses Modul ist robust, auch wenn main.js init vergisst.
   main.js sollte trotzdem initChatLayout() aufrufen (idempotent guard schützt).
------------------------------------------------------------------- */
safe(() => {
  const run = () => initChatLayout({});
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run, { once: true });
  } else {
    run();
  }
});