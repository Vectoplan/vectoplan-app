/* services/vectoplan-app/static/js/project/project_publication.js */

/*
  VECTOPLAN Project Publication

  Zweck:
  - Steuert und speichert Veröffentlichungseinstellungen für Projekt-Workspace-Reiter.
  - Arbeitet gegen /v1/projects/<project_id>/publication.
  - Sendet einen normalisierten Payload an routes/projects_api.py und project_publication_service.py.
  - Speichert nur Sichtbarkeit/Publication-Metadaten, keine Projekt-Basisdaten,
    keine Team-/Einladungsdaten und keine Fach-/Service-Daten.

  Sicherheitsregeln:
  - Admin, Team, Rechte, Einstellungen und Systemreferenzen werden nie als
    veröffentlichbare Workspaces gesendet.
  - Public/Unlisted bedeutet nicht automatisch, dass alle Workspaces öffentlich sind.
  - Private Projekte haben effektiv keine veröffentlichten Workspaces.
  - Demo, Public Viewer, Read-only, Auth-Ausfall, User-Block und fehlendes Manage-/Publish-Recht
    deaktivieren das Speichern im Frontend. Backend bleibt die Wahrheit.
  - Das Frontend ist nur UI-Gating; alle Entscheidungen werden serverseitig erneut geprüft.
*/

