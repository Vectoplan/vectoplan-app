/* services/vectoplan-app/static/js/chat/project_sidebar.js */

/*
  VECTOPLAN Project Sidebar Controller

  Zweck:
  - Linke Projekt-/Workspace-Navigation initialisieren.
  - Primärdaten kommen live aus /v1/projects/sidebar über project_sidebar_data.loadItemsAsync().
  - Fallback bleibt kompatibel: APP_CONFIG, globale Items, localStorage, bestehende DOM-Items.
  - Refresh lädt die Projektliste erneut aus der API.
  - Projekt-Speichern/-Erstellen triggert live Sidebar-Refresh.
  - Navigation bleibt native Browser-Navigation zu href.
  - Keine direkten Backenddaten außer Sidebar-API.
  - Kein Zugriff auf Chunk-, Editor-, 2D- oder LV-Daten.

  Erwartete optionale Abhängigkeiten:
  - window.VectoplanProjectSidebarData
  - window.VectoplanProjectSidebarResize

  Globale Exports:
  - window.VectoplanProjectSidebar
  - window.__VECTOPLAN_PROJECT_SIDEBAR__
*/

(function initVectoplanProjectSidebar(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectSidebar";
  var LEGACY_EXPORT_NAME = "__VECTOPLAN_PROJECT_SIDEBAR__";

  var INTERNAL_VERSION = 2;
  var DEFAULT_STORAGE_KEY = "vectoplan.projectSidebar.v1";
  var DEFAULT_ROUTE_BASE = "/";
  var DEFAULT_CREATE_PROJECT_URL = "/project=new";

  var ROOT_SELECTOR = "[data-vp-project-sidebar]";
  var ITEM_SELECTOR = "[data-project-sidebar-item]";
  var TEMPLATE_SELECTOR = "[data-project-sidebar-item-template]";
  var RESIZE_HANDLE_SELECTOR = "[data-project-sidebar-resize-handle]";

  var CLASS_COLLAPSED = "is-collapsed";
  var CLASS_EXPANDED = "is-expanded";
  var CLASS_LOADING = "is-loading";
  var CLASS_ERROR = "has-error";
  var CLASS_MOBILE_OPEN = "is-mobile-open";
  var CLASS_HIDDEN_BY_SEARCH = "is-hidden-by-search";

  var DATA_CONTROLLER_KEY = "__vpProjectSidebarController__";

  var DEFAULT_CURRENT_TITLE = "Aktuelles Projekt";
  var DEFAULT_CURRENT_SUBTITLE = "Projekt definieren";

  var PROJECT_EVENTS = [
    "vectoplan:project:saved",
    "vectoplan:project:created",
    "vectoplan:project:updated",
    "vectoplan:project:configured",
    "vectoplan:project:deleted"
  ];

  var controllers = [];
  var hasGlobalKeyHandler = false;

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

  function isArray(value) {
    return Array.isArray(value);
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

  function toBooleanSafe(value, fallback) {
    try {
      if (value === true || value === false) {
        return value;
      }

      if (value === 1 || value === "1") {
        return true;
      }

      if (value === 0 || value === "0") {
        return false;
      }

      if (typeof value === "string") {
        var normalized = value.trim().toLowerCase();

        if (
          normalized === "true" ||
          normalized === "yes" ||
          normalized === "on" ||
          normalized === "enabled" ||
          normalized === "ja"
        ) {
          return true;
        }

        if (
          normalized === "false" ||
          normalized === "no" ||
          normalized === "off" ||
          normalized === "disabled" ||
          normalized === "nein"
        ) {
          return false;
        }
      }

      return !!fallback;
    } catch (error) {
      return !!fallback;
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

      var key = "__vp_project_sidebar_controller_storage_test__";
      win.localStorage.setItem(key, "1");
      win.localStorage.removeItem(key);

      return true;
    } catch (error) {
      return false;
    }
  }

  function readStorage(storageKey) {
    try {
      var key = trimString(storageKey, DEFAULT_STORAGE_KEY);

      if (!hasLocalStorage()) {
        return {};
      }

      var parsed = safeJsonParse(getWindow().localStorage.getItem(key), {});

      if (!isObject(parsed)) {
        return {};
      }

      return parsed;
    } catch (error) {
      return {};
    }
  }

  function writeStorage(storageKey, patch) {
    try {
      var key = trimString(storageKey, DEFAULT_STORAGE_KEY);

      if (!hasLocalStorage()) {
        return false;
      }

      var current = readStorage(key);
      var update = isObject(patch) ? patch : {};
      var next = {};

      Object.keys(current).forEach(function copyCurrent(prop) {
        next[prop] = current[prop];
      });

      Object.keys(update).forEach(function applyPatch(prop) {
        next[prop] = update[prop];
      });

      next.version = next.version || INTERNAL_VERSION;
      next.updatedAt = nowIso();

      getWindow().localStorage.setItem(key, safeJsonStringify(next));
      return true;
    } catch (error) {
      return false;
    }
  }

  function getDataApi() {
    try {
      var win = getWindow();
      return win.VectoplanProjectSidebarData || win.__VECTOPLAN_PROJECT_SIDEBAR_DATA__ || null;
    } catch (error) {
      return null;
    }
  }

  function getResizeApi() {
    try {
      var win = getWindow();
      return win.VectoplanProjectSidebarResize || win.__VECTOPLAN_PROJECT_SIDEBAR_RESIZE__ || null;
    } catch (error) {
      return null;
    }
  }

  function getAppConfig() {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.getAppConfig === "function") {
        return dataApi.getAppConfig();
      }

      var win = getWindow();

      if (isObject(win.APP_CONFIG)) {
        return win.APP_CONFIG;
      }

      if (isObject(win.__APP_CONFIG__)) {
        return win.__APP_CONFIG__;
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function getSidebarConfigFromApp(appConfig) {
    try {
      var config = appConfig || getAppConfig();

      if (isObject(config.projectSidebar)) {
        return config.projectSidebar;
      }

      if (isObject(config.project_sidebar)) {
        return config.project_sidebar;
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function getDatasetValue(element, key, fallback) {
    try {
      if (!element || !element.dataset) {
        return fallback;
      }

      if (Object.prototype.hasOwnProperty.call(element.dataset, key)) {
        return element.dataset[key];
      }

      return fallback;
    } catch (error) {
      return fallback;
    }
  }

  function queryAppRoot() {
    try {
      var doc = getDocument();

      if (!doc || !doc.querySelector) {
        return null;
      }

      return doc.querySelector(".app-wrap") || doc.body || null;
    } catch (error) {
      return null;
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

  function normalizeError(error) {
    try {
      if (!error) {
        return {
          name: "Error",
          message: "Unknown error",
          stack: ""
        };
      }

      return {
        name: trimString(error.name, "Error"),
        message: trimString(error.message, String(error)),
        stack: trimString(error.stack, ""),
        status: error.status || error.statusCode || null,
        code: trimString(error.code, "")
      };
    } catch (innerError) {
      return {
        name: "Error",
        message: "Unknown error",
        stack: ""
      };
    }
  }

  function normalizeInitial(title) {
    try {
      var text = trimString(title, "P");
      var first = text.charAt(0).toUpperCase();

      return first || "P";
    } catch (error) {
      return "P";
    }
  }

  function getRouteBase() {
    try {
      var appConfig = getAppConfig();
      var sidebarConfig = getSidebarConfigFromApp(appConfig);

      return (
        trimString(sidebarConfig.routeBase, "") ||
        trimString(sidebarConfig.route_base, "") ||
        trimString(appConfig.projectRouteBase, "") ||
        trimString(appConfig.project_route_base, "") ||
        DEFAULT_ROUTE_BASE
      );
    } catch (error) {
      return DEFAULT_ROUTE_BASE;
    }
  }

  function getCreateProjectUrl(config) {
    try {
      var cfg = config || {};
      var appConfig = cfg.appConfig || getAppConfig();
      var sidebarConfig = cfg.sidebarConfig || getSidebarConfigFromApp(appConfig);

      return (
        trimString(sidebarConfig.createProjectUrl, "") ||
        trimString(sidebarConfig.create_project_url, "") ||
        trimString(appConfig.projectNewUrl, "") ||
        trimString(appConfig.project_new_url, "") ||
        trimString(appConfig.projectNew, "") ||
        trimString(appConfig.project_new, "") ||
        DEFAULT_CREATE_PROJECT_URL
      );
    } catch (error) {
      return DEFAULT_CREATE_PROJECT_URL;
    }
  }

  function buildProjectHref(projectId, config) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.buildProjectHref === "function") {
        return dataApi.buildProjectHref(projectId, {
          config: config
        });
      }

      var id = trimString(projectId, "");

      if (!id || id === "new") {
        return DEFAULT_CREATE_PROJECT_URL;
      }

      var routeBase = getRouteBase();

      if (!routeBase || routeBase === "/") {
        return "/project=" + encodeURIComponent(id);
      }

      if (routeBase.indexOf("project=") !== -1) {
        return routeBase.replace(/project=[^/?#&]*/g, "project=" + encodeURIComponent(id));
      }

      var separator = routeBase.indexOf("?") === -1 ? "?" : "&";
      return routeBase + separator + "project=" + encodeURIComponent(id);
    } catch (error) {
      return DEFAULT_CREATE_PROJECT_URL;
    }
  }

  function buildChatHref(chatId) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.buildChatHref === "function") {
        return dataApi.buildChatHref(chatId);
      }

      var routeBase = getRouteBase();
      var id = trimString(chatId, "");

      if (!id) {
        return routeBase;
      }

      return routeBase + (routeBase.indexOf("?") === -1 ? "?" : "&") + "chat_id=" + encodeURIComponent(id);
    } catch (error) {
      return DEFAULT_ROUTE_BASE;
    }
  }

  function getCurrentProjectId(root, options) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.getCurrentProjectId === "function") {
        return dataApi.getCurrentProjectId({
          element: root,
          currentProjectId: options && (
            options.currentProjectId ||
            options.current_project_id ||
            options.projectPublicId ||
            options.project_public_id
          )
        });
      }

      var appConfig = getAppConfig();
      var sidebarConfig = getSidebarConfigFromApp(appConfig);
      var appRoot = queryAppRoot();

      return (
        trimString(options && (options.currentProjectId || options.current_project_id), "") ||
        trimString(sidebarConfig.currentProjectId, "") ||
        trimString(sidebarConfig.current_project_id, "") ||
        trimString(appConfig.projectPublicId, "") ||
        trimString(appConfig.project_public_id, "") ||
        trimString(appConfig.currentProjectId, "") ||
        trimString(appConfig.current_project_id, "") ||
        trimString(getDatasetValue(root, "currentProjectId", ""), "") ||
        trimString(getDatasetValue(appRoot, "projectPublicId", ""), "") ||
        trimString(getDatasetValue(appRoot, "projectId", ""), "")
      );
    } catch (error) {
      return "";
    }
  }

  function getCurrentChatId(root, options) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.getCurrentChatId === "function") {
        return dataApi.getCurrentChatId({
          element: root,
          currentChatId: options && (options.currentChatId || options.current_chat_id)
        });
      }

      var appConfig = getAppConfig();
      var sidebarConfig = getSidebarConfigFromApp(appConfig);

      return (
        trimString(options && (options.currentChatId || options.current_chat_id), "") ||
        trimString(sidebarConfig.currentChatId, "") ||
        trimString(sidebarConfig.current_chat_id, "") ||
        trimString(appConfig.chatId, "") ||
        trimString(appConfig.chat_id, "") ||
        trimString(getDatasetValue(root, "projectSidebarCurrentChatId", ""), "")
      );
    } catch (error) {
      return "";
    }
  }

  function getConfig(root, options) {
    try {
      var opts = isObject(options) ? options : {};
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.getConfig === "function") {
        var dataConfig = dataApi.getConfig({
          element: root,
          currentChatId: opts.currentChatId || opts.current_chat_id,
          currentProjectId:
            opts.currentProjectId ||
            opts.current_project_id ||
            opts.projectPublicId ||
            opts.project_public_id,
          items: opts.items,
          storageKey: opts.storageKey || opts.storage_key,
          apiPath: opts.apiPath || opts.api_path,
          fetchEnabled: opts.fetchEnabled
        });

        if (isObject(dataConfig)) {
          dataConfig.createProjectUrl = dataConfig.createProjectUrl || getCreateProjectUrl(dataConfig);
          return dataConfig;
        }
      }

      var appConfig = getAppConfig();
      var sidebarConfig = getSidebarConfigFromApp(appConfig);

      return {
        version: INTERNAL_VERSION,
        enabled: toBooleanSafe(
          opts.enabled !== undefined ? opts.enabled : sidebarConfig.enabled,
          true
        ),
        fetchEnabled: toBooleanSafe(
          opts.fetchEnabled !== undefined
            ? opts.fetchEnabled
            : sidebarConfig.fetchEnabled !== undefined
              ? sidebarConfig.fetchEnabled
              : true,
          true
        ),
        storageKey:
          trimString(opts.storageKey, "") ||
          trimString(opts.storage_key, "") ||
          trimString(sidebarConfig.storageKey, "") ||
          trimString(sidebarConfig.storage_key, "") ||
          trimString(getDatasetValue(root, "projectSidebarStorageKey", ""), "") ||
          DEFAULT_STORAGE_KEY,
        routeBase: getRouteBase(),
        currentChatId: getCurrentChatId(root, opts),
        currentProjectId: getCurrentProjectId(root, opts),
        currentTitle:
          trimString(opts.currentTitle, "") ||
          trimString(opts.current_title, "") ||
          trimString(sidebarConfig.currentTitle, "") ||
          trimString(sidebarConfig.current_title, "") ||
          trimString(getDatasetValue(root, "projectSidebarCurrentTitle", ""), "") ||
          DEFAULT_CURRENT_TITLE,
        currentSubtitle:
          trimString(opts.currentSubtitle, "") ||
          trimString(opts.current_subtitle, "") ||
          trimString(sidebarConfig.currentSubtitle, "") ||
          trimString(sidebarConfig.current_subtitle, "") ||
          DEFAULT_CURRENT_SUBTITLE,
        createProjectUrl: getCreateProjectUrl({
          appConfig: appConfig,
          sidebarConfig: sidebarConfig
        }),
        appConfig: appConfig,
        sidebarConfig: sidebarConfig,
        element: root
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        enabled: true,
        fetchEnabled: true,
        storageKey: DEFAULT_STORAGE_KEY,
        routeBase: DEFAULT_ROUTE_BASE,
        currentChatId: "",
        currentProjectId: "",
        currentTitle: DEFAULT_CURRENT_TITLE,
        currentSubtitle: DEFAULT_CURRENT_SUBTITLE,
        createProjectUrl: DEFAULT_CREATE_PROJECT_URL,
        appConfig: {},
        sidebarConfig: {},
        element: root || null
      };
    }
  }

  function normalizeItem(raw, config) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.normalizeItem === "function") {
        return dataApi.normalizeItem(raw, {
          config: config
        });
      }

      var item = isObject(raw) ? raw : {};

      var projectId =
        trimString(item.projectId, "") ||
        trimString(item.project_id, "") ||
        trimString(item.public_id, "") ||
        trimString(item.publicId, "") ||
        trimString(item.id, "");

      var chatId =
        trimString(item.chatId, "") ||
        trimString(item.chat_id, "") ||
        trimString(item.conversationId, "") ||
        trimString(item.conversation_id, "");

      var title =
        trimString(item.title, "") ||
        trimString(item.name, "") ||
        trimString(item.label, "") ||
        (projectId && projectId === config.currentProjectId ? config.currentTitle : "") ||
        (chatId && chatId === config.currentChatId ? config.currentTitle : "") ||
        "Unbenanntes Projekt";

      var subtitle =
        trimString(item.subtitle, "") ||
        trimString(item.address_text, "") ||
        trimString(item.description, "") ||
        trimString(item.summary, "") ||
        trimString(item.setup_status, "") ||
        "Projekt";

      var href =
        trimString(item.href, "") ||
        trimString(item.url, "") ||
        trimString(item.link, "") ||
        (projectId ? buildProjectHref(projectId, config) : "") ||
        buildChatHref(chatId);

      var isConfigured = toBooleanSafe(
        item.isConfigured !== undefined
          ? item.isConfigured
          : item.is_configured !== undefined
            ? item.is_configured
            : item.setup_status === "configured",
        false
      );

      return {
        id: projectId || chatId || title,
        projectId: projectId,
        project_id: projectId,
        public_id: projectId,
        title: title,
        subtitle: subtitle,
        chatId: chatId,
        chat_id: chatId,
        conversationId: chatId,
        conversation_id: chatId,
        href: href,
        isActive: toBooleanSafe(item.isActive || item.is_active, false) ||
          (!!projectId && projectId === config.currentProjectId) ||
          (!!chatId && chatId === config.currentChatId),
        is_active: toBooleanSafe(item.isActive || item.is_active, false) ||
          (!!projectId && projectId === config.currentProjectId) ||
          (!!chatId && chatId === config.currentChatId),
        isConfigured: isConfigured,
        is_configured: isConfigured,
        setupStatus: item.setupStatus || item.setup_status || (isConfigured ? "configured" : "draft"),
        setup_status: item.setup_status || item.setupStatus || (isConfigured ? "configured" : "draft"),
        source: trimString(item.source, "client"),
        initial: trimString(item.initial, "") || normalizeInitial(title),
        updatedAt: trimString(item.updatedAt || item.updated_at, ""),
        raw: item
      };
    } catch (error) {
      return null;
    }
  }

  function parseExistingDomItems(root, config) {
    try {
      if (!root || !root.querySelectorAll) {
        return [];
      }

      var nodes = Array.prototype.slice.call(root.querySelectorAll(ITEM_SELECTOR));

      return nodes
        .map(function mapNode(node) {
          try {
            return normalizeItem(
              {
                id:
                  getDatasetValue(node, "projectId", "") ||
                  getDatasetValue(node, "projectPublicId", ""),
                projectId:
                  getDatasetValue(node, "projectId", "") ||
                  getDatasetValue(node, "projectPublicId", ""),
                chatId: getDatasetValue(node, "projectChatId", ""),
                title:
                  getDatasetValue(node, "projectTitle", "") ||
                  trimString(node.getAttribute("title"), "") ||
                  trimString(node.textContent, ""),
                subtitle: getDatasetValue(node, "projectSubtitle", ""),
                href: trimString(node.getAttribute("href"), ""),
                source: getDatasetValue(node, "projectSource", "dom"),
                isActive: node.getAttribute("aria-current") === "page" ||
                  (node.classList && node.classList.contains("is-active"))
              },
              config
            );
          } catch (error) {
            return null;
          }
        })
        .filter(Boolean);
    } catch (error) {
      return [];
    }
  }

  function loadItemsSync(root, config, options) {
    try {
      var opts = isObject(options) ? options : {};
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.loadItems === "function") {
        var loaded = dataApi.loadItems({
          config: config,
          element: root,
          items: opts.items,
          sort: opts.sort
        });

        if (loaded && isArray(loaded.items)) {
          return loaded;
        }
      }

      var domItems = parseExistingDomItems(root, config);

      if (domItems.length) {
        return {
          ok: true,
          source: "dom",
          items: domItems,
          count: domItems.length,
          currentChatId: config.currentChatId,
          currentProjectId: config.currentProjectId,
          config: config,
          loadedAt: nowIso(),
          apiUsed: false
        };
      }

      var fallback = normalizeItem(
        {
          id: config.currentProjectId || config.currentChatId || "current",
          projectId: config.currentProjectId,
          chatId: config.currentChatId,
          title: config.currentTitle || DEFAULT_CURRENT_TITLE,
          subtitle: config.currentSubtitle || DEFAULT_CURRENT_SUBTITLE,
          href: config.currentProjectId
            ? buildProjectHref(config.currentProjectId, config)
            : buildProjectHref("new", config),
          isActive: true,
          source: "fallback",
          updatedAt: nowIso()
        },
        config
      );

      return {
        ok: true,
        source: "fallback",
        items: fallback ? [fallback] : [],
        count: fallback ? 1 : 0,
        currentChatId: config.currentChatId,
        currentProjectId: config.currentProjectId,
        config: config,
        loadedAt: nowIso(),
        apiUsed: false
      };
    } catch (error) {
      return {
        ok: false,
        source: "error",
        items: [],
        count: 0,
        currentChatId: config.currentChatId,
        currentProjectId: config.currentProjectId,
        config: config,
        loadedAt: nowIso(),
        apiUsed: false,
        error: normalizeError(error)
      };
    }
  }

  async function loadItemsAsync(root, config, options) {
    try {
      var opts = isObject(options) ? options : {};
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.loadItemsAsync === "function") {
        var asyncLoaded = await dataApi.loadItemsAsync({
          config: config,
          element: root,
          items: opts.items,
          sort: opts.sort,
          skipApi: opts.skipApi || opts.skip_api,
          requireApi: opts.requireApi || opts.require_api,
          apiPath: opts.apiPath || opts.api_path
        });

        if (asyncLoaded && isArray(asyncLoaded.items)) {
          return asyncLoaded;
        }
      }

      return loadItemsSync(root, config, opts);
    } catch (error) {
      var fallback = loadItemsSync(root, config, options);
      fallback.error = fallback.error || normalizeError(error);
      fallback.apiUsed = false;
      return fallback;
    }
  }

  function queryRefs(root) {
    try {
      return {
        root: root,
        panel: root.querySelector("[data-project-sidebar-panel]"),
        toggle: root.querySelector("[data-project-sidebar-toggle]"),
        nav: root.querySelector("[data-project-sidebar-nav]"),
        list: root.querySelector("[data-project-sidebar-list]"),
        template: root.querySelector(TEMPLATE_SELECTOR),
        search: root.querySelector("[data-project-sidebar-search]"),
        create: root.querySelector("[data-project-sidebar-create]"),
        refresh: root.querySelector("[data-project-sidebar-refresh]"),
        count: root.querySelector("[data-project-sidebar-count]"),
        empty: root.querySelector("[data-project-sidebar-empty]"),
        noResults: root.querySelector("[data-project-sidebar-no-results]"),
        error: root.querySelector("[data-project-sidebar-error]"),
        errorMessage: root.querySelector("[data-project-sidebar-error-message]"),
        status: root.querySelector("[data-project-sidebar-status]"),
        resizeHandle: root.querySelector(RESIZE_HANDLE_SELECTOR),
        currentTitle: root.querySelector("[data-project-sidebar-current-title]"),
        currentSubtitle: root.querySelector("[data-project-sidebar-current-subtitle]")
      };
    } catch (error) {
      return {
        root: root
      };
    }
  }

  function setText(node, value) {
    try {
      if (node) {
        node.textContent = trimString(value, "");
      }
    } catch (error) {
      /* ignore text write error */
    }
  }

  function setHidden(node, hidden) {
    try {
      if (!node) {
        return;
      }

      if (hidden) {
        node.setAttribute("hidden", "");
      } else {
        node.removeAttribute("hidden");
      }
    } catch (error) {
      /* ignore hidden write error */
    }
  }

  function setStatus(refs, message) {
    try {
      setText(refs.status, message || "");
    } catch (error) {
      /* ignore */
    }
  }

  function setError(refs, error) {
    try {
      if (!refs || !refs.error) {
        return;
      }

      if (!error) {
        setHidden(refs.error, true);
        setText(refs.errorMessage, "");
        return;
      }

      var normalized = normalizeError(error);
      setHidden(refs.error, false);
      setText(refs.errorMessage, normalized.message || "Unbekannter Fehler.");
    } catch (innerError) {
      /* ignore */
    }
  }

  function createElement(tagName, className) {
    try {
      var doc = getDocument();
      var node = doc.createElement(tagName);

      if (className) {
        node.className = className;
      }

      return node;
    } catch (error) {
      return null;
    }
  }

  function cloneTemplate(refs) {
    try {
      if (refs.template && refs.template.content && refs.template.content.firstElementChild) {
        return refs.template.content.firstElementChild.cloneNode(true);
      }

      return null;
    } catch (error) {
      return null;
    }
  }

  function buildFallbackItemNode() {
    try {
      var item = createElement("a", "vp-project-sidebar__item");

      if (!item) {
        return null;
      }

      item.setAttribute("role", "listitem");
      item.setAttribute("href", "#");
      item.setAttribute("data-project-sidebar-item", "");

      var avatar = createElement("span", "vp-project-sidebar__item-avatar");
      avatar.setAttribute("aria-hidden", "true");
      avatar.setAttribute("data-project-sidebar-item-initial", "");
      avatar.textContent = "P";

      var body = createElement("span", "vp-project-sidebar__item-body");
      body.setAttribute("data-project-sidebar-expanded-only", "");

      var title = createElement("span", "vp-project-sidebar__item-title");
      title.setAttribute("data-project-sidebar-item-title", "");
      title.textContent = "Projekt";

      var subtitle = createElement("span", "vp-project-sidebar__item-subtitle");
      subtitle.setAttribute("data-project-sidebar-item-subtitle", "");
      subtitle.textContent = "Workspace";

      var indicator = createElement("span", "vp-project-sidebar__item-active-indicator");
      indicator.setAttribute("aria-hidden", "true");
      indicator.setAttribute("data-project-sidebar-item-active-indicator", "");
      indicator.setAttribute("hidden", "");

      body.appendChild(title);
      body.appendChild(subtitle);

      item.appendChild(avatar);
      item.appendChild(body);
      item.appendChild(indicator);

      return item;
    } catch (error) {
      return null;
    }
  }

  function getItemNodePart(node, selector) {
    try {
      return node && node.querySelector ? node.querySelector(selector) : null;
    } catch (error) {
      return null;
    }
  }

  function fillItemNode(node, item) {
    try {
      if (!node || !item) {
        return node;
      }

      var projectId = trimString(item.projectId || item.project_id || item.public_id || item.id, "");
      var chatId = trimString(item.chatId || item.chat_id || item.conversationId || item.conversation_id, "");
      var href = trimString(item.href, "") ||
        (projectId ? buildProjectHref(projectId) : buildChatHref(chatId));

      node.setAttribute("href", href);
      node.setAttribute("title", item.title || "");
      node.setAttribute("role", "listitem");
      node.setAttribute("data-project-sidebar-item", "");
      node.setAttribute("data-project-id", projectId || item.id || "");
      node.setAttribute("data-project-public-id", projectId || "");
      node.setAttribute("data-project-chat-id", chatId || "");
      node.setAttribute("data-project-title", item.title || "");
      node.setAttribute("data-project-subtitle", item.subtitle || "");
      node.setAttribute("data-project-source", item.source || "client");
      node.setAttribute("data-project-configured", item.isConfigured || item.is_configured ? "true" : "false");

      if (item.isActive || item.is_active) {
        node.classList.add("is-active");
        node.setAttribute("aria-current", "page");
      } else {
        node.classList.remove("is-active");
        node.removeAttribute("aria-current");
      }

      node.classList.toggle("is-configured", !!(item.isConfigured || item.is_configured));
      node.classList.toggle("is-draft", !(item.isConfigured || item.is_configured));

      if (item.source === "fallback" || item.source === "current") {
        node.classList.add("is-fallback");
      } else {
        node.classList.remove("is-fallback");
      }

      if (item.disabled) {
        node.setAttribute("aria-disabled", "true");
        node.setAttribute("tabindex", "-1");
      } else {
        node.removeAttribute("aria-disabled");
        node.removeAttribute("tabindex");
      }

      var initialNode = getItemNodePart(node, "[data-project-sidebar-item-initial]") ||
        getItemNodePart(node, ".vp-project-sidebar__item-avatar");

      var titleNode = getItemNodePart(node, "[data-project-sidebar-item-title]") ||
        getItemNodePart(node, ".vp-project-sidebar__item-title");

      var subtitleNode = getItemNodePart(node, "[data-project-sidebar-item-subtitle]") ||
        getItemNodePart(node, ".vp-project-sidebar__item-subtitle");

      var activeIndicator = getItemNodePart(node, "[data-project-sidebar-item-active-indicator]") ||
        getItemNodePart(node, ".vp-project-sidebar__item-active-indicator");

      setText(initialNode, item.initial || normalizeInitial(item.title));
      setText(titleNode, item.title || "Projekt");
      setText(subtitleNode, item.subtitle || "Workspace");

      if (activeIndicator) {
        setHidden(activeIndicator, !(item.isActive || item.is_active));
      }

      node.__vpProjectSidebarItem__ = item;

      return node;
    } catch (error) {
      return node;
    }
  }

  function clearList(list) {
    try {
      if (!list) {
        return;
      }

      while (list.firstChild) {
        list.removeChild(list.firstChild);
      }
    } catch (error) {
      /* ignore */
    }
  }

  function renderItems(refs, items) {
    try {
      var list = refs.list;

      if (!list) {
        return 0;
      }

      clearList(list);

      var safeItems = isArray(items) ? items : [];

      safeItems.forEach(function renderOne(item) {
        try {
          var node = cloneTemplate(refs) || buildFallbackItemNode();

          if (!node) {
            return;
          }

          fillItemNode(node, item);
          list.appendChild(node);
        } catch (error) {
          /* ignore one broken item */
        }
      });

      list.setAttribute("data-project-sidebar-list-empty", safeItems.length ? "false" : "true");

      if (refs.count) {
        setText(refs.count, String(safeItems.length));
      }

      setHidden(refs.empty, safeItems.length > 0);
      setHidden(refs.noResults, true);

      return safeItems.length;
    } catch (error) {
      return 0;
    }
  }

  function updateCurrentHeader(refs, items, config) {
    try {
      var active = null;

      if (isArray(items)) {
        for (var index = 0; index < items.length; index += 1) {
          if (items[index] && (items[index].isActive || items[index].is_active)) {
            active = items[index];
            break;
          }
        }
      }

      setText(refs.currentTitle, active ? active.title : config.currentTitle || DEFAULT_CURRENT_TITLE);
      setText(refs.currentSubtitle, active ? active.subtitle : config.currentSubtitle || DEFAULT_CURRENT_SUBTITLE);
    } catch (error) {
      /* ignore */
    }
  }

  function markActiveItem(root, items, activeProjectId, activeChatId) {
    try {
      var projectId = trimString(activeProjectId, "");
      var chatId = trimString(activeChatId, "");
      var nodes = Array.prototype.slice.call(root.querySelectorAll(ITEM_SELECTOR));

      nodes.forEach(function updateNode(node) {
        try {
          var nodeProjectId =
            trimString(getDatasetValue(node, "projectId", ""), "") ||
            trimString(getDatasetValue(node, "projectPublicId", ""), "");
          var nodeChatId = trimString(getDatasetValue(node, "projectChatId", ""), "");

          var isActive = (!!projectId && nodeProjectId === projectId) ||
            (!!chatId && nodeChatId === chatId);

          if (isActive) {
            node.classList.add("is-active");
            node.setAttribute("aria-current", "page");
          } else {
            node.classList.remove("is-active");
            node.removeAttribute("aria-current");
          }
        } catch (error) {
          /* ignore */
        }
      });

      if (isArray(items)) {
        items.forEach(function updateItem(item) {
          if (item) {
            var itemProjectId = trimString(item.projectId || item.project_id || item.public_id || item.id, "");
            var itemChatId = trimString(item.chatId || item.chat_id || item.conversationId || item.conversation_id, "");

            item.isActive = (!!projectId && itemProjectId === projectId) ||
              (!!chatId && itemChatId === chatId);
            item.is_active = item.isActive;
          }
        });
      }
    } catch (error) {
      /* ignore */
    }
  }

  function filterVisibleItems(root, refs, items, query) {
    try {
      var search = trimString(query, "").toLowerCase();
      var visibleCount = 0;
      var nodes = Array.prototype.slice.call(root.querySelectorAll(ITEM_SELECTOR));

      nodes.forEach(function filterNode(node) {
        try {
          var item = node.__vpProjectSidebarItem__;
          var title = item && item.title ? item.title : getDatasetValue(node, "projectTitle", "");
          var subtitle = item && item.subtitle ? item.subtitle : getDatasetValue(node, "projectSubtitle", "");
          var projectId =
            item && item.projectId
              ? item.projectId
              : getDatasetValue(node, "projectId", "") || getDatasetValue(node, "projectPublicId", "");
          var chatId = item && item.chatId ? item.chatId : getDatasetValue(node, "projectChatId", "");
          var source = item && item.source ? item.source : getDatasetValue(node, "projectSource", "");
          var haystack = [title, subtitle, projectId, chatId, source].join(" ").toLowerCase();
          var matches = !search || haystack.indexOf(search) !== -1;

          if (matches) {
            node.classList.remove(CLASS_HIDDEN_BY_SEARCH);
            visibleCount += 1;
          } else {
            node.classList.add(CLASS_HIDDEN_BY_SEARCH);
          }
        } catch (error) {
          node.classList.remove(CLASS_HIDDEN_BY_SEARCH);
          visibleCount += 1;
        }
      });

      setHidden(refs.noResults, !search || visibleCount > 0);
      setHidden(refs.empty, nodes.length > 0);

      setStatus(
        refs,
        search
          ? visibleCount + " Projekt" + (visibleCount === 1 ? "" : "e") + " gefunden."
          : ""
      );

      return visibleCount;
    } catch (error) {
      return isArray(items) ? items.length : 0;
    }
  }

  function setCollapsed(root, refs, collapsed, options) {
    try {
      var opts = isObject(options) ? options : {};
      var isCollapsed = !!collapsed;

      root.classList.toggle(CLASS_COLLAPSED, isCollapsed);
      root.classList.toggle(CLASS_EXPANDED, !isCollapsed);
      root.setAttribute("data-project-sidebar-collapsed", isCollapsed ? "true" : "false");

      if (refs && refs.toggle) {
        refs.toggle.setAttribute("aria-expanded", isCollapsed ? "false" : "true");

        var label = isCollapsed
          ? refs.toggle.getAttribute("data-label-collapsed") || "Projektleiste ausklappen"
          : refs.toggle.getAttribute("data-label-expanded") || "Projektleiste einklappen";

        refs.toggle.setAttribute("aria-label", label);
        refs.toggle.setAttribute("title", label);
      }

      var config = getConfig(root, opts);

      if (opts.persist !== false) {
        writeStorage(config.storageKey, {
          collapsed: isCollapsed
        });
      }

      dispatch(root, "vectoplan:project-sidebar:collapsed-change", {
        collapsed: isCollapsed,
        source: opts.source || "controller"
      });

      dispatch(root, isCollapsed ? "vectoplan:project-sidebar:collapsed" : "vectoplan:project-sidebar:expanded", {
        collapsed: isCollapsed,
        source: opts.source || "controller"
      });

      return isCollapsed;
    } catch (error) {
      return !!collapsed;
    }
  }

  function restoreCollapsedState(root, refs, config) {
    try {
      var storage = readStorage(config.storageKey);
      var collapsedFromStorage = storage.collapsed;
      var collapsedFromDataset = getDatasetValue(root, "projectSidebarCollapsed", "");
      var collapsed;

      if (collapsedFromStorage !== undefined) {
        collapsed = toBooleanSafe(collapsedFromStorage, false);
      } else if (collapsedFromDataset !== "") {
        collapsed = toBooleanSafe(collapsedFromDataset, false);
      } else {
        collapsed = root.classList.contains(CLASS_COLLAPSED);
      }

      setCollapsed(root, refs, collapsed, {
        persist: false,
        source: "restore"
      });

      return collapsed;
    } catch (error) {
      return false;
    }
  }

  function setLoading(root, loading) {
    try {
      root.classList.toggle(CLASS_LOADING, !!loading);
      root.setAttribute("data-project-sidebar-loading", loading ? "true" : "false");
    } catch (error) {
      /* ignore */
    }
  }

  function setHasError(root, hasError) {
    try {
      root.classList.toggle(CLASS_ERROR, !!hasError);
      root.setAttribute("data-project-sidebar-has-error", hasError ? "true" : "false");
    } catch (error) {
      /* ignore */
    }
  }

  function normalizeNavigationItemFromNode(node) {
    try {
      if (!node) {
        return null;
      }

      return {
        id:
          getDatasetValue(node, "projectId", "") ||
          getDatasetValue(node, "projectPublicId", ""),
        projectId:
          getDatasetValue(node, "projectId", "") ||
          getDatasetValue(node, "projectPublicId", ""),
        public_id:
          getDatasetValue(node, "projectPublicId", "") ||
          getDatasetValue(node, "projectId", ""),
        chatId: getDatasetValue(node, "projectChatId", ""),
        title: getDatasetValue(node, "projectTitle", "") || trimString(node.textContent, ""),
        subtitle: getDatasetValue(node, "projectSubtitle", ""),
        href: trimString(node.getAttribute("href"), ""),
        source: getDatasetValue(node, "projectSource", "dom")
      };
    } catch (error) {
      return null;
    }
  }

  function rememberItem(item, config) {
    try {
      var dataApi = getDataApi();

      if (dataApi && typeof dataApi.rememberItem === "function") {
        return dataApi.rememberItem(item, {
          config: config
        });
      }

      var storage = readStorage(config.storageKey);
      var recent = isArray(storage.recentProjects) ? storage.recentProjects.slice() : [];

      recent.unshift(item);

      var seen = {};
      recent = recent.filter(function dedupe(project) {
        try {
          var key =
            trimString(project.projectId || project.project_id || project.public_id, "") ||
            trimString(project.chatId || project.chat_id, "") ||
            trimString(project.id, "") ||
            trimString(project.href, "");

          if (!key || seen[key]) {
            return false;
          }

          seen[key] = true;
          return true;
        } catch (error) {
          return false;
        }
      }).slice(0, 80);

      writeStorage(config.storageKey, {
        recentProjects: recent,
        lastActiveProjectId: item.projectId || item.project_id || item.public_id || "",
        lastActiveChatId: item.chatId || item.chat_id || ""
      });

      return true;
    } catch (error) {
      return false;
    }
  }

  function applyConfigToDom(root, refs, config) {
    try {
      root.setAttribute("data-project-sidebar-api-path", config.apiPath || "");
      root.setAttribute("data-project-sidebar-current-project-id", config.currentProjectId || "");
      root.setAttribute("data-project-sidebar-current-chat-id", config.currentChatId || "");

      if (refs.create) {
        var href = getCreateProjectUrl(config);

        refs.create.removeAttribute("hidden");
        refs.create.setAttribute("data-project-sidebar-create-url", href);

        if (refs.create.tagName && refs.create.tagName.toLowerCase() === "a") {
          refs.create.setAttribute("href", href);
        }
      }
    } catch (error) {
      /* ignore */
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

      var opts = isObject(options) ? options : {};
      var refs = queryRefs(root);
      var cleanup = [];
      var config = getConfig(root, opts);

      var state = {
        root: root,
        refs: refs,
        config: config,
        items: [],
        filteredCount: 0,
        activeChatId: config.currentChatId,
        activeProjectId: config.currentProjectId,
        initialized: false,
        destroyed: false,
        loadedAt: "",
        source: "",
        lastError: null,
        apiUsed: false,
        refreshSerial: 0
      };

      function refreshConfig() {
        try {
          state.config = getConfig(root, opts);
          applyConfigToDom(root, refs, state.config);
          state.activeChatId = state.config.currentChatId;
          state.activeProjectId = state.config.currentProjectId;
          return state.config;
        } catch (error) {
          return state.config || config;
        }
      }

      async function loadAndRender(loadOptions) {
        var serial = 0;

        try {
          var cfg = refreshConfig();
          var localOptions = isObject(loadOptions) ? loadOptions : {};

          state.refreshSerial += 1;
          serial = state.refreshSerial;

          setLoading(root, true);
          setHasError(root, false);
          setError(refs, null);
          setStatus(refs, "Projektliste wird geladen.");

          var result = await loadItemsAsync(root, cfg, localOptions);

          if (serial !== state.refreshSerial) {
            return result;
          }

          state.items = isArray(result.items) ? result.items : [];
          state.source = result.source || "";
          state.loadedAt = result.loadedAt || nowIso();
          state.apiUsed = !!result.apiUsed;
          state.lastError = result.ok ? null : result.error || null;

          renderItems(refs, state.items);
          markActiveItem(root, state.items, cfg.currentProjectId, cfg.currentChatId);
          updateCurrentHeader(refs, state.items, cfg);

          if (refs.search && refs.search.value) {
            state.filteredCount = filterVisibleItems(root, refs, state.items, refs.search.value);
          } else {
            state.filteredCount = state.items.length;
          }

          if (!result.ok) {
            setHasError(root, true);
            setError(refs, result.error || new Error("Projektliste konnte nicht vollständig geladen werden."));
          }

          setStatus(
            refs,
            state.items.length
              ? state.items.length + " Projekt" + (state.items.length === 1 ? "" : "e") + " geladen."
              : "Keine Projekte sichtbar."
          );

          dispatch(root, "vectoplan:project-sidebar:items-loaded", {
            ok: !!result.ok,
            source: state.source,
            apiUsed: state.apiUsed,
            items: state.items.slice(),
            count: state.items.length,
            currentChatId: cfg.currentChatId,
            currentProjectId: cfg.currentProjectId,
            loadedAt: state.loadedAt
          });

          return result;
        } catch (error) {
          state.lastError = normalizeError(error);
          state.items = [];
          state.filteredCount = 0;

          setHasError(root, true);
          setError(refs, error);
          setStatus(refs, "Projektliste konnte nicht geladen werden.");

          dispatch(root, "vectoplan:project-sidebar:error", {
            error: state.lastError
          });

          return {
            ok: false,
            items: [],
            count: 0,
            error: state.lastError
          };
        } finally {
          if (!serial || serial === state.refreshSerial) {
            setLoading(root, false);
          }
        }
      }

      function toggleCollapsed(source) {
        try {
          var collapsed = root.classList.contains(CLASS_COLLAPSED) ||
            root.getAttribute("data-project-sidebar-collapsed") === "true";

          return setCollapsed(root, refs, !collapsed, {
            persist: true,
            source: source || "toggle"
          });
        } catch (error) {
          return false;
        }
      }

      function expand(source) {
        return setCollapsed(root, refs, false, {
          persist: true,
          source: source || "expand"
        });
      }

      function collapse(source) {
        return setCollapsed(root, refs, true, {
          persist: true,
          source: source || "collapse"
        });
      }

      function openMobile(source) {
        try {
          root.classList.add(CLASS_MOBILE_OPEN);
          root.setAttribute("data-project-sidebar-mobile-open", "true");

          dispatch(root, "vectoplan:project-sidebar:mobile-open", {
            source: source || "controller"
          });

          return true;
        } catch (error) {
          return false;
        }
      }

      function closeMobile(source) {
        try {
          root.classList.remove(CLASS_MOBILE_OPEN);
          root.setAttribute("data-project-sidebar-mobile-open", "false");

          dispatch(root, "vectoplan:project-sidebar:mobile-close", {
            source: source || "controller"
          });

          return true;
        } catch (error) {
          return false;
        }
      }

      function handleToggleClick(event) {
        try {
          if (event && event.cancelable) {
            event.preventDefault();
          }

          toggleCollapsed("button");
        } catch (error) {
          /* ignore */
        }
      }

      function handleSearchInput(event) {
        try {
          var query = event && event.target ? event.target.value : "";
          state.filteredCount = filterVisibleItems(root, refs, state.items, query);

          dispatch(root, "vectoplan:project-sidebar:search", {
            query: trimString(query, ""),
            count: state.filteredCount
          });
        } catch (error) {
          /* ignore */
        }
      }

      function handleRefreshClick(event) {
        try {
          if (event && event.cancelable) {
            event.preventDefault();
          }

          void loadAndRender({
            source: "refresh",
            skipApi: false
          });
        } catch (error) {
          /* ignore */
        }
      }

      function handleCreateClick(event) {
        try {
          if (event && event.cancelable) {
            event.preventDefault();
          }

          var cfg = refreshConfig();
          var href = getCreateProjectUrl(cfg);

          dispatch(root, "vectoplan:project-sidebar:create-requested", {
            href: href,
            source: "button"
          });

          if (href) {
            getWindow().location.href = href;
          }
        } catch (error) {
          setStatus(refs, "Projekt-Erstellung konnte nicht geöffnet werden.");
        }
      }

      function handleItemClick(event) {
        try {
          var target = event.target;
          var itemNode = target && target.closest ? target.closest(ITEM_SELECTOR) : null;

          if (!itemNode || !root.contains(itemNode)) {
            return;
          }

          if (itemNode.getAttribute("aria-disabled") === "true") {
            if (event.cancelable) {
              event.preventDefault();
            }
            return;
          }

          var navItem = itemNode.__vpProjectSidebarItem__ || normalizeNavigationItemFromNode(itemNode);
          var href = trimString(itemNode.getAttribute("href"), "") || (navItem && navItem.href) || "";

          if (!href || href === "#") {
            if (event.cancelable) {
              event.preventDefault();
            }

            setStatus(refs, "Für dieses Projekt ist noch kein Ziel hinterlegt.");
            return;
          }

          rememberItem(navItem, state.config);

          dispatch(root, "vectoplan:project-sidebar:item-selected", {
            item: navItem,
            href: href,
            source: "click"
          });

          /*
            Navigation bleibt native Browser-Navigation.
            Kein pushState-Zwang, kein iframe-only Wechsel.
          */
        } catch (error) {
          /* native navigation should continue if possible */
        }
      }

      function handleListKeydown(event) {
        try {
          if (!event || !refs.list) {
            return;
          }

          var key = event.key;

          if (
            key !== "ArrowDown" &&
            key !== "ArrowUp" &&
            key !== "Home" &&
            key !== "End"
          ) {
            return;
          }

          var nodes = Array.prototype.slice.call(root.querySelectorAll(ITEM_SELECTOR))
            .filter(function visible(node) {
              return !node.classList.contains(CLASS_HIDDEN_BY_SEARCH) &&
                node.getAttribute("aria-disabled") !== "true";
            });

          if (!nodes.length) {
            return;
          }

          var activeElement = getDocument().activeElement;
          var currentIndex = nodes.indexOf(activeElement);
          var nextIndex = currentIndex;

          if (key === "ArrowDown") {
            nextIndex = currentIndex < 0 ? 0 : Math.min(nodes.length - 1, currentIndex + 1);
          } else if (key === "ArrowUp") {
            nextIndex = currentIndex < 0 ? nodes.length - 1 : Math.max(0, currentIndex - 1);
          } else if (key === "Home") {
            nextIndex = 0;
          } else if (key === "End") {
            nextIndex = nodes.length - 1;
          }

          if (event.cancelable) {
            event.preventDefault();
          }

          if (nodes[nextIndex] && typeof nodes[nextIndex].focus === "function") {
            nodes[nextIndex].focus();
          }
        } catch (error) {
          /* ignore keyboard navigation errors */
        }
      }

      function handleRootKeydown(event) {
        try {
          if (!event) {
            return;
          }

          if (event.key === "Escape") {
            closeMobile("escape");
          }
        } catch (error) {
          /* ignore */
        }
      }

      function handleProjectEvent(event) {
        try {
          var detail = event && event.detail ? event.detail : {};
          var project = detail.project || (detail.payload && detail.payload.project) || null;

          if (project && isObject(project)) {
            try {
              if (!getWindow().APP_CONFIG || typeof getWindow().APP_CONFIG !== "object") {
                getWindow().APP_CONFIG = {};
              }

              getWindow().APP_CONFIG.project = project;
              getWindow().APP_CONFIG.currentProject = project;
              getWindow().APP_CONFIG.projectPublicId =
                project.public_id ||
                project.publicId ||
                getWindow().APP_CONFIG.projectPublicId ||
                "";
              getWindow().APP_CONFIG.currentProjectId = getWindow().APP_CONFIG.projectPublicId;
            } catch (configError) {
              /* ignore */
            }
          }

          void loadAndRender({
            source: "project-event",
            skipApi: false
          });
        } catch (error) {
          /* ignore */
        }
      }

      function handleMessageEvent(event) {
        try {
          var data = event && event.data;

          if (!data || typeof data !== "object") {
            return;
          }

          var type = trimString(data.type || data.kind, "");

          if (PROJECT_EVENTS.indexOf(type) === -1) {
            return;
          }

          handleProjectEvent({
            detail: data.detail || data
          });
        } catch (error) {
          /* ignore */
        }
      }

      function bindEvents() {
        try {
          addListener(refs.toggle, "click", handleToggleClick, false, cleanup);
          addListener(refs.search, "input", handleSearchInput, false, cleanup);
          addListener(refs.refresh, "click", handleRefreshClick, false, cleanup);
          addListener(refs.create, "click", handleCreateClick, false, cleanup);
          addListener(root, "click", handleItemClick, false, cleanup);
          addListener(root, "keydown", handleRootKeydown, false, cleanup);
          addListener(refs.list, "keydown", handleListKeydown, false, cleanup);

          PROJECT_EVENTS.forEach(function bindProjectEvent(eventName) {
            addListener(getWindow(), eventName, handleProjectEvent, false, cleanup);
          });

          addListener(getWindow(), "message", handleMessageEvent, false, cleanup);
          addListener(getWindow(), "project-sidebar:refresh", handleRefreshClick, false, cleanup);
        } catch (error) {
          /* ignore */
        }
      }

      function initResize() {
        try {
          var resizeApi = getResizeApi();

          if (resizeApi && typeof resizeApi.init === "function") {
            return resizeApi.init({
              root: root
            });
          }

          return null;
        } catch (error) {
          return null;
        }
      }

      function rememberCurrent() {
        try {
          var dataApi = getDataApi();

          if (dataApi && typeof dataApi.rememberCurrent === "function") {
            return dataApi.rememberCurrent({
              element: root,
              config: state.config
            });
          }

          var current = {
            id: state.config.currentProjectId || state.config.currentChatId || "current",
            projectId: state.config.currentProjectId,
            chatId: state.config.currentChatId,
            title: state.config.currentTitle,
            subtitle: state.config.currentSubtitle,
            href: state.config.currentProjectId
              ? buildProjectHref(state.config.currentProjectId, state.config)
              : buildProjectHref("new", state.config),
            source: "current",
            updatedAt: nowIso()
          };

          return rememberItem(current, state.config);
        } catch (error) {
          return false;
        }
      }

      function refresh(refreshOptions) {
        try {
          refreshConfig();
          return loadAndRender(refreshOptions || {
            source: "refresh-api",
            skipApi: false
          });
        } catch (error) {
          return Promise.resolve({
            ok: false,
            items: [],
            error: normalizeError(error)
          });
        }
      }

      function destroy() {
        try {
          state.destroyed = true;

          cleanup.forEach(function runCleanup(fn) {
            try {
              fn();
            } catch (error) {
              /* ignore */
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

          controllers = controllers.filter(function keep(item) {
            return item !== controller;
          });

          dispatch(root, "vectoplan:project-sidebar:destroy", {
            source: "destroy"
          });
        } catch (error) {
          /* destroy should not throw */
        }
      }

      function getSnapshot() {
        try {
          var resizeApi = getResizeApi();
          var resizeController = resizeApi && typeof resizeApi.getController === "function"
            ? resizeApi.getController(root)
            : null;

          return {
            version: INTERNAL_VERSION,
            initialized: state.initialized,
            destroyed: state.destroyed,
            source: state.source,
            apiUsed: state.apiUsed,
            count: state.items.length,
            filteredCount: state.filteredCount,
            activeChatId: state.activeChatId,
            activeProjectId: state.activeProjectId,
            currentChatId: state.config.currentChatId,
            currentProjectId: state.config.currentProjectId,
            collapsed: root.classList.contains(CLASS_COLLAPSED),
            mobileOpen: root.classList.contains(CLASS_MOBILE_OPEN),
            storageKey: state.config.storageKey,
            apiPath: state.config.apiPath || "",
            loadedAt: state.loadedAt,
            lastError: state.lastError,
            resize: resizeController && typeof resizeController.getSnapshot === "function"
              ? resizeController.getSnapshot()
              : null
          };
        } catch (error) {
          return {
            version: INTERNAL_VERSION,
            initialized: false,
            error: normalizeError(error)
          };
        }
      }

      controller = {
        version: INTERNAL_VERSION,
        root: root,
        refs: refs,
        state: state,
        refresh: refresh,
        reload: refresh,
        collapse: collapse,
        expand: expand,
        toggleCollapsed: toggleCollapsed,
        openMobile: openMobile,
        closeMobile: closeMobile,
        rememberCurrent: rememberCurrent,
        renderItems: function renderExternalItems(items) {
          try {
            var cfg = refreshConfig();
            var normalized = isArray(items)
              ? items.map(function mapItem(item) {
                  return normalizeItem(item, cfg);
                }).filter(Boolean)
              : [];

            state.items = normalized;
            renderItems(refs, state.items);
            markActiveItem(root, state.items, cfg.currentProjectId, cfg.currentChatId);
            updateCurrentHeader(refs, state.items, cfg);

            return state.items.length;
          } catch (error) {
            return 0;
          }
        },
        getItems: function getItems() {
          return state.items.slice();
        },
        getSnapshot: getSnapshot,
        destroy: destroy
      };

      root[DATA_CONTROLLER_KEY] = controller;

      applyConfigToDom(root, refs, config);
      restoreCollapsedState(root, refs, config);
      bindEvents();
      initResize();
      rememberCurrent();

      var initialSyncResult = loadItemsSync(root, config, {
        source: "init-sync"
      });

      if (initialSyncResult && isArray(initialSyncResult.items)) {
        state.items = initialSyncResult.items;
        state.source = initialSyncResult.source || "";
        state.loadedAt = initialSyncResult.loadedAt || nowIso();
        renderItems(refs, state.items);
        markActiveItem(root, state.items, config.currentProjectId, config.currentChatId);
        updateCurrentHeader(refs, state.items, config);
      }

      void loadAndRender({
        source: "init-api",
        skipApi: false
      });

      state.initialized = true;

      dispatch(root, "vectoplan:project-sidebar:ready", {
        controller: controller,
        snapshot: getSnapshot()
      });

      controllers.push(controller);
      ensureGlobalKeyHandler();

      return controller;
    } catch (error) {
      try {
        setHasError(root, true);
        dispatch(root, "vectoplan:project-sidebar:error", {
          error: normalizeError(error)
        });
      } catch (innerError) {
        /* ignore */
      }

      return null;
    }
  }

  function normalizeInitInput(rootOrOptions) {
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

        return {
          root: null,
          options: rootOrOptions
        };
      }

      return {
        root: null,
        options: {}
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
      var normalized = normalizeInitInput(rootOrOptions);
      var root = normalized.root;

      if (!root) {
        var doc = getDocument();
        root = doc && doc.querySelector ? doc.querySelector(ROOT_SELECTOR) : null;
      }

      if (!root) {
        return null;
      }

      return createController(root, normalized.options);
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
          /* ignore one broken root */
        }
      });

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

  function refreshAll(options) {
    try {
      return controllers.map(function refreshOne(controller) {
        try {
          if (controller && typeof controller.refresh === "function") {
            return controller.refresh(options || {
              source: "refresh-all",
              skipApi: false
            });
          }

          return null;
        } catch (error) {
          return null;
        }
      });
    } catch (error) {
      return [];
    }
  }

  function ensureGlobalKeyHandler() {
    try {
      if (hasGlobalKeyHandler) {
        return;
      }

      var doc = getDocument();

      if (!doc || !doc.addEventListener) {
        return;
      }

      doc.addEventListener(
        "keydown",
        function onGlobalKeydown(event) {
          try {
            if (!event || event.defaultPrevented) {
              return;
            }

            if (event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLowerCase() === "p") {
              var first = controllers[0];

              if (first && typeof first.toggleCollapsed === "function") {
                event.preventDefault();
                first.toggleCollapsed("keyboard-alt-p");
              }
            }
          } catch (error) {
            /* ignore */
          }
        },
        false
      );

      hasGlobalKeyHandler = true;
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
              error: normalizeError(error)
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
      routeBase: DEFAULT_ROUTE_BASE,
      createProjectUrl: DEFAULT_CREATE_PROJECT_URL,
      rootSelector: ROOT_SELECTOR,
      itemSelector: ITEM_SELECTOR,
      classCollapsed: CLASS_COLLAPSED,
      classExpanded: CLASS_EXPANDED
    },

    init: init,
    initAll: initAll,
    getController: getController,
    destroy: destroy,
    refreshAll: refreshAll,
    createDebugSnapshot: createDebugSnapshot,

    _private: {
      getConfig: getConfig,
      loadItemsSync: loadItemsSync,
      loadItemsAsync: loadItemsAsync,
      normalizeItem: normalizeItem,
      parseExistingDomItems: parseExistingDomItems,
      renderItems: renderItems,
      filterVisibleItems: filterVisibleItems,
      setCollapsed: setCollapsed,
      readStorage: readStorage,
      writeStorage: writeStorage,
      buildProjectHref: buildProjectHref,
      getCreateProjectUrl: getCreateProjectUrl
    }
  };

  try {
    global[EXPORT_NAME] = api;
    global[LEGACY_EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectSidebar = api;
  } catch (error) {
    /* export is best-effort */
  }

  autoInit();
})(window);