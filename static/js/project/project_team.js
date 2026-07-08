/* services/vectoplan-app/static/js/project/project_team.js */

/*
  VECTOPLAN Project Team

  Zweck:
  - Verwaltet Teammitglieder, Rollen und Projekt-Einladungen im Projekt-Workspace.
  - Arbeitet gegen:
      GET    /v1/projects/<project_id>/members
      PATCH  /v1/projects/<project_id>/members/<user_id>
      DELETE /v1/projects/<project_id>/members/<user_id>
      GET    /v1/projects/<project_id>/invitations
      POST   /v1/projects/<project_id>/invitations
      DELETE /v1/projects/<project_id>/invitations/<invitation_id>
  - Erzeugt keine Benutzeraccounts.
  - Einladungen werden per E-Mail an den Invitation-Service gegeben.
  - Der Server prüft, ob die E-Mail im externen Auth-/Registrierungsdienst existiert.
  - Owner wird nicht per Einladung vergeben.
  - Owner kann nicht über diese UI entfernt oder herabgestuft werden.
  - Demo, Public Viewer, Read-only, Auth-Ausfall und fehlendes Manage-Team-Recht
    deaktivieren persistente Team-Aktionen.
  - Teamdaten sind nie öffentlich und nie Teil einer Publication.

  Sicherheitsregel:
  - Team-/Rechteverwaltung ist nur für Projektverwalter.
  - Frontend-Gating ist nur UX; Backend bleibt die Wahrheit.
  - Keine Tokens, Secrets oder Auth-IDs werden an URLs angehängt.
  - Nur bekannte Rollen werden gesendet: viewer, editor, admin.
*/