(function initVectoplanProjectPublication(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectPublication";
  var INTERNAL_VERSION = 5;

  var ROOT_SELECTOR = "[data-project-workspace]";
  var CARD_SELECTOR = "[data-project-publication-card]";
  var FORM_SELECTOR = "[data-project-form]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var EVENT_PUBLICATION_CHANGED = "vectoplan:project:publication:changed";
  var EVENT_PUBLICATION_READY = "vectoplan:project-publication:ready";
  var EVENT_PUBLICATION_ERROR = "vectoplan:project-publication:error";
  var EVENT_PROJECT_ERROR = "vectoplan:project:error";
  var EVENT_VISIBILITY_CHANGED = "vectoplan:project:visibility:changed";

  var CLASS_SELECTED = "is-selected";
  var CLASS_EFFECTIVE = "is-effective";
  var CLASS_SAVING = "is-saving";
  var CLASS_ERROR = "is-error";
  var CLASS_DIRTY = "is-dirty";
  var CLASS_DISABLED = "is-disabled";

  var DEFAULT_REQUEST_TIMEOUT_MS = 30000;
  var MAX_RESPONSE_TEXT_LENGTH = 2 * 1024 * 1024;

  var VALID_PROJECT_ROLES = {
    owner: true,
    admin: true,
    editor: true,
    viewer: true
  };

  var ROLE_CAN_PUBLISH = {
    owner: true,
    admin: true
  };

  var READY_OPERATIONAL_STATUSES = {
    ready: true,
    fallback_ready: true
  };

  var WORKSPACES = [
    "project",
    "map",
    "editor3d",
    "cad2d",
    "lv",
    "versions"
  ];

  var DEFAULT_PUBLISHED_WORKSPACES = {
    project: true,
    map: false,
    editor3d: false,
    cad2d: false,
    lv: false,
    versions: false
  };

  var FORBIDDEN_WORKSPACES = {
    admin: true,
    team: true,
    settings: true,
    permissions: true,
    system: true,
    system_refs: true,
    systemrefs: true,
    users: true,
    billing: true,
    account: true,
    auth: true
  };

  var WORKSPACE_LABELS = {
    project: "Projekt",
    map: "Map",
    editor3d: "3D",
    cad2d: "2D",
    lv: "LV",
    versions: "Versionen"
  };

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isSaving: false,
    isLoading: false,
    isDirty: false,
    canManage: false,
    canPublish: false,
    canMutate: false,
    canEdit: false,
    isNew: false,
    demoMode: false,
    persistent: false,
    publicViewer: false,
    readOnly: false,
    authUnavailable: false,
    userBlocked: false,
    accessBlocked: false,
    identityConsistent: true,
    localLinkState: "not_applicable",
    projectRole: "viewer",
    accessMode: "anonymous",

    chunkReady: false,
    chunkProvisioningStatus: "pending",
    chunkAccessSyncStatus: "disabled",
    chunkAccessRequired: false,
    publicEditor3dVerified: false,
    workspaceAvailability: {
      project: true,
      map: true,
      editor3d: false,
      cad2d: true,
      lv: true,
      versions: true
    },
    workspaceReasons: {
      editor3d: "chunk_not_ready"
    },

    requestId: "",
    projectPublicId: "",
    endpoint: "",
    initialData: null,
    currentData: null,
    lastResponse: null,
    lastError: null,
    config: null,
    refs: {},
    listeners: []
  };

  function getWindow() {
    try {
      return global || window;
    } catch (error) {
      return {};
    }
  }

  function getDocument() {
    try {
      var win = getWindow();
      return win.document || document || null;
    } catch (error) {
      return null;
    }
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function isArray(value) {
    try {
      return Array.isArray(value);
    } catch (error) {
      return Object.prototype.toString.call(value) === "[object Array]";
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

  function lowerString(value, fallback) {
    try {
      return trimString(value, fallback || "").toLowerCase();
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
          normalized === "y" ||
          normalized === "on" ||
          normalized === "ja" ||
          normalized === "enabled" ||
          normalized === "enable" ||
          normalized === "active" ||
          normalized === "ok"
        ) {
          return true;
        }

        if (
          normalized === "false" ||
          normalized === "no" ||
          normalized === "n" ||
          normalized === "off" ||
          normalized === "nein" ||
          normalized === "disabled" ||
          normalized === "disable" ||
          normalized === "inactive" ||
          normalized === "error" ||
          normalized === "failed" ||
          normalized === "null" ||
          normalized === "none" ||
          normalized === "undefined" ||
          normalized === ""
        ) {
          return false;
        }
      }

      return !!fallback;
    } catch (error) {
      return !!fallback;
    }
  }

  function safeJsonStringify(value) {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return "{}";
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

  function safeClone(value) {
    try {
      return JSON.parse(JSON.stringify(value || {}));
    } catch (error) {
      if (isObject(value)) {
        var clone = {};
        Object.keys(value).forEach(function copyKey(key) {
          clone[key] = value[key];
        });
        return clone;
      }

      return {};
    }
  }

  function createRequestId() {
    try {
      var win = getWindow();
      if (win.crypto && typeof win.crypto.randomUUID === "function") {
        return "req_" + win.crypto.randomUUID().replace(/-/g, "");
      }
      if (win.crypto && typeof win.crypto.getRandomValues === "function") {
        var bytes = new Uint8Array(16);
        win.crypto.getRandomValues(bytes);
        return "req_" + Array.prototype.map.call(bytes, function toHex(value) {
          return value.toString(16).padStart(2, "0");
        }).join("");
      }
    } catch (error) {}
    return "req_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 14);
  }

  function currentOrigin() {
    try {
      var win = getWindow();
      return win.location && win.location.origin ? win.location.origin : "";
    } catch (error) {
      return "";
    }
  }

  function normalizeSameOriginPath(value, fallback, allowedPrefix) {
    try {
      var candidate = trimString(value, fallback || "");
      if (!candidate || candidate.indexOf("\\") !== -1 || candidate.indexOf("//") === 0) {
        return fallback || "";
      }
      var origin = currentOrigin();
      var parsed = new URL(candidate, origin || "http://localhost");
      if (origin && parsed.origin !== origin) {
        return fallback || "";
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return fallback || "";
      }
      var path = parsed.pathname + parsed.search;
      if (!path.startsWith("/")) {
        return fallback || "";
      }
      if (allowedPrefix && path !== allowedPrefix && path.indexOf(allowedPrefix + "/") !== 0 && path.indexOf(allowedPrefix + "?") !== 0) {
        return fallback || "";
      }
      return path;
    } catch (error) {
      return fallback || "";
    }
  }

  function normalizePublicationEndpoint(value, projectPublicId) {
    try {
      var publicId = trimString(projectPublicId, "");
      var fallback = publicId && publicId !== "new"
        ? "/v1/projects/" + encodeURIComponent(publicId) + "/publication"
        : "";
      var endpoint = normalizeSameOriginPath(value, fallback, "/v1/projects");
      if (!endpoint) {
        return "";
      }
      var pathOnly = endpoint.split("?", 1)[0];
      var match = pathOnly.match(/^\/v1\/projects\/([^/]+)\/publication$/);
      if (!match) {
        return "";
      }
      if (publicId && publicId !== "new" && decodeURIComponent(match[1]) !== publicId) {
        return fallback;
      }
      return endpoint;
    } catch (error) {
      return "";
    }
  }

  function normalizeProjectRole(value, fallback) {
    try {
      var role = trimString(value, fallback || "").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");
      if (role === "administrator" || role === "manager") {
        role = "admin";
      } else if (role === "write" || role === "edit") {
        role = "editor";
      } else if (role === "read" || role === "reader" || role === "readonly" || role === "read_only" || role === "public_viewer") {
        role = "viewer";
      }
      return VALID_PROJECT_ROLES[role] ? role : fallback || "viewer";
    } catch (error) {
      return fallback || "viewer";
    }
  }

  function normalizeOperationalStatus(value, fallback) {
    try {
      var text = trimString(value, fallback || "pending").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");
      var aliases = {
        ready: "ready", active: "ready", complete: "ready", completed: "ready", success: "ready", succeeded: "ready", provisioned: "ready", linked: "ready", synced: "ready",
        fallback_ready: "fallback_ready", ready_with_fallback: "fallback_ready", flat_fallback: "fallback_ready",
        pending: "pending", waiting: "pending", queued: "pending", initializing: "pending", syncing: "pending", running: "pending", in_progress: "pending",
        repair: "repair_required", repair_required: "repair_required", needs_repair: "repair_required", inconsistent: "repair_required",
        failed: "failed", failure: "failed", error: "failed", unavailable: "failed",
        disabled: "disabled", off: "disabled", not_required: "disabled", skipped: "disabled"
      };
      return aliases[text] || fallback || "pending";
    } catch (error) {
      return fallback || "pending";
    }
  }

  function firstDefined(values, fallback) {
    try {
      for (var i = 0; i < values.length; i += 1) {
        if (values[i] !== undefined && values[i] !== null && values[i] !== "") {
          return values[i];
        }
      }
    } catch (error) {}
    return fallback;
  }

  function firstString(values, fallback) {
    try {
      for (var i = 0; i < values.length; i += 1) {
        var value = trimString(values[i], "");
        if (value) {
          return value;
        }
      }
    } catch (error) {}
    return fallback || "";
  }

  function sanitizeBrowserText(value) {
    try {
      var text = trimString(value, "");
      if (!text) return "";
      text = text.replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, "Bearer [redacted]");
      text = text.replace(/([?&](?:access_token|refresh_token|token|jwt|secret|password|api_key|apikey|session|csrf_token)=)[^&#\s]*/gi, "$1[redacted]");
      text = text.replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, "[redacted-email]");
      return text.length > 8000 ? text.slice(0, 8000) : text;
    } catch (error) {
      return "";
    }
  }

  function sanitizeBrowserValue(value, depth) {
    var level = typeof depth === "number" ? depth : 0;
    try {
      if (level > 7) {
        return "[truncated]";
      }
      if (value === null || value === undefined || typeof value === "boolean" || typeof value === "number") {
        return value;
      }
      if (typeof value === "string") {
        return sanitizeBrowserText(value);
      }
      if (Array.isArray(value)) {
        return value.slice(0, 200).map(function sanitizeItem(item) {
          return sanitizeBrowserValue(item, level + 1);
        });
      }
      if (!isObject(value)) {
        return trimString(value, "");
      }
      var blockedKeys = {
        authorization: true, cookie: true, cookies: true, password: true, secret: true,
        token: true, token_hash: true, access_token: true, refresh_token: true, jwt: true,
        api_key: true, apikey: true, session: true, session_id: true, csrf: true, csrf_token: true,
        raw_auth: true, raw: true, headers: true, stack: true, traceback: true,
        auth_user_id: true, authuserid: true, canonical_user_id: true, user_id: true, userid: true,
        local_user_id: true, localuserid: true, actor_user_id: true, target_user_id: true,
        email: true, auth_email: true, account_id: true, owner_user_id: true, auth_owner_user_id: true,
        metadata: true, metadata_json: true, settings: true, service_refs: true, service_links: true,
        internal_url: true, internal_base_url: true, request_context: true
      };
      var result = {};
      Object.keys(value).slice(0, 300).forEach(function sanitizeKey(key) {
        var normalized = trimString(key, "").toLowerCase().replace(/[^a-z0-9_]/g, "");
        if (!normalized || blockedKeys[normalized]) {
          return;
        }
        result[key] = sanitizeBrowserValue(value[key], level + 1);
      });
      return result;
    } catch (error) {
      return {};
    }
  }

  function sanitizePublicationForBrowser(value) {
    try {
      var data = isObject(value) ? value : {};
      var visibility = normalizeVisibility(data.visibility, "private");
      var published = sanitizeWorkspaceMap(data.published_workspaces || data.publishedWorkspaces || {});
      var effective = effectiveWorkspaces(visibility, published);
      return {
        visibility: visibility,
        published_workspaces: published,
        publishedWorkspaces: safeClone(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: toBooleanSafe(firstDefined([data.require_auth, data.requireAuth], visibility === "private"), visibility === "private"),
        requireAuth: toBooleanSafe(firstDefined([data.require_auth, data.requireAuth], visibility === "private"), visibility === "private"),
        require_project_permission: toBooleanSafe(firstDefined([data.require_project_permission, data.requireProjectPermission], visibility === "private"), visibility === "private"),
        requireProjectPermission: toBooleanSafe(firstDefined([data.require_project_permission, data.requireProjectPermission], visibility === "private"), visibility === "private")
      };
    } catch (error) {
      return {
        visibility: "private",
        published_workspaces: emptyWorkspaceMap(false),
        publishedWorkspaces: emptyWorkspaceMap(false),
        effective_published_workspaces: emptyWorkspaceMap(false),
        effectivePublishedWorkspaces: emptyWorkspaceMap(false),
        require_auth: true,
        requireAuth: true,
        require_project_permission: true,
        requireProjectPermission: true
      };
    }
  }

  function isTrustedMessageEvent(event) {
    try {
      if (!event) {
        return false;
      }
      var win = getWindow();
      if (event.source && event.source !== win && event.source !== win.parent) {
        return false;
      }
      var origin = currentOrigin();
      if (origin && event.origin && event.origin !== origin) {
        return false;
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  function normalizeError(error) {
    try {
      if (!error) {
        return { name: "Error", message: "Unknown error", code: "", status: null, requestId: "" };
      }
      if (typeof error === "string") {
        return { name: "Error", message: sanitizeBrowserText(error).slice(0, 1000), code: "", status: null, requestId: "" };
      }
      var payload = isObject(error.payload) ? error.payload : {};
      return {
        name: trimString(error.name, "Error"),
        message: sanitizeBrowserText(trimString(error.message, String(error))).slice(0, 1000),
        code: trimString(error.code || payload.code, ""),
        status: error.status || error.statusCode || payload.status_code || payload.statusCode || null,
        requestId: trimString(payload.request_id || payload.requestId || error.requestId, "")
      };
    } catch (innerError) {
      return { name: "Error", message: "Unknown error", code: "", status: null, requestId: "" };
    }
  }

  function query(selector, root) {
    try {
      var base = root || getDocument();

      if (!base || !base.querySelector) {
        return null;
      }

      return base.querySelector(selector);
    } catch (error) {
      return null;
    }
  }

  function queryAll(selector, root) {
    try {
      var base = root || getDocument();

      if (!base || !base.querySelectorAll) {
        return [];
      }

      return Array.prototype.slice.call(base.querySelectorAll(selector));
    } catch (error) {
      return [];
    }
  }

  function queryById(id) {
    try {
      var doc = getDocument();

      if (!doc || !doc.getElementById) {
        return null;
      }

      return doc.getElementById(id);
    } catch (error) {
      return null;
    }
  }

  function addListener(target, type, handler, options) {
    try {
      if (!target || !target.addEventListener || typeof handler !== "function") {
        return false;
      }

      target.addEventListener(type, handler, options || false);
      state.listeners.push({ target: target, type: type, handler: handler, options: options || false });
      return true;
    } catch (error) {
      return false;
    }
  }

  function removeAllListeners() {
    try {
      (state.listeners || []).forEach(function removeListener(item) {
        try {
          if (item && item.target && item.target.removeEventListener) {
            item.target.removeEventListener(item.type, item.handler, item.options || false);
          }
        } catch (error) {}
      });
      state.listeners = [];
    } catch (error) {
      state.listeners = [];
    }
  }

  function normalizeVisibility(value, fallback) {
    try {
      var text = trimString(value, fallback || "private").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");

      if (text === "public" || text === "öffentlich" || text === "oeffentlich" || text === "open" || text === "listed") {
        return "public";
      }

      if (
        text === "unlisted" ||
        text === "not_listed" ||
        text === "notlisted" ||
        text === "nicht_gelistet" ||
        text === "hidden_link" ||
        text === "link" ||
        text === "link_shared" ||
        text === "share_link"
      ) {
        return "unlisted";
      }

      return "private";
    } catch (error) {
      return fallback || "private";
    }
  }

  function normalizeWorkspace(value) {
    try {
      var text = trimString(value, "").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");

      var aliases = {
        "": "",
        project: "project",
        projekt: "project",
        project_info: "project",
        projectinfo: "project",
        info: "project",
        overview: "project",
        details: "project",
        basis: "project",

        map: "map",
        maps: "map",
        karte: "map",
        openlayer: "map",
        openlayers: "map",
        gis: "map",

        "3d": "editor3d",
        editor: "editor3d",
        editor3d: "editor3d",
        editor_3d: "editor3d",
        viewer: "editor3d",
        viewer3d: "editor3d",
        viewer_3d: "editor3d",
        world: "editor3d",

        "2d": "cad2d",
        cad: "cad2d",
        cad2d: "cad2d",
        cad_2d: "cad2d",
        plan: "cad2d",
        plan2d: "cad2d",

        lv: "lv",
        boq: "lv",
        leistungsverzeichnis: "lv",
        bill_of_quantities: "lv",

        versions: "versions",
        version: "versions",
        versionen: "versions",
        history: "versions",
        snapshots: "versions",

        admin: "admin",
        management: "admin",
        team: "team",
        members: "team",
        settings: "settings",
        einstellungen: "settings",
        permissions: "permissions",
        rechte: "permissions",
        system: "system",
        systemrefs: "system",
        system_references: "system_refs"
      };

      return aliases[text] || "";
    } catch (error) {
      return "";
    }
  }

  function isAllowedWorkspace(workspace) {
    try {
      var normalized = normalizeWorkspace(workspace);
      return WORKSPACES.indexOf(normalized) !== -1 && !FORBIDDEN_WORKSPACES[normalized];
    } catch (error) {
      return false;
    }
  }

  function emptyWorkspaceMap(value) {
    var result = {};
    try {
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        result[workspace] = !!value;
      });
    } catch (error) {}
    return result;
  }

  function normalizeWorkspaces(value, fallback) {
    var result = emptyWorkspaceMap(false);

    try {
      if (isObject(fallback)) {
        Object.keys(fallback).forEach(function copyFallback(key) {
          var workspace = normalizeWorkspace(key);
          if (isAllowedWorkspace(workspace)) {
            result[workspace] = toBooleanSafe(fallback[key], false);
          }
        });
      }

      if (value === null || value === undefined || value === "") {
        return result;
      }

      if (isObject(value)) {
        Object.keys(value).forEach(function eachKey(key) {
          var workspace = normalizeWorkspace(key);
          if (isAllowedWorkspace(workspace)) {
            result[workspace] = toBooleanSafe(value[key], false);
          }
        });
        return result;
      }

      if (isArray(value)) {
        result = emptyWorkspaceMap(false);
        value.forEach(function eachItem(item) {
          var workspace = normalizeWorkspace(item);
          if (isAllowedWorkspace(workspace)) {
            result[workspace] = true;
          }
        });
        return result;
      }

      if (typeof value === "string") {
        result = emptyWorkspaceMap(false);
        value.replace(/;/g, ",").replace(/\|/g, ",").split(",").forEach(function eachPart(part) {
          var workspace = normalizeWorkspace(part);
          if (isAllowedWorkspace(workspace)) {
            result[workspace] = true;
          }
        });
        return result;
      }

      return result;
    } catch (error) {
      return result;
    }
  }

  function sanitizeWorkspaceMap(value, fallback) {
    var normalized = normalizeWorkspaces(value, fallback);
    var result = emptyWorkspaceMap(false);
    try {
      var availability = workspaceAvailability();
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        result[workspace] = !!normalized[workspace] && !!availability[workspace];
      });
    } catch (error) {}
    return result;
  }

  function anyWorkspaceEnabled(value) {
    try {
      var normalized = normalizeWorkspaces(value);
      return WORKSPACES.some(function someWorkspace(workspace) {
        return !!normalized[workspace];
      });
    } catch (error) {
      return false;
    }
  }

  function workspaceAvailability() {
    try {
      var source = isObject(state.workspaceAvailability) ? state.workspaceAvailability : {};
      var result = emptyWorkspaceMap(true);
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        result[workspace] = source[workspace] !== false;
      });
      return result;
    } catch (error) {
      return {
        project: true,
        map: true,
        editor3d: false,
        cad2d: true,
        lv: true,
        versions: true
      };
    }
  }

  function workspaceUnavailableReason(workspace) {
    try {
      var normalized = normalizeWorkspace(workspace);
      var reasons = isObject(state.workspaceReasons) ? state.workspaceReasons : {};
      return trimString(reasons[normalized], normalized === "editor3d" ? "chunk_not_ready" : "workspace_unavailable");
    } catch (error) {
      return "workspace_unavailable";
    }
  }

  function applyWorkspaceAvailability(value) {
    try {
      var normalized = normalizeWorkspaces(value);
      var availability = workspaceAvailability();
      var result = emptyWorkspaceMap(false);
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        result[workspace] = !!normalized[workspace] && !!availability[workspace];
      });
      return result;
    } catch (error) {
      return emptyWorkspaceMap(false);
    }
  }

  function effectiveWorkspaces(visibility, published) {
    try {
      var normalizedVisibility = normalizeVisibility(visibility, "private");
      var desired = applyWorkspaceAvailability(published);
      if (normalizedVisibility === "private") {
        return emptyWorkspaceMap(false);
      }
      return desired;
    } catch (error) {
      return emptyWorkspaceMap(false);
    }
  }

  function workspaceLabel(workspace) {
    try {
      var normalized = normalizeWorkspace(workspace);
      return WORKSPACE_LABELS[normalized] || trimString(workspace, "Workspace");
    } catch (error) {
      return "Workspace";
    }
  }

  function getNestedObject(source, keys) {
    try {
      var current = source;
      for (var i = 0; i < keys.length; i += 1) {
        if (!isObject(current)) {
          return {};
        }
        current = current[keys[i]];
      }
      return isObject(current) ? current : {};
    } catch (error) {
      return {};
    }
  }

  function getConfig() {
    try {
      var win = getWindow();
      var config = win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG || win.PROJECT_WORKSPACE_CONFIG || {};
      if (!isObject(config)) {
        config = {};
      }

      var paths = isObject(config.paths) ? config.paths : {};
      var project = isObject(config.project) ? config.project : {};
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};
      var access = isObject(config.access) ? config.access : (isObject(project.access) ? project.access : {});
      var publication = isObject(config.publication) ? config.publication : (isObject(project.publication) ? project.publication : {});
      var uiFlags = isObject(config.uiFlags) ? config.uiFlags : (isObject(project.ui_flags) ? project.ui_flags : {});
      var workspaceRuntime = isObject(config.workspaceRuntime) ? config.workspaceRuntime :
        isObject(config.workspace_runtime) ? config.workspace_runtime :
          isObject(project.workspace_runtime) ? project.workspace_runtime :
            isObject(project.workspaceRuntime) ? project.workspaceRuntime : {};
      var chunk = isObject(config.chunk) ? config.chunk : isObject(project.chunk) ? project.chunk : {};
      var accessSync = isObject(project.chunk_access_sync) ? project.chunk_access_sync :
        isObject(project.chunkAccessSync) ? project.chunkAccessSync :
          isObject(chunk.access_sync) ? chunk.access_sync : {};

      var publicId = firstString([
        config.projectPublicId, config.project_public_id,
        project.public_id, project.publicId, project.project_public_id, project.projectPublicId
      ], "");
      var isNew = toBooleanSafe(firstDefined([config.isNew, project.is_new, project.isNew], !publicId || publicId === "new"), !publicId || publicId === "new");

      var projectRole = normalizeProjectRole(firstString([
        config.projectRole, config.project_role,
        access.role, access.project_role, access.projectRole,
        access.membership_role, access.membershipRole,
        project.project_role, project.projectRole
      ], isNew ? "owner" : "viewer"), isNew ? "owner" : "viewer");

      var accessMode = lowerString(firstString([
        config.accessMode, config.access_mode,
        uiFlags.access_mode, uiFlags.accessMode,
        access.access_mode, access.accessMode,
        project.access_mode, project.accessMode
      ], ""), "");

      var identityConsistent = toBooleanSafe(firstDefined([
        config.identityConsistent, config.identity_consistent,
        uiFlags.identity_consistent, uiFlags.identityConsistent,
        currentUser.identity_consistent, currentUser.identityConsistent
      ], true), true);
      var localLinkState = lowerString(firstString([
        config.localLinkState, config.local_link_state,
        uiFlags.local_link_state, uiFlags.localLinkState,
        currentUser.local_link_state, currentUser.localLinkState
      ], "not_applicable"), "not_applicable");
      var identityMismatch = !identityConsistent || localLinkState === "identity_mismatch" || localLinkState === "inactive" || accessMode === "identity_mismatch";

      var authUnavailable = toBooleanSafe(firstDefined([
        config.authUnavailable, uiFlags.auth_unavailable, currentUser.auth_unavailable, currentUser.authUnavailable
      ], false), false);
      var userBlocked = toBooleanSafe(firstDefined([
        config.userBlocked, uiFlags.user_blocked, currentUser.user_blocked, currentUser.userBlocked
      ], false), false);
      var accessBlocked = toBooleanSafe(firstDefined([
        config.accessBlocked, uiFlags.access_blocked, currentUser.access_blocked, currentUser.accessBlocked
      ], false), false);
      var publicViewer = toBooleanSafe(firstDefined([
        config.publicViewer, config.isPublicViewer, uiFlags.public_viewer, access.public_viewer, access.publicViewer
      ], accessMode === "public"), accessMode === "public") || projectRole === "viewer" && accessMode === "public";
      var demoMode = toBooleanSafe(firstDefined([
        config.demoMode, config.demo_mode, uiFlags.demo_mode, currentUser.demo_mode, currentUser.demoMode, currentUser.is_demo
      ], false), false);
      if (publicViewer || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        demoMode = false;
      }

      var readOnly = toBooleanSafe(firstDefined([
        config.readOnly, config.readonly, uiFlags.read_only, access.read_only, access.readOnly
      ], publicViewer || projectRole === "viewer" || authUnavailable || userBlocked || accessBlocked || identityMismatch), true);
      if (publicViewer || projectRole === "viewer" || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        readOnly = true;
      }

      var authenticated = toBooleanSafe(firstDefined([
        config.authenticated, currentUser.authenticated, currentUser.is_authenticated, currentUser.isAuthenticated
      ], false), false);
      var persistent = toBooleanSafe(firstDefined([
        config.persistent, uiFlags.persistent, currentUser.persistent
      ], authenticated && !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked && !identityMismatch), false);
      if (demoMode || publicViewer || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        persistent = false;
      }

      var roleCanPublish = !!ROLE_CAN_PUBLISH[projectRole];
      var canManage = toBooleanSafe(firstDefined([
        config.canManage, uiFlags.can_manage, access.can_manage, access.canManage
      ], false), false) && roleCanPublish;
      var canPublish = toBooleanSafe(firstDefined([
        config.canPublish, config.canManagePublication, uiFlags.can_publish, access.can_publish, access.canPublish
      ], canManage), false) && roleCanPublish;
      var canEdit = toBooleanSafe(firstDefined([config.canEdit, access.can_edit, access.canEdit], false), false) && projectRole !== "viewer";
      var canMutate = toBooleanSafe(firstDefined([config.canMutate, access.can_mutate, access.canMutate], canPublish), false) && roleCanPublish;

      if (readOnly || publicViewer || demoMode || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        canManage = false;
        canPublish = false;
        canMutate = false;
      }

      var provisioningStatus = normalizeOperationalStatus(firstString([
        workspaceRuntime.chunk_provisioning_status, workspaceRuntime.chunkProvisioningStatus,
        chunk.provisioning_status, chunk.provisioningStatus, chunk.status,
        project.chunk_provisioning_status, project.chunkProvisioningStatus
      ], "pending"), "pending");
      var chunkReady = toBooleanSafe(firstDefined([
        workspaceRuntime.chunk_ready, workspaceRuntime.chunkReady,
        workspaceRuntime.ready, chunk.ready, chunk.chunk_ready, chunk.chunkReady,
        project.chunk_ready, project.chunkReady
      ], READY_OPERATIONAL_STATUSES[provisioningStatus] === true), READY_OPERATIONAL_STATUSES[provisioningStatus] === true);
      if (!READY_OPERATIONAL_STATUSES[provisioningStatus]) {
        chunkReady = false;
      }

      var accessSyncStatus = normalizeOperationalStatus(firstString([
        workspaceRuntime.chunk_access_sync_status, workspaceRuntime.chunkAccessSyncStatus,
        accessSync.status, project.chunk_access_sync_status, project.chunkAccessSyncStatus
      ], "disabled"), "disabled");
      var accessRequired = toBooleanSafe(firstDefined([
        workspaceRuntime.chunk_access_sync_required, workspaceRuntime.chunkAccessSyncRequired,
        chunk.access_sync_required, chunk.accessSyncRequired,
        project.chunk_access_sync_required, project.chunkAccessSyncRequired
      ], false), false);
      var accessReady = !accessRequired || accessSyncStatus === "ready";

      var publicEditor3dVerified = toBooleanSafe(firstDefined([
        workspaceRuntime.public_editor3d_verified, workspaceRuntime.publicEditor3dVerified,
        workspaceRuntime.public_embed_verified, workspaceRuntime.publicEmbedVerified,
        publication.public_editor3d_verified, publication.publicEditor3dVerified,
        publication.editor3d_publication_allowed, publication.editor3dPublicationAllowed,
        uiFlags.public_editor3d_verified, uiFlags.publicEditor3dVerified
      ], false), false);

      var editor3dAvailable = !!(chunkReady && accessReady && publicEditor3dVerified);
      var editor3dReason = !chunkReady
        ? (provisioningStatus === "repair_required" || provisioningStatus === "failed" ? "chunk_repair_required" : "chunk_not_ready")
        : !accessReady
          ? "chunk_access_not_ready"
          : !publicEditor3dVerified
            ? "public_editor3d_not_verified"
            : "ready";

      var endpoint = normalizePublicationEndpoint(
        firstString([paths.publication, paths.projectPublication, paths.project_publication], ""),
        publicId
      );

      return {
        project: project,
        currentUser: currentUser,
        access: access,
        publication: publication,
        uiFlags: uiFlags,
        workspaceRuntime: workspaceRuntime,
        projectPublicId: publicId,
        projectVisibility: normalizeVisibility(config.projectVisibility || config.project_visibility || project.visibility || publication.visibility, "private"),
        isNew: isNew,
        projectRole: projectRole,
        project_role: projectRole,
        canManage: canManage,
        canPublish: canPublish,
        canEdit: canEdit,
        canMutate: canMutate,
        demoMode: demoMode,
        persistent: persistent,
        publicViewer: publicViewer,
        readOnly: readOnly,
        authenticated: authenticated,
        authUnavailable: authUnavailable,
        userBlocked: userBlocked,
        accessBlocked: accessBlocked,
        identityConsistent: identityConsistent,
        localLinkState: localLinkState,
        accessMode: identityMismatch ? "identity_mismatch" : accessMode || (authenticated ? "authenticated" : "anonymous"),
        chunkReady: chunkReady,
        chunkProvisioningStatus: provisioningStatus,
        chunkAccessSyncStatus: accessSyncStatus,
        chunkAccessRequired: accessRequired,
        publicEditor3dVerified: publicEditor3dVerified,
        workspaceAvailability: {
          project: true,
          map: true,
          editor3d: editor3dAvailable,
          cad2d: true,
          lv: true,
          versions: true
        },
        workspaceReasons: {
          editor3d: editor3dReason
        },
        endpoint: endpoint,
        paths: paths,
        parentEvents: {
          publicationChanged: trimString(config.parentEvents && config.parentEvents.publicationChanged, EVENT_PUBLICATION_CHANGED),
          error: trimString(config.parentEvents && config.parentEvents.error, EVENT_PROJECT_ERROR)
        }
      };
    } catch (error) {
      return {
        project: {}, currentUser: {}, access: {}, publication: {}, uiFlags: {}, workspaceRuntime: {},
        projectPublicId: "", projectVisibility: "private", isNew: true,
        projectRole: "viewer", project_role: "viewer",
        canManage: false, canPublish: false, canEdit: false, canMutate: false,
        demoMode: false, persistent: false, publicViewer: false, readOnly: true, authenticated: false,
        authUnavailable: false, userBlocked: false, accessBlocked: true,
        identityConsistent: false, localLinkState: "unavailable", accessMode: "blocked",
        chunkReady: false, chunkProvisioningStatus: "pending", chunkAccessSyncStatus: "disabled",
        chunkAccessRequired: false, publicEditor3dVerified: false,
        workspaceAvailability: { project: true, map: true, editor3d: false, cad2d: true, lv: true, versions: true },
        workspaceReasons: { editor3d: "context_error" },
        endpoint: "", paths: {},
        parentEvents: { publicationChanged: EVENT_PUBLICATION_CHANGED, error: EVENT_PROJECT_ERROR }
      };
    }
  }

  function queryRefs() {
    var card = query(CARD_SELECTOR);
    var root = query(ROOT_SELECTOR);

    return {
      document: getDocument(),
      root: root,
      form: query(FORM_SELECTOR),
      card: card,
      alert: query(ALERT_SELECTOR),

      visibilityInput: queryById("projectVisibility") || query("[data-project-visibility-input]") || query("input[name='visibility']"),
      visibilityRadios: queryAll("input[name='visibility']"),
      visibilityOptions: queryAll("[data-project-visibility-option], [data-project-visibility-card], [data-visibility]"),

      save: query("[data-project-publication-save]", card),
      reset: query("[data-project-publication-reset]", card),
      status: query("[data-project-publication-status]", card),
      summary: query("[data-project-publication-summary]", card),

      checkboxes: queryAll("[data-project-publication-checkbox]", card),
      optionCards: queryAll("[data-project-publication-option]", card),

      requireAuth: query("[data-project-publication-require-auth]", card),
      requirePermission: query("[data-project-publication-require-permission]", card),

      initialJson: query("[data-project-publication-initial-json]", card)
    };
  }

  function setHidden(element, hidden) {
    try {
      if (!element) {
        return;
      }

      if (hidden) {
        element.setAttribute("hidden", "");
      } else {
        element.removeAttribute("hidden");
      }
    } catch (error) {}
  }

  function getValue(element) {
    try {
      if (!element) {
        return "";
      }

      return trimString(element.value, "");
    } catch (error) {
      return "";
    }
  }

  function setChecked(element, checked) {
    try {
      if (element) {
        element.checked = !!checked;
      }
    } catch (error) {}
  }

  function getChecked(element) {
    try {
      return !!(element && element.checked);
    } catch (error) {
      return false;
    }
  }

  function setAlert(kind, message) {
    try {
      var formApi = getWindow().VectoplanProjectForm || null;

      if (formApi && typeof formApi.setAlert === "function") {
        formApi.setAlert(kind, message);
        return;
      }
    } catch (error) {}

    try {
      var alert = state.refs && state.refs.alert;
      if (!alert) {
        return;
      }

      var text = trimString(message, "");

      alert.classList.remove("is-success", "is-warning", "is-error", "is-info");
      alert.removeAttribute("data-kind");

      if (!text) {
        alert.textContent = "";
        setHidden(alert, true);
        return;
      }

      var normalizedKind = trimString(kind, "info").toLowerCase();

      if (normalizedKind === "success") {
        alert.classList.add("is-success");
        alert.setAttribute("data-kind", "success");
      } else if (normalizedKind === "warning") {
        alert.classList.add("is-warning");
        alert.setAttribute("data-kind", "warning");
      } else if (normalizedKind === "error" || normalizedKind === "danger") {
        alert.classList.add("is-error");
        alert.setAttribute("data-kind", "error");
      } else {
        alert.classList.add("is-info");
        alert.setAttribute("data-kind", "info");
      }

      alert.textContent = text;
      setHidden(alert, false);
    } catch (error) {}
  }

  function setSaving(isSaving) {
    try {
      state.isSaving = !!isSaving;

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_SAVING, state.isSaving);
        state.refs.card.setAttribute("data-project-publication-saving", state.isSaving ? "true" : "false");
      }

      if (state.refs.save) {
        state.refs.save.disabled = state.isSaving || !canWritePublication();
        state.refs.save.setAttribute("aria-busy", state.isSaving ? "true" : "false");

        if (state.isSaving) {
          if (!state.refs.save.getAttribute("data-original-text")) {
            state.refs.save.setAttribute("data-original-text", state.refs.save.textContent || "");
          }
          state.refs.save.textContent = "Veröffentlichung wird gespeichert…";
        } else {
          var original = state.refs.save.getAttribute("data-original-text");
          state.refs.save.textContent = original || "Veröffentlichung speichern";
        }
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = state.isSaving || !canWritePublication();
      }
    } catch (error) {}
  }

  function setDirty(isDirty) {
    try {
      state.isDirty = !!isDirty;

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_DIRTY, state.isDirty);
        state.refs.card.setAttribute("data-project-publication-dirty", state.isDirty ? "true" : "false");
      }
    } catch (error) {}
  }

  function getVisibilityFromRadios() {
    try {
      var radios = state.refs.visibilityRadios || [];
      for (var i = 0; i < radios.length; i += 1) {
        if (radios[i] && radios[i].checked) {
          return normalizeVisibility(radios[i].value, "");
        }
      }
      return "";
    } catch (error) {
      return "";
    }
  }

  function getVisibilityFromOptions() {
    try {
      var options = state.refs.visibilityOptions || [];
      for (var i = 0; i < options.length; i += 1) {
        var option = options[i];
        if (!option) {
          continue;
        }

        var selected =
          option.classList && (option.classList.contains("is-selected") || option.classList.contains("active"));

        if (!selected && option.getAttribute) {
          selected = toBooleanSafe(option.getAttribute("aria-checked"), false) ||
            toBooleanSafe(option.getAttribute("data-selected"), false) ||
            toBooleanSafe(option.getAttribute("data-active"), false);
        }

        if (selected) {
          return normalizeVisibility(option.getAttribute("data-visibility") || option.getAttribute("data-value") || option.value, "");
        }
      }

      return "";
    } catch (error) {
      return "";
    }
  }

  function currentVisibility() {
    try {
      var fromInput = normalizeVisibility(getValue(state.refs.visibilityInput), "");
      var fromRadios = getVisibilityFromRadios();
      var fromOptions = getVisibilityFromOptions();
      var fromRoot = state.refs.root ? normalizeVisibility(state.refs.root.getAttribute("data-project-visibility"), "") : "";
      var fromCard = state.refs.card ? normalizeVisibility(state.refs.card.getAttribute("data-project-visibility"), "") : "";
      var fromConfig = state.config ? normalizeVisibility(state.config.projectVisibility, "private") : "private";

      return normalizeVisibility(fromInput || fromRadios || fromOptions || fromRoot || fromCard || fromConfig, "private");
    } catch (error) {
      return "private";
    }
  }

  function parseInitialData() {
    try {
      var fromJson = {};
      if (state.refs.initialJson) {
        fromJson = safeJsonParse(state.refs.initialJson.textContent || "", {});
      }

      if (!isObject(fromJson)) {
        fromJson = {};
      }

      var project = state.config && isObject(state.config.project) ? state.config.project : {};
      var configPublication = state.config && isObject(state.config.publication) ? state.config.publication : {};
      var projectPublication = isObject(project.publication) ? project.publication : {};
      var publicationWrapper = isObject(configPublication.publication) ? configPublication.publication : {};

      var source = Object.keys(fromJson).length ? fromJson :
        Object.keys(publicationWrapper).length ? publicationWrapper :
          Object.keys(configPublication).length ? configPublication : projectPublication;

      var visibility = normalizeVisibility(source.visibility || project.visibility || state.config.projectVisibility, "private");

      var published = sanitizeWorkspaceMap(
        source.published_workspaces ||
          source.publishedWorkspaces ||
          source.workspaces ||
          source.tabs ||
          configPublication.published_workspaces ||
          configPublication.publishedWorkspaces ||
          projectPublication.published_workspaces ||
          projectPublication.publishedWorkspaces ||
          {},
        DEFAULT_PUBLISHED_WORKSPACES
      );

      var effective = sanitizeWorkspaceMap(
        source.effective_published_workspaces ||
          source.effectivePublishedWorkspaces ||
          configPublication.effective_published_workspaces ||
          configPublication.effectivePublishedWorkspaces ||
          projectPublication.effective_published_workspaces ||
          projectPublication.effectivePublishedWorkspaces ||
          effectiveWorkspaces(visibility, published),
        effectiveWorkspaces(visibility, published)
      );

      return {
        visibility: visibility,
        published_workspaces: published,
        publishedWorkspaces: safeClone(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: toBooleanSafe(source.require_auth !== undefined ? source.require_auth : source.requireAuth, visibility === "private"),
        requireAuth: toBooleanSafe(source.require_auth !== undefined ? source.require_auth : source.requireAuth, visibility === "private"),
        require_project_permission: toBooleanSafe(
          source.require_project_permission !== undefined ? source.require_project_permission : source.requireProjectPermission,
          visibility === "private"
        ),
        requireProjectPermission: toBooleanSafe(
          source.require_project_permission !== undefined ? source.require_project_permission : source.requireProjectPermission,
          visibility === "private"
        )
      };
    } catch (error) {
      return {
        visibility: "private",
        published_workspaces: emptyWorkspaceMap(false),
        publishedWorkspaces: emptyWorkspaceMap(false),
        effective_published_workspaces: emptyWorkspaceMap(false),
        effectivePublishedWorkspaces: emptyWorkspaceMap(false),
        require_auth: true,
        requireAuth: true,
        require_project_permission: true,
        requireProjectPermission: true
      };
    }
  }

  function extractPublicationPayload(response) {
    try {
      var data = isObject(response) ? response : {};
      var publication = isObject(data.publication) ? data.publication : data;

      if (isObject(publication.publication)) {
        publication = publication.publication;
      }

      var visibility = normalizeVisibility(publication.visibility || data.visibility || currentVisibility(), "private");

      var fallbackPublished = collectData().published_workspaces;
      var published = sanitizeWorkspaceMap(
        publication.published_workspaces ||
          publication.publishedWorkspaces ||
          publication.workspaces ||
          data.published_workspaces ||
          data.publishedWorkspaces ||
          fallbackPublished,
        fallbackPublished
      );

      var effective = sanitizeWorkspaceMap(
        publication.effective_published_workspaces ||
          publication.effectivePublishedWorkspaces ||
          data.effective_published_workspaces ||
          data.effectivePublishedWorkspaces ||
          effectiveWorkspaces(visibility, published),
        effectiveWorkspaces(visibility, published)
      );

      return {
        visibility: visibility,
        published_workspaces: published,
        publishedWorkspaces: safeClone(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: toBooleanSafe(publication.require_auth !== undefined ? publication.require_auth : (publication.requireAuth !== undefined ? publication.requireAuth : data.require_auth), visibility === "private"),
        requireAuth: toBooleanSafe(publication.require_auth !== undefined ? publication.require_auth : (publication.requireAuth !== undefined ? publication.requireAuth : data.require_auth), visibility === "private"),
        require_project_permission: toBooleanSafe(
          publication.require_project_permission !== undefined ? publication.require_project_permission :
            publication.requireProjectPermission !== undefined ? publication.requireProjectPermission :
              data.require_project_permission,
          visibility === "private"
        ),
        requireProjectPermission: toBooleanSafe(
          publication.require_project_permission !== undefined ? publication.require_project_permission :
            publication.requireProjectPermission !== undefined ? publication.requireProjectPermission :
              data.require_project_permission,
          visibility === "private"
        )
      };
    } catch (error) {
      return collectData();
    }
  }

  function setStatusClasses(status, visibility, hasEffective) {
    try {
      if (!status) {
        return;
      }

      status.classList.remove(
        "vp-project-chip--muted",
        "vp-project-chip--success",
        "vp-project-chip--warning",
        "vp-project-chip--error"
      );

      if (state.authUnavailable || state.userBlocked || state.accessBlocked) {
        status.classList.add("vp-project-chip--error");
      } else if (visibility === "private") {
        status.classList.add("vp-project-chip--muted");
      } else if (hasEffective) {
        status.classList.add("vp-project-chip--success");
      } else {
        status.classList.add("vp-project-chip--warning");
      }
    } catch (error) {}
  }

  function updateStatus(data) {
    try {
      var status = state.refs.status;
      if (!status) return;
      var publication = isObject(data) ? data : collectData();
      var visibility = normalizeVisibility(publication.visibility, "private");
      var effective = normalizeWorkspaces(publication.effective_published_workspaces || publication.effectivePublishedWorkspaces);
      var hasEffective = anyWorkspaceEnabled(effective);
      setStatusClasses(status, visibility, hasEffective);

      if (state.authUnavailable) status.textContent = "Auth nicht erreichbar";
      else if (!state.identityConsistent || state.localLinkState === "identity_mismatch") status.textContent = "Identität inkonsistent";
      else if (state.userBlocked || state.accessBlocked) status.textContent = "Zugriff gesperrt";
      else if (visibility === "private") status.textContent = "Privat";
      else if (hasEffective) status.textContent = "Reiter veröffentlicht";
      else status.textContent = "Keine Reiter veröffentlicht";
    } catch (error) {}
  }

  function updateSummary(data) {
    try {
      var summary = state.refs.summary;
      if (!summary) return;
      var publication = isObject(data) ? data : collectData();
      var visibility = normalizeVisibility(publication.visibility, "private");
      var effective = normalizeWorkspaces(publication.effective_published_workspaces || publication.effectivePublishedWorkspaces);
      var enabledLabels = [];
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        if (effective[workspace]) enabledLabels.push(workspaceLabel(workspace));
      });

      var suffix = "";
      if (!workspaceAvailability().editor3d) {
        var reason = workspaceUnavailableReason("editor3d");
        if (reason === "chunk_not_ready") suffix = " 3D bleibt bis zur vollständigen Chunk-Provisionierung deaktiviert.";
        else if (reason === "chunk_access_not_ready") suffix = " 3D bleibt bis zur Access-Synchronisation deaktiviert.";
        else if (reason === "public_editor3d_not_verified") suffix = " 3D benötigt einen verifizierten öffentlichen Read-only-Vertrag.";
        else if (reason === "chunk_repair_required") suffix = " 3D ist wegen eines reparaturbedürftigen Chunk-Zustands deaktiviert.";
      }

      if (visibility === "private") summary.textContent = "Private Projekte veröffentlichen keine Workspaces." + suffix;
      else if (enabledLabels.length) summary.textContent = "Öffentlich sichtbar: " + enabledLabels.join(", ") + "." + suffix;
      else summary.textContent = "Projekt ist " + visibility + ", aber kein Workspace ist veröffentlicht." + suffix;
    } catch (error) {}
  }

  function applyData(data, options) {
    try {
      var opts = isObject(options) ? options : {};
      var publication = sanitizePublicationForBrowser(isObject(data) ? data : {});
      var visibility = publication.visibility;
      var published = sanitizeWorkspaceMap(publication.published_workspaces, emptyWorkspaceMap(false));
      var effective = effectiveWorkspaces(visibility, published);
      var availability = workspaceAvailability();

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
      }

      (state.refs.checkboxes || []).forEach(function applyCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");
          var allowed = isAllowedWorkspace(workspace) && availability[workspace] !== false;
          input.checked = allowed ? !!published[workspace] : false;
          input.disabled = !allowed || !canWritePublication();
          input.setAttribute("data-effective-published", effective[workspace] ? "true" : "false");
          input.setAttribute("data-workspace-available", allowed ? "true" : "false");
          if (!allowed) {
            input.setAttribute("title", workspaceUnavailableReason(workspace));
          }
        } catch (error) {}
      });

      (state.refs.optionCards || []).forEach(function applyCard(card) {
        try {
          var workspace = normalizeWorkspace(card.getAttribute("data-workspace") || card.getAttribute("data-value") || "");
          var allowed = isAllowedWorkspace(workspace) && availability[workspace] !== false;
          card.classList.toggle(CLASS_SELECTED, allowed && !!published[workspace]);
          card.classList.toggle(CLASS_EFFECTIVE, allowed && !!effective[workspace]);
          card.classList.toggle(CLASS_DISABLED, !allowed || !canWritePublication());
          card.setAttribute("aria-disabled", !allowed || !canWritePublication() ? "true" : "false");
          card.setAttribute("data-published", allowed && published[workspace] ? "true" : "false");
          card.setAttribute("data-effective-published", allowed && effective[workspace] ? "true" : "false");
          card.setAttribute("data-workspace-available", allowed ? "true" : "false");
          if (!allowed) {
            card.setAttribute("title", workspaceUnavailableReason(workspace));
          } else {
            card.removeAttribute("title");
          }
        } catch (error) {}
      });

      setChecked(state.refs.requireAuth, publication.require_auth);
      setChecked(state.refs.requirePermission, publication.require_project_permission);

      updateDisabledState();
      updateStatus({ visibility: visibility, published_workspaces: published, effective_published_workspaces: effective });
      updateSummary({ visibility: visibility, published_workspaces: published, effective_published_workspaces: effective });

      state.currentData = sanitizePublicationForBrowser({
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective,
        require_auth: getChecked(state.refs.requireAuth),
        require_project_permission: getChecked(state.refs.requirePermission)
      });

      if (!opts.keepDirty) {
        setDirty(false);
      }
      return state.currentData;
    } catch (error) {
      return sanitizePublicationForBrowser(data || {});
    }
  }

  function collectData() {
    var published = emptyWorkspaceMap(false);
    try {
      var availability = workspaceAvailability();
      (state.refs.checkboxes || []).forEach(function collectCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");
          if (isAllowedWorkspace(workspace) && availability[workspace] !== false) {
            published[workspace] = !!input.checked;
          }
        } catch (error) {}
      });

      var visibility = currentVisibility();
      var requireAuth = getChecked(state.refs.requireAuth);
      var requirePermission = getChecked(state.refs.requirePermission);
      if (visibility === "private") {
        requireAuth = true;
        requirePermission = true;
      }
      var effective = effectiveWorkspaces(visibility, published);
      return sanitizePublicationForBrowser({
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective,
        require_auth: requireAuth,
        require_project_permission: requirePermission
      });
    } catch (error) {
      return sanitizePublicationForBrowser({ visibility: "private" });
    }
  }

  function buildPayloadForSave() {
    try {
      var data = collectData();
      return {
        visibility: normalizeVisibility(data.visibility, "private"),
        published_workspaces: sanitizeWorkspaceMap(data.published_workspaces),
        require_auth: !!data.require_auth,
        require_project_permission: !!data.require_project_permission
      };
    } catch (error) {
      return {
        visibility: "private",
        published_workspaces: emptyWorkspaceMap(false),
        require_auth: true,
        require_project_permission: true
      };
    }
  }

  function buildEndpoint() {
    try {
      var publicId = trimString(state.projectPublicId, "") ||
        (state.config && trimString(state.config.projectPublicId, "")) ||
        (state.refs.card && trimString(state.refs.card.getAttribute("data-project-public-id"), ""));
      return normalizePublicationEndpoint(state.endpoint || (state.config && state.config.endpoint), publicId);
    } catch (error) {
      return "";
    }
  }

  async function requestJson(url, options) {
    var controller = null;
    var timer = null;
    try {
      var target = normalizePublicationEndpoint(url, state.projectPublicId);
      if (!target) {
        var endpointError = new Error("Unsicherer oder fehlender Publication-Endpunkt.");
        endpointError.code = "unsafe_publication_endpoint";
        throw endpointError;
      }

      var opts = isObject(options) ? options : {};
      var method = trimString(opts.method, "GET").toUpperCase();
      if (["GET", "PATCH", "PUT"].indexOf(method) === -1) {
        var methodError = new Error("Nicht unterstützte Request-Methode.");
        methodError.code = "unsupported_request_method";
        throw methodError;
      }

      var requestId = createRequestId();
      state.requestId = requestId;
      if (typeof AbortController === "function") {
        controller = new AbortController();
        timer = setTimeout(function abortRequest() {
          try { controller.abort(); } catch (_) {}
        }, DEFAULT_REQUEST_TIMEOUT_MS);
      }

      var response = await fetch(target, {
        method: method,
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-Requested-With": "fetch",
          "X-VECTOPLAN-Client": "project_publication.js",
          "X-Request-ID": requestId,
          "X-Correlation-ID": requestId
        },
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller ? controller.signal : undefined,
        body: opts.body !== undefined ? opts.body : undefined
      });

      var text = await response.text();
      if (text.length > MAX_RESPONSE_TEXT_LENGTH) {
        var sizeError = new Error("Publication-Antwort ist zu groß.");
        sizeError.code = "response_too_large";
        sizeError.status = response.status;
        throw sizeError;
      }
      var data = safeJsonParse(text, null);
      if (!response.ok) {
        var message = data && (data.error || data.message) ? data.error || data.message : "Request failed with status " + response.status;
        var error = new Error(trimString(message, "Publication-Request fehlgeschlagen.").slice(0, 1000));
        error.status = response.status;
        error.statusCode = response.status;
        error.payload = sanitizeBrowserValue(data || {}, 0);
        error.code = data && data.code ? data.code : "request_failed";
        error.requestId = trimString(data && (data.request_id || data.requestId), requestId);
        throw error;
      }
      return data === null ? {} : sanitizeBrowserValue(data, 0);
    } catch (error) {
      if (error && error.name === "AbortError") {
        var timeoutError = new Error("Publication-Request hat das Zeitlimit überschritten.");
        timeoutError.code = "request_timeout";
        timeoutError.requestId = state.requestId;
        throw timeoutError;
      }
      throw error;
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
    }
  }

  function canWritePublication() {
    try {
      var roleAllowed = !!ROLE_CAN_PUBLISH[state.projectRole];
      return !!(
        !state.isSaving &&
        !state.isLoading &&
        !state.isNew &&
        !state.demoMode &&
        !state.publicViewer &&
        !state.readOnly &&
        !state.authUnavailable &&
        !state.userBlocked &&
        !state.accessBlocked &&
        state.identityConsistent &&
        state.localLinkState !== "identity_mismatch" &&
        state.localLinkState !== "inactive" &&
        state.persistent &&
        roleAllowed &&
        (state.canPublish || state.canManage) &&
        !!buildEndpoint()
      );
    } catch (error) {
      return false;
    }
  }

  function disabledReason() {
    try {
      if (state.authUnavailable) return "Auth-Service nicht erreichbar. Veröffentlichung kann nicht gespeichert werden.";
      if (!state.identityConsistent || state.localLinkState === "identity_mismatch") return "Die lokale und kanonische Benutzeridentität stimmen nicht überein.";
      if (state.localLinkState === "inactive") return "Der lokale Benutzerlink ist inaktiv.";
      if (state.userBlocked || state.accessBlocked) return "Der Zugriff ist gesperrt. Veröffentlichung kann nicht gespeichert werden.";
      if (state.publicViewer || state.projectRole === "viewer") return "Öffentliche oder reine Viewer-Ansichten sind schreibgeschützt.";
      if (state.readOnly) return "Dieses Projekt ist schreibgeschützt.";
      if (state.isNew) return "Speichere das Projekt zuerst. Danach kannst du Reiter veröffentlichen.";
      if (state.demoMode) return "Im Demo-Modus werden Veröffentlichungseinstellungen nicht dauerhaft gespeichert.";
      if (!state.persistent) return "Für Veröffentlichungseinstellungen ist ein persistenter AppUser-Kontext erforderlich.";
      if (!ROLE_CAN_PUBLISH[state.projectRole]) return "Nur Projekt-Owner und Projekt-Admins dürfen Workspaces veröffentlichen.";
      if (!(state.canPublish || state.canManage)) return "Du hast keine Berechtigung, Veröffentlichungseinstellungen zu ändern.";
      if (!buildEndpoint()) return "Publication-Endpunkt fehlt oder ist unsicher.";
      return "";
    } catch (error) {
      return "Veröffentlichung kann nicht gespeichert werden.";
    }
  }

  function validateBeforeSave() {
    try {
      var reason = disabledReason();
      if (reason) {
        return {
          ok: false,
          message: reason
        };
      }

      return {
        ok: true,
        message: ""
      };
    } catch (error) {
      return {
        ok: false,
        message: "Veröffentlichung kann nicht gespeichert werden."
      };
    }
  }

  async function savePublication() {
    try {
      var validation = validateBeforeSave();

      if (!validation.ok) {
        setAlert("warning", validation.message);
        return false;
      }

      if (state.isSaving) {
        return false;
      }

      var payload = buildPayloadForSave();

      setSaving(true);
      setAlert("info", "Veröffentlichung wird gespeichert…");

      var response = await requestJson(buildEndpoint(), {
        method: "PATCH",
        body: safeJsonStringify(payload)
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Veröffentlichung konnte nicht gespeichert werden.");
      }

      var publication = extractPublicationPayload(response);

      state.lastResponse = sanitizeBrowserValue({ ok: response && response.ok !== false, code: response && response.code, status_code: response && (response.status_code || response.statusCode), request_id: response && (response.request_id || response.requestId) }, 0);
      state.lastError = null;
      state.initialData = safeClone(publication);
      applyData(publication, { keepDirty: false });

      if (state.refs.card) {
        state.refs.card.classList.remove(CLASS_ERROR);
      }

      setAlert("success", "Veröffentlichung wurde gespeichert.");
      emitChangeEvent(response, publication);

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;

      if (state.refs.card) {
        state.refs.card.classList.add(CLASS_ERROR);
      }

      setAlert("error", normalized.message || "Veröffentlichung konnte nicht gespeichert werden.");

      emitLocal(EVENT_PUBLICATION_ERROR, {
        error: normalized,
        publication: collectData(),
        endpoint: buildEndpoint()
      });

      emitLocal(EVENT_PROJECT_ERROR, {
        error: normalized,
        area: "publication",
        publication: collectData(),
        endpoint: buildEndpoint()
      });

      return false;
    } finally {
      setSaving(false);
    }
  }

  function resetPublication() {
    try {
      applyData(state.initialData || parseInitialData(), { keepDirty: false });
      setAlert("", "");
      return true;
    } catch (error) {
      return false;
    }
  }

  async function refreshPublication() {
    try {
      var endpoint = buildEndpoint();
      if (!endpoint) {
        return false;
      }

      state.isLoading = true;
      updateDisabledState();

      var response = await requestJson(endpoint, { method: "GET" });
      var publication = extractPublicationPayload(response);

      state.lastResponse = sanitizeBrowserValue({ ok: response && response.ok !== false, code: response && response.code, status_code: response && (response.status_code || response.statusCode), request_id: response && (response.request_id || response.requestId) }, 0);
      state.initialData = safeClone(publication);
      applyData(publication, { keepDirty: false });

      return true;
    } catch (error) {
      state.lastError = normalizeError(error);
      return false;
    } finally {
      state.isLoading = false;
      updateDisabledState();
    }
  }

  function emitLocal(type, detail) {
    try {
      var root = state.refs && state.refs.root;
      if (!root || typeof CustomEvent !== "function") return false;
      root.dispatchEvent(new CustomEvent(type, {
        bubbles: true,
        cancelable: false,
        detail: sanitizeBrowserValue(detail || {}, 0)
      }));
      return true;
    } catch (error) {
      return false;
    }
  }

  function postParentMessage(type, detail) {
    try {
      var win = getWindow();
      if (!win.parent || win.parent === win) return false;
      var origin = currentOrigin();
      if (!origin) return false;
      var message = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-publication",
        version: INTERNAL_VERSION,
        detail: sanitizeBrowserValue(detail || {}, 0),
        ts: Date.now()
      };
      win.parent.postMessage(message, origin);
      return true;
    } catch (error) {
      return false;
    }
  }

  function dispatchParentEvent(type, detail) {
    try {
      var win = getWindow();
      if (win.parent && win.parent !== win && win.parent.dispatchEvent && typeof win.parent.CustomEvent === "function") {
        win.parent.dispatchEvent(new win.parent.CustomEvent(type, { detail: sanitizeBrowserValue(detail || {}, 0) }));
        return true;
      }
    } catch (error) {}
    return false;
  }

  function emitChangeEvent(response, publication) {
    try {
      var safePublication = sanitizePublicationForBrowser(publication || collectData());
      var detail = {
        response: sanitizeBrowserValue({
          ok: !response || response.ok !== false,
          code: response && response.code,
          status_code: response && (response.status_code || response.statusCode),
          request_id: response && (response.request_id || response.requestId)
        }, 0),
        publication: safePublication,
        projectPublicId: state.projectPublicId,
        visibility: safePublication.visibility,
        projectRole: state.projectRole,
        editor3dAvailable: !!workspaceAvailability().editor3d,
        chunkProvisioningStatus: state.chunkProvisioningStatus,
        chunkAccessSyncStatus: state.chunkAccessSyncStatus
      };
      var type = state.config && state.config.parentEvents
        ? state.config.parentEvents.publicationChanged || EVENT_PUBLICATION_CHANGED
        : EVENT_PUBLICATION_CHANGED;
      emitLocal(type, sanitizeBrowserValue(detail, 0));
      postParentMessage(type, detail);
      dispatchParentEvent(type, detail);
      try {
        var win = getWindow();
        if (win.parent && win.parent !== win) win.parent.dispatchEvent(new win.parent.Event("project-sidebar:refresh"));
      } catch (error) {}
      return true;
    } catch (error) {
      return false;
    }
  }

  function updateDisabledState() {
    try {
      var visibility = currentVisibility();
      var disabled = !canWritePublication();
      var reason = disabled ? disabledReason() : "";
      var availability = workspaceAvailability();

      (state.refs.checkboxes || []).forEach(function disableCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");
          var workspaceAvailable = isAllowedWorkspace(workspace) && availability[workspace] !== false;
          var itemDisabled = disabled || !workspaceAvailable;
          input.disabled = itemDisabled;
          input.setAttribute("aria-disabled", itemDisabled ? "true" : "false");
          input.setAttribute("data-workspace-available", workspaceAvailable ? "true" : "false");
          if (!workspaceAvailable) {
            input.checked = false;
            input.setAttribute("title", workspaceUnavailableReason(workspace));
          } else if (reason) {
            input.setAttribute("title", reason);
          } else {
            input.removeAttribute("title");
          }
        } catch (error) {}
      });

      if (state.refs.requireAuth) {
        state.refs.requireAuth.disabled = disabled || visibility === "private";
        state.refs.requireAuth.setAttribute("aria-disabled", state.refs.requireAuth.disabled ? "true" : "false");
      }
      if (state.refs.requirePermission) {
        state.refs.requirePermission.disabled = disabled || visibility === "private";
        state.refs.requirePermission.setAttribute("aria-disabled", state.refs.requirePermission.disabled ? "true" : "false");
      }
      if (state.refs.save) {
        state.refs.save.disabled = disabled;
        state.refs.save.setAttribute("aria-disabled", disabled ? "true" : "false");
        if (reason) state.refs.save.setAttribute("title", reason); else state.refs.save.removeAttribute("title");
      }
      if (state.refs.reset) {
        state.refs.reset.disabled = disabled;
        state.refs.reset.setAttribute("aria-disabled", disabled ? "true" : "false");
      }

      (state.refs.optionCards || []).forEach(function disableCard(card) {
        try {
          var workspace = normalizeWorkspace(card.getAttribute("data-workspace") || card.getAttribute("data-value") || "");
          var workspaceAvailable = isAllowedWorkspace(workspace) && availability[workspace] !== false;
          var cardDisabled = disabled || !workspaceAvailable;
          card.classList.toggle(CLASS_DISABLED, cardDisabled);
          card.setAttribute("aria-disabled", cardDisabled ? "true" : "false");
          card.setAttribute("data-workspace-available", workspaceAvailable ? "true" : "false");
          if (!workspaceAvailable) card.setAttribute("title", workspaceUnavailableReason(workspace));
          else if (reason) card.setAttribute("title", reason);
          else card.removeAttribute("title");
        } catch (error) {}
      });

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
        state.refs.card.setAttribute("data-project-publication-disabled", disabled ? "true" : "false");
        state.refs.card.setAttribute("data-project-publication-disabled-reason", reason || "");
        state.refs.card.setAttribute("data-project-role", state.projectRole);
        state.refs.card.setAttribute("data-editor3d-publication-available", availability.editor3d ? "true" : "false");
        state.refs.card.setAttribute("data-editor3d-publication-reason", workspaceUnavailableReason("editor3d"));
      }
    } catch (error) {}
  }

  function onInputChange() {
    try {
      if (!canWritePublication()) {
        updateDisabledState();
        if (disabledReason()) setAlert("info", disabledReason());
        return;
      }
      var data = collectData();
      applyData(data, { keepDirty: true });
      setDirty(true);
      setAlert("", "");
    } catch (error) {}
  }

  function onWorkspaceOptionClick(event) {
    try {
      var target = event && event.currentTarget ? event.currentTarget : null;
      if (!target || !canWritePublication()) {
        return;
      }

      var workspace = normalizeWorkspace(target.getAttribute("data-workspace") || target.getAttribute("data-value") || "");
      if (!isAllowedWorkspace(workspace)) {
        return;
      }

      var checkbox = query("[data-project-publication-checkbox][data-workspace='" + workspace + "']", state.refs.card) ||
        query("[data-project-publication-checkbox][name='" + workspace + "']", state.refs.card);

      if (checkbox) {
        checkbox.checked = !checkbox.checked;
        onInputChange();
      }
    } catch (error) {}
  }

  function onSaveClick(event) {
    try {
      if (event && event.preventDefault) {
        event.preventDefault();
      }

      void savePublication();
    } catch (error) {}
  }

  function onResetClick(event) {
    try {
      if (event && event.preventDefault) {
        event.preventDefault();
      }

      resetPublication();
    } catch (error) {}
  }

  function onVisibilityChanged(event) {
    try {
      var detail = event && event.detail ? event.detail : {};
      var visibility = normalizeVisibility(detail.visibility || detail.value || currentVisibility(), "private");
      if (state.refs.card) state.refs.card.setAttribute("data-project-visibility", visibility);
      if (state.refs.root) state.refs.root.setAttribute("data-project-visibility", visibility);
      var data = collectData();
      data.visibility = visibility;
      data.effective_published_workspaces = effectiveWorkspaces(visibility, data.published_workspaces);
      data.effectivePublishedWorkspaces = safeClone(data.effective_published_workspaces);
      if (visibility === "private") {
        data.require_auth = true;
        data.requireAuth = true;
        data.require_project_permission = true;
        data.requireProjectPermission = true;
      }
      applyData(data, { keepDirty: true });
      if (canWritePublication()) setDirty(true);
    } catch (error) {}
  }

  function onMessage(event) {
    try {
      if (!isTrustedMessageEvent(event)) return;
      var data = event && event.data;
      if (!data || typeof data !== "object") return;
      var type = trimString(data.type || data.kind, "");
      if (type === EVENT_VISIBILITY_CHANGED) {
        onVisibilityChanged({ detail: sanitizeBrowserValue(data.detail || data, 0) });
      }
    } catch (error) {}
  }

  function wireEvents() {
    try {
      addListener(state.refs.save, "click", onSaveClick);
      addListener(state.refs.reset, "click", onResetClick);

      (state.refs.checkboxes || []).forEach(function wireCheckbox(input) {
        addListener(input, "change", onInputChange);
      });

      (state.refs.optionCards || []).forEach(function wireOption(card) {
        addListener(card, "click", onWorkspaceOptionClick);
        addListener(card, "keydown", function onCardKeydown(event) {
          try {
            if (event && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              onWorkspaceOptionClick({ currentTarget: card });
            }
          } catch (error) {}
        });
      });

      addListener(state.refs.requireAuth, "change", onInputChange);
      addListener(state.refs.requirePermission, "change", onInputChange);

      if (state.refs.root) {
        addListener(state.refs.root, EVENT_VISIBILITY_CHANGED, onVisibilityChanged);
      }

      if (state.refs.visibilityInput) {
        addListener(state.refs.visibilityInput, "change", onVisibilityChanged);
        addListener(state.refs.visibilityInput, "input", onVisibilityChanged);
      }

      (state.refs.visibilityRadios || []).forEach(function wireVisibilityRadio(input) {
        addListener(input, "change", onVisibilityChanged);
      });

      addListener(window, "message", onMessage);
    } catch (error) {}
  }

  function syncInitialStateFromRefs() {
    try {
      state.projectPublicId = trimString(state.refs.card && state.refs.card.getAttribute("data-project-public-id"), "") || state.config.projectPublicId;
      state.endpoint = normalizePublicationEndpoint(
        trimString(state.refs.card && state.refs.card.getAttribute("data-publication-url"), "") || state.config.endpoint,
        state.projectPublicId
      );
      state.isNew = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-is-new"), state.config.isNew);
      state.projectRole = normalizeProjectRole(
        trimString(state.refs.card && state.refs.card.getAttribute("data-project-role"), "") || state.config.projectRole,
        state.isNew ? "owner" : "viewer"
      );
      state.identityConsistent = toBooleanSafe(state.config.identityConsistent, true);
      state.localLinkState = lowerString(state.config.localLinkState, "not_applicable");
      state.accessMode = lowerString(state.config.accessMode, "anonymous");
      state.canManage = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-can-manage"), state.config.canManage) && !!ROLE_CAN_PUBLISH[state.projectRole];
      state.canPublish = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-can-publish"), state.config.canPublish || state.config.canManage) && !!ROLE_CAN_PUBLISH[state.projectRole];
      state.canMutate = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-can-mutate"), state.config.canMutate || state.config.canPublish || state.config.canManage) && !!ROLE_CAN_PUBLISH[state.projectRole];
      state.canEdit = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-can-edit"), state.config.canEdit) && state.projectRole !== "viewer";
      state.demoMode = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-demo-mode"), state.config.demoMode);
      state.persistent = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-persistent"), state.config.persistent);
      state.publicViewer = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-public-viewer"), state.config.publicViewer);
      state.readOnly = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-read-only"), state.config.readOnly);
      state.authUnavailable = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-auth-unavailable"), state.config.authUnavailable);
      state.userBlocked = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-user-blocked"), state.config.userBlocked);
      state.accessBlocked = toBooleanSafe(state.refs.card && state.refs.card.getAttribute("data-project-access-blocked"), state.config.accessBlocked);

      state.chunkReady = !!state.config.chunkReady;
      state.chunkProvisioningStatus = normalizeOperationalStatus(state.config.chunkProvisioningStatus, "pending");
      state.chunkAccessSyncStatus = normalizeOperationalStatus(state.config.chunkAccessSyncStatus, "disabled");
      state.chunkAccessRequired = !!state.config.chunkAccessRequired;
      state.publicEditor3dVerified = !!state.config.publicEditor3dVerified;
      state.workspaceAvailability = safeClone(state.config.workspaceAvailability || state.workspaceAvailability);
      state.workspaceReasons = safeClone(state.config.workspaceReasons || state.workspaceReasons);

      var identityMismatch = !state.identityConsistent || state.localLinkState === "identity_mismatch" || state.localLinkState === "inactive";
      if (state.publicViewer || state.projectRole === "viewer" || state.authUnavailable || state.userBlocked || state.accessBlocked || identityMismatch) {
        state.readOnly = true;
        state.canManage = false;
        state.canPublish = false;
        state.canMutate = false;
      }
      if (state.demoMode || state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked || identityMismatch) {
        state.persistent = false;
      }

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-public-id", state.projectPublicId || "");
        state.refs.card.setAttribute("data-project-publication-version", String(INTERNAL_VERSION));
        state.refs.card.setAttribute("data-project-role", state.projectRole);
      }
    } catch (error) {}
  }

  function init() {
    if (state.initialized) {
      return state;
    }

    try {
      state.destroyed = false;
      state.config = getConfig();
      state.refs = queryRefs();

      if (!state.refs.card) {
        return state;
      }

      syncInitialStateFromRefs();

      state.initialData = parseInitialData();
      state.currentData = safeClone(state.initialData);

      applyData(state.initialData, { keepDirty: false });
      wireEvents();
      updateDisabledState();

      state.initialized = true;

      emitLocal(EVENT_PUBLICATION_READY, {
        publication: state.currentData,
        canManage: state.canManage,
        canPublish: state.canPublish,
        canWrite: canWritePublication(),
        isNew: state.isNew,
        demoMode: state.demoMode,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        endpoint: buildEndpoint()
      });

      try {
        if (window.__VECTOPLAN_DEBUG_ENABLED__ === true) {
          window.__VECTOPLAN_PROJECT_PUBLICATION_STATE__ = getSnapshot();
        }
      } catch (error) {}

      return state;
    } catch (error) {
      state.lastError = normalizeError(error);
      setAlert("error", "Veröffentlichungseinstellungen konnten nicht initialisiert werden.");
      return state;
    }
  }

  function destroy() {
    try {
      removeAllListeners();
      state.destroyed = true;
      state.initialized = false;
    } catch (error) {}

    return true;
  }

  function getSnapshot() {
    try {
      return sanitizeBrowserValue({
        version: INTERNAL_VERSION,
        initialized: state.initialized,
        destroyed: state.destroyed,
        isSaving: state.isSaving,
        isLoading: state.isLoading,
        isDirty: state.isDirty,
        canManage: state.canManage,
        canPublish: state.canPublish,
        canMutate: state.canMutate,
        canWrite: canWritePublication(),
        isNew: state.isNew,
        demoMode: state.demoMode,
        persistent: state.persistent,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        identityConsistent: state.identityConsistent,
        localLinkState: state.localLinkState,
        projectRole: state.projectRole,
        accessMode: state.accessMode,
        disabledReason: disabledReason(),
        projectPublicId: state.projectPublicId,
        endpoint: buildEndpoint(),
        chunkReady: state.chunkReady,
        chunkProvisioningStatus: state.chunkProvisioningStatus,
        chunkAccessSyncStatus: state.chunkAccessSyncStatus,
        publicEditor3dVerified: state.publicEditor3dVerified,
        workspaceAvailability: workspaceAvailability(),
        initialData: sanitizePublicationForBrowser(state.initialData || {}),
        currentData: collectData(),
        lastError: state.lastError
      }, 0);
    } catch (error) {
      return { version: INTERNAL_VERSION, initialized: false, error: normalizeError(error) };
    }
  }

  var api = {
    version: INTERNAL_VERSION,
    init: init,
    destroy: destroy,
    save: savePublication,
    reset: resetPublication,
    refresh: refreshPublication,
    collectData: collectData,
    buildPayloadForSave: buildPayloadForSave,
    applyData: applyData,
    getSnapshot: getSnapshot,
    setAlert: setAlert,
    canWrite: canWritePublication,
    disabledReason: disabledReason,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      normalizeVisibility: normalizeVisibility,
      normalizeWorkspace: normalizeWorkspace,
      normalizeWorkspaces: normalizeWorkspaces,
      sanitizeWorkspaceMap: sanitizeWorkspaceMap,
      effectiveWorkspaces: effectiveWorkspaces,
      requestJson: requestJson,
      normalizeError: normalizeError,
      extractPublicationPayload: extractPublicationPayload,
      normalizePublicationEndpoint: normalizePublicationEndpoint,
      normalizeProjectRole: normalizeProjectRole,
      workspaceAvailability: workspaceAvailability,
      sanitizeBrowserValue: sanitizeBrowserValue,
      isTrustedMessageEvent: isTrustedMessageEvent
    }
  };

  try {
    global[EXPORT_NAME] = api;

    if (global.__VECTOPLAN_DEBUG_ENABLED__ === true) {
      if (!global.__VECTOPLAN_DEBUG__) {
        global.__VECTOPLAN_DEBUG__ = {};
      }
      global.__VECTOPLAN_DEBUG__.projectPublication = api;
    }
  } catch (error) {}

  try {
    var doc = getDocument();

    if (doc && doc.readyState === "loading") {
      doc.addEventListener("DOMContentLoaded", function onReady() {
        init();
      }, { once: true });
    } else {
      init();
    }
  } catch (error) {
    init();
  }
})(window);