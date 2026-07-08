# services/vectoplan-app/services/project_workspace_context.py
from __future__ import annotations

"""
VECTOPLAN Project Workspace Context Service.

Zweck:
- Baut den vollständigen, normalisierten Template-Context für:
    templates/viewer/project.html
- Hält komplexe Access-/Permission-/Publication-/Auth-/Demo-/Read-only-Logik
  aus Jinja-Templates heraus.
- Verhindert, dass fehlende optionale Keys, alte Payload-Formate oder
  Mapping/Object-Mischungen einen Template-Renderfehler auslösen.
- Liefert konsequent snake_case und camelCase Alias-Felder, damit bestehendes
  Frontend und alte Template-Fragmente stabil bleiben.
- Ist bewusst defensiv: Fehler im Context-Aufbau führen zu einem kontrollierten
  Fallback-Context, nicht zu einem 500 durch das Template.

Sicherheitsregeln:
- Diese Datei entscheidet nicht final über Berechtigungen.
- Backend-Guards in routes/viewer.py, project_permissions.py und
  project_publication_service.py bleiben die Wahrheit.
- Public Viewer werden immer read-only normalisiert.
- Demo-Kontext darf keine persistenten Management-Aktionen aktivieren.
- Auth-unavailable / blocked / access-blocked deaktivieren Mutationen.
- Team, Invitation, Publication und Admin/System-Bereiche werden nur bei
  berechtigtem, persistentem Nicht-Public-/Nicht-Demo-Kontext freigegeben.

Erwartete Verwendung in routes/viewer.py:

    from services.project_workspace_context import build_project_workspace_context

    context = build_project_workspace_context(
        project=project,
        current_user=current_user_context,
        auth_context=auth_context,
        workspace_access=workspace_access,
        publication=publication,
        is_new=is_new,
        request_obj=request,
    )
    return render_template("viewer/project.html", **context)
"""

import json
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional


try:
    from flask import current_app, request
except Exception:  # pragma: no cover
    current_app = None  # type: ignore
    request = None  # type: ignore


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

WORKSPACE_PROJECT = "project"

DEFAULT_CACHE_TTL_SECONDS = 2.0
DEFAULT_CACHE_MAX_ITEMS = 512

DEFAULT_VISIBILITY = "private"
DEFAULT_SETUP_STATUS = "draft"

PROJECT_CONTEXT_PATH_NEW = "/ui/project/new/context.json"
PROJECT_CONTEXT_PATH_TEMPLATE = "/ui/project/{project_public_id}/context.json"
PROJECT_RETURN_PATH_TEMPLATE = "/project={project_public_id}"

PUBLICATION_WORKSPACES = (
    "project",
    "map",
    "editor3d",
    "cad2d",
    "lv",
    "versions",
)

NEVER_PUBLIC_WORKSPACES = (
    "admin",
    "team",
    "settings",
    "permissions",
    "system",
    "system_refs",
)

TRUTHY_VALUES = {
    True,
    1,
    "1",
    "true",
    "True",
    "TRUE",
    "yes",
    "Yes",
    "YES",
    "y",
    "Y",
    "on",
    "On",
    "ON",
    "ja",
    "Ja",
    "JA",
    "enabled",
    "Enabled",
    "ENABLED",
    "ready",
    "Ready",
    "READY",
    "ok",
    "OK",
}

FALSEY_VALUES = {
    False,
    0,
    "0",
    "false",
    "False",
    "FALSE",
    "no",
    "No",
    "NO",
    "n",
    "N",
    "off",
    "Off",
    "OFF",
    "nein",
    "Nein",
    "NEIN",
    "disabled",
    "Disabled",
    "DISABLED",
    "none",
    "None",
    "NONE",
    "null",
    "Null",
    "NULL",
    "",
    None,
}

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

ROLE_CAN_MANAGE = {ROLE_OWNER, ROLE_ADMIN}
ROLE_CAN_EDIT = {ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR}
ROLE_CAN_VIEW = {ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}

_MODULE_CACHE: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────
# Result object
# ─────────────────────────────────────────────────────────────

@dataclass
class ProjectWorkspaceContextResult:
    ok: bool
    context: Dict[str, Any] = field(default_factory=dict)
    code: str = "ok"
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "code": self.code,
            "message": self.message,
            "warnings": list(self.warnings or []),
            "errors": list(self.errors or []),
            "context": dict(self.context or {}),
        }


# ─────────────────────────────────────────────────────────────
# Generic safe helpers
# ─────────────────────────────────────────────────────────────

def _now() -> float:
    try:
        return time.monotonic()
    except Exception:
        return time.time()


def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    try:
        if value is None:
            text = str(default if default is not None else "")
        else:
            text = str(value)

        text = text.strip()

        if not text:
            text = str(default if default is not None else "").strip()

        if max_len and max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value in TRUTHY_VALUES:
            return True

        if value in FALSEY_VALUES:
            return False

        if isinstance(value, bool):
            return value

        if isinstance(value, int) and not isinstance(value, bool):
            return bool(value)

        if isinstance(value, float):
            return bool(value)

        text = _safe_str(value, "", 120).strip()

        if text in TRUTHY_VALUES:
            return True

        if text in FALSEY_VALUES:
            return False

        lowered = text.lower()

        if lowered in {"1", "true", "yes", "y", "on", "ja", "enabled", "ready", "ok"}:
            return True

        if lowered in {"0", "false", "no", "n", "off", "nein", "disabled", "none", "null", "error"}:
            return False

        return bool(default)
    except Exception:
        return bool(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return 1 if value else 0

        if isinstance(value, int):
            return value

        if isinstance(value, float):
            return int(value)

        text = _safe_str(value, "", 120)
        if not text:
            return default

        return int(float(text))
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}

        if isinstance(value, dict):
            return dict(value)

        if isinstance(value, Mapping):
            return dict(value)

        if hasattr(value, "to_dict") and callable(value.to_dict):
            try:
                data = value.to_dict(include_private=True, include_refs=True)
            except TypeError:
                try:
                    data = value.to_dict(include_private=True)
                except TypeError:
                    try:
                        data = value.to_dict()
                    except TypeError:
                        data = {}
            return dict(data) if isinstance(data, Mapping) else {}

        return {}
    except Exception:
        return {}


def _safe_list(value: Any) -> list[Any]:
    try:
        if value is None:
            return []

        if isinstance(value, list):
            return list(value)

        if isinstance(value, tuple):
            return list(value)

        if isinstance(value, set):
            return list(value)

        return []
    except Exception:
        return []


