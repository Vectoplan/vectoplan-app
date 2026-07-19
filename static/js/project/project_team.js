/* services/vectoplan-app/static/js/project/project_team.js */

/*
  VECTOPLAN Project Team

  Zweck:
  - Verwaltet Projektmitglieder, Rollen und Einladungen im Projekt-Workspace.
  - Nutzt ausschließlich die App-API als Browser-Gateway.
  - Erzeugt keine Benutzeraccounts und sendet keine Auth-IDs.
  - Owner-Änderungen erfolgen ausschließlich über den dedizierten Transfer-Flow.

  Sicherheitsregeln:
  - Nur Projekt-Owner und Projekt-Admins dürfen Teamdaten lesen oder ändern.
  - Globale Account-/Plattformrollen erzeugen keine Projektberechtigung.
  - Rollenänderungen senden ausschließlich viewer, editor oder admin.
  - Viewer, Demo, Public Viewer, Read-only, Auth-Ausfall, Identity-Mismatch und
    fehlende lokale AppUser-Verknüpfung bleiben fail-closed.
  - Alle Endpunkte müssen same-origin sein und exakt zum aktuellen Projekt gehören.
  - Browser-Events enthalten keine E-Mail-Adressen, Auth-IDs, Tokens oder Rohantworten.
  - Frontend-Gating ist nur UX; das Backend bleibt die Berechtigungswahrheit.
*/

