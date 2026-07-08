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
  var INTERNAL_VERSION = 5;

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

    isDirty: false,
    isSaving: false,
    isLoading: false,
    lastSavedAt: null,
    lastError: null,
    originalPayload: null,
    currentProject: null,
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

      if (text === "blocked" || text === "banned" || text === "access_blocked") {
        return "blocked";
      }

      if (
        text === "public" ||
        text === "demo" ||
        text === "authenticated" ||
        text === "anonymous" ||
        text === "auth_unavailable" ||
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
      var config =
        win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG ||
        win.PROJECT_WORKSPACE_CONFIG ||
        {};

      if (!isObject(config)) {
        config = {};
      }

      var root = query(ROOT_SELECTOR);
      var form = query(FORM_SELECTOR);

      var paths = isObject(config.paths) ? config.paths : {};
      var parentEvents = isObject(config.parentEvents) ? config.parentEvents : {};
      var project = isObject(config.project) ? config.project : {};
      var currentProject = isObject(config.currentProject) ? config.currentProject : project;
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};
      var access = isObject(config.access) ? config.access : isObject(project.access) ? project.access : {};
      var workspaceAccess = isObject(config.workspaceAccess) ? config.workspaceAccess : isObject(project.workspace_access) ? project.workspace_access : {};

      var accessMode = normalizeAccessMode(
        pickString(
          [
            config.accessMode,
            config.access_mode,
            access.accessMode,
            access.access_mode,
            workspaceAccess.accessMode,
            workspaceAccess.access_mode,
            project.accessMode,
            project.access_mode,
            attr(root, "data-project-access-mode", "")
          ],
          ""
        ),
        ""
      );

      var authUnavailable = pickBoolean(
        [
          config.authUnavailable,
          config.auth_unavailable,
          currentUser.auth_unavailable,
          currentUser.authUnavailable,
          access.auth_unavailable,
          access.authUnavailable,
          attr(root, "data-project-auth-unavailable", ""),
          attr(form, "data-project-form-auth-unavailable", "")
        ],
        accessMode === "auth_unavailable"
      );

      var userBlocked = pickBoolean(
        [
          config.userBlocked,
          config.user_blocked,
          currentUser.user_blocked,
          currentUser.userBlocked,
          access.user_blocked,
          access.userBlocked,
          attr(root, "data-project-user-blocked", ""),
          attr(form, "data-project-form-user-blocked", "")
        ],
        false
      );

      var accessBlocked = pickBoolean(
        [
          config.accessBlocked,
          config.access_blocked,
          currentUser.access_blocked,
          currentUser.accessBlocked,
          access.access_blocked,
          access.accessBlocked,
          attr(root, "data-project-access-blocked", ""),
          attr(form, "data-project-form-access-blocked", "")
        ],
        accessMode === "blocked"
      );

      var publicViewer = pickBoolean(
        [
          config.publicViewer,
          config.isPublicViewer,
          config.public_viewer,
          access.publicViewer,
          access.public_viewer,
          access.isPublicViewer,
          access.is_public_viewer,
          workspaceAccess.publicViewer,
          workspaceAccess.public_viewer,
          project.publicViewer,
          project.public_viewer,
          attr(root, "data-project-public-viewer", ""),
          attr(form, "data-project-form-public-viewer", "")
        ],
        accessMode === "public"
      );

      if (publicViewer) {
        accessMode = "public";
      }

      var readOnly = pickBoolean(
        [
          config.readOnly,
          config.readonly,
          config.read_only,
          access.readOnly,
          access.readonly,
          access.read_only,
          workspaceAccess.readOnly,
          workspaceAccess.read_only,
          project.readOnly,
          project.readonly,
          project.read_only,
          attr(root, "data-project-read-only", ""),
          attr(form, "data-project-form-readonly", "")
        ],
        publicViewer || authUnavailable || userBlocked || accessBlocked
      );

      if (publicViewer || authUnavailable || userBlocked || accessBlocked) {
        readOnly = true;
      }

      var demoMode = pickBoolean(
        [
          config.demoMode,
          config.demo_mode,
          currentUser.demo_mode,
          currentUser.demoMode,
          currentUser.is_demo,
          project.demo_mode,
          project.demoMode,
          attr(root, "data-project-demo-mode", ""),
          attr(form, "data-project-form-demo-mode", "")
        ],
        false
      );

      if (publicViewer || authUnavailable || userBlocked || accessBlocked) {
        demoMode = false;
      }

      if (!accessMode) {
        accessMode = authUnavailable
          ? "auth_unavailable"
          : userBlocked || accessBlocked
            ? "blocked"
            : demoMode
              ? "demo"
              : publicViewer
                ? "public"
                : "";
      }

      var authenticated = pickBoolean(
        [
          config.authenticated,
          currentUser.authenticated,
          currentUser.is_authenticated,
          currentUser.isAuthenticated
        ],
        !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked
      );

      var persistent = pickBoolean(
        [
          config.persistent,
          currentUser.persistent,
          attr(root, "data-project-persistent", "")
        ],
        authenticated && !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked
      );

      if (publicViewer || demoMode || authUnavailable || userBlocked || accessBlocked) {
        persistent = false;
      }

      var isNew = pickBoolean(
        [
          config.isNew,
          config.is_new,
          project.is_new,
          project.isNew,
          attr(root, "data-project-is-new", "")
        ],
        true
      );

      var templateCanEdit = pickBoolean(
        [
          config.canEdit,
          config.can_edit,
          config.canMutate,
          config.can_mutate,
          access.canEdit,
          access.can_edit,
          access.canMutate,
          access.can_mutate,
          attr(root, "data-project-can-edit", ""),
          attr(form, "data-project-form-can-edit", "")
        ],
        false
      );

      var canEdit = !!templateCanEdit &&
        !readOnly &&
        !publicViewer &&
        !authUnavailable &&
        !userBlocked &&
        !accessBlocked &&
        (persistent || demoMode);

      var canManage = pickBoolean(
        [
          config.canManage,
          config.can_manage,
          access.canManage,
          access.can_manage,
          attr(root, "data-project-can-manage", "")
        ],
        false
      ) && !publicViewer && !readOnly && !demoMode && !authUnavailable && !userBlocked && !accessBlocked;

      var canMutate = pickBoolean(
        [
          config.canMutate,
          config.can_mutate,
          access.canMutate,
          access.can_mutate,
          attr(root, "data-project-can-mutate", ""),
          attr(form, "data-project-form-can-edit", "")
        ],
        canEdit
      ) && canEdit;

      var projectPublicId = pickString(
        [
          config.projectPublicId,
          config.project_public_id,
          project.public_id,
          project.publicId,
          project.project_public_id,
          project.projectPublicId,
          attr(root, "data-project-public-id", "")
        ],
        isNew ? "new" : ""
      );

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

        project: project,
        currentProject: currentProject,
        currentUser: currentUser,
        access: access,
        workspaceAccess: workspaceAccess,

        projectId: pickString(
          [
            config.projectId,
            config.project_id,
            project.id,
            project.project_id,
            project.projectId,
            attr(root, "data-project-id", "")
          ],
          ""
        ),
        projectPublicId: projectPublicId,
        projectVisibility: normalizeVisibility(config.projectVisibility || config.project_visibility || project.visibility || attr(root, "data-project-visibility", ""), "private"),

        paths: {
          createProject: trimString(paths.createProject || paths.create_project, DEFAULT_CREATE_PATH),
          updateProject: trimString(paths.updateProject || paths.update_project, ""),
          getProject: trimString(paths.getProject || paths.get_project, ""),
          context: trimString(paths.context, isNew ? DEFAULT_PROJECT_CONTEXT_NEW : ""),
          workspaceAccess: trimString(paths.workspaceAccess || paths.workspace_access, ""),
          publication: canManage ? trimString(paths.publication || paths.projectPublication || paths.project_publication, "") : "",
          members: canManage ? trimString(paths.members, "") : "",
          invitations: canManage ? trimString(paths.invitations, "") : "",
          projectRoot: trimString(paths.projectRoot || paths.project_root, DEFAULT_PROJECT_ROOT_URL),
          projectNew: trimString(paths.projectNew || paths.project_new, DEFAULT_PROJECT_NEW_URL)
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
      addressText: queryById("projectAddressText"),

      visibility: queryById("projectVisibility"),
      visibilityOptions: queryAll("[data-project-visibility-option]"),
      visibilityCard: query("[data-project-visibility-card]"),
      visibilityCurrentLabel: query("[data-project-visibility-current-label]"),
      visibilityStatus: query("[data-project-visibility-status]"),
      visibilityHelp: query("[data-project-visibility-help]"),

      addressCounter: query("[data-project-address-counter]"),
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
      }
    } catch (error) {}
  }

  function canWriteProject() {
    try {
      return !!(
        !state.isSaving &&
        !state.isLoading &&
        state.canEdit &&
        state.canMutate &&
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

      if (state.userBlocked || state.accessBlocked) {
        return "Der Zugriff ist gesperrt. Projekt kann nicht gespeichert werden.";
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
        address_text: addressText,
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
      var p = isObject(project) ? project : {};
      var address = isObject(p.address) ? p.address : {};

      var publicId = pickString(
        [
          p.public_id,
          p.publicId,
          p.project_public_id,
          p.projectPublicId
        ],
        ""
      );

      var isNew = toBooleanSafe(p.is_new || p.isNew, false) || !trimString(publicId, "");

      setValue(refs.projectId, p.id || p.project_id || p.projectId || "");
      setValue(refs.projectPublicId, publicId);
      setValue(refs.projectIsNew, isNew ? "true" : "false");
      setValue(refs.projectAccessMode, state.accessMode || "");

      setValue(refs.name, p.name || p.display_name || p.displayName || "");
      setValue(refs.description, p.description || "");
      setValue(refs.addressText, p.address_text || p.addressText || address.text || "");

      setVisibility(p.visibility || state.config.projectVisibility || "private", { silent: true });

      state.currentProject = safeClone(p);
      state.isNew = isNew;
      state.originalPayload = collectPayload();

      setConfigured(
        toBooleanSafe(p.is_configured || p.isConfigured, false),
        p.setup_status || p.setupStatus || "draft"
      );

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
      var configuredPath = trimString(state.config.paths.updateProject, "");
      var apiId = getProjectApiId(project || state.currentProject);

      if (configuredPath) {
        return configuredPath;
      }

      if (apiId && apiId !== "new") {
        return "/v1/projects/" + encodeURIComponent(apiId);
      }

      return DEFAULT_CREATE_PATH;
    } catch (error) {
      return DEFAULT_CREATE_PATH;
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

      var payload = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-form",
        version: INTERNAL_VERSION,
        detail: detail || {},
        project: detail && detail.project ? detail.project : state.currentProject,
        ts: Date.now()
      };

      try {
        window.parent.postMessage(payload, window.location.origin);
        return true;
      } catch (postError) {
        try {
          window.parent.postMessage(payload, "*");
          return true;
        } catch (fallbackError) {
          return false;
        }
      }
    } catch (error) {
      return false;
    }
  }

  function dispatchParentEvent(type, detail) {
    try {
      if (
        window.parent &&
        window.parent !== window &&
        window.parent.dispatchEvent &&
        typeof window.parent.CustomEvent === "function"
      ) {
        window.parent.dispatchEvent(
          new window.parent.CustomEvent(type, {
            detail: detail || {}
          })
        );
        return true;
      }
    } catch (error) {}

    return false;
  }

  function emitParentEvent(type, detail) {
    try {
      var payloadDetail = detail || {};

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
    try {
      var target = trimString(url, "");

      if (!target) {
        throw new Error("request URL missing");
      }

      var opts = isObject(options) ? options : {};
      var response = await fetch(target, {
        method: opts.method || "GET",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-Requested-With": "fetch",
          "X-VECTOPLAN-Client": "project_form.js"
        },
        credentials: "same-origin",
        cache: "no-store",
        body: opts.body !== undefined ? opts.body : undefined
      });

      var text = await response.text();
      var data = safeJsonParse(text, null);

      if (!response.ok) {
        var message = data && (data.error || data.message)
          ? data.error || data.message
          : "Request failed with status " + response.status;

        var error = new Error(message);
        error.status = response.status;
        error.statusCode = response.status;
        error.payload = data;
        error.code = data && data.code ? data.code : "request_failed";
        throw error;
      }

      if (data === null) {
        return {};
      }

      return data;
    } catch (error) {
      throw error;
    }
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
      state.config.paths.updateProject = "/v1/projects/" + encodeURIComponent(publicId);
      state.config.paths.getProject = "/v1/projects/" + encodeURIComponent(publicId);
      state.config.paths.context = "/ui/project/" + encodeURIComponent(publicId) + "/context.json";
      state.config.paths.workspaceAccess = "/v1/projects/" + encodeURIComponent(publicId) + "/workspace-access/project";

      if (state.config.canManage) {
        state.config.paths.publication = "/v1/projects/" + encodeURIComponent(publicId) + "/publication";
        state.config.paths.members = "/v1/projects/" + encodeURIComponent(publicId) + "/members";
        state.config.paths.invitations = "/v1/projects/" + encodeURIComponent(publicId) + "/invitations";
      }
    } catch (error) {}
  }

  function refreshGlobalConfigFromState() {
    try {
      var win = getWindow();
      var config = win.VECTOPLAN_PROJECT_WORKSPACE_CONFIG || win.PROJECT_WORKSPACE_CONFIG || state.config || {};

      config.project = state.currentProject || config.project || {};
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
      var project = payload && isObject(payload.project) ? payload.project : null;

      if (!project && payload && isObject(payload.item)) {
        project = payload.item;
      }

      if (!project && payload && isObject(payload.data) && isObject(payload.data.project)) {
        project = payload.data.project;
      }

      if (!project) {
        project = state.currentProject || {};
      }

      state.currentProject = safeClone(project);
      state.isNew = false;
      state.lastSavedAt = nowIso();
      state.lastError = null;

      updateConfigPathsFromProject(project);

      fillFormFromProject({
        id: project.id,
        project_id: project.project_id || project.projectId,
        projectId: project.projectId || project.project_id,
        public_id: project.public_id || project.publicId || project.project_public_id || project.projectPublicId,
        publicId: project.publicId || project.public_id || project.projectPublicId || project.project_public_id,
        name: project.name,
        display_name: project.display_name || project.displayName,
        displayName: project.displayName || project.display_name,
        description: project.description,
        address_text: project.address_text || project.addressText,
        addressText: project.addressText || project.address_text,
        address: isObject(project.address) ? project.address : {},
        visibility: project.visibility || getValue(state.refs.visibility),
        is_configured: project.is_configured || project.isConfigured,
        isConfigured: project.isConfigured || project.is_configured,
        setup_status: project.setup_status || project.setupStatus,
        setupStatus: project.setupStatus || project.setup_status,
        is_new: false,
        isNew: false
      });

      state.isNew = false;

      if (state.refs.projectIsNew) {
        setValue(state.refs.projectIsNew, "false");
      }

      if (state.refs.root) {
        state.refs.root.setAttribute("data-project-is-new", "false");
        state.refs.root.setAttribute("data-project-public-id", getProjectPublicId(project));
      }

      setDirty(false);
      setRootState("error", false);
      setRootState("saved", true);

      var configured = isProjectConfigured(project);
      setConfigured(configured, configured ? "configured" : (project.setup_status || project.setupStatus || "draft"));

      refreshGlobalConfigFromState();

      var detail = {
        project: project,
        payload: payload || {},
        isNew: !!wasNew,
        is_new: !!wasNew,
        isConfigured: configured,
        is_configured: configured,
        redirectUrl: payload && payload.redirect_url ? payload.redirect_url : buildProjectUrl(project),
        savedAt: state.lastSavedAt,
        demoMode: state.demoMode,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        accessMode: state.accessMode,
        visibility: normalizeVisibility(project.visibility || getValue(state.refs.visibility), "private")
      };

      emitParentEvent(state.config.parentEvents.saved || EVENT_SAVED, detail);

      if (wasNew) {
        emitParentEvent(state.config.parentEvents.created || EVENT_CREATED, detail);
      } else {
        emitParentEvent(state.config.parentEvents.updated || EVENT_UPDATED, detail);
      }

      if (configured) {
        emitParentEvent(state.config.parentEvents.configured || EVENT_CONFIGURED, detail);
      }

      emitParentEvent(EVENT_PUBLICATION_REFRESH, detail);

      return detail;
    } catch (error) {
      return {
        project: state.currentProject,
        isNew: !!wasNew,
        redirectUrl: DEFAULT_PROJECT_ROOT_URL
      };
    }
  }

  function redirectAfterCreate(detail) {
    try {
      var url = trimString(detail && detail.redirectUrl, "");

      if (!url && detail && detail.project) {
        url = buildProjectUrl(detail.project);
      }

      if (!url || url === DEFAULT_PROJECT_NEW_URL) {
        return false;
      }

      setTimeout(function runRedirect() {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.location.href = url;
          } else {
            window.location.href = url;
          }
        } catch (error) {
          try {
            window.top.location.href = url;
          } catch (_) {
            window.location.href = url;
          }
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

  async function saveProject() {
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
        setAlert("error", validation.errors[0] && validation.errors[0].message
          ? validation.errors[0].message
          : "Bitte prüfe die Pflichtfelder.");
        return false;
      }

      setSaving(true);
      setRootState("error", false);
      setAlert("info", state.isNew ? "Projekt wird erstellt…" : "Projekt wird gespeichert…");

      var wasNew = !!state.isNew;
      var method = wasNew ? "POST" : "PATCH";
      var url = wasNew
        ? trimString(state.config.paths.createProject, DEFAULT_CREATE_PATH)
        : buildUpdatePath(state.currentProject);

      var response = await requestJson(url, {
        method: method,
        body: safeJsonStringify(payload)
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Speichern fehlgeschlagen.");
      }

      var detail = updateAfterSave(response, wasNew);

      setAlert(
        "success",
        wasNew
          ? (state.demoMode ? "Demo-Projekt wurde temporär erstellt. Die Projektansicht wird geöffnet…" : "Projekt wurde erstellt. Die Projektansicht wird geöffnet…")
          : (state.demoMode ? "Demo-Projekt wurde temporär gespeichert." : "Projekt wurde gespeichert.")
      );

      if (wasNew) {
        redirectAfterCreate(detail);
      }

      return true;
    } catch (error) {
      var normalized = normalizeError(error);

      state.lastError = normalized;
      setRootState("error", true);

      setAlert("error", normalized.message || "Projekt konnte nicht gespeichert werden.");

      emitParentEvent(EVENT_ERROR, {
        error: normalized,
        project: state.currentProject,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        demoMode: state.demoMode,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        accessMode: state.accessMode
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

      [
        refs.name,
        refs.description,
        refs.addressText
      ].forEach(function wireInput(element) {
        addListener(element, "input", function onInput() {
          if (!canWriteProject()) {
            return;
          }
          removeFieldError(element);
          markDirtyFromInput();
        });

        addListener(element, "change", function onChange() {
          if (!canWriteProject()) {
            return;
          }
          removeFieldError(element);
          markDirtyFromInput();
        });
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
          var data = event && event.data;

          if (!data || typeof data !== "object") {
            return;
          }

          var type = trimString(data.type || data.kind, "").toLowerCase();

          if (type === "vectoplan:theme:update" && data.theme) {
            applyTheme(data.theme);
          }

          if (type === EVENT_VISIBILITY_CHANGED) {
            onExternalVisibilityChanged({ detail: data.detail || data });
          }
        } catch (error) {}
      });
    } catch (error) {}
  }

  function applyTheme(theme) {
    try {
      var normalized = trimString(theme, "").toLowerCase();

      if (normalized !== "dark" && normalized !== "light") {
        return false;
      }

      document.documentElement.setAttribute("data-theme", normalized);

      try {
        localStorage.setItem("theme", normalized);
      } catch (_) {}

      return true;
    } catch (error) {
      return false;
    }
  }

  function syncTheme() {
    try {
      var saved = "";

      try {
        saved = localStorage.getItem("theme") || "";
      } catch (_) {
        saved = "";
      }

      if (saved === "dark" || saved === "light") {
        applyTheme(saved);
        return;
      }

      try {
        if (window.parent && window.parent !== window) {
          var parentTheme = window.parent.document.documentElement.getAttribute("data-theme");
          if (parentTheme === "dark" || parentTheme === "light") {
            applyTheme(parentTheme);
          }
        }
      } catch (_) {}
    } catch (error) {}
  }

  function setFormControlsReadonly(isReadonly) {
    try {
      var refs = state.refs || {};
      var readonly = !!isReadonly;
      var disabled = readonly || !canWriteProject();

      [
        refs.name,
        refs.description,
        refs.addressText
      ].forEach(function syncControl(control) {
        try {
          if (!control) {
            return;
          }

          control.disabled = disabled;

          if (readonly) {
            control.setAttribute("aria-readonly", "true");
            control.setAttribute("data-readonly", "true");
          } else {
            control.removeAttribute("aria-readonly");
            control.removeAttribute("data-readonly");
          }
        } catch (error) {}
      });

      if (refs.visibility) {
        refs.visibility.disabled = false;
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

      if (readonly) {
        if (disabledReason()) {
          setAlert(state.publicViewer || state.readOnly ? "info" : "warning", disabledReason());
        }
      } else {
        setAlert("", "");
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

      state.readOnly = toBooleanSafe(
        state.config.readOnly || state.config.readonly,
        state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked
      );

      state.accessMode = normalizeAccessMode(
        state.config.accessMode,
        state.authUnavailable
          ? "auth_unavailable"
          : state.userBlocked || state.accessBlocked
            ? "blocked"
            : state.publicViewer
              ? "public"
              : state.demoMode
                ? "demo"
                : "anonymous"
      );

      if (state.publicViewer) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "public";
      }

      if (state.authUnavailable) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "auth_unavailable";
      }

      if (state.userBlocked || state.accessBlocked) {
        state.demoMode = false;
        state.readOnly = true;
        state.accessMode = "blocked";
      }

      state.authenticated = toBooleanSafe(
        state.config.authenticated,
        !state.demoMode && !state.publicViewer && !state.authUnavailable && !state.userBlocked && !state.accessBlocked
      );

      state.persistent = toBooleanSafe(
        state.config.persistent,
        state.authenticated && !state.demoMode && !state.publicViewer && !state.authUnavailable && !state.userBlocked && !state.accessBlocked
      );

      if (state.demoMode || state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked) {
        state.persistent = false;
      }

      state.canEdit = toBooleanSafe(state.config.canEdit, false) &&
        !state.readOnly &&
        !state.publicViewer &&
        !state.authUnavailable &&
        !state.userBlocked &&
        !state.accessBlocked &&
        (state.persistent || state.demoMode);

      state.canManage = toBooleanSafe(state.config.canManage, false) &&
        !state.publicViewer &&
        !state.readOnly &&
        !state.demoMode &&
        !state.authUnavailable &&
        !state.userBlocked &&
        !state.accessBlocked;

      state.canMutate = toBooleanSafe(state.config.canMutate, state.canEdit) && state.canEdit;
      state.currentProject = safeClone(state.config.project || {});
    } catch (error) {}
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

      syncTheme();

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
        project: state.currentProject,
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
        canWrite: canWriteProject()
      });

      try {
        window.__VECTOPLAN_PROJECT_FORM_STATE__ = state;
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
      removeAllListeners();
      state.destroyed = true;
      state.initialized = false;
    } catch (error) {}

    return true;
  }

  function getSnapshot() {
    try {
      return {
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
        isDirty: state.isDirty,
        isSaving: state.isSaving,
        isLoading: state.isLoading,
        lastSavedAt: state.lastSavedAt,
        lastError: state.lastError,
        currentProject: safeClone(state.currentProject),
        config: safeClone(state.config),
        payload: collectPayload()
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        initialized: false,
        error: normalizeError(error)
      };
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
    applyTheme: applyTheme,
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
      disabledReason: disabledReason
    }
  };

  try {
    global[EXPORT_NAME] = api;
    global[LEGACY_EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectForm = api;
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