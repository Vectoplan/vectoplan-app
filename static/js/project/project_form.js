/* services/vectoplan-app/static/js/project/project_form.js */

/*
  VECTOPLAN Project Form

  Zweck:
  - Steuert das Projektformular im Workspace-iframe.
  - Erstellt neue Projekte über POST /v1/projects.
  - Aktualisiert bestehende Projekte über PATCH /v1/projects/<public_id>.
  - Leitet nach Erstellung die Parent-Shell auf /project=<public_id> weiter.
  - Informiert nach Speichern Parent-Shell, Sidebar, Workspace-Gating und Publication-UI.
  - Verwaltet nur App-Projektmetadaten.
  - Kein direkter Zugriff auf Chunk-, Editor-, Map-, 2D- oder LV-Fachdaten.

  Gesendet werden nur:
  - name
  - title
  - description
  - address_text
  - address.text
  - visibility

  Nicht gesendet werden:
  - street
  - house_number
  - postal_code
  - city
  - region
  - country
  - latitude
  - longitude
  - coordinate_srid
  - is_public
  - publication / published_workspaces
  - members / invitations
  - chunk/editor/map/2d/lv/system refs

  Regeln:
  - Public Viewer ist strikt read-only und darf nie POST/PATCH senden.
  - Read-only ist strikt read-only und darf nie POST/PATCH senden.
  - Auth-unavailable, user-blocked und access-blocked senden nie POST/PATCH.
  - Demo-Modus darf nur dann temporär arbeiten, wenn Backend/Context canEdit erlaubt.
  - Demo-Modus wird nie mit Public Viewer vermischt.
  - visibility steuert nur private/unlisted/public.
  - is_public wird serverseitig aus visibility abgeleitet.
  - Veröffentlichte Reiter werden separat über project_publication.js gespeichert.
  - Team/Einladungen werden separat über project_team.js gespeichert.
  - Frontend-Gating ist nur UX. Backend bleibt die Wahrheit.
  - Mutation-Requests und Redirects bleiben strikt same-origin.
  - Ein bereits persistiertes App-Projekt bleibt auch bei Chunk-/Access-Sync-Fehlern erhalten.
  - Browser-Events enthalten nur redigierte Projekt- und Statusdaten.

  Erwartetes Template:
  - services/vectoplan-app/templates/viewer/project.html

  Erwartete globale Config:
  - window.VECTOPLAN_PROJECT_WORKSPACE_CONFIG
  - window.PROJECT_WORKSPACE_CONFIG

  Wichtig:
  - Diese Datei ist bewusst ohne ES-Module gebaut.
  - Läuft robust auch, wenn Parent-Fenster nicht erreichbar ist.
  - Jede kritische Aktion ist defensiv mit try/catch gekapselt.
*/

