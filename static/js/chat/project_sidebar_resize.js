/* services/vectoplan-app/static/js/chat/project_sidebar_resize.js */

/*
  VECTOPLAN Project Sidebar Resize Controller

  Zweck:
  - Isolierte Resize-Logik für die linke Projekt-/Workspace-Leiste.
  - Frontend-only.
  - Keine Backend-Abhängigkeit.
  - Kein Build-System nötig.
  - Robust gegen fehlendes APP_CONFIG, fehlendes localStorage, fehlende CSS-Variablen.
  - Berührt Pointer-/Mouse-Events nur während aktivem Drag am Resize-Griff.
  - Vermeidet Konflikte mit Editor-iframe und Pointer Lock.
  - Kann automatisch starten, kann aber auch später explizit aus main.js initialisiert werden.

  Erwartetes Markup:
  - [data-vp-project-sidebar]
  - [data-project-sidebar-resize-handle]

  Erwartete optionale data-Attribute am Sidebar-Root:
  - data-project-sidebar-storage-key
  - data-project-sidebar-default-width
  - data-project-sidebar-min-width
  - data-project-sidebar-max-width
  - data-project-sidebar-collapsed-width
  - data-project-sidebar-collapsed

  Gespeicherter Zustand:
  localStorage[storageKey] = {
    version: 1,
    width: 280,
    updatedAt: "..."
  }

  Globale Exports:
  - window.VectoplanProjectSidebarResize
  - window.__VECTOPLAN_PROJECT_SIDEBAR_RESIZE__

  Hauptmethoden:
  - init(rootOrOptions)
  - initAll(options)
  - destroy(root)
  - getController(root)
  - applyWidth(root, width, options)
  - readState(storageKey)
  - writeState(storageKey, patch)
*/