def _safe_jsonable(value: Any, fallback: Any = None) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        try:
            if isinstance(value, Mapping):
                return {str(k): _safe_jsonable(v, None) for k, v in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [_safe_jsonable(v, None) for v in value]
            return str(value)
        except Exception:
            return fallback


def _json_fingerprint(value: Any, max_len: int = 3000) -> str:
    try:
        text = json.dumps(_safe_jsonable(value, {}), sort_keys=True, ensure_ascii=False, default=str)
        if len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return ""


def _first_non_empty(*values: Any, default: Any = "") -> Any:
    try:
        for value in values:
            if value is None:
                continue

            if isinstance(value, bool):
                return value

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value

            if isinstance(value, Mapping) and value:
                return value

            if isinstance(value, (list, tuple, set)) and value:
                return value

            text = _safe_str(value, "", 4000)
            if text:
                return value

        return default
    except Exception:
        return default


def _mapping_value(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    try:
        if not isinstance(mapping, Mapping):
            return default

        for key in keys:
            if key in mapping:
                value = mapping.get(key)
                if value is not None:
                    return value

        return default
    except Exception:
        return default


def _object_value(obj: Any, *keys: str, default: Any = None) -> Any:
    try:
        if obj is None:
            return default

        if isinstance(obj, Mapping):
            return _mapping_value(obj, *keys, default=default)

        for key in keys:
            try:
                if hasattr(obj, key):
                    value = getattr(obj, key)
                    if value is not None:
                        return value
            except Exception:
                continue

        return default
    except Exception:
        return default


def _log_warning(message: str, *args: Any) -> None:
    try:
        if current_app is not None:
            current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[BaseException] = None) -> None:
    try:
        if current_app is not None:
            if exc is None:
                current_app.logger.exception(message)
            else:
                current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Config and cache helpers
# ─────────────────────────────────────────────────────────────

def _config_get(key: str, default: Any = None) -> Any:
    try:
        if current_app is not None:
            value = current_app.config.get(key)
            if value not in {None, ""}:
                return value
    except Exception:
        pass

    try:
        value = os.environ.get(key)
        if value not in {None, ""}:
            return value
    except Exception:
        pass

    return default


def _config_bool(key: str, default: bool = False) -> bool:
    try:
        return _safe_bool(_config_get(key, default), default)
    except Exception:
        return default


def _config_float(key: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    try:
        value = float(_safe_str(_config_get(key, default), str(default), 100))

        if minimum is not None:
            value = max(minimum, value)

        if maximum is not None:
            value = min(maximum, value)

        return value
    except Exception:
        return default


def _cache_enabled() -> bool:
    try:
        return _config_bool("VECTOPLAN_PROJECT_WORKSPACE_CONTEXT_CACHE_ENABLED", False)
    except Exception:
        return False


def _cache_ttl_seconds() -> float:
    return _config_float(
        "VECTOPLAN_PROJECT_WORKSPACE_CONTEXT_CACHE_TTL_SECONDS",
        DEFAULT_CACHE_TTL_SECONDS,
        minimum=0.0,
        maximum=60.0,
    )


def _cache_max_items() -> int:
    try:
        value = _safe_int(
            _config_get("VECTOPLAN_PROJECT_WORKSPACE_CONTEXT_CACHE_MAX_ITEMS", DEFAULT_CACHE_MAX_ITEMS),
            DEFAULT_CACHE_MAX_ITEMS,
        )
        return max(32, min(10000, value))
    except Exception:
        return DEFAULT_CACHE_MAX_ITEMS


def _cache_get(key: str) -> Optional[Any]:
    try:
        if not key:
            return None

        ttl = _cache_ttl_seconds()
        if ttl <= 0:
            return None

        item = _MODULE_CACHE.get(key)
        if not item:
            return None

        ts = float(item.get("ts") or 0.0)
        if _now() - ts > ttl:
            _MODULE_CACHE.pop(key, None)
            return None

        return item.get("value")
    except Exception:
        return None


def _cache_set(key: str, value: Any) -> Any:
    try:
        if not key:
            return value

        if len(_MODULE_CACHE) > _cache_max_items():
            _cache_prune()

        _MODULE_CACHE[key] = {
            "ts": _now(),
            "value": value,
        }
    except Exception:
        pass

    return value


def _cache_prune() -> None:
    try:
        max_items = _cache_max_items()
        if len(_MODULE_CACHE) <= max_items:
            return

        items = sorted(_MODULE_CACHE.items(), key=lambda item: float(item[1].get("ts") or 0))
        remove_count = max(1, len(items) - max_items + max(1, max_items // 4))

        for key, _ in items[:remove_count]:
            _MODULE_CACHE.pop(key, None)
    except Exception:
        try:
            _MODULE_CACHE.clear()
        except Exception:
            pass


def clear_project_workspace_context_cache() -> None:
    try:
        _MODULE_CACHE.clear()
    except Exception:
        pass

    for cached in (
        normalize_visibility,
        normalize_role,
        normalize_access_mode,
        normalize_setup_status,
        normalize_workspace,
    ):
        try:
            cached.cache_clear()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Normalizers
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def normalize_visibility(value: Any, default: str = DEFAULT_VISIBILITY) -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "private": "private",
            "privat": "private",
            "closed": "private",
            "internal": "private",
            "unlisted": "unlisted",
            "not_listed": "unlisted",
            "notlisted": "unlisted",
            "nicht_gelistet": "unlisted",
            "link": "unlisted",
            "link_shared": "unlisted",
            "public": "public",
            "öffentlich": "public",
            "oeffentlich": "public",
            "open": "public",
        }

        return aliases.get(text, default)
    except Exception:
        return default


@lru_cache(maxsize=512)
def normalize_role(value: Any, default: str = "") -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "owner": ROLE_OWNER,
            "admin": ROLE_ADMIN,
            "administrator": ROLE_ADMIN,
            "manager": ROLE_ADMIN,
            "editor": ROLE_EDITOR,
            "edit": ROLE_EDITOR,
            "write": ROLE_EDITOR,
            "viewer": ROLE_VIEWER,
            "view": ROLE_VIEWER,
            "reader": ROLE_VIEWER,
            "readonly": ROLE_VIEWER,
            "read_only": ROLE_VIEWER,
        }

        return aliases.get(text, default)
    except Exception:
        return default


@lru_cache(maxsize=512)
def normalize_access_mode(value: Any, default: str = "") -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "auth_unavailable": "auth_unavailable",
            "unavailable": "auth_unavailable",
            "service_unavailable": "auth_unavailable",
            "blocked": "blocked",
            "banned": "blocked",
            "access_blocked": "blocked",
            "public": "public",
            "public_viewer": "public",
            "anonymous_public": "public",
            "demo": "demo",
            "demo_mode": "demo",
            "authenticated": "authenticated",
            "auth": "authenticated",
            "user": "authenticated",
            "anonymous": "anonymous",
            "guest": "anonymous",
            "unauthenticated": "anonymous",
        }

        return aliases.get(text, default)
    except Exception:
        return default


@lru_cache(maxsize=512)
def normalize_setup_status(value: Any, default: str = DEFAULT_SETUP_STATUS) -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "draft": "draft",
            "new": "draft",
            "created": "draft",
            "configured": "configured",
            "complete": "configured",
            "completed": "configured",
            "ready": "configured",
            "active": "configured",
            "archived": "archived",
            "deleted": "deleted",
        }

        return aliases.get(text, default)
    except Exception:
        return default


@lru_cache(maxsize=512)
def normalize_workspace(value: Any, default: str = WORKSPACE_PROJECT) -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": WORKSPACE_PROJECT,
            "project": WORKSPACE_PROJECT,
            "projekt": WORKSPACE_PROJECT,
            "details": WORKSPACE_PROJECT,
            "overview": WORKSPACE_PROJECT,
            "info": WORKSPACE_PROJECT,
        }

        return aliases.get(text, text or default)
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Payload extraction
# ─────────────────────────────────────────────────────────────

