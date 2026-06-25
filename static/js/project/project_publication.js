/* services/vectoplan-app/static/js/project/project_publication.js */

/*
  VECTOPLAN Project Publication

  Zweck:
  - Speichert Veröffentlichungseinstellungen für Workspace-Reiter.
  - Arbeitet gegen /v1/projects/<project_id>/publication.
  - Steuert nur:
      published_workspaces
      require_auth
      require_project_permission
  - Nutzt die aktuelle Projekt-Sichtbarkeit aus project_form.js.
  - Speichert keine Projekt-Basisdaten.
  - Speichert keine Team-/Einladungsdaten.
  - Speichert keine Chunk-/Editor-/2D-/LV-Fachdaten.

  Sicherheitsregel:
  - Admin, Team, Rechte, Einstellungen und Systemreferenzen werden nie
    als veröffentlichbare Workspaces gesendet.
  - Im Demo-Modus und bei fehlendem manage-Recht wird nicht gespeichert.
*/

(function initVectoplanProjectPublication(global) {
  "use strict";

  var EXPORT_NAME = "VectoplanProjectPublication";
  var INTERNAL_VERSION = 1;

  var ROOT_SELECTOR = "[data-project-workspace]";
  var CARD_SELECTOR = "[data-project-publication-card]";
  var FORM_SELECTOR = "[data-project-form]";
  var ALERT_SELECTOR = "[data-project-alert]";

  var EVENT_PUBLICATION_CHANGED = "vectoplan:project:publication:changed";
  var EVENT_ERROR = "vectoplan:project:error";

  var CLASS_SELECTED = "is-selected";
  var CLASS_EFFECTIVE = "is-effective";
  var CLASS_SAVING = "is-saving";
  var CLASS_ERROR = "is-error";

  var WORKSPACES = [
    "project",
    "map",
    "editor3d",
    "cad2d",
    "lv",
    "versions"
  ];

  var FORBIDDEN_WORKSPACES = {
    admin: true,
    team: true,
    settings: true,
    permissions: true,
    system: true
  };

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isSaving: false,
    isDirty: false,
    canManage: false,
    isNew: false,
    demoMode: false,
    projectPublicId: "",
    endpoint: "",
    initialData: null,
    currentData: null,
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
      return true;
    } catch (error) {
      return false;
    }
  }

  function normalizeVisibility(value, fallback) {
    try {
      var text = trimString(value, fallback || "private").toLowerCase().replace(/-/g, "_");

      if (text === "public" || text === "öffentlich" || text === "oeffentlich" || text === "open") {
        return "public";
      }

      if (
        text === "unlisted" ||
        text === "not_listed" ||
        text === "nicht_gelistet" ||
        text === "link" ||
        text === "link_shared"
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
      var text = trimString(value, "").toLowerCase().replace(/-/g, "_");

      var aliases = {
        "project": "project",
        "project_info": "project",
        "projectinfo": "project",
        "info": "project",

        "map": "map",
        "karte": "map",
        "openlayer": "map",
        "openlayers": "map",

        "3d": "editor3d",
        "editor": "editor3d",
        "editor3d": "editor3d",
        "editor_3d": "editor3d",
        "viewer3d": "editor3d",

        "2d": "cad2d",
        "cad": "cad2d",
        "cad2d": "cad2d",
        "cad_2d": "cad2d",
        "plan": "cad2d",

        "lv": "lv",
        "boq": "lv",
        "leistungsverzeichnis": "lv",

        "versions": "versions",
        "versionen": "versions",
        "history": "versions",

        "admin": "admin",
        "team": "team",
        "settings": "settings",
        "permissions": "permissions",
        "system": "system"
      };

      return aliases[text] || "";
    } catch (error) {
      return "";
    }
  }

  function isAllowedWorkspace(workspace) {
    var normalized = normalizeWorkspace(workspace);
    return WORKSPACES.indexOf(normalized) !== -1 && !FORBIDDEN_WORKSPACES[normalized];
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

      var demoMode = toBooleanSafe(
        config.demoMode ||
          config.demo_mode ||
          currentUser.demo_mode ||
          currentUser.demoMode ||
          currentUser.is_demo,
        false
      );

      var persistent = toBooleanSafe(
        config.persistent !== undefined ? config.persistent : currentUser.persistent,
        !demoMode
      );

      var publicId = trimString(
        config.projectPublicId ||
          project.public_id ||
          project.publicId ||
          "",
        ""
      );

      var endpoint = trimString(paths.publication, "");
      if (!endpoint && publicId && publicId !== "new") {
        endpoint = "/v1/projects/" + encodeURIComponent(publicId) + "/publication";
      }

      return {
        project: project,
        currentUser: currentUser,
        projectPublicId: publicId,
        projectVisibility: normalizeVisibility(config.projectVisibility || project.visibility, "private"),
        isNew: toBooleanSafe(config.isNew || project.is_new || project.isNew, !publicId || publicId === "new"),
        canManage: toBooleanSafe(config.canManage, false),
        canEdit: toBooleanSafe(config.canEdit, false),
        demoMode: demoMode,
        persistent: persistent,
        endpoint: endpoint,
        parentEvents: {
          publicationChanged: trimString(
            config.parentEvents && config.parentEvents.publicationChanged,
            EVENT_PUBLICATION_CHANGED
          )
        }
      };
    } catch (error) {
      return {
        project: {},
        currentUser: {},
        projectPublicId: "",
        projectVisibility: "private",
        isNew: true,
        canManage: false,
        canEdit: false,
        demoMode: false,
        persistent: true,
        endpoint: "",
        parentEvents: {
          publicationChanged: EVENT_PUBLICATION_CHANGED
        }
      };
    }
  }

  function queryRefs() {
    var card = query(CARD_SELECTOR);

    return {
      document: getDocument(),
      root: query(ROOT_SELECTOR),
      form: query(FORM_SELECTOR),
      card: card,
      alert: query(ALERT_SELECTOR),

      visibilityInput: queryById("projectVisibility"),
      save: query("[data-project-publication-save]", card),
      reset: query("[data-project-publication-reset]", card),
      status: query("[data-project-publication-status]", card),

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
        state.refs.save.disabled = state.isSaving || !state.canManage || state.isNew || state.demoMode;
        state.refs.save.setAttribute("aria-busy", state.isSaving ? "true" : "false");

        if (state.isSaving) {
          state.refs.save.setAttribute("data-original-text", state.refs.save.textContent || "");
          state.refs.save.textContent = "Veröffentlichung wird gespeichert…";
        } else {
          var original = state.refs.save.getAttribute("data-original-text");
          state.refs.save.textContent = original || "Veröffentlichung speichern";
        }
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = state.isSaving || !state.canManage || state.isNew || state.demoMode;
      }
    } catch (error) {}
  }

  function setDirty(isDirty) {
    try {
      state.isDirty = !!isDirty;

      if (state.refs.card) {
        state.refs.card.classList.toggle("is-dirty", state.isDirty);
        state.refs.card.setAttribute("data-project-publication-dirty", state.isDirty ? "true" : "false");
      }
    } catch (error) {}
  }

  function currentVisibility() {
    try {
      var fromInput = getValue(state.refs.visibilityInput);
      var fromRoot = state.refs.root ? state.refs.root.getAttribute("data-project-visibility") : "";
      var fromCard = state.refs.card ? state.refs.card.getAttribute("data-project-visibility") : "";
      return normalizeVisibility(fromInput || fromRoot || fromCard || state.config.projectVisibility, "private");
    } catch (error) {
      return "private";
    }
  }

  function emptyWorkspaceMap(value) {
    var result = {};
    WORKSPACES.forEach(function eachWorkspace(workspace) {
      result[workspace] = !!value;
    });
    return result;
  }

  function normalizeWorkspaces(value) {
    var result = emptyWorkspaceMap(false);

    try {
      if (!isObject(value)) {
        return result;
      }

      Object.keys(value).forEach(function eachKey(key) {
        var workspace = normalizeWorkspace(key);
        if (isAllowedWorkspace(workspace)) {
          result[workspace] = toBooleanSafe(value[key], false);
        }
      });

      return result;
    } catch (error) {
      return result;
    }
  }

  function effectiveWorkspaces(visibility, published) {
    var normalizedVisibility = normalizeVisibility(visibility, "private");
    var desired = normalizeWorkspaces(published);

    if (normalizedVisibility === "private") {
      return emptyWorkspaceMap(false);
    }

    return desired;
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
      var publication = isObject(project.publication) ? project.publication : {};

      var source = Object.keys(fromJson).length ? fromJson : publication;

      return {
        visibility: normalizeVisibility(source.visibility || project.visibility || state.config.projectVisibility, "private"),
        published_workspaces: normalizeWorkspaces(
          source.published_workspaces ||
            source.publishedWorkspaces ||
            publication.published_workspaces ||
            publication.publishedWorkspaces ||
            {}
        ),
        effective_published_workspaces: normalizeWorkspaces(
          source.effective_published_workspaces ||
            source.effectivePublishedWorkspaces ||
            {}
        ),
        require_auth: toBooleanSafe(source.require_auth || source.requireAuth, false),
        require_project_permission: toBooleanSafe(
          source.require_project_permission || source.requireProjectPermission,
          false
        )
      };
    } catch (error) {
      return {
        visibility: "private",
        published_workspaces: emptyWorkspaceMap(false),
        effective_published_workspaces: emptyWorkspaceMap(false),
        require_auth: false,
        require_project_permission: false
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

      var visibility = normalizeVisibility(
        publication.visibility ||
          data.visibility ||
          currentVisibility(),
        "private"
      );

      var published = normalizeWorkspaces(
        publication.published_workspaces ||
          publication.publishedWorkspaces ||
          data.published_workspaces ||
          data.publishedWorkspaces ||
          collectData().published_workspaces
      );

      var effective = normalizeWorkspaces(
        publication.effective_published_workspaces ||
          publication.effectivePublishedWorkspaces ||
          data.effective_published_workspaces ||
          data.effectivePublishedWorkspaces ||
          effectiveWorkspaces(visibility, published)
      );

      return {
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective,
        require_auth: toBooleanSafe(publication.require_auth || publication.requireAuth || data.require_auth || data.requireAuth, visibility === "private"),
        require_project_permission: toBooleanSafe(
          publication.require_project_permission ||
            publication.requireProjectPermission ||
            data.require_project_permission ||
            data.requireProjectPermission,
          visibility === "private"
        )
      };
    } catch (error) {
      return collectData();
    }
  }

  function updateStatus(data) {
    try {
      var status = state.refs.status;
      if (!status) {
        return;
      }

      var publication = isObject(data) ? data : collectData();
      var visibility = normalizeVisibility(publication.visibility, "private");
      var effective = normalizeWorkspaces(publication.effective_published_workspaces);
      var hasEffective = WORKSPACES.some(function someWorkspace(workspace) {
        return !!effective[workspace];
      });

      status.classList.remove(
        "vp-project-chip--muted",
        "vp-project-chip--success",
        "vp-project-chip--warning"
      );

      if (visibility === "private") {
        status.classList.add("vp-project-chip--muted");
        status.textContent = "Privat";
      } else if (hasEffective) {
        status.classList.add("vp-project-chip--success");
        status.textContent = "Reiter veröffentlicht";
      } else {
        status.classList.add("vp-project-chip--warning");
        status.textContent = "Keine Reiter veröffentlicht";
      }
    } catch (error) {}
  }

  function applyData(data, options) {
    try {
      var opts = isObject(options) ? options : {};
      var publication = isObject(data) ? data : {};
      var visibility = normalizeVisibility(publication.visibility || currentVisibility(), "private");
      var published = normalizeWorkspaces(publication.published_workspaces);
      var effective = normalizeWorkspaces(
        publication.effective_published_workspaces ||
          effectiveWorkspaces(visibility, published)
      );

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
      }

      (state.refs.checkboxes || []).forEach(function applyCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || "");
          if (!isAllowedWorkspace(workspace)) {
            input.checked = false;
            input.disabled = true;
            return;
          }

          input.checked = !!published[workspace];
        } catch (error) {}
      });

      (state.refs.optionCards || []).forEach(function applyCard(card) {
        try {
          var workspace = normalizeWorkspace(card.getAttribute("data-workspace") || "");
          if (!isAllowedWorkspace(workspace)) {
            card.classList.remove(CLASS_SELECTED);
            card.classList.remove(CLASS_EFFECTIVE);
            return;
          }

          card.classList.toggle(CLASS_SELECTED, !!published[workspace]);
          card.classList.toggle(CLASS_EFFECTIVE, !!effective[workspace]);
        } catch (error) {}
      });

      setChecked(state.refs.requireAuth, toBooleanSafe(publication.require_auth, visibility === "private"));
      setChecked(
        state.refs.requirePermission,
        toBooleanSafe(publication.require_project_permission, visibility === "private")
      );

      updateDisabledState();
      updateStatus({
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective
      });

      state.currentData = {
        visibility: visibility,
        published_workspaces: published,
        effective_published_workspaces: effective,
        require_auth: getChecked(state.refs.requireAuth),
        require_project_permission: getChecked(state.refs.requirePermission)
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
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || "");

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

      return {
        visibility: visibility,
        published_workspaces: published,
        require_auth: requireAuth,
        require_project_permission: requirePermission
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

  function validateBeforeSave() {
    try {
      if (state.isNew) {
        return {
          ok: false,
          message: "Speichere das Projekt zuerst. Danach kannst du Reiter veröffentlichen."
        };
      }

      if (!state.canManage) {
        return {
          ok: false,
          message: "Du hast keine Berechtigung, Veröffentlichungseinstellungen zu ändern."
        };
      }

      if (state.demoMode) {
        return {
          ok: false,
          message: "Im Demo-Modus werden Veröffentlichungseinstellungen nicht dauerhaft gespeichert."
        };
      }

      if (!buildEndpoint()) {
        return {
          ok: false,
          message: "Publication-Endpunkt fehlt."
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

      var payload = collectData();

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

      state.initialData = safeClone(publication);
      applyData(publication, { keepDirty: false });

      setAlert("success", "Veröffentlichung wurde gespeichert.");

      emitChangeEvent(response, publication);

      return true;
    } catch (error) {
      var normalized = normalizeError(error);

      if (state.refs.card) {
        state.refs.card.classList.add(CLASS_ERROR);
      }

      setAlert("error", normalized.message || "Veröffentlichung konnte nicht gespeichert werden.");

      emitLocal(EVENT_ERROR, {
        error: normalized,
        publication: collectData()
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

  function emitChangeEvent(response, publication) {
    try {
      var detail = {
        response: response || {},
        publication: publication || collectData(),
        projectPublicId: state.projectPublicId,
        endpoint: buildEndpoint()
      };

      var type = state.config && state.config.parentEvents
        ? state.config.parentEvents.publicationChanged || EVENT_PUBLICATION_CHANGED
        : EVENT_PUBLICATION_CHANGED;

      emitLocal(type, detail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage(
            {
              type: type,
              kind: type,
              source: "vectoplan-app.project-publication",
              version: INTERNAL_VERSION,
              detail: detail,
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
                type: type,
                kind: type,
                source: "vectoplan-app.project-publication",
                version: INTERNAL_VERSION,
                detail: detail,
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
            new window.parent.CustomEvent(type, {
              detail: detail
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

  function updateDisabledState() {
    try {
      var visibility = currentVisibility();
      var disabled = state.isSaving || !state.canManage || state.isNew || state.demoMode;

      (state.refs.checkboxes || []).forEach(function disableCheckbox(input) {
        try {
          var workspace = normalizeWorkspace(input.getAttribute("data-workspace") || input.name || "");
          input.disabled = disabled || !isAllowedWorkspace(workspace);
        } catch (error) {}
      });

      if (state.refs.requireAuth) {
        state.refs.requireAuth.disabled = disabled || visibility === "private";
      }

      if (state.refs.requirePermission) {
        state.refs.requirePermission.disabled = disabled || visibility === "private";
      }

      if (state.refs.save) {
        state.refs.save.disabled = disabled;
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = disabled;
      }

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
        state.refs.card.setAttribute("data-project-publication-disabled", disabled ? "true" : "false");
      }
    } catch (error) {}
  }

  function onInputChange() {
    try {
      var data = collectData();
      var effective = effectiveWorkspaces(data.visibility, data.published_workspaces);

      applyData(
        {
          visibility: data.visibility,
          published_workspaces: data.published_workspaces,
          effective_published_workspaces: effective,
          require_auth: data.require_auth,
          require_project_permission: data.require_project_permission
        },
        { keepDirty: true }
      );

      setDirty(true);
      setAlert("", "");
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
      var visibility = normalizeVisibility(detail.visibility || currentVisibility(), "private");

      if (state.refs.card) {
        state.refs.card.setAttribute("data-project-visibility", visibility);
      }

      var data = collectData();
      data.visibility = visibility;

      applyData(
        {
          visibility: visibility,
          published_workspaces: data.published_workspaces,
          effective_published_workspaces: effectiveWorkspaces(visibility, data.published_workspaces),
          require_auth: visibility === "private" ? true : data.require_auth,
          require_project_permission: visibility === "private" ? true : data.require_project_permission
        },
        { keepDirty: true }
      );

      setDirty(true);
    } catch (error) {}
  }

  function wireEvents() {
    try {
      addListener(state.refs.save, "click", onSaveClick);
      addListener(state.refs.reset, "click", onResetClick);

      (state.refs.checkboxes || []).forEach(function wireCheckbox(input) {
        addListener(input, "change", onInputChange);
      });

      addListener(state.refs.requireAuth, "change", onInputChange);
      addListener(state.refs.requirePermission, "change", onInputChange);

      if (state.refs.root) {
        addListener(state.refs.root, "vectoplan:project:visibility:changed", onVisibilityChanged);
      }

      addListener(window, "message", function onMessage(event) {
        try {
          var data = event && event.data;

          if (!data || typeof data !== "object") {
            return;
          }

          var type = trimString(data.type || data.kind, "");

          if (type === "vectoplan:project:visibility:changed") {
            onVisibilityChanged({ detail: data.detail || data });
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

      state.endpoint =
        trimString(state.refs.card.getAttribute("data-publication-url"), "") ||
        state.config.endpoint;

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

      state.initialData = parseInitialData();
      state.currentData = safeClone(state.initialData);

      applyData(state.initialData, { keepDirty: false });
      wireEvents();
      updateDisabledState();

      state.initialized = true;

      emitLocal("vectoplan:project-publication:ready", {
        publication: state.currentData,
        canManage: state.canManage,
        isNew: state.isNew,
        demoMode: state.demoMode
      });

      try {
        window.__VECTOPLAN_PROJECT_PUBLICATION_STATE__ = state;
      } catch (_) {}

      return state;
    } catch (error) {
      setAlert("error", "Veröffentlichungseinstellungen konnten nicht initialisiert werden.");
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
        isSaving: state.isSaving,
        isDirty: state.isDirty,
        canManage: state.canManage,
        isNew: state.isNew,
        demoMode: state.demoMode,
        projectPublicId: state.projectPublicId,
        endpoint: buildEndpoint(),
        initialData: state.initialData,
        currentData: collectData()
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
    collectData: collectData,
    applyData: applyData,
    getSnapshot: getSnapshot,
    setAlert: setAlert,
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      normalizeVisibility: normalizeVisibility,
      normalizeWorkspace: normalizeWorkspace,
      effectiveWorkspaces: effectiveWorkspaces,
      requestJson: requestJson,
      normalizeError: normalizeError
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