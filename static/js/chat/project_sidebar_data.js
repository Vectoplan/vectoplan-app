/* services/vectoplan-app/static/js/chat/project_sidebar_data.js */

/*
  VECTOPLAN Project Sidebar Data Adapter

  Zweck:
  - Datenadapter für die linke Projekt-/Workspace-Leiste.
  - Primäre Datenquelle: /v1/projects/sidebar.
  - Fallbacks: Template-/APP_CONFIG-Items, globale Items, localStorage, aktuelles Projekt.
  - Keine Render-Logik.
  - Keine Event-Bindings.
  - Keine Build-Tools.
  - Vollständig defensiv gegen fehlende API, kaputte localStorage-Daten und unvollständige Items.

  Globale Exports:
  - window.VectoplanProjectSidebarData
  - window.__VECTOPLAN_PROJECT_SIDEBAR_DATA__

  Hauptmethoden:
  - getConfig(options)
  - loadItems(options)              // synchroner Fallback, kompatibel mit altem Controller
  - loadItemsAsync(options)         // neuer API-first Loader
  - fetchApiItems(options)
  - normalizeItem(raw, options)
  - normalizeItems(rawItems, options)
  - buildCurrentItem(options)
  - rememberItem(item, options)
  - rememberCurrent(options)
  - readStorage(storageKey)
  - writeStorage(storageKey, patch)
  - findItemByProjectId(items, projectId)
  - findItemByChatId(items, chatId)

  Datenquellen:
  Async/API-first:
  1. /v1/projects/sidebar
  2. options.items
  3. APP_CONFIG.projectSidebar.items
  4. APP_CONFIG.projects / APP_CONFIG.projectItems
  5. window.__VECTOPLAN_PROJECT_SIDEBAR_ITEMS__
  6. window.__VECTOPLAN_PROJECTS__
  7. localStorage cachedApiItems / recentProjects
  8. aktuelles Projekt als Fallback

  Sync/Legacy:
  - keine fetch-Aufrufe
  - nutzt 2 bis 8
*/

