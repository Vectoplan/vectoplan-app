/* services/vectoplan-app/static/js/project/project_form.js */

/*
  VECTOPLAN Project Form

  Zweck:
  - Projektformular im Workspace-iframe steuern.
  - Neues Projekt über POST /v1/projects erstellen.
  - Bestehendes Projekt über PATCH /v1/projects/<public_id> aktualisieren.
  - Nach Erstellung Parent-Shell auf /project=<public_id> weiterleiten.
  - Nach Speichern Parent-Shell informieren, damit Sidebar und Workspace-Gating
    aktualisiert werden können.
  - Kein direkter Zugriff auf Chunk-, Editor-, 2D- oder LV-Daten.

  Neuer Formularstand:
  - Gesendet werden nur:
      name
      title
      description
      address_text
      address.text
      visibility
  - Keine manuellen Felder mehr für:
      street
      house_number
      postal_code
      city
      region
      country
      latitude
      longitude
      coordinate_srid
      is_public
  - is_public wird serverseitig aus visibility abgeleitet.
  - Veröffentlichte Reiter werden separat über project_publication.js gespeichert.
  - Team/Einladungen werden separat über project_team.js gespeichert.

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

  var INTERNAL_VERSION = 2;

  var DEFAULT_CREATE_PATH = "/v1/projects";
  var DEFAULT_PROJECT_NEW_URL = "/project=new";
  var DEFAULT_PROJECT_ROOT_URL = "/";

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

  var FIELD_ERROR_CLASS = "vp-project-field__error";

  var EVENT_SAVED = "vectoplan:project:saved";
  var EVENT_CREATED = "vectoplan:project:created";
  var EVENT_UPDATED = "vectoplan:project:updated";
  var EVENT_CONFIGURED = "vectoplan:project:configured";
  var EVENT_DIRTY = "vectoplan:project:dirty";
  var EVENT_ERROR = "vectoplan:project:error";
  var EVENT_VISIBILITY_CHANGED = "vectoplan:project:visibility:changed";

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
    canEdit: true,
    canManage: false,
    demoMode: false,
    persistent: true,
    authenticated: true,
    isDirty: false,
    isSaving: false,
    lastSavedAt: null,
    lastError: null,
    originalPayload: null,
    currentProject: null,
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
      var text = trimString(value, fallback || "private")
        .toLowerCase()
        .replace(/-/g, "_");

      if (text === "öffentlich" || text === "oeffentlich" || text === "open" || text === "listed") {
        return "public";
      }

      if (
        text === "not_listed" ||
        text === "nicht_gelistet" ||
        text === "link" ||
        text === "link_shared" ||
        text === "share_link"
      ) {
        return "unlisted";
      }

      if (text === "shared" || text === "geteilt" || text === "privat" || text === "internal") {
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
      var parentEvents = isObject(config.parentEvents) ? config.parentEvents : {};
      var project = isObject(config.project) ? config.project : {};
      var currentUser = isObject(config.currentUser) ? config.currentUser : {};
      var access = isObject(config.access) ? config.access : isObject(project.access) ? project.access : {};

      var demoMode = toBooleanSafe(
        config.demoMode ||
          config.demo_mode ||
          currentUser.demo_mode ||
          currentUser.demoMode ||
          currentUser.is_demo,
        false
      );

      var authenticated = toBooleanSafe(
        config.authenticated ||
          currentUser.authenticated ||
          currentUser.is_authenticated ||
          currentUser.isAuthenticated,
        !demoMode
      );

      var persistent = toBooleanSafe(
        config.persistent !== undefined ? config.persistent : currentUser.persistent,
        authenticated && !demoMode
      );

      var canEdit = toBooleanSafe(config.canEdit, true) && persistent && !demoMode;
      var canManage = toBooleanSafe(config.canManage, false);

      return {
        version: INTERNAL_VERSION,
        isNew: toBooleanSafe(config.isNew, true),
        canEdit: canEdit,
        canManage: canManage,
        demoMode: demoMode,
        authenticated: authenticated,
        persistent: persistent,
        project: project,
        currentProject: isObject(config.currentProject) ? config.currentProject : project,
        currentUser: currentUser,
        access: access,
        projectId: trimString(config.projectId || project.id || project.project_id, ""),
        projectPublicId: trimString(
          config.projectPublicId ||
            project.public_id ||
            project.publicId ||
            (config.isNew ? "new" : ""),
          config.isNew ? "new" : ""
        ),
        projectVisibility: normalizeVisibility(config.projectVisibility || project.visibility, "private"),
        paths: {
          createProject: trimString(paths.createProject, DEFAULT_CREATE_PATH),
          updateProject: trimString(paths.updateProject, ""),
          getProject: trimString(paths.getProject, ""),
          context: trimString(paths.context, ""),
          publication: trimString(paths.publication, ""),
          members: trimString(paths.members, ""),
          invitations: trimString(paths.invitations, ""),
          projectRoot: trimString(paths.projectRoot, DEFAULT_PROJECT_ROOT_URL),
          projectNew: trimString(paths.projectNew, DEFAULT_PROJECT_NEW_URL)
        },
        parentEvents: {
          saved: trimString(parentEvents.saved, EVENT_SAVED),
          created: trimString(parentEvents.created, EVENT_CREATED),
          updated: trimString(parentEvents.updated, EVENT_UPDATED),
          deleted: trimString(parentEvents.deleted, "vectoplan:project:deleted"),
          configured: trimString(parentEvents.configured, EVENT_CONFIGURED),
          publicationChanged: trimString(parentEvents.publicationChanged, "vectoplan:project:publication:changed"),
          teamChanged: trimString(parentEvents.teamChanged, "vectoplan:project:team:changed")
        }
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        isNew: true,
        canEdit: true,
        canManage: false,
        demoMode: false,
        authenticated: true,
        persistent: true,
        project: {},
        currentProject: {},
        currentUser: {},
        access: {},
        projectId: "",
        projectPublicId: "new",
        projectVisibility: "private",
        paths: {
          createProject: DEFAULT_CREATE_PATH,
          updateProject: "",
          getProject: "",
          context: "",
          publication: "",
          members: "",
          invitations: "",
          projectRoot: DEFAULT_PROJECT_ROOT_URL,
          projectNew: DEFAULT_PROJECT_NEW_URL
        },
        parentEvents: {
          saved: EVENT_SAVED,
          created: EVENT_CREATED,
          updated: EVENT_UPDATED,
          deleted: "vectoplan:project:deleted",
          configured: EVENT_CONFIGURED,
          publicationChanged: "vectoplan:project:publication:changed",
          teamChanged: "vectoplan:project:team:changed"
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

      name: queryById("projectName"),
      description: queryById("projectDescription"),
      addressText: queryById("projectAddressText"),

      visibility: queryById("projectVisibility"),
      visibilityOptions: queryAll("[data-project-visibility-option]"),
      visibilityCurrentLabel: query("[data-project-visibility-current-label]"),
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
      }
    } catch (error) {}
  }

  function setSaving(isSaving) {
    try {
      state.isSaving = !!isSaving;
      setRootState("saving", state.isSaving);

      if (state.refs.submit) {
        state.refs.submit.disabled = state.isSaving || !state.canEdit;
        state.refs.submit.setAttribute("aria-busy", state.isSaving ? "true" : "false");

        if (state.isSaving) {
          state.refs.submit.setAttribute("data-original-text", state.refs.submit.textContent || "");
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
        state.refs.reset.disabled = state.isSaving || !state.canEdit;
      }
    } catch (error) {}
  }

  function setDirty(isDirty) {
    try {
      state.isDirty = !!isDirty;
      setRootState("dirty", state.isDirty);
      setRootState("saved", !state.isDirty && !!state.lastSavedAt);

      dispatchLocal(EVENT_DIRTY, {
        dirty: state.isDirty,
        project: state.currentProject
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
        state.refs.statusPill.classList.toggle("vp-project-status--configured", !!isConfigured);
        state.refs.statusPill.classList.toggle("vp-project-status--draft", !isConfigured && !state.demoMode);
        state.refs.statusPill.textContent = state.demoMode ? "Demo" : isConfigured ? "Konfiguriert" : "Entwurf";
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

      var maxLength = Number(textarea.getAttribute("maxlength") || refs.addressCounter.getAttribute("data-max-length") || 2000);
      if (Number.isFinite(maxLength) && maxLength > 0) {
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

    if (normalized === "public") {
      return "Öffentlich bedeutet nicht automatisch, dass alle Arbeitsbereiche sichtbar sind. Map, 3D, 2D, LV und Versionen werden separat über Veröffentlichung freigegeben.";
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

      setValue(refs.visibility, normalized);

      if (refs.root) {
        refs.root.setAttribute("data-project-visibility", normalized);
      }

      if (refs.visibilityCurrentLabel) {
        refs.visibilityCurrentLabel.textContent = visibilityLabel(normalized);
      }

      if (refs.visibilityHelp) {
        refs.visibilityHelp.textContent = visibilityHelpText(normalized);
      }

      (refs.visibilityOptions || []).forEach(function syncOption(option) {
        try {
          var optionValue = normalizeVisibility(option.getAttribute("data-value"), "private");
          var selected = optionValue === normalized;

          option.classList.toggle(CLASS_SELECTED, selected);
          option.setAttribute("aria-checked", selected ? "true" : "false");
        } catch (error) {}
      });
    } catch (error) {}
  }

  function setVisibility(value, options) {
    try {
      var normalized = normalizeVisibility(value, "private");
      var opts = isObject(options) ? options : {};

      setValue(state.refs.visibility, normalized);
      syncVisibilityCards(normalized);

      if (!opts.silent) {
        dispatchLocal(EVENT_VISIBILITY_CHANGED, {
          visibility: normalized,
          project: state.currentProject
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

      return {
        name: getValue(refs.name),
        title: getValue(refs.name),
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

      var publicId = p.public_id || p.publicId || "";
      var isNew = toBooleanSafe(p.is_new || p.isNew, false) || !trimString(publicId, "");

      setValue(refs.projectId, p.id || p.project_id || "");
      setValue(refs.projectPublicId, publicId);
      setValue(refs.projectIsNew, isNew ? "true" : "false");

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
          p.id ||
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

  function emitParentEvent(type, detail) {
    try {
      var payload = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-form",
        version: INTERNAL_VERSION,
        detail: detail || {},
        project: detail && detail.project ? detail.project : state.currentProject,
        ts: Date.now()
      };

      dispatchLocal(type, payload.detail);

      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage(payload, window.location.origin);
        }
      } catch (postError) {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage(payload, "*");
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
              detail: payload.detail
            })
          );
        }
      } catch (_) {}

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event("project-sidebar:refresh"));
        }
      } catch (_) {}

      try {
        if (window.parent && window.parent !== window) {
          window.parent.dispatchEvent(new window.parent.Event("resize"));
        }
      } catch (_) {}

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

  function markDirtyFromInput() {
    try {
      if (state.isSaving) {
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
      var publicId = getProjectPublicId(project);

      if (!publicId || publicId === "new") {
        return;
      }

      state.config.projectPublicId = publicId;
      state.config.paths.updateProject = "/v1/projects/" + encodeURIComponent(publicId);
      state.config.paths.getProject = "/v1/projects/" + encodeURIComponent(publicId);
      state.config.paths.publication = "/v1/projects/" + encodeURIComponent(publicId) + "/publication";
      state.config.paths.members = "/v1/projects/" + encodeURIComponent(publicId) + "/members";
      state.config.paths.invitations = "/v1/projects/" + encodeURIComponent(publicId) + "/invitations";
    } catch (error) {}
  }

  function updateAfterSave(payload, wasNew) {
    try {
      var project = payload && isObject(payload.project) ? payload.project : null;

      if (!project && payload && isObject(payload.item)) {
        project = payload.item;
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
        project_id: project.project_id,
        public_id: project.public_id || project.publicId,
        publicId: project.publicId || project.public_id,
        name: project.name,
        display_name: project.display_name || project.displayName,
        displayName: project.displayName || project.display_name,
        description: project.description,
        address_text: project.address_text || project.addressText,
        addressText: project.addressText || project.address_text,
        address: isObject(project.address) ? project.address : {},
        visibility: project.visibility,
        is_configured: project.is_configured || project.isConfigured,
        isConfigured: project.isConfigured || project.is_configured,
        setup_status: project.setup_status || project.setupStatus,
        setupStatus: project.setupStatus || project.setup_status,
        is_new: false,
        isNew: false
      });

      setDirty(false);
      setRootState("error", false);
      setRootState("saved", true);

      var configured = isProjectConfigured(project);
      setConfigured(configured, configured ? "configured" : (project.setup_status || project.setupStatus || "draft"));

      var detail = {
        project: project,
        payload: payload || {},
        isNew: !!wasNew,
        is_new: !!wasNew,
        isConfigured: configured,
        is_configured: configured,
        redirectUrl: payload && payload.redirect_url ? payload.redirect_url : buildProjectUrl(project),
        savedAt: state.lastSavedAt
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

  async function saveProject() {
    try {
      if (!state.canEdit) {
        if (state.demoMode) {
          setAlert("warning", "Im Demo-Modus wird dieses Projekt nicht dauerhaft gespeichert.");
        } else if (!state.persistent) {
          setAlert("warning", "Dein Account ist noch nicht lokal verknüpft. Speichern ist deaktiviert.");
        } else {
          setAlert("warning", "Du hast für dieses Projekt nur Leserechte.");
        }
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
          ? "Projekt wurde erstellt. Die Projektansicht wird geöffnet…"
          : "Projekt wurde gespeichert."
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
        project: state.currentProject
      });

      return false;
    } finally {
      setSaving(false);
    }
  }

  function resetForm() {
    try {
      if (state.originalPayload) {
        var p = {
          id: state.config.projectId,
          public_id: state.config.projectPublicId,
          name: state.originalPayload.name,
          description: state.originalPayload.description,
          address_text: state.originalPayload.address_text,
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
    } catch (error) {}
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
      if (!target || target.disabled) {
        return;
      }

      setVisibility(target.getAttribute("data-value") || "private");
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
      setVisibility(getValue(state.refs.visibility));
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
          removeFieldError(element);
          markDirtyFromInput();
        });

        addListener(element, "change", function onChange() {
          removeFieldError(element);
          markDirtyFromInput();
        });
      });

      addListener(refs.visibility, "change", onVisibilityInputChange);

      (refs.visibilityOptions || []).forEach(function wireOption(option) {
        addListener(option, "click", onVisibilityOptionClick);
        addListener(option, "keydown", onVisibilityOptionKeydown);
      });

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

  function updateReadonlyState() {
    try {
      setRootState("readonly", !state.canEdit);

      if (state.refs.submit) {
        state.refs.submit.disabled = state.isSaving || !state.canEdit;
      }

      if (state.refs.reset) {
        state.refs.reset.disabled = state.isSaving || !state.canEdit;
      }

      (state.refs.visibilityOptions || []).forEach(function syncDisabled(option) {
        try {
          option.disabled = !state.canEdit;
          option.setAttribute("aria-disabled", !state.canEdit ? "true" : "false");
        } catch (error) {}
      });

      if (!state.canEdit) {
        if (state.demoMode) {
          setAlert("warning", "Demo-Modus: Dieses Formular speichert nicht dauerhaft.");
        } else if (!state.persistent) {
          setAlert("warning", "Dein Account ist noch nicht lokal verknüpft. Speichern ist deaktiviert.");
        } else {
          setAlert("warning", "Du hast für dieses Projekt nur Leserechte.");
        }
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
      state.isNew = toBooleanSafe(state.config.isNew, true);
      state.canEdit = toBooleanSafe(state.config.canEdit, true);
      state.canManage = toBooleanSafe(state.config.canManage, false);
      state.demoMode = toBooleanSafe(state.config.demoMode, false);
      state.authenticated = toBooleanSafe(state.config.authenticated, true);
      state.persistent = toBooleanSafe(state.config.persistent, true);
      state.currentProject = safeClone(state.config.project || {});

      if (!state.refs.root || !state.refs.form) {
        return state;
      }

      syncTheme();

      fillFormFromProject({
        ...(state.currentProject || {}),
        visibility: state.config.projectVisibility || (state.currentProject && state.currentProject.visibility) || "private",
        is_new: state.isNew,
        isNew: state.isNew
      });

      updateReadonlyState();
      wireEvents();
      updateAddressCounter();
      syncVisibilityCards(getValue(state.refs.visibility) || state.config.projectVisibility);

      state.initialized = true;

      dispatchLocal("vectoplan:project-form:ready", {
        project: state.currentProject,
        isNew: state.isNew,
        canEdit: state.canEdit,
        demoMode: state.demoMode,
        persistent: state.persistent
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
        demoMode: state.demoMode,
        persistent: state.persistent,
        isDirty: state.isDirty,
        isSaving: state.isSaving,
        lastSavedAt: state.lastSavedAt,
        lastError: state.lastError,
        currentProject: state.currentProject,
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
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      fillFormFromProject: fillFormFromProject,
      updateAfterSave: updateAfterSave,
      emitParentEvent: emitParentEvent,
      requestJson: requestJson,
      normalizeError: normalizeError,
      normalizeVisibility: normalizeVisibility
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