(function initVectoplanProjectTeam(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectTeam";
  var INTERNAL_VERSION = 5;

  var ROOT_SELECTOR = "[data-project-workspace]";
  var CARD_SELECTOR = "[data-project-team-card]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var EVENT_READY = "vectoplan:project-team:ready";
  var EVENT_TEAM_CHANGED = "vectoplan:project:team:changed";
  var EVENT_INVITATION_CREATED = "vectoplan:project:invitation:created";
  var EVENT_INVITATION_REVOKED = "vectoplan:project:invitation:revoked";
  var EVENT_MEMBER_CHANGED = "vectoplan:project:member:changed";
  var EVENT_MEMBER_REMOVED = "vectoplan:project:member:removed";
  var EVENT_ACCESS_SYNC_CHANGED = "vectoplan:project:chunk-access-sync:changed";
  var EVENT_REPAIR_REQUIRED = "vectoplan:project:repair-required";
  var EVENT_ERROR = "vectoplan:project:error";
  var EVENT_SIDEBAR_REFRESH = "project-sidebar:refresh";

  var CLASS_LOADING = "is-loading";
  var CLASS_SAVING = "is-saving";
  var CLASS_ERROR = "is-error";
  var CLASS_DISABLED = "is-disabled";

  var ROLE_VIEWER = "viewer";
  var ROLE_EDITOR = "editor";
  var ROLE_ADMIN = "admin";
  var ROLE_OWNER = "owner";

  var PROJECT_ROLES = {
    viewer: true,
    editor: true,
    admin: true,
    owner: true
  };

  var ASSIGNABLE_ROLES = {
    viewer: true,
    editor: true,
    admin: true
  };

  var MANAGER_ROLES = {
    owner: true,
    admin: true
  };

  var INVITATION_TERMINAL_STATUSES = {
    accepted: true,
    rejected: true,
    revoked: true,
    expired: true,
    failed: true,
    cancelled: true,
    canceled: true
  };

  var ACCESS_SYNC_READY_STATUSES = {
    ready: true,
    synced: true,
    success: true,
    successful: true,
    disabled: true,
    not_required: true
  };

  var ACCESS_SYNC_PENDING_STATUSES = {
    pending: true,
    syncing: true,
    queued: true,
    waiting: true,
    deferred: true,
    unknown: true
  };

  var ACCESS_SYNC_REPAIR_STATUSES = {
    failed: true,
    error: true,
    repair_required: true
  };

  var REQUEST_TIMEOUT_MS = 20000;
  var MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
  var MAX_TEXT_LENGTH = 4000;

  var SENSITIVE_KEYS = {
    token: true,
    access_token: true,
    refresh_token: true,
    invitation_token: true,
    token_hash: true,
    password: true,
    secret: true,
    api_key: true,
    apikey: true,
    authorization: true,
    cookie: true,
    cookies: true,
    session: true,
    session_id: true,
    csrf: true,
    csrf_token: true,
    auth_user_id: true,
    authuserid: true,
    canonical_user_id: true,
    user_id: true,
    userid: true,
    local_user_id: true,
    target_user_id: true,
    targetuserid: true,
    email: true,
    user_email: true,
    invitee_email: true,
    account_id: true,
    owner_user_id: true,
    auth_owner_user_id: true,
    metadata: true,
    metadata_json: true,
    settings: true,
    service_refs: true,
    service_links: true,
    raw_auth: true,
    headers: true,
    request_headers: true,
    response_headers: true,
    stack: true
  };

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isLoading: false,
    isSaving: false,

    projectRole: "",
    canEdit: false,
    canManage: false,
    canManageTeam: false,
    canReadTeam: false,
    canWrite: false,

    isNew: false,
    demoMode: false,
    persistent: false,
    publicViewer: false,
    readOnly: true,
    authenticated: false,
    authUnavailable: false,
    userBlocked: false,
    accessBlocked: false,
    identityConsistent: true,
    localLinkState: "",

    projectPublicId: "",
    projectId: "",
    membersUrl: "",
    invitationsUrl: "",
    disabledReason: "",

    members: [],
    invitations: [],
    chunkAccessSync: {
      status: "unknown",
      ready: false,
      required: false,
      repairRequired: false,
      retryable: false,
      code: "",
      message: "",
      requestId: ""
    },

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
      return getWindow().document || document || null;
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

  function trimString(value, fallback, maxLength) {
    try {
      if (value === null || value === undefined) {
        return fallback || "";
      }

      var text = String(value).trim();
      if (!text) {
        text = fallback || "";
      }

      var limit = Number(maxLength || 0);
      if (limit > 0 && text.length > limit) {
        return text.slice(0, limit);
      }

      return text;
    } catch (error) {
      return fallback || "";
    }
  }

  function lowerString(value, fallback, maxLength) {
    return trimString(value, fallback || "", maxLength || 0).toLowerCase();
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
      if (value === null || value === undefined) {
        return !!fallback;
      }

      if (typeof value === "string") {
        var normalized = value.trim().toLowerCase();
        if (["true", "yes", "y", "on", "ja", "enabled", "enable", "active", "ok", "ready", "linked"].indexOf(normalized) !== -1) {
          return true;
        }
        if (["false", "no", "n", "off", "nein", "disabled", "disable", "inactive", "error", "failed", "readonly", "read_only", "null", "none", "undefined", ""].indexOf(normalized) !== -1) {
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
      return JSON.parse(JSON.stringify(value === undefined ? null : value));
    } catch (error) {
      if (isArray(value)) {
        return value.slice();
      }
      if (isObject(value)) {
        var clone = {};
        Object.keys(value).forEach(function copyKey(key) {
          clone[key] = value[key];
        });
        return clone;
      }
      return value;
    }
  }

  function sanitizeBrowserValue(value, depth) {
    var currentDepth = Number(depth || 0);
    if (currentDepth > 6) {
      return "[truncated]";
    }

    try {
      if (value === null || value === undefined || typeof value === "boolean" || typeof value === "number") {
        return value;
      }
      if (typeof value === "string") {
        var text = value.length > MAX_TEXT_LENGTH ? value.slice(0, MAX_TEXT_LENGTH) : value;
        text = text.replace(/Bearer\s+[A-Za-z0-9._~+\/-]+/gi, "Bearer [redacted]");
        text = text.replace(/([?&](?:token|access_token|refresh_token|api_key|apikey|secret|session|csrf_token)=)[^&#\s]*/gi, "$1[redacted]");
        return text;
      }
      if (isArray(value)) {
        return value.slice(0, 200).map(function sanitizeItem(item) {
          return sanitizeBrowserValue(item, currentDepth + 1);
        });
      }
      if (isObject(value)) {
        var result = {};
        Object.keys(value).slice(0, 300).forEach(function sanitizeKey(key) {
          var normalizedKey = lowerString(key, "", 160).replace(/-/g, "_");
          if (!normalizedKey || SENSITIVE_KEYS[normalizedKey]) {
            return;
          }
          result[key] = sanitizeBrowserValue(value[key], currentDepth + 1);
        });
        return result;
      }
      return trimString(value, "", MAX_TEXT_LENGTH);
    } catch (error) {
      return null;
    }
  }

  function normalizeError(error) {
    try {
      var payload = error && error.payload ? sanitizeBrowserValue(error.payload, 0) : null;
      var message = trimString(
        error && (error.message || (payload && (payload.message || payload.error))),
        typeof error === "string" ? error : "Unknown error",
        1000
      );

      return {
        name: trimString(error && error.name, "Error", 120),
        message: message,
        code: trimString(error && (error.code || (payload && payload.code)), "", 160),
        status: Number(error && (error.status || error.statusCode || (payload && (payload.status_code || payload.statusCode)))) || null,
        requestId: trimString(payload && (payload.request_id || payload.requestId), "", 200),
        payload: payload
      };
    } catch (innerError) {
      return {
        name: "Error",
        message: "Unknown error",
        code: "",
        status: null,
        requestId: "",
        payload: null
      };
    }
  }

  function query(selector, root) {
    try {
      var base = root || getDocument();
      return base && base.querySelector ? base.querySelector(selector) : null;
    } catch (error) {
      return null;
    }
  }

  function queryAll(selector, root) {
    try {
      var base = root || getDocument();
      return base && base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll(selector)) : [];
    } catch (error) {
      return [];
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
      (state.listeners || []).forEach(function removeOne(item) {
        try {
          if (item && item.target && item.target.removeEventListener) {
            item.target.removeEventListener(item.type, item.handler, item.options);
          }
        } catch (error) {}
      });
    } catch (error) {}
    state.listeners = [];
  }

  function closest(target, selector) {
    try {
      return target && target.closest ? target.closest(selector) : null;
    } catch (error) {
      return null;
    }
  }

  function createElement(tagName, className, text) {
    try {
      var doc = getDocument();
      if (!doc || !doc.createElement) {
        return null;
      }
      var node = doc.createElement(tagName);
      if (className) {
        node.className = className;
      }
      if (text !== undefined && text !== null) {
        node.textContent = String(text);
      }
      return node;
    } catch (error) {
      return null;
    }
  }

  function append(parent, child) {
    try {
      if (parent && child) {
        parent.appendChild(child);
      }
    } catch (error) {}
  }

  function removeChildren(node) {
    try {
      while (node && node.firstChild) {
        node.removeChild(node.firstChild);
      }
    } catch (error) {}
  }

  function attr(element, name, fallback) {
    try {
      if (!element || !element.getAttribute) {
        return fallback || "";
      }
      var value = element.getAttribute(name);
      return value === null || value === undefined || value === "" ? fallback || "" : value;
    } catch (error) {
      return fallback || "";
    }
  }

  function pickBoolean(values, fallback) {
    try {
      for (var index = 0; index < values.length; index += 1) {
        if (values[index] !== undefined && values[index] !== null && values[index] !== "") {
          return toBooleanSafe(values[index], fallback);
        }
      }
      return !!fallback;
    } catch (error) {
      return !!fallback;
    }
  }

  function pickString(values, fallback) {
    try {
      for (var index = 0; index < values.length; index += 1) {
        var text = trimString(values[index], "");
        if (text) {
          return text;
        }
      }
      return fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function normalizeProjectRole(value, fallback) {
    try {
      var role = lowerString(value, fallback || "", 40).replace(/-/g, "_").replace(/\s+/g, "_");
      var aliases = {
        administrator: ROLE_ADMIN,
        manager: ROLE_ADMIN,
        edit: ROLE_EDITOR,
        writer: ROLE_EDITOR,
        view: ROLE_VIEWER,
        reader: ROLE_VIEWER,
        readonly: ROLE_VIEWER,
        read_only: ROLE_VIEWER
      };
      role = aliases[role] || role;
      return PROJECT_ROLES[role] ? role : fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function normalizeAssignableRole(value, fallback) {
    var role = normalizeProjectRole(value, "");
    if (ASSIGNABLE_ROLES[role]) {
      return role;
    }
    return fallback && ASSIGNABLE_ROLES[fallback] ? fallback : "";
  }

  function roleLabel(role) {
    var normalized = normalizeProjectRole(role, ROLE_VIEWER);
    if (normalized === ROLE_OWNER) {
      return "Owner";
    }
    if (normalized === ROLE_ADMIN) {
      return "Admin";
    }
    if (normalized === ROLE_EDITOR) {
      return "Editor";
    }
    return "Viewer";
  }

  function statusLabel(status) {
    var normalized = lowerString(status, "pending", 80);
    if (normalized === "accepted" || normalized === "active") {
      return "aktiv";
    }
    if (normalized === "pending") {
      return "wartet";
    }
    if (normalized === "revoked" || normalized === "cancelled" || normalized === "canceled") {
      return "widerrufen";
    }
    if (normalized === "expired") {
      return "abgelaufen";
    }
    if (normalized === "rejected") {
      return "abgelehnt";
    }
    if (normalized === "failed") {
      return "fehlgeschlagen";
    }
    return normalized;
  }

  function currentOrigin() {
    try {
      var locationObject = getWindow().location;
      return trimString(locationObject && locationObject.origin, "", 500);
    } catch (error) {
      return "";
    }
  }

  function generateRequestId() {
    try {
      var win = getWindow();
      if (win.crypto && typeof win.crypto.randomUUID === "function") {
        return "req_" + win.crypto.randomUUID().replace(/-/g, "");
      }
    } catch (error) {}
    return "req_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 14);
  }

  function validProjectPublicId(value) {
    var text = trimString(value, "", 160);
    return !!text && text !== "new" && /^[A-Za-z0-9._~-]+$/.test(text);
  }

  function positiveLocalUserId(value) {
    try {
      var text = trimString(value, "", 40);
      if (!/^\d+$/.test(text)) {
        return "";
      }
      var parsed = Number(text);
      return Number.isSafeInteger(parsed) && parsed > 0 ? String(parsed) : "";
    } catch (error) {
      return "";
    }
  }

  function safeInvitationId(value) {
    var text = trimString(value, "", 200);
    return /^[A-Za-z0-9._~-]+$/.test(text) ? text : "";
  }

  function normalizeApiUrl(value, expectedPath) {
    try {
      var origin = currentOrigin();
      if (!origin || !expectedPath) {
        return "";
      }
      var candidate = trimString(value, expectedPath, 2000);
      var parsed = new URL(candidate, origin + "/");
      if (parsed.origin !== origin || parsed.username || parsed.password || parsed.hash) {
        return "";
      }
      if (parsed.pathname !== expectedPath || parsed.search) {
        return "";
      }
      return parsed.pathname;
    } catch (error) {
      return "";
    }
  }

  function collectionPath(projectPublicId, collection) {
    if (!validProjectPublicId(projectPublicId) || ["members", "invitations"].indexOf(collection) === -1) {
      return "";
    }
    return "/v1/projects/" + encodeURIComponent(projectPublicId) + "/" + collection;
  }

  function normalizeCollectionUrl(value, projectPublicId, collection) {
    var expected = collectionPath(projectPublicId, collection);
    return normalizeApiUrl(value || expected, expected);
  }

  function memberUrl(userId) {
    var uid = positiveLocalUserId(userId);
    var base = normalizeCollectionUrl(state.membersUrl, state.projectPublicId, "members");
    if (!uid || !base) {
      return "";
    }
    return normalizeApiUrl(base + "/" + encodeURIComponent(uid), base + "/" + encodeURIComponent(uid));
  }

  function invitationUrl(invitationId) {
    var id = safeInvitationId(invitationId);
    var base = normalizeCollectionUrl(state.invitationsUrl, state.projectPublicId, "invitations");
    if (!id || !base) {
      return "";
    }
    return normalizeApiUrl(base + "/" + encodeURIComponent(id), base + "/" + encodeURIComponent(id));
  }

  function normalizeAccessSyncStatus(value) {
    var status = lowerString(value, "unknown", 80).replace(/-/g, "_").replace(/\s+/g, "_");
    if (ACCESS_SYNC_READY_STATUSES[status] || ACCESS_SYNC_PENDING_STATUSES[status] || ACCESS_SYNC_REPAIR_STATUSES[status]) {
      return status;
    }
    return "unknown";
  }

  function extractChunkAccessSync(payload) {
    try {
      var data = isObject(payload) ? payload : {};
      var project = isObject(data.project) ? data.project : {};
      var nestedData = isObject(data.data) ? data.data : {};
      var source = isObject(data.chunk_access_sync) ? data.chunk_access_sync :
        isObject(data.chunkAccessSync) ? data.chunkAccessSync :
          isObject(project.chunk_access_sync) ? project.chunk_access_sync :
            isObject(project.chunkAccessSync) ? project.chunkAccessSync :
              isObject(nestedData.chunk_access_sync) ? nestedData.chunk_access_sync :
                isObject(nestedData.chunkAccessSync) ? nestedData.chunkAccessSync : {};

      var status = normalizeAccessSyncStatus(source.status || source.sync_status || source.syncStatus || source.state);
      var required = pickBoolean([
        source.required,
        source.sync_required,
        source.syncRequired,
        data.chunk_access_sync_required,
        data.chunkAccessSyncRequired
      ], false);
      var repairRequired = pickBoolean([
        source.repair_required,
        source.repairRequired,
        source.requires_repair,
        source.requiresRepair
      ], !!ACCESS_SYNC_REPAIR_STATUSES[status]);
      var ready = pickBoolean([
        source.ready,
        source.synced,
        source.ok
      ], !!ACCESS_SYNC_READY_STATUSES[status]);

      if (required && (ACCESS_SYNC_PENDING_STATUSES[status] || ACCESS_SYNC_REPAIR_STATUSES[status])) {
        ready = false;
      }
      if (repairRequired) {
        ready = false;
      }

      return {
        status: status,
        ready: ready,
        required: required,
        repairRequired: repairRequired,
        retryable: pickBoolean([source.retryable], false),
        code: trimString(source.code || source.error_code || source.errorCode, "", 160),
        message: trimString(source.message || source.error || source.error_message || source.errorMessage, "", 500),
        requestId: trimString(source.request_id || source.requestId || data.request_id || data.requestId, "", 200)
      };
    } catch (error) {
      return {
        status: "unknown",
        ready: false,
        required: false,
        repairRequired: false,
        retryable: false,
        code: "",
        message: "",
        requestId: ""
      };
    }
  }

  function accessSyncSummary() {
    return safeClone(state.chunkAccessSync || {});
  }

  function applyChunkAccessSync(payload, options) {
    var opts = isObject(options) ? options : {};
    try {
      var sync = extractChunkAccessSync(payload);
      if (sync.status === "unknown" && !sync.code && !sync.message && !sync.required) {
        return state.chunkAccessSync;
      }

      state.chunkAccessSync = sync;
      var detail = {
        projectPublicId: state.projectPublicId,
        status: sync.status,
        ready: sync.ready,
        required: sync.required,
        repairRequired: sync.repairRequired,
        retryable: sync.retryable,
        code: sync.code,
        requestId: sync.requestId
      };

      emitLocal(EVENT_ACCESS_SYNC_CHANGED, detail);
      postParentMessage(EVENT_ACCESS_SYNC_CHANGED, detail);
      dispatchParentEvent(EVENT_ACCESS_SYNC_CHANGED, detail);

      if (sync.repairRequired || ACCESS_SYNC_REPAIR_STATUSES[sync.status]) {
        emitLocal(EVENT_REPAIR_REQUIRED, detail);
        postParentMessage(EVENT_REPAIR_REQUIRED, detail);
        dispatchParentEvent(EVENT_REPAIR_REQUIRED, detail);
      }

      if (!opts.silent) {
        updateStatus();
      }
      return sync;
    } catch (error) {
      return state.chunkAccessSync;
    }
  }

  function mutationSuccessMessage(baseMessage) {
    var sync = state.chunkAccessSync || {};
    if (sync.repairRequired || ACCESS_SYNC_REPAIR_STATUSES[sync.status]) {
      return baseMessage + " Die App-Änderung ist gespeichert; der Chunk-Zugriff muss repariert werden.";
    }
    if (sync.required && ACCESS_SYNC_PENDING_STATUSES[sync.status]) {
      return baseMessage + " Die App-Änderung ist gespeichert; der Chunk-Zugriff wird noch synchronisiert.";
    }
    return baseMessage;
  }

  function getConfig() {
    try {
      var win = getWindow();
      var config = win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG || win.PROJECT_WORKSPACE_CONFIG || {};
      if (!isObject(config)) {
        config = {};
      }

      var root = query(ROOT_SELECTOR);
      var card = query(CARD_SELECTOR);
      var paths = isObject(config.paths) ? config.paths : {};
      var parentEvents = isObject(config.parentEvents) ? config.parentEvents : {};
      var project = isObject(config.project) ? config.project : {};
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};
      var access = isObject(config.access) ? config.access : isObject(project.access) ? project.access : {};
      var uiFlags = isObject(config.uiFlags) ? config.uiFlags : isObject(project.ui_flags) ? project.ui_flags : {};

      var publicId = pickString([
        config.projectPublicId,
        config.project_public_id,
        project.public_id,
        project.publicId,
        project.project_public_id,
        project.projectPublicId,
        attr(card, "data-project-public-id", ""),
        attr(root, "data-project-public-id", "")
      ], "");

      var projectRole = normalizeProjectRole(pickString([
        config.projectRole,
        config.project_role,
        access.role,
        access.project_role,
        access.projectRole,
        access.membership_role,
        access.membershipRole,
        project.project_role,
        project.projectRole,
        attr(card, "data-project-role", ""),
        attr(root, "data-project-role", "")
      ], ""), "");

      var demoMode = pickBoolean([
        config.demoMode,
        config.demo_mode,
        uiFlags.demo_mode,
        currentUser.demo_mode,
        currentUser.demoMode,
        currentUser.is_demo,
        attr(card, "data-project-demo-mode", "")
      ], false);

      var publicViewer = pickBoolean([
        config.publicViewer,
        config.isPublicViewer,
        uiFlags.public_viewer,
        access.public_viewer,
        access.publicViewer,
        attr(card, "data-project-public-viewer", "")
      ], false);

      var authUnavailable = pickBoolean([
        config.authUnavailable,
        uiFlags.auth_unavailable,
        currentUser.auth_unavailable,
        currentUser.authUnavailable,
        attr(card, "data-project-auth-unavailable", "")
      ], false);

      var userBlocked = pickBoolean([
        config.userBlocked,
        uiFlags.user_blocked,
        currentUser.user_blocked,
        currentUser.userBlocked,
        attr(card, "data-project-user-blocked", "")
      ], false);

      var accessBlocked = pickBoolean([
        config.accessBlocked,
        uiFlags.access_blocked,
        currentUser.access_blocked,
        currentUser.accessBlocked,
        attr(card, "data-project-access-blocked", "")
      ], false);

      var identityConsistent = pickBoolean([
        config.identityConsistent,
        config.identity_consistent,
        currentUser.identity_consistent,
        currentUser.identityConsistent,
        uiFlags.identity_consistent,
        attr(card, "data-project-identity-consistent", "")
      ], true);

      var localLinkState = lowerString(pickString([
        config.localLinkState,
        config.local_link_state,
        currentUser.local_link_state,
        currentUser.localLinkState,
        attr(card, "data-project-local-link-state", "")
      ], ""), "", 80).replace(/-/g, "_");

      var linkBlocked = ["identity_mismatch", "inactive", "deleted", "unavailable", "error"].indexOf(localLinkState) !== -1;
      if (!identityConsistent || linkBlocked) {
        accessBlocked = true;
      }

      var readOnly = pickBoolean([
        config.readOnly,
        config.readonly,
        uiFlags.read_only,
        access.read_only,
        access.readOnly,
        attr(card, "data-project-read-only", "")
      ], publicViewer || authUnavailable || userBlocked || accessBlocked);

      if (publicViewer || authUnavailable || userBlocked || accessBlocked) {
        readOnly = true;
        demoMode = false;
      }

      var authenticated = pickBoolean([
        config.authenticated,
        currentUser.authenticated,
        currentUser.is_authenticated,
        currentUser.isAuthenticated
      ], false);

      var persistent = pickBoolean([
        config.persistent,
        uiFlags.persistent,
        currentUser.persistent,
        attr(card, "data-project-persistent", "")
      ], authenticated && !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked);

      if (demoMode || publicViewer || authUnavailable || userBlocked || accessBlocked || !identityConsistent || linkBlocked) {
        persistent = false;
      }

      var isNew = pickBoolean([
        config.isNew,
        config.is_new,
        project.is_new,
        project.isNew,
        attr(card, "data-project-is-new", "")
      ], !validProjectPublicId(publicId));

      var roleCanManage = !!MANAGER_ROLES[projectRole];
      var manageFlag = pickBoolean([
        config.canManage,
        uiFlags.can_manage,
        access.can_manage,
        access.canManage,
        attr(card, "data-project-can-manage", "")
      ], roleCanManage);
      var manageTeamFlag = pickBoolean([
        config.canManageTeam,
        uiFlags.can_manage_team,
        access.can_manage_team,
        access.canManageTeam,
        attr(card, "data-project-can-manage-team", "")
      ], manageFlag);

      var canManage = roleCanManage && manageFlag && !readOnly && !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked;
      var canManageTeam = canManage && manageTeamFlag;
      var canReadTeam = canManage && persistent && !isNew;

      var membersUrl = canReadTeam ? normalizeCollectionUrl(
        paths.members || attr(card, "data-members-url", ""),
        publicId,
        "members"
      ) : "";
      var invitationsUrl = canReadTeam ? normalizeCollectionUrl(
        paths.invitations || attr(card, "data-invitations-url", ""),
        publicId,
        "invitations"
      ) : "";

      var canWrite = canReadTeam && canManageTeam && !!membersUrl && !!invitationsUrl;

      return {
        project: project,
        currentUser: currentUser,
        access: access,
        uiFlags: uiFlags,
        projectId: trimString(config.projectId || config.project_id || project.id || project.project_id || project.projectId || attr(card, "data-project-id", ""), "", 80),
        projectPublicId: publicId,
        projectRole: projectRole,
        membersUrl: membersUrl,
        invitationsUrl: invitationsUrl,
        isNew: isNew,
        canEdit: pickBoolean([config.canEdit, access.can_edit, access.canEdit], false),
        canManage: canManage,
        canManageTeam: canManageTeam,
        canReadTeam: canReadTeam,
        canWrite: canWrite,
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
        disabledReason: trimString(attr(card, "data-project-team-disabled-reason", ""), "", 500),
        chunkAccessSync: extractChunkAccessSync(project),
        parentEvents: {
          ready: trimString(parentEvents.teamReady, EVENT_READY, 200),
          teamChanged: trimString(parentEvents.teamChanged, EVENT_TEAM_CHANGED, 200),
          error: trimString(parentEvents.error, EVENT_ERROR, 200)
        }
      };
    } catch (error) {
      return {
        project: {},
        currentUser: {},
        access: {},
        uiFlags: {},
        projectId: "",
        projectPublicId: "",
        projectRole: "",
        membersUrl: "",
        invitationsUrl: "",
        isNew: true,
        canEdit: false,
        canManage: false,
        canManageTeam: false,
        canReadTeam: false,
        canWrite: false,
        demoMode: false,
        persistent: false,
        publicViewer: false,
        readOnly: true,
        authenticated: false,
        authUnavailable: false,
        userBlocked: false,
        accessBlocked: true,
        identityConsistent: false,
        localLinkState: "unavailable",
        disabledReason: "Teamverwaltung konnte nicht initialisiert werden.",
        chunkAccessSync: extractChunkAccessSync({}),
        parentEvents: { ready: EVENT_READY, teamChanged: EVENT_TEAM_CHANGED, error: EVENT_ERROR }
      };
    }
  }

  function queryRefs() {
    var card = query(CARD_SELECTOR);
    return {
      document: getDocument(),
      root: query(ROOT_SELECTOR),
      card: card,
      alert: query(ALERT_SELECTOR),
      status: query("[data-project-team-status]", card),
      inviteEmail: query("[data-project-team-invite-email]", card),
      inviteRole: query("[data-project-team-invite-role]", card),
      inviteSubmit: query("[data-project-team-invite-submit]", card),
      message: query("[data-project-team-message]", card),
      refresh: query("[data-project-team-refresh]", card),
      refreshMembers: query("[data-project-team-members-refresh]", card),
      refreshInvitations: query("[data-project-team-invitations-refresh]", card),
      members: query("[data-project-team-members]", card),
      invitations: query("[data-project-team-invitations]", card),
      initialJson: query("[data-project-team-initial-json]", card)
    };
  }

  function setHidden(element, hidden) {
    try {
      if (!element) {
        return;
      }
      hidden ? element.setAttribute("hidden", "") : element.removeAttribute("hidden");
    } catch (error) {}
  }

  function setGlobalAlert(kind, message) {
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
      var text = trimString(message, "", 1000);
      alert.classList.remove("is-success", "is-warning", "is-error", "is-info");
      alert.removeAttribute("data-kind");
      if (!text) {
        alert.textContent = "";
        setHidden(alert, true);
        return;
      }
      var normalizedKind = lowerString(kind, "info", 30);
      var className = normalizedKind === "success" ? "is-success" :
        normalizedKind === "warning" ? "is-warning" :
          normalizedKind === "error" || normalizedKind === "danger" ? "is-error" : "is-info";
      alert.classList.add(className);
      alert.setAttribute("data-kind", className.replace("is-", ""));
      alert.textContent = text;
      setHidden(alert, false);
    } catch (error) {}
  }

  function setMessage(kind, message) {
    try {
      var node = state.refs && state.refs.message;
      if (!node) {
        setGlobalAlert(kind, message);
        return;
      }
      var text = trimString(message, "", 1000);
      node.classList.remove("is-success", "is-warning", "is-error", "is-info");
      node.removeAttribute("data-kind");
      if (!text) {
        node.textContent = "";
        setHidden(node, true);
        return;
      }
      var normalizedKind = lowerString(kind, "info", 30);
      var className = normalizedKind === "success" ? "is-success" :
        normalizedKind === "warning" ? "is-warning" :
          normalizedKind === "error" || normalizedKind === "danger" ? "is-error" : "is-info";
      node.classList.add(className);
      node.setAttribute("data-kind", className.replace("is-", ""));
      node.textContent = text;
      setHidden(node, false);
    } catch (error) {
      setGlobalAlert(kind, message);
    }
  }

  function disabledReason() {
    try {
      if (state.disabledReason) {
        return state.disabledReason;
      }
      if (state.authUnavailable) {
        return "Auth-Service nicht erreichbar. Teamverwaltung ist deaktiviert.";
      }
      if (!state.identityConsistent || state.localLinkState === "identity_mismatch") {
        return "Die lokale Benutzerverknüpfung stimmt nicht mit der Auth-Identität überein.";
      }
      if (["inactive", "deleted", "unavailable", "error"].indexOf(state.localLinkState) !== -1) {
        return "Der lokale AppUser-Link ist nicht aktiv. Teamverwaltung ist deaktiviert.";
      }
      if (state.userBlocked || state.accessBlocked) {
        return "Der Zugriff ist gesperrt. Teamverwaltung ist deaktiviert.";
      }
      if (state.publicViewer) {
        return "Öffentliche Ansicht: Team, Einladungen und Rechte sind nicht öffentlich verfügbar.";
      }
      if (state.readOnly) {
        return "Dieses Projekt ist schreibgeschützt. Teamverwaltung ist deaktiviert.";
      }
      if (state.isNew) {
        return "Speichere das Projekt zuerst. Danach kannst du Team und Einladungen verwalten.";
      }
      if (state.demoMode) {
        return "Demo-Projekte unterstützen keine dauerhafte Team- oder Einladungsverwaltung.";
      }
      if (!state.persistent) {
        return "Für Teamverwaltung ist ein persistenter, kanonisch verknüpfter AppUser erforderlich.";
      }
      if (!MANAGER_ROLES[state.projectRole]) {
        return "Nur Projekt-Owner und Projekt-Admins dürfen Team und Einladungen verwalten.";
      }
      if (!state.canManage || !state.canManageTeam) {
        return "Du hast keine Berechtigung, Team und Einladungen zu verwalten.";
      }
      if (!state.membersUrl || !state.invitationsUrl) {
        return "Sichere Team-Endpunkte fehlen.";
      }
      return "";
    } catch (error) {
      return "Teamverwaltung ist deaktiviert.";
    }
  }

  function canReadTeam() {
    return !!(
      state.canReadTeam &&
      state.canManage &&
      MANAGER_ROLES[state.projectRole] &&
      !state.isNew &&
      !state.demoMode &&
      state.persistent &&
      !state.publicViewer &&
      !state.readOnly &&
      !state.authUnavailable &&
      !state.userBlocked &&
      !state.accessBlocked &&
      state.identityConsistent &&
      ["identity_mismatch", "inactive", "deleted", "unavailable", "error"].indexOf(state.localLinkState) === -1 &&
      !!state.membersUrl &&
      !!state.invitationsUrl
    );
  }

  function canOperate() {
    return !!(
      canReadTeam() &&
      state.canWrite &&
      state.canManageTeam &&
      !state.isSaving &&
      !state.isLoading
    );
  }

  function setLoading(isLoading) {
    state.isLoading = !!isLoading;
    try {
      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_LOADING, state.isLoading);
        state.refs.card.setAttribute("data-project-team-loading", state.isLoading ? "true" : "false");
      }
    } catch (error) {}
    updateDisabledState();
  }

  function setSaving(isSaving) {
    state.isSaving = !!isSaving;
    try {
      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_SAVING, state.isSaving);
        state.refs.card.setAttribute("data-project-team-saving", state.isSaving ? "true" : "false");
      }
    } catch (error) {}
    updateDisabledState();
  }

  function updateStatus() {
    try {
      var node = state.refs.status;
      if (!node) {
        return;
      }
      node.classList.remove("vp-project-chip--success", "vp-project-chip--muted", "vp-project-chip--warning", "vp-project-chip--error");

      if (state.authUnavailable) {
        node.classList.add("vp-project-chip--error");
        node.textContent = "Auth nicht erreichbar";
      } else if (!state.identityConsistent || state.localLinkState === "identity_mismatch") {
        node.classList.add("vp-project-chip--error");
        node.textContent = "Identität inkonsistent";
      } else if (state.userBlocked || state.accessBlocked) {
        node.classList.add("vp-project-chip--error");
        node.textContent = "Zugriff gesperrt";
      } else if (state.chunkAccessSync.repairRequired || ACCESS_SYNC_REPAIR_STATUSES[state.chunkAccessSync.status]) {
        node.classList.add("vp-project-chip--error");
        node.textContent = "Chunk-Zugriff reparieren";
      } else if (state.chunkAccessSync.required && ACCESS_SYNC_PENDING_STATUSES[state.chunkAccessSync.status]) {
        node.classList.add("vp-project-chip--warning");
        node.textContent = "Chunk-Zugriff ausstehend";
      } else if (canOperate()) {
        node.classList.add("vp-project-chip--success");
        node.textContent = "Verwaltung aktiv";
      } else if (state.isNew) {
        node.classList.add("vp-project-chip--warning");
        node.textContent = "Erst speichern";
      } else if (state.demoMode) {
        node.classList.add("vp-project-chip--muted");
        node.textContent = "Demo";
      } else {
        node.classList.add("vp-project-chip--muted");
        node.textContent = "Geschützt";
      }
    } catch (error) {}
  }

  function updateDisabledState() {
    try {
      var writeDisabled = !canOperate();
      var readDisabled = !canReadTeam() || state.isLoading;
      var reason = writeDisabled ? disabledReason() : "";

      [state.refs.inviteEmail, state.refs.inviteRole, state.refs.inviteSubmit].forEach(function disableInvite(control) {
        try {
          if (!control) {
            return;
          }
          control.disabled = writeDisabled;
          control.setAttribute("aria-disabled", writeDisabled ? "true" : "false");
          reason ? control.setAttribute("title", reason) : control.removeAttribute("title");
        } catch (error) {}
      });

      [state.refs.refresh, state.refs.refreshMembers, state.refs.refreshInvitations].forEach(function disableRefresh(button) {
        try {
          if (button) {
            button.disabled = readDisabled;
            button.setAttribute("aria-disabled", readDisabled ? "true" : "false");
          }
        } catch (error) {}
      });

      queryAll("[data-project-team-member-role]", state.refs.card).forEach(function disableRole(select) {
        try {
          var row = closest(select, "[data-project-team-member]");
          var role = normalizeProjectRole((row && row.getAttribute("data-role")) || select.getAttribute("data-current-role") || select.value, ROLE_VIEWER);
          var owner = role === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true");
          select.disabled = writeDisabled || owner || !positiveLocalUserId(select.getAttribute("data-user-id"));
          select.setAttribute("aria-disabled", select.disabled ? "true" : "false");
        } catch (error) {}
      });

      queryAll("[data-project-team-member-save], [data-project-team-member-remove]", state.refs.card).forEach(function disableMemberAction(button) {
        try {
          var row = closest(button, "[data-project-team-member]");
          var role = normalizeProjectRole((row && row.getAttribute("data-role")) || button.getAttribute("data-current-role"), ROLE_VIEWER);
          var owner = role === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true");
          button.disabled = writeDisabled || owner || !positiveLocalUserId(button.getAttribute("data-user-id"));
          button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        } catch (error) {}
      });

      queryAll("[data-project-team-invitation-revoke]", state.refs.card).forEach(function disableInvitationAction(button) {
        try {
          var row = closest(button, "[data-project-team-invitation]");
          var status = lowerString(button.getAttribute("data-status") || (row && row.getAttribute("data-status")), "pending", 80);
          button.disabled = writeDisabled || !!INVITATION_TERMINAL_STATUSES[status] || !safeInvitationId(button.getAttribute("data-invitation-id"));
          button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        } catch (error) {}
      });

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_DISABLED, writeDisabled);
        state.refs.card.setAttribute("data-project-team-disabled", writeDisabled ? "true" : "false");
        state.refs.card.setAttribute("data-project-team-disabled-reason", reason || "");
        state.refs.card.setAttribute("data-project-role", state.projectRole || "");
        state.refs.card.setAttribute("data-project-team-can-read", canReadTeam() ? "true" : "false");
      }
      updateStatus();
    } catch (error) {}
  }

  function sanitizeMember(member) {
    try {
      var source = isObject(member) ? member : {};
      var user = isObject(source.user) ? source.user : {};
      var localUserId = positiveLocalUserId(source.user_id || source.userId || user.id || user.user_id || user.userId);
      var role = normalizeProjectRole(source.role || source.project_role || source.projectRole, ROLE_VIEWER);
      return {
        user_id: localUserId,
        userId: localUserId,
        role: role,
        status: lowerString(source.status, "active", 80),
        display_name: trimString(user.display_name || user.displayName || source.display_name || source.displayName || user.name || source.name || user.handle || source.handle, localUserId ? "User " + localUserId : "Unbekannter Benutzer", 200),
        email: trimString(user.email || source.email || source.user_email || source.userEmail, "", 320),
        permissions: isObject(source.permissions) ? sanitizeBrowserValue(source.permissions, 0) : {},
        can_view: toBooleanSafe(source.can_view !== undefined ? source.can_view : source.canView, role !== ""),
        can_edit: role === ROLE_OWNER || role === ROLE_ADMIN || role === ROLE_EDITOR,
        can_manage: role === ROLE_OWNER || role === ROLE_ADMIN,
        can_manage_team: role === ROLE_OWNER || role === ROLE_ADMIN,
        can_delete: role === ROLE_OWNER,
        can_embed: role === ROLE_OWNER || role === ROLE_ADMIN
      };
    } catch (error) {
      return null;
    }
  }

  function sanitizeInvitation(invitation) {
    try {
      var source = isObject(invitation) ? invitation : {};
      var id = safeInvitationId(source.public_id || source.publicId || source.invitation_id || source.invitationId || source.id);
      var role = normalizeAssignableRole(source.role, ROLE_VIEWER);
      return {
        public_id: id,
        publicId: id,
        email: trimString(source.email || source.invitee_email || source.inviteeEmail || source.email_normalized || source.emailNormalized, "", 320),
        role: role || ROLE_VIEWER,
        status: lowerString(source.status, "pending", 80),
        created_at: trimString(source.created_at || source.createdAt, "", 120),
        expires_at: trimString(source.expires_at || source.expiresAt, "", 120)
      };
    } catch (error) {
      return null;
    }
  }

  function extractItems(payload, preferredKey) {
    try {
      var data = isObject(payload) ? payload : {};
      var list = isArray(data[preferredKey]) ? data[preferredKey] :
        isArray(data.items) ? data.items :
          preferredKey === "members" && isArray(data.team) ? data.team :
            preferredKey === "members" && isArray(data.memberships) ? data.memberships :
              preferredKey === "invitations" && isArray(data.pending_invitations) ? data.pending_invitations :
                preferredKey === "invitations" && isArray(data.pendingInvitations) ? data.pendingInvitations : null;

      if (!list && isObject(data.data)) {
        return extractItems(data.data, preferredKey);
      }
      if (!list && isObject(data.payload)) {
        return extractItems(data.payload, preferredKey);
      }
      list = list || [];
      return list.map(preferredKey === "members" ? sanitizeMember : sanitizeInvitation).filter(Boolean);
    } catch (error) {
      return [];
    }
  }

  function memberUserId(member) {
    try {
      var m = isObject(member) ? member : {};
      var user = isObject(m.user) ? m.user : {};
      return positiveLocalUserId(m.user_id || m.userId || user.id || user.user_id || user.userId);
    } catch (error) {
      return "";
    }
  }

  function memberDisplayName(member) {
    var m = isObject(member) ? member : {};
    var id = memberUserId(m);
    return trimString(m.display_name || m.displayName || m.name || m.handle, id ? "User " + id : "Unbekannter Benutzer", 200);
  }

  function memberEmail(member) {
    var m = isObject(member) ? member : {};
    return trimString(m.email || m.user_email || m.userEmail, "", 320);
  }

  function memberRole(member) {
    var m = isObject(member) ? member : {};
    return normalizeProjectRole(m.role || m.project_role || m.projectRole, ROLE_VIEWER);
  }

  function memberStatus(member) {
    var m = isObject(member) ? member : {};
    return lowerString(m.status, "active", 80);
  }

  function appendPermissionChip(parent, label, extraClass) {
    append(parent, createElement("span", "vp-project-permission-chip" + (extraClass ? " " + extraClass : ""), label));
  }

  function createRoleSelect(userId, role) {
    var select = createElement("select", "vp-project-select vp-project-select--compact");
    if (!select) {
      return null;
    }
    var normalizedRole = normalizeProjectRole(role, ROLE_VIEWER);
    select.setAttribute("data-project-team-member-role", "");
    select.setAttribute("data-user-id", userId);
    select.setAttribute("data-current-role", normalizedRole);

    [[ROLE_VIEWER, "Viewer"], [ROLE_EDITOR, "Editor"], [ROLE_ADMIN, "Admin"]].forEach(function eachOption(pair) {
      var option = createElement("option", "", pair[1]);
      if (option) {
        option.value = pair[0];
        option.selected = pair[0] === normalizedRole;
        append(select, option);
      }
    });

    if (normalizedRole === ROLE_OWNER) {
      var ownerOption = createElement("option", "", "Owner");
      if (ownerOption) {
        ownerOption.value = ROLE_OWNER;
        ownerOption.selected = true;
        append(select, ownerOption);
      }
    }

    select.disabled = !canOperate() || normalizedRole === ROLE_OWNER || !positiveLocalUserId(userId);
    return select;
  }

  function renderMembers(members) {
    try {
      var container = state.refs.members;
      if (!container) {
        return;
      }
      removeChildren(container);
      var list = isArray(members) ? members : [];
      if (!list.length) {
        var empty = createElement("div", "vp-project-empty");
        append(empty, createElement("p", "", "Keine Projektmitglieder geladen."));
        append(empty, createElement("p", "vp-project-help", "Mitglieder werden ausschließlich für berechtigte Projektverwalter geladen."));
        append(container, empty);
        return;
      }

      list.forEach(function renderMember(member) {
        try {
          var userId = memberUserId(member);
          var role = memberRole(member);
          var status = memberStatus(member);
          var name = memberDisplayName(member);
          var email = memberEmail(member);
          var isOwner = role === ROLE_OWNER;
          var row = createElement("article", "vp-project-team-row" + (isOwner ? " vp-project-team-row--owner" : ""));
          if (!row) {
            return;
          }
          row.setAttribute("data-project-team-member", "");
          row.setAttribute("data-user-id", userId);
          row.setAttribute("data-role", role);
          row.setAttribute("data-status", status);
          row.setAttribute("data-owner", isOwner ? "true" : "false");

          var identity = createElement("div", "vp-project-team-row__identity");
          var avatar = createElement("div", "vp-project-avatar", name ? name.charAt(0).toUpperCase() : "?");
          if (avatar) {
            avatar.setAttribute("aria-hidden", "true");
          }
          var identityText = createElement("div");
          append(identityText, createElement("h4", "vp-project-team-row__name", name));
          append(identityText, createElement("p", "vp-project-team-row__meta", email || (userId ? "Lokale Benutzerreferenz " + userId : "Unbekannte Benutzerreferenz")));
          append(identity, avatar);
          append(identity, identityText);

          var roleBox = createElement("div", "vp-project-team-row__role");
          append(roleBox, createRoleSelect(userId, role));

          var permissions = createElement("div", "vp-project-team-row__permissions");
          if (permissions) {
            permissions.setAttribute("aria-label", "Effektive Rechte");
          }
          appendPermissionChip(permissions, "Ansehen");
          if (role === ROLE_OWNER || role === ROLE_ADMIN || role === ROLE_EDITOR) {
            appendPermissionChip(permissions, "Bearbeiten");
          }
          if (role === ROLE_OWNER || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Verwalten");
            appendPermissionChip(permissions, "Team");
          }
          if (role === ROLE_OWNER) {
            appendPermissionChip(permissions, "Löschen");
          }

          var actions = createElement("div", "vp-project-team-row__actions");
          var save = createElement("button", "vp-project-btn vp-project-btn--ghost", "Rolle speichern");
          if (save) {
            save.type = "button";
            save.setAttribute("data-project-team-member-save", "");
            save.setAttribute("data-user-id", userId);
            save.setAttribute("data-current-role", role);
            save.disabled = !canOperate() || isOwner || !userId;
            if (isOwner) {
              save.setAttribute("title", "Owner werden ausschließlich über die Eigentumsübertragung geändert.");
            }
          }
          var remove = createElement("button", "vp-project-btn vp-project-btn--danger", "Entfernen");
          if (remove) {
            remove.type = "button";
            remove.setAttribute("data-project-team-member-remove", "");
            remove.setAttribute("data-user-id", userId);
            remove.setAttribute("data-current-role", role);
            remove.disabled = !canOperate() || isOwner || !userId;
            if (isOwner) {
              remove.setAttribute("title", "Übertrage zuerst die Eigentümerschaft.");
            }
          }
          append(actions, save);
          append(actions, remove);

          append(row, identity);
          append(row, roleBox);
          append(row, permissions);
          append(row, actions);
          append(container, row);
        } catch (error) {}
      });
    } catch (error) {}
  }

  function invitationId(invitation) {
    var inv = isObject(invitation) ? invitation : {};
    return safeInvitationId(inv.public_id || inv.publicId || inv.invitation_id || inv.invitationId || inv.id);
  }

  function renderInvitations(invitations) {
    try {
      var container = state.refs.invitations;
      if (!container) {
        return;
      }
      removeChildren(container);
      var list = isArray(invitations) ? invitations : [];
      if (!list.length) {
        var empty = createElement("div", "vp-project-empty");
        append(empty, createElement("p", "", "Keine Einladungen vorhanden."));
        append(container, empty);
        return;
      }

      list.forEach(function renderInvitation(invitation) {
        try {
          var inv = sanitizeInvitation(invitation) || {};
          var id = invitationId(inv);
          var email = trimString(inv.email, "", 320);
          var role = normalizeAssignableRole(inv.role, ROLE_VIEWER) || ROLE_VIEWER;
          var status = lowerString(inv.status, "pending", 80);
          var row = createElement("article", "vp-project-team-row vp-project-team-row--invitation");
          if (!row) {
            return;
          }
          row.setAttribute("data-project-team-invitation", "");
          row.setAttribute("data-invitation-id", id);
          row.setAttribute("data-role", role);
          row.setAttribute("data-status", status);

          var identity = createElement("div", "vp-project-team-row__identity");
          var avatar = createElement("div", "vp-project-avatar vp-project-avatar--pending", "@");
          if (avatar) {
            avatar.setAttribute("aria-hidden", "true");
          }
          var identityText = createElement("div");
          append(identityText, createElement("h4", "vp-project-team-row__name", email || "Ausstehende Einladung"));
          var meta = "Status: " + statusLabel(status);
          if (inv.created_at) {
            meta += " · erstellt " + inv.created_at;
          }
          if (inv.expires_at) {
            meta += " · gültig bis " + inv.expires_at;
          }
          append(identityText, createElement("p", "vp-project-team-row__meta", meta));
          append(identity, avatar);
          append(identity, identityText);

          var roleBox = createElement("div", "vp-project-team-row__role");
          append(roleBox, createElement("span", "vp-project-role-chip", roleLabel(role)));
          var permissions = createElement("div", "vp-project-team-row__permissions");
          appendPermissionChip(permissions, "wartet auf Annahme", "vp-project-permission-chip--pending");
          var actions = createElement("div", "vp-project-team-row__actions");
          var revoke = createElement("button", "vp-project-btn vp-project-btn--danger", "Widerrufen");
          if (revoke) {
            revoke.type = "button";
            revoke.setAttribute("data-project-team-invitation-revoke", "");
            revoke.setAttribute("data-invitation-id", id);
            revoke.setAttribute("data-status", status);
            revoke.disabled = !canOperate() || !!INVITATION_TERMINAL_STATUSES[status] || !id;
          }
          append(actions, revoke);
          append(row, identity);
          append(row, roleBox);
          append(row, permissions);
          append(row, actions);
          append(container, row);
        } catch (error) {}
      });
    } catch (error) {}
  }

  function renderAll() {
    renderMembers(state.members);
    renderInvitations(state.invitations);
    updateDisabledState();
  }

  function updateFromListResponses(memberPayload, invitationPayload) {
    try {
      if (memberPayload) {
        state.members = extractItems(memberPayload, "members");
        applyChunkAccessSync(memberPayload, { silent: true });
      }
      if (invitationPayload) {
        state.invitations = extractItems(invitationPayload, "invitations");
      }
      renderAll();
      return { members: state.members, invitations: state.invitations };
    } catch (error) {
      return { members: state.members, invitations: state.invitations };
    }
  }

  function parseInitialData() {
    try {
      var data = {};
      if (state.refs.initialJson) {
        data = safeJsonParse(state.refs.initialJson.textContent || "", {});
      }
      if (!isObject(data)) {
        data = {};
      }
      var project = state.config && isObject(state.config.project) ? state.config.project : {};
      return {
        members: extractItems({ members: isArray(data.members) ? data.members : isArray(project.members) ? project.members : [] }, "members"),
        invitations: extractItems({ invitations: isArray(data.invitations) ? data.invitations : isArray(project.invitations) ? project.invitations : [] }, "invitations"),
        chunkAccessSync: extractChunkAccessSync(data)
      };
    } catch (error) {
      return { members: [], invitations: [], chunkAccessSync: extractChunkAccessSync({}) };
    }
  }

  function isAllowedTeamRequestTarget(target, method) {
    try {
      var normalizedMethod = trimString(method, "GET", 12).toUpperCase();
      var membersBase = normalizeCollectionUrl(state.membersUrl, state.projectPublicId, "members");
      var invitationsBase = normalizeCollectionUrl(state.invitationsUrl, state.projectPublicId, "invitations");

      if (!target || (!membersBase && !invitationsBase)) {
        return false;
      }
      if (normalizedMethod === "GET") {
        return target === membersBase || target === invitationsBase;
      }
      if (normalizedMethod === "POST") {
        return target === invitationsBase;
      }
      if (normalizedMethod === "PATCH") {
        if (!membersBase || target.indexOf(membersBase + "/") !== 0) {
          return false;
        }
        return !!positiveLocalUserId(decodeURIComponent(target.slice((membersBase + "/").length)));
      }
      if (normalizedMethod === "DELETE") {
        if (membersBase && target.indexOf(membersBase + "/") === 0) {
          return !!positiveLocalUserId(decodeURIComponent(target.slice((membersBase + "/").length)));
        }
        if (invitationsBase && target.indexOf(invitationsBase + "/") === 0) {
          return !!safeInvitationId(decodeURIComponent(target.slice((invitationsBase + "/").length)));
        }
      }
      return false;
    } catch (error) {
      return false;
    }
  }

  async function requestJson(url, options) {
    var opts = isObject(options) ? options : {};
    var method = trimString(opts.method, "GET", 12).toUpperCase();
    var allowedMethods = { GET: true, POST: true, PATCH: true, DELETE: true };
    var target = trimString(url, "", 2000);

    if (!allowedMethods[method]) {
      throw new Error("request method not allowed");
    }
    if (!target || target.charAt(0) !== "/" || target.indexOf("//") === 0) {
      throw new Error("unsafe request URL");
    }
    if (!isAllowedTeamRequestTarget(target, method)) {
      var targetError = new Error("team API target not allowed");
      targetError.code = "team_api_target_not_allowed";
      targetError.status = 400;
      throw targetError;
    }

    var requestId = generateRequestId();
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timeoutId = null;
    try {
      if (controller) {
        timeoutId = setTimeout(function abortRequest() {
          try { controller.abort(); } catch (error) {}
        }, REQUEST_TIMEOUT_MS);
      }

      var response = await fetch(target, {
        method: method,
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-Requested-With": "fetch",
          "X-VECTOPLAN-Client": "project_team.js",
          "X-Request-ID": requestId,
          "X-Correlation-ID": requestId
        },
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: controller ? controller.signal : undefined,
        body: opts.body !== undefined ? opts.body : undefined
      });

      var contentLength = Number(response.headers && response.headers.get ? response.headers.get("Content-Length") : 0) || 0;
      if (contentLength > MAX_RESPONSE_BYTES) {
        var sizeError = new Error("response too large");
        sizeError.code = "response_too_large";
        sizeError.status = 502;
        throw sizeError;
      }

      var text = await response.text();
      if (text.length > MAX_RESPONSE_BYTES) {
        var bodySizeError = new Error("response too large");
        bodySizeError.code = "response_too_large";
        bodySizeError.status = 502;
        throw bodySizeError;
      }
      var data = safeJsonParse(text, null);
      if (!response.ok) {
        var error = new Error(data && (data.error || data.message) ? data.error || data.message : "Request failed with status " + response.status);
        error.status = response.status;
        error.statusCode = response.status;
        error.payload = sanitizeBrowserValue(data, 0);
        error.code = data && data.code ? data.code : "request_failed";
        throw error;
      }
      return data === null ? {} : data;
    } catch (error) {
      if (error && error.name === "AbortError") {
        var timeoutError = new Error("Request timeout");
        timeoutError.code = "request_timeout";
        timeoutError.status = 504;
        throw timeoutError;
      }
      throw error;
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }
  }

  async function refreshMembers(options) {
    var opts = isObject(options) ? options : {};
    try {
      if (!canReadTeam()) {
        if (!opts.silent) {
          setMessage("warning", disabledReason());
        }
        return false;
      }
      setLoading(true);
      var payload = await requestJson(state.membersUrl, { method: "GET" });
      updateFromListResponses(payload, null);
      if (!opts.silent) {
        setMessage("success", "Mitglieder wurden aktualisiert.");
      }
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Mitglieder konnten nicht geladen werden.");
      emitError(normalized, { area: "members" });
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function refreshInvitations(options) {
    var opts = isObject(options) ? options : {};
    try {
      if (!canReadTeam()) {
        if (!opts.silent) {
          setMessage("warning", disabledReason());
        }
        return false;
      }
      setLoading(true);
      var payload = await requestJson(state.invitationsUrl, { method: "GET" });
      updateFromListResponses(null, payload);
      if (!opts.silent) {
        setMessage("success", "Einladungen wurden aktualisiert.");
      }
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Einladungen konnten nicht geladen werden.");
      emitError(normalized, { area: "invitations" });
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function refreshTeam(options) {
    var opts = isObject(options) ? options : {};
    try {
      if (!canReadTeam()) {
        if (!opts.silent) {
          setMessage("warning", disabledReason());
        }
        return false;
      }
      setLoading(true);
      var results = await Promise.all([
        requestJson(state.membersUrl, { method: "GET" }),
        requestJson(state.invitationsUrl, { method: "GET" })
      ]);
      updateFromListResponses(results[0], results[1]);
      if (!opts.silent) {
        setMessage("success", "Teamdaten wurden aktualisiert.");
      }
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Teamdaten konnten nicht geladen werden.");
      emitError(normalized, { area: "team" });
      return false;
    } finally {
      setLoading(false);
    }
  }

  function validateEmail(email) {
    var text = lowerString(email, "", 320);
    return !!text && text.length <= 320 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text);
  }

  function responseSummary(response) {
    try {
      var data = isObject(response) ? response : {};
      return {
        ok: data.ok !== false,
        code: trimString(data.code, "", 160),
        statusCode: Number(data.status_code || data.statusCode) || 200,
        requestId: trimString(data.request_id || data.requestId, "", 200),
        chunkAccessSync: extractChunkAccessSync(data)
      };
    } catch (error) {
      return { ok: false, code: "", statusCode: 0, requestId: "", chunkAccessSync: extractChunkAccessSync({}) };
    }
  }

  async function inviteByEmail() {
    try {
      if (!canOperate()) {
        setMessage("warning", disabledReason());
        return false;
      }
      var email = lowerString(state.refs.inviteEmail && state.refs.inviteEmail.value, "", 320);
      var selectedRole = normalizeProjectRole(state.refs.inviteRole && state.refs.inviteRole.value, "");
      var role = normalizeAssignableRole(selectedRole, "");
      if (!validateEmail(email)) {
        setMessage("error", "Bitte gib eine gültige E-Mail-Adresse ein.");
        if (state.refs.inviteEmail && typeof state.refs.inviteEmail.focus === "function") {
          try { state.refs.inviteEmail.focus(); } catch (error) {}
        }
        return false;
      }
      if (!role) {
        setMessage("error", selectedRole === ROLE_OWNER ? "Owner kann nicht per Einladung vergeben werden." : "Ungültige Einladungsrolle.");
        return false;
      }

      setSaving(true);
      setMessage("info", "Einladung wird geprüft und erstellt…");
      var response = await requestJson(state.invitationsUrl, {
        method: "POST",
        body: safeJsonStringify({ email: email, role: role })
      });
      if (!response || response.ok === false) {
        var inviteError = new Error(response && (response.error || response.message) ? response.error || response.message : "Einladung konnte nicht erstellt werden.");
        inviteError.payload = response;
        throw inviteError;
      }
      if (state.refs.inviteEmail) {
        state.refs.inviteEmail.value = "";
      }
      state.lastResponse = responseSummary(response);
      state.lastError = null;
      setMessage("success", "Einladung wurde erstellt.");
      emitChangeEvent(EVENT_INVITATION_CREATED, { action: "invitation_created", role: role, requestId: state.lastResponse.requestId });
      await refreshTeam({ silent: true });
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Einladung konnte nicht erstellt werden.");
      emitError(normalized, { area: "invite" });
      return false;
    } finally {
      setSaving(false);
    }
  }

  function escapeCssValue(value) {
    try {
      return String(value).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
    } catch (error) {
      return "";
    }
  }

  function roleForUserId(userId) {
    var uid = positiveLocalUserId(userId);
    if (!uid) {
      return "";
    }
    var select = query("[data-project-team-member-role][data-user-id='" + escapeCssValue(uid) + "']", state.refs.card);
    return normalizeProjectRole(select && select.value, "");
  }

  async function saveMemberRole(userId) {
    try {
      var uid = positiveLocalUserId(userId);
      if (!uid) {
        setMessage("error", "Gültige lokale User-ID fehlt.");
        return false;
      }
      if (!canOperate()) {
        setMessage("warning", disabledReason());
        return false;
      }
      var row = query("[data-project-team-member][data-user-id='" + escapeCssValue(uid) + "']", state.refs.card);
      var currentRole = normalizeProjectRole(row && row.getAttribute("data-role"), ROLE_VIEWER);
      if (currentRole === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true")) {
        setMessage("warning", "Owner werden ausschließlich über die Eigentumsübertragung geändert.");
        return false;
      }
      var selectedRole = roleForUserId(uid);
      var role = normalizeAssignableRole(selectedRole, "");
      if (!role) {
        setMessage("error", selectedRole === ROLE_OWNER ? "Owner kann nicht über diese UI gesetzt werden." : "Ungültige Rolle.");
        return false;
      }

      var url = memberUrl(uid);
      if (!url) {
        setMessage("error", "Sicherer Mitglieder-Endpunkt fehlt.");
        return false;
      }
      setSaving(true);
      setMessage("info", "Rolle wird gespeichert…");
      var response = await requestJson(url, { method: "PATCH", body: safeJsonStringify({ role: role }) });
      if (!response || response.ok === false) {
        var roleError = new Error(response && (response.error || response.message) ? response.error || response.message : "Rolle konnte nicht gespeichert werden.");
        roleError.payload = response;
        throw roleError;
      }
      state.lastResponse = responseSummary(response);
      state.lastError = null;
      applyChunkAccessSync(response, { silent: false });
      setMessage(state.chunkAccessSync.repairRequired ? "warning" : "success", mutationSuccessMessage("Rolle wurde gespeichert."));
      emitChangeEvent(EVENT_MEMBER_CHANGED, {
        action: "member_role_changed",
        targetUserId: uid,
        role: role,
        requestId: state.lastResponse.requestId
      });
      await refreshTeam({ silent: true });
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Rolle konnte nicht gespeichert werden.");
      emitError(normalized, { area: "member_role" });
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function removeMember(userId) {
    try {
      var uid = positiveLocalUserId(userId);
      if (!uid) {
        setMessage("error", "Gültige lokale User-ID fehlt.");
        return false;
      }
      if (!canOperate()) {
        setMessage("warning", disabledReason());
        return false;
      }
      var row = query("[data-project-team-member][data-user-id='" + escapeCssValue(uid) + "']", state.refs.card);
      var role = normalizeProjectRole(row && row.getAttribute("data-role"), ROLE_VIEWER);
      if (role === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true")) {
        setMessage("warning", "Übertrage zuerst die Eigentümerschaft.");
        return false;
      }

      var confirmed = true;
      try { confirmed = window.confirm("Mitglied aus diesem Projekt entfernen?"); } catch (error) { confirmed = true; }
      if (!confirmed) {
        return false;
      }

      var url = memberUrl(uid);
      if (!url) {
        setMessage("error", "Sicherer Mitglieder-Endpunkt fehlt.");
        return false;
      }
      setSaving(true);
      setMessage("info", "Mitglied wird entfernt…");
      var response = await requestJson(url, { method: "DELETE" });
      if (!response || response.ok === false) {
        var removeError = new Error(response && (response.error || response.message) ? response.error || response.message : "Mitglied konnte nicht entfernt werden.");
        removeError.payload = response;
        throw removeError;
      }
      state.lastResponse = responseSummary(response);
      state.lastError = null;
      applyChunkAccessSync(response, { silent: false });
      setMessage(state.chunkAccessSync.repairRequired ? "warning" : "success", mutationSuccessMessage("Mitglied wurde entfernt."));
      emitChangeEvent(EVENT_MEMBER_REMOVED, {
        action: "member_removed",
        targetUserId: uid,
        removed: true,
        requestId: state.lastResponse.requestId
      });
      await refreshTeam({ silent: true });
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Mitglied konnte nicht entfernt werden.");
      emitError(normalized, { area: "member_remove" });
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function revokeInvitation(invitationIdValue) {
    try {
      var id = safeInvitationId(invitationIdValue);
      if (!id) {
        setMessage("error", "Gültige Einladungs-ID fehlt.");
        return false;
      }
      if (!canOperate()) {
        setMessage("warning", disabledReason());
        return false;
      }
      var row = query("[data-project-team-invitation][data-invitation-id='" + escapeCssValue(id) + "']", state.refs.card);
      var status = lowerString(row && row.getAttribute("data-status"), "pending", 80);
      if (INVITATION_TERMINAL_STATUSES[status]) {
        setMessage("warning", "Diese Einladung ist bereits abgeschlossen.");
        return false;
      }
      var url = invitationUrl(id);
      if (!url) {
        setMessage("error", "Sicherer Einladungs-Endpunkt fehlt.");
        return false;
      }
      setSaving(true);
      setMessage("info", "Einladung wird widerrufen…");
      var response = await requestJson(url, { method: "DELETE" });
      if (!response || response.ok === false) {
        var revokeError = new Error(response && (response.error || response.message) ? response.error || response.message : "Einladung konnte nicht widerrufen werden.");
        revokeError.payload = response;
        throw revokeError;
      }
      state.lastResponse = responseSummary(response);
      state.lastError = null;
      setMessage("success", "Einladung wurde widerrufen.");
      emitChangeEvent(EVENT_INVITATION_REVOKED, {
        action: "invitation_revoked",
        invitationId: id,
        revoked: true,
        requestId: state.lastResponse.requestId
      });
      await refreshTeam({ silent: true });
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Einladung konnte nicht widerrufen werden.");
      emitError(normalized, { area: "invitation_revoke" });
      return false;
    } finally {
      setSaving(false);
    }
  }

  function emitLocal(type, detail) {
    try {
      var root = state.refs && state.refs.root;
      if (!root || typeof CustomEvent !== "function") {
        return false;
      }
      root.dispatchEvent(new CustomEvent(type, { bubbles: true, cancelable: false, detail: sanitizeBrowserValue(detail || {}, 0) }));
      return true;
    } catch (error) {
      return false;
    }
  }

  function trustedParentOrigin() {
    return currentOrigin();
  }

  function postParentMessage(type, detail) {
    try {
      var win = getWindow();
      var origin = trustedParentOrigin();
      if (!origin || !win.parent || win.parent === win || typeof win.parent.postMessage !== "function") {
        return false;
      }
      win.parent.postMessage({
        type: type,
        kind: type,
        source: "vectoplan-app.project-team",
        version: INTERNAL_VERSION,
        detail: sanitizeBrowserValue(detail || {}, 0),
        ts: Date.now()
      }, origin);
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

  function eventCounts() {
    return {
      memberCount: state.members.length,
      invitationCount: state.invitations.length,
      pendingInvitationCount: state.invitations.filter(function pending(invitation) {
        return !INVITATION_TERMINAL_STATUSES[lowerString(invitation && invitation.status, "pending", 80)];
      }).length
    };
  }

  function emitChangeEvent(type, detail) {
    try {
      var eventType = type || EVENT_TEAM_CHANGED;
      var payloadDetail = sanitizeBrowserValue(detail || {}, 0) || {};
      payloadDetail.projectPublicId = state.projectPublicId;
      payloadDetail.projectRole = state.projectRole;
      payloadDetail.canManageTeam = state.canManageTeam;
      payloadDetail.counts = eventCounts();
      payloadDetail.chunkAccessSync = accessSyncSummary();

      emitLocal(eventType, payloadDetail);
      if (eventType !== EVENT_TEAM_CHANGED) {
        emitLocal(EVENT_TEAM_CHANGED, payloadDetail);
      }
      postParentMessage(eventType, payloadDetail);
      dispatchParentEvent(eventType, payloadDetail);
      if (eventType !== EVENT_TEAM_CHANGED) {
        dispatchParentEvent(EVENT_TEAM_CHANGED, payloadDetail);
      }

      try {
        var win = getWindow();
        if (win.parent && win.parent !== win && win.parent.dispatchEvent && typeof win.parent.Event === "function") {
          win.parent.dispatchEvent(new win.parent.Event(EVENT_SIDEBAR_REFRESH));
        }
      } catch (error) {}
      return true;
    } catch (error) {
      return false;
    }
  }

  function emitError(error, extra) {
    try {
      var normalized = normalizeError(error);
      var payload = {
        error: {
          name: normalized.name,
          message: normalized.message,
          code: normalized.code,
          status: normalized.status,
          requestId: normalized.requestId
        },
        area: extra && extra.area ? trimString(extra.area, "team", 80) : "team",
        projectPublicId: state.projectPublicId
      };
      emitLocal(EVENT_ERROR, payload);
      postParentMessage(EVENT_ERROR, payload);
      dispatchParentEvent(EVENT_ERROR, payload);
      if (state.refs.card) {
        state.refs.card.classList.add(CLASS_ERROR);
      }
      return true;
    } catch (innerError) {
      return false;
    }
  }

  function isTrustedMessageEvent(event) {
    try {
      var win = getWindow();
      return !!event && event.source === win.parent && event.origin === currentOrigin();
    } catch (error) {
      return false;
    }
  }

  function updateAfterProjectSaved(detail) {
    try {
      var project = isObject(detail && detail.project) ? detail.project : {};
      var publicId = pickString([
        project.public_id,
        project.publicId,
        project.project_public_id,
        project.projectPublicId,
        detail && detail.projectPublicId,
        detail && detail.project_public_id
      ], "");
      if (!validProjectPublicId(publicId)) {
        return false;
      }
      state.projectPublicId = publicId;
      state.projectId = trimString(project.id || project.project_id || project.projectId || state.projectId, "", 80);
      state.isNew = false;
      state.membersUrl = state.canManage && state.persistent ? normalizeCollectionUrl("", publicId, "members") : "";
      state.invitationsUrl = state.canManage && state.persistent ? normalizeCollectionUrl("", publicId, "invitations") : "";
      state.canReadTeam = !!(MANAGER_ROLES[state.projectRole] && state.canManage && state.persistent && state.membersUrl && state.invitationsUrl);
      state.canWrite = state.canReadTeam && state.canManageTeam;
      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-public-id", publicId);
        state.refs.card.setAttribute("data-project-is-new", "false");
        state.refs.card.setAttribute("data-members-url", state.membersUrl);
        state.refs.card.setAttribute("data-invitations-url", state.invitationsUrl);
      }
      applyChunkAccessSync(detail, { silent: true });
      updateDisabledState();
      return true;
    } catch (error) {
      return false;
    }
  }

  function onMessage(event) {
    try {
      if (!isTrustedMessageEvent(event)) {
        return;
      }
      var data = event && event.data;
      if (!isObject(data)) {
        return;
      }
      var type = trimString(data.type || data.kind, "", 200);
      if (type === "vectoplan:project:saved" || type === "vectoplan:project:created") {
        updateAfterProjectSaved(isObject(data.detail) ? data.detail : data);
      }
    } catch (error) {}
  }

  function onCardClick(event) {
    try {
      var target = event && event.target ? event.target : null;
      var inviteButton = closest(target, "[data-project-team-invite-submit]");
      if (inviteButton) {
        event.preventDefault();
        void inviteByEmail();
        return;
      }
      var refreshButton = closest(target, "[data-project-team-refresh]");
      if (refreshButton) {
        event.preventDefault();
        void refreshTeam();
        return;
      }
      var membersRefreshButton = closest(target, "[data-project-team-members-refresh]");
      if (membersRefreshButton) {
        event.preventDefault();
        void refreshMembers();
        return;
      }
      var invitationsRefreshButton = closest(target, "[data-project-team-invitations-refresh]");
      if (invitationsRefreshButton) {
        event.preventDefault();
        void refreshInvitations();
        return;
      }
      var saveButton = closest(target, "[data-project-team-member-save]");
      if (saveButton) {
        event.preventDefault();
        void saveMemberRole(saveButton.getAttribute("data-user-id"));
        return;
      }
      var removeButton = closest(target, "[data-project-team-member-remove]");
      if (removeButton) {
        event.preventDefault();
        void removeMember(removeButton.getAttribute("data-user-id"));
        return;
      }
      var revokeButton = closest(target, "[data-project-team-invitation-revoke]");
      if (revokeButton) {
        event.preventDefault();
        void revokeInvitation(revokeButton.getAttribute("data-invitation-id"));
      }
    } catch (error) {}
  }

  function onInviteKeydown(event) {
    try {
      if (event && event.key === "Enter") {
        event.preventDefault();
        void inviteByEmail();
      }
    } catch (error) {}
  }

  function wireEvents() {
    addListener(state.refs.card, "click", onCardClick);
    addListener(state.refs.inviteEmail, "keydown", onInviteKeydown);
    addListener(getWindow(), "message", onMessage);
  }

  function initStateFromConfig(initialData) {
    state.projectPublicId = state.config.projectPublicId;
    state.projectId = state.config.projectId;
    state.projectRole = state.config.projectRole;
    state.membersUrl = state.config.membersUrl;
    state.invitationsUrl = state.config.invitationsUrl;
    state.isNew = state.config.isNew;
    state.canEdit = state.config.canEdit;
    state.canManage = state.config.canManage;
    state.canManageTeam = state.config.canManageTeam;
    state.canReadTeam = state.config.canReadTeam;
    state.canWrite = state.config.canWrite;
    state.demoMode = state.config.demoMode;
    state.persistent = state.config.persistent;
    state.publicViewer = state.config.publicViewer;
    state.readOnly = state.config.readOnly;
    state.authenticated = state.config.authenticated;
    state.authUnavailable = state.config.authUnavailable;
    state.userBlocked = state.config.userBlocked;
    state.accessBlocked = state.config.accessBlocked;
    state.identityConsistent = state.config.identityConsistent;
    state.localLinkState = state.config.localLinkState;
    state.disabledReason = state.config.disabledReason;
    state.members = isArray(initialData.members) ? initialData.members : [];
    state.invitations = isArray(initialData.invitations) ? initialData.invitations : [];
    state.chunkAccessSync = initialData.chunkAccessSync && initialData.chunkAccessSync.status !== "unknown" ? initialData.chunkAccessSync : state.config.chunkAccessSync;

    if (!MANAGER_ROLES[state.projectRole] || state.publicViewer || state.readOnly || state.demoMode || !state.persistent || !state.identityConsistent) {
      state.canManage = false;
      state.canManageTeam = false;
      state.canReadTeam = false;
      state.canWrite = false;
      state.membersUrl = "";
      state.invitationsUrl = "";
    }

    if (state.refs.card) {
      state.refs.card.setAttribute("data-project-public-id", state.projectPublicId || "");
      state.refs.card.setAttribute("data-project-id", state.projectId || "");
      state.refs.card.setAttribute("data-members-url", state.membersUrl || "");
      state.refs.card.setAttribute("data-invitations-url", state.invitationsUrl || "");
      state.refs.card.setAttribute("data-project-team-version", String(INTERNAL_VERSION));
      state.refs.card.setAttribute("data-project-role", state.projectRole || "");
    }
  }

  function maskEmail(email) {
    var value = trimString(email, "", 320);
    var parts = value.split("@");
    if (parts.length !== 2) {
      return "";
    }
    var local = parts[0];
    var masked = local.length <= 2 ? local.charAt(0) + "*" : local.charAt(0) + "***" + local.charAt(local.length - 1);
    return masked + "@" + parts[1];
  }

  function publicMemberSnapshot(member) {
    return {
      userId: memberUserId(member),
      displayName: memberDisplayName(member),
      emailMasked: maskEmail(memberEmail(member)),
      role: memberRole(member),
      status: memberStatus(member)
    };
  }

  function publicInvitationSnapshot(invitation) {
    var inv = sanitizeInvitation(invitation) || {};
    return {
      invitationId: invitationId(inv),
      emailMasked: maskEmail(inv.email),
      role: inv.role || ROLE_VIEWER,
      status: inv.status || "pending",
      createdAt: inv.created_at || "",
      expiresAt: inv.expires_at || ""
    };
  }

  function getSnapshot() {
    try {
      return {
        version: INTERNAL_VERSION,
        initialized: state.initialized,
        destroyed: state.destroyed,
        isLoading: state.isLoading,
        isSaving: state.isSaving,
        projectRole: state.projectRole,
        canEdit: state.canEdit,
        canManage: state.canManage,
        canManageTeam: state.canManageTeam,
        canReadTeam: canReadTeam(),
        canWrite: canOperate(),
        disabledReason: disabledReason(),
        isNew: state.isNew,
        demoMode: state.demoMode,
        persistent: state.persistent,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        authenticated: state.authenticated,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        identityConsistent: state.identityConsistent,
        localLinkState: state.localLinkState,
        projectPublicId: state.projectPublicId,
        membersUrl: state.membersUrl,
        invitationsUrl: state.invitationsUrl,
        counts: eventCounts(),
        members: state.members.map(publicMemberSnapshot),
        invitations: state.invitations.map(publicInvitationSnapshot),
        chunkAccessSync: accessSyncSummary(),
        lastResponse: safeClone(state.lastResponse),
        lastError: state.lastError ? {
          name: state.lastError.name,
          message: state.lastError.message,
          code: state.lastError.code,
          status: state.lastError.status,
          requestId: state.lastError.requestId
        } : null
      };
    } catch (error) {
      return { version: INTERNAL_VERSION, initialized: false, error: normalizeError(error) };
    }
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
      var initialData = parseInitialData();
      initStateFromConfig(initialData);
      renderAll();
      wireEvents();
      updateDisabledState();
      state.initialized = true;

      emitLocal(state.config.parentEvents.ready || EVENT_READY, {
        projectPublicId: state.projectPublicId,
        projectRole: state.projectRole,
        canManage: state.canManage,
        canManageTeam: state.canManageTeam,
        canReadTeam: canReadTeam(),
        canWrite: canOperate(),
        counts: eventCounts(),
        chunkAccessSync: accessSyncSummary()
      });

      try {
        var win = getWindow();
        if (win.__VECTOPLAN_DEBUG_ENABLED__ === true || win.__VECTOPLAN_DEBUG__ === true) {
          win.__VECTOPLAN_PROJECT_TEAM_STATE__ = getSnapshot();
        }
      } catch (error) {}
      return state;
    } catch (error) {
      state.lastError = normalizeError(error);
      if (state.refs.card) {
        state.refs.card.classList.add(CLASS_ERROR);
      }
      setMessage("error", "Teamverwaltung konnte nicht initialisiert werden.");
      return state;
    }
  }

  function destroy() {
    removeAllListeners();
    state.destroyed = true;
    state.initialized = false;
    return true;
  }

  var api = {
    version: INTERNAL_VERSION,
    init: init,
    destroy: destroy,
    refresh: refreshTeam,
    refreshMembers: refreshMembers,
    refreshInvitations: refreshInvitations,
    invite: inviteByEmail,
    saveMemberRole: saveMemberRole,
    removeMember: removeMember,
    revokeInvitation: revokeInvitation,
    renderAll: renderAll,
    getSnapshot: getSnapshot,
    setMessage: setMessage,
    canRead: canReadTeam,
    canOperate: canOperate,
    disabledReason: disabledReason,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      requestJson: requestJson,
      normalizeError: normalizeError,
      normalizeProjectRole: normalizeProjectRole,
      normalizeAssignableRole: normalizeAssignableRole,
      normalizeCollectionUrl: normalizeCollectionUrl,
      memberUrl: memberUrl,
      invitationUrl: invitationUrl,
      memberUserId: memberUserId,
      sanitizeMember: sanitizeMember,
      sanitizeInvitation: sanitizeInvitation,
      sanitizeBrowserValue: sanitizeBrowserValue,
      extractItems: extractItems,
      extractChunkAccessSync: extractChunkAccessSync,
      applyChunkAccessSync: applyChunkAccessSync,
      renderMembers: renderMembers,
      renderInvitations: renderInvitations,
      updateAfterProjectSaved: updateAfterProjectSaved,
      isTrustedMessageEvent: isTrustedMessageEvent,
      responseSummary: responseSummary,
      isAllowedTeamRequestTarget: isAllowedTeamRequestTarget
    }
  };

  try {
    global[EXPORT_NAME] = api;
    if (global.__VECTOPLAN_DEBUG_ENABLED__ === true || global.__VECTOPLAN_DEBUG__ === true) {
      if (!isObject(global.__VECTOPLAN_DEBUG__)) {
        global.__VECTOPLAN_DEBUG__ = {};
      }
      global.__VECTOPLAN_DEBUG__.projectTeam = api;
    }
  } catch (error) {}

  try {
    var doc = getDocument();
    if (doc && doc.readyState === "loading") {
      doc.addEventListener("DOMContentLoaded", function onReady() { init(); }, { once: true });
    } else {
      init();
    }
  } catch (error) {
    init();
  }
})(window);