(function initVectoplanProjectSidebarData(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectSidebarData";
  var LEGACY_EXPORT_NAME = "__VECTOPLAN_PROJECT_SIDEBAR_DATA__";

  var DEFAULT_STORAGE_KEY = "vectoplan.projectSidebar.v1";
  var DEFAULT_ROUTE_BASE = "/";
  var DEFAULT_API_PATH = "/v1/projects/sidebar";
  var DEFAULT_MAX_RECENT_ITEMS = 80;
  var DEFAULT_MAX_CACHED_API_ITEMS = 200;
  var DEFAULT_CURRENT_TITLE = "Aktuelles Projekt";
  var DEFAULT_CURRENT_SUBTITLE = "Projekt definieren";

  var EMPTY_STRING = "";
  var SOURCE_API = "api";
  var SOURCE_SERVER = "server";
  var SOURCE_CLIENT = "client";
  var SOURCE_CURRENT = "current";
  var SOURCE_STORAGE = "storage";
  var SOURCE_CACHE = "cache";
  var SOURCE_FALLBACK = "fallback";

  var INTERNAL_VERSION = 2;

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

  function toStringSafe(value, fallback) {
    try {
      if (value === null || value === undefined) {
        return fallback || EMPTY_STRING;
      }

      if (typeof value === "string") {
        return value;
      }

      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }

      return fallback || EMPTY_STRING;
    } catch (error) {
      return fallback || EMPTY_STRING;
    }
  }

  function trimString(value, fallback) {
    try {
      var result = toStringSafe(value, fallback || EMPTY_STRING).trim();
      return result || fallback || EMPTY_STRING;
    } catch (error) {
      return fallback || EMPTY_STRING;
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

  function clampNumber(value, min, max, fallback) {
    try {
      var number = toNumberSafe(value, fallback);

      if (!Number.isFinite(number)) {
        number = fallback;
      }

      if (Number.isFinite(min) && number < min) {
        return min;
      }

      if (Number.isFinite(max) && number > max) {
        return max;
      }

      return number;
    } catch (error) {
      return fallback;
    }
  }

  function shallowClone(value) {
    try {
      if (!isObject(value)) {
        return {};
      }

      var clone = {};
      Object.keys(value).forEach(function copyKey(key) {
        clone[key] = value[key];
      });
      return clone;
    } catch (error) {
      return {};
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

  function normalizeError(error) {
    try {
      if (!error) {
        return {
          name: "Error",
          message: "Unknown error",
          stack: ""
        };
      }

      if (typeof error === "string") {
        return {
          name: "Error",
          message: error,
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

  function getAppConfig() {
    try {
      var win = getWindow();

      if (isObject(win.APP_CONFIG)) {
        return win.APP_CONFIG;
      }

      if (isObject(win.__APP_CONFIG__)) {
        return win.__APP_CONFIG__;
      }

      if (isObject(win.VECTOPLAN_APP_CONFIG)) {
        return win.VECTOPLAN_APP_CONFIG;
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function getProjectSidebarConfig(appConfig) {
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

  function getProjectsApiConfig(appConfig) {
    try {
      var config = appConfig || getAppConfig();

      if (isObject(config.projectsApi)) {
        return config.projectsApi;
      }

      if (isObject(config.projects_api)) {
        return config.projects_api;
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function getCurrentProject() {
    try {
      var win = getWindow();
      var appConfig = getAppConfig();

      var candidates = [
        appConfig.currentProject,
        appConfig.project,
        win.__VECTOPLAN_CURRENT_PROJECT__
      ];

      for (var index = 0; index < candidates.length; index += 1) {
        if (isObject(candidates[index])) {
          return candidates[index];
        }
      }

      return {};
    } catch (error) {
      return {};
    }
  }

  function querySidebarElement() {
    try {
      var doc = getDocument();

      if (!doc || !doc.querySelector) {
        return null;
      }

      return doc.querySelector("[data-vp-project-sidebar]");
    } catch (error) {
      return null;
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

  function getElementDatasetValue(element, key, fallback) {
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

  function getQueryParam(name) {
    try {
      var win = getWindow();
      var location = win.location;

      if (!location || typeof location.search !== "string") {
        return "";
      }

      var params = new URLSearchParams(location.search);
      return trimString(params.get(name), "");
    } catch (error) {
      return "";
    }
  }

  function getPathname() {
    try {
      var win = getWindow();
      return trimString(win.location && win.location.pathname, "");
    } catch (error) {
      return "";
    }
  }

  function getProjectIdFromLocation() {
    try {
      var win = getWindow();
      var path = getPathname();

      if (path.indexOf("/project=") === 0) {
        return trimString(decodeURIComponent(path.slice("/project=".length)), "");
      }

      if (path.indexOf("/project/") === 0) {
        return trimString(decodeURIComponent(path.slice("/project/".length)), "");
      }

      var params = new URLSearchParams(win.location && win.location.search ? win.location.search : "");
      return trimString(
        params.get("project") ||
          params.get("project_id") ||
          params.get("projectPublicId") ||
          params.get("project_public_id") ||
          "",
        ""
      );
    } catch (error) {
      return "";
    }
  }

  function getPathSegments() {
    try {
      var pathname = getPathname();

      return pathname
        .split("/")
        .map(function normalizeSegment(segment) {
          return trimString(segment, "");
        })
        .filter(Boolean);
    } catch (error) {
      return [];
    }
  }

  function getChatIdFromPath() {
    try {
      var segments = getPathSegments();

      for (var index = 0; index < segments.length; index += 1) {
        if (segments[index] === "chat" && segments[index + 1]) {
          return trimString(decodeURIComponent(segments[index + 1]), "");
        }
      }

      return "";
    } catch (error) {
      return "";
    }
  }

  function getCurrentProjectId(options) {
    try {
      var opts = isObject(options) ? options : {};
      var appConfig = getAppConfig();
      var sidebarConfig = getProjectSidebarConfig(appConfig);
      var project = getCurrentProject();
      var element = opts.element || querySidebarElement();
      var root = queryAppRoot();

      var candidates = [
        opts.currentProjectId,
        opts.current_project_id,
        opts.projectPublicId,
        opts.project_public_id,
        sidebarConfig.currentProjectId,
        sidebarConfig.current_project_id,
        sidebarConfig.projectPublicId,
        sidebarConfig.project_public_id,
        appConfig.projectPublicId,
        appConfig.project_public_id,
        appConfig.currentProjectId,
        appConfig.current_project_id,
        project.public_id,
        project.publicId,
        project.project_public_id,
        project.projectPublicId,
        project.id,
        project.project_id,
        getElementDatasetValue(element, "projectSidebarCurrentProjectId", ""),
        getElementDatasetValue(element, "currentProjectId", ""),
        getElementDatasetValue(root, "projectPublicId", ""),
        getElementDatasetValue(root, "projectId", ""),
        getProjectIdFromLocation()
      ];

      for (var index = 0; index < candidates.length; index += 1) {
        var value = trimString(candidates[index], "");

        if (value && value !== "new") {
          return value;
        }
      }

      return "";
    } catch (error) {
      return "";
    }
  }

  function getCurrentChatId(options) {
    try {
      var opts = isObject(options) ? options : {};
      var appConfig = getAppConfig();
      var sidebarConfig = getProjectSidebarConfig(appConfig);
      var element = opts.element || querySidebarElement();

      var candidates = [
        opts.currentChatId,
        opts.current_chat_id,
        sidebarConfig.currentChatId,
        sidebarConfig.current_chat_id,
        appConfig.chatId,
        appConfig.chat_id,
        appConfig.currentChatId,
        appConfig.current_chat_id,
        getElementDatasetValue(element, "projectSidebarCurrentChatId", ""),
        getQueryParam("chat_id"),
        getQueryParam("chatId"),
        getChatIdFromPath()
      ];

      for (var index = 0; index < candidates.length; index += 1) {
        var value = trimString(candidates[index], "");

        if (value) {
          return value;
        }
      }

      return "";
    } catch (error) {
      return "";
    }
  }

  function getConfig(options) {
    try {
      var opts = isObject(options) ? options : {};
      var appConfig = getAppConfig();
      var sidebarConfig = getProjectSidebarConfig(appConfig);
      var projectsApi = getProjectsApiConfig(appConfig);
      var element = opts.element || querySidebarElement();

      var storageKey =
        trimString(opts.storageKey, "") ||
        trimString(opts.storage_key, "") ||
        trimString(sidebarConfig.storageKey, "") ||
        trimString(sidebarConfig.storage_key, "") ||
        trimString(getElementDatasetValue(element, "projectSidebarStorageKey", ""), "") ||
        DEFAULT_STORAGE_KEY;

      var routeBase =
        trimString(opts.routeBase, "") ||
        trimString(opts.route_base, "") ||
        trimString(sidebarConfig.routeBase, "") ||
        trimString(sidebarConfig.route_base, "") ||
        trimString(appConfig.projectRouteBase, "") ||
        trimString(appConfig.project_route_base, "") ||
        DEFAULT_ROUTE_BASE;

      var apiPath =
        trimString(opts.apiPath, "") ||
        trimString(opts.api_path, "") ||
        trimString(sidebarConfig.apiPath, "") ||
        trimString(sidebarConfig.api_path, "") ||
        trimString(sidebarConfig.sidebarApiPath, "") ||
        trimString(sidebarConfig.sidebar_api_path, "") ||
        trimString(projectsApi.sidebar, "") ||
        trimString(projectsApi.sidebarPath, "") ||
        trimString(projectsApi.sidebar_path, "") ||
        DEFAULT_API_PATH;

      var currentChatId = getCurrentChatId({
        element: element,
        currentChatId: opts.currentChatId || opts.current_chat_id
      });

      var currentProjectId = getCurrentProjectId({
        element: element,
        currentProjectId: opts.currentProjectId || opts.current_project_id
      });

      var currentProject = getCurrentProject();

      var currentTitle =
        trimString(opts.currentTitle, "") ||
        trimString(opts.current_title, "") ||
        trimString(sidebarConfig.currentTitle, "") ||
        trimString(sidebarConfig.current_title, "") ||
        trimString(currentProject.name, "") ||
        trimString(currentProject.display_name, "") ||
        trimString(currentProject.displayName, "") ||
        trimString(getElementDatasetValue(element, "projectSidebarCurrentTitle", ""), "") ||
        DEFAULT_CURRENT_TITLE;

      var currentSubtitle =
        trimString(opts.currentSubtitle, "") ||
        trimString(opts.current_subtitle, "") ||
        trimString(sidebarConfig.currentSubtitle, "") ||
        trimString(sidebarConfig.current_subtitle, "") ||
        trimString(currentProject.address_text, "") ||
        trimString(currentProject.setup_status, "") ||
        DEFAULT_CURRENT_SUBTITLE;

      var defaultWidth = clampNumber(
        opts.defaultWidth ||
          opts.default_width ||
          sidebarConfig.defaultWidth ||
          sidebarConfig.default_width ||
          getElementDatasetValue(element, "projectSidebarDefaultWidth", ""),
        180,
        720,
        280
      );

      var minWidth = clampNumber(
        opts.minWidth ||
          opts.min_width ||
          sidebarConfig.minWidth ||
          sidebarConfig.min_width ||
          getElementDatasetValue(element, "projectSidebarMinWidth", ""),
        160,
        720,
        220
      );

      var maxWidth = clampNumber(
        opts.maxWidth ||
          opts.max_width ||
          sidebarConfig.maxWidth ||
          sidebarConfig.max_width ||
          getElementDatasetValue(element, "projectSidebarMaxWidth", ""),
        minWidth,
        960,
        420
      );

      var collapsedWidth = clampNumber(
        opts.collapsedWidth ||
          opts.collapsed_width ||
          sidebarConfig.collapsedWidth ||
          sidebarConfig.collapsed_width ||
          getElementDatasetValue(element, "projectSidebarCollapsedWidth", ""),
        48,
        120,
        64
      );

      return {
        version: INTERNAL_VERSION,
        enabled: toBooleanSafe(
          opts.enabled !== undefined ? opts.enabled : sidebarConfig.enabled,
          true
        ),
        fetchEnabled: toBooleanSafe(
          opts.fetchEnabled !== undefined
            ? opts.fetchEnabled
            : opts.fetch_enabled !== undefined
              ? opts.fetch_enabled
              : sidebarConfig.fetchEnabled !== undefined
                ? sidebarConfig.fetchEnabled
                : sidebarConfig.fetch_enabled !== undefined
                  ? sidebarConfig.fetch_enabled
                  : true,
          true
        ),
        storageKey: storageKey,
        routeBase: routeBase,
        apiPath: apiPath,
        currentChatId: currentChatId,
        currentProjectId: currentProjectId,
        currentTitle: currentTitle,
        currentSubtitle: currentSubtitle,
        defaultWidth: defaultWidth,
        minWidth: minWidth,
        maxWidth: maxWidth,
        collapsedWidth: collapsedWidth,
        maxRecentItems: clampNumber(
          opts.maxRecentItems ||
            opts.max_recent_items ||
            sidebarConfig.maxRecentItems ||
            sidebarConfig.max_recent_items,
          1,
          500,
          DEFAULT_MAX_RECENT_ITEMS
        ),
        maxCachedApiItems: clampNumber(
          opts.maxCachedApiItems ||
            opts.max_cached_api_items ||
            sidebarConfig.maxCachedApiItems ||
            sidebarConfig.max_cached_api_items,
          1,
          1000,
          DEFAULT_MAX_CACHED_API_ITEMS
        ),
        appConfig: appConfig,
        sidebarConfig: sidebarConfig,
        projectsApi: projectsApi,
        currentProject: currentProject,
        element: element
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        enabled: true,
        fetchEnabled: true,
        storageKey: DEFAULT_STORAGE_KEY,
        routeBase: DEFAULT_ROUTE_BASE,
        apiPath: DEFAULT_API_PATH,
        currentChatId: "",
        currentProjectId: "",
        currentTitle: DEFAULT_CURRENT_TITLE,
        currentSubtitle: DEFAULT_CURRENT_SUBTITLE,
        defaultWidth: 280,
        minWidth: 220,
        maxWidth: 420,
        collapsedWidth: 64,
        maxRecentItems: DEFAULT_MAX_RECENT_ITEMS,
        maxCachedApiItems: DEFAULT_MAX_CACHED_API_ITEMS,
        appConfig: {},
        sidebarConfig: {},
        projectsApi: {},
        currentProject: {},
        element: null
      };
    }
  }

  function hasLocalStorage() {
    try {
      var win = getWindow();
      var storage = win.localStorage;

      if (!storage) {
        return false;
      }

      var key = "__vp_project_sidebar_storage_test__";
      storage.setItem(key, "1");
      storage.removeItem(key);
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

  function writeFullStorage(storageKey, value) {
    try {
      var key = trimString(storageKey, DEFAULT_STORAGE_KEY);

      if (!hasLocalStorage()) {
        return false;
      }

      var payload = isObject(value) ? value : {};
      payload.version = payload.version || INTERNAL_VERSION;
      payload.updatedAt = nowIso();

      getWindow().localStorage.setItem(key, safeJsonStringify(payload));
      return true;
    } catch (error) {
      return false;
    }
  }

  function writeStorage(storageKey, patch) {
    try {
      var current = readStorage(storageKey);
      var update = isObject(patch) ? patch : {};
      var next = shallowClone(current);

      Object.keys(update).forEach(function applyPatchKey(key) {
        next[key] = update[key];
      });

      next.version = next.version || INTERNAL_VERSION;
      next.updatedAt = nowIso();

      return writeFullStorage(storageKey, next);
    } catch (error) {
      return false;
    }
  }

  function readRecentItems(storageKey) {
    try {
      var storage = readStorage(storageKey);

      if (isArray(storage.recentProjects)) {
        return storage.recentProjects;
      }

      if (isArray(storage.items)) {
        return storage.items;
      }

      if (isArray(storage.projects)) {
        return storage.projects;
      }

      return [];
    } catch (error) {
      return [];
    }
  }

  function readCachedApiItems(storageKey) {
    try {
      var storage = readStorage(storageKey);

      if (isArray(storage.cachedApiItems)) {
        return storage.cachedApiItems;
      }

      if (isArray(storage.cachedProjects)) {
        return storage.cachedProjects;
      }

      if (isArray(storage.serverProjects)) {
        return storage.serverProjects;
      }

      return [];
    } catch (error) {
      return [];
    }
  }

  function writeCachedApiItems(items, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var list = isArray(items) ? items : [];
      var storage = readStorage(config.storageKey);

      storage.version = storage.version || INTERNAL_VERSION;
      storage.cachedApiItems = list.slice(0, config.maxCachedApiItems).map(function serializeCached(item) {
        return serializeStorageItem(item, SOURCE_CACHE);
      });
      storage.cachedApiItemsUpdatedAt = nowIso();
      storage.updatedAt = nowIso();

      return writeFullStorage(config.storageKey, storage);
    } catch (error) {
      return false;
    }
  }

  function normalizeRouteBase(routeBase) {
    try {
      var base = trimString(routeBase, DEFAULT_ROUTE_BASE);

      if (!base) {
        return DEFAULT_ROUTE_BASE;
      }

      return base;
    } catch (error) {
      return DEFAULT_ROUTE_BASE;
    }
  }

  function buildProjectHref(projectId, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var id = trimString(projectId, "");

      if (!id || id === "new") {
        return "/project=new";
      }

      var routeBase = normalizeRouteBase(opts.routeBase || opts.route_base || config.routeBase);

      if (routeBase === "/" || routeBase === "") {
        return "/project=" + encodeURIComponent(id);
      }

      if (routeBase.indexOf("project=") !== -1) {
        return routeBase.replace(/project=[^/?#&]*/g, "project=" + encodeURIComponent(id));
      }

      var separator = routeBase.indexOf("?") === -1 ? "?" : "&";
      return routeBase + separator + "project=" + encodeURIComponent(id);
    } catch (error) {
      return "/project=new";
    }
  }

  function buildChatHref(chatId, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var routeBase = normalizeRouteBase(opts.routeBase || opts.route_base || config.routeBase);
      var id = trimString(chatId, "");

      if (!id) {
        return routeBase;
      }

      var separator = routeBase.indexOf("?") === -1 ? "?" : "&";
      return routeBase + separator + "chat_id=" + encodeURIComponent(id);
    } catch (error) {
      return DEFAULT_ROUTE_BASE;
    }
  }

  function normalizeInitial(title) {
    try {
      var text = trimString(title, "P");

      if (!text) {
        return "P";
      }

      var first = text.charAt(0).toUpperCase();

      if (!first || first.trim() === "") {
        return "P";
      }

      return first;
    } catch (error) {
      return "P";
    }
  }

  function pickFirstNonEmpty(values, fallback) {
    try {
      if (!isArray(values)) {
        return fallback || "";
      }

      for (var index = 0; index < values.length; index += 1) {
        var value = trimString(values[index], "");

        if (value) {
          return value;
        }
      }

      return fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function stableHash(value) {
    try {
      var input = toStringSafe(value, "");
      var hash = 0;

      if (!input) {
        return "0";
      }

      for (var index = 0; index < input.length; index += 1) {
        hash = ((hash << 5) - hash + input.charCodeAt(index)) | 0;
      }

      return Math.abs(hash).toString(36);
    } catch (error) {
      return "0";
    }
  }

  function normalizeItem(raw, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var item = isObject(raw) ? raw : {};

      var projectId = pickFirstNonEmpty(
        [
          item.projectId,
          item.project_id,
          item.public_id,
          item.publicId,
          item.projectPublicId,
          item.project_public_id,
          item.id
        ],
        ""
      );

      var chatId = pickFirstNonEmpty(
        [
          item.chatId,
          item.chat_id,
          item.conversationId,
          item.conversation_id,
          item.conversation
        ],
        ""
      );

      var id = pickFirstNonEmpty(
        [
          item.id,
          projectId,
          item.workspaceId,
          item.workspace_id,
          chatId
        ],
        ""
      );

      if (!projectId && id && id !== chatId) {
        projectId = id;
      }

      var title = pickFirstNonEmpty(
        [
          item.title,
          item.name,
          item.label,
          item.display_name,
          item.displayName,
          item.projectName,
          item.project_name,
          item.workspaceName,
          item.workspace_name
        ],
        ""
      );

      if (!title && projectId && projectId === config.currentProjectId) {
        title = config.currentTitle;
      }

      if (!title && chatId && chatId === config.currentChatId) {
        title = config.currentTitle;
      }

      if (!title) {
        title = "Unbenanntes Projekt";
      }

      var subtitle = pickFirstNonEmpty(
        [
          item.subtitle,
          item.address_text,
          item.addressText,
          item.description,
          item.summary,
          item.statusLabel,
          item.status_label,
          item.setup_status,
          item.setupStatus,
          item.workspaceMode,
          item.workspace_mode
        ],
        ""
      );

      if (!subtitle) {
        subtitle = projectId ? "Projekt" : chatId ? "Workspace" : "Projekt";
      }

      var href = pickFirstNonEmpty(
        [
          item.href,
          item.url,
          item.link,
          item.projectPublicUrl,
          item.project_public_url
        ],
        ""
      );

      if (!href && projectId) {
        href = buildProjectHref(projectId, { config: config });
      }

      if (!href && chatId) {
        href = buildChatHref(chatId, { config: config });
      }

      if (!href) {
        href = buildProjectHref("new", { config: config });
      }

      var updatedAt = pickFirstNonEmpty(
        [
          item.updatedAt,
          item.updated_at,
          item.modifiedAt,
          item.modified_at,
          item.lastUsedAt,
          item.last_used_at,
          item.createdAt,
          item.created_at
        ],
        ""
      );

      var source = pickFirstNonEmpty([item.source, opts.source], SOURCE_CLIENT);
      var isActive = toBooleanSafe(item.isActive || item.is_active, false);

      if (projectId && config.currentProjectId && projectId === config.currentProjectId) {
        isActive = true;
      }

      if (chatId && config.currentChatId && chatId === config.currentChatId) {
        isActive = true;
      }

      var isConfigured = toBooleanSafe(
        item.isConfigured !== undefined
          ? item.isConfigured
          : item.is_configured !== undefined
            ? item.is_configured
            : item.setup_status === "configured",
        false
      );

      var normalized = {
        id: id || "project-" + stableHash(title + "|" + href),
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
        isActive: isActive,
        is_active: isActive,
        isConfigured: isConfigured,
        is_configured: isConfigured,
        setupStatus: item.setupStatus || item.setup_status || (isConfigured ? "configured" : "draft"),
        setup_status: item.setup_status || item.setupStatus || (isConfigured ? "configured" : "draft"),
        source: source,
        initial: normalizeInitial(item.initial || title),
        updatedAt: updatedAt,
        updated_at: updatedAt,
        raw: item
      };

      if (item.disabled !== undefined) {
        normalized.disabled = toBooleanSafe(item.disabled, false);
      }

      if (item.hidden !== undefined) {
        normalized.hidden = toBooleanSafe(item.hidden, false);
      }

      if (item.permissions !== undefined && isObject(item.permissions)) {
        normalized.permissions = shallowClone(item.permissions);
      }

      if (item.access !== undefined && isObject(item.access)) {
        normalized.access = shallowClone(item.access);
      }

      if (item.meta !== undefined && isObject(item.meta)) {
        normalized.meta = shallowClone(item.meta);
      }

      return normalized;
    } catch (error) {
      return null;
    }
  }

  function dedupeItems(items) {
    try {
      if (!isArray(items)) {
        return [];
      }

      var seen = {};
      var result = [];

      items.forEach(function addIfNew(item) {
        try {
          if (!item || item.hidden) {
            return;
          }

          var key =
            trimString(item.projectId, "") ||
            trimString(item.public_id, "") ||
            trimString(item.chatId, "") ||
            trimString(item.id, "") ||
            trimString(item.href, "") ||
            stableHash(item.title + "|" + item.subtitle);

          if (!key || seen[key]) {
            return;
          }

          seen[key] = true;
          result.push(item);
        } catch (error) {
          /* ignore one broken item */
        }
      });

      return result;
    } catch (error) {
      return [];
    }
  }

  function sortItems(items, options) {
    try {
      var opts = isObject(options) ? options : {};
      var shouldSort = opts.sort !== false;

      if (!shouldSort || !isArray(items)) {
        return items || [];
      }

      return items.slice().sort(function compareItems(a, b) {
        try {
          if (a.isActive && !b.isActive) {
            return -1;
          }

          if (!a.isActive && b.isActive) {
            return 1;
          }

          var aTime = Date.parse(a.updatedAt || "");
          var bTime = Date.parse(b.updatedAt || "");

          if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) {
            return bTime - aTime;
          }

          if (Number.isFinite(aTime) && !Number.isFinite(bTime)) {
            return -1;
          }

          if (!Number.isFinite(aTime) && Number.isFinite(bTime)) {
            return 1;
          }

          return String(a.title || "").localeCompare(String(b.title || ""), "de", {
            sensitivity: "base"
          });
        } catch (error) {
          return 0;
        }
      });
    } catch (error) {
      return items || [];
    }
  }

  function normalizeItems(rawItems, options) {
    try {
      var opts = isObject(options) ? options : {};
      var list = isArray(rawItems) ? rawItems : [];
      var normalized = [];

      list.forEach(function normalizeOne(raw) {
        var item = normalizeItem(raw, opts);

        if (item) {
          normalized.push(item);
        }
      });

      return sortItems(dedupeItems(normalized), opts);
    } catch (error) {
      return [];
    }
  }

  function buildCurrentItem(options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var project = config.currentProject || getCurrentProject();

      var projectId = trimString(
        opts.currentProjectId ||
          config.currentProjectId ||
          project.public_id ||
          project.publicId ||
          project.project_public_id ||
          project.projectPublicId ||
          project.id,
        ""
      );

      var chatId = trimString(opts.currentChatId || config.currentChatId || project.conversation_id || project.conversationId, "");
      var title = trimString(
        opts.currentTitle ||
          config.currentTitle ||
          project.name ||
          project.display_name ||
          project.displayName,
        DEFAULT_CURRENT_TITLE
      );
      var subtitle = trimString(
        opts.currentSubtitle ||
          config.currentSubtitle ||
          project.address_text ||
          project.setup_status,
        DEFAULT_CURRENT_SUBTITLE
      );

      return normalizeItem(
        {
          id: projectId || chatId || "current",
          projectId: projectId,
          public_id: projectId,
          chatId: chatId,
          conversationId: chatId,
          title: title,
          subtitle: subtitle,
          href: projectId ? buildProjectHref(projectId, { config: config }) : buildChatHref(chatId, { config: config }),
          isActive: true,
          isConfigured: toBooleanSafe(project.is_configured || project.isConfigured, false),
          setup_status: project.setup_status || "draft",
          source: SOURCE_CURRENT,
          updatedAt: nowIso()
        },
        {
          config: config,
          source: SOURCE_CURRENT
        }
      );
    } catch (error) {
      return {
        id: "current",
        projectId: "",
        project_id: "",
        public_id: "",
        title: DEFAULT_CURRENT_TITLE,
        subtitle: DEFAULT_CURRENT_SUBTITLE,
        chatId: "",
        href: "/project=new",
        isActive: true,
        is_active: true,
        source: SOURCE_FALLBACK,
        initial: "P",
        updatedAt: nowIso(),
        raw: {}
      };
    }
  }

  function getConfiguredItems(options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var appConfig = config.appConfig || getAppConfig();
      var sidebarConfig = config.sidebarConfig || getProjectSidebarConfig(appConfig);
      var win = getWindow();

      if (isArray(opts.items)) {
        return {
          items: opts.items,
          source: "options"
        };
      }

      if (isArray(sidebarConfig.items)) {
        return {
          items: sidebarConfig.items,
          source: SOURCE_SERVER
        };
      }

      if (isArray(sidebarConfig.projects)) {
        return {
          items: sidebarConfig.projects,
          source: SOURCE_SERVER
        };
      }

      if (isArray(appConfig.projects)) {
        return {
          items: appConfig.projects,
          source: SOURCE_SERVER
        };
      }

      if (isArray(appConfig.projectItems)) {
        return {
          items: appConfig.projectItems,
          source: SOURCE_SERVER
        };
      }

      if (isArray(appConfig.project_items)) {
        return {
          items: appConfig.project_items,
          source: SOURCE_SERVER
        };
      }

      if (isArray(win.__VECTOPLAN_PROJECT_SIDEBAR_ITEMS__)) {
        return {
          items: win.__VECTOPLAN_PROJECT_SIDEBAR_ITEMS__,
          source: SOURCE_CLIENT
        };
      }

      if (isArray(win.__VECTOPLAN_PROJECTS__)) {
        return {
          items: win.__VECTOPLAN_PROJECTS__,
          source: SOURCE_CLIENT
        };
      }

      return {
        items: [],
        source: ""
      };
    } catch (error) {
      return {
        items: [],
        source: ""
      };
    }
  }

  function ensureCurrentItem(items, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var list = isArray(items) ? items.slice() : [];
      var currentChatId = trimString(config.currentChatId, "");
      var currentProjectId = trimString(config.currentProjectId, "");

      if (!currentChatId && !currentProjectId && list.length > 0) {
        return list;
      }

      var hasCurrent = list.some(function isCurrent(item) {
        return !!item && (
          item.isActive ||
          (currentProjectId && item.projectId === currentProjectId) ||
          (currentProjectId && item.public_id === currentProjectId) ||
          (currentChatId && item.chatId === currentChatId)
        );
      });

      if (!hasCurrent) {
        var currentItem = buildCurrentItem({ config: config });

        if (currentItem) {
          list.unshift(currentItem);
        }
      }

      return list.map(function markCurrent(item) {
        if (!item) {
          return item;
        }

        if (currentProjectId && (item.projectId === currentProjectId || item.public_id === currentProjectId)) {
          item.isActive = true;
          item.is_active = true;
        }

        if (currentChatId && item.chatId === currentChatId) {
          item.isActive = true;
          item.is_active = true;
        }

        return item;
      });
    } catch (error) {
      return items || [];
    }
  }

  function finalizeItems(rawItems, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var source = opts.source || SOURCE_CLIENT;

      var normalized = normalizeItems(rawItems, {
        config: config,
        source: source,
        sort: opts.sort
      });

      normalized = ensureCurrentItem(normalized, { config: config });
      normalized = dedupeItems(normalized);
      normalized = sortItems(normalized, { sort: opts.sort });

      return normalized;
    } catch (error) {
      return [];
    }
  }

  function loadItems(options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var configured = getConfiguredItems({
        config: config,
        items: opts.items
      });

      var rawItems = configured.items;
      var source = configured.source;

      if (!rawItems.length) {
        rawItems = readCachedApiItems(config.storageKey);
        source = SOURCE_CACHE;
      }

      if (!rawItems.length) {
        rawItems = readRecentItems(config.storageKey);
        source = SOURCE_STORAGE;
      }

      var normalized = finalizeItems(rawItems, {
        config: config,
        source: source || SOURCE_CLIENT,
        sort: opts.sort
      });

      return {
        ok: true,
        source: source || SOURCE_FALLBACK,
        items: normalized,
        count: normalized.length,
        currentChatId: config.currentChatId,
        currentProjectId: config.currentProjectId,
        config: config,
        loadedAt: nowIso(),
        async: false,
        apiUsed: false
      };
    } catch (error) {
      var fallbackConfig = getConfig(options);
      var fallbackItem = buildCurrentItem({ config: fallbackConfig });

      return {
        ok: false,
        source: SOURCE_FALLBACK,
        items: fallbackItem ? [fallbackItem] : [],
        count: fallbackItem ? 1 : 0,
        currentChatId: fallbackConfig.currentChatId,
        currentProjectId: fallbackConfig.currentProjectId,
        config: fallbackConfig,
        loadedAt: nowIso(),
        async: false,
        apiUsed: false,
        error: normalizeError(error)
      };
    }
  }

  function extractApiItems(payload) {
    try {
      if (!payload) {
        return [];
      }

      if (isArray(payload)) {
        return payload;
      }

      if (!isObject(payload)) {
        return [];
      }

      if (isArray(payload.items)) {
        return payload.items;
      }

      if (isArray(payload.sidebar_items)) {
        return payload.sidebar_items;
      }

      if (isArray(payload.sidebarItems)) {
        return payload.sidebarItems;
      }

      if (isArray(payload.projects)) {
        return payload.projects;
      }

      if (isArray(payload.data)) {
        return payload.data;
      }

      return [];
    } catch (error) {
      return [];
    }
  }

  async function fetchApiItems(options) {
    var config = null;

    try {
      var opts = isObject(options) ? options : {};
      config = opts.config || getConfig(opts);

      if (!config.fetchEnabled || opts.skipApi || opts.skip_api) {
        return {
          ok: false,
          skipped: true,
          source: SOURCE_API,
          items: [],
          count: 0,
          config: config,
          loadedAt: nowIso(),
          error: {
            name: "Skipped",
            message: "API fetch skipped"
          }
        };
      }

      var win = getWindow();

      if (typeof win.fetch !== "function") {
        return {
          ok: false,
          source: SOURCE_API,
          items: [],
          count: 0,
          config: config,
          loadedAt: nowIso(),
          error: {
            name: "FetchUnavailable",
            message: "fetch is unavailable"
          }
        };
      }

      var apiPath = trimString(opts.apiPath || opts.api_path || config.apiPath, DEFAULT_API_PATH);
      var url = new URL(apiPath, win.location && win.location.origin ? win.location.origin : "http://localhost");

      if (config.currentProjectId) {
        url.searchParams.set("current_project_id", config.currentProjectId);
      }

      if (config.currentChatId) {
        url.searchParams.set("current_chat_id", config.currentChatId);
      }

      url.searchParams.set("_", String(Date.now()));

      var requestUrl = url.pathname + "?" + url.searchParams.toString();

      var response = await win.fetch(requestUrl, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json"
        }
      });

      var text = await response.text();
      var payload = safeJsonParse(text, null);

      if (!response.ok) {
        var fetchError = new Error(
          payload && payload.error
            ? payload.error
            : "Project sidebar API failed with status " + response.status
        );
        fetchError.status = response.status;
        fetchError.payload = payload;
        throw fetchError;
      }

      var rawItems = extractApiItems(payload);
      var normalized = finalizeItems(rawItems, {
        config: config,
        source: SOURCE_API,
        sort: opts.sort
      });

      writeCachedApiItems(normalized, { config: config });

      return {
        ok: true,
        source: SOURCE_API,
        items: normalized,
        count: normalized.length,
        currentChatId: config.currentChatId,
        currentProjectId: config.currentProjectId,
        config: config,
        loadedAt: nowIso(),
        payload: payload,
        apiPath: requestUrl
      };
    } catch (error) {
      return {
        ok: false,
        source: SOURCE_API,
        items: [],
        count: 0,
        currentChatId: config ? config.currentChatId : "",
        currentProjectId: config ? config.currentProjectId : "",
        config: config || getConfig(options),
        loadedAt: nowIso(),
        error: normalizeError(error)
      };
    }
  }

  async function loadItemsAsync(options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);

      if (config.fetchEnabled && !opts.skipApi && !opts.skip_api) {
        var apiResult = await fetchApiItems({
          config: config,
          apiPath: opts.apiPath || opts.api_path,
          sort: opts.sort
        });

        if (apiResult && apiResult.ok && apiResult.items.length) {
          return {
            ok: true,
            source: SOURCE_API,
            items: apiResult.items,
            count: apiResult.items.length,
            currentChatId: config.currentChatId,
            currentProjectId: config.currentProjectId,
            config: config,
            loadedAt: apiResult.loadedAt,
            async: true,
            apiUsed: true,
            payload: apiResult.payload
          };
        }

        if (opts.requireApi || opts.require_api) {
          return apiResult;
        }
      }

      var fallback = loadItems({
        config: config,
        items: opts.items,
        sort: opts.sort
      });

      fallback.async = true;
      fallback.apiUsed = false;

      return fallback;
    } catch (error) {
      var fallbackResult = loadItems(options);
      fallbackResult.async = true;
      fallbackResult.apiUsed = false;
      fallbackResult.error = normalizeError(error);
      return fallbackResult;
    }
  }

  function serializeStorageItem(item, source) {
    try {
      var normalized = normalizeItem(item, { source: source || SOURCE_STORAGE });

      if (!normalized) {
        return {};
      }

      return {
        id: normalized.id,
        projectId: normalized.projectId,
        project_id: normalized.project_id,
        public_id: normalized.public_id,
        title: normalized.title,
        subtitle: normalized.subtitle,
        chatId: normalized.chatId,
        chat_id: normalized.chat_id,
        conversationId: normalized.conversationId,
        conversation_id: normalized.conversation_id,
        href: normalized.href,
        source: source || normalized.source || SOURCE_STORAGE,
        initial: normalized.initial,
        isConfigured: normalized.isConfigured,
        is_configured: normalized.is_configured,
        setupStatus: normalized.setupStatus,
        setup_status: normalized.setup_status,
        updatedAt: normalized.updatedAt || nowIso()
      };
    } catch (error) {
      return {};
    }
  }

  function rememberItem(item, options) {
    try {
      var opts = isObject(options) ? options : {};
      var config = opts.config || getConfig(opts);
      var normalized = normalizeItem(item, {
        config: config,
        source: item && item.source ? item.source : SOURCE_STORAGE
      });

      if (!normalized) {
        return false;
      }

      normalized.updatedAt = normalized.updatedAt || nowIso();

      var storage = readStorage(config.storageKey);
      var recent = normalizeItems(readRecentItems(config.storageKey), {
        config: config,
        source: SOURCE_STORAGE,
        sort: false
      });

      recent.unshift(normalized);
      recent = dedupeItems(recent);
      recent = sortItems(recent, { sort: true }).slice(0, config.maxRecentItems);

      storage.version = storage.version || INTERNAL_VERSION;
      storage.recentProjects = recent.map(function serializeRecent(project) {
        return serializeStorageItem(project, SOURCE_STORAGE);
      });

      storage.lastActiveProjectId = normalized.projectId || normalized.public_id || storage.lastActiveProjectId || "";
      storage.lastActiveChatId = normalized.chatId || storage.lastActiveChatId || "";
      storage.updatedAt = nowIso();

      return writeFullStorage(config.storageKey, storage);
    } catch (error) {
      return false;
    }
  }

  function rememberCurrent(options) {
    try {
      var config = getConfig(options);
      var current = buildCurrentItem({ config: config });

      if (!current) {
        return false;
      }

      return rememberItem(current, { config: config });
    } catch (error) {
      return false;
    }
  }

  function findItemByProjectId(items, projectId) {
    try {
      var id = trimString(projectId, "");

      if (!id || !isArray(items)) {
        return null;
      }

      for (var index = 0; index < items.length; index += 1) {
        if (
          items[index] &&
          (
            items[index].projectId === id ||
            items[index].project_id === id ||
            items[index].public_id === id ||
            items[index].id === id
          )
        ) {
          return items[index];
        }
      }

      return null;
    } catch (error) {
      return null;
    }
  }

  function findItemByChatId(items, chatId) {
    try {
      var id = trimString(chatId, "");

      if (!id || !isArray(items)) {
        return null;
      }

      for (var index = 0; index < items.length; index += 1) {
        if (
          items[index] &&
          (
            items[index].chatId === id ||
            items[index].chat_id === id ||
            items[index].conversationId === id ||
            items[index].conversation_id === id
          )
        ) {
          return items[index];
        }
      }

      return null;
    } catch (error) {
      return null;
    }
  }

  function findActiveItem(items) {
    try {
      if (!isArray(items)) {
        return null;
      }

      for (var index = 0; index < items.length; index += 1) {
        if (items[index] && items[index].isActive) {
          return items[index];
        }
      }

      return null;
    } catch (error) {
      return null;
    }
  }

  function filterItems(items, query) {
    try {
      var list = isArray(items) ? items : [];
      var search = trimString(query, "").toLowerCase();

      if (!search) {
        return list.slice();
      }

      return list.filter(function matchItem(item) {
        try {
          var haystack = [
            item.title,
            item.subtitle,
            item.projectId,
            item.project_id,
            item.public_id,
            item.chatId,
            item.chat_id,
            item.conversationId,
            item.conversation_id,
            item.id,
            item.source
          ]
            .map(function toSearchText(value) {
              return trimString(value, "").toLowerCase();
            })
            .join(" ");

          return haystack.indexOf(search) !== -1;
        } catch (error) {
          return false;
        }
      });
    } catch (error) {
      return [];
    }
  }

  function clearRecentItems(options) {
    try {
      var config = getConfig(options);
      var storage = readStorage(config.storageKey);

      storage.recentProjects = [];
      storage.updatedAt = nowIso();

      return writeFullStorage(config.storageKey, storage);
    } catch (error) {
      return false;
    }
  }

  function clearCachedApiItems(options) {
    try {
      var config = getConfig(options);
      var storage = readStorage(config.storageKey);

      storage.cachedApiItems = [];
      storage.cachedApiItemsUpdatedAt = "";
      storage.updatedAt = nowIso();

      return writeFullStorage(config.storageKey, storage);
    } catch (error) {
      return false;
    }
  }

  function createDebugSnapshot(options) {
    try {
      var result = loadItems(options);

      return {
        ok: result.ok,
        source: result.source,
        count: result.count,
        currentChatId: result.currentChatId,
        currentProjectId: result.currentProjectId,
        activeItem: findActiveItem(result.items),
        storageKey: result.config.storageKey,
        routeBase: result.config.routeBase,
        apiPath: result.config.apiPath,
        fetchEnabled: result.config.fetchEnabled,
        loadedAt: result.loadedAt,
        apiUsed: result.apiUsed
      };
    } catch (error) {
      return {
        ok: false,
        error: normalizeError(error)
      };
    }
  }

  var api = {
    version: INTERNAL_VERSION,

    constants: {
      storageKey: DEFAULT_STORAGE_KEY,
      routeBase: DEFAULT_ROUTE_BASE,
      apiPath: DEFAULT_API_PATH,
      maxRecentItems: DEFAULT_MAX_RECENT_ITEMS,
      maxCachedApiItems: DEFAULT_MAX_CACHED_API_ITEMS,
      sources: {
        api: SOURCE_API,
        server: SOURCE_SERVER,
        client: SOURCE_CLIENT,
        current: SOURCE_CURRENT,
        storage: SOURCE_STORAGE,
        cache: SOURCE_CACHE,
        fallback: SOURCE_FALLBACK
      }
    },

    getConfig: getConfig,
    getAppConfig: getAppConfig,
    getProjectSidebarConfig: getProjectSidebarConfig,
    getProjectsApiConfig: getProjectsApiConfig,
    getCurrentProject: getCurrentProject,
    getCurrentProjectId: getCurrentProjectId,
    getCurrentChatId: getCurrentChatId,

    buildProjectHref: buildProjectHref,
    buildChatHref: buildChatHref,

    normalizeItem: normalizeItem,
    normalizeItems: normalizeItems,
    buildCurrentItem: buildCurrentItem,
    loadItems: loadItems,
    loadItemsAsync: loadItemsAsync,
    fetchApiItems: fetchApiItems,
    ensureCurrentItem: ensureCurrentItem,

    readStorage: readStorage,
    writeStorage: writeStorage,
    readRecentItems: readRecentItems,
    readCachedApiItems: readCachedApiItems,
    writeCachedApiItems: writeCachedApiItems,
    rememberItem: rememberItem,
    rememberCurrent: rememberCurrent,
    clearRecentItems: clearRecentItems,
    clearCachedApiItems: clearCachedApiItems,

    findItemByProjectId: findItemByProjectId,
    findItemByChatId: findItemByChatId,
    findActiveItem: findActiveItem,
    filterItems: filterItems,

    createDebugSnapshot: createDebugSnapshot,
    normalizeError: normalizeError,

    _private: {
      trimString: trimString,
      toBooleanSafe: toBooleanSafe,
      toNumberSafe: toNumberSafe,
      clampNumber: clampNumber,
      stableHash: stableHash,
      querySidebarElement: querySidebarElement,
      queryAppRoot: queryAppRoot,
      nowIso: nowIso,
      extractApiItems: extractApiItems,
      finalizeItems: finalizeItems,
      serializeStorageItem: serializeStorageItem
    }
  };

  try {
    global[EXPORT_NAME] = api;
    global[LEGACY_EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectSidebarData = api;
  } catch (error) {
    /* Exports are best-effort. */
  }
})(window);