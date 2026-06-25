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
  - Demo-Modus und fehlendes manage-Recht deaktivieren persistente Aktionen.

  Sicherheitsregel:
  - Team-/Rechteverwaltung ist nur für Projektverwalter.
  - Owner kann nicht über diese UI entfernt werden.
  - Rollenänderungen auf Owner werden nicht angeboten.
*/

(function initVectoplanProjectTeam(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectTeam";
  var INTERNAL_VERSION = 1;

  var ROOT_SELECTOR = "[data-project-workspace]";
  var CARD_SELECTOR = "[data-project-team-card]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var EVENT_TEAM_CHANGED = "vectoplan:project:team:changed";
  var EVENT_INVITATION_CREATED = "vectoplan:project:invitation:created";
  var EVENT_MEMBER_CHANGED = "vectoplan:project:member:changed";
  var EVENT_ERROR = "vectoplan:project:error";

  var CLASS_LOADING = "is-loading";
  var CLASS_SAVING = "is-saving";
  var CLASS_ERROR = "is-error";

  var ROLE_VIEWER = "viewer";
  var ROLE_EDITOR = "editor";
  var ROLE_ADMIN = "admin";
  var ROLE_OWNER = "owner";

  var INVITATION_TERMINAL_STATUSES = {
    accepted: true,
    rejected: true,
    revoked: true,
    expired: true,
    failed: true
  };

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isLoading: false,
    isSaving: false,
    canManage: false,
    isNew: false,
    demoMode: false,
    projectPublicId: "",
    membersUrl: "",
    invitationsUrl: "",
    members: [],
    invitations: [],
    config: null,
    refs: {}
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
    return Array.isArray(value);
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
          normalized === "ja" ||
          normalized === "enabled"
        ) {
          return true;
        }

        if (
          normalized === "false" ||
          normalized === "no" ||
          normalized === "off" ||
          normalized === "nein" ||
          normalized === "disabled"
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
      return "";
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
        code: trimString(error.code, ""),
        status: error.status || error.statusCode || null,
        payload: error.payload || null
      };
    } catch (innerError) {
      return {
        name: "Error",
        message: "Unknown error",
        stack: ""
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
      return true;
    } catch (error) {
      return false;
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

      var paths = isObject(config.paths) ? config.paths : {};
      var project = isObject(config.project) ? config.project : {};
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};

      var publicId = trimString(
        config.projectPublicId ||
          project.public_id ||
          project.publicId ||
          "",
        ""
      );

      var demoMode = toBooleanSafe(
        config.demoMode ||
          config.demo_mode ||
          currentUser.demo_mode ||
          currentUser.demoMode ||
          currentUser.is_demo,
        false
      );

      var membersUrl = trimString(paths.members, "");
      var invitationsUrl = trimString(paths.invitations, "");

      if (!membersUrl && publicId && publicId !== "new") {
        membersUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/members";
      }

      if (!invitationsUrl && publicId && publicId !== "new") {
        invitationsUrl = "/v1/projects/" + encodeURIComponent(publicId) + "/invitations";
      }

      return {
        project: project,
        currentUser: currentUser,
        projectPublicId: publicId,
        membersUrl: membersUrl,
        invitationsUrl: invitationsUrl,
        isNew: toBooleanSafe(config.isNew || project.is_new || project.isNew, !publicId || publicId === "new"),
        canManage: toBooleanSafe(config.canManage, false),
        canEdit: toBooleanSafe(config.canEdit, false),
        demoMode: demoMode,
        persistent: toBooleanSafe(config.persistent !== undefined ? config.persistent : currentUser.persistent, !demoMode),
        parentEvents: {
          teamChanged: trimString(
            config.parentEvents && config.parentEvents.teamChanged,
            EVENT_TEAM_CHANGED
          )
        }
      };
    } catch (error) {
      return {
        project: {},
        currentUser: {},
        projectPublicId: "",
        membersUrl: "",
        invitationsUrl: "",
        isNew: true,
        canManage: false,
        canEdit: false,
        demoMode: false,
        persistent: true,
        parentEvents: {
          teamChanged: EVENT_TEAM_CHANGED
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

      inviteEmail: query("[data-project-team-invite-email]", card),
      inviteRole: query("[data-project-team-invite-role]", card),
      inviteSubmit: query("[data-project-team-invite-submit]", card),
      message: query("[data-project-team-message]", card),

      refresh: query("[data-project-team-refresh]", card),

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

  function canOperate() {
    return !!(state.canManage && !state.isNew && !state.demoMode && !state.isSaving && !state.isLoading);
  }

  function updateDisabledState() {
    try {
      var disabled = !canOperate();

      if (state.refs.inviteEmail) {
        state.refs.inviteEmail.disabled = disabled;
      }

      if (state.refs.inviteRole) {
        state.refs.inviteRole.disabled = disabled;
      }

      if (state.refs.inviteSubmit) {
        state.refs.inviteSubmit.disabled = disabled;
      }

      if (state.refs.refresh) {
        state.refs.refresh.disabled = state.isNew || state.isLoading;
      }

      queryAll("[data-project-team-member-role]", state.refs.card).forEach(function eachSelect(select) {
        try {
          var role = trimString(select.getAttribute("data-current-role"), select.value);
          select.disabled = disabled || role === ROLE_OWNER;
        } catch (error) {}
      });

      queryAll("[data-project-team-member-save]", state.refs.card).forEach(function eachButton(button) {
        try {
          var role = trimString(button.getAttribute("data-current-role"), "");
          button.disabled = disabled || role === ROLE_OWNER;
        } catch (error) {}
      });

      queryAll("[data-project-team-member-remove]", state.refs.card).forEach(function eachButton(button) {
        try {
          var role = trimString(button.getAttribute("data-current-role"), "");
          button.disabled = disabled || role === ROLE_OWNER;
        } catch (error) {}
      });

      queryAll("[data-project-team-invitation-revoke]", state.refs.card).forEach(function eachButton(button) {
        try {
          var status = trimString(button.getAttribute("data-status"), "pending").toLowerCase();
          button.disabled = disabled || !!INVITATION_TERMINAL_STATUSES[status];
        } catch (error) {}
      });

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-team-disabled", disabled ? "true" : "false");
      }
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
        project_public_id: trimString(data.project_public_id || project.public_id || project.publicId || state.projectPublicId, ""),
        project_id: data.project_id || project.id || "",
        can_manage: toBooleanSafe(data.can_manage, state.canManage),
        can_edit: toBooleanSafe(data.can_edit, state.config && state.config.canEdit),
        is_new: toBooleanSafe(data.is_new, state.isNew),
        demo_mode: toBooleanSafe(data.demo_mode, state.demoMode),
        members_url: trimString(data.members_url, state.membersUrl),
        invitations_url: trimString(data.invitations_url, state.invitationsUrl),
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
        project_id: "",
        can_manage: state.canManage,
        can_edit: false,
        is_new: state.isNew,
        demo_mode: state.demoMode,
        members_url: state.membersUrl,
        invitations_url: state.invitationsUrl,
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
          "Accept": "application/json"
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
        error.payload = data;
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

      if (preferredKey === "invitations" && isArray(data.pending_invitations)) {
        return data.pending_invitations;
      }

      if (isObject(data.data)) {
        return extractItems(data.data, preferredKey);
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

    if (normalized === "revoked") {
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
      return trimString(m.user_id || m.userId || user.id || "", "");
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
      return trimString(user.email || m.email || "", "");
    } catch (error) {
      return "";
    }
  }

  function memberRole(member) {
    try {
      var m = isObject(member) ? member : {};
      return trimString(m.role, ROLE_VIEWER).toLowerCase();
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

      var directKey = "can_" + key;
      return toBooleanSafe(m[directKey], toBooleanSafe(permissions[key], false));
    } catch (error) {
      return false;
    }
  }

  function appendPermissionChip(parent, label) {
    var chip = createElement("span", "vp-project-permission-chip", label);
    append(parent, chip);
  }

  function createRoleSelect(userId, role) {
    var select = createElement("select", "vp-project-select vp-project-select--compact");
    if (!select) {
      return null;
    }

    select.setAttribute("data-project-team-member-role", "");
    select.setAttribute("data-user-id", userId);
    select.setAttribute("data-current-role", role);

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
      option.selected = pair[0] === role;
      append(select, option);
    });

    if (role === ROLE_OWNER) {
      var ownerOption = createElement("option", "", "Owner");
      if (ownerOption) {
        ownerOption.value = ROLE_OWNER;
        ownerOption.selected = true;
        append(select, ownerOption);
      }
    }

    select.disabled = !canOperate() || role === ROLE_OWNER;

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

          var row = createElement("article", "vp-project-team-row");
          if (!row) {
            return;
          }

          row.setAttribute("data-project-team-member", "");
          row.setAttribute("data-user-id", userId);
          row.setAttribute("data-role", role);
          row.setAttribute("data-status", status);

          var identity = createElement("div", "vp-project-team-row__identity");
          var avatar = createElement("div", "vp-project-avatar", name ? name.charAt(0).toUpperCase() : "?");
          if (avatar) {
            avatar.setAttribute("aria-hidden", "true");
          }

          var identityText = createElement("div");
          var title = createElement("h4", "vp-project-team-row__name", name);
          var meta = createElement("p", "vp-project-team-row__meta", email || ("User-ID: " + userId));

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

          if (memberPermission(m, "view") || role === ROLE_OWNER || role === ROLE_ADMIN || role === ROLE_EDITOR || role === ROLE_VIEWER) {
            appendPermissionChip(permissions, "Ansehen");
          }

          if (memberPermission(m, "edit") || role === ROLE_OWNER || role === ROLE_ADMIN || role === ROLE_EDITOR) {
            appendPermissionChip(permissions, "Bearbeiten");
          }

          if (memberPermission(m, "manage") || role === ROLE_OWNER || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Verwalten");
          }

          if (memberPermission(m, "delete") || role === ROLE_OWNER) {
            appendPermissionChip(permissions, "Löschen");
          }

          if (memberPermission(m, "embed") || role === ROLE_OWNER || role === ROLE_ADMIN) {
            appendPermissionChip(permissions, "Einbetten");
          }

          var actions = createElement("div", "vp-project-team-row__actions");

          var save = createElement("button", "vp-project-btn vp-project-btn--ghost", "Rolle speichern");
          if (save) {
            save.type = "button";
            save.setAttribute("data-project-team-member-save", "");
            save.setAttribute("data-user-id", userId);
            save.setAttribute("data-current-role", role);
            save.disabled = !canOperate() || role === ROLE_OWNER;
          }

          var remove = createElement("button", "vp-project-btn vp-project-btn--danger", "Entfernen");
          if (remove) {
            remove.type = "button";
            remove.setAttribute("data-project-team-member-remove", "");
            remove.setAttribute("data-user-id", userId);
            remove.setAttribute("data-current-role", role);
            remove.disabled = !canOperate() || role === ROLE_OWNER;
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
      return trimString(inv.email || inv.email_normalized || inv.emailNormalized || "", "");
    } catch (error) {
      return "";
    }
  }

  function invitationRole(invitation) {
    try {
      var inv = isObject(invitation) ? invitation : {};
      return trimString(inv.role, ROLE_VIEWER).toLowerCase();
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
            revoke.disabled = !canOperate() || !!INVITATION_TERMINAL_STATUSES[status];
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
      setMessage("error", normalized.message || "Teamdaten konnten nicht geladen werden.");
      emitLocal(EVENT_ERROR, {
        error: normalized
      });
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

  function normalizeInviteRole(role) {
    try {
      var normalized = trimString(role, ROLE_VIEWER).toLowerCase();

      if (normalized === ROLE_OWNER) {
        return ROLE_ADMIN;
      }

      if (normalized === ROLE_ADMIN || normalized === ROLE_EDITOR || normalized === ROLE_VIEWER) {
        return normalized;
      }

      return ROLE_VIEWER;
    } catch (error) {
      return ROLE_VIEWER;
    }
  }

  async function inviteByEmail() {
    try {
      if (!canOperate()) {
        if (state.demoMode) {
          setMessage("warning", "Im Demo-Modus werden keine echten Einladungen versendet.");
        } else {
          setMessage("warning", "Du hast keine Berechtigung, Einladungen zu erstellen.");
        }
        return false;
      }

      var email = trimString(state.refs.inviteEmail && state.refs.inviteEmail.value, "").toLowerCase();
      var role = normalizeInviteRole(state.refs.inviteRole && state.refs.inviteRole.value);

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

      emitChangeEvent(EVENT_INVITATION_CREATED, {
        response: response,
        email: email,
        role: role
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      setMessage("error", normalized.message || "Einladung konnte nicht erstellt werden.");
      emitLocal(EVENT_ERROR, {
        error: normalized
      });
      return false;
    } finally {
      setSaving(false);
    }
  }

  function roleForUserId(userId) {
    try {
      var selector = "[data-project-team-member-role][data-user-id='" + String(userId).replace(/'/g, "\\'") + "']";
      var select = query(selector, state.refs.card);
      return normalizeInviteRole(select && select.value);
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
        setMessage("warning", "Du hast keine Berechtigung, Rollen zu ändern.");
        return false;
      }

      var role = roleForUserId(uid);

      if (role === ROLE_OWNER) {
        setMessage("warning", "Owner kann nicht über diese UI gesetzt werden.");
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

      emitChangeEvent(EVENT_MEMBER_CHANGED, {
        response: response,
        user_id: uid,
        role: role
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      setMessage("error", normalized.message || "Rolle konnte nicht gespeichert werden.");
      emitLocal(EVENT_ERROR, {
        error: normalized
      });
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
        setMessage("warning", "Du hast keine Berechtigung, Mitglieder zu entfernen.");
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

      emitChangeEvent(EVENT_MEMBER_CHANGED, {
        response: response,
        user_id: uid,
        removed: true
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      setMessage("error", normalized.message || "Mitglied konnte nicht entfernt werden.");
      emitLocal(EVENT_ERROR, {
        error: normalized
      });
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
        setMessage("warning", "Du hast keine Berechtigung, Einladungen zu widerrufen.");
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

      emitChangeEvent(EVENT_INVITATION_CREATED, {
        response: response,
        invitation_id: id,
        revoked: true
      });

      await refreshTeam({ silent: true });

      return true;
    } catch (error) {
      var normalized = normalizeError(error);
      setMessage("error", normalized.message || "Einladung konnte nicht widerrufen werden.");
      emitLocal(EVENT_ERROR, {
        error: normalized
      });
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

  function emitChangeEvent(type, detail) {
    try {
      var eventType = type || EVENT_TEAM_CHANGED;

      var payloadDetail = {
        ...(detail || {}),
        projectPublicId: state.projectPublicId,
        members: state.members,
        invitations: state.invitations
      };

      emitLocal(eventType, payloadDetail);
      emitLocal(EVENT_TEAM_CHANGED, payloadDetail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage(
            {
              type: eventType,
              kind: eventType,
              source: "vectoplan-app.project-team",
              version: INTERNAL_VERSION,
              detail: payloadDetail,
              ts: Date.now()
            },
            window.location.origin
          );
        }
      } catch (postError) {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage(
              {
                type: eventType,
                kind: eventType,
                source: "vectoplan-app.project-team",
                version: INTERNAL_VERSION,
                detail: payloadDetail,
                ts: Date.now()
              },
              "*"
            );
          }
        } catch (_) {}
      }

      try {
        if (
          window.parent &&
          window.parent !== window &&
          window.parent.dispatchEvent &&
          typeof window.parent.CustomEvent === "function"
        ) {
          window.parent.dispatchEvent(
            new window.parent.CustomEvent(eventType, {
              detail: payloadDetail
            })
          );
          window.parent.dispatchEvent(
            new window.parent.CustomEvent(EVENT_TEAM_CHANGED, {
              detail: payloadDetail
            })
          );
        }
      } catch (_) {}

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event("project-sidebar:refresh"));
        }
      } catch (_) {}

      return true;
    } catch (error) {
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

      var refreshButton = closest(target, "[data-project-team-refresh]");
      if (refreshButton) {
        event.preventDefault();
        void refreshTeam();
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

  function wireEvents() {
    try {
      addListener(state.refs.card, "click", onCardClick);
      addListener(state.refs.card, "change", onCardChange);
      addListener(state.refs.inviteEmail, "keydown", onInviteKeydown);

      addListener(window, "message", function onMessage(event) {
        try {
          var data = event && event.data;

          if (!data || typeof data !== "object") {
            return;
          }

          var type = trimString(data.type || data.kind, "");

          if (type === "vectoplan:project:saved" || type === "vectoplan:project:created") {
            var detail = isObject(data.detail) ? data.detail : {};
            var project = isObject(detail.project) ? detail.project : {};

            var publicId = trimString(project.public_id || project.publicId || detail.projectPublicId || "", "");

            if (publicId && publicId !== "new") {
              state.projectPublicId = publicId;
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
            }
          }
        } catch (error) {}
      });
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

      state.projectPublicId =
        trimString(state.refs.card.getAttribute("data-project-public-id"), "") ||
        state.config.projectPublicId;

      state.membersUrl =
        trimString(state.refs.card.getAttribute("data-members-url"), "") ||
        state.config.membersUrl;

      state.invitationsUrl =
        trimString(state.refs.card.getAttribute("data-invitations-url"), "") ||
        state.config.invitationsUrl;

      state.isNew = toBooleanSafe(
        state.refs.card.getAttribute("data-project-is-new"),
        state.config.isNew
      );

      state.canManage = toBooleanSafe(
        state.refs.card.getAttribute("data-project-can-manage"),
        state.config.canManage
      );

      state.demoMode = toBooleanSafe(
        state.refs.card.getAttribute("data-project-demo-mode"),
        state.config.demoMode
      );

      var initialData = parseInitialData();

      state.members = isArray(initialData.members) ? safeClone(initialData.members) : [];
      state.invitations = isArray(initialData.invitations) ? safeClone(initialData.invitations) : [];

      renderAll();
      wireEvents();
      updateDisabledState();

      state.initialized = true;

      emitLocal("vectoplan:project-team:ready", {
        members: state.members,
        invitations: state.invitations,
        canManage: state.canManage,
        isNew: state.isNew,
        demoMode: state.demoMode
      });

      try {
        window.__VECTOPLAN_PROJECT_TEAM_STATE__ = state;
      } catch (_) {}

      return state;
    } catch (error) {
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
        canManage: state.canManage,
        isNew: state.isNew,
        demoMode: state.demoMode,
        projectPublicId: state.projectPublicId,
        membersUrl: state.membersUrl,
        invitationsUrl: state.invitationsUrl,
        members: state.members,
        invitations: state.invitations
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
    invite: inviteByEmail,
    saveMemberRole: saveMemberRole,
    removeMember: removeMember,
    revokeInvitation: revokeInvitation,
    renderAll: renderAll,
    getSnapshot: getSnapshot,
    setMessage: setMessage,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      requestJson: requestJson,
      normalizeError: normalizeError,
      extractItems: extractItems,
      renderMembers: renderMembers,
      renderInvitations: renderInvitations
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