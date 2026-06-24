/* services/vectoplan-app/static/js/project/project_form.js */

/*
  VECTOPLAN Project Form

  Zweck:
  - Projektformular im Workspace-iframe steuern.
  - Neues Projekt über POST /v1/projects erstellen.
  - Bestehendes Projekt über PATCH /v1/projects/<public_id> aktualisieren.
  - Nach Erstellung Parent-Shell auf /project=<public_id> weiterleiten.
  - Nach Speichern Parent-Shell informieren, damit Sidebar und Workspace-Gating
    später aktualisiert werden können.
  - Kein direkter Zugriff auf Chunk-, Editor-, 2D- oder LV-Daten.

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

  var INTERNAL_VERSION = 1;

  var DEFAULT_CREATE_PATH = "/v1/projects";
  var DEFAULT_PROJECT_NEW_URL = "/project=new";
  var DEFAULT_PROJECT_ROOT_URL = "/";
  var DEFAULT_COUNTRY = "DE";

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

  var FIELD_ERROR_CLASS = "vp-project-field__error";

  var EVENT_SAVED = "vectoplan:project:saved";
  var EVENT_CREATED = "vectoplan:project:created";
  var EVENT_UPDATED = "vectoplan:project:updated";
  var EVENT_CONFIGURED = "vectoplan:project:configured";
  var EVENT_DIRTY = "vectoplan:project:dirty";
  var EVENT_ERROR = "vectoplan:project:error";

  var state = {
    version: INTERNAL_VERSION,
    initialized: false,
    destroyed: false,
    isNew: true,
    canEdit: true,
    isDirty: false,
    isSaving: false,
    lastSavedAt: null,
    lastError: null,
    originalPayload: null,
    currentProject: null,
    config: null,
    refs: {},
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

  function toNumberOrNull(value) {
    try {
      if (value === null || value === undefined || value === "") {
        return null;
      }

      if (typeof value === "boolean") {
        return null;
      }

      var parsed = Number(value);

      if (!Number.isFinite(parsed)) {
        return null;
      }

      return parsed;
    } catch (error) {
      return null;
    }
  }

  function toIntegerOrNull(value) {
    try {
      if (value === null || value === undefined || value === "") {
        return null;
      }

      if (typeof value === "boolean") {
        return null;
      }

      var parsed = parseInt(String(value), 10);

      if (!Number.isFinite(parsed)) {
        return null;
      }

      return parsed;
    } catch (error) {
      return null;
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
          stack: "",
        };
      }

      if (typeof error === "string") {
        return {
          name: "Error",
          message: error,
          stack: "",
        };
      }

      return {
        name: trimString(error.name, "Error"),
        message: trimString(error.message, String(error)),
        stack: trimString(error.stack, ""),
        code: trimString(error.code, ""),
        status: error.status || error.statusCode || null,
      };
    } catch (innerError) {
      return {
        name: "Error",
        message: "Unknown error",
        stack: "",
      };
    }
  }

  function safeCall(label, fn, fallback) {
    try {
      if (typeof fn !== "function") {
        return fallback;
      }

      return fn();
    } catch (error) {
      try {
        console.warn("[vectoplan-project] " + label + " failed", error);
      } catch (_) {}

      return fallback;
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

      return {
        version: INTERNAL_VERSION,
        isNew: toBooleanSafe(config.isNew, true),
        canEdit: toBooleanSafe(config.canEdit, true),
        project: project,
        currentProject: isObject(config.currentProject) ? config.currentProject : project,
        currentUser: currentUser,
        projectId: trimString(config.projectId || project.id || project.project_id, ""),
        projectPublicId: trimString(
          config.projectPublicId ||
            project.public_id ||
            project.publicId ||
            (config.isNew ? "new" : ""),
          config.isNew ? "new" : ""
        ),
        paths: {
          createProject: trimString(paths.createProject, DEFAULT_CREATE_PATH),
          updateProject: trimString(paths.updateProject, ""),
          getProject: trimString(paths.getProject, ""),
          context: trimString(paths.context, ""),
          projectRoot: trimString(paths.projectRoot, DEFAULT_PROJECT_ROOT_URL),
          projectNew: trimString(paths.projectNew, DEFAULT_PROJECT_NEW_URL),
        },
        parentEvents: {
          saved: trimString(
            config.parentEvents && config.parentEvents.saved,
            EVENT_SAVED
          ),
          created: trimString(
            config.parentEvents && config.parentEvents.created,
            EVENT_CREATED
          ),
          updated: trimString(
            config.parentEvents && config.parentEvents.updated,
            EVENT_UPDATED
          ),
          deleted: trimString(
            config.parentEvents && config.parentEvents.deleted,
            "vectoplan:project:deleted"
          ),
          configured: trimString(
            config.parentEvents && config.parentEvents.configured,
            EVENT_CONFIGURED
          ),
        },
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        isNew: true,
        canEdit: true,
        project: {},
        currentProject: {},
        currentUser: {},
        projectId: "",
        projectPublicId: "new",
        paths: {
          createProject: DEFAULT_CREATE_PATH,
          updateProject: "",
          getProject: "",
          context: "",
          projectRoot: DEFAULT_PROJECT_ROOT_URL,
          projectNew: DEFAULT_PROJECT_NEW_URL,
        },
        parentEvents: {
          saved: EVENT_SAVED,
          created: EVENT_CREATED,
          updated: EVENT_UPDATED,
          deleted: "vectoplan:project:deleted",
          configured: EVENT_CONFIGURED,
        },
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
      addressStreet: queryById("projectAddressStreet"),
      addressHouseNumber: queryById("projectAddressHouseNumber"),
      addressPostalCode: queryById("projectAddressPostalCode"),
      addressCity: queryById("projectAddressCity"),
      addressRegion: queryById("projectAddressRegion"),
      addressCountry: queryById("projectAddressCountry"),

      latitude: queryById("projectLatitude"),
      longitude: queryById("projectLongitude"),
      coordinateSrid: queryById("projectCoordinateSrid"),

      visibility: queryById("projectVisibility"),
      isPublic: queryById("projectIsPublic"),

      submit: query("[data-project-submit]"),
      reset: query("[data-project-reset]"),

      statusPill: query("[data-project-status-pill]"),
      setupStatusText: query("[data-project-setup-status-text]"),
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
        project: state.currentProject,
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
        state.refs.statusPill.classList.toggle("vp-project-status--draft", !isConfigured);
        state.refs.statusPill.textContent = isConfigured ? "Konfiguriert" : "Entwurf";
      }

      if (state.refs.setupStatusText) {
        state.refs.setupStatusText.textContent = status;
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

  function setValue(element, value) {
    try {
      if (element) {
        element.value = value === null || value === undefined ? "" : String(value);
      }
    } catch (error) {}
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
        refs.latitude,
        refs.longitude,
        refs.coordinateSrid,
        refs.visibility,
      ].forEach(function clearOne(element) {
        removeFieldError(element);
      });
    } catch (error) {}
  }

  function validatePayload(payload) {
    var errors = [];

    try {
      clearValidation();

      if (!trimString(payload.name, "")) {
        errors.push({
          field: "name",
          element: state.refs.name,
          message: "Projektname ist erforderlich.",
        });
      }

      if (!trimString(payload.address_text, "")) {
        errors.push({
          field: "address_text",
          element: state.refs.addressText,
          message: "Adresse oder Standortbeschreibung ist erforderlich.",
        });
      }

      if (payload.latitude !== null && (payload.latitude < -90 || payload.latitude > 90)) {
        errors.push({
          field: "latitude",
          element: state.refs.latitude,
          message: "Latitude muss zwischen -90 und 90 liegen.",
        });
      }

      if (payload.longitude !== null && (payload.longitude < -180 || payload.longitude > 180)) {
        errors.push({
          field: "longitude",
          element: state.refs.longitude,
          message: "Longitude muss zwischen -180 und 180 liegen.",
        });
      }

      if (
        payload.coordinate_srid !== null &&
        payload.coordinate_srid !== undefined &&
        payload.coordinate_srid <= 0
      ) {
        errors.push({
          field: "coordinate_srid",
          element: state.refs.coordinateSrid,
          message: "SRID muss eine positive Zahl sein.",
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
        errors: errors,
      };
    } catch (error) {
      return {
        ok: false,
        errors: [
          {
            field: "form",
            message: "Validierung fehlgeschlagen.",
          },
        ],
      };
    }
  }

  function collectPayload() {
    try {
      var refs = state.refs || {};
      var visibility = getValue(refs.visibility) || "private";
      var isPublic = getChecked(refs.isPublic) || visibility === "public";

      if (isPublic) {
        visibility = "public";
      }

      var latitude = toNumberOrNull(getValue(refs.latitude));
      var longitude = toNumberOrNull(getValue(refs.longitude));
      var coordinateSrid = toIntegerOrNull(getValue(refs.coordinateSrid));

      if (coordinateSrid === null) {
        coordinateSrid = 4326;
      }

      return {
        name: getValue(refs.name),
        title: getValue(refs.name),
        description: getValue(refs.description),

        address_text: getValue(refs.addressText),
        address_street: getValue(refs.addressStreet),
        address_house_number: getValue(refs.addressHouseNumber),
        address_postal_code: getValue(refs.addressPostalCode),
        address_city: getValue(refs.addressCity),
        address_region: getValue(refs.addressRegion),
        address_country: getValue(refs.addressCountry) || DEFAULT_COUNTRY,

        address: {
          text: getValue(refs.addressText),
          street: getValue(refs.addressStreet),
          house_number: getValue(refs.addressHouseNumber),
          postal_code: getValue(refs.addressPostalCode),
          city: getValue(refs.addressCity),
          region: getValue(refs.addressRegion),
          country: getValue(refs.addressCountry) || DEFAULT_COUNTRY,
        },

        latitude: latitude,
        longitude: longitude,
        coordinate_srid: coordinateSrid,

        coordinates: {
          latitude: latitude,
          longitude: longitude,
          srid: coordinateSrid,
        },

        visibility: visibility,
        is_public: isPublic,
      };
    } catch (error) {
      return {};
    }
  }

  function fillFormFromProject(project) {
    try {
      var refs = state.refs || {};
      var p = isObject(project) ? project : {};
      var address = isObject(p.address) ? p.address : {};
      var coordinates = isObject(p.coordinates) ? p.coordinates : {};

      setValue(refs.projectId, p.id || p.project_id || "");
      setValue(refs.projectPublicId, p.public_id || p.publicId || "");
      setValue(refs.projectIsNew, p.is_new || p.isNew ? "true" : "false");

      setValue(refs.name, p.name || p.display_name || p.displayName || "");
      setValue(refs.description, p.description || "");

      setValue(refs.addressText, p.address_text || address.text || "");
      setValue(refs.addressStreet, p.address_street || address.street || "");
      setValue(refs.addressHouseNumber, p.address_house_number || address.house_number || "");
      setValue(refs.addressPostalCode, p.address_postal_code || address.postal_code || "");
      setValue(refs.addressCity, p.address_city || address.city || "");
      setValue(refs.addressRegion, p.address_region || address.region || "");
      setValue(refs.addressCountry, p.address_country || address.country || DEFAULT_COUNTRY);

      setValue(
        refs.latitude,
        p.latitude !== undefined && p.latitude !== null
          ? p.latitude
          : coordinates.latitude !== undefined && coordinates.latitude !== null
            ? coordinates.latitude
            : ""
      );

      setValue(
        refs.longitude,
        p.longitude !== undefined && p.longitude !== null
          ? p.longitude
          : coordinates.longitude !== undefined && coordinates.longitude !== null
            ? coordinates.longitude
            : ""
      );

      setValue(refs.coordinateSrid, p.coordinate_srid || coordinates.srid || 4326);

      setValue(refs.visibility, p.visibility || "private");
      setChecked(refs.isPublic, toBooleanSafe(p.is_public, false) || p.visibility === "public");

      state.currentProject = safeClone(p);
      state.isNew = toBooleanSafe(p.is_new || p.isNew, false) || !trimString(p.public_id || p.publicId, "");
      state.originalPayload = collectPayload();

      setConfigured(
        toBooleanSafe(p.is_configured || p.isConfigured, false),
        p.setup_status || "draft"
      );

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
      var addressText = trimString(p.address_text || address.text, "");

      return toBooleanSafe(p.is_configured || p.isConfigured, false) ||
        p.setup_status === "configured" ||
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

  function emitParentEvent(type, detail) {
    try {
      var payload = {
        type: type,
        kind: type,
        source: "vectoplan-app.project-form",
        version: INTERNAL_VERSION,
        detail: detail || {},
        project: detail && detail.project ? detail.project : state.currentProject,
        ts: Date.now(),
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
              detail: payload.detail,
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
          detail: detail || {},
        })
      );

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
          ...(opts.headers || {}),
        },
        credentials: "same-origin",
        cache: "no-store",
        body: opts.body !== undefined ? opts.body : undefined,
      });

      var text = await response.text();
      var data = safeJsonParse(text, null);

      if (!response.ok) {
        var message = data && data.error
          ? data.error
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

  function syncVisibilityFromPublicCheckbox() {
    try {
      var refs = state.refs || {};

      if (!refs.visibility || !refs.isPublic) {
        return;
      }

      if (refs.isPublic.checked) {
        refs.visibility.value = "public";
      } else if (refs.visibility.value === "public") {
        refs.visibility.value = "private";
      }
    } catch (error) {}
  }

  function syncPublicCheckboxFromVisibility() {
    try {
      var refs = state.refs || {};

      if (!refs.visibility || !refs.isPublic) {
        return;
      }

      refs.isPublic.checked = refs.visibility.value === "public";
    } catch (error) {}
  }

  function markDirtyFromInput() {
    try {
      if (state.isSaving) {
        return;
      }

      setDirty(true);
      setRootState("saved", false);
      setAlert("", "");
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

      fillFormFromProject({
        ...project,
        is_new: false,
        isNew: false,
      });

      setDirty(false);
      setRootState("error", false);
      setRootState("saved", true);

      var configured = isProjectConfigured(project);
      setConfigured(configured, configured ? "configured" : (project.setup_status || "draft"));

      var detail = {
        project: project,
        payload: payload || {},
        isNew: !!wasNew,
        is_new: !!wasNew,
        isConfigured: configured,
        is_configured: configured,
        redirectUrl: payload && payload.redirect_url ? payload.redirect_url : buildProjectUrl(project),
        savedAt: state.lastSavedAt,
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
        redirectUrl: DEFAULT_PROJECT_ROOT_URL,
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
        setAlert("warning", "Du hast für dieses Projekt nur Leserechte.");
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
        body: safeJsonStringify(payload),
      });

      if (!response || response.ok === false) {
        throw new Error(response && response.error ? response.error : "Speichern fehlgeschlagen.");
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
        project: state.currentProject,
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
          address_street: state.originalPayload.address_street,
          address_house_number: state.originalPayload.address_house_number,
          address_postal_code: state.originalPayload.address_postal_code,
          address_city: state.originalPayload.address_city,
          address_region: state.originalPayload.address_region,
          address_country: state.originalPayload.address_country,
          latitude: state.originalPayload.latitude,
          longitude: state.originalPayload.longitude,
          coordinate_srid: state.originalPayload.coordinate_srid,
          visibility: state.originalPayload.visibility,
          is_public: state.originalPayload.is_public,
          is_new: state.isNew,
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

  function onVisibilityChange() {
    try {
      syncPublicCheckboxFromVisibility();
      markDirtyFromInput();
    } catch (error) {}
  }

  function onPublicChange() {
    try {
      syncVisibilityFromPublicCheckbox();
      markDirtyFromInput();
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
        refs.addressText,
        refs.addressStreet,
        refs.addressHouseNumber,
        refs.addressPostalCode,
        refs.addressCity,
        refs.addressRegion,
        refs.addressCountry,
        refs.latitude,
        refs.longitude,
        refs.coordinateSrid,
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

      addListener(refs.visibility, "change", onVisibilityChange);
      addListener(refs.isPublic, "change", onPublicChange);

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

      if (!state.canEdit) {
        setAlert("warning", "Du hast für dieses Projekt nur Leserechte.");
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
      state.currentProject = safeClone(state.config.project || {});

      if (!state.refs.root || !state.refs.form) {
        return state;
      }

      syncTheme();

      fillFormFromProject({
        ...(state.currentProject || {}),
        is_new: state.isNew,
        isNew: state.isNew,
      });

      updateReadonlyState();
      wireEvents();

      state.initialized = true;

      dispatchLocal("vectoplan:project-form:ready", {
        project: state.currentProject,
        isNew: state.isNew,
        canEdit: state.canEdit,
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
        isDirty: state.isDirty,
        isSaving: state.isSaving,
        lastSavedAt: state.lastSavedAt,
        lastError: state.lastError,
        currentProject: state.currentProject,
        payload: collectPayload(),
      };
    } catch (error) {
      return {
        version: INTERNAL_VERSION,
        initialized: false,
        error: normalizeError(error),
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
    _private: {
      getConfig: getConfig,
      queryRefs: queryRefs,
      fillFormFromProject: fillFormFromProject,
      updateAfterSave: updateAfterSave,
      emitParentEvent: emitParentEvent,
      requestJson: requestJson,
      normalizeError: normalizeError,
    },
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