(function initVectoplanProjectTeam(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectTeam";
  var INTERNAL_VERSION = 4;

  var ROOT_SELECTOR = "[data-project-workspace]";
  var CARD_SELECTOR = "[data-project-team-card]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var EVENT_READY = "vectoplan:project-team:ready";
  var EVENT_TEAM_CHANGED = "vectoplan:project:team:changed";
  var EVENT_INVITATION_CREATED = "vectoplan:project:invitation:created";
  var EVENT_INVITATION_REVOKED = "vectoplan:project:invitation:revoked";
  var EVENT_MEMBER_CHANGED = "vectoplan:project:member:changed";
  var EVENT_MEMBER_REMOVED = "vectoplan:project:member:removed";
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

  var ALLOWED_ROLES = {
    viewer: true,
    editor: true,
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

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isLoading: false,
    isSaving: false,

    canEdit: false,
    canManage: false,
    canManageTeam: false,
    canWrite: false,

    isNew: false,
    demoMode: false,
    persistent: false,
    publicViewer: false,
    readOnly: false,
    authUnavailable: false,
    userBlocked: false,
    accessBlocked: false,

    projectPublicId: "",
    projectId: "",
    membersUrl: "",
    invitationsUrl: "",
    disabledReason: "",

    members: [],
    invitations: [],
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
          normalized === "error" ||
          normalized === "failed" ||
          normalized === "readonly" ||
          normalized === "read_only" ||
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

      if (isArray(value)) {
        return value.slice();
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

  function closest(target, selector) {
    try {
      if (!target || !target.closest) {
        return null;
      }

      return target.closest(selector);
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
      if (!node) {
        return;
      }

      while (node.firstChild) {
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
      var card = query(CARD_SELECTOR);

      var paths = isObject(config.paths) ? config.paths : {};
      var parentEvents = isObject(config.parentEvents) ? config.parentEvents : {};
      var project = isObject(config.project) ? config.project : {};
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};
      var access = isObject(config.access) ? config.access : isObject(project.access) ? project.access : {};
      var uiFlags = isObject(config.uiFlags) ? config.uiFlags : isObject(project.ui_flags) ? project.ui_flags : {};

      var publicId = trimString(
        config.projectPublicId ||
          config.project_public_id ||
          project.public_id ||
          project.publicId ||
          project.project_public_id ||
          project.projectPublicId ||
          attr(card, "data-project-public-id", "") ||
          attr(root, "data-project-public-id", ""),
        ""
      );

      var demoMode = toBooleanSafe(
        config.demoMode !== undefined ? config.demoMode :
          config.demo_mode !== undefined ? config.demo_mode :
            uiFlags.demo_mode !== undefined ? uiFlags.demo_mode :
              currentUser.demo_mode !== undefined ? currentUser.demo_mode :
                currentUser.demoMode !== undefined ? currentUser.demoMode :
                  currentUser.is_demo !== undefined ? currentUser.is_demo :
                    attr(card, "data-project-demo-mode", ""),
        false
      );

      var publicViewer = toBooleanSafe(
        config.publicViewer !== undefined ? config.publicViewer :
          config.isPublicViewer !== undefined ? config.isPublicViewer :
            uiFlags.public_viewer !== undefined ? uiFlags.public_viewer :
              access.public_viewer !== undefined ? access.public_viewer :
                access.publicViewer !== undefined ? access.publicViewer :
                  attr(card, "data-project-public-viewer", ""),
        false
      );

      var readOnly = toBooleanSafe(
        config.readOnly !== undefined ? config.readOnly :
          config.readonly !== undefined ? config.readonly :
            uiFlags.read_only !== undefined ? uiFlags.read_only :
              access.read_only !== undefined ? access.read_only :
                access.readOnly !== undefined ? access.readOnly :
                  attr(card, "data-project-read-only", ""),
        publicViewer
      );

      var authUnavailable = toBooleanSafe(
        config.authUnavailable !== undefined ? config.authUnavailable :
          uiFlags.auth_unavailable !== undefined ? uiFlags.auth_unavailable :
            currentUser.auth_unavailable !== undefined ? currentUser.auth_unavailable :
              currentUser.authUnavailable !== undefined ? currentUser.authUnavailable :
                attr(card, "data-project-auth-unavailable", ""),
        false
      );

      var userBlocked = toBooleanSafe(
        config.userBlocked !== undefined ? config.userBlocked :
          uiFlags.user_blocked !== undefined ? uiFlags.user_blocked :
            currentUser.user_blocked !== undefined ? currentUser.user_blocked :
              currentUser.userBlocked !== undefined ? currentUser.userBlocked :
                attr(card, "data-project-user-blocked", ""),
        false
      );

      var accessBlocked = toBooleanSafe(
        config.accessBlocked !== undefined ? config.accessBlocked :
          uiFlags.access_blocked !== undefined ? uiFlags.access_blocked :
            currentUser.access_blocked !== undefined ? currentUser.access_blocked :
              currentUser.accessBlocked !== undefined ? currentUser.accessBlocked :
                attr(card, "data-project-access-blocked", ""),
        false
      );

      if (publicViewer || authUnavailable || userBlocked || accessBlocked) {
        readOnly = true;
      }

      if (publicViewer || authUnavailable || userBlocked || accessBlocked) {
        demoMode = false;
      }

      var persistent = toBooleanSafe(
        config.persistent !== undefined ? config.persistent :
          uiFlags.persistent !== undefined ? uiFlags.persistent :
            currentUser.persistent !== undefined ? currentUser.persistent :
              attr(card, "data-project-persistent", ""),
        !demoMode && !publicViewer && !authUnavailable && !userBlocked && !accessBlocked
      );

      if (demoMode || publicViewer || authUnavailable || userBlocked || accessBlocked) {
        persistent = false;
      }

      var membersUrl = trimString(paths.members || attr(card, "data-members-url", ""), "");
      var invitationsUrl = trimString(paths.invitations || attr(card, "data-invitations-url", ""), "");

      if (!membersUrl && publicId && publicId !== "new") {
        membersUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/members";
      }

      if (!invitationsUrl && publicId && publicId !== "new") {
        invitationsUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/invitations";
      }

      var canManage = toBooleanSafe(
        config.canManage !== undefined ? config.canManage :
          uiFlags.can_manage !== undefined ? uiFlags.can_manage :
            access.can_manage !== undefined ? access.can_manage :
              access.canManage !== undefined ? access.canManage :
                attr(card, "data-project-can-manage", ""),
        false
      );

      var canManageTeam = toBooleanSafe(
        config.canManageTeam !== undefined ? config.canManageTeam :
          uiFlags.can_manage_team !== undefined ? uiFlags.can_manage_team :
            access.can_manage_team !== undefined ? access.can_manage_team :
              access.canManageTeam !== undefined ? access.canManageTeam :
                attr(card, "data-project-can-manage-team", ""),
        canManage
      );

      var isNew = toBooleanSafe(
        config.isNew !== undefined ? config.isNew :
          config.is_new !== undefined ? config.is_new :
            project.is_new !== undefined ? project.is_new :
              project.isNew !== undefined ? project.isNew :
                attr(card, "data-project-is-new", ""),
        !publicId || publicId === "new"
      );

      var canWrite = toBooleanSafe(
        config.canWriteTeam !== undefined ? config.canWriteTeam :
          attr(card, "data-project-team-can-write", ""),
        canManage && canManageTeam && !isNew && !demoMode && persistent && !publicViewer && !readOnly && !authUnavailable && !userBlocked && !accessBlocked
      );

      return {
        project: project,
        currentUser: currentUser,
        access: access,
        uiFlags: uiFlags,

        projectId: trimString(config.projectId || config.project_id || project.id || project.project_id || project.projectId || attr(card, "data-project-id", ""), ""),
        projectPublicId: publicId,

        membersUrl: membersUrl,
        invitationsUrl: invitationsUrl,

        isNew: isNew,
        canEdit: toBooleanSafe(config.canEdit !== undefined ? config.canEdit : access.can_edit, false),
        canManage: canManage && !publicViewer && !readOnly && !demoMode && !authUnavailable && !userBlocked && !accessBlocked,
        canManageTeam: canManageTeam && !publicViewer && !readOnly && !demoMode && !authUnavailable && !userBlocked && !accessBlocked,
        canWrite: canWrite,

        demoMode: demoMode,
        persistent: persistent,
        publicViewer: publicViewer,
        readOnly: readOnly,
        authUnavailable: authUnavailable,
        userBlocked: userBlocked,
        accessBlocked: accessBlocked,

        disabledReason: trimString(attr(card, "data-project-team-disabled-reason", ""), ""),

        parentEvents: {
          ready: trimString(parentEvents.teamReady, EVENT_READY),
          teamChanged: trimString(parentEvents.teamChanged, EVENT_TEAM_CHANGED),
          error: trimString(parentEvents.error, EVENT_ERROR)
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
        membersUrl: "",
        invitationsUrl: "",
        isNew: true,
        canEdit: false,
        canManage: false,
        canManageTeam: false,
        canWrite: false,
        demoMode: false,
        persistent: false,
        publicViewer: false,
        readOnly: true,
        authUnavailable: false,
        userBlocked: false,
        accessBlocked: true,
        disabledReason: "Teamverwaltung konnte nicht initialisiert werden.",
        parentEvents: {
          ready: EVENT_READY,
          teamChanged: EVENT_TEAM_CHANGED,
          error: EVENT_ERROR
        }
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

      if (hidden) {
        element.setAttribute("hidden", "");
      } else {
        element.removeAttribute("hidden");
      }
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

  function setMessage(kind, message) {
    try {
      var node = state.refs && state.refs.message;
      if (!node) {
        setGlobalAlert(kind, message);
        return;
      }

      var text = trimString(message, "");

      node.classList.remove("is-success", "is-warning", "is-error", "is-info");
      node.removeAttribute("data-kind");

      if (!text) {
        node.textContent = "";
        setHidden(node, true);
        return;
      }

      var normalizedKind = trimString(kind, "info").toLowerCase();

      if (normalizedKind === "success") {
        node.classList.add("is-success");
        node.setAttribute("data-kind", "success");
      } else if (normalizedKind === "warning") {
        node.classList.add("is-warning");
        node.setAttribute("data-kind", "warning");
      } else if (normalizedKind === "error" || normalizedKind === "danger") {
        node.classList.add("is-error");
        node.setAttribute("data-kind", "error");
      } else {
        node.classList.add("is-info");
        node.setAttribute("data-kind", "info");
      }

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
        return "Für Teamverwaltung ist ein persistenter AppUser-Kontext erforderlich.";
      }

      if (!state.canManage || !state.canManageTeam) {
        return "Du hast keine Berechtigung, Team und Einladungen zu verwalten.";
      }

      if (!state.membersUrl || !state.invitationsUrl) {
        return "Team-Endpunkte fehlen.";
      }

      return "";
    } catch (error) {
      return "Teamverwaltung ist deaktiviert.";
    }
  }

  function canOperate() {
    try {
      return !!(
        state.canWrite &&
        state.canManage &&
        state.canManageTeam &&
        !state.isNew &&
        !state.demoMode &&
        state.persistent &&
        !state.publicViewer &&
        !state.readOnly &&
        !state.authUnavailable &&
        !state.userBlocked &&
        !state.accessBlocked &&
        !state.isSaving &&
        !state.isLoading &&
        state.membersUrl &&
        state.invitationsUrl
      );
    } catch (error) {
      return false;
    }
  }

  function setLoading(isLoading) {
    try {
      state.isLoading = !!isLoading;

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_LOADING, state.isLoading);
        state.refs.card.setAttribute("data-project-team-loading", state.isLoading ? "true" : "false");
      }

      updateDisabledState();
    } catch (error) {}
  }

  function setSaving(isSaving) {
    try {
      state.isSaving = !!isSaving;

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_SAVING, state.isSaving);
        state.refs.card.setAttribute("data-project-team-saving", state.isSaving ? "true" : "false");
      }

      updateDisabledState();
    } catch (error) {}
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
      } else if (state.userBlocked || state.accessBlocked) {
        node.classList.add("vp-project-chip--error");
        node.textContent = "Zugriff gesperrt";
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
      var disabled = !canOperate();
      var reason = disabled ? disabledReason() : "";

      if (state.refs.inviteEmail) {
        state.refs.inviteEmail.disabled = disabled;
        if (reason) {
          state.refs.inviteEmail.setAttribute("title", reason);
        } else {
          state.refs.inviteEmail.removeAttribute("title");
        }
      }

      if (state.refs.inviteRole) {
        state.refs.inviteRole.disabled = disabled;
        if (reason) {
          state.refs.inviteRole.setAttribute("title", reason);
        } else {
          state.refs.inviteRole.removeAttribute("title");
        }
      }

      if (state.refs.inviteSubmit) {
        state.refs.inviteSubmit.disabled = disabled;
        state.refs.inviteSubmit.setAttribute("aria-disabled", disabled ? "true" : "false");
        if (reason) {
          state.refs.inviteSubmit.setAttribute("title", reason);
        } else {
          state.refs.inviteSubmit.removeAttribute("title");
        }
      }

      [state.refs.refresh, state.refs.refreshMembers, state.refs.refreshInvitations].forEach(function eachRefresh(button) {
        try {
          if (button) {
            button.disabled = state.isNew || state.isLoading || !state.membersUrl;
          }
        } catch (error) {}
      });

      queryAll("[data-project-team-member-role]", state.refs.card).forEach(function eachSelect(select) {
        try {
          var role = trimString(select.getAttribute("data-current-role") || select.value, ROLE_VIEWER).toLowerCase();
          var owner = role === ROLE_OWNER || select.closest("[data-owner='true']");
          select.disabled = disabled || owner;
          select.setAttribute("aria-disabled", select.disabled ? "true" : "false");
        } catch (error) {}
      });

      queryAll("[data-project-team-member-save]", state.refs.card).forEach(function eachButton(button) {
        try {
          var row = closest(button, "[data-project-team-member]");
          var role = trimString(button.getAttribute("data-current-role") || (row && row.getAttribute("data-role")), "").toLowerCase();
          var owner = role === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true");
          button.disabled = disabled || owner;
          button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        } catch (error) {}
      });

      queryAll("[data-project-team-member-remove]", state.refs.card).forEach(function eachButton(button) {
        try {
          var row = closest(button, "[data-project-team-member]");
          var role = trimString(button.getAttribute("data-current-role") || (row && row.getAttribute("data-role")), "").toLowerCase();
          var owner = role === ROLE_OWNER || (row && row.getAttribute("data-owner") === "true");
          button.disabled = disabled || owner;
          button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        } catch (error) {}
      });

      queryAll("[data-project-team-invitation-revoke]", state.refs.card).forEach(function eachButton(button) {
        try {
          var row = closest(button, "[data-project-team-invitation]");
          var status = trimString(button.getAttribute("data-status") || (row && row.getAttribute("data-status")), "pending").toLowerCase();
          button.disabled = disabled || !!INVITATION_TERMINAL_STATUSES[status];
          button.setAttribute("aria-disabled", button.disabled ? "true" : "false");
        } catch (error) {}
      });

      if (state.refs.card) {
        state.refs.card.classList.toggle(CLASS_DISABLED, disabled);
        state.refs.card.setAttribute("data-project-team-disabled", disabled ? "true" : "false");
        state.refs.card.setAttribute("data-project-team-disabled-reason", reason);
      }

      updateStatus();
    } catch (error) {}
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
        project_public_id: trimString(data.project_public_id || data.projectPublicId || project.public_id || project.publicId || state.projectPublicId, ""),
        project_id: data.project_id || data.projectId || project.id || "",
        can_manage: toBooleanSafe(data.can_manage !== undefined ? data.can_manage : data.canManage, state.canManage),
        can_manage_team: toBooleanSafe(data.can_manage_team !== undefined ? data.can_manage_team : data.canManageTeam, state.canManageTeam),
        can_write: toBooleanSafe(data.can_write !== undefined ? data.can_write : data.canWrite, state.canWrite),
        can_edit: toBooleanSafe(data.can_edit !== undefined ? data.can_edit : data.canEdit, state.config && state.config.canEdit),
        is_new: toBooleanSafe(data.is_new !== undefined ? data.is_new : data.isNew, state.isNew),
        demo_mode: toBooleanSafe(data.demo_mode !== undefined ? data.demo_mode : data.demoMode, state.demoMode),
        persistent: toBooleanSafe(data.persistent, state.persistent),
        public_viewer: toBooleanSafe(data.public_viewer !== undefined ? data.public_viewer : data.publicViewer, state.publicViewer),
        read_only: toBooleanSafe(data.read_only !== undefined ? data.read_only : data.readOnly, state.readOnly),
        auth_unavailable: toBooleanSafe(data.auth_unavailable !== undefined ? data.auth_unavailable : data.authUnavailable, state.authUnavailable),
        user_blocked: toBooleanSafe(data.user_blocked !== undefined ? data.user_blocked : data.userBlocked, state.userBlocked),
        access_blocked: toBooleanSafe(data.access_blocked !== undefined ? data.access_blocked : data.accessBlocked, state.accessBlocked),
        members_url: trimString(data.members_url || data.membersUrl, state.membersUrl),
        invitations_url: trimString(data.invitations_url || data.invitationsUrl, state.invitationsUrl),
        disabled_reason: trimString(data.disabled_reason, state.disabledReason),
        members: isArray(data.members) ? data.members : isArray(project.members) ? project.members : [],
        invitations: isArray(data.invitations)
          ? data.invitations
          : isArray(project.invitations)
            ? project.invitations
            : []
      };
    } catch (error) {
      return {
        project_public_id: state.projectPublicId,
        project_id: state.projectId,
        can_manage: state.canManage,
        can_manage_team: state.canManageTeam,
        can_write: state.canWrite,
        can_edit: false,
        is_new: state.isNew,
        demo_mode: state.demoMode,
        persistent: state.persistent,
        public_viewer: state.publicViewer,
        read_only: state.readOnly,
        auth_unavailable: state.authUnavailable,
        user_blocked: state.userBlocked,
        access_blocked: state.accessBlocked,
        members_url: state.membersUrl,
        invitations_url: state.invitationsUrl,
        disabled_reason: state.disabledReason,
        members: [],
        invitations: []
      };
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
          "X-VECTOPLAN-Client": "project_team.js"
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

  function extractItems(payload, preferredKey) {
    try {
      var data = isObject(payload) ? payload : {};

      if (isArray(data[preferredKey])) {
        return data[preferredKey];
      }

      if (isArray(data.items)) {
        return data.items;
      }

      if (preferredKey === "members" && isArray(data.team)) {
        return data.team;
      }

      if (preferredKey === "members" && isArray(data.memberships)) {
        return data.memberships;
      }

      if (preferredKey === "invitations" && isArray(data.pending_invitations)) {
        return data.pending_invitations;
      }

      if (preferredKey === "invitations" && isArray(data.pendingInvitations)) {
        return data.pendingInvitations;
      }

      if (isObject(data.data)) {
        return extractItems(data.data, preferredKey);
      }

      if (isObject(data.payload)) {
        return extractItems(data.payload, preferredKey);
      }

      return [];
    } catch (error) {
      return [];
    }
  }

  function roleLabel(role) {
    var normalized = trimString(role, ROLE_VIEWER).toLowerCase();

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
    var normalized = trimString(status, "pending").toLowerCase();

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

  function memberUserId(member) {
    try {
      var m = isObject(member) ? member : {};
      var user = isObject(m.user) ? m.user : {};
      return trimString(m.user_id || m.userId || m.id || user.id || "", "");
    } catch (error) {
      return "";
    }
  }

  function memberDisplayName(member) {
    try {
      var m = isObject(member) ? member : {};
      var user = isObject(m.user) ? m.user : {};
      var id = memberUserId(m);

      return trimString(
        user.display_name ||
          user.displayName ||
          m.display_name ||
          m.displayName ||
          user.name ||
          m.name ||
          user.handle ||
          m.handle ||
          (id ? "User " + id : "Unbekannter Benutzer"),
        "Unbekannter Benutzer"
      );
    } catch (error) {
      return "Unbekannter Benutzer";
    }
  }

  function memberEmail(member) {
    try {
      var m = isObject(member) ? member : {};
      var user = isObject(m.user) ? m.user : {};
      return trimString(user.email || m.email || m.user_email || m.userEmail || "", "");
    } catch (error) {
      return "";
    }
  }

  function memberRole(member) {
    try {
      var m = isObject(member) ? member : {};
      return trimString(m.role || m.project_role || m.projectRole, ROLE_VIEWER).toLowerCase();
    } catch (error) {
      return ROLE_VIEWER;
    }
  }

  function memberStatus(member) {
    try {
      var m = isObject(member) ? member : {};
      return trimString(m.status, "active").toLowerCase();
    } catch (error) {
      return "active";
    }
  }

  function memberPermission(member, key) {
    try {
      var m = isObject(member) ? member : {};
      var permissions = isObject(m.permissions) ? m.permissions : {};
      var camel = "can" + key.charAt(0).toUpperCase() + key.slice(1);
      var directKey = "can_" + key;

      return toBooleanSafe(
        m[directKey] !== undefined ? m[directKey] :
          m[camel] !== undefined ? m[camel] :
            permissions[key],
        false
      );
    } catch (error) {
      return false;
    }
  }

  function appendPermissionChip(parent, label, extraClass) {
    var chip = createElement("span", "vp-project-permission-chip" + (extraClass ? " " + extraClass : ""), label);
    append(parent, chip);
  }

  function normalizeRole(role, fallback) {
    try {
      var normalized = trimString(role, fallback || ROLE_VIEWER).toLowerCase();

      if (normalized === ROLE_OWNER) {
        return ROLE_ADMIN;
      }

      if (ALLOWED_ROLES[normalized]) {
        return normalized;
      }

      return fallback || ROLE_VIEWER;
    } catch (error) {
      return fallback || ROLE_VIEWER;
    }
  }

  function createRoleSelect(userId, role) {
    var select = createElement("select", "vp-project-select vp-project-select--compact");
    if (!select) {
      return null;
    }

    var normalizedRole = trimString(role, ROLE_VIEWER).toLowerCase();

    select.setAttribute("data-project-team-member-role", "");
    select.setAttribute("data-user-id", userId);
    select.setAttribute("data-current-role", normalizedRole);

    [
      [ROLE_VIEWER, "Viewer"],
      [ROLE_EDITOR, "Editor"],
      [ROLE_ADMIN, "Admin"]
    ].forEach(function eachOption(pair) {
      var option = createElement("option", "", pair[1]);
      if (!option) {
        return;
      }

      option.value = pair[0];
      option.selected = pair[0] === normalizedRole;
      append(select, option);
    });

    if (normalizedRole === ROLE_OWNER) {
      var ownerOption = createElement("option", "", "Owner");
      if (ownerOption) {
        ownerOption.value = ROLE_OWNER;
        ownerOption.selected = true;
        append(select, ownerOption);
      }
    }

    select.disabled = !canOperate() || normalizedRole === ROLE_OWNER || !userId;

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
        append(empty, createElement("p", "", "Noch keine zusätzlichen Mitglieder geladen."));

        var help = createElement("p", "vp-project-help", "Der Owner wird serverseitig als Projektmitglied geführt. Die Liste kann über „Aktualisieren“ geladen werden.");
        append(empty, help);
        append(container, empty);
        return;
      }

      list.forEach(function renderMember(member) {
        try {
          var m = isObject(member) ? member : {};
          var userId = memberUserId(m);
          var role = memberRole(m);
          var status = memberStatus(m);
          var name = memberDisplayName(m);
          var email = memberEmail(m);
          var isOwner = role === ROLE_OWNER;

          var rowClass = "vp-project-team-row" + (isOwner ? " vp-project-team-row--owner" : "");
          var row = createElement("article", rowClass);
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
          var title = createElement("h4", "vp-project-team-row__name", name);
          var meta = createElement("p", "vp-project-team-row__meta", email || (userId ? "User-ID: " + userId : "Unbekannte Benutzerreferenz"));

          append(identityText, title);
          append(identityText, meta);
          append(identity, avatar);
          append(identity, identityText);

          var roleBox = createElement("div", "vp-project-team-row__role");
          append(roleBox, createRoleSelect(userId, role));

          var permissions = createElement("div", "vp-project-team-row__permissions");
          if (permissions) {
            permissions.setAttribute("aria-label", "Effektive Rechte");
          }

          if (memberPermission(m, "view") || isOwner || role === ROLE_ADMIN || role === ROLE_EDITOR || role === ROLE_VIEWER) {
            appendPermissionChip(permissions, "Ansehen");
          }

          if (memberPermission(m, "edit") || isOwner || role === ROLE_ADMIN || role === ROLE_EDITOR) {
            appendPermissionChip(permissions, "Bearbeiten");
          }

          if (memberPermission(m, "manage") || isOwner || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Verwalten");
          }

          if (memberPermission(m, "manage_team") || isOwner || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Team");
          }

          if (memberPermission(m, "delete") || isOwner) {
            appendPermissionChip(permissions, "Löschen");
          }

          if (memberPermission(m, "embed") || isOwner || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Einbetten");
          }

          if (permissions && !permissions.childNodes.length) {
            appendPermissionChip(permissions, "Keine Rechte geladen", "vp-project-permission-chip--muted");
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
              save.setAttribute("title", "Owner können nicht über diese Oberfläche geändert werden.");
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
              remove.setAttribute("title", "Owner können nicht über diese Oberfläche entfernt werden.");
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
    try {
      var inv = isObject(invitation) ? invitation : {};
      return trimString(inv.public_id || inv.publicId || inv.invitation_id || inv.invitationId || inv.id || "", "");
    } catch (error) {
      return "";
    }
  }

  function invitationEmail(invitation) {
    try {
      var inv = isObject(invitation) ? invitation : {};
      return trimString(inv.email || inv.invitee_email || inv.inviteeEmail || inv.email_normalized || inv.emailNormalized || "", "");
    } catch (error) {
      return "";
    }
  }

  function invitationRole(invitation) {
    try {
      var inv = isObject(invitation) ? invitation : {};
      return normalizeRole(inv.role, ROLE_VIEWER);
    } catch (error) {
      return ROLE_VIEWER;
    }
  }

  function invitationStatus(invitation) {
    try {
      var inv = isObject(invitation) ? invitation : {};
      return trimString(inv.status, "pending").toLowerCase();
    } catch (error) {
      return "pending";
    }
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
        append(empty, createElement("p", "", "Keine ausstehenden Einladungen."));
        append(container, empty);
        return;
      }

      list.forEach(function renderInvitation(invitation) {
        try {
          var inv = isObject(invitation) ? invitation : {};
          var id = invitationId(inv);
          var email = invitationEmail(inv);
          var role = invitationRole(inv);
          var status = invitationStatus(inv);
          var expiresAt = trimString(inv.expires_at || inv.expiresAt, "");
          var createdAt = trimString(inv.created_at || inv.createdAt, "");

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

          var metaText = "Status: " + statusLabel(status);
          if (createdAt) {
            metaText += " · erstellt " + createdAt;
          }
          if (expiresAt) {
            metaText += " · gültig bis " + expiresAt;
          }
          append(identityText, createElement("p", "vp-project-team-row__meta", metaText));

          append(identity, avatar);
          append(identity, identityText);

          var roleBox = createElement("div", "vp-project-team-row__role");
          append(roleBox, createElement("span", "vp-project-role-chip", roleLabel(role)));

          var permissions = createElement("div", "vp-project-team-row__permissions");
          append(permissions, createElement("span", "vp-project-permission-chip vp-project-permission-chip--pending", "wartet auf Annahme"));

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
    try {
      renderMembers(state.members);
      renderInvitations(state.invitations);
      updateDisabledState();
    } catch (error) {}
  }

  function updateFromListResponses(memberPayload, invitationPayload) {
    try {
      if (memberPayload) {
        state.members = extractItems(memberPayload, "members");
      }

      if (invitationPayload) {
        state.invitations = extractItems(invitationPayload, "invitations");
      }

      renderAll();

      return {
        members: state.members,
        invitations: state.invitations
      };
    } catch (error) {
      return {
        members: state.members,
        invitations: state.invitations
      };
    }
  }

  async function refreshMembers(options) {
    var opts = isObject(options) ? options : {};

    try {
      if (state.isNew) {
        setMessage("warning", "Speichere das Projekt zuerst. Danach können Mitglieder geladen werden.");
        return false;
      }

      if (!state.membersUrl) {
        setMessage("warning", "Mitglieder-Endpunkt fehlt.");
        return false;
      }

      setLoading(true);

      var memberPayload = await requestJson(state.membersUrl, { method: "GET" });
      updateFromListResponses(memberPayload, null);

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
      if (state.isNew) {
        setMessage("warning", "Speichere das Projekt zuerst. Danach können Einladungen geladen werden.");
        return false;
      }

      if (!state.invitationsUrl) {
        setMessage("warning", "Einladungs-Endpunkt fehlt.");
        return false;
      }

      setLoading(true);

      var invitationPayload = await requestJson(state.invitationsUrl, { method: "GET" });
      updateFromListResponses(null, invitationPayload);

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
      if (state.isNew) {
        setMessage("warning", "Speichere das Projekt zuerst. Danach kann das Team geladen werden.");
        return false;
      }

      setLoading(true);

      var memberPayload = null;
      var invitationPayload = null;

      if (state.membersUrl) {
        memberPayload = await requestJson(state.membersUrl, { method: "GET" });
      }

      if (state.invitationsUrl) {
        invitationPayload = await requestJson(state.invitationsUrl, { method: "GET" });
      }

      updateFromListResponses(memberPayload, invitationPayload);

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
    try {
      var text = trimString(email, "").toLowerCase();

      if (!text) {
        return false;
      }

      if (text.length > 320) {
        return false;
      }

      return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text);
    } catch (error) {
      return false;
    }
  }

  async function inviteByEmail() {
    try {
      if (!canOperate()) {
        setMessage("warning", disabledReason() || "Du hast keine Berechtigung, Einladungen zu erstellen.");
        return false;
      }

      var email = trimString(state.refs.inviteEmail && state.refs.inviteEmail.value, "").toLowerCase();
      var role = normalizeRole(state.refs.inviteRole && state.refs.inviteRole.value, ROLE_EDITOR);

      if (!validateEmail(email)) {
        setMessage("error", "Bitte gib eine gültige E-Mail-Adresse ein.");
        if (state.refs.inviteEmail && typeof state.refs.inviteEmail.focus === "function") {
          state.refs.inviteEmail.focus();
        }
        return false;
      }

      if (!state.invitationsUrl) {
        setMessage("error", "Einladungs-Endpunkt fehlt.");
        return false;
      }

      setSaving(true);
      setMessage("info", "Einladung wird geprüft und erstellt…");

      var response = await requestJson(state.invitationsUrl, {
        method: "POST",
        body: safeJsonStringify({
          email: email,
          role: role
        })
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Einladung konnte nicht erstellt werden.");
      }

      if (state.refs.inviteEmail) {
        state.refs.inviteEmail.value = "";
      }

      setMessage("success", "Einladung wurde erstellt.");

      state.lastResponse = response;
      state.lastError = null;

      emitChangeEvent(EVENT_INVITATION_CREATED, {
        response: response,
        email: email,
        role: role
      });

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
    try {
      var selector = "[data-project-team-member-role][data-user-id='" + escapeCssValue(userId) + "']";
      var select = query(selector, state.refs.card);
      return normalizeRole(select && select.value, ROLE_VIEWER);
    } catch (error) {
      return ROLE_VIEWER;
    }
  }

  function memberUrl(userId) {
    try {
      if (!state.membersUrl) {
        return "";
      }

      return state.membersUrl.replace(/\/$/, "") + "/" + encodeURIComponent(userId);
    } catch (error) {
      return "";
    }
  }

  async function saveMemberRole(userId) {
    try {
      var uid = trimString(userId, "");
      if (!uid) {
        setMessage("error", "User-ID fehlt.");
        return false;
      }

      if (!canOperate()) {
        setMessage("warning", disabledReason() || "Du hast keine Berechtigung, Rollen zu ändern.");
        return false;
      }

      var role = roleForUserId(uid);

      if (role === ROLE_OWNER) {
        setMessage("warning", "Owner kann nicht über diese UI gesetzt werden.");
        return false;
      }

      if (!ALLOWED_ROLES[role]) {
        setMessage("error", "Ungültige Rolle.");
        return false;
      }

      setSaving(true);
      setMessage("info", "Rolle wird gespeichert…");

      var response = await requestJson(memberUrl(uid), {
        method: "PATCH",
        body: safeJsonStringify({
          role: role
        })
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Rolle konnte nicht gespeichert werden.");
      }

      setMessage("success", "Rolle wurde gespeichert.");

      state.lastResponse = response;
      state.lastError = null;

      emitChangeEvent(EVENT_MEMBER_CHANGED, {
        response: response,
        user_id: uid,
        userId: uid,
        role: role
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Rolle konnte nicht gespeichert werden.");
      emitError(normalized, { area: "member_role", user_id: userId });
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function removeMember(userId) {
    try {
      var uid = trimString(userId, "");
      if (!uid) {
        setMessage("error", "User-ID fehlt.");
        return false;
      }

      if (!canOperate()) {
        setMessage("warning", disabledReason() || "Du hast keine Berechtigung, Mitglieder zu entfernen.");
        return false;
      }

      var row = query("[data-project-team-member][data-user-id='" + escapeCssValue(uid) + "']", state.refs.card);
      if (row && row.getAttribute("data-owner") === "true") {
        setMessage("warning", "Owner können nicht über diese UI entfernt werden.");
        return false;
      }

      var confirmed = true;
      try {
        confirmed = window.confirm("Mitglied aus diesem Projekt entfernen?");
      } catch (error) {
        confirmed = true;
      }

      if (!confirmed) {
        return false;
      }

      setSaving(true);
      setMessage("info", "Mitglied wird entfernt…");

      var response = await requestJson(memberUrl(uid), {
        method: "DELETE"
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Mitglied konnte nicht entfernt werden.");
      }

      setMessage("success", "Mitglied wurde entfernt.");

      state.lastResponse = response;
      state.lastError = null;

      emitChangeEvent(EVENT_MEMBER_REMOVED, {
        response: response,
        user_id: uid,
        userId: uid,
        removed: true
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Mitglied konnte nicht entfernt werden.");
      emitError(normalized, { area: "member_remove", user_id: userId });
      return false;
    } finally {
      setSaving(false);
    }
  }

  function invitationUrl(invitationId) {
    try {
      if (!state.invitationsUrl) {
        return "";
      }

      return state.invitationsUrl.replace(/\/$/, "") + "/" + encodeURIComponent(invitationId);
    } catch (error) {
      return "";
    }
  }

  async function revokeInvitation(invitationId) {
    try {
      var id = trimString(invitationId, "");
      if (!id) {
        setMessage("error", "Einladungs-ID fehlt.");
        return false;
      }

      if (!canOperate()) {
        setMessage("warning", disabledReason() || "Du hast keine Berechtigung, Einladungen zu widerrufen.");
        return false;
      }

      var row = query("[data-project-team-invitation][data-invitation-id='" + escapeCssValue(id) + "']", state.refs.card);
      var status = lowerString(row && row.getAttribute("data-status"), "pending");
      if (INVITATION_TERMINAL_STATUSES[status]) {
        setMessage("warning", "Diese Einladung ist bereits abgeschlossen.");
        return false;
      }

      setSaving(true);
      setMessage("info", "Einladung wird widerrufen…");

      var response = await requestJson(invitationUrl(id), {
        method: "DELETE"
      });

      if (!response || response.ok === false) {
        throw new Error(response && (response.error || response.message) ? response.error || response.message : "Einladung konnte nicht widerrufen werden.");
      }

      setMessage("success", "Einladung wurde widerrufen.");

      state.lastResponse = response;
      state.lastError = null;

      emitChangeEvent(EVENT_INVITATION_REVOKED, {
        response: response,
        invitation_id: id,
        invitationId: id,
        revoked: true
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      state.lastError = normalized;
      setMessage("error", normalized.message || "Einladung konnte nicht widerrufen werden.");
      emitError(normalized, { area: "invitation_revoke", invitation_id: invitationId });
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
        source: "vectoplan-app.project-team",
        version: INTERNAL_VERSION,
        detail: detail || {},
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

  function emitChangeEvent(type, detail) {
    try {
      var eventType = type || EVENT_TEAM_CHANGED;
      var payloadDetail = {};

      Object.keys(detail || {}).forEach(function copyDetail(key) {
        payloadDetail[key] = detail[key];
      });

      payloadDetail.projectPublicId = state.projectPublicId;
      payloadDetail.project_public_id = state.projectPublicId;
      payloadDetail.members = state.members;
      payloadDetail.invitations = state.invitations;
      payloadDetail.canManage = state.canManage;
      payloadDetail.canManageTeam = state.canManageTeam;

      emitLocal(eventType, payloadDetail);
      emitLocal(EVENT_TEAM_CHANGED, payloadDetail);
      postParentMessage(eventType, payloadDetail);
      dispatchParentEvent(eventType, payloadDetail);
      dispatchParentEvent(EVENT_TEAM_CHANGED, payloadDetail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event(EVENT_SIDEBAR_REFRESH));
        }
      } catch (error) {}

      return true;
    } catch (error) {
      return false;
    }
  }

  function emitError(error, extra) {
    try {
      var payload = {
        error: error || {},
        area: extra && extra.area ? extra.area : "team",
        projectPublicId: state.projectPublicId,
        membersUrl: state.membersUrl,
        invitationsUrl: state.invitationsUrl
      };

      Object.keys(extra || {}).forEach(function copyExtra(key) {
        payload[key] = extra[key];
      });

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

  function onCardClick(event) {
    try {
      var target = event && event.target ? event.target : null;

      var inviteButton = closest(target, "[data-project-team-invite-submit]");
      if (inviteButton) {
        event.preventDefault();
        void inviteByEmail();
        return;
      }

      var refreshButton = closest(target, "[data-project-team-refresh], [data-project-team-members-refresh]");
      if (refreshButton) {
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

  function onCardChange(event) {
    try {
      var target = event && event.target ? event.target : null;
      var roleSelect = closest(target, "[data-project-team-member-role]");

      if (roleSelect) {
        setMessage("", "");
      }
    } catch (error) {}
  }

  function onInviteKeydown(event) {
    try {
      if (!event) {
        return;
      }

      if (event.key !== "Enter") {
        return;
      }

      event.preventDefault();
      void inviteByEmail();
    } catch (error) {}
  }

  function updateAfterProjectSaved(detail) {
    try {
      var project = isObject(detail && detail.project) ? detail.project : {};
      var publicId = trimString(
        project.public_id ||
          project.publicId ||
          project.project_public_id ||
          project.projectPublicId ||
          detail.projectPublicId ||
          detail.project_public_id ||
          "",
        ""
      );

      if (!publicId || publicId === "new") {
        return false;
      }

      state.projectPublicId = publicId;
      state.projectId = trimString(project.id || project.project_id || project.projectId || state.projectId, "");
      state.isNew = false;
      state.membersUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/members";
      state.invitationsUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/invitations";

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-public-id", publicId);
        state.refs.card.setAttribute("data-project-is-new", "false");
        state.refs.card.setAttribute("data-members-url", state.membersUrl);
        state.refs.card.setAttribute("data-invitations-url", state.invitationsUrl);
      }

      updateDisabledState();
      return true;
    } catch (error) {
      return false;
    }
  }

  function onMessage(event) {
    try {
      var data = event && event.data;

      if (!data || typeof data !== "object") {
        return;
      }

      var type = trimString(data.type || data.kind, "");

      if (type === "vectoplan:project:saved" || type === "vectoplan:project:created") {
        updateAfterProjectSaved(isObject(data.detail) ? data.detail : data);
      }
    } catch (error) {}
  }

  function wireEvents() {
    try {
      addListener(state.refs.card, "click", onCardClick);
      addListener(state.refs.card, "change", onCardChange);
      addListener(state.refs.inviteEmail, "keydown", onInviteKeydown);
      addListener(window, "message", onMessage);
    } catch (error) {}
  }

  function initStateFromConfigAndInitialData(initialData) {
    try {
      state.projectPublicId = trimString(initialData.project_public_id, state.config.projectPublicId);
      state.projectId = trimString(initialData.project_id, state.config.projectId);

      state.membersUrl = trimString(initialData.members_url, state.config.membersUrl);
      state.invitationsUrl = trimString(initialData.invitations_url, state.config.invitationsUrl);

      state.isNew = toBooleanSafe(initialData.is_new, state.config.isNew);
      state.canEdit = toBooleanSafe(initialData.can_edit, state.config.canEdit);
      state.canManage = toBooleanSafe(initialData.can_manage, state.config.canManage);
      state.canManageTeam = toBooleanSafe(initialData.can_manage_team, state.config.canManageTeam);
      state.canWrite = toBooleanSafe(initialData.can_write, state.config.canWrite);

      state.demoMode = toBooleanSafe(initialData.demo_mode, state.config.demoMode);
      state.persistent = toBooleanSafe(initialData.persistent, state.config.persistent);
      state.publicViewer = toBooleanSafe(initialData.public_viewer, state.config.publicViewer);
      state.readOnly = toBooleanSafe(initialData.read_only, state.config.readOnly);
      state.authUnavailable = toBooleanSafe(initialData.auth_unavailable, state.config.authUnavailable);
      state.userBlocked = toBooleanSafe(initialData.user_blocked, state.config.userBlocked);
      state.accessBlocked = toBooleanSafe(initialData.access_blocked, state.config.accessBlocked);
      state.disabledReason = trimString(initialData.disabled_reason, state.config.disabledReason);

      if (state.publicViewer || state.authUnavailable || state.userBlocked || state.accessBlocked) {
        state.readOnly = true;
        state.demoMode = false;
        state.canWrite = false;
      }

      if (state.demoMode) {
        state.persistent = false;
        state.canWrite = false;
      }

      if (!state.persistent) {
        state.canWrite = false;
      }

      state.members = isArray(initialData.members) ? safeClone(initialData.members) : [];
      state.invitations = isArray(initialData.invitations) ? safeClone(initialData.invitations) : [];

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-public-id", state.projectPublicId || "");
        state.refs.card.setAttribute("data-project-id", state.projectId || "");
        state.refs.card.setAttribute("data-members-url", state.membersUrl || "");
        state.refs.card.setAttribute("data-invitations-url", state.invitationsUrl || "");
      }
    } catch (error) {}
  }

  function init() {
    if (state.initialized) {
      return state;
    }

    try {
      state.config = getConfig();
      state.refs = queryRefs();

      if (!state.refs.card) {
        return state;
      }

      var initialData = parseInitialData();
      initStateFromConfigAndInitialData(initialData);

      renderAll();
      wireEvents();
      updateDisabledState();

      state.initialized = true;

      emitLocal(state.config.parentEvents.ready || EVENT_READY, {
        members: state.members,
        invitations: state.invitations,
        canManage: state.canManage,
        canManageTeam: state.canManageTeam,
        canWrite: canOperate(),
        isNew: state.isNew,
        demoMode: state.demoMode,
        persistent: state.persistent,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,
        disabledReason: disabledReason()
      });

      try {
        window.__VECTOPLAN_PROJECT_TEAM_STATE__ = state;
      } catch (error) {}

      return state;
    } catch (error) {
      state.lastError = normalizeError(error);
      state.refs = state.refs || {};
      if (state.refs.card) {
        state.refs.card.classList.add(CLASS_ERROR);
      }
      setMessage("error", "Teamverwaltung konnte nicht initialisiert werden.");
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
        isLoading: state.isLoading,
        isSaving: state.isSaving,

        canEdit: state.canEdit,
        canManage: state.canManage,
        canManageTeam: state.canManageTeam,
        canWrite: canOperate(),
        disabledReason: disabledReason(),

        isNew: state.isNew,
        demoMode: state.demoMode,
        persistent: state.persistent,
        publicViewer: state.publicViewer,
        readOnly: state.readOnly,
        authUnavailable: state.authUnavailable,
        userBlocked: state.userBlocked,
        accessBlocked: state.accessBlocked,

        projectPublicId: state.projectPublicId,
        projectId: state.projectId,
        membersUrl: state.membersUrl,
        invitationsUrl: state.invitationsUrl,

        members: safeClone(state.members),
        invitations: safeClone(state.invitations),
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
    canOperate: canOperate,
    disabledReason: disabledReason,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      requestJson: requestJson,
      normalizeError: normalizeError,
      extractItems: extractItems,
      renderMembers: renderMembers,
      renderInvitations: renderInvitations,
      updateAfterProjectSaved: updateAfterProjectSaved,
      normalizeRole: normalizeRole
    }
  };

  try {
    global[EXPORT_NAME] = api;

    if (!global.__VECTOPLAN_DEBUG__) {
      global.__VECTOPLAN_DEBUG__ = {};
    }

    global.__VECTOPLAN_DEBUG__.projectTeam = api;
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