(function initVectoplanProjectSidebarResize(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectSidebarResize";
  var LEGACY_EXPORT_NAME = "__VECTOPLAN_PROJECT_SIDEBAR_RESIZE__";

  var INTERNAL_VERSION = 1;
  var DEFAULT_STORAGE_KEY = "vectoplan.projectSidebar.v1";

  var DEFAULT_WIDTH = 280;
  var DEFAULT_MIN_WIDTH = 220;
  var DEFAULT_MAX_WIDTH = 420;
  var DEFAULT_COLLAPSED_WIDTH = 64;

  var KEY_STEP = 16;
  var KEY_STEP_LARGE = 48;

  var ROOT_SELECTOR = "[data-vp-project-sidebar]";
  var HANDLE_SELECTOR = "[data-project-sidebar-resize-handle]";

  var CLASS_RESIZING = "is-resizing";
  var CLASS_COLLAPSED = "is-collapsed";
  var CLASS_EXPANDED = "is-expanded";

  var DATA_CONTROLLER_KEY = "__vpProjectSidebarResizeController__";

  var controllers = [];
  var hasWindowResizeListener = false;

  function getWindow() {
    try {
      return global || window;
    } catch (error) {
      return {};
    }
  }

  function getDocument() {
    try {
      return getWindow().document || document || null;
    } catch (error) {
      return null;
    }
  }

  function nowIso() {
    try {
      return new Date().toISOString();
    } catch (error) {
      return "";
    }
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function isElement(value) {
    try {
      return !!value && value.nodeType === 1;
    } catch (error) {
      return false;
    }
  }

  function trimString(value, fallback) {
    try {
      if (value === null || value === undefined) {
        return fallback || "";
      }

      var text = String(value).trim();
      return text || fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function toNumberSafe(value, fallback) {
    try {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }

      if (typeof value === "string" && value.trim() !== "") {
        var parsed = Number(value);

        if (Number.isFinite(parsed)) {
          return parsed;
        }
      }

      return fallback;
    } catch (error) {
      return fallback;
    }
  }

  function clamp(value, min, max) {
    try {
      var number = toNumberSafe(value, min);

      if (Number.isFinite(min) && number < min) {
        return min;
      }

      if (Number.isFinite(max) && number > max) {
        return max;
      }

      return number;
    } catch (error) {
      return min;
    }
  }

  function safeJsonParse(value, fallback) {
    try {
      if (typeof value !== "string" || value.trim() === "") {
        return fallback;
      }

      var parsed = JSON.parse(value);
      return parsed === undefined ? fallback : parsed;
    } catch (error) {
      return fallback;
    }
  }

  function safeJsonStringify(value) {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return "";
    }
  }

  function hasLocalStorage() {
    try {
      var win = getWindow();

      if (!win.localStorage) {
        return false;
      }

      var key = "__vp_project_sidebar_resize_storage_test__";
      win.localStorage.setItem(key, "1");
      win.localStorage.removeItem(key);

      return true;
    } catch (error) {
      return false;
    }
  }

  function readState(storageKey) {
    try {
      var key = trimString(storageKey, DEFAULT_STORAGE_KEY);

      if (!hasLocalStorage()) {
        return {};
      }

      var raw = getWindow().localStorage.getItem(key);
      var parsed = safeJsonParse(raw, {});

      if (!isObject(parsed)) {
        return {};
      }

      return parsed;
    } catch (error) {
      return {};
    }
  }

  function writeFullState(storageKey, state) {
    try {
      var key = trimString(storageKey, DEFAULT_STORAGE_KEY);

      if (!hasLocalStorage()) {
        return false;
      }

      var payload = isObject(state) ? state : {};
      payload.version = payload.version || INTERNAL_VERSION;
      payload.updatedAt = nowIso();

      getWindow().localStorage.setItem(key, safeJsonStringify(payload));
      return true;
    } catch (error) {
      return false;
    }
  }

  function writeState(storageKey, patch) {
    try {
      var current = readState(storageKey);
      var update = isObject(patch) ? patch : {};
      var next = {};

      Object.keys(current).forEach(function copyCurrent(key) {
        next[key] = current[key];
      });

      Object.keys(update).forEach(function applyPatch(key) {
        next[key] = update[key];
      });

      next.version = next.version || INTERNAL_VERSION;
      next.updatedAt = nowIso();

      return writeFullState(storageKey, next);
    } catch (error) {
      return false;
    }
  }

  function getData(root, name, fallback) {
    try {
      if (!root || !root.dataset) {
        return fallback;
      }

      if (Object.prototype.hasOwnProperty.call(root.dataset, name)) {
        return root.dataset[name];
      }

      return fallback;
    } catch (error) {
      return fallback;
    }
  }

  function getCssNumber(root, variableName, fallback) {
    try {
      var win = getWindow();

      if (!root || !win.getComputedStyle) {
        return fallback;
      }

      var value = win.getComputedStyle(root).getPropertyValue(variableName);
      var parsed = parseFloat(value);

      if (Number.isFinite(parsed)) {
        return parsed;
      }

      return fallback;
    } catch (error) {
      return fallback;
    }
  }

  function getProjectSidebarDataConfig(root) {
    try {
      var win = getWindow();
      var dataApi = win.VectoplanProjectSidebarData || win.__VECTOPLAN_PROJECT_SIDEBAR_DATA__;

      if (dataApi && typeof dataApi.getConfig === "function") {
        return dataApi.getConfig({
          element: root
        });
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function getRootConfig(root, options) {
    try {
      var opts = isObject(options) ? options : {};
      var dataConfig = getProjectSidebarDataConfig(root);

      var storageKey =
        trimString(opts.storageKey, "") ||
        trimString(opts.storage_key, "") ||
        trimString(dataConfig.storageKey, "") ||
        trimString(getData(root, "projectSidebarStorageKey", ""), "") ||
        DEFAULT_STORAGE_KEY;

      var minWidth = clamp(
        opts.minWidth ||
          opts.min_width ||
          dataConfig.minWidth ||
          getData(root, "projectSidebarMinWidth", "") ||
          getCssNumber(root, "--vp-project-sidebar-min-width", DEFAULT_MIN_WIDTH),
        160,
        960
      );

      var maxWidth = clamp(
        opts.maxWidth ||
          opts.max_width ||
          dataConfig.maxWidth ||
          getData(root, "projectSidebarMaxWidth", "") ||
          getCssNumber(root, "--vp-project-sidebar-max-width", DEFAULT_MAX_WIDTH),
        minWidth,
        1200
      );

      var defaultWidth = clamp(
        opts.defaultWidth ||
          opts.default_width ||
          dataConfig.defaultWidth ||
          getData(root, "projectSidebarDefaultWidth", "") ||
          getCssNumber(root, "--vp-project-sidebar-width", DEFAULT_WIDTH),
        minWidth,
        maxWidth
      );

      var collapsedWidth = clamp(
        opts.collapsedWidth ||
          opts.collapsed_width ||
          dataConfig.collapsedWidth ||
          getData(root, "projectSidebarCollapsedWidth", "") ||
          getCssNumber(root, "--vp-project-sidebar-collapsed-width", DEFAULT_COLLAPSED_WIDTH),
        48,
        160
      );

      return {
        storageKey: storageKey,
        minWidth: minWidth,
        maxWidth: maxWidth,
        defaultWidth: defaultWidth,
        collapsedWidth: collapsedWidth,
        persist: opts.persist !== false,
        dataConfig: dataConfig
      };
    } catch (error) {
      return {
        storageKey: DEFAULT_STORAGE_KEY,
        minWidth: DEFAULT_MIN_WIDTH,
        maxWidth: DEFAULT_MAX_WIDTH,
        defaultWidth: DEFAULT_WIDTH,
        collapsedWidth: DEFAULT_COLLAPSED_WIDTH,
        persist: true,
        dataConfig: {}
      };
    }
  }

  function isCollapsed(root) {
    try {
      if (!root) {
        return false;
      }

      if (root.classList && root.classList.contains(CLASS_COLLAPSED)) {
        return true;
      }

      var attr = root.getAttribute("data-project-sidebar-collapsed");
      return attr === "true" || attr === "1";
    } catch (error) {
      return false;
    }
  }

  function setNumberAttributes(handle, config, width) {
    try {
      if (!handle) {
        return;
      }

      handle.setAttribute("aria-valuemin", String(Math.round(config.minWidth)));
      handle.setAttribute("aria-valuemax", String(Math.round(config.maxWidth)));
      handle.setAttribute("aria-valuenow", String(Math.round(width)));
      handle.setAttribute("aria-valuetext", "Breite " + Math.round(width) + " Pixel");
    } catch (error) {
      /* aria update is best-effort */
    }
  }

  function setRootWidth(root, width, config, options) {
    try {
      if (!root) {
        return DEFAULT_WIDTH;
      }

      var cfg = config || getRootConfig(root, options);
      var safeWidth = clamp(width, cfg.minWidth, cfg.maxWidth);

      root.style.setProperty("--vp-project-sidebar-width", Math.round(safeWidth) + "px");
      root.setAttribute("data-project-sidebar-width", String(Math.round(safeWidth)));

      var handle = root.querySelector ? root.querySelector(HANDLE_SELECTOR) : null;
      setNumberAttributes(handle, cfg, safeWidth);

      return safeWidth;
    } catch (error) {
      return DEFAULT_WIDTH;
    }
  }

  function applyWidth(root, width, options) {
    try {
      if (!isElement(root)) {
        return DEFAULT_WIDTH;
      }

      var config = getRootConfig(root, options);
      var safeWidth = setRootWidth(root, width, config, options);

      if (config.persist && (!options || options.persist !== false)) {
        writeState(config.storageKey, {
          width: Math.round(safeWidth)
        });
      }

      return safeWidth;
    } catch (error) {
      return DEFAULT_WIDTH;
    }
  }

  function getInitialWidth(root, options) {
    try {
      var config = getRootConfig(root, options);
      var stored = readState(config.storageKey);
      var storedWidth = toNumberSafe(stored.width, NaN);

      if (Number.isFinite(storedWidth)) {
        return clamp(storedWidth, config.minWidth, config.maxWidth);
      }

      var attrWidth = toNumberSafe(getData(root, "projectSidebarWidth", ""), NaN);

      if (Number.isFinite(attrWidth)) {
        return clamp(attrWidth, config.minWidth, config.maxWidth);
      }

      var cssWidth = getCssNumber(root, "--vp-project-sidebar-width", NaN);

      if (Number.isFinite(cssWidth)) {
        return clamp(cssWidth, config.minWidth, config.maxWidth);
      }

      return clamp(config.defaultWidth, config.minWidth, config.maxWidth);
    } catch (error) {
      return DEFAULT_WIDTH;
    }
  }

  function normalizePointerClientX(event) {
    try {
      if (!event) {
        return 0;
      }

      if (Number.isFinite(event.clientX)) {
        return event.clientX;
      }

      if (event.touches && event.touches[0] && Number.isFinite(event.touches[0].clientX)) {
        return event.touches[0].clientX;
      }

      if (
        event.changedTouches &&
        event.changedTouches[0] &&
        Number.isFinite(event.changedTouches[0].clientX)
      ) {
        return event.changedTouches[0].clientX;
      }

      return 0;
    } catch (error) {
      return 0;
    }
  }

  function addListener(target, type, handler, options, cleanupList) {
    try {
      if (!target || !target.addEventListener || typeof handler !== "function") {
        return;
      }

      target.addEventListener(type, handler, options || false);

      if (Array.isArray(cleanupList)) {
        cleanupList.push(function removeListener() {
          try {
            target.removeEventListener(type, handler, options || false);
          } catch (error) {
            /* ignore cleanup error */
          }
        });
      }
    } catch (error) {
      /* ignore listener error */
    }
  }

  function removeControllerFromList(controller) {
    try {
      controllers = controllers.filter(function keep(item) {
        return item !== controller;
      });
    } catch (error) {
      /* ignore */
    }
  }

  function dispatch(root, name, detail) {
    try {
      if (!root || typeof CustomEvent !== "function") {
        return false;
      }

      return root.dispatchEvent(
        new CustomEvent(name, {
          bubbles: true,
          cancelable: false,
          detail: detail || {}
        })
      );
    } catch (error) {
      return false;
    }
  }

  function setDocumentResizingState(isActive) {
    try {
      var doc = getDocument();

      if (!doc || !doc.documentElement) {
        return;
      }

      if (isActive) {
        doc.documentElement.setAttribute("data-project-sidebar-resizing", "true");
      } else {
        doc.documentElement.removeAttribute("data-project-sidebar-resizing");
      }
    } catch (error) {
      /* best-effort */
    }
  }

  function createController(root, options) {
    var controller = null;

    try {
      if (!isElement(root)) {
        return null;
      }

      if (root[DATA_CONTROLLER_KEY]) {
        return root[DATA_CONTROLLER_KEY];
      }

      var handle = root.querySelector ? root.querySelector(HANDLE_SELECTOR) : null;

      if (!handle) {
        return null;
      }

      var config = getRootConfig(root, options);
      var cleanup = [];
      var dragCleanup = [];

      var state = {
        root: root,
        handle: handle,
        config: config,
        isDragging: false,
        startX: 0,
        startWidth: 0,
        currentWidth: getInitialWidth(root, options),
        lastAppliedWidth: 0,
        pointerId: null,
        destroyed: false
      };

      function refreshConfig() {
        try {
          state.config = getRootConfig(root, options);
          return state.config;
        } catch (error) {
          return state.config || config;
        }
      }

      function apply(newWidth, applyOptions) {
        try {
          var cfg = refreshConfig();
          var safeWidth = setRootWidth(root, newWidth, cfg, applyOptions);

          state.currentWidth = safeWidth;
          state.lastAppliedWidth = safeWidth;

          if (!applyOptions || applyOptions.persist !== false) {
            if (cfg.persist) {
              writeState(cfg.storageKey, {
                width: Math.round(safeWidth)
              });
            }
          }

          dispatch(root, "vectoplan:project-sidebar:resize", {
            width: safeWidth,
            minWidth: cfg.minWidth,
            maxWidth: cfg.maxWidth,
            source: applyOptions && applyOptions.source ? applyOptions.source : "resize"
          });

          return safeWidth;
        } catch (error) {
          return state.currentWidth;
        }
      }

      function cancelNativeSelection(event) {
        try {
          if (event && event.cancelable) {
            event.preventDefault();
          }
        } catch (error) {
          /* ignore */
        }
      }

      function clearDragListeners() {
        try {
          dragCleanup.forEach(function runCleanup(fn) {
            try {
              fn();
            } catch (error) {
              /* ignore individual cleanup */
            }
          });
          dragCleanup = [];
        } catch (error) {
          dragCleanup = [];
        }
      }

      function finishDrag(event, reason) {
        try {
          if (!state.isDragging) {
            return;
          }

          state.isDragging = false;

          root.classList.remove(CLASS_RESIZING);
          root.removeAttribute("data-project-sidebar-is-resizing");
          setDocumentResizingState(false);

          try {
            if (
              event &&
              event.pointerId !== undefined &&
              handle.releasePointerCapture &&
              state.pointerId !== null
            ) {
              handle.releasePointerCapture(state.pointerId);
            }
          } catch (captureError) {
            /* pointer capture release is best-effort */
          }

          clearDragListeners();

          apply(state.currentWidth, {
            source: reason || "drag-end",
            persist: true
          });

          dispatch(root, "vectoplan:project-sidebar:resize-end", {
            width: state.currentWidth,
            reason: reason || "end"
          });

          state.pointerId = null;
        } catch (error) {
          clearDragListeners();
          root.classList.remove(CLASS_RESIZING);
          root.removeAttribute("data-project-sidebar-is-resizing");
          setDocumentResizingState(false);
          state.isDragging = false;
          state.pointerId = null;
        }
      }

      function onDragMove(event) {
        try {
          if (!state.isDragging || isCollapsed(root)) {
            return;
          }

          cancelNativeSelection(event);

          var cfg = refreshConfig();
          var currentX = normalizePointerClientX(event);
          var delta = currentX - state.startX;
          var nextWidth = clamp(state.startWidth + delta, cfg.minWidth, cfg.maxWidth);

          setRootWidth(root, nextWidth, cfg, {
            persist: false
          });

          state.currentWidth = nextWidth;

          dispatch(root, "vectoplan:project-sidebar:resize-preview", {
            width: nextWidth,
            minWidth: cfg.minWidth,
            maxWidth: cfg.maxWidth,
            delta: delta
          });
        } catch (error) {
          finishDrag(event, "error");
        }
      }

      function onDragEnd(event) {
        finishDrag(event, "drag-end");
      }

      function onDragCancel(event) {
        finishDrag(event, "drag-cancel");
      }

      function startDrag(event) {
        try {
          if (state.destroyed || isCollapsed(root)) {
            return;
          }

          if (event && event.button !== undefined && event.button !== 0) {
            return;
          }

          cancelNativeSelection(event);

          var cfg = refreshConfig();
          var startX = normalizePointerClientX(event);

          state.isDragging = true;
          state.startX = startX;
          state.startWidth = clamp(state.currentWidth || getInitialWidth(root, options), cfg.minWidth, cfg.maxWidth);
          state.currentWidth = state.startWidth;
          state.pointerId = event && event.pointerId !== undefined ? event.pointerId : null;

          root.classList.add(CLASS_RESIZING);
          root.setAttribute("data-project-sidebar-is-resizing", "true");
          setDocumentResizingState(true);

          try {
            if (event && event.pointerId !== undefined && handle.setPointerCapture) {
              handle.setPointerCapture(event.pointerId);
            }
          } catch (captureError) {
            /* pointer capture is best-effort */
          }

          clearDragListeners();

          var doc = getDocument();
          var win = getWindow();

          if (win.PointerEvent) {
            addListener(doc, "pointermove", onDragMove, { passive: false }, dragCleanup);
            addListener(doc, "pointerup", onDragEnd, { passive: false }, dragCleanup);
            addListener(doc, "pointercancel", onDragCancel, { passive: false }, dragCleanup);
          } else {
            addListener(doc, "mousemove", onDragMove, { passive: false }, dragCleanup);
            addListener(doc, "mouseup", onDragEnd, { passive: false }, dragCleanup);
            addListener(doc, "touchmove", onDragMove, { passive: false }, dragCleanup);
            addListener(doc, "touchend", onDragEnd, { passive: false }, dragCleanup);
            addListener(doc, "touchcancel", onDragCancel, { passive: false }, dragCleanup);
          }

          addListener(win, "blur", onDragCancel, false, dragCleanup);

          dispatch(root, "vectoplan:project-sidebar:resize-start", {
            width: state.startWidth,
            minWidth: cfg.minWidth,
            maxWidth: cfg.maxWidth,
            startX: startX
          });
        } catch (error) {
          finishDrag(event, "error");
        }
      }

      function onPointerDown(event) {
        startDrag(event);
      }

      function onMouseDown(event) {
        try {
          var win = getWindow();

          if (win.PointerEvent) {
            return;
          }

          startDrag(event);
        } catch (error) {
          startDrag(event);
        }
      }

      function onTouchStart(event) {
        try {
          var win = getWindow();

          if (win.PointerEvent) {
            return;
          }

          startDrag(event);
        } catch (error) {
          startDrag(event);
        }
      }

      function onKeyDown(event) {
        try {
          if (!event || isCollapsed(root)) {
            return;
          }

          var cfg = refreshConfig();
          var key = event.key;
          var step = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
          var nextWidth = state.currentWidth || getInitialWidth(root, options);
          var handled = true;

          if (key === "ArrowLeft") {
            nextWidth -= step;
          } else if (key === "ArrowRight") {
            nextWidth += step;
          } else if (key === "Home") {
            nextWidth = cfg.minWidth;
          } else if (key === "End") {
            nextWidth = cfg.maxWidth;
          } else if (key === "Enter") {
            nextWidth = cfg.defaultWidth;
          } else {
            handled = false;
          }

          if (!handled) {
            return;
          }

          if (event.cancelable) {
            event.preventDefault();
          }

          nextWidth = clamp(nextWidth, cfg.minWidth, cfg.maxWidth);

          apply(nextWidth, {
            source: "keyboard",
            persist: true
          });
        } catch (error) {
          /* keyboard resize should never break page */
        }
      }

      function onDoubleClick(event) {
        try {
          if (event && event.cancelable) {
            event.preventDefault();
          }

          var cfg = refreshConfig();
          apply(cfg.defaultWidth, {
            source: "double-click-reset",
            persist: true
          });
        } catch (error) {
          /* ignore */
        }
      }

      function onCollapsedChanged() {
        try {
          if (isCollapsed(root)) {
            if (state.isDragging) {
              finishDrag(null, "collapsed");
            }

            return;
          }

          var cfg = refreshConfig();
          var current = state.currentWidth || getInitialWidth(root, options);
          setRootWidth(root, current, cfg, {
            persist: false
          });
        } catch (error) {
          /* ignore */
        }
      }

      function restore() {
        try {
          var cfg = refreshConfig();
          var width = getInitialWidth(root, options);

          state.currentWidth = clamp(width, cfg.minWidth, cfg.maxWidth);
          setRootWidth(root, state.currentWidth, cfg, {
            persist: false
          });

          handle.setAttribute("aria-valuemin", String(Math.round(cfg.minWidth)));
          handle.setAttribute("aria-valuemax", String(Math.round(cfg.maxWidth)));
          handle.setAttribute("aria-valuenow", String(Math.round(state.currentWidth)));
          handle.setAttribute("aria-valuetext", "Breite " + Math.round(state.currentWidth) + " Pixel");

          if (!handle.getAttribute("role")) {
            handle.setAttribute("role", "separator");
          }

          if (!handle.getAttribute("aria-orientation")) {
            handle.setAttribute("aria-orientation", "vertical");
          }

          if (!handle.getAttribute("tabindex")) {
            handle.setAttribute("tabindex", "0");
          }
        } catch (error) {
          /* restore is best-effort */
        }
      }

      function destroy() {
        try {
          state.destroyed = true;

          finishDrag(null, "destroy");

          cleanup.forEach(function runCleanup(fn) {
            try {
              fn();
            } catch (error) {
              /* ignore individual cleanup */
            }
          });

          cleanup = [];

          if (root[DATA_CONTROLLER_KEY] === controller) {
            try {
              delete root[DATA_CONTROLLER_KEY];
            } catch (deleteError) {
              root[DATA_CONTROLLER_KEY] = null;
            }
          }

          removeControllerFromList(controller);

          dispatch(root, "vectoplan:project-sidebar:resize-destroy", {
            width: state.currentWidth
          });
        } catch (error) {
          /* destroy should never throw */
        }
      }

      controller = {
        version: INTERNAL_VERSION,
        root: root,
        handle: handle,
        state: state,
        applyWidth: apply,
        restore: restore,
        refreshConfig: refreshConfig,
        onCollapsedChanged: onCollapsedChanged,
        destroy: destroy,
        getSnapshot: function getSnapshot() {
          try {
            return {
              version: INTERNAL_VERSION,
              width: state.currentWidth,
              minWidth: state.config.minWidth,
              maxWidth: state.config.maxWidth,
              defaultWidth: state.config.defaultWidth,
              collapsedWidth: state.config.collapsedWidth,
              isDragging: state.isDragging,
              isCollapsed: isCollapsed(root),
              storageKey: state.config.storageKey,
              destroyed: state.destroyed
            };
          } catch (error) {
            return {
              version: INTERNAL_VERSION,
              destroyed: true
            };
          }
        }
      };

      root[DATA_CONTROLLER_KEY] = controller;

      restore();

      var win = getWindow();

      if (win.PointerEvent) {
        addListener(handle, "pointerdown", onPointerDown, { passive: false }, cleanup);
      } else {
        addListener(handle, "mousedown", onMouseDown, { passive: false }, cleanup);
        addListener(handle, "touchstart", onTouchStart, { passive: false }, cleanup);
      }

      addListener(handle, "keydown", onKeyDown, false, cleanup);
      addListener(handle, "dblclick", onDoubleClick, false, cleanup);

      addListener(root, "vectoplan:project-sidebar:collapsed-change", onCollapsedChanged, false, cleanup);
      addListener(root, "vectoplan:project-sidebar:expanded-change", onCollapsedChanged, false, cleanup);

      controllers.push(controller);

      dispatch(root, "vectoplan:project-sidebar:resize-ready", {
        width: state.currentWidth,
        minWidth: config.minWidth,
        maxWidth: config.maxWidth
      });

      return controller;
    } catch (error) {
      return null;
    }
  }

  function normalizeRootInput(rootOrOptions) {
    try {
      if (isElement(rootOrOptions)) {
        return {
          root: rootOrOptions,
          options: {}
        };
      }

      if (isObject(rootOrOptions)) {
        if (isElement(rootOrOptions.root)) {
          return {
            root: rootOrOptions.root,
            options: rootOrOptions
          };
        }

        if (isElement(rootOrOptions.element)) {
          return {
            root: rootOrOptions.element,
            options: rootOrOptions
          };
        }

        if (typeof rootOrOptions.selector === "string") {
          var doc = getDocument();
          var found = doc && doc.querySelector ? doc.querySelector(rootOrOptions.selector) : null;

          return {
            root: found,
            options: rootOrOptions
          };
        }
      }

      var fallbackDoc = getDocument();
      var fallbackRoot = fallbackDoc && fallbackDoc.querySelector ? fallbackDoc.querySelector(ROOT_SELECTOR) : null;

      return {
        root: fallbackRoot,
        options: isObject(rootOrOptions) ? rootOrOptions : {}
      };
    } catch (error) {
      return {
        root: null,
        options: {}
      };
    }
  }

  function init(rootOrOptions) {
    try {
      var normalized = normalizeRootInput(rootOrOptions);

      if (!normalized.root) {
        return null;
      }

      return createController(normalized.root, normalized.options);
    } catch (error) {
      return null;
    }
  }

  function initAll(options) {
    try {
      var doc = getDocument();

      if (!doc || !doc.querySelectorAll) {
        return [];
      }

      var selector = isObject(options) && typeof options.selector === "string"
        ? options.selector
        : ROOT_SELECTOR;

      var roots = Array.prototype.slice.call(doc.querySelectorAll(selector));
      var result = [];

      roots.forEach(function initOne(root) {
        try {
          var controller = createController(root, options || {});

          if (controller) {
            result.push(controller);
          }
        } catch (error) {
          /* ignore broken sidebar root */
        }
      });

      ensureWindowResizeListener();

      return result;
    } catch (error) {
      return [];
    }
  }

  function getController(root) {
    try {
      if (!isElement(root)) {
        return null;
      }

      return root[DATA_CONTROLLER_KEY] || null;
    } catch (error) {
      return null;
    }
  }

  function destroy(root) {
    try {
      if (isElement(root)) {
        var controller = getController(root);

        if (controller && typeof controller.destroy === "function") {
          controller.destroy();
          return true;
        }

        return false;
      }

      controllers.slice().forEach(function destroyOne(controller) {
        try {
          controller.destroy();
        } catch (error) {
          /* ignore */
        }
      });

      controllers = [];
      return true;
    } catch (error) {
      return false;
    }
  }

  function refreshAll() {
    try {
      controllers.slice().forEach(function refreshOne(controller) {
        try {
          if (controller && typeof controller.restore === "function") {
            controller.restore();
          }
        } catch (error) {
          /* ignore one broken controller */
        }
      });
    } catch (error) {
      /* ignore */
    }
  }

  function ensureWindowResizeListener() {
    try {
      if (hasWindowResizeListener) {
        return;
      }

      var win = getWindow();

      if (!win || !win.addEventListener) {
        return;
      }

      var scheduled = false;

      win.addEventListener(
        "resize",
        function onWindowResize() {
          try {
            if (scheduled) {
              return;
            }

            scheduled = true;

            win.requestAnimationFrame(function runRefresh() {
              scheduled = false;
              refreshAll();
            });
          } catch (error) {
            scheduled = false;
            refreshAll();
          }
        },
        { passive: true }
      );

      hasWindowResizeListener = true;
    } catch (error) {
      /* ignore */
    }
  }

  function autoInit() {
    try {
      var doc = getDocument();

      if (!doc) {
        return;
      }

      if (doc.readyState === "loading") {
        doc.addEventListener(
          "DOMContentLoaded",
          function onReady() {
            initAll({
              auto: true
            });
          },
          { once: true }
        );
      } else {
        initAll({
          auto: true
        });
      }
    } catch (error) {
      /* auto init is best-effort */
    }
  }

  function createDebugSnapshot() {
    try {
      return {
        version: INTERNAL_VERSION,
        controllerCount: controllers.length,
        controllers: controllers.map(function mapController(controller) {
          try {
            return controller.getSnapshot();
          } catch (error) {
            return {
              error: true
            };
          }
        })
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        controllerCount: 0,
        controllers: []
      };
    }
  }

  var api = {
    version: INTERNAL_VERSION,

    constants: {
      storageKey: DEFAULT_STORAGE_KEY,
      rootSelector: ROOT_SELECTOR,
      handleSelector: HANDLE_SELECTOR,
      defaultWidth: DEFAULT_WIDTH,
      defaultMinWidth: DEFAULT_MIN_WIDTH,
      defaultMaxWidth: DEFAULT_MAX_WIDTH,
      defaultCollapsedWidth: DEFAULT_COLLAPSED_WIDTH
    },

    init: init,
    initAll: initAll,
    destroy: destroy,
    getController: getController,
    applyWidth: applyWidth,
    readState: readState,
    writeState: writeState,
    refreshAll: refreshAll,
    createDebugSnapshot: createDebugSnapshot,

    _private: {
      getRootConfig: getRootConfig,
      getInitialWidth: getInitialWidth,
      setRootWidth: setRootWidth,
      isCollapsed: isCollapsed,
      clamp: clamp,
      trimString: trimString,
      toNumberSafe: toNumberSafe
    }
  };

  try {
    global[EXPORT_NAME] = api;
    global[LEGACY_EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectSidebarResize = api;
  } catch (error) {
    /* export is best-effort */
  }

  autoInit();
})(window);