(function initVectoplanProjectForm(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectForm";
  var LEGACY_EXPORT_NAME = "__VECTOPLAN_PROJECT_FORM__";
  var INTERNAL_VERSION = 6;

  var DEFAULT_CREATE_PATH = "/v1/projects";
  var DEFAULT_PROJECT_NEW_URL = "/project=new";
  var DEFAULT_PROJECT_ROOT_URL = "/";
  var DEFAULT_PROJECT_CONTEXT_NEW = "/ui/project/new/context.json";

  var FORM_SELECTOR = "[data-project-form]";
  var ROOT_SELECTOR = "[data-project-workspace]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var CLASS_LOADING = "is-loading";
  var CLASS_SAVING = "is-saving";
  var CLASS_SAVED = "is-saved";
  var CLASS_DIRTY = "is-dirty";
  var CLASS_ERROR = "is-error";
  var CLASS_READONLY = "is-readonly";
  var CLASS_INVALID = "is-invalid";
  var CLASS_SELECTED = "is-selected";
  var CLASS_DISABLED = "is-disabled";

  var FIELD_ERROR_CLASS = "vp-project-field__error";

  var EVENT_READY = "vectoplan:project-form:ready";
  var EVENT_SAVED = "vectoplan:project:saved";
  var EVENT_CREATED = "vectoplan:project:created";
  var EVENT_UPDATED = "vectoplan:project:updated";
  var EVENT_CONFIGURED = "vectoplan:project:configured";
  var EVENT_DIRTY = "vectoplan:project:dirty";
  var EVENT_ERROR = "vectoplan:project:error";
  var EVENT_VISIBILITY_CHANGED = "vectoplan:project:visibility:changed";
  var EVENT_READONLY_BLOCKED = "vectoplan:project:readonly-blocked";
  var EVENT_SIDEBAR_REFRESH = "project-sidebar:refresh";
  var EVENT_PUBLICATION_REFRESH = "vectoplan:project:publication:refresh";
  var EVENT_PROVISIONING_CHANGED = "vectoplan:project:chunk-provisioning:changed";
  var EVENT_ACCESS_SYNC_CHANGED = "vectoplan:project:chunk-access-sync:changed";
  var EVENT_REPAIR_REQUIRED = "vectoplan:project:repair-required";

  var DEFAULT_REQUEST_TIMEOUT_MS = 30000;
  var MAX_RESPONSE_TEXT_LENGTH = 2 * 1024 * 1024;
  var GEOCODER_DEBOUNCE_MS = 350;

  var VALID_PROJECT_ROLES = {
    owner: true,
    admin: true,
    editor: true,
    viewer: true
  };

  var ROLE_CAN_EDIT = { owner: true, admin: true, editor: true };
  var ROLE_CAN_MANAGE = { owner: true, admin: true };

  var PERSISTED_PROJECT_CODES = {
    project_created: true,
    project_created_with_flat_fallback: true,
    project_created_chunk_pending: true,
    project_created_chunk_repair_required: true,
    project_invitation_accepted_chunk_sync_pending: true,
    project_invitation_accepted_chunk_sync_failed: true
  };

  var VALID_VISIBILITIES = {
    private: true,
    unlisted: true,
    public: true
  };

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,

    isNew: true,
    canEdit: false,
    canManage: false,
    canMutate: false,

    demoMode: false,
    publicViewer: false,
    readOnly: false,
    persistent: false,
    authenticated: false,
    authUnavailable: false,
    userBlocked: false,
    accessBlocked: false,
    accessMode: "anonymous",
    projectRole: "",
    identityConsistent: true,
    localLinkState: "not_applicable",
    principalType: "anonymous",

    projectPersisted: false,
    chunkReady: false,
    chunkProvisioningStatus: "pending",
    chunkAccessSyncStatus: "disabled",
    chunkAccessReady: true,
    chunkFallbackUsed: false,
    chunkWorldTemplateRequested: "earth",
    chunkWorldTemplateEffective: "",
    repairRequired: false,

    requestId: "",
    lastResponseStatus: null,
    isDirty: false,
    isSaving: false,
    isLoading: false,
    lastSavedAt: null,
    lastError: null,
    autoSaveTimer: null,
    autoSaveDelay: 700,
    originalPayload: null,
    currentProject: null,
    config: null,
    refs: {},
    geocoder: {
      timer: null,
      controller: null,
      items: [],
      activeIndex: -1
    },
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

  function isElement(value) {
    try {
      return !!value && value.nodeType === 1;
    } catch (error) {
      return false;
    }
  }

  function nowIso() {
    try {
      return new Date().toISOString();
    } catch (error) {
      return "";
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

      if (value === null || value === undefined) {
        return !!fallback;
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
          normalized === "ok" ||
          normalized === "ready"
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
          normalized === "readonly" ||
          normalized === "read_only" ||
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
        return value.length > 8000 ? value.slice(0, 8000) : value;
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
        authorization: true,
        cookie: true,
        cookies: true,
        password: true,
        secret: true,
        token: true,
        token_hash: true,
        access_token: true,
        refresh_token: true,
        jwt: true,
        api_key: true,
        apikey: true,
        session: true,
        session_id: true,
        csrf: true,
        csrf_token: true,
        raw_auth: true,
        raw: true,
        request_body: true,
        requestbody: true,
        request_headers: true,
        requestheaders: true,
        response_headers: true,
        responseheaders: true,
        headers: true,
        stack: true,
        traceback: true,
        error_details: true,
        errordetails: true,
        auth_user_id: true,
        authuserid: true,
        canonical_user_id: true,
        user_id: true,
        userid: true,
        local_user_id: true,
        localuserid: true,
        actor_user_id: true,
        actoruserid: true,
        target_user_id: true,
        targetuserid: true,
        actor_auth_user_id: true,
        target_auth_user_id: true,
        email: true,
        auth_email: true,
        account_id: true,
        owner_user_id: true,
        owneruserid: true,
        auth_owner_user_id: true,
        invited_by_auth_user_id: true,
        accepted_by_auth_user_id: true,
        metadata: true,
        metadata_json: true,
        settings: true,
        service_refs: true,
        service_links: true,
        internal_url: true,
        internal_base_url: true
      };

      var result = {};
      Object.keys(value).slice(0, 300).forEach(function sanitizeKey(key) {
        var normalizedKey = trimString(key, "").toLowerCase().replace(/[^a-z0-9_]/g, "");
        if (!normalizedKey || blockedKeys[normalizedKey]) {
          return;
        }
        result[key] = sanitizeBrowserValue(value[key], level + 1);
      });
      return result;
    } catch (error) {
      return {};
    }
  }

  function sanitizeCurrentUserForBrowser(currentUser) {
    try {
      var user = isObject(currentUser) ? currentUser : {};
      return sanitizeBrowserValue({
        display_name: user.display_name || user.displayName || "",
        displayName: user.displayName || user.display_name || "",
        role: user.role || "",
        locale: user.locale || "",
        timezone: user.timezone || "",
        authenticated: toBooleanSafe(user.authenticated !== undefined ? user.authenticated : user.isAuthenticated, false),
        persistent: toBooleanSafe(user.persistent, false),
        demo_mode: toBooleanSafe(user.demo_mode !== undefined ? user.demo_mode : user.demoMode, false),
        demoMode: toBooleanSafe(user.demoMode !== undefined ? user.demoMode : user.demo_mode, false),
        public_viewer: toBooleanSafe(user.public_viewer !== undefined ? user.public_viewer : user.publicViewer, false),
        publicViewer: toBooleanSafe(user.publicViewer !== undefined ? user.publicViewer : user.public_viewer, false),
        auth_unavailable: toBooleanSafe(user.auth_unavailable !== undefined ? user.auth_unavailable : user.authUnavailable, false),
        authUnavailable: toBooleanSafe(user.authUnavailable !== undefined ? user.authUnavailable : user.auth_unavailable, false),
        user_blocked: toBooleanSafe(user.user_blocked !== undefined ? user.user_blocked : user.userBlocked, false),
        userBlocked: toBooleanSafe(user.userBlocked !== undefined ? user.userBlocked : user.user_blocked, false),
        access_blocked: toBooleanSafe(user.access_blocked !== undefined ? user.access_blocked : user.accessBlocked, false),
        accessBlocked: toBooleanSafe(user.accessBlocked !== undefined ? user.accessBlocked : user.access_blocked, false),
        auth_state: user.auth_state || user.authState || "",
        authState: user.authState || user.auth_state || "",
        blocked_reason: user.blocked_reason || user.blockedReason || "",
        blockedReason: user.blockedReason || user.blocked_reason || "",
        identity_consistent: toBooleanSafe(user.identity_consistent !== undefined ? user.identity_consistent : user.identityConsistent, true),
        identityConsistent: toBooleanSafe(user.identityConsistent !== undefined ? user.identityConsistent : user.identity_consistent, true),
        local_link_state: user.local_link_state || user.localLinkState || "not_applicable",
        localLinkState: user.localLinkState || user.local_link_state || "not_applicable",
        principal_type: user.principal_type || user.principalType || "anonymous",
        principalType: user.principalType || user.principal_type || "anonymous"
      }, 0);
    } catch (error) {
      return {};
    }
  }

  function sanitizeProjectForBrowser(project) {
    try {
      var p = isObject(project) ? project : {};
      var address = isObject(p.address) ? p.address : {};
      var chunk = isObject(p.chunk) ? p.chunk : {};
      var provisioning = isObject(p.chunk_provisioning) ? p.chunk_provisioning : isObject(p.chunkProvisioning) ? p.chunkProvisioning : {};
      var accessSync = isObject(p.chunk_access_sync) ? p.chunk_access_sync : isObject(p.chunkAccessSync) ? p.chunkAccessSync : {};
      var access = isObject(p.access) ? p.access : {};
      var permissions = isObject(access.permissions) ? access.permissions : {};

      return sanitizeBrowserValue({
        id: p.id || p.project_id || p.projectId || "",
        project_id: p.project_id || p.projectId || p.id || "",
        projectId: p.projectId || p.project_id || p.id || "",
        public_id: p.public_id || p.publicId || p.project_public_id || p.projectPublicId || "",
        publicId: p.publicId || p.public_id || p.projectPublicId || p.project_public_id || "",
        project_public_id: p.project_public_id || p.projectPublicId || p.public_id || p.publicId || "",
        projectPublicId: p.projectPublicId || p.project_public_id || p.publicId || p.public_id || "",
        name: p.name || p.title || "",
        title: p.title || p.name || "",
        display_name: p.display_name || p.displayName || p.name || "",
        displayName: p.displayName || p.display_name || p.name || "",
        description: p.description || "",
        cost_center: p.cost_center || p.costCenter || "",
        costCenter: p.costCenter || p.cost_center || "",
        address_text: p.address_text || p.addressText || address.text || "",
        addressText: p.addressText || p.address_text || address.text || "",
        address: { text: p.address_text || p.addressText || address.text || "" },
        visibility: normalizeVisibility(p.visibility, "private"),
        status: p.status || "active",
        setup_status: p.setup_status || p.setupStatus || "draft",
        setupStatus: p.setupStatus || p.setup_status || "draft",
        is_configured: toBooleanSafe(p.is_configured !== undefined ? p.is_configured : p.isConfigured, false),
        isConfigured: toBooleanSafe(p.isConfigured !== undefined ? p.isConfigured : p.is_configured, false),
        is_new: toBooleanSafe(p.is_new !== undefined ? p.is_new : p.isNew, false),
        isNew: toBooleanSafe(p.isNew !== undefined ? p.isNew : p.is_new, false),
        access: {
          role: access.role || p.project_role || p.projectRole || "",
          read_only: toBooleanSafe(access.read_only !== undefined ? access.read_only : access.readOnly, false),
          readOnly: toBooleanSafe(access.readOnly !== undefined ? access.readOnly : access.read_only, false),
          can_view: toBooleanSafe(access.can_view !== undefined ? access.can_view : access.canView, false),
          can_edit: toBooleanSafe(access.can_edit !== undefined ? access.can_edit : access.canEdit, false),
          can_manage: toBooleanSafe(access.can_manage !== undefined ? access.can_manage : access.canManage, false),
          permissions: {
            view: toBooleanSafe(permissions.view, false),
            edit: toBooleanSafe(permissions.edit, false),
            manage: toBooleanSafe(permissions.manage, false)
          }
        },
        chunk: {
          status: chunk.status || p.chunk_status || p.chunkStatus || provisioning.status || "pending",
          ready: toBooleanSafe(chunk.ready !== undefined ? chunk.ready : p.chunk_ready !== undefined ? p.chunk_ready : p.chunkReady, false),
          chunk_project_id: chunk.chunk_project_id || chunk.chunkProjectId || p.chunk_project_id || p.chunkProjectId || null,
          chunk_universe_id: chunk.chunk_universe_id || chunk.chunkUniverseId || p.chunk_universe_id || p.chunkUniverseId || null,
          chunk_world_id: chunk.chunk_world_id || chunk.chunkWorldId || p.chunk_world_id || p.chunkWorldId || null
        },
        chunk_provisioning: {
          status: provisioning.status || p.chunk_provisioning_status || p.chunkProvisioningStatus || chunk.status || p.chunk_status || p.chunkStatus || "pending",
          world_template_requested: provisioning.world_template_requested || provisioning.worldTemplateRequested || p.chunk_world_template_requested || p.chunkWorldTemplateRequested || "earth",
          world_template_effective: provisioning.world_template_effective || provisioning.worldTemplateEffective || p.chunk_world_template_effective || p.chunkWorldTemplateEffective || "",
          fallback_used: toBooleanSafe(provisioning.fallback_used !== undefined ? provisioning.fallback_used : p.chunk_fallback_used !== undefined ? p.chunk_fallback_used : p.chunkFallbackUsed, false),
          fallback_reason: provisioning.fallback_reason || provisioning.fallbackReason || p.chunk_fallback_reason || p.chunkFallbackReason || "",
          repair_required: toBooleanSafe(provisioning.repair_required !== undefined ? provisioning.repair_required : p.chunk_repair_required !== undefined ? p.chunk_repair_required : p.chunkRepairRequired, false)
        },
        chunk_access_sync: {
          status: accessSync.status || p.chunk_access_sync_status || p.chunkAccessSyncStatus || "disabled",
          required: toBooleanSafe(accessSync.required !== undefined ? accessSync.required : p.chunk_access_sync_required !== undefined ? p.chunk_access_sync_required : p.chunkAccessSyncRequired, false),
          repair_required: toBooleanSafe(accessSync.repair_required !== undefined ? accessSync.repair_required : p.chunk_access_sync_repair_required !== undefined ? p.chunk_access_sync_repair_required : p.chunkAccessSyncRepairRequired, false)
        },
        paths: isObject(p.paths) ? p.paths : {}
      }, 0);
    } catch (error) {
      return {};
    }
  }

  function sanitizeResponseForBrowser(payload) {
    try {
      var data = isObject(payload) ? payload : {};
      var project = extractProjectFromResponse(data);
      return sanitizeBrowserValue({
        ok: data.ok !== false,
        code: trimString(data.code, ""),
        message: trimString(data.message || data.error, ""),
        status_code: data.status_code || data.statusCode || null,
        request_id: trimString(data.request_id || data.requestId, ""),
        project_persisted: toBooleanSafe(data.project_persisted !== undefined ? data.project_persisted : data.projectPersisted, false),
        membership_persisted: toBooleanSafe(data.membership_persisted !== undefined ? data.membership_persisted : data.membershipPersisted, false),
        redirect_url: normalizeSafeRedirectUrl(data.redirect_url || data.redirectUrl, project),
        project: sanitizeProjectForBrowser(project),
        chunk: isObject(data.chunk) ? {
          status: data.chunk.status || data.chunk.chunk_status || "",
          ready: toBooleanSafe(data.chunk.ready !== undefined ? data.chunk.ready : data.chunk.chunk_ready, false),
          chunk_project_id: data.chunk.chunk_project_id || data.chunk.chunkProjectId || null,
          chunk_universe_id: data.chunk.chunk_universe_id || data.chunk.chunkUniverseId || null,
          chunk_world_id: data.chunk.chunk_world_id || data.chunk.chunkWorldId || null
        } : {},
        chunk_result: isObject(data.chunk_result) ? {
          ok: data.chunk_result.ok !== false,
          code: trimString(data.chunk_result.code, ""),
          status_code: data.chunk_result.status_code || data.chunk_result.statusCode || null
        } : {},
        access_sync: (function accessSyncSummary() {
          var sync = isObject(data.access_sync) ? data.access_sync : isObject(data.chunk_access_sync) ? data.chunk_access_sync : {};
          return {
            ok: sync.ok !== false,
            code: trimString(sync.code, ""),
            status: trimString(sync.status, ""),
            status_code: sync.status_code || sync.statusCode || null,
            repair_required: toBooleanSafe(sync.repair_required !== undefined ? sync.repair_required : sync.repairRequired, false)
          };
        })()
      }, 0);
    } catch (error) {
      return {};
    }
  }

  function normalizeProjectRole(value, fallback) {
    try {
      var role = trimString(value, fallback || "").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");
      if (role === "administrator" || role === "manager") {
        role = "admin";
      } else if (role === "write" || role === "edit") {
        role = "editor";
      } else if (role === "read" || role === "reader" || role === "readonly" || role === "read_only") {
        role = "viewer";
      }
      return VALID_PROJECT_ROLES[role] ? role : fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function normalizeOperationalStatus(value, fallback) {
    try {
      var text = trimString(value, fallback || "pending").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");
      var aliases = {
        ready: "ready", active: "ready", complete: "ready", completed: "ready", success: "ready", succeeded: "ready", provisioned: "ready", linked: "ready",
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

  function parentOrigin() {
    try {
      if (!window.parent || window.parent === window) {
        return "";
      }
      var referrer = trimString(document.referrer, "");
      if (!referrer) {
        return "";
      }
      var parsed = new URL(referrer, currentOrigin() || undefined);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return "";
      }
      return parsed.origin;
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

  function normalizeMutationUrl(value, fallback) {
    var resolvedFallback = fallback === undefined ? DEFAULT_CREATE_PATH : fallback;
    return normalizeSameOriginPath(value, resolvedFallback, "/v1/projects");
  }

  function normalizeSafeRedirectUrl(value, project) {
    try {
      var fallback = buildProjectUrl(project || state.currentProject || {});
      var candidate = trimString(value, fallback);
      if (!candidate || candidate.indexOf("\\") !== -1 || candidate.indexOf("//") === 0) {
        return fallback;
      }

      var origin = currentOrigin();
      var parsed = new URL(candidate, origin || "http://localhost");
      if (origin && parsed.origin !== origin) {
        return fallback;
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return fallback;
      }

      var path = parsed.pathname + parsed.search + parsed.hash;
      var allowed = path.indexOf("/project=") === 0 || path.indexOf("/ui/project/") === 0;
      if (!allowed) {
        return fallback;
      }

      var publicId = getProjectPublicId(project || state.currentProject || {});
      if (publicId && publicId !== "new" && path.indexOf(encodeURIComponent(publicId)) === -1 && path.indexOf(publicId) === -1) {
        return fallback;
      }
      return path;
    } catch (error) {
      return buildProjectUrl(project || state.currentProject || {});
    }
  }

  function extractProjectFromResponse(payload) {
    try {
      var data = isObject(payload) ? payload : {};
      if (isObject(data.project)) {
        return data.project;
      }
      if (isObject(data.item)) {
        return data.item;
      }
      if (isObject(data.data) && isObject(data.data.project)) {
        return data.data.project;
      }
      return {};
    } catch (error) {
      return {};
    }
  }

  function lifecycleStateFrom(project, payload) {
    try {
      var p = isObject(project) ? project : {};
      var response = isObject(payload) ? payload : {};
      var chunk = isObject(p.chunk) ? p.chunk : isObject(response.chunk) ? response.chunk : {};
      var provisioning = isObject(p.chunk_provisioning) ? p.chunk_provisioning : isObject(p.chunkProvisioning) ? p.chunkProvisioning : {};
      var accessSync = isObject(p.chunk_access_sync) ? p.chunk_access_sync : isObject(p.chunkAccessSync) ? p.chunkAccessSync : isObject(response.access_sync) ? response.access_sync : isObject(response.chunk_access_sync) ? response.chunk_access_sync : {};

      var provisioningStatus = normalizeOperationalStatus(
        provisioning.status || p.chunk_provisioning_status || p.chunkProvisioningStatus || chunk.status || p.chunk_status || p.chunkStatus || response.code,
        "pending"
      );
      var fallbackUsed = toBooleanSafe(
        provisioning.fallback_used !== undefined ? provisioning.fallback_used : p.chunk_fallback_used !== undefined ? p.chunk_fallback_used : p.chunkFallbackUsed,
        provisioningStatus === "fallback_ready" || trimString(response.code, "") === "project_created_with_flat_fallback"
      );
      if (fallbackUsed && provisioningStatus === "ready") {
        provisioningStatus = "fallback_ready";
      }

      var accessRequired = toBooleanSafe(
        accessSync.required !== undefined ? accessSync.required : p.chunk_access_sync_required !== undefined ? p.chunk_access_sync_required : p.chunkAccessSyncRequired,
        false
      );
      var accessStatus = normalizeOperationalStatus(
        accessSync.status || p.chunk_access_sync_status || p.chunkAccessSyncStatus || (accessRequired ? "pending" : "disabled"),
        accessRequired ? "pending" : "disabled"
      );
      var chunkReady = toBooleanSafe(
        chunk.ready !== undefined ? chunk.ready : p.chunk_ready !== undefined ? p.chunk_ready : p.chunkReady,
        false
      ) && (provisioningStatus === "ready" || provisioningStatus === "fallback_ready");
      var accessReady = !accessRequired || accessStatus === "ready" || accessStatus === "disabled";
      var repairRequired = provisioningStatus === "repair_required" || provisioningStatus === "failed" || accessStatus === "repair_required" || accessStatus === "failed" || toBooleanSafe(provisioning.repair_required || accessSync.repair_required || response.repair_required, false);

      return {
        projectPersisted: toBooleanSafe(response.project_persisted !== undefined ? response.project_persisted : response.projectPersisted, !!getProjectPublicId(p)),
        chunkReady: chunkReady,
        provisioningStatus: provisioningStatus,
        accessSyncStatus: accessStatus,
        accessReady: accessReady,
        accessRequired: accessRequired,
        fallbackUsed: fallbackUsed,
        worldTemplateRequested: trimString(provisioning.world_template_requested || provisioning.worldTemplateRequested || p.chunk_world_template_requested || p.chunkWorldTemplateRequested, "earth").toLowerCase(),
        worldTemplateEffective: trimString(provisioning.world_template_effective || provisioning.worldTemplateEffective || p.chunk_world_template_effective || p.chunkWorldTemplateEffective, "").toLowerCase(),
        repairRequired: repairRequired
      };
    } catch (error) {
      return {
        projectPersisted: false,
        chunkReady: false,
        provisioningStatus: "pending",
        accessSyncStatus: "disabled",
        accessReady: true,
        accessRequired: false,
        fallbackUsed: false,
        worldTemplateRequested: "earth",
        worldTemplateEffective: "",
        repairRequired: false
      };
    }
  }

  function applyLifecycleState(project, payload) {
    try {
      var lifecycle = lifecycleStateFrom(project, payload);
      state.projectPersisted = lifecycle.projectPersisted;
      state.chunkReady = lifecycle.chunkReady;
      state.chunkProvisioningStatus = lifecycle.provisioningStatus;
      state.chunkAccessSyncStatus = lifecycle.accessSyncStatus;
      state.chunkAccessReady = lifecycle.accessReady;
      state.chunkFallbackUsed = lifecycle.fallbackUsed;
      state.chunkWorldTemplateRequested = lifecycle.worldTemplateRequested;
      state.chunkWorldTemplateEffective = lifecycle.worldTemplateEffective;
      state.repairRequired = lifecycle.repairRequired;

      var root = state.refs && state.refs.root;
      if (root) {
        root.setAttribute("data-project-persisted", state.projectPersisted ? "true" : "false");
        root.setAttribute("data-project-chunk-ready", state.chunkReady ? "true" : "false");
        root.setAttribute("data-project-chunk-provisioning-status", state.chunkProvisioningStatus);
        root.setAttribute("data-project-chunk-access-sync-status", state.chunkAccessSyncStatus);
        root.setAttribute("data-project-chunk-access-ready", state.chunkAccessReady ? "true" : "false");
        root.setAttribute("data-project-chunk-fallback-used", state.chunkFallbackUsed ? "true" : "false");
        root.setAttribute("data-project-repair-required", state.repairRequired ? "true" : "false");
      }
      return lifecycle;
    } catch (error) {
      return lifecycleStateFrom({}, {});
    }
  }

  function isPersistedProjectResponse(payload, status) {
    try {
      var data = isObject(payload) ? payload : {};
      var project = extractProjectFromResponse(data);
      var hasProject = !!getProjectPublicId(project);
      var nested = isObject(data.data) ? data.data : {};
      var explicit = toBooleanSafe(
        data.project_persisted !== undefined ? data.project_persisted
          : data.projectPersisted !== undefined ? data.projectPersisted
          : nested.project_persisted !== undefined ? nested.project_persisted
          : nested.projectPersisted,
        false
      );
      var code = trimString(data.code || nested.code, "");
      return !!(hasProject && (explicit || PERSISTED_PROJECT_CODES[code]));
    } catch (error) {
      return false;
    }
  }

  function saveOutcome(payload, wasNew) {
    try {
      var data = isObject(payload) ? payload : {};
      var project = extractProjectFromResponse(data);
      var lifecycle = lifecycleStateFrom(project, data);
      var code = trimString(data.code, "");
      var warning = lifecycle.repairRequired || lifecycle.provisioningStatus === "pending" || (lifecycle.accessRequired && lifecycle.accessSyncStatus === "pending") || lifecycle.fallbackUsed;
      var message = "";

      if (lifecycle.repairRequired) {
        message = wasNew
          ? "Projekt wurde gespeichert. Die Chunk- oder Rechteanbindung benötigt eine Reparatur."
          : "Projekt wurde gespeichert. Die Chunk- oder Rechteanbindung benötigt eine Reparatur.";
      } else if (lifecycle.provisioningStatus === "pending") {
        message = "Projekt wurde gespeichert. Die 3D-Welt wird noch bereitgestellt.";
      } else if (lifecycle.accessRequired && lifecycle.accessSyncStatus === "pending") {
        message = "Projekt wurde gespeichert. Die Projektrollen werden noch mit 3D synchronisiert.";
      } else if (lifecycle.fallbackUsed || code === "project_created_with_flat_fallback") {
        message = "Projekt wurde gespeichert. Earth war nicht verfügbar; die kontrollierte Flat-Welt wurde verwendet.";
      } else if (wasNew) {
        message = state.demoMode ? "Demo-Projekt wurde temporär erstellt. Die Projektansicht wird geöffnet…" : "Projekt wurde erstellt. Die Projektansicht wird geöffnet…";
      } else {
        message = state.demoMode ? "Demo-Projekt wurde temporär gespeichert." : "Projekt wurde gespeichert.";
      }

      return { warning: warning, message: message, lifecycle: lifecycle };
    } catch (error) {
      return { warning: false, message: wasNew ? "Projekt wurde erstellt." : "Projekt wurde gespeichert.", lifecycle: lifecycleStateFrom({}, {}) };
    }
  }

  function isTrustedMessageEvent(event) {
    try {
      if (!event) {
        return false;
      }
      if (event.source && window.parent && event.source !== window.parent && event.source !== window) {
        return false;
      }
      var ownOrigin = currentOrigin();
      var trustedParentOrigin = parentOrigin();
      return !event.origin ||
        (!!ownOrigin && event.origin === ownOrigin) ||
        (!!trustedParentOrigin && event.origin === trustedParentOrigin);
    } catch (error) {
      return false;
    }
  }

  function normalizeError(error) {
    try {
      if (!error) {
        return {
          name: "Error",
          message: "Unknown error",
          stack: "",
          code: "",
          status: null,
          payload: null
        };
      }

      if (typeof error === "string") {
        return {
          name: "Error",
          message: error,
          stack: "",
          code: "",
          status: null,
          payload: null
        };
      }

      return {
        name: trimString(error.name, "Error"),
        message: trimString(error.message, String(error)),
        stack: trimString(error.stack, ""),
        code: trimString(error.code || (error.payload && error.payload.code), ""),
        status: error.status || error.statusCode || (error.payload && error.payload.status_code) || null,
        payload: error.payload || null
      };
    } catch (innerError) {
      return {
        name: "Error",
        message: "Unknown error",
        stack: "",
        code: "",
        status: null,
        payload: null
      };
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
      state.listeners.push({
        target: target,
        type: type,
        handler: handler,
        options: options || false
      });

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

      state.listeners = [];
    } catch (error) {
      state.listeners = [];
    }
  }

  function normalizeAccessMode(value, fallback) {
    try {
      var text = trimString(value, fallback || "").toLowerCase().replace(/-/g, "_").replace(/\s+/g, "_");

      if (
        text === "public_viewer" ||
        text === "public_readonly" ||
        text === "public_read_only" ||
        text === "anonymous_public"
      ) {
        return "public";
      }

      if (text === "demo_guest" || text === "demo_project" || text === "demo_mode") {
        return "demo";
      }

      if (text === "auth" || text === "member" || text === "user") {
        return "authenticated";
      }

      if (text === "auth_unavailable" || text === "service_unavailable") {
        return "auth_unavailable";
      }

      if (text === "identity_mismatch" || text === "link_mismatch" || text === "auth_identity_mismatch") {
        return "identity_mismatch";
      }

      if (text === "blocked" || text === "banned" || text === "access_blocked") {
        return "blocked";
      }

      if (
        text === "public" ||
        text === "demo" ||
        text === "authenticated" ||
        text === "anonymous" ||
        text === "auth_unavailable" ||
        text === "identity_mismatch" ||
        text === "blocked"
      ) {
        return text;
      }

      return fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function normalizeVisibility(value, fallback) {
    try {
      var text = trimString(value, fallback || "private")
        .toLowerCase()
        .replace(/-/g, "_")
        .replace(/\s+/g, "_");

      if (text === "öffentlich" || text === "oeffentlich" || text === "open" || text === "listed") {
        return "public";
      }

      if (
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

      if (text === "shared" || text === "geteilt" || text === "privat" || text === "internal" || text === "members") {
        return "private";
      }

      if (VALID_VISIBILITIES[text]) {
        return text;
      }

      return fallback || "private";
    } catch (error) {
      return fallback || "private";
    }
  }

  function pickBoolean(sources, fallback) {
    try {
      for (var i = 0; i < sources.length; i += 1) {
        if (sources[i] !== undefined && sources[i] !== null && sources[i] !== "") {
          return toBooleanSafe(sources[i], fallback);
        }
      }

      return !!fallback;
    } catch (error) {
      return !!fallback;
    }
  }

  function pickString(sources, fallback) {
    try {
      for (var i = 0; i < sources.length; i += 1) {
        var value = trimString(sources[i], "");
        if (value) {
          return value;
        }
      }
      return fallback || "";
    } catch (error) {
      return fallback || "";
    }
  }

  function attr(element, name, fallback) {
    try {
      if (!element || !element.getAttribute) {
        return fallback || "";
      }

      var value = element.getAttribute(name);
      if (value === null || value === undefined || value === "") {
        return fallback || "";
      }

      return value;
    } catch (error) {
      return fallback || "";
    }
  }

  function getConfig() {
    try {
      var win = getWindow();
      var rawConfig = win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG || win.PROJECT_WORKSPACE_CONFIG || {};
      var config = isObject(rawConfig) ? rawConfig : {};
      var root = query(ROOT_SELECTOR);
      var form = query(FORM_SELECTOR);
      var paths = isObject(config.paths) ? config.paths : {};
      var parentEvents = isObject(config.parentEvents) ? config.parentEvents : {};
      var project = sanitizeProjectForBrowser(isObject(config.project) ? config.project : {});
      var currentProject = sanitizeProjectForBrowser(isObject(config.currentProject) ? config.currentProject : project);
      var currentUser = sanitizeCurrentUserForBrowser(isObject(config.currentUser) ? config.currentUser : {});
      var access = isObject(config.access) ? sanitizeBrowserValue(config.access, 0) : isObject(project.access) ? project.access : {};
      var workspaceAccess = isObject(config.workspaceAccess) ? sanitizeBrowserValue(config.workspaceAccess, 0) : isObject(project.workspace_access) ? project.workspace_access : {};

      var accessMode = normalizeAccessMode(pickString([
        config.accessMode, config.access_mode, access.accessMode, access.access_mode,
        workspaceAccess.accessMode, workspaceAccess.access_mode,
        project.accessMode, project.access_mode,
        attr(root, "data-project-access-mode", "")
      ], ""), "");

      var authUnavailable = pickBoolean([
        config.authUnavailable, config.auth_unavailable, currentUser.auth_unavailable,
        currentUser.authUnavailable, access.auth_unavailable, access.authUnavailable,
        attr(root, "data-project-auth-unavailable", ""),
        attr(form, "data-project-form-auth-unavailable", "")
      ], accessMode === "auth_unavailable");

      var userBlocked = pickBoolean([
        config.userBlocked, config.user_blocked, currentUser.user_blocked,
        currentUser.userBlocked, access.user_blocked, access.userBlocked,
        attr(root, "data-project-user-blocked", ""),
        attr(form, "data-project-form-user-blocked", "")
      ], false);

      var accessBlocked = pickBoolean([
        config.accessBlocked, config.access_blocked, currentUser.access_blocked,
        currentUser.accessBlocked, access.access_blocked, access.accessBlocked,
        attr(root, "data-project-access-blocked", ""),
        attr(form, "data-project-form-access-blocked", "")
      ], accessMode === "blocked");

      var identityConsistent = pickBoolean([
        config.identityConsistent, config.identity_consistent,
        currentUser.identity_consistent, currentUser.identityConsistent,
        access.identity_consistent, access.identityConsistent,
        attr(root, "data-project-identity-consistent", "")
      ], true);
      var localLinkState = pickString([
        config.localLinkState, config.local_link_state,
        currentUser.local_link_state, currentUser.localLinkState,
        attr(root, "data-project-local-link-state", "")
      ], "not_applicable").toLowerCase();
      var principalType = pickString([
        config.principalType, config.principal_type,
        currentUser.principal_type, currentUser.principalType
      ], "anonymous").toLowerCase();
      var identityMismatch = !identityConsistent || localLinkState === "identity_mismatch" || localLinkState === "inactive" || accessMode === "identity_mismatch";

      var publicViewer = pickBoolean([
        config.publicViewer, config.isPublicViewer, config.public_viewer,
        access.publicViewer, access.public_viewer, access.isPublicViewer,
        access.is_public_viewer, workspaceAccess.publicViewer,
        workspaceAccess.public_viewer, project.publicViewer,
        project.public_viewer, attr(root, "data-project-public-viewer", ""),
        attr(form, "data-project-form-public-viewer", "")
      ], accessMode === "public");

      var demoMode = pickBoolean([
        config.demoMode, config.demo_mode, currentUser.demo_mode,
        currentUser.demoMode, currentUser.is_demo, project.demo_mode,
        project.demoMode, attr(root, "data-project-demo-mode", ""),
        attr(form, "data-project-form-demo-mode", "")
      ], false);

      var isNew = pickBoolean([
        config.isNew, config.is_new, project.is_new, project.isNew,
        attr(root, "data-project-is-new", "")
      ], true);

      var projectRole = normalizeProjectRole(pickString([
        config.projectRole, config.project_role,
        access.role, access.project_role, access.projectRole,
        project.project_role, project.projectRole,
        attr(root, "data-project-role", "")
      ], isNew ? "owner" : ""), isNew ? "owner" : "");

      if (projectRole === "viewer") {
        publicViewer = publicViewer || accessMode === "public";
      }
      if (publicViewer) {
        accessMode = "public";
      }

      var readOnly = pickBoolean([
        config.readOnly, config.readonly, config.read_only,
        access.readOnly, access.readonly, access.read_only,
        workspaceAccess.readOnly, workspaceAccess.read_only,
        project.readOnly, project.readonly, project.read_only,
        attr(root, "data-project-read-only", ""),
        attr(form, "data-project-form-readonly", "")
      ], publicViewer || projectRole === "viewer" || authUnavailable || userBlocked || accessBlocked || identityMismatch);

      if (publicViewer || projectRole === "viewer" || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        readOnly = true;
      }
      if (publicViewer || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        demoMode = false;
      }

      if (!accessMode) {
        accessMode = authUnavailable ? "auth_unavailable"
          : identityMismatch ? "identity_mismatch"
          : userBlocked || accessBlocked ? "blocked"
          : demoMode ? "demo"
          : publicViewer ? "public"
          : "";
      }

      var authenticated = pickBoolean([
        config.authenticated, currentUser.authenticated,
        currentUser.is_authenticated, currentUser.isAuthenticated
      ], !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked && !identityMismatch);

      var persistent = pickBoolean([
        config.persistent, currentUser.persistent,
        attr(root, "data-project-persistent", "")
      ], authenticated && !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked && !identityMismatch);

      if (publicViewer || demoMode || authUnavailable || userBlocked || accessBlocked || identityMismatch) {
        persistent = false;
      }

      var templateCanEdit = pickBoolean([
        config.canEdit, config.can_edit, config.canMutate, config.can_mutate,
        access.canEdit, access.can_edit, access.canMutate, access.can_mutate,
        attr(root, "data-project-can-edit", ""),
        attr(form, "data-project-form-can-edit", "")
      ], false);

      var roleCanEdit = !!ROLE_CAN_EDIT[projectRole] || (isNew && projectRole === "owner");
      var roleCanManage = !!ROLE_CAN_MANAGE[projectRole] || (isNew && projectRole === "owner");
      var canEdit = !!templateCanEdit && roleCanEdit && !readOnly && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked && !identityMismatch && (persistent || demoMode);
      var canManage = pickBoolean([
        config.canManage, config.can_manage, access.canManage,
        access.can_manage, attr(root, "data-project-can-manage", "")
      ], false) && roleCanManage && !publicViewer && !readOnly && !demoMode && !authUnavailable && !userBlocked && !accessBlocked && !identityMismatch;
      var canMutate = pickBoolean([
        config.canMutate, config.can_mutate, access.canMutate,
        access.can_mutate, attr(root, "data-project-can-mutate", ""),
        attr(form, "data-project-form-can-edit", "")
      ], canEdit) && canEdit;

      var projectPublicId = pickString([
        config.projectPublicId, config.project_public_id,
        project.public_id, project.publicId, project.project_public_id,
        project.projectPublicId, attr(root, "data-project-public-id", "")
      ], isNew ? "new" : "");

      var safeCreatePath = normalizeMutationUrl(paths.createProject || paths.create_project, DEFAULT_CREATE_PATH);
      var safeUpdatePath = normalizeMutationUrl(paths.updateProject || paths.update_project, "");
      var safeGetPath = normalizeSameOriginPath(paths.getProject || paths.get_project, "", "/v1/projects");

      return {
        version: INTERNAL_VERSION,
        isNew: isNew,
        canEdit: canEdit,
        canManage: canManage,
        canMutate: canMutate,
        demoMode: demoMode,
        publicViewer: publicViewer,
        isPublicViewer: publicViewer,
        readOnly: readOnly,
        readonly: readOnly,
        authenticated: authenticated,
        persistent: persistent,
        authUnavailable: authUnavailable,
        userBlocked: userBlocked,
        accessBlocked: accessBlocked,
        accessMode: accessMode || (authenticated ? "authenticated" : "anonymous"),
        projectRole: projectRole,
        project_role: projectRole,
        identityConsistent: identityConsistent,
        localLinkState: localLinkState,
        principalType: principalType,
        project: project,
        currentProject: currentProject,
        currentUser: currentUser,
        access: access,
        workspaceAccess: workspaceAccess,
        projectId: pickString([config.projectId, config.project_id, project.id, project.project_id, project.projectId, attr(root, "data-project-id", "")], ""),
        projectPublicId: projectPublicId,
        projectVisibility: normalizeVisibility(config.projectVisibility || config.project_visibility || project.visibility || attr(root, "data-project-visibility", ""), "private"),
        paths: {
          createProject: safeCreatePath,
          updateProject: safeUpdatePath,
          getProject: safeGetPath,
          context: normalizeSameOriginPath(paths.context, isNew ? DEFAULT_PROJECT_CONTEXT_NEW : "", "/ui/project"),
          workspaceAccess: normalizeSameOriginPath(paths.workspaceAccess || paths.workspace_access, "", "/v1/projects"),
          publication: canManage ? normalizeSameOriginPath(paths.publication || paths.projectPublication || paths.project_publication, "", "/v1/projects") : "",
          members: canManage ? normalizeSameOriginPath(paths.members, "", "/v1/projects") : "",
          invitations: canManage ? normalizeSameOriginPath(paths.invitations, "", "/v1/projects") : "",
          projectRoot: DEFAULT_PROJECT_ROOT_URL,
          projectNew: DEFAULT_PROJECT_NEW_URL
        },
        parentEvents: {
          ready: trimString(parentEvents.ready, EVENT_READY),
          saved: trimString(parentEvents.saved, EVENT_SAVED),
          created: trimString(parentEvents.created, EVENT_CREATED),
          updated: trimString(parentEvents.updated, EVENT_UPDATED),
          deleted: trimString(parentEvents.deleted, "vectoplan:project:deleted"),
          configured: trimString(parentEvents.configured, EVENT_CONFIGURED),
          publicationChanged: trimString(parentEvents.publicationChanged, "vectoplan:project:publication:changed"),
          teamChanged: trimString(parentEvents.teamChanged, "vectoplan:project:team:changed"),
          error: trimString(parentEvents.error, EVENT_ERROR)
        }
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        isNew: true,
        canEdit: false,
        canManage: false,
        canMutate: false,
        demoMode: false,
        publicViewer: false,
        isPublicViewer: false,
        readOnly: true,
        readonly: true,
        authenticated: false,
        persistent: false,
        authUnavailable: false,
        userBlocked: false,
        accessBlocked: true,
        accessMode: "blocked",
        projectRole: "viewer",
        project_role: "viewer",
        identityConsistent: false,
        localLinkState: "unavailable",
        principalType: "anonymous",
        project: {},
        currentProject: {},
        currentUser: {},
        access: {},
        workspaceAccess: {},
        projectId: "",
        projectPublicId: "new",
        projectVisibility: "private",
        paths: {
          createProject: DEFAULT_CREATE_PATH,
          updateProject: "",
          getProject: "",
          context: DEFAULT_PROJECT_CONTEXT_NEW,
          workspaceAccess: "",
          publication: "",
          members: "",
          invitations: "",
          projectRoot: DEFAULT_PROJECT_ROOT_URL,
          projectNew: DEFAULT_PROJECT_NEW_URL
        },
        parentEvents: {
          ready: EVENT_READY,
          saved: EVENT_SAVED,
          created: EVENT_CREATED,
          updated: EVENT_UPDATED,
          deleted: "vectoplan:project:deleted",
          configured: EVENT_CONFIGURED,
          publicationChanged: "vectoplan:project:publication:changed",
          teamChanged: "vectoplan:project:team:changed",
          error: EVENT_ERROR
        }
      };
    }
  }

  function queryRefs() {
    var doc = getDocument();

    return {
      document: doc,
      root: query(ROOT_SELECTOR),
      form: query(FORM_SELECTOR),
      alert: query(ALERT_SELECTOR),

      projectId: queryById("projectId"),
      projectPublicId: queryById("projectPublicId"),
      projectIsNew: queryById("projectIsNew"),
      projectAccessMode: queryById("projectAccessMode"),

      name: queryById("projectName"),
      description: queryById("projectDescription"),
      costCenter: queryById("projectCostCenter"),
      addressText: queryById("projectAddressText"),
      addressMapboxId: queryById("projectAddressMapboxId"),

      visibility: queryById("projectVisibility"),
      visibilityOptions: queryAll("[data-project-visibility-option]"),
      visibilityCard: query("[data-project-visibility-card]"),
      visibilityCurrentLabel: query("[data-project-visibility-current-label]"),
      visibilityStatus: query("[data-project-visibility-status]"),
      visibilityHelp: query("[data-project-visibility-help]"),

      addressCounter: query("[data-project-address-counter]"),
      geocoder: query("[data-project-geocoder]"),
      geocoderSuggestions: query("[data-project-geocoder-suggestions]"),
      geocoderStatus: query("[data-project-geocoder-status]"),
      addressCounterCurrent: query("[data-project-address-counter-current]"),

      submit: query("[data-project-submit]"),
      reset: query("[data-project-reset]"),

      statusPill: query("[data-project-status-pill]"),
      setupStatusText: query("[data-project-setup-status-text]")
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

  function setText(element, value) {
    try {
      if (element) {
        element.textContent = trimString(value, "");
      }
    } catch (error) {}
  }

  function setValue(element, value) {
    try {
      if (element) {
        element.value = value === null || value === undefined ? "" : String(value);
        element.setAttribute("value", element.value);
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

  function setAlert(kind, message) {
    try {
      var refs = state.refs || {};
      var alert = refs.alert;

      if (!alert) {
        return;
      }

      var text = trimString(message, "");

      alert.classList.remove("is-success", "is-warning", "is-error", "is-info");
      alert.removeAttribute("data-kind");

      if (!text) {
        setHidden(alert, true);
        alert.textContent = "";
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

  function setRootState(name, value) {
    try {
      var root = state.refs && state.refs.root;

      if (!root) {
        return;
      }

      var boolValue = !!value;

      if (name === "saving") {
        root.classList.toggle(CLASS_SAVING, boolValue);
        root.setAttribute("data-project-saving", boolValue ? "true" : "false");
      } else if (name === "loading") {
        root.classList.toggle(CLASS_LOADING, boolValue);
        root.setAttribute("data-project-loading", boolValue ? "true" : "false");
      } else if (name === "saved") {
        root.classList.toggle(CLASS_SAVED, boolValue);
        root.setAttribute("data-project-saved", boolValue ? "true" : "false");
      } else if (name === "dirty") {
        root.classList.toggle(CLASS_DIRTY, boolValue);
        root.setAttribute("data-project-dirty", boolValue ? "true" : "false");
      } else if (name === "error") {
        root.classList.toggle(CLASS_ERROR, boolValue);
        root.setAttribute("data-project-error", boolValue ? "true" : "false");
      } else if (name === "readonly") {
        root.classList.toggle(CLASS_READONLY, boolValue);
        root.setAttribute("data-project-readonly", boolValue ? "true" : "false");
        root.setAttribute("data-project-read-only", boolValue ? "true" : "false");
      } else if (name === "publicViewer") {
        root.setAttribute("data-project-public-viewer", boolValue ? "true" : "false");
      } else if (name === "demoMode") {
        root.setAttribute("data-project-demo-mode", boolValue ? "true" : "false");
      } else if (name === "authUnavailable") {
        root.setAttribute("data-project-auth-unavailable", boolValue ? "true" : "false");
      } else if (name === "userBlocked") {
        root.setAttribute("data-project-user-blocked", boolValue ? "true" : "false");
      } else if (name === "accessBlocked") {
        root.setAttribute("data-project-access-blocked", boolValue ? "true" : "false");
      } else if (name === "identityConsistent") {
        root.setAttribute("data-project-identity-consistent", boolValue ? "true" : "false");
      } else if (name === "repairRequired") {
        root.setAttribute("data-project-repair-required", boolValue ? "true" : "false");
      }
    } catch (error) {}
  }

  function canWriteProject() {
    try {
      var roleAllowed = state.isNew ? state.projectRole === "owner" : !!ROLE_CAN_EDIT[state.projectRole];
      return !!(
        !state.isSaving &&
        !state.isLoading &&
        state.canEdit &&
        state.canMutate &&
        roleAllowed &&
        state.identityConsistent &&
        state.localLinkState !== "identity_mismatch" &&
        state.localLinkState !== "inactive" &&
        !state.readOnly &&
        !state.publicViewer &&
        !state.authUnavailable &&
        !state.userBlocked &&
        !state.accessBlocked &&
        (state.persistent || state.demoMode)
      );
    } catch (error) {
      return false;
    }
  }

  function disabledReason() {
    try {
      if (state.authUnavailable) {
        return "Auth-Service nicht erreichbar. Projekt kann nicht gespeichert werden.";
      }
      if (!state.identityConsistent || state.localLinkState === "identity_mismatch") {
        return "Die lokale Benutzerverknüpfung stimmt nicht mit der Auth-Identität überein.";
      }
      if (state.localLinkState === "inactive") {
        return "Die lokale Benutzerverknüpfung ist inaktiv.";
      }
      if (state.userBlocked || state.accessBlocked) {
        return "Der Zugriff ist gesperrt. Projekt kann nicht gespeichert werden.";
      }
      if (state.projectRole === "viewer") {
        return "Viewer haben für dieses Projekt ausschließlich Leserechte.";
      }
      if (state.publicViewer) {
        return "Öffentliche Ansicht: Dieses Projekt ist schreibgeschützt.";
      }
      if (state.readOnly) {
        return "Diese Ansicht ist schreibgeschützt.";
      }
      if (!state.persistent && !state.demoMode) {
        return "Dein Account ist noch nicht lokal verknüpft. Speichern ist deaktiviert.";
      }
      if (!state.canEdit || !state.canMutate) {
        return "Du hast für dieses Projekt nur Leserechte.";
      }
      return "";
    } catch (error) {
      return "Projekt kann nicht gespeichert werden.";
    }
  }

  function setSaving(isSaving) {
    try {
      state.isSaving = !!isSaving;
      setRootState("saving", state.isSaving);

      var disabled = state.isSaving || !canWriteProject();

      if (state.refs.submit) {
        state.refs.submit.disabled = disabled;
        state.refs.submit.setAttribute("aria-busy", state.isSaving ? "true" : "false");
        state.refs.submit.setAttribute("aria-disabled", disabled ? "true" : "false");

        if (disabledReason()) {
          state.refs.submit.setAttribute("title", disabledReason());
        } else {
          state.refs.submit.removeAttribute("title");
        }

        if (state.isSaving) {
          if (!state.refs.submit.getAttribute("data-original-text")) {
            state.refs.submit.setAttribute("data-original-text", state.refs.submit.textContent || "");
          }
          state.refs.submit.textContent = state.isNew ? "Projekt wird erstellt…" : "Projekt wird gespeichert…";
        } else {
          var original = state.refs.submit.getAttribute("data-original-text");
          if (original) {
            state.refs.submit.textContent = original;
          } else {
            state.refs.submit.textContent = state.isNew ? "Projekt erstellen" : "Projekt speichern";
          }
        }
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = disabled;
        state.refs.reset.setAttribute("aria-disabled", disabled ? "true" : "false");
      }
    } catch (error) {}
  }

  function setDirty(isDirty) {
    try {
      if (state.readOnly || state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked) {
        state.isDirty = false;
        setRootState("dirty", false);
        return;
      }

      state.isDirty = !!isDirty;
      setRootState("dirty", state.isDirty);
      setRootState("saved", !state.isDirty && !!state.lastSavedAt);

      dispatchLocal(EVENT_DIRTY, {
        dirty: state.isDirty,
        project: state.currentProject,
        readOnly: state.readOnly,
        publicViewer: state.publicViewer,
        accessMode: state.accessMode
      });
    } catch (error) {}
  }

  function setConfigured(isConfigured, setupStatus) {
    try {
      var root = state.refs && state.refs.root;
      var status = trimString(setupStatus, isConfigured ? "configured" : "draft");

      if (root) {
        root.setAttribute("data-project-is-configured", isConfigured ? "true" : "false");
        root.setAttribute("data-project-setup-status", status);
      }

      if (state.refs.statusPill) {
        state.refs.statusPill.classList.toggle("vp-project-status--configured", !!isConfigured && !state.demoMode && !state.publicViewer && !state.readOnly);
        state.refs.statusPill.classList.toggle("vp-project-status--draft", !isConfigured && !state.demoMode && !state.publicViewer && !state.readOnly);
        state.refs.statusPill.classList.toggle("vp-project-status--demo", !!state.demoMode);
        state.refs.statusPill.classList.toggle("vp-project-status--readonly", !!state.publicViewer || !!state.readOnly);

        state.refs.statusPill.textContent =
          state.publicViewer || state.readOnly
            ? "Read-only"
            : state.demoMode
              ? "Demo"
              : isConfigured
                ? "Konfiguriert"
                : "Entwurf";
      }

      if (state.refs.setupStatusText) {
        state.refs.setupStatusText.textContent = status;
      }
    } catch (error) {}
  }

  function findFieldWrapper(element) {
    try {
      if (!element || !element.closest) {
        return null;
      }

      return element.closest(".vp-project-field");
    } catch (error) {
      return null;
    }
  }

  function removeFieldError(element) {
    try {
      if (!element) {
        return;
      }

      element.removeAttribute("aria-invalid");

      var wrapper = findFieldWrapper(element);
      if (wrapper) {
        wrapper.classList.remove(CLASS_INVALID);

        var old = wrapper.querySelector("." + FIELD_ERROR_CLASS);
        if (old && old.parentNode) {
          old.parentNode.removeChild(old);
        }
      }
    } catch (error) {}
  }

  function setFieldError(element, message) {
    try {
      if (!element) {
        return;
      }

      element.setAttribute("aria-invalid", "true");

      var wrapper = findFieldWrapper(element);
      if (!wrapper) {
        return;
      }

      wrapper.classList.add(CLASS_INVALID);

      var old = wrapper.querySelector("." + FIELD_ERROR_CLASS);
      if (old && old.parentNode) {
        old.parentNode.removeChild(old);
      }

      var doc = getDocument();
      if (!doc || !doc.createElement) {
        return;
      }

      var errorNode = doc.createElement("p");
      errorNode.className = FIELD_ERROR_CLASS;
      errorNode.textContent = trimString(message, "Dieses Feld ist erforderlich.");

      wrapper.appendChild(errorNode);
    } catch (error) {}
  }

  function clearValidation() {
    try {
      var refs = state.refs || {};

      [
        refs.name,
        refs.addressText,
        refs.visibility
      ].forEach(function clearOne(element) {
        removeFieldError(element);
      });
    } catch (error) {}
  }

  function updateAddressCounter() {
    try {
      var refs = state.refs || {};
      var textarea = refs.addressText;

      if (!textarea || !refs.addressCounterCurrent) {
        return;
      }

      var value = textarea.value || "";
      refs.addressCounterCurrent.textContent = String(value.length);

      var maxLength = Number(textarea.getAttribute("maxlength") || (refs.addressCounter ? refs.addressCounter.getAttribute("data-max-length") : "") || 2000);
      if (Number.isFinite(maxLength) && maxLength > 0 && refs.addressCounter) {
        refs.addressCounter.setAttribute("data-over-limit", value.length > maxLength ? "true" : "false");
      }
    } catch (error) {}
  }

  function setGeocoderStatus(message, tone) {
    try {
      var node = state.refs && state.refs.geocoderStatus;
      if (!node) { return; }
      node.textContent = trimString(message, "");
      node.setAttribute("data-tone", trimString(tone, "neutral"));
    } catch (error) {}
  }

  function closeGeocoderSuggestions() {
    try {
      var refs = state.refs || {};
      state.geocoder.items = [];
      state.geocoder.activeIndex = -1;
      if (refs.geocoderSuggestions) {
        refs.geocoderSuggestions.replaceChildren();
        setHidden(refs.geocoderSuggestions, true);
      }
      if (refs.addressText) {
        refs.addressText.setAttribute("aria-expanded", "false");
        refs.addressText.removeAttribute("aria-activedescendant");
      }
    } catch (error) {}
  }

  function selectGeocoderSuggestion(index) {
    try {
      var item = state.geocoder.items[index];
      var label = item && trimString(item.label || item.address_text, "");
      if (!label || !state.refs.addressText) { return; }
      setValue(state.refs.addressText, label);
      setValue(state.refs.addressMapboxId, trimString(item.mapbox_id || item.id, ""));
      removeFieldError(state.refs.addressText);
      updateAddressCounter();
      closeGeocoderSuggestions();
      setGeocoderStatus("Adresse ausgew\u00e4hlt \u00b7 Powered by Mapbox", "success");
      if (canWriteProject()) {
        markDirtyFromInput();
      }
    } catch (error) {}
  }

  function renderGeocoderSuggestions(items) {
    try {
      var refs = state.refs || {};
      var list = refs.geocoderSuggestions;
      var doc = refs.document || getDocument();
      if (!list || !doc) { return; }

      list.replaceChildren();
      state.geocoder.items = Array.isArray(items) ? items.slice(0, 6) : [];
      state.geocoder.activeIndex = -1;

      if (!state.geocoder.items.length) {
        closeGeocoderSuggestions();
        setGeocoderStatus("Keine passende Adresse gefunden \u00b7 Powered by Mapbox", "muted");
        return;
      }

      state.geocoder.items.forEach(function addSuggestion(item, index) {
        var option = doc.createElement("button");
        option.type = "button";
        option.className = "vp-project-geocoder__option";
        option.id = "projectAddressSuggestion-" + String(index);
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.setAttribute("data-geocoder-index", String(index));

        var label = doc.createElement("span");
        label.className = "vp-project-geocoder__option-label";
        label.textContent = trimString(item.label || item.address_text, "Adresse");
        option.appendChild(label);

        var meta = doc.createElement("span");
        meta.className = "vp-project-geocoder__option-meta";
        meta.textContent = trimString(item.feature_type, "Adresse");
        option.appendChild(meta);

        addListener(option, "mousedown", function keepFocus(event) {
          try { event.preventDefault(); } catch (_) {}
        });
        addListener(option, "click", function chooseSuggestion() {
          selectGeocoderSuggestion(index);
        });
        list.appendChild(option);
      });

      setHidden(list, false);
      if (refs.addressText) {
        refs.addressText.setAttribute("aria-expanded", "true");
      }
      setGeocoderStatus("Adresse ausw\u00e4hlen \u00b7 Powered by Mapbox", "neutral");
    } catch (error) {
      closeGeocoderSuggestions();
    }
  }

  async function requestGeocoderSuggestions(query) {
    var cleanQuery = trimString(query, "");
    if (cleanQuery.length < 3 || !canWriteProject()) {
      closeGeocoderSuggestions();
      setGeocoderStatus(
        cleanQuery ? "Mindestens 3 Zeichen eingeben \u00b7 Powered by Mapbox" : "Adresssuche bereit \u00b7 Powered by Mapbox",
        "muted"
      );
      return;
    }

    try {
      if (state.geocoder.controller && typeof state.geocoder.controller.abort === "function") {
        state.geocoder.controller.abort();
      }
      state.geocoder.controller = typeof AbortController === "function" ? new AbortController() : null;
      setGeocoderStatus("Adresse wird gesucht \u2026", "loading");

      var response = await fetch(
        "/v1/geocoding/suggest?q=" + encodeURIComponent(cleanQuery) + "&limit=5",
        {
          method: "GET",
          credentials: "same-origin",
          headers: { "Accept": "application/json" },
          signal: state.geocoder.controller ? state.geocoder.controller.signal : undefined
        }
      );
      var payload = await response.json().catch(function emptyPayload() { return {}; });
      if (!response.ok || payload.ok === false) {
        throw new Error(trimString(payload.message || payload.error, "Adresssuche fehlgeschlagen."));
      }
      if (getValue(state.refs.addressText) !== cleanQuery) {
        return;
      }
      renderGeocoderSuggestions(payload.items || []);
    } catch (error) {
      if (error && error.name === "AbortError") { return; }
      closeGeocoderSuggestions();
      setGeocoderStatus("Adresssuche derzeit nicht verf\u00fcgbar \u00b7 Powered by Mapbox", "error");
    }
  }

  function scheduleGeocoderSearch() {
    try {
      if (state.geocoder.timer) {
        clearTimeout(state.geocoder.timer);
      }
      var query = getValue(state.refs.addressText);
      state.geocoder.timer = setTimeout(function runGeocoderSearch() {
        requestGeocoderSuggestions(query);
      }, GEOCODER_DEBOUNCE_MS);
    } catch (error) {}
  }

  function onGeocoderKeydown(event) {
    try {
      var items = state.geocoder.items || [];
      if (!items.length) {
        if (event && event.key === "Escape") {
          closeGeocoderSuggestions();
        }
        return;
      }
      var next = state.geocoder.activeIndex;
      if (event.key === "ArrowDown") {
        next = Math.min(items.length - 1, next + 1);
      } else if (event.key === "ArrowUp") {
        next = Math.max(0, next < 0 ? items.length - 1 : next - 1);
      } else if (event.key === "Enter" && next >= 0) {
        event.preventDefault();
        selectGeocoderSuggestion(next);
        return;
      } else if (event.key === "Escape") {
        closeGeocoderSuggestions();
        return;
      } else {
        return;
      }

      event.preventDefault();
      state.geocoder.activeIndex = next;
      var options = queryAll("[data-geocoder-index]");
      options.forEach(function markOption(option, index) {
        var active = index === next;
        option.setAttribute("aria-selected", active ? "true" : "false");
        option.classList.toggle("is-active", active);
      });
      if (state.refs.addressText && options[next]) {
        state.refs.addressText.setAttribute("aria-activedescendant", options[next].id);
        if (typeof options[next].scrollIntoView === "function") {
          options[next].scrollIntoView({ block: "nearest" });
        }
      }
    } catch (error) {}
  }

  function visibilityLabel(value) {
    var normalized = normalizeVisibility(value, "private");

    if (normalized === "public") {
      return "Öffentlich";
    }

    if (normalized === "unlisted") {
      return "Nicht gelistet";
    }

    return "Privat";
  }

  function visibilityHelpText(value) {
    var normalized = normalizeVisibility(value, "private");

    if (state.publicViewer) {
      return "Öffentliche Ansicht: Die Sichtbarkeit kann hier nicht geändert werden.";
    }

    if (state.readOnly) {
      return "Schreibgeschützte Ansicht: Die Sichtbarkeit kann hier nicht geändert werden.";
    }

    if (state.authUnavailable) {
      return "Auth-Service nicht erreichbar. Die Sichtbarkeit kann aktuell nicht geändert werden.";
    }

    if (state.userBlocked || state.accessBlocked) {
      return "Der Zugriff ist gesperrt. Die Sichtbarkeit kann aktuell nicht geändert werden.";
    }

    if (normalized === "public") {
      return "Öffentlich bedeutet nicht automatisch, dass alle Arbeitsbereiche sichtbar sind. Workspaces werden separat über Veröffentlichung freigegeben.";
    }

    if (normalized === "unlisted") {
      return "Nicht gelistet eignet sich für Linkfreigaben. Das Projekt erscheint nicht automatisch in öffentlichen Listen.";
    }

    return "Privat ist der sicherste Standard. Zugriff erhalten nur berechtigte Projektmitglieder.";
  }

  function syncVisibilityCards(value) {
    try {
      var refs = state.refs || {};
      var normalized = normalizeVisibility(value || getValue(refs.visibility), "private");
      var readonly = !canWriteProject();

      setValue(refs.visibility, normalized);

      if (refs.root) {
        refs.root.setAttribute("data-project-visibility", normalized);
      }

      if (refs.visibilityCard) {
        refs.visibilityCard.setAttribute("data-current-visibility", normalized);
        refs.visibilityCard.setAttribute("data-project-visibility", normalized);
        refs.visibilityCard.setAttribute("data-project-visibility-disabled", readonly ? "true" : "false");
        refs.visibilityCard.setAttribute("data-project-visibility-disabled-reason", disabledReason());
      }

      if (refs.visibilityCurrentLabel) {
        refs.visibilityCurrentLabel.textContent = visibilityLabel(normalized);
      }

      if (refs.visibilityStatus) {
        refs.visibilityStatus.textContent = visibilityLabel(normalized);
      }

      if (refs.visibilityHelp) {
        refs.visibilityHelp.textContent = visibilityHelpText(normalized);
      }

      (refs.visibilityOptions || []).forEach(function syncOption(option) {
        try {
          var optionValue = normalizeVisibility(option.getAttribute("data-value") || option.getAttribute("data-visibility"), "private");
          var selected = optionValue === normalized;

          option.classList.toggle(CLASS_SELECTED, selected);
          option.classList.toggle(CLASS_DISABLED, readonly);
          option.classList.toggle(CLASS_READONLY, readonly);

          option.setAttribute("aria-checked", selected ? "true" : "false");
          option.setAttribute("data-selected", selected ? "true" : "false");
          option.setAttribute("aria-disabled", readonly ? "true" : "false");
          option.setAttribute("tabindex", readonly ? "-1" : "0");

          if ("disabled" in option) {
            option.disabled = readonly;
          }

          if (readonly && disabledReason()) {
            option.setAttribute("title", disabledReason());
          } else {
            option.removeAttribute("title");
          }
        } catch (error) {}
      });
    } catch (error) {}
  }

  function setVisibility(value, options) {
    try {
      var normalized = normalizeVisibility(value, "private");
      var opts = isObject(options) ? options : {};

      if (!canWriteProject()) {
        syncVisibilityCards(normalized);
        if (!opts.silent && disabledReason()) {
          setAlert("info", disabledReason());
        }
        return normalized;
      }

      setValue(state.refs.visibility, normalized);
      syncVisibilityCards(normalized);

      if (!opts.silent) {
        dispatchLocal(EVENT_VISIBILITY_CHANGED, {
          visibility: normalized,
          value: normalized,
          project: state.currentProject,
          accessMode: state.accessMode,
          source: "project_form"
        });

        emitParentEvent(EVENT_VISIBILITY_CHANGED, {
          visibility: normalized,
          value: normalized,
          project: state.currentProject,
          accessMode: state.accessMode,
          source: "project_form"
        });

        markDirtyFromInput();
      }

      return normalized;
    } catch (error) {
      return "private";
    }
  }

  function collectPayload() {
    try {
      var refs = state.refs || {};
      var visibility = normalizeVisibility(getValue(refs.visibility), "private");
      var addressText = getValue(refs.addressText);
      var name = getValue(refs.name);

      return {
        name: name,
        title: name,
        description: getValue(refs.description),
        cost_center: getValue(refs.costCenter),
        address_text: addressText,
        address_mapbox_id: getValue(refs.addressMapboxId),
        address: {
          text: addressText
        },
        visibility: visibility
      };
    } catch (error) {
      return {};
    }
  }

  function validatePayload(payload) {
    var errors = [];

    try {
      clearValidation();

      if (!trimString(payload.name, "")) {
        errors.push({
          field: "name",
          element: state.refs.name,
          message: "Projektname ist erforderlich."
        });
      }

      if (!trimString(payload.address_text, "")) {
        errors.push({
          field: "address_text",
          element: state.refs.addressText,
          message: "Adresse oder Standortbeschreibung ist erforderlich."
        });
      }

      if (!VALID_VISIBILITIES[normalizeVisibility(payload.visibility, "")]) {
        errors.push({
          field: "visibility",
          element: state.refs.visibility,
          message: "Bitte wähle eine gültige Sichtbarkeit."
        });
      }

      errors.forEach(function mark(error) {
        setFieldError(error.element, error.message);
      });

      if (errors.length && errors[0].element && typeof errors[0].element.focus === "function") {
        try {
          errors[0].element.focus();
        } catch (focusError) {}
      }

      return {
        ok: errors.length === 0,
        errors: errors
      };
    } catch (error) {
      return {
        ok: false,
        errors: [
          {
            field: "form",
            message: "Validierung fehlgeschlagen."
          }
        ]
      };
    }
  }

  function fillFormFromProject(project) {
    try {
      var refs = state.refs || {};
      var p = sanitizeProjectForBrowser(isObject(project) ? project : {});
      var address = isObject(p.address) ? p.address : {};
      var publicId = pickString([p.public_id, p.publicId, p.project_public_id, p.projectPublicId], "");
      var isNew = toBooleanSafe(p.is_new !== undefined ? p.is_new : p.isNew, false) || !trimString(publicId, "");

      setValue(refs.projectId, p.id || p.project_id || p.projectId || "");
      setValue(refs.projectPublicId, publicId);
      setValue(refs.projectIsNew, isNew ? "true" : "false");
      setValue(refs.projectAccessMode, state.accessMode || "");
      setValue(refs.name, p.name || p.display_name || p.displayName || "");
      setValue(refs.description, p.description || "");
      setValue(refs.costCenter, p.cost_center || p.costCenter || "");
      setValue(refs.addressText, p.address_text || p.addressText || address.text || "");
      setValue(refs.addressMapboxId, "");
      setVisibility(p.visibility || state.config.projectVisibility || "private", { silent: true });

      state.currentProject = safeClone(p);
      state.isNew = isNew;
      state.originalPayload = collectPayload();
      applyLifecycleState(p, {});
      setConfigured(toBooleanSafe(p.is_configured !== undefined ? p.is_configured : p.isConfigured, false), p.setup_status || p.setupStatus || "draft");

      if (refs.root) {
        refs.root.setAttribute("data-project-id", p.id || p.project_id || p.projectId || "");
        refs.root.setAttribute("data-project-public-id", publicId || (isNew ? "new" : ""));
        refs.root.setAttribute("data-project-is-new", isNew ? "true" : "false");
      }
      if (refs.form) {
        refs.form.setAttribute("data-project-form-can-edit", canWriteProject() ? "true" : "false");
      }
      updateAddressCounter();
      setDirty(false);
    } catch (error) {}
  }

  function getProjectPublicId(project) {
    try {
      var p = isObject(project) ? project : {};
      return trimString(
        p.public_id ||
          p.publicId ||
          p.project_public_id ||
          p.projectPublicId ||
          "",
        ""
      );
    } catch (error) {
      return "";
    }
  }

  function getProjectApiId(project) {
    try {
      var p = isObject(project) ? project : {};
      return trimString(
        p.public_id ||
          p.publicId ||
          p.project_public_id ||
          p.projectPublicId ||
          state.config.projectPublicId ||
          state.config.projectId ||
          p.id ||
          "",
        ""
      );
    } catch (error) {
      return "";
    }
  }

  function isProjectConfigured(project) {
    try {
      var p = isObject(project) ? project : {};
      var name = trimString(p.name || p.display_name || p.displayName, "");
      var address = isObject(p.address) ? p.address : {};
      var addressText = trimString(p.address_text || p.addressText || address.text, "");

      return toBooleanSafe(p.is_configured || p.isConfigured, false) ||
        p.setup_status === "configured" ||
        p.setupStatus === "configured" ||
        (!!name && !!addressText);
    } catch (error) {
      return false;
    }
  }

  function buildProjectUrl(project) {
    try {
      var publicId = getProjectPublicId(project);

      if (!publicId || publicId === "new") {
        return DEFAULT_PROJECT_NEW_URL;
      }

      return "/project=" + encodeURIComponent(publicId);
    } catch (error) {
      return DEFAULT_PROJECT_ROOT_URL;
    }
  }

  function buildUpdatePath(project) {
    try {
      var configuredPath = normalizeMutationUrl(state.config.paths.updateProject, "");
      var apiId = getProjectApiId(project || state.currentProject);
      if (configuredPath) {
        return configuredPath;
      }
      if (apiId && apiId !== "new") {
        return normalizeMutationUrl("/v1/projects/" + encodeURIComponent(apiId), "");
      }
      return "";
    } catch (error) {
      return "";
    }
  }

  function dispatchLocal(type, detail) {
    try {
      var root = state.refs && state.refs.root;

      if (!root || typeof CustomEvent !== "function") {
        return false;
      }

      root.dispatchEvent(
        new CustomEvent(type, {
          bubbles: true,
          cancelable: false,
          detail: detail || {}
        })
      );

      return true;
    } catch (error) {
      return false;
    }
  }

  function postParentMessage(type, detail) {
    try {
      if (!window.parent || window.parent === window) {
        return false;
      }

      var targetOrigin = parentOrigin() || currentOrigin();
      if (!targetOrigin) {
        return false;
      }

      var safeDetail = sanitizeBrowserValue(detail || {}, 0);
      var payload = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-form",
        version: INTERNAL_VERSION,
        detail: safeDetail,
        project: sanitizeProjectForBrowser(safeDetail.project || state.currentProject),
        requestId: state.requestId || "",
        ts: Date.now()
      };

      window.parent.postMessage(payload, targetOrigin);
      return true;
    } catch (error) {
      return false;
    }
  }

  function dispatchParentEvent(type, detail) {
    try {
      if (window.parent && window.parent !== window && window.parent.dispatchEvent && typeof window.parent.CustomEvent === "function") {
        window.parent.dispatchEvent(new window.parent.CustomEvent(type, { detail: sanitizeBrowserValue(detail || {}, 0) }));
        return true;
      }
    } catch (error) {}
    return false;
  }

  function emitParentEvent(type, detail) {
    try {
      var payloadDetail = sanitizeBrowserValue(detail || {}, 0);
      if (payloadDetail.project) {
        payloadDetail.project = sanitizeProjectForBrowser(payloadDetail.project);
      }

      dispatchLocal(type, payloadDetail);
      postParentMessage(type, payloadDetail);
      dispatchParentEvent(type, payloadDetail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event(EVENT_SIDEBAR_REFRESH));
        }
      } catch (error) {}
      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event("resize"));
        }
      } catch (error) {}
      return true;
    } catch (error) {
      return false;
    }
  }

  async function requestJson(url, options) {
    var controller = null;
    var timeoutId = null;

    try {
      var opts = isObject(options) ? options : {};
      var method = trimString(opts.method, "GET").toUpperCase();
      var target = method === "GET"
        ? normalizeSameOriginPath(url, "", "/v1/projects")
        : normalizeMutationUrl(url, "");

      if (!target) {
        var unsafeError = new Error("Unsichere oder fehlende Request-URL.");
        unsafeError.code = "unsafe_request_url";
        unsafeError.status = 400;
        throw unsafeError;
      }

      if (["GET", "POST", "PATCH", "PUT", "DELETE"].indexOf(method) === -1) {
        var methodError = new Error("Nicht unterstützte HTTP-Methode.");
        methodError.code = "unsupported_request_method";
        methodError.status = 400;
        throw methodError;
      }

      var requestId = trimString(opts.requestId, "") || createRequestId();
      state.requestId = requestId;
      var headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "fetch",
        "X-VECTOPLAN-Client": "project_form.js",
        "X-Request-ID": requestId,
        "X-Correlation-ID": requestId
      };

      if (typeof AbortController === "function") {
        controller = new AbortController();
        timeoutId = setTimeout(function abortRequest() {
          try { controller.abort(); } catch (_) {}
        }, Math.max(1000, Number(opts.timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS)));
      }

      var response = await fetch(target, {
        method: method,
        headers: headers,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller ? controller.signal : undefined,
        body: opts.body !== undefined ? opts.body : undefined
      });

      state.lastResponseStatus = response.status;
      var text = await response.text();
      if (text.length > MAX_RESPONSE_TEXT_LENGTH) {
        var sizeError = new Error("Serverantwort ist zu groß.");
        sizeError.code = "response_too_large";
        sizeError.status = 502;
        throw sizeError;
      }

      var data = safeJsonParse(text, null);
      if (!isObject(data)) {
        data = data === null ? {} : { data: data };
      }
      data.status_code = data.status_code || response.status;
      data.request_id = data.request_id || data.requestId || response.headers.get("X-Request-ID") || requestId;

      if (!response.ok) {
        var message = data.error || data.message || "Request failed with status " + response.status;
        var error = new Error(message);
        error.status = response.status;
        error.statusCode = response.status;
        error.payload = data;
        error.code = data.code || "request_failed";
        error.requestId = data.request_id;
        throw error;
      }

      return data;
    } catch (error) {
      if (error && error.name === "AbortError") {
        var timeoutError = new Error("Die Anfrage hat das Zeitlimit überschritten.");
        timeoutError.name = "TimeoutError";
        timeoutError.code = "request_timeout";
        timeoutError.status = 504;
        timeoutError.payload = { request_id: state.requestId };
        throw timeoutError;
      }
      throw error;
    } finally {
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
      }
    }
  }

  function canAutoSaveProject() {
    try {
      if (state.isNew || state.isSaving || !state.isDirty || !canWriteProject()) {
        return false;
      }
      var payload = collectPayload();
      return !!(
        trimString(payload.name, "") &&
        trimString(payload.address_text, "") &&
        VALID_VISIBILITIES[normalizeVisibility(payload.visibility, "")]
      );
    } catch (error) {
      return false;
    }
  }

  function scheduleAutoSave(delay) {
    try {
      if (state.autoSaveTimer !== null) {
        clearTimeout(state.autoSaveTimer);
        state.autoSaveTimer = null;
      }
      if (state.isNew || !state.isDirty) {
        return;
      }
      state.autoSaveTimer = setTimeout(function runAutoSave() {
        state.autoSaveTimer = null;
        if (canAutoSaveProject()) {
          void saveProject({ autosave: true });
        }
      }, Math.max(150, Number(delay) || state.autoSaveDelay));
    } catch (error) {}
  }

  function flushAutoSave() {
    try {
      if (state.autoSaveTimer !== null) {
        clearTimeout(state.autoSaveTimer);
        state.autoSaveTimer = null;
      }
      if (canAutoSaveProject()) {
        void saveProject({ autosave: true });
      }
    } catch (error) {}
  }

  function markDirtyFromInput() {
    try {
      if (state.isSaving || !canWriteProject()) {
        return;
      }

      updateAddressCounter();
      setDirty(true);
      setRootState("saved", false);
      setAlert("", "");
      scheduleAutoSave();
    } catch (error) {}
  }

  function updateConfigPathsFromProject(project) {
    try {
      if (state.readOnly || state.publicViewer) {
        return;
      }

      var publicId = getProjectPublicId(project);
      if (!publicId || publicId === "new") {
        return;
      }

      state.config.projectPublicId = publicId;
      state.config.projectId = project && project.id ? project.id : state.config.projectId;
      state.config.paths.updateProject = normalizeMutationUrl("/v1/projects/" + encodeURIComponent(publicId), "");
      state.config.paths.getProject = normalizeSameOriginPath("/v1/projects/" + encodeURIComponent(publicId), "", "/v1/projects");
      state.config.paths.context = normalizeSameOriginPath("/ui/project/" + encodeURIComponent(publicId) + "/context.json", "", "/ui/project");
      state.config.paths.workspaceAccess = normalizeSameOriginPath("/v1/projects/" + encodeURIComponent(publicId) + "/workspace-access/project", "", "/v1/projects");

      if (state.config.canManage && ROLE_CAN_MANAGE[state.projectRole]) {
        state.config.paths.publication = normalizeSameOriginPath("/v1/projects/" + encodeURIComponent(publicId) + "/publication", "", "/v1/projects");
        state.config.paths.members = normalizeSameOriginPath("/v1/projects/" + encodeURIComponent(publicId) + "/members", "", "/v1/projects");
        state.config.paths.invitations = normalizeSameOriginPath("/v1/projects/" + encodeURIComponent(publicId) + "/invitations", "", "/v1/projects");
      } else {
        state.config.paths.publication = "";
        state.config.paths.members = "";
        state.config.paths.invitations = "";
      }
    } catch (error) {}
  }

  function refreshGlobalConfigFromState() {
    try {
      var win = getWindow();
      var config = win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG || win.PROJECT_WORKSPACE_CONFIG || state.config || {};

      config.project = sanitizeProjectForBrowser(state.currentProject || config.project || {});
      config.currentProject = config.project;
      config.projectPublicId = getProjectPublicId(state.currentProject) || state.config.projectPublicId;
      config.projectVisibility = normalizeVisibility(getValue(state.refs.visibility), "private");
      config.isNew = !!state.isNew;
      config.canEdit = !!state.canEdit;
      config.canManage = !!state.canManage;
      config.canMutate = !!state.canMutate;
      config.readOnly = !!state.readOnly;
      config.publicViewer = !!state.publicViewer;
      config.demoMode = !!state.demoMode;
      config.persistent = !!state.persistent;
      config.authUnavailable = !!state.authUnavailable;
      config.userBlocked = !!state.userBlocked;
      config.accessBlocked = !!state.accessBlocked;
      config.projectRole = state.projectRole;
      config.identityConsistent = !!state.identityConsistent;
      config.localLinkState = state.localLinkState;
      config.chunkReady = !!state.chunkReady;
      config.chunkProvisioningStatus = state.chunkProvisioningStatus;
      config.chunkAccessSyncStatus = state.chunkAccessSyncStatus;
      config.chunkAccessReady = !!state.chunkAccessReady;
      config.chunkFallbackUsed = !!state.chunkFallbackUsed;
      config.repairRequired = !!state.repairRequired;

      if (!isObject(config.paths)) {
        config.paths = {};
      }
      Object.keys(state.config.paths || {}).forEach(function copyPath(key) {
        config.paths[key] = state.config.paths[key];
      });

      win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG = config;
      win.PROJECT_WORKSPACE_CONFIG = config;
      state.config = config;
    } catch (error) {}
  }

  function updateAfterSave(payload, wasNew) {
    try {
      var rawProject = extractProjectFromResponse(payload);
      var project = sanitizeProjectForBrowser(rawProject);
      if (!getProjectPublicId(project)) {
        project = sanitizeProjectForBrowser(state.currentProject || {});
      }

      state.currentProject = safeClone(project);
      state.isNew = !getProjectPublicId(project) || getProjectPublicId(project) === "new";
      state.lastSavedAt = nowIso();
      state.lastError = null;
      state.projectPersisted = !state.isNew;

      updateConfigPathsFromProject(project);
      fillFormFromProject(Object.assign({}, project, { is_new: state.isNew, isNew: state.isNew }));
      applyLifecycleState(project, payload);

      if (state.refs.projectIsNew) {
        setValue(state.refs.projectIsNew, state.isNew ? "true" : "false");
      }
      if (state.refs.root) {
        state.refs.root.setAttribute("data-project-is-new", state.isNew ? "true" : "false");
        state.refs.root.setAttribute("data-project-public-id", getProjectPublicId(project));
      }

      setDirty(false);
      setRootState("error", false);
      setRootState("saved", true);

      var configured = isProjectConfigured(project);
      setConfigured(configured, configured ? "configured" : (project.setup_status || project.setupStatus || "draft"));
      refreshGlobalConfigFromState();

      var safePayload = sanitizeResponseForBrowser(payload || {});
      var detail = {
        project: sanitizeProjectForBrowser(project),
        payload: safePayload,
        isNew: !!wasNew,
        is_new: !!wasNew,
        isConfigured: configured,
        is_configured: configured,
        projectPersisted: state.projectPersisted,
        redirectUrl: normalizeSafeRedirectUrl(safePayload.redirect_url || safePayload.redirectUrl, project),
        savedAt: state.lastSavedAt,
        requestId: safePayload.request_id || state.requestId || "",
        demoMode: state.demoMode,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        accessMode: state.accessMode,
        projectRole: state.projectRole,
        visibility: normalizeVisibility(project.visibility || getValue(state.refs.visibility), "private"),
        chunkReady: state.chunkReady,
        chunkProvisioningStatus: state.chunkProvisioningStatus,
        chunkAccessSyncStatus: state.chunkAccessSyncStatus,
        chunkAccessReady: state.chunkAccessReady,
        chunkFallbackUsed: state.chunkFallbackUsed,
        worldTemplateRequested: state.chunkWorldTemplateRequested,
        worldTemplateEffective: state.chunkWorldTemplateEffective,
        repairRequired: state.repairRequired
      };

      emitParentEvent(state.config.parentEvents.saved || EVENT_SAVED, detail);
      emitParentEvent(wasNew ? state.config.parentEvents.created || EVENT_CREATED : state.config.parentEvents.updated || EVENT_UPDATED, detail);
      if (configured) {
        emitParentEvent(state.config.parentEvents.configured || EVENT_CONFIGURED, detail);
      }
      emitParentEvent(EVENT_PROVISIONING_CHANGED, detail);
      emitParentEvent(EVENT_ACCESS_SYNC_CHANGED, detail);
      if (state.repairRequired) {
        emitParentEvent(EVENT_REPAIR_REQUIRED, detail);
      }
      emitParentEvent(EVENT_PUBLICATION_REFRESH, detail);
      return detail;
    } catch (error) {
      return {
        project: sanitizeProjectForBrowser(state.currentProject),
        isNew: !!wasNew,
        redirectUrl: normalizeSafeRedirectUrl("", state.currentProject),
        repairRequired: state.repairRequired
      };
    }
  }

  function redirectAfterCreate(detail) {
    try {
      var project = detail && detail.project ? detail.project : state.currentProject;
      var url = normalizeSafeRedirectUrl(detail && detail.redirectUrl, project);
      if (!url || url === DEFAULT_PROJECT_NEW_URL || !getProjectPublicId(project)) {
        return false;
      }

      setTimeout(function runRedirect() {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.location.assign(url);
          } else {
            window.location.assign(url);
          }
        } catch (error) {
          try {
            window.location.assign(url);
          } catch (_) {}
        }
      }, 250);
      return true;
    } catch (error) {
      return false;
    }
  }

  function blockedEditMessage() {
    return disabledReason() || "Du hast für dieses Projekt nur Leserechte.";
  }

  function guardCanMutate(actionName) {
    try {
      if (!canWriteProject()) {
        var message = blockedEditMessage();
        setAlert(state.publicViewer || state.readOnly ? "info" : "warning", message);

        emitParentEvent(EVENT_READONLY_BLOCKED, {
          action: actionName || "mutate",
          message: message,
          project: state.currentProject,
          publicViewer: state.publicViewer,
          readOnly: state.readOnly,
          demoMode: state.demoMode,
          persistent: state.persistent,
          authUnavailable: state.authUnavailable,
          userBlocked: state.userBlocked,
          accessBlocked: state.accessBlocked,
          accessMode: state.accessMode
        });

        return false;
      }

      return true;
    } catch (error) {
      return false;
    }
  }

  async function saveProject(options) {
    var wasNew = !!state.isNew;
    var saveOptions = isObject(options) ? options : {};
    var isAutoSave = !!saveOptions.autosave && !wasNew;

    try {
      if (!guardCanMutate("save")) {
        return false;
      }
      if (state.isSaving) {
        return false;
      }

      var payload = collectPayload();
      var validation = validatePayload(payload);
      if (!validation.ok) {
        setRootState("error", true);
        setAlert("error", validation.errors[0] && validation.errors[0].message ? validation.errors[0].message : "Bitte prüfe die Pflichtfelder.");
        return false;
      }

      setSaving(true);
      setRootState("error", false);
      if (!isAutoSave) {
        setAlert("info", wasNew ? "Projekt wird erstellt…" : "Projekt wird gespeichert…");
      }

      var method = wasNew ? "POST" : "PATCH";
      var url = wasNew
        ? normalizeMutationUrl(state.config.paths.createProject, DEFAULT_CREATE_PATH)
        : normalizeMutationUrl(buildUpdatePath(state.currentProject), "");
      if (!url) {
        var urlError = new Error("Sicherer Projekt-API-Pfad fehlt.");
        urlError.code = "unsafe_request_url";
        throw urlError;
      }

      var response = await requestJson(url, {
        method: method,
        body: safeJsonStringify(payload)
      });
      if (!response || response.ok === false) {
        var responseError = new Error(response && (response.error || response.message) ? response.error || response.message : "Speichern fehlgeschlagen.");
        responseError.payload = response || {};
        responseError.code = response && response.code ? response.code : "project_save_failed";
        throw responseError;
      }

      var detail = updateAfterSave(response, wasNew);
      var outcome = saveOutcome(response, wasNew);
      if (outcome.warning || !isAutoSave) {
        setAlert(outcome.warning ? "warning" : "success", outcome.message);
      } else {
        setAlert("", "");
      }
      if (wasNew && state.projectPersisted) {
        redirectAfterCreate(detail);
      }
      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      var errorPayload = isObject(error && error.payload) ? error.payload : {};

      if (isPersistedProjectResponse(errorPayload, normalized.status)) {
        var persistedDetail = updateAfterSave(errorPayload, wasNew);
        var persistedOutcome = saveOutcome(errorPayload, wasNew);
        state.lastError = normalized;
        setRootState("error", false);
        setAlert("warning", persistedOutcome.message || "Projekt wurde gespeichert; die externe Bereitstellung benötigt eine Reparatur.");
        if (wasNew && state.projectPersisted) {
          redirectAfterCreate(persistedDetail);
        }
        return true;
      }

      state.lastError = normalized;
      setRootState("error", true);
      setAlert("error", normalized.message || "Projekt konnte nicht gespeichert werden.");
      emitParentEvent(EVENT_ERROR, {
        error: sanitizeBrowserValue(normalized, 0),
        project: sanitizeProjectForBrowser(state.currentProject),
        requestId: errorPayload.request_id || errorPayload.requestId || state.requestId || "",
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        demoMode: state.demoMode,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        accessMode: state.accessMode,
        projectRole: state.projectRole
      });
      return false;
    } finally {
      setSaving(false);
      updateReadonlyState();
    }
  }

  function resetForm() {
    try {
      if (!guardCanMutate("reset")) {
        return false;
      }

      if (state.originalPayload) {
        var p = {
          id: state.config.projectId,
          public_id: state.config.projectPublicId,
          publicId: state.config.projectPublicId,
          name: state.originalPayload.name,
          description: state.originalPayload.description,
          cost_center: state.originalPayload.cost_center,
          costCenter: state.originalPayload.cost_center,
          address_text: state.originalPayload.address_text,
          addressText: state.originalPayload.address_text,
          address: {
            text: state.originalPayload.address_text
          },
          visibility: state.originalPayload.visibility,
          is_new: state.isNew,
          isNew: state.isNew
        };

        fillFormFromProject(p);
      } else {
        fillFormFromProject(state.config.project || {});
      }

      clearValidation();
      setAlert("", "");
      setDirty(false);
      return true;
    } catch (error) {
      return false;
    }
  }

  function onSubmit(event) {
    try {
      if (event && event.preventDefault) {
        event.preventDefault();
      }

      if (state.autoSaveTimer !== null) {
        clearTimeout(state.autoSaveTimer);
        state.autoSaveTimer = null;
      }
      void saveProject();
    } catch (error) {}
  }

  function onResetClick(event) {
    try {
      if (event && event.preventDefault) {
        event.preventDefault();
      }

      resetForm();
    } catch (error) {}
  }

  function onVisibilityOptionClick(event) {
    try {
      if (event && event.preventDefault) {
        event.preventDefault();
      }

      var target = event && event.currentTarget ? event.currentTarget : null;
      if (!target) {
        return;
      }

      if (!canWriteProject() || target.disabled) {
        if (disabledReason()) {
          setAlert("info", disabledReason());
        }
        return;
      }

      setVisibility(target.getAttribute("data-value") || target.getAttribute("data-visibility") || "private");
    } catch (error) {}
  }

  function onVisibilityOptionKeydown(event) {
    try {
      if (!event) {
        return;
      }

      var key = event.key || "";
      if (key !== "Enter" && key !== " ") {
        return;
      }

      onVisibilityOptionClick(event);
    } catch (error) {}
  }

  function onVisibilityInputChange() {
    try {
      if (!canWriteProject()) {
        syncVisibilityCards(getValue(state.refs.visibility));
        if (disabledReason()) {
          setAlert("info", disabledReason());
        }
        return;
      }

      setVisibility(getValue(state.refs.visibility));
    } catch (error) {}
  }

  function onExternalVisibilityChanged(event) {
    try {
      var detail = event && event.detail ? event.detail : {};
      var visibility = normalizeVisibility(detail.visibility || detail.value || getValue(state.refs.visibility), "private");

      setValue(state.refs.visibility, visibility);
      syncVisibilityCards(visibility);

      if (detail && detail.source === "project_form") {
        return;
      }

      if (canWriteProject()) {
        markDirtyFromInput();
      }
    } catch (error) {}
  }

  function wireEvents() {
    try {
      var refs = state.refs || {};
      addListener(refs.form, "submit", onSubmit);
      addListener(refs.reset, "click", onResetClick);

      [refs.name, refs.description, refs.costCenter, refs.addressText].forEach(function wireInput(element) {
        addListener(element, "input", function onInput() {
          if (!canWriteProject()) { return; }
          removeFieldError(element);
          markDirtyFromInput();
        });
        addListener(element, "change", function onChange() {
          if (!canWriteProject()) { return; }
          removeFieldError(element);
          markDirtyFromInput();
        });
      });
      addListener(refs.addressText, "input", function onAddressGeocoderInput() {
        setValue(refs.addressMapboxId, "");
        updateAddressCounter();
        scheduleGeocoderSearch();
      });
      addListener(refs.addressText, "keydown", onGeocoderKeydown);
      addListener(refs.addressText, "focus", function onAddressGeocoderFocus() {
        if (getValue(refs.addressText).length >= 3) {
          scheduleGeocoderSearch();
        }
      });
      addListener(refs.addressText, "blur", function onAddressGeocoderBlur() {
        setTimeout(closeGeocoderSuggestions, 140);
        flushAutoSave();
      });

      addListener(refs.visibility, "change", onVisibilityInputChange);
      addListener(refs.visibility, "input", onVisibilityInputChange);
      (refs.visibilityOptions || []).forEach(function wireOption(option) {
        addListener(option, "click", onVisibilityOptionClick);
        addListener(option, "keydown", onVisibilityOptionKeydown);
      });
      if (refs.root) {
        addListener(refs.root, EVENT_VISIBILITY_CHANGED, onExternalVisibilityChanged);
      }

      addListener(window, "message", function onMessage(event) {
        try {
          if (!isTrustedMessageEvent(event)) {
            return;
          }
          var data = event && event.data;
          if (!data || typeof data !== "object") {
            return;
          }
          var type = trimString(data.type || data.kind, "").toLowerCase();
          if (type === EVENT_VISIBILITY_CHANGED) {
            onExternalVisibilityChanged({ detail: data.detail || data });
          }
        } catch (error) {}
      });
    } catch (error) {}
  }

  function setFormControlsReadonly(isReadonly) {
    try {
      var refs = state.refs || {};
      var readonly = !!isReadonly;
      var disabled = readonly || !canWriteProject();

      [refs.name, refs.description, refs.costCenter, refs.addressText].forEach(function syncControl(control) {
        try {
          if (!control) { return; }
          control.disabled = disabled;
          if (disabled) {
            control.setAttribute("aria-readonly", "true");
            control.setAttribute("data-readonly", "true");
          } else {
            control.removeAttribute("aria-readonly");
            control.removeAttribute("data-readonly");
          }
        } catch (error) {}
      });

      if (refs.visibility) {
        refs.visibility.disabled = disabled;
        refs.visibility.setAttribute("aria-disabled", disabled ? "true" : "false");
        refs.visibility.setAttribute("data-readonly", disabled ? "true" : "false");
      }

      (refs.visibilityOptions || []).forEach(function syncOption(option) {
        try {
          option.disabled = disabled;
          option.setAttribute("aria-disabled", disabled ? "true" : "false");
          option.classList.toggle(CLASS_READONLY, disabled);
          option.classList.toggle(CLASS_DISABLED, disabled);
          option.setAttribute("tabindex", disabled ? "-1" : "0");
          if (disabled && disabledReason()) {
            option.setAttribute("title", disabledReason());
          } else {
            option.removeAttribute("title");
          }
        } catch (error) {}
      });
    } catch (error) {}
  }

  function updateReadonlyState() {
    try {
      var readonly = state.readOnly ||
        state.publicViewer ||
        state.authUnavailable ||
        state.userBlocked ||
        state.accessBlocked ||
        !state.canEdit ||
        !state.canMutate;

      setRootState("readonly", readonly);
      setRootState("publicViewer", state.publicViewer);
      setRootState("demoMode", state.demoMode);
      setRootState("authUnavailable", state.authUnavailable);
      setRootState("userBlocked", state.userBlocked);
      setRootState("accessBlocked", state.accessBlocked);
      setRootState("identityConsistent", state.identityConsistent);
      setRootState("repairRequired", state.repairRequired);

      setFormControlsReadonly(readonly);

      if (state.refs.submit) {
        state.refs.submit.disabled = state.isSaving || !canWriteProject();
        state.refs.submit.setAttribute("aria-disabled", state.refs.submit.disabled ? "true" : "false");
        if (state.refs.submit.disabled && disabledReason()) {
          state.refs.submit.setAttribute("title", disabledReason());
        } else {
          state.refs.submit.removeAttribute("title");
        }
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = state.isSaving || !canWriteProject();
        state.refs.reset.setAttribute("aria-disabled", state.refs.reset.disabled ? "true" : "false");
      }

      if (readonly && disabledReason()) {
        setAlert(state.publicViewer || state.readOnly ? "info" : "warning", disabledReason());
      }

      syncVisibilityCards(getValue(state.refs.visibility) || state.config.projectVisibility);
    } catch (error) {}
  }

  function initStateFromConfig() {
    try {
      state.isNew = toBooleanSafe(state.config.isNew, true);
      state.demoMode = toBooleanSafe(state.config.demoMode, false);
      state.publicViewer = toBooleanSafe(state.config.publicViewer || state.config.isPublicViewer, false);
      state.authUnavailable = toBooleanSafe(state.config.authUnavailable, false);
      state.userBlocked = toBooleanSafe(state.config.userBlocked, false);
      state.accessBlocked = toBooleanSafe(state.config.accessBlocked, false);
      state.projectRole = normalizeProjectRole(state.config.projectRole || state.config.project_role, state.isNew ? "owner" : "viewer");
      state.identityConsistent = toBooleanSafe(state.config.identityConsistent, true);
      state.localLinkState = trimString(state.config.localLinkState, state.demoMode || state.publicViewer ? "not_applicable" : "linked").toLowerCase();
      state.principalType = trimString(state.config.principalType, state.demoMode ? "demo_guest" : state.publicViewer ? "public_viewer" : "user").toLowerCase();

      var identityMismatch = !state.identityConsistent || state.localLinkState === "identity_mismatch" || state.localLinkState === "inactive";
      state.readOnly = toBooleanSafe(
        state.config.readOnly !== undefined ? state.config.readOnly : state.config.readonly,
        state.publicViewer || state.projectRole === "viewer" || state.authUnavailable || state.userBlocked || state.accessBlocked || identityMismatch
      );
      state.accessMode = normalizeAccessMode(
        state.config.accessMode,
        state.authUnavailable ? "auth_unavailable"
          : identityMismatch ? "identity_mismatch"
          : state.userBlocked || state.accessBlocked ? "blocked"
          : state.publicViewer ? "public"
          : state.demoMode ? "demo"
          : "anonymous"
      );

      if (state.publicViewer || state.projectRole === "viewer") {
        state.readOnly = true;
        if (state.publicViewer) {
          state.demoMode = false;
          state.accessMode = "public";
        }
      }
      if (state.authUnavailable) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "auth_unavailable";
      }
      if (identityMismatch) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "identity_mismatch";
      }
      if (state.userBlocked || state.accessBlocked) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "blocked";
      }

      state.authenticated = toBooleanSafe(
        state.config.authenticated,
        !state.demoMode && !state.publicViewer && !state.authUnavailable && !state.userBlocked && !state.accessBlocked && !identityMismatch
      );
      state.persistent = toBooleanSafe(
        state.config.persistent,
        state.authenticated && !state.demoMode && !state.publicViewer && !state.authUnavailable && !state.userBlocked && !state.accessBlocked && !identityMismatch
      );
      if (state.demoMode || state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked || identityMismatch) {
        state.persistent = false;
      }

      var roleCanEdit = state.isNew ? state.projectRole === "owner" : !!ROLE_CAN_EDIT[state.projectRole];
      var roleCanManage = state.isNew ? state.projectRole === "owner" : !!ROLE_CAN_MANAGE[state.projectRole];
      state.canEdit = toBooleanSafe(state.config.canEdit, false) && roleCanEdit && !state.readOnly && !state.publicViewer && !state.authUnavailable && !state.userBlocked && !state.accessBlocked && !identityMismatch && (state.persistent || state.demoMode);
      state.canManage = toBooleanSafe(state.config.canManage, false) && roleCanManage && !state.publicViewer && !state.readOnly && !state.demoMode && !state.authUnavailable && !state.userBlocked && !state.accessBlocked && !identityMismatch;
      state.canMutate = toBooleanSafe(state.config.canMutate, state.canEdit) && state.canEdit;
      state.currentProject = sanitizeProjectForBrowser(state.config.project || {});
      applyLifecycleState(state.currentProject, state.config);
    } catch (error) {
      state.readOnly = true;
      state.canEdit = false;
      state.canManage = false;
      state.canMutate = false;
      state.identityConsistent = false;
    }
  }

  function init() {
    if (state.initialized) {
      return state;
    }

    try {
      state.config = getConfig();
      state.refs = queryRefs();

      initStateFromConfig();

      if (!state.refs.root || !state.refs.form) {
        return state;
      }


      fillFormFromProject(Object.assign({}, state.currentProject || {}, {
        visibility: state.config.projectVisibility || (state.currentProject && state.currentProject.visibility) || "private",
        is_new: state.isNew,
        isNew: state.isNew
      }));

      updateReadonlyState();
      wireEvents();
      updateAddressCounter();
      syncVisibilityCards(getValue(state.refs.visibility) || state.config.projectVisibility);

      state.initialized = true;

      dispatchLocal(EVENT_READY, {
        project: sanitizeProjectForBrowser(state.currentProject),
        isNew: state.isNew,
        canEdit: state.canEdit,
        canManage: state.canManage,
        canMutate: state.canMutate,
        demoMode: state.demoMode,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        persistent: state.persistent,
        authenticated: state.authenticated,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        accessMode: state.accessMode,
        projectRole: state.projectRole,
        identityConsistent: state.identityConsistent,
        localLinkState: state.localLinkState,
        chunkReady: state.chunkReady,
        chunkProvisioningStatus: state.chunkProvisioningStatus,
        chunkAccessSyncStatus: state.chunkAccessSyncStatus,
        repairRequired: state.repairRequired,
        canWrite: canWriteProject()
      });

      try {
        window.__VECTOPLAN_PROJECT_FORM_STATE__ = getSnapshot();
      } catch (_) {}

      return state;
    } catch (error) {
      state.lastError = normalizeError(error);
      setAlert("error", "Projektformular konnte nicht initialisiert werden.");
      return state;
    }
  }

  function destroy() {
    try {
      if (state.geocoder.timer) {
        clearTimeout(state.geocoder.timer);
      }
      if (state.geocoder.controller && typeof state.geocoder.controller.abort === "function") {
        state.geocoder.controller.abort();
      }
      closeGeocoderSuggestions();
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
        isNew: state.isNew,
        canEdit: state.canEdit,
        canManage: state.canManage,
        canMutate: state.canMutate,
        canWrite: canWriteProject(),
        disabledReason: disabledReason(),
        demoMode: state.demoMode,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        persistent: state.persistent,
        authenticated: state.authenticated,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        accessMode: state.accessMode,
        projectRole: state.projectRole,
        identityConsistent: state.identityConsistent,
        localLinkState: state.localLinkState,
        principalType: state.principalType,
        projectPersisted: state.projectPersisted,
        chunkReady: state.chunkReady,
        chunkProvisioningStatus: state.chunkProvisioningStatus,
        chunkAccessSyncStatus: state.chunkAccessSyncStatus,
        chunkAccessReady: state.chunkAccessReady,
        chunkFallbackUsed: state.chunkFallbackUsed,
        worldTemplateRequested: state.chunkWorldTemplateRequested,
        worldTemplateEffective: state.chunkWorldTemplateEffective,
        repairRequired: state.repairRequired,
        requestId: state.requestId,
        lastResponseStatus: state.lastResponseStatus,
        isDirty: state.isDirty,
        isSaving: state.isSaving,
        isLoading: state.isLoading,
        lastSavedAt: state.lastSavedAt,
        lastError: sanitizeBrowserValue(state.lastError, 0),
        currentProject: sanitizeProjectForBrowser(state.currentProject),
        config: {
          version: state.config && state.config.version,
          projectPublicId: state.config && state.config.projectPublicId,
          projectVisibility: state.config && state.config.projectVisibility,
          paths: state.config && state.config.paths
        },
        payload: collectPayload()
      }, 0);
    } catch (error) {
      return { version: INTERNAL_VERSION, initialized: false, error: sanitizeBrowserValue(normalizeError(error), 0) };
    }
  }

  var api = {
    version: INTERNAL_VERSION,
    init: init,
    destroy: destroy,
    save: saveProject,
    reset: resetForm,
    collectPayload: collectPayload,
    validatePayload: validatePayload,
    getSnapshot: getSnapshot,
    setAlert: setAlert,
    setVisibility: setVisibility,
    canWrite: canWriteProject,
    disabledReason: disabledReason,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      fillFormFromProject: fillFormFromProject,
      updateAfterSave: updateAfterSave,
      emitParentEvent: emitParentEvent,
      requestJson: requestJson,
      normalizeError: normalizeError,
      normalizeVisibility: normalizeVisibility,
      guardCanMutate: guardCanMutate,
      blockedEditMessage: blockedEditMessage,
      canWriteProject: canWriteProject,
      disabledReason: disabledReason,
      normalizeMutationUrl: normalizeMutationUrl,
      normalizeSafeRedirectUrl: normalizeSafeRedirectUrl,
      sanitizeProjectForBrowser: sanitizeProjectForBrowser,
      sanitizeCurrentUserForBrowser: sanitizeCurrentUserForBrowser,
      sanitizeResponseForBrowser: sanitizeResponseForBrowser,
      lifecycleStateFrom: lifecycleStateFrom,
      isPersistedProjectResponse: isPersistedProjectResponse,
      normalizeProjectRole: normalizeProjectRole,
      isTrustedMessageEvent: isTrustedMessageEvent
    }
  };

  try {
    global[EXPORT_NAME] = api;
    global[LEGACY_EXPORT_NAME] = api;

    if (global.__VECTOPLAN_DEBUG__ === true) {
      global.__VECTOPLAN_DEBUG__ = { enabled: true, projectForm: api };
    } else if (isObject(global.__VECTOPLAN_DEBUG__) && global.__VECTOPLAN_DEBUG__.enabled === true) {
      global.__VECTOPLAN_DEBUG__.projectForm = api;
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