def _object_payload_from_known_attrs(obj: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    try:
        if obj is None or isinstance(obj, Mapping):
            return result

        known_attrs = (
            "id",
            "public_id",
            "publicId",
            "project_id",
            "projectId",
            "owner_user_id",
            "ownerUserId",
            "conversation_id",
            "conversationId",
            "name",
            "title",
            "display_name",
            "displayName",
            "description",
            "address_text",
            "addressText",
            "street",
            "house_number",
            "houseNumber",
            "postal_code",
            "postalCode",
            "city",
            "region",
            "country",
            "latitude",
            "longitude",
            "coordinate_srid",
            "coordinateSrid",
            "visibility",
            "is_public",
            "isPublic",
            "setup_status",
            "setupStatus",
            "setup_completed_at",
            "setupCompletedAt",
            "status",
            "chunk_project_id",
            "chunkProjectId",
            "chunk_universe_id",
            "chunkUniverseId",
            "chunk_world_id",
            "chunkWorldId",
            "plan2d_id",
            "plan2dId",
            "lv_id",
            "lvId",
            "service_refs",
            "serviceRefs",
            "artifact_refs",
            "artifactRefs",
            "metadata_json",
            "metadataJson",
            "settings",
            "access",
            "publication",
            "workspace_access",
            "workspaceAccess",
        )

        for attr in known_attrs:
            try:
                if hasattr(obj, attr):
                    value = getattr(obj, attr)
                    if value is not None:
                        result[attr] = _safe_jsonable(value, value)
            except Exception:
                continue

        return result
    except Exception:
        return result


def _payload_from_object(value: Any) -> Dict[str, Any]:
    try:
        payload = _safe_dict(value)
        if payload:
            return payload

        payload = _object_payload_from_known_attrs(value)
        if payload:
            return payload

        raw_dict = getattr(value, "__dict__", None)
        if isinstance(raw_dict, Mapping):
            result = {}
            for key, item in raw_dict.items():
                key_text = _safe_str(key, "", 200)
                if not key_text or key_text.startswith("_sa_"):
                    continue
                if key_text.startswith("_"):
                    continue
                result[key_text] = _safe_jsonable(item, item)
            return result

        return {}
    except Exception:
        return {}


def _project_payload(project: Any = None, project_payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    try:
        explicit = _safe_dict(project_payload)
        if explicit:
            return explicit

        return _payload_from_object(project)
    except Exception:
        return {}


def _current_user_payload(current_user: Any = None, auth_context: Any = None) -> Dict[str, Any]:
    try:
        user = _payload_from_object(current_user)
        auth = _payload_from_object(auth_context)

        if not user and auth:
            user = dict(auth)

        if auth:
            user.setdefault("auth", auth)
            for key in (
                "authenticated",
                "is_authenticated",
                "isAuthenticated",
                "auth_unavailable",
                "authUnavailable",
                "service_unavailable",
                "serviceUnavailable",
                "user_blocked",
                "userBlocked",
                "access_blocked",
                "accessBlocked",
                "demo_mode",
                "demoMode",
                "can_demo",
                "canDemo",
                "persistent",
                "can_persist",
                "canPersist",
                "auth_user_id",
                "authUserId",
                "email",
                "display_name",
                "displayName",
            ):
                if key not in user and key in auth:
                    user[key] = auth.get(key)

        return user
    except Exception:
        return {}


def _access_payload(project_payload: Mapping[str, Any], access: Any = None) -> Dict[str, Any]:
    try:
        explicit = _payload_from_object(access)
        if explicit:
            return explicit

        project_access = _safe_dict(project_payload.get("access"))
        if project_access:
            return project_access

        permission_result = _safe_dict(project_payload.get("permission_result") or project_payload.get("permissionResult"))
        if permission_result:
            return permission_result

        return {}
    except Exception:
        return {}


def _workspace_access_payload(project_payload: Mapping[str, Any], workspace_access: Any = None) -> Dict[str, Any]:
    try:
        explicit = _payload_from_object(workspace_access)
        if explicit:
            return explicit

        direct = _safe_dict(project_payload.get("workspace_access") or project_payload.get("workspaceAccess"))
        if direct:
            return direct

        access = _safe_dict(project_payload.get("access"))
        workspace_direct = _safe_dict(access.get("workspace_access") or access.get("workspaceAccess"))
        if workspace_direct:
            return workspace_direct

        return {}
    except Exception:
        return {}


def _publication_payload(project_payload: Mapping[str, Any], publication: Any = None) -> Dict[str, Any]:
    try:
        explicit = _payload_from_object(publication)
        if explicit:
            return explicit

        direct = _safe_dict(project_payload.get("publication") or project_payload.get("publication_view") or project_payload.get("publicationView"))
        if direct:
            return direct

        settings = _safe_dict(project_payload.get("settings"))
        settings_publication = _safe_dict(settings.get("publication"))
        if settings_publication:
            return settings_publication

        metadata = _safe_dict(project_payload.get("metadata_json") or project_payload.get("metadataJson") or project_payload.get("metadata"))
        metadata_publication = _safe_dict(metadata.get("publication"))
        if metadata_publication:
            return metadata_publication

        return {}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# Project normalization
# ─────────────────────────────────────────────────────────────

def _project_public_id(payload: Mapping[str, Any], is_new: Optional[bool] = None) -> str:
    try:
        value = _first_non_empty(
            payload.get("public_id"),
            payload.get("publicId"),
            payload.get("project_public_id"),
            payload.get("projectPublicId"),
            default="",
        )
        text = _safe_str(value, "", 240)

        if text.lower() in {"none", "null"}:
            text = ""

        if not text and is_new:
            return "new"

        return text
    except Exception:
        return "new" if is_new else ""


def _is_new_project(payload: Mapping[str, Any], explicit_is_new: Any = None) -> bool:
    try:
        if explicit_is_new is not None:
            return _safe_bool(explicit_is_new, False)

        value = _first_non_empty(
            payload.get("is_new"),
            payload.get("isNew"),
            default=None,
        )

        if value is not None:
            return _safe_bool(value, False)

        public_id = _project_public_id(payload)
        if _safe_str(public_id, "", 240).lower() in {"", "new", "create", "neu", "none", "null"}:
            return True

        return False
    except Exception:
        return True


def _project_address_text(payload: Mapping[str, Any]) -> str:
    try:
        address = _safe_dict(payload.get("address"))
        return _safe_str(
            _first_non_empty(
                payload.get("address_text"),
                payload.get("addressText"),
                address.get("text"),
                address.get("address_text"),
                address.get("addressText"),
                default="",
            ),
            "",
            2000,
        )
    except Exception:
        return ""


def _project_is_configured(payload: Mapping[str, Any], setup_status: str) -> bool:
    try:
        explicit = _first_non_empty(
            payload.get("is_configured"),
            payload.get("isConfigured"),
            default=None,
        )
        if explicit is not None:
            return _safe_bool(explicit, False)

        if setup_status == "configured":
            return True

        name = _safe_str(_first_non_empty(payload.get("name"), payload.get("title"), default=""), "", 300)
        address_text = _project_address_text(payload)
        lat = _first_non_empty(payload.get("latitude"), payload.get("lat"), default=None)
        lng = _first_non_empty(payload.get("longitude"), payload.get("lng"), payload.get("lon"), default=None)

        has_location = bool(address_text or (lat is not None and lng is not None))

        return bool(name and has_location)
    except Exception:
        return False


def _build_project_view(payload: Mapping[str, Any], *, is_new: bool) -> Dict[str, Any]:
    try:
        project_id = _safe_str(_first_non_empty(payload.get("id"), payload.get("project_id"), payload.get("projectId"), default=""), "", 240)
        public_id = _project_public_id(payload, is_new=is_new)
        name = _safe_str(_first_non_empty(payload.get("name"), payload.get("title"), default=""), "", 300)
        display_name = _safe_str(
            _first_non_empty(payload.get("display_name"), payload.get("displayName"), name, default="Neues Projekt"),
            "Neues Projekt",
            300,
        )
        description = _safe_str(payload.get("description"), "", 5000)
        address_text = _project_address_text(payload)

        visibility = normalize_visibility(
            _first_non_empty(
                payload.get("visibility"),
                "public" if _safe_bool(payload.get("is_public") or payload.get("isPublic"), False) else "",
                default=DEFAULT_VISIBILITY,
            )
        )

        setup_status = normalize_setup_status(_first_non_empty(payload.get("setup_status"), payload.get("setupStatus"), default=DEFAULT_SETUP_STATUS))
        is_configured = _project_is_configured(payload, setup_status)

        view: Dict[str, Any] = dict(payload)
        view.update(
            {
                "id": project_id,
                "project_id": project_id,
                "projectId": project_id,
                "public_id": public_id,
                "publicId": public_id,
                "project_public_id": public_id,
                "projectPublicId": public_id,
                "name": name,
                "title": name,
                "display_name": display_name,
                "displayName": display_name,
                "description": description,
                "address_text": address_text,
                "addressText": address_text,
                "address": {
                    **_safe_dict(payload.get("address")),
                    "text": address_text,
                    "address_text": address_text,
                    "addressText": address_text,
                },
                "visibility": visibility,
                "is_public": visibility == "public",
                "isPublic": visibility == "public",
                "setup_status": setup_status,
                "setupStatus": setup_status,
                "is_configured": is_configured,
                "isConfigured": is_configured,
                "is_new": is_new,
                "isNew": is_new,
            }
        )

        return view
    except Exception:
        return {
            "id": "",
            "project_id": "",
            "projectId": "",
            "public_id": "new" if is_new else "",
            "publicId": "new" if is_new else "",
            "project_public_id": "new" if is_new else "",
            "projectPublicId": "new" if is_new else "",
            "name": "",
            "title": "",
            "display_name": "Neues Projekt",
            "displayName": "Neues Projekt",
            "description": "",
            "address_text": "",
            "addressText": "",
            "address": {"text": "", "address_text": "", "addressText": ""},
            "visibility": DEFAULT_VISIBILITY,
            "is_public": False,
            "isPublic": False,
            "setup_status": DEFAULT_SETUP_STATUS,
            "setupStatus": DEFAULT_SETUP_STATUS,
            "is_configured": False,
            "isConfigured": False,
            "is_new": is_new,
            "isNew": is_new,
        }


# ─────────────────────────────────────────────────────────────
# Auth/access normalization
# ─────────────────────────────────────────────────────────────

def _auth_flag(user: Mapping[str, Any], auth: Mapping[str, Any], *keys: str, default: bool = False) -> bool:
    try:
        for key in keys:
            if key in auth:
                return _safe_bool(auth.get(key), default)
            if key in user:
                return _safe_bool(user.get(key), default)

        nested_auth = _safe_dict(user.get("auth"))
        for key in keys:
            if key in nested_auth:
                return _safe_bool(nested_auth.get(key), default)

        return default
    except Exception:
        return default


def _auth_value(user: Mapping[str, Any], auth: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    try:
        for key in keys:
            if key in auth and auth.get(key) is not None:
                return auth.get(key)
            if key in user and user.get(key) is not None:
                return user.get(key)

        nested_auth = _safe_dict(user.get("auth"))
        for key in keys:
            if key in nested_auth and nested_auth.get(key) is not None:
                return nested_auth.get(key)

        return default
    except Exception:
        return default


def _permission_flag(access: Mapping[str, Any], permissions: Mapping[str, Any], *keys: str, default: bool = False) -> bool:
    try:
        for key in keys:
            if key in access:
                return _safe_bool(access.get(key), default)
            if key in permissions:
                return _safe_bool(permissions.get(key), default)

        return default
    except Exception:
        return default


def _role_from_payloads(project_view: Mapping[str, Any], access: Mapping[str, Any], user: Mapping[str, Any]) -> str:
    try:
        role = _first_non_empty(
            access.get("role"),
            access.get("project_role"),
            access.get("projectRole"),
            access.get("membership_role"),
            access.get("membershipRole"),
            user.get("project_role"),
            user.get("projectRole"),
            user.get("role"),
            project_view.get("role"),
            project_view.get("project_role"),
            project_view.get("projectRole"),
            default="",
        )

        return normalize_role(role, "")
    except Exception:
        return ""


def _explicit_public_viewer(project_view: Mapping[str, Any], user: Mapping[str, Any], access: Mapping[str, Any], workspace_access: Mapping[str, Any]) -> bool:
    try:
        candidates = (
            user.get("public_viewer"),
            user.get("publicViewer"),
            user.get("is_public_viewer"),
            user.get("isPublicViewer"),
            access.get("public_viewer"),
            access.get("publicViewer"),
            access.get("is_public_viewer"),
            access.get("isPublicViewer"),
            workspace_access.get("public_viewer"),
            workspace_access.get("publicViewer"),
            workspace_access.get("is_public_viewer"),
            workspace_access.get("isPublicViewer"),
            project_view.get("public_viewer"),
            project_view.get("publicViewer"),
            project_view.get("is_public_viewer"),
            project_view.get("isPublicViewer"),
        )

        for value in candidates:
            if value is not None:
                if _safe_bool(value, False):
                    return True

        explicit_mode = normalize_access_mode(
            _first_non_empty(
                user.get("access_mode"),
                user.get("accessMode"),
                access.get("access_mode"),
                access.get("accessMode"),
                workspace_access.get("access_mode"),
                workspace_access.get("accessMode"),
                project_view.get("access_mode"),
                project_view.get("accessMode"),
                default="",
            ),
            "",
        )

        return explicit_mode == "public"
    except Exception:
        return False


def _derive_access_mode(
    *,
    user: Mapping[str, Any],
    auth: Mapping[str, Any],
    project_view: Mapping[str, Any],
    access: Mapping[str, Any],
    workspace_access: Mapping[str, Any],
    auth_unavailable: bool,
    user_blocked: bool,
    access_blocked: bool,
    demo_mode: bool,
    public_viewer: bool,
    authenticated: bool,
) -> str:
    try:
        explicit = normalize_access_mode(
            _first_non_empty(
                access.get("access_mode"),
                access.get("accessMode"),
                workspace_access.get("access_mode"),
                workspace_access.get("accessMode"),
                project_view.get("access_mode"),
                project_view.get("accessMode"),
                user.get("access_mode"),
                user.get("accessMode"),
                auth.get("access_mode"),
                auth.get("accessMode"),
                default="",
            ),
            "",
        )

        if explicit:
            return explicit

        if auth_unavailable:
            return "auth_unavailable"

        if user_blocked or access_blocked:
            return "blocked"

        if public_viewer:
            return "public"

        if demo_mode:
            return "demo"

        if authenticated:
            return "authenticated"

        return "anonymous"
    except Exception:
        return "anonymous"


def _build_access_view(
    *,
    project_view: Mapping[str, Any],
    access_payload: Mapping[str, Any],
    workspace_access_payload: Mapping[str, Any],
    current_user_payload: Mapping[str, Any],
    auth_payload: Mapping[str, Any],
    is_new: bool,
) -> Dict[str, Any]:
    try:
        access = _safe_dict(access_payload)
        workspace_access = _safe_dict(workspace_access_payload)
        user = _safe_dict(current_user_payload)
        auth = _safe_dict(auth_payload)

        permissions = _safe_dict(access.get("permissions") or access.get("permission_map") or access.get("permissionMap"))
        role = _role_from_payloads(project_view, access, user)

        authenticated = _auth_flag(user, auth, "authenticated", "is_authenticated", "isAuthenticated", default=False)

        auth_unavailable = _auth_flag(
            user,
            auth,
            "auth_unavailable",
            "authUnavailable",
            "service_unavailable",
            "serviceUnavailable",
            "dependency_unavailable",
            "dependencyUnavailable",
            default=False,
        )

        user_blocked = _auth_flag(
            user,
            auth,
            "user_blocked",
            "userBlocked",
            "blocked",
            "is_blocked",
            "isBlocked",
            "banned",
            "is_banned",
            "isBanned",
            default=False,
        )

        explicit_access_blocked = _permission_flag(
            access,
            permissions,
            "access_blocked",
            "accessBlocked",
            "blocked",
            default=False,
        ) or _safe_bool(workspace_access.get("access_blocked") or workspace_access.get("accessBlocked"), False)

        access_blocked = bool(auth_unavailable or user_blocked or explicit_access_blocked)

        public_viewer = _explicit_public_viewer(project_view, user, access, workspace_access)

        demo_candidate = _auth_flag(
            user,
            auth,
            "demo_mode",
            "demoMode",
            "is_demo",
            "isDemo",
            "can_demo",
            "canDemo",
            "demo_project_access",
            "demoProjectAccess",
            default=False,
        )
        demo_mode = bool(demo_candidate and not public_viewer and not auth_unavailable and not user_blocked)

        persistent = _auth_flag(
            user,
            auth,
            "persistent",
            "can_persist",
            "canPersist",
            "is_persistent",
            "isPersistent",
            default=False,
        )

        if not persistent:
            auth_user_id = _safe_str(
                _auth_value(user, auth, "auth_user_id", "authUserId", "user_id", "userId", "id", default=""),
                "",
                240,
            )
            persistent = bool(authenticated and auth_user_id and not demo_mode and not public_viewer and not access_blocked)

        access_mode = _derive_access_mode(
            user=user,
            auth=auth,
            project_view=project_view,
            access=access,
            workspace_access=workspace_access,
            auth_unavailable=auth_unavailable,
            user_blocked=user_blocked,
            access_blocked=access_blocked,
            demo_mode=demo_mode,
            public_viewer=public_viewer,
            authenticated=authenticated,
        )

        role_can_view = role in ROLE_CAN_VIEW
        role_can_edit = role in ROLE_CAN_EDIT
        role_can_manage = role in ROLE_CAN_MANAGE

        can_view = _permission_flag(
            access,
            permissions,
            "can_view",
            "canView",
            "view",
            "read",
            default=bool(role_can_view or is_new),
        )

        can_edit = _permission_flag(
            access,
            permissions,
            "can_edit",
            "canEdit",
            "edit",
            "write",
            default=bool(role_can_edit or (is_new and (persistent or demo_mode))),
        )

        can_manage = _permission_flag(
            access,
            permissions,
            "can_manage",
            "canManage",
            "manage",
            "admin",
            default=bool(role_can_manage),
        )

        can_view_settings = _permission_flag(
            access,
            permissions,
            "can_view_settings",
            "canViewSettings",
            "view_settings",
            "viewSettings",
            default=bool(can_manage),
        )

        can_manage_team = _permission_flag(
            access,
            permissions,
            "can_manage_team",
            "canManageTeam",
            "manage_team",
            "manageTeam",
            default=bool(can_manage),
        )

        can_publish = _permission_flag(
            access,
            permissions,
            "can_publish",
            "canPublish",
            "publish",
            "manage_publication",
            "managePublication",
            default=bool(can_manage),
        )

        read_only_explicit = _first_non_empty(
            access.get("read_only"),
            access.get("readOnly"),
            access.get("readonly"),
            workspace_access.get("read_only"),
            workspace_access.get("readOnly"),
            workspace_access.get("readonly"),
            project_view.get("read_only"),
            project_view.get("readOnly"),
            project_view.get("readonly"),
            default=None,
        )

        read_only = _safe_bool(read_only_explicit, default=not can_edit) if read_only_explicit is not None else bool(not can_edit)

        if public_viewer:
            can_view = True
            can_edit = False
            can_manage = False
            can_view_settings = False
            can_manage_team = False
            can_publish = False
            read_only = True
            persistent = False
            access_mode = "public"

        if demo_mode:
            can_view = True
            can_edit = True
            can_manage = False
            can_view_settings = False
            can_manage_team = False
            can_publish = False
            persistent = False
            if access_mode not in {"public", "blocked", "auth_unavailable"}:
                access_mode = "demo"

        if auth_unavailable or user_blocked or access_blocked:
            can_edit = False
            can_manage = False
            can_view_settings = False
            can_manage_team = False
            can_publish = False
            read_only = True
            persistent = False

        if is_new:
            can_view = True
            if demo_mode:
                can_edit = True
                read_only = False
            elif persistent and not public_viewer and not access_blocked:
                can_edit = True
                read_only = False

        form_can_edit = bool(
            can_edit
            and not read_only
            and (persistent or demo_mode)
            and not public_viewer
            and not auth_unavailable
            and not user_blocked
            and not access_blocked
        )

        can_mutate = form_can_edit

        show_management_sections = bool(
            can_manage
            and not is_new
            and not demo_mode
            and not public_viewer
            and not read_only
            and not auth_unavailable
            and not user_blocked
            and not access_blocked
        )

        show_management_locked = bool(
            not is_new
            and not show_management_sections
            and not public_viewer
            and not demo_mode
        )

        result: Dict[str, Any] = dict(access)
        result.update(
            {
                "role": role,
                "permissions": permissions,

                "authenticated": authenticated,
                "auth_unavailable": auth_unavailable,
                "authUnavailable": auth_unavailable,
                "user_blocked": user_blocked,
                "userBlocked": user_blocked,
                "access_blocked": access_blocked,
                "accessBlocked": access_blocked,

                "demo_mode": demo_mode,
                "demoMode": demo_mode,
                "persistent": persistent,

                "public_viewer": public_viewer,
                "publicViewer": public_viewer,
                "is_public_viewer": public_viewer,
                "isPublicViewer": public_viewer,

                "access_mode": access_mode,
                "accessMode": access_mode,

                "read_only": read_only,
                "readOnly": read_only,
                "readonly": read_only,

                "can_view": can_view,
                "canView": can_view,
                "can_edit": can_edit,
                "canEdit": can_edit,
                "can_manage": can_manage,
                "canManage": can_manage,
                "can_view_settings": can_view_settings,
                "canViewSettings": can_view_settings,
                "can_manage_team": can_manage_team,
                "canManageTeam": can_manage_team,
                "can_publish": can_publish,
                "canPublish": can_publish,
                "can_mutate": can_mutate,
                "canMutate": can_mutate,
                "form_can_edit": form_can_edit,
                "formCanEdit": form_can_edit,
                "show_management_sections": show_management_sections,
                "showManagementSections": show_management_sections,
                "show_management_locked": show_management_locked,
                "showManagementLocked": show_management_locked,
            }
        )

        return result
    except Exception as exc:
        _log_exception("build access view failed", exc)
        return {
            "role": "",
            "permissions": {},
            "authenticated": False,
            "auth_unavailable": False,
            "authUnavailable": False,
            "user_blocked": False,
            "userBlocked": False,
            "access_blocked": True,
            "accessBlocked": True,
            "demo_mode": False,
            "demoMode": False,
            "persistent": False,
            "public_viewer": False,
            "publicViewer": False,
            "is_public_viewer": False,
            "isPublicViewer": False,
            "access_mode": "blocked",
            "accessMode": "blocked",
            "read_only": True,
            "readOnly": True,
            "readonly": True,
            "can_view": False,
            "canView": False,
            "can_edit": False,
            "canEdit": False,
            "can_manage": False,
            "canManage": False,
            "can_view_settings": False,
            "canViewSettings": False,
            "can_manage_team": False,
            "canManageTeam": False,
            "can_publish": False,
            "canPublish": False,
            "can_mutate": False,
            "canMutate": False,
            "form_can_edit": False,
            "formCanEdit": False,
            "show_management_sections": False,
            "showManagementSections": False,
            "show_management_locked": True,
            "showManagementLocked": True,
        }


# ─────────────────────────────────────────────────────────────
# Publication/workspace normalization
# ─────────────────────────────────────────────────────────────

def _normalize_published_workspaces(value: Any) -> Dict[str, bool]:
    result = {workspace: False for workspace in PUBLICATION_WORKSPACES}

    try:
        if isinstance(value, Mapping):
            for workspace in PUBLICATION_WORKSPACES:
                result[workspace] = _safe_bool(
                    value.get(workspace)
                    or value.get(workspace.replace("_", "-"))
                    or value.get(workspace.replace("_", "")),
                    False,
                )
            return result

        if isinstance(value, (list, tuple, set)):
            normalized_items = {_safe_str(item, "", 120).lower().replace("-", "_") for item in value}
            for workspace in PUBLICATION_WORKSPACES:
                result[workspace] = workspace in normalized_items
            return result

        return result
    except Exception:
        return result


def _build_publication_view(
    *,
    project_view: Mapping[str, Any],
    publication_payload: Mapping[str, Any],
    access_view: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        publication = _safe_dict(publication_payload)

        visibility = normalize_visibility(
            _first_non_empty(
                publication.get("visibility"),
                project_view.get("visibility"),
                default=DEFAULT_VISIBILITY,
            )
        )

        publication_enabled = _safe_bool(
            _first_non_empty(
                publication.get("publication_enabled"),
                publication.get("publicationEnabled"),
                publication.get("enabled"),
                default=visibility in {"public", "unlisted"},
            ),
            visibility in {"public", "unlisted"},
        )

        published_workspaces = _normalize_published_workspaces(
            _first_non_empty(
                publication.get("published_workspaces"),
                publication.get("publishedWorkspaces"),
                publication.get("workspaces"),
                default={},
            )
        )

        effective_published_workspaces = _normalize_published_workspaces(
            _first_non_empty(
                publication.get("effective_published_workspaces"),
                publication.get("effectivePublishedWorkspaces"),
                default=published_workspaces,
            )
        )

        if not publication_enabled or visibility == "private":
            effective_published_workspaces = {workspace: False for workspace in PUBLICATION_WORKSPACES}

        for workspace in NEVER_PUBLIC_WORKSPACES:
            effective_published_workspaces.pop(workspace, None)

        require_auth = _safe_bool(
            _first_non_empty(
                publication.get("require_auth"),
                publication.get("requireAuth"),
                default=False,
            ),
            False,
        )

        require_project_permission = _safe_bool(
            _first_non_empty(
                publication.get("require_project_permission"),
                publication.get("requireProjectPermission"),
                default=False,
            ),
            False,
        )

        result: Dict[str, Any] = dict(publication)
        result.update(
            {
                "visibility": visibility,
                "publication_enabled": publication_enabled,
                "publicationEnabled": publication_enabled,
                "published_workspaces": published_workspaces,
                "publishedWorkspaces": published_workspaces,
                "effective_published_workspaces": effective_published_workspaces,
                "effectivePublishedWorkspaces": effective_published_workspaces,
                "require_auth": require_auth,
                "requireAuth": require_auth,
                "require_project_permission": require_project_permission,
                "requireProjectPermission": require_project_permission,
                "can_publish": _safe_bool(access_view.get("can_publish"), False),
                "canPublish": _safe_bool(access_view.get("can_publish"), False),
                "read_only": _safe_bool(access_view.get("read_only"), True),
                "readOnly": _safe_bool(access_view.get("read_only"), True),
            }
        )

        return result
    except Exception:
        return {
            "visibility": normalize_visibility(project_view.get("visibility", DEFAULT_VISIBILITY)),
            "publication_enabled": False,
            "publicationEnabled": False,
            "published_workspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "publishedWorkspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "effective_published_workspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "effectivePublishedWorkspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "require_auth": False,
            "requireAuth": False,
            "require_project_permission": False,
            "requireProjectPermission": False,
            "can_publish": False,
            "canPublish": False,
            "read_only": True,
            "readOnly": True,
        }


def _build_workspace_access_view(
    *,
    workspace: str,
    project_view: Mapping[str, Any],
    workspace_access_payload: Mapping[str, Any],
    access_view: Mapping[str, Any],
    publication_view: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        workspace_access = _safe_dict(workspace_access_payload)
        normalized_workspace = normalize_workspace(workspace, WORKSPACE_PROJECT)

        explicit_allowed = _first_non_empty(
            workspace_access.get("allowed"),
            workspace_access.get("ok"),
            workspace_access.get("can_view"),
            workspace_access.get("canView"),
            default=None,
        )

        if explicit_allowed is None:
            allowed = _safe_bool(access_view.get("can_view"), False)
        else:
            allowed = _safe_bool(explicit_allowed, False)

        if _safe_bool(access_view.get("access_blocked"), False):
            allowed = False

        if _safe_bool(access_view.get("public_viewer"), False):
            effective = _safe_dict(publication_view.get("effective_published_workspaces"))
            if normalized_workspace in PUBLICATION_WORKSPACES:
                allowed = bool(effective.get(normalized_workspace, False))
            else:
                allowed = False

        reason = _safe_str(
            _first_non_empty(
                workspace_access.get("reason"),
                workspace_access.get("code"),
                "ok" if allowed else "forbidden",
                default="forbidden",
            ),
            "forbidden",
            240,
        )

        status_code = _safe_int(
            _first_non_empty(
                workspace_access.get("status_code"),
                workspace_access.get("statusCode"),
                200 if allowed else 403,
                default=403,
            ),
            403,
        )

        if _safe_bool(access_view.get("auth_unavailable"), False):
            reason = "auth_unavailable"
            status_code = 503
        elif _safe_bool(access_view.get("user_blocked"), False):
            reason = "user_blocked"
            status_code = 403
        elif _safe_bool(access_view.get("access_blocked"), False):
            reason = "access_blocked"
            status_code = 403

        result: Dict[str, Any] = dict(workspace_access)
        result.update(
            {
                "ok": allowed,
                "allowed": allowed,
                "workspace": normalized_workspace,
                "project_public_id": project_view.get("public_id", ""),
                "projectPublicId": project_view.get("public_id", ""),
                "can_view": allowed,
                "canView": allowed,
                "can_edit": _safe_bool(access_view.get("form_can_edit"), False),
                "canEdit": _safe_bool(access_view.get("form_can_edit"), False),
                "read_only": _safe_bool(access_view.get("read_only"), True),
                "readOnly": _safe_bool(access_view.get("read_only"), True),
                "public_viewer": _safe_bool(access_view.get("public_viewer"), False),
                "publicViewer": _safe_bool(access_view.get("public_viewer"), False),
                "access_mode": access_view.get("access_mode", "anonymous"),
                "accessMode": access_view.get("access_mode", "anonymous"),
                "reason": reason,
                "code": reason,
                "status_code": status_code,
                "statusCode": status_code,
            }
        )

        return result
    except Exception:
        return {
            "ok": False,
            "allowed": False,
            "workspace": normalize_workspace(workspace, WORKSPACE_PROJECT),
            "project_public_id": project_view.get("public_id", ""),
            "projectPublicId": project_view.get("public_id", ""),
            "can_view": False,
            "canView": False,
            "can_edit": False,
            "canEdit": False,
            "read_only": True,
            "readOnly": True,
            "public_viewer": False,
            "publicViewer": False,
            "access_mode": "blocked",
            "accessMode": "blocked",
            "reason": "context_error",
            "code": "context_error",
            "status_code": 500,
            "statusCode": 500,
        }


# ─────────────────────────────────────────────────────────────
# Paths/context helpers
# ─────────────────────────────────────────────────────────────

def _quote_path_value(value: Any) -> str:
    try:
        from urllib.parse import quote

        return quote(_safe_str(value, "", 240), safe="")
    except Exception:
        return _safe_str(value, "", 240)


def _project_context_path(project_public_id: str, is_new: bool) -> str:
    try:
        if is_new or not project_public_id or project_public_id == "new":
            return PROJECT_CONTEXT_PATH_NEW

        return PROJECT_CONTEXT_PATH_TEMPLATE.format(project_public_id=_quote_path_value(project_public_id))
    except Exception:
        return PROJECT_CONTEXT_PATH_NEW


def _project_return_path(project_public_id: str, is_new: bool) -> str:
    try:
        if is_new or not project_public_id or project_public_id == "new":
            return "/project=new"

        return PROJECT_RETURN_PATH_TEMPLATE.format(project_public_id=_quote_path_value(project_public_id))
    except Exception:
        return "/project=new"


def _build_paths(project_public_id: str, is_new: bool, show_management_sections: bool) -> Dict[str, str]:
    try:
        has_project = bool(project_public_id and project_public_id != "new" and not is_new)

        return {
            "createProject": "/v1/projects",
            "create_project": "/v1/projects",
            "updateProject": f"/v1/projects/{project_public_id}" if has_project else "",
            "update_project": f"/v1/projects/{project_public_id}" if has_project else "",
            "getProject": f"/v1/projects/{project_public_id}" if has_project else "",
            "get_project": f"/v1/projects/{project_public_id}" if has_project else "",
            "context": _project_context_path(project_public_id, is_new),
            "returnUrl": _project_return_path(project_public_id, is_new),
            "return_url": _project_return_path(project_public_id, is_new),
            "workspaceAccess": f"/v1/projects/{project_public_id}/workspace-access/project" if has_project else "",
            "workspace_access": f"/v1/projects/{project_public_id}/workspace-access/project" if has_project else "",
            "members": f"/v1/projects/{project_public_id}/members" if has_project and show_management_sections else "",
            "invitations": f"/v1/projects/{project_public_id}/invitations" if has_project and show_management_sections else "",
            "publication": f"/v1/projects/{project_public_id}/publication" if has_project and show_management_sections else "",
            "projectRoot": "/",
            "project_root": "/",
            "projectNew": "/project=new",
            "project_new": "/project=new",
            "projectOpen": f"/project={project_public_id}" if has_project else "/project=new",
            "project_open": f"/project={project_public_id}" if has_project else "/project=new",
        }
    except Exception:
        return {
            "createProject": "/v1/projects",
            "create_project": "/v1/projects",
            "updateProject": "",
            "update_project": "",
            "getProject": "",
            "get_project": "",
            "context": PROJECT_CONTEXT_PATH_NEW,
            "returnUrl": "/project=new",
            "return_url": "/project=new",
            "workspaceAccess": "",
            "workspace_access": "",
            "members": "",
            "invitations": "",
            "publication": "",
            "projectRoot": "/",
            "project_root": "/",
            "projectNew": "/project=new",
            "project_new": "/project=new",
            "projectOpen": "/project=new",
            "project_open": "/project=new",
        }


def _request_info(request_obj: Any = None) -> Dict[str, Any]:
    try:
        req = request_obj if request_obj is not None else request

        if req is None:
            return {}

        return {
            "path": _safe_str(getattr(req, "path", ""), "", 2000),
            "method": _safe_str(getattr(req, "method", ""), "", 40),
            "url_root": _safe_str(getattr(req, "url_root", ""), "", 2000),
            "host": _safe_str(getattr(req, "host", ""), "", 500),
        }
    except Exception:
        return {}


def _context_cache_key(
    *,
    project_view: Mapping[str, Any],
    current_user_payload: Mapping[str, Any],
    access_payload: Mapping[str, Any],
    workspace_access_payload: Mapping[str, Any],
    publication_payload: Mapping[str, Any],
    is_new: bool,
    workspace: str,
) -> str:
    try:
        user_fingerprint = {
            "id": _first_non_empty(
                current_user_payload.get("id"),
                current_user_payload.get("user_id"),
                current_user_payload.get("userId"),
                current_user_payload.get("app_user_id"),
                current_user_payload.get("appUserId"),
                current_user_payload.get("auth_user_id"),
                current_user_payload.get("authUserId"),
                default="anonymous",
            ),
            "authenticated": current_user_payload.get("authenticated"),
            "persistent": current_user_payload.get("persistent"),
            "demo": current_user_payload.get("demo_mode") or current_user_payload.get("demoMode"),
            "blocked": current_user_payload.get("blocked") or current_user_payload.get("user_blocked"),
        }

        raw = {
            "project_public_id": project_view.get("public_id"),
            "project_updated_at": project_view.get("updated_at") or project_view.get("updatedAt"),
            "project_status": project_view.get("status"),
            "visibility": project_view.get("visibility"),
            "is_new": is_new,
            "workspace": workspace,
            "user": user_fingerprint,
            "access": access_payload,
            "workspace_access": workspace_access_payload,
            "publication": publication_payload,
        }

        return "project_workspace_context:" + str(abs(hash(_json_fingerprint(raw, 6000))))
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# Public builders
# ─────────────────────────────────────────────────────────────

def build_project_workspace_context_result(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Any = None,
    auth_context: Any = None,
    access: Any = None,
    workspace_access: Any = None,
    publication: Any = None,
    ui_flags: Any = None,
    is_new: Any = None,
    workspace: Any = WORKSPACE_PROJECT,
    request_obj: Any = None,
    extra_context: Optional[Mapping[str, Any]] = None,
    use_cache: Optional[bool] = None,
) -> ProjectWorkspaceContextResult:
    warnings: list[str] = []
    errors: list[str] = []

    try:
        normalized_workspace = normalize_workspace(workspace, WORKSPACE_PROJECT)

        project_payload_dict = _project_payload(project, project_payload)
        resolved_is_new = _is_new_project(project_payload_dict, explicit_is_new=is_new)
        project_view = _build_project_view(project_payload_dict, is_new=resolved_is_new)

        current_user_payload = _current_user_payload(current_user, auth_context)
        auth_payload = _payload_from_object(auth_context) or _safe_dict(current_user_payload.get("auth"))
        access_payload = _access_payload(project_view, access=access)
        workspace_access_payload = _workspace_access_payload(project_view, workspace_access=workspace_access)
        publication_payload = _publication_payload(project_view, publication=publication)
        ui_flags_payload = _payload_from_object(ui_flags)

        cache_allowed = _cache_enabled() if use_cache is None else bool(use_cache)

        cache_key = ""
        if cache_allowed:
            cache_key = _context_cache_key(
                project_view=project_view,
                current_user_payload=current_user_payload,
                access_payload=access_payload,
                workspace_access_payload=workspace_access_payload,
                publication_payload=publication_payload,
                is_new=resolved_is_new,
                workspace=normalized_workspace,
            )
            cached = _cache_get(cache_key)
            if isinstance(cached, ProjectWorkspaceContextResult):
                return cached
            if isinstance(cached, Mapping):
                return ProjectWorkspaceContextResult(
                    ok=True,
                    code="ok_cached",
                    message="Project workspace context loaded from cache.",
                    context=dict(cached),
                )

        access_view = _build_access_view(
            project_view=project_view,
            access_payload=access_payload,
            workspace_access_payload=workspace_access_payload,
            current_user_payload=current_user_payload,
            auth_payload=auth_payload,
            is_new=resolved_is_new,
        )

        publication_view = _build_publication_view(
            project_view=project_view,
            publication_payload=publication_payload,
            access_view=access_view,
        )

        workspace_access_view = _build_workspace_access_view(
            workspace=normalized_workspace,
            project_view=project_view,
            workspace_access_payload=workspace_access_payload,
            access_view=access_view,
            publication_view=publication_view,
        )

        project_public_id = _safe_str(project_view.get("public_id"), "new" if resolved_is_new else "", 240)
        show_management_sections = _safe_bool(access_view.get("show_management_sections"), False)
        paths = _build_paths(project_public_id, resolved_is_new, show_management_sections)

        ui_flags_view: Dict[str, Any] = dict(ui_flags_payload)
        ui_flags_view.update(
            {
                "workspace": normalized_workspace,
                "is_new": resolved_is_new,
                "isNew": resolved_is_new,
                "is_configured": _safe_bool(project_view.get("is_configured"), False),
                "isConfigured": _safe_bool(project_view.get("is_configured"), False),

                "authenticated": _safe_bool(access_view.get("authenticated"), False),
                "auth_unavailable": _safe_bool(access_view.get("auth_unavailable"), False),
                "authUnavailable": _safe_bool(access_view.get("auth_unavailable"), False),
                "user_blocked": _safe_bool(access_view.get("user_blocked"), False),
                "userBlocked": _safe_bool(access_view.get("user_blocked"), False),
                "access_blocked": _safe_bool(access_view.get("access_blocked"), False),
                "accessBlocked": _safe_bool(access_view.get("access_blocked"), False),

                "demo_mode": _safe_bool(access_view.get("demo_mode"), False),
                "demoMode": _safe_bool(access_view.get("demo_mode"), False),
                "persistent": _safe_bool(access_view.get("persistent"), False),
                "public_viewer": _safe_bool(access_view.get("public_viewer"), False),
                "publicViewer": _safe_bool(access_view.get("public_viewer"), False),
                "read_only": _safe_bool(access_view.get("read_only"), True),
                "readOnly": _safe_bool(access_view.get("read_only"), True),
                "access_mode": _safe_str(access_view.get("access_mode"), "anonymous", 120),
                "accessMode": _safe_str(access_view.get("access_mode"), "anonymous", 120),

                "can_view": _safe_bool(access_view.get("can_view"), False),
                "canView": _safe_bool(access_view.get("can_view"), False),
                "can_edit": _safe_bool(access_view.get("can_edit"), False),
                "canEdit": _safe_bool(access_view.get("can_edit"), False),
                "can_manage": _safe_bool(access_view.get("can_manage"), False),
                "canManage": _safe_bool(access_view.get("can_manage"), False),
                "can_view_settings": _safe_bool(access_view.get("can_view_settings"), False),
                "canViewSettings": _safe_bool(access_view.get("can_view_settings"), False),
                "can_manage_team": _safe_bool(access_view.get("can_manage_team"), False),
                "canManageTeam": _safe_bool(access_view.get("can_manage_team"), False),
                "can_publish": _safe_bool(access_view.get("can_publish"), False),
                "canPublish": _safe_bool(access_view.get("can_publish"), False),
                "can_mutate": _safe_bool(access_view.get("can_mutate"), False),
                "canMutate": _safe_bool(access_view.get("can_mutate"), False),
                "form_can_edit": _safe_bool(access_view.get("form_can_edit"), False),
                "formCanEdit": _safe_bool(access_view.get("form_can_edit"), False),
                "show_management_sections": show_management_sections,
                "showManagementSections": show_management_sections,
                "show_management_locked": _safe_bool(access_view.get("show_management_locked"), False),
                "showManagementLocked": _safe_bool(access_view.get("show_management_locked"), False),

                "address_mode": "single_box",
                "addressMode": "single_box",
                "system_references_visible": False,
                "systemReferencesVisible": False,
            }
        )

        project_view["access"] = access_view
        project_view["workspace_access"] = workspace_access_view
        project_view["workspaceAccess"] = workspace_access_view
        project_view["publication"] = publication_view
        project_view["ui_flags"] = ui_flags_view
        project_view["uiFlags"] = ui_flags_view
        project_view["read_only"] = ui_flags_view["read_only"]
        project_view["readOnly"] = ui_flags_view["readOnly"]
        project_view["public_viewer"] = ui_flags_view["public_viewer"]
        project_view["publicViewer"] = ui_flags_view["publicViewer"]
        project_view["demo_mode"] = ui_flags_view["demo_mode"]
        project_view["demoMode"] = ui_flags_view["demoMode"]
        project_view["access_mode"] = ui_flags_view["access_mode"]
        project_view["accessMode"] = ui_flags_view["accessMode"]

        context: Dict[str, Any] = {
            "project": project_view,
            "current_project": project_view,
            "project_view": project_view,

            "current_user": current_user_payload,
            "current_user_view": current_user_payload,
            "auth": auth_payload or current_user_payload,
            "auth_context": auth_payload or current_user_payload,

            "access": access_view,
            "access_view": access_view,
            "workspace_access": workspace_access_view,
            "workspace_access_view": workspace_access_view,
            "publication": publication_view,
            "publication_view": publication_view,
            "ui_flags": ui_flags_view,

            "is_new": resolved_is_new,
            "workspace": normalized_workspace,
            "paths": paths,
            "request_info": _request_info(request_obj),

            "project_workspace_context": {
                "ok": True,
                "code": "ok",
                "service": "project_workspace_context",
                "warnings": warnings,
                "errors": errors,
                "cache_enabled": cache_allowed,
                "workspace": normalized_workspace,
                "project_public_id": project_public_id,
            },
        }

        extra = _safe_dict(extra_context)
        if extra:
            context.update(extra)

        result = ProjectWorkspaceContextResult(
            ok=True,
            code="ok",
            message="Project workspace context built.",
            context=context,
            warnings=warnings,
            errors=errors,
        )

        if cache_allowed and cache_key:
            _cache_set(cache_key, result)

        return result

    except Exception as exc:
        _log_exception("build_project_workspace_context_result failed", exc)

        errors.append(f"{exc.__class__.__name__}: {exc}")

        fallback_context = build_project_workspace_fallback_context(
            code="project_workspace_context_failed",
            message="Projekt-Workspace-Kontext konnte nicht vollständig aufgebaut werden.",
            error=f"{exc.__class__.__name__}: {exc}",
        )

        return ProjectWorkspaceContextResult(
            ok=False,
            code="project_workspace_context_failed",
            message="Project workspace context failed; fallback context returned.",
            context=fallback_context,
            warnings=warnings,
            errors=errors,
        )


def build_project_workspace_context(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Any = None,
    auth_context: Any = None,
    access: Any = None,
    workspace_access: Any = None,
    publication: Any = None,
    ui_flags: Any = None,
    is_new: Any = None,
    workspace: Any = WORKSPACE_PROJECT,
    request_obj: Any = None,
    extra_context: Optional[Mapping[str, Any]] = None,
    use_cache: Optional[bool] = None,
) -> Dict[str, Any]:
    try:
        result = build_project_workspace_context_result(
            project=project,
            project_payload=project_payload,
            current_user=current_user,
            auth_context=auth_context,
            access=access,
            workspace_access=workspace_access,
            publication=publication,
            ui_flags=ui_flags,
            is_new=is_new,
            workspace=workspace,
            request_obj=request_obj,
            extra_context=extra_context,
            use_cache=use_cache,
        )
        return dict(result.context or {})
    except Exception as exc:
        _log_exception("build_project_workspace_context failed", exc)
        return build_project_workspace_fallback_context(
            code="project_workspace_context_exception",
            message="Projekt-Workspace-Kontext konnte nicht aufgebaut werden.",
            error=f"{exc.__class__.__name__}: {exc}",
        )


def build_project_workspace_fallback_context(
    *,
    code: str = "project_workspace_context_fallback",
    message: str = "Projekt-Workspace-Kontext wurde mit Fallback-Werten aufgebaut.",
    error: str = "",
) -> Dict[str, Any]:
    try:
        access_view = {
            "role": "",
            "permissions": {},
            "authenticated": False,
            "auth_unavailable": False,
            "authUnavailable": False,
            "user_blocked": False,
            "userBlocked": False,
            "access_blocked": True,
            "accessBlocked": True,
            "demo_mode": False,
            "demoMode": False,
            "persistent": False,
            "public_viewer": False,
            "publicViewer": False,
            "is_public_viewer": False,
            "isPublicViewer": False,
            "access_mode": "blocked",
            "accessMode": "blocked",
            "read_only": True,
            "readOnly": True,
            "readonly": True,
            "can_view": False,
            "canView": False,
            "can_edit": False,
            "canEdit": False,
            "can_manage": False,
            "canManage": False,
            "can_view_settings": False,
            "canViewSettings": False,
            "can_manage_team": False,
            "canManageTeam": False,
            "can_publish": False,
            "canPublish": False,
            "can_mutate": False,
            "canMutate": False,
            "form_can_edit": False,
            "formCanEdit": False,
            "show_management_sections": False,
            "showManagementSections": False,
            "show_management_locked": True,
            "showManagementLocked": True,
        }

        publication_view = {
            "visibility": DEFAULT_VISIBILITY,
            "publication_enabled": False,
            "publicationEnabled": False,
            "published_workspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "publishedWorkspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "effective_published_workspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "effectivePublishedWorkspaces": {workspace: False for workspace in PUBLICATION_WORKSPACES},
            "require_auth": False,
            "requireAuth": False,
            "require_project_permission": False,
            "requireProjectPermission": False,
            "can_publish": False,
            "canPublish": False,
            "read_only": True,
            "readOnly": True,
        }

        workspace_access_view = {
            "ok": False,
            "allowed": False,
            "workspace": WORKSPACE_PROJECT,
            "project_public_id": "new",
            "projectPublicId": "new",
            "can_view": False,
            "canView": False,
            "can_edit": False,
            "canEdit": False,
            "read_only": True,
            "readOnly": True,
            "public_viewer": False,
            "publicViewer": False,
            "access_mode": "blocked",
            "accessMode": "blocked",
            "reason": code,
            "code": code,
            "status_code": 500,
            "statusCode": 500,
        }

        ui_flags = {
            "workspace": WORKSPACE_PROJECT,
            "is_new": True,
            "isNew": True,
            "is_configured": False,
            "isConfigured": False,
            "authenticated": False,
            "auth_unavailable": False,
            "authUnavailable": False,
            "user_blocked": False,
            "userBlocked": False,
            "access_blocked": True,
            "accessBlocked": True,
            "demo_mode": False,
            "demoMode": False,
            "persistent": False,
            "public_viewer": False,
            "publicViewer": False,
            "read_only": True,
            "readOnly": True,
            "access_mode": "blocked",
            "accessMode": "blocked",
            "can_view": False,
            "canView": False,
            "can_edit": False,
            "canEdit": False,
            "can_manage": False,
            "canManage": False,
            "can_view_settings": False,
            "canViewSettings": False,
            "can_manage_team": False,
            "canManageTeam": False,
            "can_publish": False,
            "canPublish": False,
            "can_mutate": False,
            "canMutate": False,
            "form_can_edit": False,
            "formCanEdit": False,
            "show_management_sections": False,
            "showManagementSections": False,
            "show_management_locked": True,
            "showManagementLocked": True,
            "address_mode": "single_box",
            "addressMode": "single_box",
            "system_references_visible": False,
            "systemReferencesVisible": False,
        }

        project_view = {
            "id": "",
            "project_id": "",
            "projectId": "",
            "public_id": "new",
            "publicId": "new",
            "project_public_id": "new",
            "projectPublicId": "new",
            "name": "",
            "title": "",
            "display_name": "Neues Projekt",
            "displayName": "Neues Projekt",
            "description": "",
            "address_text": "",
            "addressText": "",
            "address": {"text": "", "address_text": "", "addressText": ""},
            "visibility": DEFAULT_VISIBILITY,
            "is_public": False,
            "isPublic": False,
            "setup_status": DEFAULT_SETUP_STATUS,
            "setupStatus": DEFAULT_SETUP_STATUS,
            "is_configured": False,
            "isConfigured": False,
            "is_new": True,
            "isNew": True,
            "access": access_view,
            "workspace_access": workspace_access_view,
            "workspaceAccess": workspace_access_view,
            "publication": publication_view,
            "ui_flags": ui_flags,
            "uiFlags": ui_flags,
            "read_only": True,
            "readOnly": True,
            "public_viewer": False,
            "publicViewer": False,
            "demo_mode": False,
            "demoMode": False,
            "access_mode": "blocked",
            "accessMode": "blocked",
        }

        return {
            "project": project_view,
            "current_project": project_view,
            "project_view": project_view,
            "current_user": {},
            "current_user_view": {},
            "auth": {},
            "auth_context": {},
            "access": access_view,
            "access_view": access_view,
            "workspace_access": workspace_access_view,
            "workspace_access_view": workspace_access_view,
            "publication": publication_view,
            "publication_view": publication_view,
            "ui_flags": ui_flags,
            "is_new": True,
            "workspace": WORKSPACE_PROJECT,
            "paths": _build_paths("new", True, False),
            "request_info": {},
            "project_workspace_context": {
                "ok": False,
                "code": code,
                "service": "project_workspace_context",
                "message": message,
                "error": error,
                "warnings": [],
                "errors": [error] if error else [],
                "cache_enabled": False,
                "workspace": WORKSPACE_PROJECT,
                "project_public_id": "new",
            },
        }
    except Exception:
        return {
            "project": {},
            "current_project": {},
            "project_view": {},
            "current_user": {},
            "current_user_view": {},
            "auth": {},
            "auth_context": {},
            "access": {},
            "access_view": {},
            "workspace_access": {},
            "workspace_access_view": {},
            "publication": {},
            "publication_view": {},
            "ui_flags": {},
            "is_new": True,
            "workspace": WORKSPACE_PROJECT,
            "paths": {},
            "request_info": {},
            "project_workspace_context": {
                "ok": False,
                "code": "fatal_fallback_failed",
                "service": "project_workspace_context",
            },
        }


def get_project_workspace_context_status() -> Dict[str, Any]:
    try:
        return {
            "ok": True,
            "service": "project_workspace_context",
            "phase": "template-context-normalization",
            "cache": {
                "enabled": _cache_enabled(),
                "ttl_seconds": _cache_ttl_seconds(),
                "max_items": _cache_max_items(),
                "module_cache_keys": sorted(_MODULE_CACHE.keys()),
                "module_cache_size": len(_MODULE_CACHE),
            },
            "rules": {
                "template_receives_normalized_context": True,
                "public_viewer_forces_read_only": True,
                "demo_disables_management_sections": True,
                "auth_unavailable_disables_mutations": True,
                "blocked_user_disables_mutations": True,
                "team_publication_only_for_management_context": True,
                "snake_and_camel_aliases": True,
                "fallback_context_on_error": True,
            },
            "publication_workspaces": list(PUBLICATION_WORKSPACES),
            "never_public_workspaces": list(NEVER_PUBLIC_WORKSPACES),
        }
    except Exception as exc:
        return {
            "ok": False,
            "service": "project_workspace_context",
            "error": f"{exc.__class__.__name__}: {exc}",
        }


__all__ = [
    "WORKSPACE_PROJECT",
    "ProjectWorkspaceContextResult",
    "build_project_workspace_context",
    "build_project_workspace_context_result",
    "build_project_workspace_fallback_context",
    "clear_project_workspace_context_cache",
    "get_project_workspace_context_status",
    "normalize_visibility",
    "normalize_role",
    "normalize_access_mode",
    "normalize_setup_status",
    "normalize_workspace",
]