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
  var INTERNAL_VERSION = 4;

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
      WORKSPACES.forEach(function eachWorkspace(workspace) {
        result[workspace] = !!normalized[workspace];
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

  function effectiveWorkspaces(visibility, published) {
    try {
      var normalizedVisibility = normalizeVisibility(visibility, "private");
      var desired = normalizeWorkspaces(published);

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

      var demoMode = toBooleanSafe(
        config.demoMode !== undefined ? config.demoMode :
          config.demo_mode !== undefined ? config.demo_mode :
            uiFlags.demo_mode !== undefined ? uiFlags.demo_mode :
              currentUser.demo_mode !== undefined ? currentUser.demo_mode :
                currentUser.demoMode !== undefined ? currentUser.demoMode :
                  currentUser.is_demo,
        false
      );

      var persistent = toBooleanSafe(
        config.persistent !== undefined ? config.persistent :
          uiFlags.persistent !== undefined ? uiFlags.persistent :
            currentUser.persistent,
        !demoMode
      );

      var publicViewer = toBooleanSafe(
        config.publicViewer !== undefined ? config.publicViewer :
          config.isPublicViewer !== undefined ? config.isPublicViewer :
            uiFlags.public_viewer !== undefined ? uiFlags.public_viewer :
              access.public_viewer !== undefined ? access.public_viewer :
                access.publicViewer,
        false
      );

      var readOnly = toBooleanSafe(
        config.readOnly !== undefined ? config.readOnly :
          config.readonly !== undefined ? config.readonly :
            uiFlags.read_only !== undefined ? uiFlags.read_only :
              access.read_only !== undefined ? access.read_only :
                access.readOnly,
        false
      );

      var authUnavailable = toBooleanSafe(
        config.authUnavailable !== undefined ? config.authUnavailable :
          uiFlags.auth_unavailable !== undefined ? uiFlags.auth_unavailable :
            currentUser.auth_unavailable !== undefined ? currentUser.auth_unavailable :
              currentUser.authUnavailable,
        false
      );

      var userBlocked = toBooleanSafe(
        config.userBlocked !== undefined ? config.userBlocked :
          uiFlags.user_blocked !== undefined ? uiFlags.user_blocked :
            currentUser.user_blocked !== undefined ? currentUser.user_blocked :
              currentUser.userBlocked,
        false
      );

      var accessBlocked = toBooleanSafe(
        config.accessBlocked !== undefined ? config.accessBlocked :
          uiFlags.access_blocked !== undefined ? uiFlags.access_blocked :
            currentUser.access_blocked !== undefined ? currentUser.access_blocked :
              currentUser.accessBlocked,
        false
      );

      var publicId = trimString(
        config.projectPublicId ||
          config.project_public_id ||
          project.public_id ||
          project.publicId ||
          project.project_public_id ||
          project.projectPublicId ||
          "",
        ""
      );

      var endpoint = trimString(paths.publication || paths.projectPublication || paths.project_publication, "");
      if (!endpoint && publicId && publicId !== "new") {
        endpoint = "/v1/projects/" + encodeURIComponent(publicId) + "/publication";
      }

      var canManage = toBooleanSafe(
        config.canManage !== undefined ? config.canManage :
          uiFlags.can_manage !== undefined ? uiFlags.can_manage :
            access.can_manage !== undefined ? access.can_manage :
              access.canManage,
        false
      );

      var canPublish = toBooleanSafe(
        config.canPublish !== undefined ? config.canPublish :
          config.canManagePublication !== undefined ? config.canManagePublication :
            uiFlags.can_publish !== undefined ? uiFlags.can_publish :
              access.can_publish !== undefined ? access.can_publish :
                access.canPublish,
        canManage
      );

      return {
        project: project,
        currentUser: currentUser,
        access: access,
        publication: publication,
        uiFlags: uiFlags,
        projectPublicId: publicId,
        projectVisibility: normalizeVisibility(config.projectVisibility || config.project_visibility || project.visibility || publication.visibility, "private"),
        isNew: toBooleanSafe(config.isNew !== undefined ? config.isNew : (project.is_new !== undefined ? project.is_new : project.isNew), !publicId || publicId === "new"),
        canManage: canManage,
        canPublish: canPublish,
        canEdit: toBooleanSafe(config.canEdit !== undefined ? config.canEdit : access.can_edit, false),
        canMutate: toBooleanSafe(config.canMutate !== undefined ? config.canMutate : access.can_mutate, canPublish),
        demoMode: demoMode,
        persistent: persistent,
        publicViewer: publicViewer,
        readOnly: readOnly,
        authUnavailable: authUnavailable,
        userBlocked: userBlocked,
        accessBlocked: accessBlocked,
        endpoint: endpoint,
        paths: paths,
        parentEvents: {
          publicationChanged: trimString(
            config.parentEvents && config.parentEvents.publicationChanged,
            EVENT_PUBLICATION_CHANGED
          ),
          error: trimString(
            config.parentEvents && config.parentEvents.error,
            EVENT_PROJECT_ERROR
          )
        }
      };
    } catch (error) {
      return {
        project: {},
        currentUser: {},
        access: {},
        publication: {},
        uiFlags: {},
        projectPublicId: "",
        projectVisibility: "private",
        isNew: true,
        canManage: false,
        canPublish: false,
        canEdit: false,
        canMutate: false,
        demoMode: false,
        persistent: false,
        publicViewer: false,
        readOnly: true,
        authUnavailable: false,
        userBlocked: false,
        accessBlocked: true,
        endpoint: "",
        paths: {},
        parentEvents: {
          publicationChanged: EVENT_PUBLICATION_CHANGED,
          error: EVENT_PROJECT_ERROR
        }
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
      if (!status) {
        return;
      }

      var publication = isObject(data) ? data : collectData();
      var visibility = normalizeVisibility(publication.visibility, "private");
      var effective = normalizeWorkspaces(publication.effective_published_workspaces || publication.effectivePublishedWorkspaces);
      var hasEffective = anyWorkspaceEnabled(effective);

      setStatusClasses(status, visibility, hasEffective);

      if (state.authUnavailable) {
        status.textContent = "Auth nicht erreichbar";
      } else if (state.userBlocked || state.accessBlocked) {
        status.textContent = "Zugriff gesperrt";
      } else if (visibility === "private") {
        status.textContent = "Privat";
      } else if (hasEffective) {
        status.textContent = "Reiter veröffentlicht";
      } else {
        status.textContent = "Keine Reiter veröffentlicht";
      }
    } catch (error) {}
  }

  function updateSummary(data) {
    try {
      var summary = state.refs.summary;
      if (!summary) {
        return;
      }

      var publication = isObject(data) ? data : collectData();
      var visibility = normalizeVisibility(publication.visibility, "private");
      var effective = normalizeWorkspaces(publication.effective_published_workspaces || publication.effectivePublishedWorkspaces);
      var enabledLabels = [];

      WORKSPACES.forEach(function eachWorkspace(workspace) {
        if (effective[workspace]) {
          enabledLabels.push(workspaceLabel(workspace));
        }
      });

      if (visibility === "private") {
        summary.textContent = "Private Projekte veröffentlichen keine Workspaces.";
      } else if (enabledLabels.length) {
        summary.textContent = "Öffentlich sichtbar: " + enabledLabels.join(", ") + ".";
      } else {
        summary.textContent = "Projekt ist " + visibility + ", aber kein Workspace ist veröffentlicht.";
      }
    } catch (error) {}
  }

  function applyData(data, options) {
    try {
      var opts = isObject(options) ? options : {};
      var publication = isObject(data) ? data : {};
      var visibility = normalizeVisibility(publication.visibility || currentVisibility(), "private");
      var published = sanitizeWorkspaceMap(publication.published_workspaces || publication.publishedWorkspaces, emptyWorkspaceMap(false));
      var effective = sanitizeWorkspaceMap(
        publication.effective_published_workspaces || publication.effectivePublishedWorkspaces || effectiveWorkspaces(visibility, published),
        effectiveWorkspaces(visibility, published)
      );

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
      }

      (state.refs.checkboxes || []).forEach(function applyCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");
          if (!isAllowedWorkspace(workspace)) {
            input.checked = false;
            input.disabled = true;
            return;
          }

          input.checked = !!published[workspace];
          input.setAttribute("data-effective-published", effective[workspace] ? "true" : "false");
        } catch (error) {}
      });

      (state.refs.optionCards || []).forEach(function applyCard(card) {
        try {
          var workspace = normalizeWorkspace(card.getAttribute("data-workspace") || card.getAttribute("data-value") || "");
          if (!isAllowedWorkspace(workspace)) {
            card.classList.remove(CLASS_SELECTED);
            card.classList.remove(CLASS_EFFECTIVE);
            card.setAttribute("aria-disabled", "true");
            return;
          }

          card.classList.toggle(CLASS_SELECTED, !!published[workspace]);
          card.classList.toggle(CLASS_EFFECTIVE, !!effective[workspace]);
          card.setAttribute("data-published", published[workspace] ? "true" : "false");
          card.setAttribute("data-effective-published", effective[workspace] ? "true" : "false");
        } catch (error) {}
      });

      setChecked(state.refs.requireAuth, toBooleanSafe(publication.require_auth !== undefined ? publication.require_auth : publication.requireAuth, visibility === "private"));
      setChecked(
        state.refs.requirePermission,
        toBooleanSafe(
          publication.require_project_permission !== undefined ? publication.require_project_permission : publication.requireProjectPermission,
          visibility === "private"
        )
      );

      updateDisabledState();
      updateStatus({
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective
      });
      updateSummary({
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective
      });

      state.currentData = {
        visibility: visibility,
        published_workspaces: published,
        publishedWorkspaces: safeClone(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: getChecked(state.refs.requireAuth),
        requireAuth: getChecked(state.refs.requireAuth),
        require_project_permission: getChecked(state.refs.requirePermission),
        requireProjectPermission: getChecked(state.refs.requirePermission)
      };

      if (!opts.keepDirty) {
        setDirty(false);
      }

      return state.currentData;
    } catch (error) {
      return data || {};
    }
  }

  function collectData() {
    var published = emptyWorkspaceMap(false);

    try {
      (state.refs.checkboxes || []).forEach(function collectCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");

          if (isAllowedWorkspace(workspace)) {
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

      return {
        visibility: visibility,
        published_workspaces: sanitizeWorkspaceMap(published),
        publishedWorkspaces: sanitizeWorkspaceMap(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: requireAuth,
        requireAuth: requireAuth,
        require_project_permission: requirePermission,
        requireProjectPermission: requirePermission
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

  function buildPayloadForSave() {
    try {
      var data = collectData();
      var published = sanitizeWorkspaceMap(data.published_workspaces);
      var effective = effectiveWorkspaces(data.visibility, published);

      return {
        visibility: data.visibility,
        published_workspaces: published,
        publishedWorkspaces: safeClone(published),
        workspaces: safeClone(published),
        effective_published_workspaces: effective,
        effectivePublishedWorkspaces: safeClone(effective),
        require_auth: data.require_auth,
        requireAuth: data.require_auth,
        require_project_permission: data.require_project_permission,
        requireProjectPermission: data.require_project_permission,
        source: "vectoplan-app.project-publication",
        client_version: INTERNAL_VERSION,
        clientVersion: INTERNAL_VERSION
      };
    } catch (error) {
      return {
        visibility: "private",
        published_workspaces: emptyWorkspaceMap(false),
        publishedWorkspaces: emptyWorkspaceMap(false),
        workspaces: emptyWorkspaceMap(false),
        effective_published_workspaces: emptyWorkspaceMap(false),
        effectivePublishedWorkspaces: emptyWorkspaceMap(false),
        require_auth: true,
        requireAuth: true,
        require_project_permission: true,
        requireProjectPermission: true,
        source: "vectoplan-app.project-publication",
        client_version: INTERNAL_VERSION,
        clientVersion: INTERNAL_VERSION
      };
    }
  }

  function buildEndpoint() {
    try {
      if (state.endpoint) {
        return state.endpoint;
      }

      var publicId =
        trimString(state.projectPublicId, "") ||
        (state.config && trimString(state.config.projectPublicId, "")) ||
        (state.refs.card && trimString(state.refs.card.getAttribute("data-project-public-id"), ""));

      if (publicId && publicId !== "new") {
        return "/v1/projects/" + encodeURIComponent(publicId) + "/publication";
      }

      return "";
    } catch (error) {
      return "";
    }
  }

  async function requestJson(url, options) {
    try {
      var target = trimString(url, "");

      if (!target) {
        throw new Error("request URL missing");
      }

      var opts = isObject(options) ? options : {};
      var headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "fetch",
        "X-VECTOPLAN-Client": "project_publication.js"
      };

      var response = await fetch(target, {
        method: opts.method || "GET",
        headers: headers,
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

  function canWritePublication() {
    try {
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
        state.persistent &&
        (state.canPublish || state.canManage || state.canMutate) &&
        buildEndpoint()
      );
    } catch (error) {
      return false;
    }
  }

  function disabledReason() {
    try {
      if (state.authUnavailable) {
        return "Auth-Service nicht erreichbar. Veröffentlichung kann nicht gespeichert werden.";
      }

      if (state.userBlocked || state.accessBlocked) {
        return "Der Zugriff ist gesperrt. Veröffentlichung kann nicht gespeichert werden.";
      }

      if (state.publicViewer) {
        return "Öffentliche Ansicht ist schreibgeschützt.";
      }

      if (state.readOnly) {
        return "Dieses Projekt ist schreibgeschützt.";
      }

      if (state.isNew) {
        return "Speichere das Projekt zuerst. Danach kannst du Reiter veröffentlichen.";
      }

      if (state.demoMode) {
        return "Im Demo-Modus werden Veröffentlichungseinstellungen nicht dauerhaft gespeichert.";
      }

      if (!state.persistent) {
        return "Für Veröffentlichungseinstellungen ist ein persistenter AppUser-Kontext erforderlich.";
      }

      if (!(state.canPublish || state.canManage || state.canMutate)) {
        return "Du hast keine Berechtigung, Veröffentlichungseinstellungen zu ändern.";
      }

      if (!buildEndpoint()) {
        return "Publication-Endpunkt fehlt.";
      }

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

      state.lastResponse = response;
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

      state.lastResponse = response;
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

      var message = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-publication",
        version: INTERNAL_VERSION,
        detail: detail || {},
        ts: Date.now()
      };

      try {
        window.parent.postMessage(message, window.location.origin);
        return true;
      } catch (originError) {
        try {
          window.parent.postMessage(message, "*");
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

  function emitChangeEvent(response, publication) {
    try {
      var detail = {
        response: response || {},
        publication: publication || collectData(),
        projectPublicId: state.projectPublicId,
        endpoint: buildEndpoint(),
        visibility: publication && publication.visibility ? publication.visibility : currentVisibility()
      };

      var type = state.config && state.config.parentEvents
        ? state.config.parentEvents.publicationChanged || EVENT_PUBLICATION_CHANGED
        : EVENT_PUBLICATION_CHANGED;

      emitLocal(type, detail);
      postParentMessage(type, detail);
      dispatchParentEvent(type, detail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event("project-sidebar:refresh"));
        }
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

      (state.refs.checkboxes || []).forEach(function disableCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || input.value || "");
          var itemDisabled = disabled || !isAllowedWorkspace(workspace);
          input.disabled = itemDisabled;
          input.setAttribute("aria-disabled", itemDisabled ? "true" : "false");
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
        if (reason) {
          state.refs.save.setAttribute("title", reason);
        } else {
          state.refs.save.removeAttribute("title");
        }
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = disabled;
        state.refs.reset.setAttribute("aria-disabled", disabled ? "true" : "false");
      }

      (state.refs.optionCards || []).forEach(function disableCard(card) {
        try {
          var workspace = normalizeWorkspace(card.getAttribute("data-workspace") || card.getAttribute("data-value") || "");
          var cardDisabled = disabled || !isAllowedWorkspace(workspace);
          card.classList.toggle(CLASS_DISABLED, cardDisabled);
          card.setAttribute("aria-disabled", cardDisabled ? "true" : "false");
        } catch (error) {}
      });

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
        state.refs.card.setAttribute("data-project-publication-disabled", disabled ? "true" : "false");
        state.refs.card.setAttribute("data-project-publication-disabled-reason", reason || "");
      }
    } catch (error) {}
  }

  function onInputChange() {
    try {
      var data = collectData();

      applyData(
        {
          visibility: data.visibility,
          published_workspaces: data.published_workspaces,
          publishedWorkspaces: data.publishedWorkspaces,
          effective_published_workspaces: data.effective_published_workspaces,
          effectivePublishedWorkspaces: data.effectivePublishedWorkspaces,
          require_auth: data.require_auth,
          requireAuth: data.requireAuth,
          require_project_permission: data.require_project_permission,
          requireProjectPermission: data.requireProjectPermission
        },
        { keepDirty: true }
      );

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

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
      }

      if (state.refs.root) {
        state.refs.root.setAttribute("data-project-visibility", visibility);
      }

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
      setDirty(true);
    } catch (error) {}
  }

  function onMessage(event) {
    try {
      var data = event && event.data;

      if (!data || typeof data !== "object") {
        return;
      }

      var type = trimString(data.type || data.kind, "");

      if (type === EVENT_VISIBILITY_CHANGED) {
        onVisibilityChanged({ detail: data.detail || data });
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
      state.projectPublicId =
        trimString(state.refs.card && state.refs.card.getAttribute("data-project-public-id"), "") ||
        state.config.projectPublicId;

      state.endpoint =
        trimString(state.refs.card && state.refs.card.getAttribute("data-publication-url"), "") ||
        state.config.endpoint;

      state.isNew = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-is-new"),
        state.config.isNew
      );

      state.canManage = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-can-manage"),
        state.config.canManage
      );

      state.canPublish = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-can-publish"),
        state.config.canPublish || state.config.canManage
      );

      state.canMutate = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-can-mutate"),
        state.config.canMutate || state.config.canPublish || state.config.canManage
      );

      state.canEdit = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-can-edit"),
        state.config.canEdit
      );

      state.demoMode = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-demo-mode"),
        state.config.demoMode
      );

      state.persistent = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-persistent"),
        state.config.persistent
      );

      state.publicViewer = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-public-viewer"),
        state.config.publicViewer
      );

      state.readOnly = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-read-only"),
        state.config.readOnly
      );

      state.authUnavailable = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-auth-unavailable"),
        state.config.authUnavailable
      );

      state.userBlocked = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-user-blocked"),
        state.config.userBlocked
      );

      state.accessBlocked = toBooleanSafe(
        state.refs.card && state.refs.card.getAttribute("data-project-access-blocked"),
        state.config.accessBlocked
      );

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-public-id", state.projectPublicId || "");
        state.refs.card.setAttribute("data-project-publication-version", String(INTERNAL_VERSION));
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
        window.__VECTOPLAN_PROJECT_PUBLICATION_STATE__ = state;
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
      return {
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
        disabledReason: disabledReason(),
        projectPublicId: state.projectPublicId,
        endpoint: buildEndpoint(),
        initialData: safeClone(state.initialData),
        currentData: collectData(),
        lastResponse: safeClone(state.lastResponse),
        lastError: state.lastError
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
      extractPublicationPayload: extractPublicationPayload
    }
  };

  try {
    global[EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectPublication = api;
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