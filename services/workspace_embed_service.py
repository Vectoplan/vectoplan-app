# services/vectoplan-app/services/workspace_embed_service.py
from __future__ import annotations

"""
Browser-safe workspace embed URL builder for vectoplan-app.

Responsibilities
----------------
- Build public Editor, CAD, LV and OpenLayer URLs only after route/service authorization.
- Translate an App project into a small browser contract.
- Keep App project IDs distinct from Chunk project/world IDs.
- Enforce project-role maximums as defense in depth.
- Refuse Editor embeds until Chunk provisioning and access projection are usable.

Security invariants
-------------------
- Never expose INTERNAL_URL values, auth identities, e-mail addresses, sessions,
  cookies, credentials, API keys, tokens, local database IDs, or raw metadata.
- A project viewer and every public viewer are always read-only.
- Browser parameters cannot elevate role, edit, command, materialize, or access.
- Chunk IDs do not imply readiness when an explicit pending/error state exists.
- Missing Chunk world IDs are never replaced with an invented default world.
- Admin, settings, team, permissions, and system workspaces are never external.
"""

import copy
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

try:
    from flask import current_app, has_app_context, has_request_context, request
except Exception:  # pragma: no cover - allows isolated imports and tests.
    current_app = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[no-redef]
        return False

    def has_request_context() -> bool:  # type: ignore[no-redef]
        return False


# -----------------------------------------------------------------------------
# Workspaces and roles
# -----------------------------------------------------------------------------

WORKSPACE_PROJECT = "project"
WORKSPACE_MAP = "map"
WORKSPACE_EDITOR3D = "editor3d"
WORKSPACE_CAD2D = "cad2d"
WORKSPACE_LV = "lv"
WORKSPACE_VERSIONS = "versions"
WORKSPACE_ADMIN = "admin"

EXTERNAL_WORKSPACES = frozenset(
    {WORKSPACE_EDITOR3D, WORKSPACE_MAP, WORKSPACE_CAD2D, WORKSPACE_LV}
)
FORBIDDEN_EXTERNAL_WORKSPACES = frozenset(
    {
        WORKSPACE_ADMIN,
        "settings",
        "team",
        "permissions",
        "system",
        "system_refs",
        "access",
    }
)

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
VALID_PROJECT_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER})

ROLE_MAXIMUMS: Dict[str, Dict[str, bool]] = {
    ROLE_OWNER: {
        "view": True,
        "edit": True,
        "manage": True,
        "delete": True,
        "transfer": True,
        "command": True,
        "materialize": True,
    },
    ROLE_ADMIN: {
        "view": True,
        "edit": True,
        "manage": True,
        "delete": False,
        "transfer": False,
        "command": True,
        "materialize": True,
    },
    ROLE_EDITOR: {
        "view": True,
        "edit": True,
        "manage": False,
        "delete": False,
        "transfer": False,
        "command": True,
        "materialize": True,
    },
    ROLE_VIEWER: {
        "view": True,
        "edit": False,
        "manage": False,
        "delete": False,
        "transfer": False,
        "command": False,
        "materialize": False,
    },
}


# -----------------------------------------------------------------------------
# Defaults and contracts
# -----------------------------------------------------------------------------

DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"
DEFAULT_CAD_PUBLIC_URL = "http://localhost:5104"
DEFAULT_CAD_ROUTE = "/cad"
DEFAULT_LV_PUBLIC_URL = "http://localhost:5105"
DEFAULT_LV_ROUTE = "/lv"
DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"
DEFAULT_OPENLAYER_ROUTE = "/map"
DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"
DEFAULT_MAP_PROJECT_ZOOM = 17

DEFAULT_CONTEXT_PATH_TEMPLATE = "/ui/project/{project_public_id}/context.json"
DEFAULT_RETURN_PATH_TEMPLATE = "/project={project_public_id}"
DEFAULT_CHUNK_BROWSER_BASE_URL = "/editor/api/chunk"

DEFAULT_CACHE_MAX_AGE_SECONDS = 15.0
DEFAULT_CACHE_MAX_ITEMS = 512
EMBED_CONTRACT_VERSION = "2026-07-29.1"

MAX_QUERY_VALUE_LENGTH = 4096
MAX_ROUTE_HINTS_QUERY_LENGTH = 12000

DOCKER_INTERNAL_HOSTS = frozenset(
    {
        "cad",
        "chunk",
        "editor",
        "lv",
        "openlayer",
        "server-cad",
        "server-chunk",
        "server-editor",
        "server-lv",
        "server-openlayer",
        "vectoplan-cad",
        "vectoplan-chunk",
        "vectoplan-editor",
        "vectoplan-lv",
        "vectoplan-openlayer",
        "vectoplan_cad",
        "vectoplan_chunk",
        "vectoplan_editor",
        "vectoplan_lv",
        "vectoplan_openlayer",
        "postgres",
        "redis",
        "db",
    }
)

SECRET_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "jwt",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session",
    "token",
)

SECRET_QUERY_KEYS = frozenset(
    {
        "access_token",
        "account_id",
        "accountid",
        "api_key",
        "apikey",
        "auth",
        "auth_email",
        "auth_user_id",
        "authorization",
        "cookie",
        "csrf",
        "csrf_token",
        "email",
        "internal_base_url",
        "internal_url",
        "jwt",
        "key",
        "local_user_id",
        "password",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "sessionid",
        "sid",
        "token",
        "user_id",
        "userid",
    }
)

# External callers may add presentation hints, but never security or identity data.
DEFAULT_ALLOWED_EXTRA_QUERY_KEYS = frozenset(
    {
        "bbox",
        "camera",
        "center",
        "fit",
        "focus",
        "initial_panel",
        "initial_tab",
        "lat",
        "layer",
        "layers",
        "lng",
        "locale",
        "lon",
        "mode",
        "panel",
        "pitch",
        "selection",
        "show_sidebar",
        "show_toolbar",
        "style",
        "tab",
        "theme",
        "tool",
        "view",
        "bearing",
        "zoom",
    }
)

SECURITY_CONTROL_QUERY_KEYS = frozenset(
    {
        "access_mode",
        "app_project_db_id",
        "app_project_id",
        "app_project_public_id",
        "auth_required",
        "can_command",
        "can_edit",
        "can_manage",
        "can_materialize",
        "chunk_access_sync_status",
        "chunk_project_id",
        "chunk_provisioning_status",
        "chunk_ready",
        "chunk_route_hints",
        "chunk_status",
        "chunk_universe_id",
        "chunk_world_id",
        "conversation_id",
        "core_project_id",
        "demo_mode",
        "embed",
        "ephemeral",
        "is_public_viewer",
        "persistent",
        "project_id",
        "project_public_id",
        "project_role",
        "public_viewer",
        "read_only",
        "readonly",
        "role",
        "source",
        "universe_id",
        "world_id",
        "workspace",
        "vp_access_ticket",
    }
)

READ_ONLY_ROUTE_HINT_KEYS = frozenset(
    {
        "apiBaseUrl",
        "browserBaseUrl",
        "status",
        "placeableBlocks",
        "projects",
        "project",
        "projectBootstrap",
        "universes",
        "universe",
        "worlds",
        "world",
        "blocks",
        "chunk",
        "chunks",
        "chunksBatch",
    }
)
MUTATING_ROUTE_HINT_KEYS = frozenset({"commands"})

CHUNK_STATUS_READY = frozenset({"ready", "active", "linked", "created", "provisioned", "ok", "available"})
CHUNK_STATUS_PENDING = frozenset({"pending", "waiting", "queued", "initializing", "unknown", "provisioning"})
CHUNK_STATUS_FAILED = frozenset({"error", "failed", "failure", "unavailable", "repair_required"})
CHUNK_STATUS_DISABLED = frozenset({"disabled", "off"})

PROVISIONING_READY = frozenset({"ready", "fallback_ready", "provisioned", "created", "complete", "completed"})
PROVISIONING_PENDING = frozenset({"pending", "provisioning", "queued", "waiting", "deferred", "unknown"})
PROVISIONING_FAILED = frozenset({"failed", "error", "repair_required", "unavailable"})
PROVISIONING_DISABLED = frozenset({"disabled", "off"})

ACCESS_SYNC_READY = frozenset({"ready", "synced", "complete", "completed", "ok"})
ACCESS_SYNC_PENDING = frozenset({"pending", "syncing", "queued", "waiting", "deferred", "unknown"})
ACCESS_SYNC_FAILED = frozenset({"failed", "error", "repair_required", "unavailable"})
ACCESS_SYNC_DISABLED = frozenset({"disabled", "off"})


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkspaceTargetConfig:
    workspace: str
    service_name: str
    enabled: bool
    public_base_url: str
    route: str
    public_route_url: str
    source: str = "config"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "service_name": self.service_name,
            "enabled": bool(self.enabled),
            "public_base_url": self.public_base_url,
            "route": self.route,
            "public_route_url": self.public_route_url,
            "source": self.source,
            "warnings": list(self.warnings),
            "uses_public_url": True,
        }


@dataclass(frozen=True)
class WorkspaceAccessContract:
    role: str = ""
    access_mode: str = "anonymous"
    can_view: bool = False
    can_edit: bool = False
    can_manage: bool = False
    can_delete: bool = False
    can_transfer: bool = False
    can_command: bool = False
    can_materialize: bool = False
    read_only: bool = True
    public_viewer: bool = False
    demo_mode: bool = False
    persistent: bool = False
    authenticated: bool = False
    auth_unavailable: bool = False
    user_blocked: bool = False
    access_blocked: bool = False
    identity_mismatch: bool = False
    denial_code: str = ""
    denial_status_code: int = 403

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "project_role": self.role,
            "projectRole": self.role,
            "access_mode": self.access_mode,
            "accessMode": self.access_mode,
            "can_view": self.can_view,
            "canView": self.can_view,
            "can_edit": self.can_edit,
            "canEdit": self.can_edit,
            "can_manage": self.can_manage,
            "canManage": self.can_manage,
            "can_delete": self.can_delete,
            "canDelete": self.can_delete,
            "can_transfer": self.can_transfer,
            "canTransfer": self.can_transfer,
            "can_command": self.can_command,
            "canCommand": self.can_command,
            "can_materialize": self.can_materialize,
            "canMaterialize": self.can_materialize,
            "read_only": self.read_only,
            "readOnly": self.read_only,
            "public_viewer": self.public_viewer,
            "publicViewer": self.public_viewer,
            "demo_mode": self.demo_mode,
            "demoMode": self.demo_mode,
            "persistent": self.persistent,
            "authenticated": self.authenticated,
            "auth_unavailable": self.auth_unavailable,
            "authUnavailable": self.auth_unavailable,
            "user_blocked": self.user_blocked,
            "userBlocked": self.user_blocked,
            "access_blocked": self.access_blocked,
            "accessBlocked": self.access_blocked,
            "identity_mismatch": self.identity_mismatch,
            "identityMismatch": self.identity_mismatch,
            "denial_code": self.denial_code,
            "denialCode": self.denial_code,
            "denial_status_code": self.denial_status_code,
            "denialStatusCode": self.denial_status_code,
        }


@dataclass
class WorkspaceEmbedResult:
    ok: bool
    workspace: str
    url: str = ""
    target_url: str = ""
    public_base_url: str = ""
    route: str = ""
    code: str = "ok"
    message: str = ""
    project_public_id: str = ""
    app_project_public_id: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None
    uses_public_url: bool = True
    status_code: int = 200
    read_only: bool = True
    project_role: str = ""
    access_mode: str = "anonymous"
    can_edit: bool = False
    can_command: bool = False
    can_materialize: bool = False
    chunk: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.ok and self.url)

    def to_dict(self) -> Dict[str, Any]:
        safe_url = _sanitize_result_url(self.url)
        safe_target = _sanitize_result_url(self.target_url)
        safe_base = _normalize_public_base_url(self.public_base_url, "")
        safe_params = _clean_query_params(self.params, allow_security_controls=True)
        safe_chunk = _public_chunk_contract(self.chunk)

        return {
            "ok": bool(self.ok and (safe_url or not self.ok)),
            "workspace": self.workspace,
            "url": safe_url,
            "target_url": safe_target,
            "targetUrl": safe_target,
            "public_base_url": safe_base,
            "publicBaseUrl": safe_base,
            "route": self.route,
            "code": self.code,
            "message": self.message,
            "project_public_id": self.project_public_id,
            "projectPublicId": self.project_public_id,
            "app_project_public_id": self.app_project_public_id,
            "appProjectPublicId": self.app_project_public_id,
            "params": safe_params,
            "warnings": list(self.warnings or []),
            "error": self.error,
            "uses_public_url": bool(self.uses_public_url),
            "usesPublicUrl": bool(self.uses_public_url),
            "status_code": int(self.status_code),
            "statusCode": int(self.status_code),
            "read_only": bool(self.read_only),
            "readOnly": bool(self.read_only),
            "project_role": self.project_role,
            "projectRole": self.project_role,
            "access_mode": self.access_mode,
            "accessMode": self.access_mode,
            "can_edit": bool(self.can_edit),
            "canEdit": bool(self.can_edit),
            "can_command": bool(self.can_command),
            "canCommand": bool(self.can_command),
            "can_materialize": bool(self.can_materialize),
            "canMaterialize": bool(self.can_materialize),
            "chunk": safe_chunk,
            "contract_version": EMBED_CONTRACT_VERSION,
            "contractVersion": EMBED_CONTRACT_VERSION,
        }


# -----------------------------------------------------------------------------
# Generic defensive helpers
# -----------------------------------------------------------------------------

def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    try:
        text = str(value if value is not None else default).strip()
        if not text:
            text = str(default if default is not None else "").strip()
        if max_len > 0 and len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        text = _safe_str(value, "", 80).lower()
        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "ready", "ok", "active"}:
            return True
        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "pending", "error", "failed", "none", "null"}:
            return False
        return bool(default)
    except Exception:
        return bool(default)


def _safe_int(value: Any, default: int = 0, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        if value is None or isinstance(value, bool):
            result = int(default)
        else:
            result = int(float(str(value).strip()))
    except Exception:
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def _safe_float(value: Any, default: float = 0.0, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    try:
        result = float(value)
    except Exception:
        result = float(default)
    if minimum is not None:
        result = max(float(minimum), result)
    if maximum is not None:
        result = min(float(maximum), result)
    return result


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            for kwargs in (
                {"include_private": True, "include_refs": True},
                {"include_private": True},
                {},
            ):
                try:
                    result = value.to_dict(**kwargs)
                    return dict(result) if isinstance(result, Mapping) else {}
                except TypeError:
                    continue
                except Exception:
                    return {}
        return {}
    except Exception:
        return {}


def _safe_list(value: Any) -> list[Any]:
    try:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, (tuple, set, frozenset)):
            return list(value)
        return []
    except Exception:
        return []


def _deepcopy(value: Any, fallback: Any = None) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return fallback if fallback is not None else value


def _safe_quote(value: Any) -> str:
    try:
        return quote(_safe_str(value, "", 1000), safe="")
    except Exception:
        return ""


def _first_value(*values: Any, default: Any = None) -> Any:
    try:
        for value in values:
            if value is not None and value != "":
                return value
    except Exception:
        pass
    return default


def _mapping_value(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    try:
        for key in keys:
            if key in mapping:
                value = mapping.get(key)
                if value is not None and value != "":
                    return value
    except Exception:
        pass
    return default


def _object_value(obj: Any, *keys: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return _mapping_value(obj, *keys, default=default)
    for key in keys:
        try:
            if hasattr(obj, key):
                value = getattr(obj, key)
                if value is not None and value != "":
                    return value
        except Exception:
            continue
    return default


def _now() -> float:
    try:
        return time.monotonic()
    except Exception:
        return time.time()


def _json_dumps_safe(value: Any, max_len: int = MAX_ROUTE_HINTS_QUERY_LENGTH) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return text if len(text) <= max_len else ""
    except Exception:
        return ""


def _stable_fingerprint(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return ""


def _is_secret_key(key: Any) -> bool:
    text = _safe_str(key, "", 240).lower().replace("-", "_")
    if text in SECRET_QUERY_KEYS:
        return True
    return any(fragment in text for fragment in SECRET_KEY_FRAGMENTS)


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[REDACTED_DEPTH]"
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = _safe_str(key, "", 240)
            if _is_secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item, depth=depth + 1) for item in value]
    return value


def _log_warning(message: str, *args: Any) -> None:
    try:
        if has_app_context() and current_app is not None:
            current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[BaseException] = None) -> None:
    try:
        if has_app_context() and current_app is not None:
            if exc is None:
                current_app.logger.exception(message)
            else:
                current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Configuration and cache
# -----------------------------------------------------------------------------

def _config_get(key: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
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


def _config_str(key: str, default: str = "", max_len: int = 4000) -> str:
    return _safe_str(_config_get(key, default), default, max_len)


def _config_bool(key: str, default: bool = False) -> bool:
    return _safe_bool(_config_get(key, default), default)


def _config_float(key: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    return _safe_float(_config_get(key, default), default, minimum, maximum)


def _config_text_set(key: str, default: Iterable[str] = ()) -> set[str]:
    raw = _config_get(key, None)
    if raw is None:
        return {_safe_str(item, "", 240).lower() for item in default if _safe_str(item, "", 240)}
    try:
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("["):
                parsed = json.loads(text)
                values = parsed if isinstance(parsed, list) else []
            else:
                values = [item.strip() for item in text.replace(";", ",").split(",")]
        else:
            values = list(raw)
        return {_safe_str(item, "", 240).lower() for item in values if _safe_str(item, "", 240)}
    except Exception:
        return {_safe_str(item, "", 240).lower() for item in default if _safe_str(item, "", 240)}


_CACHE_LOCK = threading.RLock()
_MODULE_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _cache_max_age_seconds() -> float:
    return _config_float(
        "VECTOPLAN_WORKSPACE_EMBED_CACHE_TTL_SECONDS",
        DEFAULT_CACHE_MAX_AGE_SECONDS,
        minimum=0.0,
        maximum=3600.0,
    )


def _cache_max_items() -> int:
    return _safe_int(
        _config_get("VECTOPLAN_WORKSPACE_EMBED_CACHE_MAX_ITEMS", DEFAULT_CACHE_MAX_ITEMS),
        DEFAULT_CACHE_MAX_ITEMS,
        minimum=16,
        maximum=10000,
    )


def _cache_get(name: str) -> Optional[Any]:
    if not name or _cache_max_age_seconds() <= 0:
        return None
    try:
        with _CACHE_LOCK:
            item = _MODULE_CACHE.get(name)
            if not item:
                return None
            if _now() - float(item.get("ts") or 0.0) > _cache_max_age_seconds():
                _MODULE_CACHE.pop(name, None)
                return None
            _MODULE_CACHE.move_to_end(name)
            return _deepcopy(item.get("value"))
    except Exception:
        return None


def _cache_set(name: str, value: Any) -> Any:
    if not name or _cache_max_age_seconds() <= 0:
        return value
    try:
        with _CACHE_LOCK:
            _MODULE_CACHE[name] = {"ts": _now(), "value": _deepcopy(value)}
            _MODULE_CACHE.move_to_end(name)
            max_items = _cache_max_items()
            while len(_MODULE_CACHE) > max_items:
                _MODULE_CACHE.popitem(last=False)
    except Exception:
        pass
    return value


def clear_workspace_embed_cache() -> None:
    try:
        with _CACHE_LOCK:
            _MODULE_CACHE.clear()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Normalization
# -----------------------------------------------------------------------------

def normalize_workspace(value: Any, default: str = WORKSPACE_PROJECT) -> str:
    try:
        text = _safe_str(value, default, 120).lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "": WORKSPACE_PROJECT,
            "project": WORKSPACE_PROJECT,
            "projekt": WORKSPACE_PROJECT,
            "info": WORKSPACE_PROJECT,
            "overview": WORKSPACE_PROJECT,
            "details": WORKSPACE_PROJECT,
            "map": WORKSPACE_MAP,
            "karte": WORKSPACE_MAP,
            "openlayer": WORKSPACE_MAP,
            "openlayers": WORKSPACE_MAP,
            "3d": WORKSPACE_EDITOR3D,
            "editor": WORKSPACE_EDITOR3D,
            "editor3d": WORKSPACE_EDITOR3D,
            "editor_3d": WORKSPACE_EDITOR3D,
            "viewer": WORKSPACE_EDITOR3D,
            "viewer3d": WORKSPACE_EDITOR3D,
            "model": WORKSPACE_EDITOR3D,
            "2d": WORKSPACE_CAD2D,
            "cad": WORKSPACE_CAD2D,
            "cad2d": WORKSPACE_CAD2D,
            "cad_2d": WORKSPACE_CAD2D,
            "plan": WORKSPACE_CAD2D,
            "plan2d": WORKSPACE_CAD2D,
            "lv": WORKSPACE_LV,
            "boq": WORKSPACE_LV,
            "leistungsverzeichnis": WORKSPACE_LV,
            "versions": WORKSPACE_VERSIONS,
            "version": WORKSPACE_VERSIONS,
            "versionen": WORKSPACE_VERSIONS,
            "history": WORKSPACE_VERSIONS,
            "admin": WORKSPACE_ADMIN,
            "settings": WORKSPACE_ADMIN,
            "team": WORKSPACE_ADMIN,
            "permissions": WORKSPACE_ADMIN,
            "rechte": WORKSPACE_ADMIN,
            "system": WORKSPACE_ADMIN,
            "system_refs": WORKSPACE_ADMIN,
        }
        return aliases.get(text, default)
    except Exception:
        return default


def _normalize_role(value: Any) -> str:
    text = _safe_str(value, "", 80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "owner": ROLE_OWNER,
        "admin": ROLE_ADMIN,
        "administrator": ROLE_ADMIN,
        "manager": ROLE_ADMIN,
        "editor": ROLE_EDITOR,
        "edit": ROLE_EDITOR,
        "writer": ROLE_EDITOR,
        "viewer": ROLE_VIEWER,
        "view": ROLE_VIEWER,
        "reader": ROLE_VIEWER,
        "readonly": ROLE_VIEWER,
        "read_only": ROLE_VIEWER,
    }
    normalized = aliases.get(text, "")
    return normalized if normalized in VALID_PROJECT_ROLES else ""


def _normalize_chunk_status(value: Any, default: str = "pending") -> str:
    text = _safe_str(value, "", 80).lower().replace("-", "_").replace(" ", "_")
    if text in CHUNK_STATUS_READY:
        return "ready"
    if text in CHUNK_STATUS_FAILED:
        return "error" if text != "repair_required" else "repair_required"
    if text in CHUNK_STATUS_DISABLED:
        return "disabled"
    if text in CHUNK_STATUS_PENDING:
        return "pending"
    return default


def _normalize_provisioning_status(value: Any, default: str = "pending") -> str:
    text = _safe_str(value, "", 80).lower().replace("-", "_").replace(" ", "_")
    if text in PROVISIONING_READY:
        return "fallback_ready" if text == "fallback_ready" else "ready"
    if text in PROVISIONING_FAILED:
        return "repair_required" if text == "repair_required" else "failed"
    if text in PROVISIONING_DISABLED:
        return "disabled"
    if text in PROVISIONING_PENDING:
        return "provisioning" if text == "provisioning" else "pending"
    return default


def _normalize_access_sync_status(value: Any, default: str = "pending") -> str:
    text = _safe_str(value, "", 80).lower().replace("-", "_").replace(" ", "_")
    if text in ACCESS_SYNC_READY:
        return "ready"
    if text in ACCESS_SYNC_FAILED:
        return "repair_required" if text == "repair_required" else "failed"
    if text in ACCESS_SYNC_DISABLED:
        return "disabled"
    if text in ACCESS_SYNC_PENDING:
        return "syncing" if text == "syncing" else "pending"
    return default


def _normalize_world_template(value: Any, default: str = "") -> str:
    text = _safe_str(value, default, 80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "earth": "earth",
        "geo": "earth",
        "geospatial": "earth",
        "globe": "earth",
        "flat": "flat",
        "plane": "flat",
        "empty": "flat",
    }
    return aliases.get(text, default)


# -----------------------------------------------------------------------------
# URL security
# -----------------------------------------------------------------------------

def _normalize_public_base_url(value: Any, default: str = "") -> str:
    try:
        text = _safe_str(value, default, 4000).rstrip("/")
        if not text:
            return _safe_str(default, "", 4000).rstrip("/")
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return _safe_str(default, "", 4000).rstrip("/")
        if parsed.username or parsed.password:
            return _safe_str(default, "", 4000).rstrip("/")
        host = _safe_str(parsed.hostname, "", 255).lower()
        if not host or host in DOCKER_INTERNAL_HOSTS or host.endswith(".internal"):
            return _safe_str(default, "", 4000).rstrip("/")
        clean_path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, clean_path, "", ""))
    except Exception:
        return _safe_str(default, "", 4000).rstrip("/")


def _normalize_route(value: Any, default: str = "/") -> str:
    try:
        text = _safe_str(value, default, 2000)
        parsed = urlsplit(text)
        if parsed.scheme or parsed.netloc:
            text = default
            parsed = urlsplit(text)
        path = parsed.path or default or "/"
        if not path.startswith("/"):
            path = "/" + path
        while "//" in path:
            path = path.replace("//", "/")
        return path
    except Exception:
        fallback = _safe_str(default, "/", 2000)
        return fallback if fallback.startswith("/") else "/" + fallback


def _join_url(base_url: Any, route: Any) -> str:
    base = _normalize_public_base_url(base_url, "")
    path = _normalize_route(route, "/")
    if not base:
        return ""
    return base.rstrip("/") + "/" + path.lstrip("/")


def _is_public_browser_url(value: Any) -> bool:
    text = _normalize_public_base_url(value, "")
    return bool(text)


def _origin(value: Any) -> str:
    try:
        parsed = urlsplit(_safe_str(value, "", 4000))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except Exception:
        return ""


def _request_url_root(request_obj: Any = None) -> str:
    try:
        req = request_obj if request_obj is not None else request
        if req is None:
            return ""
        return _normalize_public_base_url(getattr(req, "url_root", ""), "")
    except Exception:
        return ""


_LOOPBACK_BROWSER_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _match_loopback_target_host(value: Any, request_obj: Any = None) -> str:
    """Keep local iframe hops on one browser site.

    Local development commonly opens the App through either ``localhost`` or
    ``127.0.0.1``. Cookies are host-bound, so mixing those names between App
    and Editor turns the Editor session exchange into a third-party-cookie
    flow. Only the exact loopback allowlist may be substituted; arbitrary Host
    headers never influence configured public targets.
    """

    target = _safe_str(value, "", 12000)
    if not target or not _config_bool("VECTOPLAN_EMBED_MATCH_LOOPBACK_HOST", True):
        return target

    try:
        target_parts = urlsplit(target)
        request_parts = urlsplit(_request_url_root(request_obj))
        target_host = _safe_str(target_parts.hostname, "", 255).lower()
        request_host = _safe_str(request_parts.hostname, "", 255).lower()
        if (
            target_parts.scheme not in {"http", "https"}
            or target_host not in _LOOPBACK_BROWSER_HOSTS
            or request_host not in _LOOPBACK_BROWSER_HOSTS
        ):
            return target

        rendered_host = f"[{request_host}]" if ":" in request_host else request_host
        netloc = rendered_host
        if target_parts.port is not None:
            netloc = f"{rendered_host}:{target_parts.port}"
        return urlunsplit(
            (
                target_parts.scheme,
                netloc,
                target_parts.path,
                target_parts.query,
                target_parts.fragment,
            )
        )
    except Exception:
        return target


def _configured_app_public_url() -> str:
    explicit = (
        _config_str("VECTOPLAN_APP_PUBLIC_URL", "", 4000)
        or _config_str("VECTOPLAN_APP_PUBLIC_BASE_URL", "", 4000)
        or _config_str("APP_PUBLIC_URL", "", 4000)
    )
    if explicit:
        return _normalize_public_base_url(explicit, "")
    return _normalize_public_base_url(DEFAULT_APP_PUBLIC_URL, DEFAULT_APP_PUBLIC_URL)


def _app_public_base_url(request_obj: Any = None, prefer_request_host: Optional[bool] = None) -> str:
    configured = _configured_app_public_url()
    if prefer_request_host is None:
        prefer_request_host = _config_bool("VECTOPLAN_EMBED_PREFER_REQUEST_HOST", False)
    if not prefer_request_host:
        return configured

    request_root = _request_url_root(request_obj)
    if not request_root:
        return configured

    configured_origin = _origin(configured)
    request_origin = _origin(request_root)
    allowlist = _config_text_set("VECTOPLAN_EMBED_ALLOWED_APP_ORIGINS", {configured_origin})
    if request_origin and (request_origin == configured_origin or request_origin.lower() in allowlist):
        return request_root
    return configured


def _absolute_app_url(path_or_url: Any, request_obj: Any = None, prefer_request_host: Optional[bool] = None) -> str:
    try:
        value = _safe_str(path_or_url, "", 4000)
        if not value:
            return ""
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            public = _normalize_public_base_url(value, "")
            if not public:
                return ""
            # Preserve the original safe path, but never its query or fragment.
            safe_parsed = urlsplit(value)
            return urlunsplit((safe_parsed.scheme, safe_parsed.netloc, safe_parsed.path, "", ""))
        return _join_url(_app_public_base_url(request_obj, prefer_request_host), value)
    except Exception:
        return ""


def _sanitize_result_url(value: Any) -> str:
    try:
        text = _safe_str(value, "", 12000)
        if not text:
            return ""
        split = urlsplit(text)
        if split.scheme not in {"http", "https"} or not split.netloc:
            return ""
        if not _is_public_browser_url(urlunsplit((split.scheme, split.netloc, "", "", ""))):
            return ""
        clean_query = urlencode(
            list(_clean_query_params(dict(parse_qsl(split.query, keep_blank_values=False)), allow_security_controls=True).items()),
            doseq=True,
        )
        return urlunsplit((split.scheme, split.netloc, split.path, clean_query, split.fragment))
    except Exception:
        return ""


def _looks_like_internal_url(value: Any) -> bool:
    try:
        text = _safe_str(value, "", 4000)
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        host = _safe_str(parsed.hostname, "", 255).lower()
        return bool(host in DOCKER_INTERNAL_HOSTS or host.endswith(".internal"))
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Query parameter sanitization
# -----------------------------------------------------------------------------

def _clean_query_params(
    params: Mapping[str, Any],
    *,
    allow_security_controls: bool = False,
    allowed_keys: Optional[set[str]] = None,
) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    try:
        for key, value in dict(params or {}).items():
            key_text = _safe_str(key, "", 180)
            key_lower = key_text.lower()
            if not key_text or _is_secret_key(key_lower):
                continue
            if not allow_security_controls and key_lower in SECURITY_CONTROL_QUERY_KEYS:
                continue
            if allowed_keys is not None and key_lower not in allowed_keys:
                continue
            if value is None:
                continue
            if isinstance(value, bool):
                clean[key_text] = "1" if value else "0"
                continue
            if isinstance(value, (list, tuple)):
                values = []
                for item in value:
                    item_text = _safe_str(item, "", MAX_QUERY_VALUE_LENGTH)
                    if item_text and not _looks_like_internal_url(item_text):
                        values.append(item_text)
                if values:
                    clean[key_text] = values
                continue
            value_text = _safe_str(value, "", MAX_QUERY_VALUE_LENGTH)
            if value_text and not _looks_like_internal_url(value_text):
                clean[key_text] = value_text
    except Exception:
        pass
    return clean


def _allowed_extra_query_keys() -> set[str]:
    configured = _config_text_set("VECTOPLAN_EMBED_ALLOWED_EXTRA_QUERY_KEYS", DEFAULT_ALLOWED_EXTRA_QUERY_KEYS)
    return configured or set(DEFAULT_ALLOWED_EXTRA_QUERY_KEYS)


def _clean_extra_query_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    return _clean_query_params(
        params,
        allow_security_controls=False,
        allowed_keys=_allowed_extra_query_keys(),
    )


def _append_query_params(url: str, params: Mapping[str, Any]) -> str:
    try:
        target = _safe_str(url, "", 12000)
        if not target:
            return ""
        split = urlsplit(target)
        if split.scheme not in {"http", "https"} or not split.netloc:
            return ""
        base_origin = _normalize_public_base_url(urlunsplit((split.scheme, split.netloc, "", "", "")), "")
        if not base_origin:
            return ""

        existing = _clean_extra_query_params(dict(parse_qsl(split.query, keep_blank_values=False)))
        secure = _clean_query_params(params, allow_security_controls=True)
        merged = dict(existing)
        merged.update(secure)  # Server-generated security controls always win.
        query = urlencode(merged, doseq=True)
        return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))
    except Exception:
        return ""


# -----------------------------------------------------------------------------
# Project and current-user extraction
# -----------------------------------------------------------------------------

def _project_payload_from_object(project: Any = None, project_payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    explicit = _safe_dict(project_payload)
    if explicit:
        return explicit
    payload = _safe_dict(project)
    if payload:
        return payload

    result: Dict[str, Any] = {}
    known = (
        "id",
        "public_id",
        "project_public_id",
        "app_project_public_id",
        "conversation_id",
        "is_demo",
        "project_scope",
        "chunk_project_id",
        "chunk_universe_id",
        "chunk_world_id",
        "chunk_status",
        "chunk_ready",
        "chunk_provisioning_status",
        "chunk_world_template_requested",
        "chunk_world_template_fallback",
        "chunk_world_template_effective",
        "chunk_fallback_used",
        "chunk_fallback_reason",
        "chunk_reference_fingerprint",
        "chunk_access_sync_status",
        "chunk_access_sync_error_code",
        "chunk_access_sync_error_message",
        "chunk_route_hints",
        "service_refs",
        "metadata_json",
        "settings",
        "latitude",
        "longitude",
        "coordinate_srid",
        "access",
        "workspace_access",
        "publication",
        "service_links",
    )
    for key in known:
        try:
            if hasattr(project, key):
                value = getattr(project, key)
                if value is not None:
                    result[key] = value
        except Exception:
            continue
    return result


def _core_project_id(project: Any, project_payload: Mapping[str, Any]) -> str:
    """Resolve the trusted Core reference from App-owned project state."""
    candidates: list[Mapping[str, Any]] = []
    try:
        project_refs = getattr(project, "service_refs", None)
        if isinstance(project_refs, Mapping):
            candidates.append(project_refs)
    except Exception:
        pass
    payload = _safe_dict(project_payload)
    for key in ("service_refs", "serviceRefs"):
        refs = _safe_dict(payload.get(key))
        if refs:
            candidates.append(refs)
    for refs in candidates:
        core = _safe_dict(refs.get("core"))
        value = _safe_str(
            _first_value(
                core.get("core_project_id"),
                core.get("coreProjectId"),
                core.get("project_id"),
                core.get("projectId"),
                default="",
            ),
            "",
            240,
        )
        if value:
            return value
    metadata_candidates = []
    try:
        metadata = getattr(project, "metadata_json", None)
        if isinstance(metadata, Mapping):
            metadata_candidates.append(metadata)
    except Exception:
        pass
    for key in ("metadata_json", "metadataJson", "metadata"):
        metadata = _safe_dict(payload.get(key))
        if metadata:
            metadata_candidates.append(metadata)
    for metadata in metadata_candidates:
        provisioning = _safe_dict(
            metadata.get("coreProvisioning") or metadata.get("core_provisioning")
        )
        value = _safe_str(
            provisioning.get("coreProjectId") or provisioning.get("core_project_id"),
            "",
            240,
        )
        if value:
            return value
    return ""


def _current_user_payload(current_user: Any = None) -> Dict[str, Any]:
    user = _safe_dict(current_user)
    nested = _safe_dict(user.get("auth"))
    for key in (
        "authenticated",
        "auth_unavailable",
        "user_blocked",
        "access_blocked",
        "blocked",
        "blocked_reason",
        "blocked_kind",
        "denial_status_code",
        "demo_mode",
        "persistent",
        "identity_consistent",
        "local_link_state",
        "access_mode",
        "public_viewer",
        "read_only",
    ):
        if key not in user and key in nested:
            user[key] = nested.get(key)
    return user

def _canonical_auth_user_id(current_user: Mapping[str, Any]) -> str:
    user = _safe_dict(current_user)
    auth = _safe_dict(user.get("auth"))
    return _safe_str(
        _first_value(
            user.get("canonical_user_id"),
            user.get("canonicalUserId"),
            user.get("auth_user_id"),
            user.get("authUserId"),
            auth.get("canonical_user_id"),
            auth.get("canonicalUserId"),
            auth.get("auth_user_id"),
            auth.get("authUserId"),
            default="",
        ),
        "",
        180,
    )


def _canonical_auth_username(current_user: Mapping[str, Any]) -> str:
    user = _safe_dict(current_user)
    auth = _safe_dict(user.get("auth"))
    nested_user = _safe_dict(user.get("user"))
    auth_user = _safe_dict(auth.get("user"))
    return _safe_str(
        _first_value(
            user.get("username"),
            user.get("preferred_username"),
            user.get("handle"),
            nested_user.get("username"),
            nested_user.get("preferred_username"),
            nested_user.get("handle"),
            auth.get("username"),
            auth.get("preferred_username"),
            auth.get("handle"),
            auth_user.get("username"),
            auth_user.get("preferred_username"),
            auth_user.get("handle"),
            default="",
        ),
        "",
        80,
    )


def _project_public_id(project: Any = None, project_payload: Optional[Mapping[str, Any]] = None) -> str:
    payload = _safe_dict(project_payload)
    value = _first_value(
        _mapping_value(payload, "public_id", "publicId", "project_public_id", "projectPublicId", "app_project_public_id", "appProjectPublicId"),
        _object_value(project, "public_id", "project_public_id", "app_project_public_id"),
        default="",
    )
    text = _safe_str(value, "", 180)
    return "" if text.lower() in {"", "none", "null"} else text


def _map_project_view_params(
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Build an OpenLayer start view from the current WGS84 project position."""
    payload = _safe_dict(project_payload)
    coordinates = _safe_dict(
        _first_value(
            payload.get("coordinates"),
            payload.get("coordinate"),
            payload.get("location"),
            default={},
        )
    )

    latitude_raw = _first_value(
        _mapping_value(payload, "latitude", "lat"),
        _mapping_value(coordinates, "latitude", "lat"),
        _object_value(project, "latitude", "lat"),
        default=None,
    )
    longitude_raw = _first_value(
        _mapping_value(payload, "longitude", "lng", "lon"),
        _mapping_value(coordinates, "longitude", "lng", "lon"),
        _object_value(project, "longitude", "lng", "lon"),
        default=None,
    )
    srid = _safe_str(
        _first_value(
            _mapping_value(payload, "coordinate_srid", "coordinateSrid", "srid"),
            _mapping_value(coordinates, "coordinate_srid", "coordinateSrid", "srid"),
            _object_value(project, "coordinate_srid", "coordinateSrid", "srid"),
            default="EPSG:4326",
        ),
        "EPSG:4326",
        80,
    ).upper().replace(" ", "")

    # OpenLayers receives geographic longitude/latitude. Projected coordinates
    # must never be interpreted as WGS84 by accident.
    if srid and not (
        srid in {"4326", "EPSG:4326", "WGS84", "CRS84", "OGC:CRS84"}
        or srid.endswith(":4326")
    ):
        return {}

    try:
        latitude = float(latitude_raw)
        longitude = float(longitude_raw)
    except (TypeError, ValueError):
        return {}

    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or latitude < -90.0
        or latitude > 90.0
        or longitude < -180.0
        or longitude > 180.0
    ):
        return {}

    zoom = _safe_int(
        _config_get("MAP_PROJECT_ZOOM", DEFAULT_MAP_PROJECT_ZOOM),
        DEFAULT_MAP_PROJECT_ZOOM,
        minimum=0,
        maximum=22,
    )

    def format_coordinate(value: float) -> str:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    return {
        "lon": format_coordinate(longitude),
        "lat": format_coordinate(latitude),
        "zoom": str(zoom),
    }


def _is_new_project_id(project_public_id: Any) -> bool:
    return _safe_str(project_public_id, "", 180).lower() in {"", "new", "create", "neu", "none", "null"}


def _project_access_payload(project_payload: Mapping[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(project_payload)
    return _safe_dict(payload.get("access") or payload.get("access_view") or payload.get("permission_result"))


def _project_role(project_payload: Mapping[str, Any]) -> str:
    payload = _safe_dict(project_payload)
    access = _project_access_payload(payload)
    # Deliberately exclude current_user.role/account_role/global roles.
    return _normalize_role(
        _first_value(
            access.get("role"),
            access.get("project_role"),
            access.get("projectRole"),
            access.get("membership_role"),
            access.get("membershipRole"),
            payload.get("project_role"),
            payload.get("projectRole"),
            payload.get("membership_role"),
            payload.get("membershipRole"),
            default="",
        )
    )


def _is_demo_mode(current_user: Mapping[str, Any], project_payload: Mapping[str, Any]) -> bool:
    if _is_blocked_context(current_user)[0]:
        return False
    payload = _safe_dict(project_payload)
    metadata = _safe_dict(payload.get("metadata") or payload.get("metadata_json"))
    demo_meta = _safe_dict(metadata.get("vectoplan_demo"))
    return _safe_bool(
        _first_value(
            current_user.get("demo_mode"),
            current_user.get("demoMode"),
            current_user.get("is_demo"),
            payload.get("is_demo"),
            payload.get("isDemo"),
            payload.get("demo_mode"),
            demo_meta.get("enabled"),
            default=False,
        ),
        False,
    )


def _is_public_viewer_context(current_user: Mapping[str, Any], project_payload: Mapping[str, Any]) -> bool:
    blocked, _, _ = _is_blocked_context(current_user)
    if blocked:
        return False
    payload = _safe_dict(project_payload)
    access = _project_access_payload(payload)
    mode = _safe_str(
        _first_value(
            current_user.get("access_mode"),
            current_user.get("accessMode"),
            access.get("access_mode"),
            access.get("accessMode"),
            payload.get("access_mode"),
            payload.get("accessMode"),
            default="",
        ),
        "",
        80,
    ).lower()
    return bool(
        mode == "public"
        or _safe_bool(current_user.get("public_viewer") or current_user.get("publicViewer") or current_user.get("is_public_viewer"), False)
        or _safe_bool(access.get("public_viewer") or access.get("publicViewer") or access.get("is_public_viewer"), False)
        or _safe_bool(payload.get("public_viewer") or payload.get("publicViewer") or payload.get("is_public_viewer"), False)
    )


def _is_blocked_context(current_user: Mapping[str, Any]) -> tuple[bool, str, int]:
    user = _safe_dict(current_user)
    auth_unavailable = _safe_bool(user.get("auth_unavailable") or user.get("authUnavailable"), False)
    local_link_state = _safe_str(user.get("local_link_state") or user.get("localLinkState"), "", 80).lower()
    identity_consistent_value = _first_value(user.get("identity_consistent"), user.get("identityConsistent"), default=None)
    identity_mismatch = local_link_state == "identity_mismatch" or (
        identity_consistent_value is not None and not _safe_bool(identity_consistent_value, True)
    )
    user_blocked = _safe_bool(user.get("user_blocked") or user.get("userBlocked"), False)
    access_blocked = _safe_bool(user.get("access_blocked") or user.get("accessBlocked") or user.get("blocked"), False)

    if auth_unavailable:
        return True, _safe_str(user.get("blocked_reason"), "auth_service_unavailable", 160), 503
    if identity_mismatch:
        return True, "identity_mismatch", 409
    if user_blocked:
        return True, _safe_str(user.get("blocked_reason"), "user_blocked", 160), 403
    if access_blocked:
        return True, _safe_str(user.get("blocked_reason"), "access_blocked", 160), _safe_int(user.get("denial_status_code"), 403, 400, 599)
    return False, "", 200


def _permission_value(access: Mapping[str, Any], permissions: Mapping[str, Any], keys: Sequence[str]) -> Optional[bool]:
    for key in keys:
        if key in access:
            return _safe_bool(access.get(key), False)
        if key in permissions:
            return _safe_bool(permissions.get(key), False)
    return None


def _bounded_permission(maximum: bool, explicit: Optional[bool], default: Optional[bool] = None) -> bool:
    if not maximum:
        return False
    if explicit is None:
        return maximum if default is None else bool(default and maximum)
    return bool(explicit and maximum)


def _build_access_contract(current_user: Mapping[str, Any], project_payload: Mapping[str, Any]) -> WorkspaceAccessContract:
    user = _safe_dict(current_user)
    payload = _safe_dict(project_payload)
    access = _project_access_payload(payload)
    permissions = _safe_dict(access.get("permissions") or access.get("permission_map") or access.get("permissionMap"))
    role = _project_role(payload)
    blocked, denial_code, denial_status = _is_blocked_context(user)
    public_viewer = _is_public_viewer_context(user, payload)
    demo_mode = _is_demo_mode(user, payload)
    authenticated = _safe_bool(user.get("authenticated") or user.get("is_authenticated") or user.get("isAuthenticated"), False)
    persistent = _safe_bool(user.get("persistent"), False) and authenticated and not blocked and not demo_mode and not public_viewer

    if blocked:
        return WorkspaceAccessContract(
            role=role,
            access_mode="auth_unavailable" if denial_status == 503 else "blocked",
            auth_unavailable=denial_status == 503,
            user_blocked=denial_code in {"user_blocked", "blocked", "banned"},
            access_blocked=True,
            identity_mismatch=denial_code == "identity_mismatch",
            denial_code=denial_code,
            denial_status_code=denial_status,
        )

    maximums = ROLE_MAXIMUMS.get(role, {})
    explicit_view = _permission_value(access, permissions, ("can_view", "canView", "view", "read"))
    explicit_edit = _permission_value(access, permissions, ("can_edit", "canEdit", "edit", "write"))
    explicit_manage = _permission_value(access, permissions, ("can_manage", "canManage", "manage", "admin"))
    explicit_delete = _permission_value(access, permissions, ("can_delete", "canDelete", "delete"))
    explicit_transfer = _permission_value(access, permissions, ("can_transfer", "canTransfer", "transfer"))
    explicit_command = _permission_value(access, permissions, ("can_command", "canCommand", "command", "commands"))
    explicit_materialize = _permission_value(access, permissions, ("can_materialize", "canMaterialize", "materialize"))

    # Without a project role, explicit view is accepted for already-authorized public/demo
    # contexts, but mutation is not inferred from a global account role.
    if role:
        can_view = _bounded_permission(bool(maximums.get("view")), explicit_view)
        can_edit = _bounded_permission(bool(maximums.get("edit")), explicit_edit)
        can_manage = _bounded_permission(bool(maximums.get("manage")), explicit_manage)
        can_delete = _bounded_permission(bool(maximums.get("delete")), explicit_delete)
        can_transfer = _bounded_permission(bool(maximums.get("transfer")), explicit_transfer)
        can_command = _bounded_permission(bool(maximums.get("command")), explicit_command)
        can_materialize = _bounded_permission(bool(maximums.get("materialize")), explicit_materialize)
    else:
        can_view = bool(explicit_view)
        can_edit = bool(explicit_edit and demo_mode)
        can_manage = False
        can_delete = False
        can_transfer = False
        can_command = bool(can_edit and explicit_command is not False)
        can_materialize = bool(can_edit and explicit_materialize is not False)

    explicit_read_only = _first_value(
        access.get("read_only"),
        access.get("readOnly"),
        access.get("readonly"),
        payload.get("read_only"),
        payload.get("readOnly"),
        default=None,
    )
    read_only = bool(not can_edit)
    if explicit_read_only is not None and _safe_bool(explicit_read_only, False):
        read_only = True

    if role == ROLE_VIEWER or public_viewer:
        can_view = True
        can_edit = can_manage = can_delete = can_transfer = can_command = can_materialize = False
        read_only = True

    if demo_mode:
        can_view = True
        can_manage = can_delete = can_transfer = False
        persistent = False
        read_only = not can_edit

    access_mode = "public" if public_viewer else "demo" if demo_mode else "authenticated" if authenticated else "anonymous"
    if not can_edit:
        can_command = False
        can_materialize = False

    return WorkspaceAccessContract(
        role=role,
        access_mode=access_mode,
        can_view=can_view,
        can_edit=can_edit and not read_only,
        can_manage=can_manage and not read_only,
        can_delete=can_delete and not read_only,
        can_transfer=can_transfer and not read_only,
        can_command=can_command and not read_only,
        can_materialize=can_materialize and not read_only,
        read_only=read_only,
        public_viewer=public_viewer,
        demo_mode=demo_mode,
        persistent=persistent,
        authenticated=authenticated,
        denial_status_code=200,
    )


# -----------------------------------------------------------------------------
# Chunk extraction and readiness
# -----------------------------------------------------------------------------

def _nested_candidates(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    payload_dict = _safe_dict(payload)
    for candidate in (
        payload_dict,
        _safe_dict(payload_dict.get("chunk")),
        _safe_dict(payload_dict.get("chunk_provisioning") or payload_dict.get("chunkProvisioning")),
    ):
        if candidate:
            result.append(candidate)

    for container_name in ("service_refs", "serviceRefs", "metadata", "metadata_json", "metadataJson", "settings"):
        container = _safe_dict(payload_dict.get(container_name))
        if not container:
            continue
        chunk = _safe_dict(container.get("chunk"))
        if chunk:
            result.append(chunk)
        provisioning = _safe_dict(container.get("chunkProvisioning") or container.get("chunk_provisioning"))
        if provisioning:
            result.append(provisioning)

    for link in _safe_list(payload_dict.get("service_links") or payload_dict.get("serviceLinks")):
        link_payload = _safe_dict(link)
        service = _safe_str(link_payload.get("service") or link_payload.get("service_name"), "", 80).lower()
        if service and service != "chunk":
            continue
        metadata = _safe_dict(link_payload.get("metadata"))
        reference = _safe_dict(link_payload.get("reference") or link_payload.get("resource_ref"))
        chunk = _safe_dict(link_payload.get("chunk") or metadata.get("chunk") or reference.get("chunk"))
        if chunk:
            result.append(chunk)
        resource_type = _safe_str(link_payload.get("resource_type") or link_payload.get("resourceType"), "", 80).lower()
        if resource_type in {"chunk_project", "project"}:
            result.append(
                {
                    "chunk_project_id": link_payload.get("resource_id") or link_payload.get("external_id"),
                    "chunk_universe_id": metadata.get("chunk_universe_id") or reference.get("external_universe_id"),
                    "chunk_world_id": metadata.get("chunk_world_id") or reference.get("external_world_id"),
                    "chunk_status": link_payload.get("status") or metadata.get("status"),
                }
            )
        elif resource_type in {"chunk_world", "world"}:
            result.append(
                {
                    "chunk_project_id": metadata.get("chunk_project_id") or reference.get("external_project_id"),
                    "chunk_universe_id": metadata.get("chunk_universe_id") or reference.get("external_universe_id"),
                    "chunk_world_id": link_payload.get("resource_id") or link_payload.get("external_id"),
                    "chunk_status": link_payload.get("status") or metadata.get("status"),
                }
            )
    return result


def _candidate_value(candidates: Sequence[Mapping[str, Any]], keys: Sequence[str], default: Any = None) -> Any:
    for candidate in candidates:
        value = _mapping_value(candidate, *keys, default=None)
        if value is not None and value != "":
            return value
    return default


def _chunk_context_from_project(project: Any = None, project_payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    payload = _project_payload_from_object(project, project_payload)
    direct: Dict[str, Any] = {}
    for name in (
        "chunk_project_id",
        "chunk_universe_id",
        "chunk_world_id",
        "chunk_status",
        "chunk_ready",
        "chunk_provisioning_status",
        "chunk_world_template_requested",
        "chunk_world_template_fallback",
        "chunk_world_template_effective",
        "chunk_fallback_used",
        "chunk_fallback_reason",
        "chunk_reference_fingerprint",
        "chunk_access_sync_status",
        "chunk_access_sync_error_code",
        "chunk_access_sync_error_message",
    ):
        value = _object_value(project, name, default=None)
        if value is not None and value != "":
            direct[name] = value
    candidates: list[Mapping[str, Any]] = [direct] if direct else []
    candidates.extend(_nested_candidates(payload))

    chunk_project_id = _safe_str(_candidate_value(candidates, ("chunk_project_id", "chunkProjectId"), ""), "", 240)
    chunk_universe_id = _safe_str(_candidate_value(candidates, ("chunk_universe_id", "chunkUniverseId"), ""), "", 240)
    chunk_world_id = _safe_str(_candidate_value(candidates, ("chunk_world_id", "chunkWorldId"), ""), "", 240)

    raw_chunk_status = _candidate_value(candidates, ("chunk_status", "chunkStatus", "status"), None)
    raw_provisioning = _candidate_value(
        candidates,
        ("chunk_provisioning_status", "chunkProvisioningStatus", "provisioning_status", "provisioningStatus"),
        None,
    )
    explicit_ready_value = _candidate_value(candidates, ("chunk_ready", "chunkReady", "ready"), None)

    chunk_status = _normalize_chunk_status(raw_chunk_status, "pending")
    provisioning_status = _normalize_provisioning_status(raw_provisioning, "pending")
    has_required_ids = bool(chunk_project_id and chunk_world_id)

    if provisioning_status in {"failed", "repair_required", "disabled", "pending", "provisioning"}:
        chunk_ready = False
    elif provisioning_status in {"ready", "fallback_ready"}:
        chunk_ready = has_required_ids
    elif chunk_status in {"error", "repair_required", "disabled", "pending"}:
        chunk_ready = False
    elif chunk_status == "ready":
        chunk_ready = has_required_ids
    else:
        chunk_ready = bool(has_required_ids and explicit_ready_value is not None and _safe_bool(explicit_ready_value, False))

    # Legacy data without an explicit provisioning field may infer ready only when
    # both IDs and an explicit ready/ready-status are present. IDs alone are not enough.
    legacy_inferred_ready = False
    if raw_provisioning in {None, ""} and has_required_ids:
        if _normalize_chunk_status(raw_chunk_status, "pending") == "ready" or _safe_bool(explicit_ready_value, False):
            chunk_ready = True
            provisioning_status = "ready"
            legacy_inferred_ready = True

    requested_template = _normalize_world_template(
        _candidate_value(candidates, ("chunk_world_template_requested", "chunkWorldTemplateRequested", "requested_world_template", "requestedWorldTemplate"), "earth"),
        "earth",
    )
    fallback_template = _normalize_world_template(
        _candidate_value(candidates, ("chunk_world_template_fallback", "chunkWorldTemplateFallback", "fallback_world_template", "fallbackWorldTemplate"), "flat"),
        "flat",
    )
    effective_template = _normalize_world_template(
        _candidate_value(candidates, ("chunk_world_template_effective", "chunkWorldTemplateEffective", "effective_world_template", "effectiveWorldTemplate"), ""),
        "",
    )
    fallback_used = _safe_bool(
        _candidate_value(candidates, ("chunk_fallback_used", "chunkFallbackUsed", "fallback_used", "fallbackUsed"), False),
        False,
    ) or bool(effective_template and requested_template and effective_template != requested_template)
    fallback_reason = _safe_str(
        _candidate_value(candidates, ("chunk_fallback_reason", "chunkFallbackReason", "fallback_reason", "fallbackReason"), ""),
        "",
        500,
    )
    reference_fingerprint = _safe_str(
        _candidate_value(candidates, ("chunk_reference_fingerprint", "chunkReferenceFingerprint", "reference_fingerprint", "referenceFingerprint"), ""),
        "",
        128,
    )

    access_sync_status = _normalize_access_sync_status(
        _candidate_value(candidates, ("chunk_access_sync_status", "chunkAccessSyncStatus", "access_sync_status", "accessSyncStatus"), "pending"),
        "pending",
    )
    access_sync_error_code = _safe_str(
        _candidate_value(candidates, ("chunk_access_sync_error_code", "chunkAccessSyncErrorCode", "access_sync_error_code", "accessSyncErrorCode"), ""),
        "",
        160,
    )

    if not has_required_ids and provisioning_status in {"ready", "fallback_ready"}:
        provisioning_status = "repair_required"
        chunk_ready = False

    if chunk_ready:
        effective_chunk_status = "ready"
    elif provisioning_status == "repair_required":
        effective_chunk_status = "repair_required"
    elif provisioning_status == "failed":
        effective_chunk_status = "error"
    elif provisioning_status == "disabled":
        effective_chunk_status = "disabled"
    elif provisioning_status in {"pending", "provisioning"}:
        effective_chunk_status = "pending"
    elif chunk_status == "ready":
        effective_chunk_status = "repair_required" if not has_required_ids else "pending"
    else:
        effective_chunk_status = chunk_status

    return {
        "chunk_project_id": chunk_project_id,
        "chunkProjectId": chunk_project_id,
        "chunk_universe_id": chunk_universe_id,
        "chunkUniverseId": chunk_universe_id,
        "chunk_world_id": chunk_world_id,
        "chunkWorldId": chunk_world_id,
        "chunk_status": effective_chunk_status,
        "chunkStatus": effective_chunk_status,
        "chunk_ready": chunk_ready,
        "chunkReady": chunk_ready,
        "chunk_provisioning_status": provisioning_status,
        "chunkProvisioningStatus": provisioning_status,
        "requested_world_template": requested_template,
        "requestedWorldTemplate": requested_template,
        "fallback_world_template": fallback_template,
        "fallbackWorldTemplate": fallback_template,
        "effective_world_template": effective_template,
        "effectiveWorldTemplate": effective_template,
        "fallback_used": fallback_used,
        "fallbackUsed": fallback_used,
        "fallback_reason": fallback_reason,
        "fallbackReason": fallback_reason,
        "reference_fingerprint": reference_fingerprint,
        "referenceFingerprint": reference_fingerprint,
        "chunk_access_sync_status": access_sync_status,
        "chunkAccessSyncStatus": access_sync_status,
        "chunk_access_sync_error_code": access_sync_error_code,
        "chunkAccessSyncErrorCode": access_sync_error_code,
        "legacy_inferred_ready": legacy_inferred_ready,
        "legacyInferredReady": legacy_inferred_ready,
    }


def _public_chunk_contract(chunk: Mapping[str, Any]) -> Dict[str, Any]:
    data = _safe_dict(chunk)
    allowed = (
        "chunk_project_id",
        "chunkProjectId",
        "chunk_universe_id",
        "chunkUniverseId",
        "chunk_world_id",
        "chunkWorldId",
        "chunk_status",
        "chunkStatus",
        "chunk_ready",
        "chunkReady",
        "chunk_provisioning_status",
        "chunkProvisioningStatus",
        "requested_world_template",
        "requestedWorldTemplate",
        "fallback_world_template",
        "fallbackWorldTemplate",
        "effective_world_template",
        "effectiveWorldTemplate",
        "fallback_used",
        "fallbackUsed",
        "fallback_reason",
        "fallbackReason",
        "reference_fingerprint",
        "referenceFingerprint",
        "chunk_access_sync_status",
        "chunkAccessSyncStatus",
        "legacy_inferred_ready",
        "legacyInferredReady",
    )
    return {key: data.get(key) for key in allowed if key in data}


def _access_sync_enabled() -> bool:
    return _config_bool(
        "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED",
        _config_bool("VECTOPLAN_CHUNK_ACCESS_SYNC_ENABLED", True),
    )


def _access_sync_required() -> bool:
    return _config_bool(
        "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED",
        _config_bool("VECTOPLAN_CHUNK_ACCESS_SYNC_REQUIRED", False),
    )


def _join_route_path(*parts: str) -> str:
    cleaned = [_safe_str(part, "", 1000).strip("/") for part in parts if _safe_str(part, "", 1000)]
    return "/" + "/".join(cleaned) if cleaned else "/"


def _build_chunk_route_hints(chunk: Mapping[str, Any], access: WorkspaceAccessContract) -> Dict[str, str]:
    chunk_project_id = _safe_str(chunk.get("chunk_project_id"), "", 240)
    chunk_universe_id = _safe_str(chunk.get("chunk_universe_id"), "", 240)
    chunk_world_id = _safe_str(chunk.get("chunk_world_id"), "", 240)
    if not chunk_project_id or not chunk_world_id:
        return {}

    api_base = _normalize_route(
        _config_str("VECTOPLAN_EDITOR_CHUNK_API_PREFIX", DEFAULT_CHUNK_BROWSER_BASE_URL, 4000)
        or _config_str("EDITOR_CHUNK_API_PREFIX", DEFAULT_CHUNK_BROWSER_BASE_URL, 4000),
        DEFAULT_CHUNK_BROWSER_BASE_URL,
    )
    project_base = _join_route_path(api_base, "projects", chunk_project_id)
    world_base = _join_route_path(project_base, "worlds", chunk_world_id)
    hints: Dict[str, str] = {
        "apiBaseUrl": api_base,
        "browserBaseUrl": api_base,
        "status": _join_route_path(api_base, "_status"),
        "placeableBlocks": _join_route_path(api_base, "placeable-blocks"),
        "projects": _join_route_path(api_base, "projects"),
        "project": project_base,
        "projectBootstrap": _join_route_path(project_base, "bootstrap"),
        "universes": _join_route_path(project_base, "universes"),
        "universe": _join_route_path(project_base, "universes", chunk_universe_id) if chunk_universe_id else "",
        "worlds": _join_route_path(project_base, "worlds"),
        "world": world_base,
        "blocks": _join_route_path(world_base, "blocks"),
        "chunk": _join_route_path(world_base, "chunks"),
        "chunks": _join_route_path(world_base, "chunks"),
        "chunksBatch": _join_route_path(world_base, "chunks", "batch"),
    }
    if access.can_command and not access.read_only:
        hints["commands"] = _join_route_path(world_base, "commands")

    allowed_keys = set(READ_ONLY_ROUTE_HINT_KEYS)
    if access.can_command and not access.read_only:
        allowed_keys.update(MUTATING_ROUTE_HINT_KEYS)
    return {
        key: value
        for key, value in hints.items()
        if key in allowed_keys and value and value.startswith("/") and not _looks_like_internal_url(value)
    }


def _chunk_hint_payload(chunk: Mapping[str, Any], access: WorkspaceAccessContract) -> Dict[str, Any]:
    data = _safe_dict(chunk)
    result: Dict[str, Any] = {
        "chunk_status": _safe_str(data.get("chunk_status"), "pending", 80),
        "chunk_ready": "1" if _safe_bool(data.get("chunk_ready"), False) else "0",
        "chunk_provisioning_status": _safe_str(data.get("chunk_provisioning_status"), "pending", 80),
        "chunk_access_sync_status": _safe_str(data.get("chunk_access_sync_status"), "pending", 80),
        "requested_world_template": _safe_str(data.get("requested_world_template"), "earth", 80),
        "fallback_world_template": _safe_str(data.get("fallback_world_template"), "flat", 80),
        "effective_world_template": _safe_str(data.get("effective_world_template"), "", 80),
        "chunk_fallback_used": "1" if _safe_bool(data.get("fallback_used"), False) else "0",
    }
    for source_key, target_key in (
        ("chunk_project_id", "chunk_project_id"),
        ("chunk_project_id", "project_id"),
        ("chunk_universe_id", "chunk_universe_id"),
        ("chunk_universe_id", "universe_id"),
        ("chunk_world_id", "chunk_world_id"),
        ("chunk_world_id", "world_id"),
        ("reference_fingerprint", "chunk_reference_fingerprint"),
    ):
        value = _safe_str(data.get(source_key), "", 240)
        if value:
            result[target_key] = value

    if _config_bool("VECTOPLAN_EMBED_INCLUDE_CHUNK_ROUTE_HINTS", False):
        hints = _build_chunk_route_hints(data, access)
        hints_json = _json_dumps_safe(hints)
        if hints_json:
            result["chunk_route_hints"] = hints_json
    return result


# -----------------------------------------------------------------------------
# Target configuration
# -----------------------------------------------------------------------------

def _target_cache_key(workspace: str, values: Mapping[str, Any]) -> str:
    return f"target:{workspace}:{_stable_fingerprint(values)}"


def _editor_target_config() -> WorkspaceTargetConfig:
    explicit_base = (
        _config_str("VECTOPLAN_EDITOR_PUBLIC_URL", "", 4000)
        or _config_str("VECTOPLAN_EDITOR_PUBLIC_BASE_URL", "", 4000)
    )
    raw = {
        "enabled": _config_bool("VECTOPLAN_EDITOR_EMBED_ENABLED", True),
        "base": explicit_base or DEFAULT_EDITOR_PUBLIC_URL,
        "base_explicit": bool(explicit_base),
        "route": _config_str("VECTOPLAN_EDITOR_EMBED_ROUTE", "", 2000)
        or _config_str("VECTOPLAN_EDITOR_ROUTE", "", 2000)
        or DEFAULT_EDITOR_ROUTE,
    }
    cache_key = _target_cache_key(WORKSPACE_EDITOR3D, raw)
    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []
    base = _normalize_public_base_url(raw["base"], "" if raw.get("base_explicit") else DEFAULT_EDITOR_PUBLIC_URL)
    route = _normalize_route(raw["route"], DEFAULT_EDITOR_ROUTE)
    target = _join_url(base, route)
    enabled = bool(raw["enabled"] and base and target)
    if not base:
        warnings.append("Editor public URL is invalid.")
    if not target:
        warnings.append("Editor public route URL could not be built.")
    result = WorkspaceTargetConfig(
        workspace=WORKSPACE_EDITOR3D,
        service_name="vectoplan-editor",
        enabled=enabled,
        public_base_url=base,
        route=route,
        public_route_url=target,
        source="VECTOPLAN_EDITOR_PUBLIC_URL",
        warnings=tuple(warnings),
    )
    _cache_set(cache_key, result)
    return result


def _map_target_config() -> WorkspaceTargetConfig:
    explicit_base = (
        _config_str("OPENLAYER_PUBLIC_URL", "", 4000)
        or _config_str("OPENLAYER_PUBLIC_BASE_URL", "", 4000)
    )
    raw = {
        "enabled": _config_bool("OPENLAYER_EMBED_ENABLED", True),
        "base": explicit_base or DEFAULT_OPENLAYER_PUBLIC_URL,
        "base_explicit": bool(explicit_base),
        "route": _config_str("OPENLAYER_ROUTE", DEFAULT_OPENLAYER_ROUTE, 2000),
    }
    cache_key = _target_cache_key(WORKSPACE_MAP, raw)
    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []
    base = _normalize_public_base_url(raw["base"], "" if raw.get("base_explicit") else DEFAULT_OPENLAYER_PUBLIC_URL)
    route = _normalize_route(raw["route"], DEFAULT_OPENLAYER_ROUTE)
    target = _join_url(base, route)
    enabled = bool(raw["enabled"] and base and target)
    if not base:
        warnings.append("OpenLayer public URL is invalid.")
    if not target:
        warnings.append("OpenLayer public route URL could not be built.")
    result = WorkspaceTargetConfig(
        workspace=WORKSPACE_MAP,
        service_name="vectoplan-openlayer",
        enabled=enabled,
        public_base_url=base,
        route=route,
        public_route_url=target,
        source="OPENLAYER_PUBLIC_URL",
        warnings=tuple(warnings),
    )
    _cache_set(cache_key, result)
    return result


def _cad2d_target_config() -> WorkspaceTargetConfig:
    explicit_base = (
        _config_str("VECTOPLAN_CAD_PUBLIC_URL", "", 4000)
        or _config_str("VECTOPLAN_CAD_PUBLIC_BASE_URL", "", 4000)
        or _config_str("CAD_PUBLIC_URL", "", 4000)
    )
    raw = {
        "enabled": _config_bool("VECTOPLAN_CAD_EMBED_ENABLED", True),
        "base": explicit_base or DEFAULT_CAD_PUBLIC_URL,
        "base_explicit": bool(explicit_base),
        "route": _config_str("VECTOPLAN_CAD_ROUTE", "", 2000)
        or _config_str("VECTOPLAN_CAD_EMBED_ROUTE", "", 2000)
        or DEFAULT_CAD_ROUTE,
    }
    cache_key = _target_cache_key(WORKSPACE_CAD2D, raw)
    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []
    base = _normalize_public_base_url(
        raw["base"],
        "" if raw.get("base_explicit") else DEFAULT_CAD_PUBLIC_URL,
    )
    route = _normalize_route(raw["route"], DEFAULT_CAD_ROUTE)
    target = _join_url(base, route)
    enabled = bool(raw["enabled"] and base and target)
    if not base:
        warnings.append("CAD public URL is invalid.")
    if not target:
        warnings.append("CAD public route URL could not be built.")
    result = WorkspaceTargetConfig(
        workspace=WORKSPACE_CAD2D,
        service_name="vectoplan-cad",
        enabled=enabled,
        public_base_url=base,
        route=route,
        public_route_url=target,
        source="VECTOPLAN_CAD_PUBLIC_URL",
        warnings=tuple(warnings),
    )
    _cache_set(cache_key, result)
    return result


def _lv_target_config() -> WorkspaceTargetConfig:
    explicit_base = (
        _config_str("VECTOPLAN_LV_PUBLIC_URL", "", 4000)
        or _config_str("VECTOPLAN_LV_PUBLIC_BASE_URL", "", 4000)
        or _config_str("LV_PUBLIC_URL", "", 4000)
    )
    raw = {
        "enabled": _config_bool("VECTOPLAN_LV_EMBED_ENABLED", True),
        "base": explicit_base or DEFAULT_LV_PUBLIC_URL,
        "base_explicit": bool(explicit_base),
        "route": _config_str("VECTOPLAN_LV_ROUTE", "", 2000)
        or _config_str("VECTOPLAN_LV_EMBED_ROUTE", "", 2000)
        or _config_str("LV_ROUTE", "", 2000)
        or DEFAULT_LV_ROUTE,
    }
    cache_key = _target_cache_key(WORKSPACE_LV, raw)
    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []
    base = _normalize_public_base_url(
        raw["base"],
        "" if raw.get("base_explicit") else DEFAULT_LV_PUBLIC_URL,
    )
    route = _normalize_route(raw["route"], DEFAULT_LV_ROUTE)
    target = _join_url(base, route)
    enabled = bool(raw["enabled"] and base and target)
    if not base:
        warnings.append("LV public URL is invalid.")
    if not target:
        warnings.append("LV public route URL could not be built.")
    result = WorkspaceTargetConfig(
        workspace=WORKSPACE_LV,
        service_name="vectoplan-lv",
        enabled=enabled,
        public_base_url=base,
        route=route,
        public_route_url=target,
        source="VECTOPLAN_LV_PUBLIC_URL",
        warnings=tuple(warnings),
    )
    _cache_set(cache_key, result)
    return result


def get_workspace_target_config(workspace: Any) -> WorkspaceTargetConfig:
    normalized = normalize_workspace(workspace)
    if normalized == WORKSPACE_EDITOR3D:
        return _editor_target_config()
    if normalized == WORKSPACE_MAP:
        return _map_target_config()
    if normalized == WORKSPACE_CAD2D:
        return _cad2d_target_config()
    if normalized == WORKSPACE_LV:
        return _lv_target_config()
    return WorkspaceTargetConfig(
        workspace=normalized,
        service_name="vectoplan-app",
        enabled=False,
        public_base_url="",
        route="",
        public_route_url="",
        source="not_external",
        warnings=(f"Workspace {normalized!r} is not an external embed workspace.",),
    )


def is_external_workspace(workspace: Any) -> bool:
    return normalize_workspace(workspace) in EXTERNAL_WORKSPACES


# -----------------------------------------------------------------------------
# Embed gate and parameters
# -----------------------------------------------------------------------------

def _public_editor_embed_verified(project_payload: Mapping[str, Any]) -> bool:
    if not _config_bool("VECTOPLAN_EDITOR_PUBLIC_VIEWER_EMBED_ENABLED", True):
        return False
    payload = _safe_dict(project_payload)
    publication = _safe_dict(payload.get("publication"))
    visibility = _safe_str(
        _first_value(
            payload.get("visibility"),
            publication.get("visibility"),
            default="private",
        ),
        "private",
        40,
    ).lower()
    published = _safe_dict(
        publication.get("effective_published_workspaces")
        or publication.get("effectivePublishedWorkspaces")
        or payload.get("effective_published_workspaces")
        or payload.get("effectivePublishedWorkspaces")
        or publication.get("published_workspaces")
        or publication.get("publishedWorkspaces")
        or payload.get("published_workspaces")
        or payload.get("publishedWorkspaces")
    )
    editor_published = _safe_bool(
        _first_value(
            published.get(WORKSPACE_EDITOR3D),
            publication.get(WORKSPACE_EDITOR3D),
            default=False,
        ),
        False,
    )
    return bool(visibility in {"public", "unlisted"} and editor_published)


def _workspace_gate(
    workspace: str,
    access: WorkspaceAccessContract,
    chunk: Mapping[str, Any],
    project_payload: Mapping[str, Any],
) -> tuple[bool, str, int, str]:
    if access.access_blocked:
        return False, access.denial_code or "access_blocked", access.denial_status_code or 403, "Workspace access is blocked."
    if not access.authenticated and not access.public_viewer and not access.demo_mode:
        return False, "authentication_required", 401, "Authentication or an explicit public viewer context is required."
    if not access.can_view:
        return False, "project_view_permission_required", 403, "Project view permission is required."
    if access.public_viewer and workspace == WORKSPACE_EDITOR3D and not _public_editor_embed_verified(project_payload):
        return False, "public_editor_embed_not_verified", 403, "Public 3D embed requires a verified read-only context."
    if workspace not in {WORKSPACE_EDITOR3D, WORKSPACE_CAD2D}:
        return True, "ok", 200, "Workspace is ready."

    provisioning = _safe_str(chunk.get("chunk_provisioning_status"), "pending", 80)
    chunk_status = _safe_str(chunk.get("chunk_status"), "pending", 80)
    if provisioning in {"failed", "repair_required"} or chunk_status in {"error", "repair_required"}:
        return False, "chunk_provisioning_repair_required", 503, "Chunk provisioning requires repair."
    if provisioning == "disabled" or chunk_status == "disabled":
        return False, "chunk_provisioning_disabled", 503, "Chunk provisioning is disabled."
    if not _safe_bool(chunk.get("chunk_ready"), False):
        return False, "chunk_not_ready", 409, "Chunk project/world is not ready."
    if not _safe_str(chunk.get("chunk_project_id"), "", 240) or not _safe_str(chunk.get("chunk_world_id"), "", 240):
        return False, "chunk_references_incomplete", 503, "Chunk project/world references are incomplete."

    if workspace == WORKSPACE_CAD2D:
        return True, "ok", 200, "Workspace is ready."

    sync_status = _safe_str(chunk.get("chunk_access_sync_status"), "pending", 80)
    # When synchronization is enabled and the user has a direct project role,
    # opening Editor before projection is unsafe even if 'required' is soft for creation.
    enforce_sync = _access_sync_required() or (_access_sync_enabled() and bool(access.role) and not access.demo_mode)
    if enforce_sync and sync_status not in {"ready", "disabled"}:
        if sync_status in {"failed", "repair_required"}:
            return False, "chunk_access_sync_repair_required", 503, "Chunk access synchronization requires repair."
        return False, "chunk_access_sync_pending", 409, "Chunk access synchronization is pending."
    if _access_sync_required() and sync_status == "disabled":
        return False, "chunk_access_sync_disabled", 503, "Required Chunk access synchronization is disabled."
    return True, "ok", 200, "Workspace is ready."


def _context_path(project_public_id: str) -> str:
    template = _config_str("VECTOPLAN_PROJECT_CONTEXT_PATH_TEMPLATE", DEFAULT_CONTEXT_PATH_TEMPLATE, 4000)
    try:
        return _normalize_route(template.format(project_public_id=_safe_quote(project_public_id)), DEFAULT_CONTEXT_PATH_TEMPLATE)
    except Exception:
        return DEFAULT_CONTEXT_PATH_TEMPLATE.format(project_public_id=_safe_quote(project_public_id))


def _return_path(project_public_id: str) -> str:
    template = _config_str("VECTOPLAN_PROJECT_RETURN_PATH_TEMPLATE", DEFAULT_RETURN_PATH_TEMPLATE, 4000)
    try:
        return _normalize_route(template.format(project_public_id=_safe_quote(project_public_id)), DEFAULT_RETURN_PATH_TEMPLATE)
    except Exception:
        return DEFAULT_RETURN_PATH_TEMPLATE.format(project_public_id=_safe_quote(project_public_id))


def _base_embed_params(
    *,
    workspace: str,
    project: Any,
    project_payload: Mapping[str, Any],
    access: WorkspaceAccessContract,
    chunk: Mapping[str, Any],
    current_user: Mapping[str, Any],
    request_obj: Any,
    include_context: bool,
    include_return_url: bool,
    include_chunk_hints: Optional[bool],
    prefer_request_host: Optional[bool],
) -> Dict[str, Any]:
    project_public_id = _project_public_id(project, project_payload)
    params: Dict[str, Any] = {
        "embed": "1",
        "embed_contract_version": EMBED_CONTRACT_VERSION,
        "source": "vectoplan-app",
        "workspace": workspace,
        "app_project_public_id": project_public_id,
        "project_public_id": project_public_id,
        "project_role": access.role,
        "access_mode": access.access_mode,
        "public_viewer": "1" if access.public_viewer else "0",
        "is_public_viewer": "1" if access.public_viewer else "0",
        "read_only": "1" if access.read_only else "0",
        "readonly": "1" if access.read_only else "0",
        "can_edit": "1" if access.can_edit else "0",
        "can_manage": "1" if access.can_manage else "0",
        "can_command": "1" if access.can_command else "0",
        "can_materialize": "1" if access.can_materialize else "0",
        "persistent": "1" if access.persistent else "0",
    }

    if include_context and project_public_id:
        params["context_url"] = _absolute_app_url(_context_path(project_public_id), request_obj, prefer_request_host)
    if include_return_url and project_public_id:
        params["return_url"] = _absolute_app_url(_return_path(project_public_id), request_obj, prefer_request_host)

    if workspace == WORKSPACE_MAP:
        params.update(_map_project_view_params(project, project_payload))

    app_public_url = _app_public_base_url(request_obj, prefer_request_host)
    if app_public_url:
        params["app_public_url"] = app_public_url

    if access.demo_mode:
        params["demo_mode"] = "1"
        params["ephemeral"] = "1"

    if include_chunk_hints is None:
        include_chunk_hints = _config_bool("VECTOPLAN_EMBED_INCLUDE_CHUNK_QUERY_PARAMS", True)
    if include_chunk_hints and workspace == WORKSPACE_EDITOR3D:
        params.update(_chunk_hint_payload(chunk, access))
        from services.editor_access_ticket import mint_editor_access_ticket

        params["vp_access_ticket"] = mint_editor_access_ticket(
            {
                "workspace": WORKSPACE_EDITOR3D,
                "app_project_id": project_public_id,
                "chunk_project_id": _safe_str(chunk.get("chunk_project_id"), "", 240),
                "world_id": _safe_str(chunk.get("chunk_world_id"), "", 240),
                "universe_id": _safe_str(chunk.get("chunk_universe_id"), "", 240),
                "auth_user_id": _canonical_auth_user_id(current_user),
                "auth_username": _canonical_auth_username(current_user),
                "role": access.role or (ROLE_VIEWER if access.public_viewer else ""),
                "public": bool(access.public_viewer),
                "demo": bool(access.demo_mode),
                "read_only": bool(access.read_only),
                "can_view": bool(access.can_view),
                "can_edit": bool(access.can_edit),
                "can_manage": bool(access.can_manage),
                "can_command": bool(access.can_command),
                "can_materialize": bool(access.can_materialize),
            }
        )

    return _clean_query_params(params, allow_security_controls=True)


# -----------------------------------------------------------------------------
# Public builders
# -----------------------------------------------------------------------------

def build_workspace_embed_result(
    workspace: Any,
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    include_context: bool = True,
    include_return_url: bool = True,
    include_chunk_hints: Optional[bool] = None,
    prefer_request_host: Optional[bool] = None,
) -> WorkspaceEmbedResult:
    normalized_workspace = normalize_workspace(workspace)
    payload = _project_payload_from_object(project, project_payload)
    user = _current_user_payload(current_user)
    access = _build_access_contract(user, payload)
    chunk = _chunk_context_from_project(project, payload)
    project_public_id = _project_public_id(project, payload)

    def failure(code: str, status: int, message: str, *, target: Optional[WorkspaceTargetConfig] = None) -> WorkspaceEmbedResult:
        return WorkspaceEmbedResult(
            ok=False,
            workspace=normalized_workspace,
            code=code,
            message=message,
            project_public_id=project_public_id,
            app_project_public_id=project_public_id,
            public_base_url=target.public_base_url if target else "",
            route=target.route if target else "",
            target_url=target.public_route_url if target else "",
            warnings=list(target.warnings) if target else [],
            error=code,
            status_code=status,
            read_only=True,
            project_role=access.role,
            access_mode=access.access_mode,
            can_edit=False,
            can_command=False,
            can_materialize=False,
            chunk=_public_chunk_contract(chunk),
        )

    try:
        if normalized_workspace in FORBIDDEN_EXTERNAL_WORKSPACES:
            return failure("workspace_forbidden", 403, "This workspace may not be embedded externally.")
        if normalized_workspace not in EXTERNAL_WORKSPACES:
            return failure("workspace_not_external", 400, "This workspace is not provided by an external embed service.")
        if _is_new_project_id(project_public_id):
            return failure("project_public_id_missing", 409, "The project must be stored before an external workspace can open.")

        target = get_workspace_target_config(normalized_workspace)
        if not target.enabled:
            return failure("embed_disabled", 503, f"Embed for workspace {normalized_workspace!r} is disabled.", target=target)
        if not target.public_route_url:
            return failure("target_url_missing", 503, "The public embed target could not be built.", target=target)

        allowed, gate_code, gate_status, gate_message = _workspace_gate(normalized_workspace, access, chunk, payload)
        if not allowed:
            return failure(gate_code, gate_status, gate_message, target=target)

        if normalized_workspace == WORKSPACE_CAD2D:
            core_project_id = _core_project_id(project, payload)
            if not core_project_id:
                return failure(
                    "core_project_not_ready",
                    409,
                    "The App project has no ready Core project reference.",
                    target=target,
                )
            # CAD receives one stable project reference. Chunk coordinates and
            # internal service URLs remain owned by Core and never enter the URL.
            params = _base_embed_params(
                workspace=normalized_workspace,
                project=project,
                project_payload=payload,
                access=access,
                chunk=chunk,
                current_user=user,
                request_obj=request_obj,
                include_context=include_context,
                include_return_url=include_return_url,
                include_chunk_hints=False,
                prefer_request_host=prefer_request_host,
            )
            params["core_project_id"] = core_project_id
            params = _clean_query_params(params, allow_security_controls=True)
        elif normalized_workspace == WORKSPACE_LV:
            # The initial LV integration deliberately stays service-owned and
            # database-agnostic. Only the public App project key scopes the shell.
            params = {"project_public_id": project_public_id}
        else:
            base_params = _base_embed_params(
                workspace=normalized_workspace,
                project=project,
                project_payload=payload,
                access=access,
                chunk=chunk,
                request_obj=request_obj,
                include_context=include_context,
                current_user=user,
                include_return_url=include_return_url,
                include_chunk_hints=include_chunk_hints,
                prefer_request_host=prefer_request_host,
            )
            extras = _clean_extra_query_params(extra_params or {})
            params = dict(extras)
            params.update(base_params)  # Security contract overwrites every external value.
            params = _clean_query_params(params, allow_security_controls=True)
        request_target_url = _match_loopback_target_host(
            target.public_route_url,
            request_obj,
        )
        url = _append_query_params(request_target_url, params)
        if not url:
            return failure("url_build_failed", 500, "The embed URL could not be built.", target=target)

        warnings = list(target.warnings)
        if _safe_bool(chunk.get("legacy_inferred_ready"), False):
            warnings.append("Chunk readiness was inferred from a legacy payload.")
        if _safe_bool(chunk.get("fallback_used"), False):
            warnings.append("The Chunk world uses the configured fallback template.")

        return WorkspaceEmbedResult(
            ok=True,
            workspace=normalized_workspace,
            url=url,
            target_url=request_target_url,
            public_base_url=target.public_base_url,
            route=target.route,
            code="ok",
            message="Embed URL built.",
            project_public_id=project_public_id,
            app_project_public_id=project_public_id,
            params=params,
            warnings=warnings,
            error=None,
            uses_public_url=True,
            status_code=200,
            read_only=access.read_only,
            project_role=access.role,
            access_mode=access.access_mode,
            can_edit=access.can_edit,
            can_command=access.can_command,
            can_materialize=access.can_materialize,
            chunk=_public_chunk_contract(chunk),
        )
    except Exception as exc:
        _log_exception("build_workspace_embed_result failed", exc)
        return failure("embed_url_build_exception", 500, "The embed URL could not be built because of an internal error.")


def build_workspace_embed_url(
    workspace: Any,
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    include_context: bool = True,
    include_return_url: bool = True,
    include_chunk_hints: Optional[bool] = None,
    prefer_request_host: Optional[bool] = None,
    fallback: str = "",
) -> str:
    try:
        result = build_workspace_embed_result(
            workspace,
            project=project,
            project_payload=project_payload,
            current_user=current_user,
            request_obj=request_obj,
            extra_params=extra_params,
            include_context=include_context,
            include_return_url=include_return_url,
            include_chunk_hints=include_chunk_hints,
            prefer_request_host=prefer_request_host,
        )
        return result.url if result.ok else fallback
    except Exception:
        return fallback


def build_editor3d_embed_result(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    include_context: bool = True,
    include_return_url: bool = True,
    include_chunk_hints: Optional[bool] = None,
    prefer_request_host: Optional[bool] = None,
) -> WorkspaceEmbedResult:
    return build_workspace_embed_result(
        WORKSPACE_EDITOR3D,
        project=project,
        project_payload=project_payload,
        current_user=current_user,
        request_obj=request_obj,
        extra_params=extra_params,
        include_context=include_context,
        include_return_url=include_return_url,
        include_chunk_hints=include_chunk_hints,
        prefer_request_host=prefer_request_host,
    )


def build_editor3d_embed_url(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    include_context: bool = True,
    include_return_url: bool = True,
    include_chunk_hints: Optional[bool] = None,
    prefer_request_host: Optional[bool] = None,
    fallback: str = "",
) -> str:
    return build_workspace_embed_url(
        WORKSPACE_EDITOR3D,
        project=project,
        project_payload=project_payload,
        current_user=current_user,
        request_obj=request_obj,
        extra_params=extra_params,
        include_context=include_context,
        include_return_url=include_return_url,
        include_chunk_hints=include_chunk_hints,
        prefer_request_host=prefer_request_host,
        fallback=fallback,
    )


def build_map_embed_result(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    extra_params: Optional[Mapping[str, Any]] = None,
    include_context: bool = True,
    include_return_url: bool = True,
    prefer_request_host: Optional[bool] = None,
) -> WorkspaceEmbedResult:
    return build_workspace_embed_result(
        WORKSPACE_MAP,
        project=project,
        project_payload=project_payload,
        current_user=current_user,
        request_obj=request_obj,
        extra_params=extra_params,
        include_context=include_context,
        include_return_url=include_return_url,
        include_chunk_hints=False,
        prefer_request_host=prefer_request_host,
    )


# -----------------------------------------------------------------------------
# Status
# -----------------------------------------------------------------------------

def get_workspace_embed_status() -> Dict[str, Any]:
    try:
        editor = get_workspace_target_config(WORKSPACE_EDITOR3D)
        map_target = get_workspace_target_config(WORKSPACE_MAP)
        cad2d = get_workspace_target_config(WORKSPACE_CAD2D)
        lv = get_workspace_target_config(WORKSPACE_LV)
        with _CACHE_LOCK:
            cache_size = len(_MODULE_CACHE)
        return {
            "ok": True,
            "service": "workspace_embed_service",
            "phase": "chunk-provisioning-and-project-role-safe-embed",
            "contract_version": EMBED_CONTRACT_VERSION,
            "cache": {
                "ttl_seconds": _cache_max_age_seconds(),
                "max_items": _cache_max_items(),
                "size": cache_size,
                "keys_exposed": False,
                "thread_safe": True,
                "defensive_copies": True,
            },
            "targets": {
                WORKSPACE_EDITOR3D: editor.to_dict(),
                WORKSPACE_MAP: map_target.to_dict(),
                WORKSPACE_CAD2D: cad2d.to_dict(),
                WORKSPACE_LV: lv.to_dict(),
            },
            "external_workspaces": sorted(EXTERNAL_WORKSPACES),
            "forbidden_external_workspaces": sorted(FORBIDDEN_EXTERNAL_WORKSPACES),
            "chunk": {
                "default_requested_world_template": "earth",
                "default_fallback_world_template": "flat",
                "access_sync_enabled": _access_sync_enabled(),
                "access_sync_required": _access_sync_required(),
                "missing_world_is_invented": False,
                "ids_alone_force_ready": False,
            },
            "rules": {
                "browser_uses_public_url": True,
                "internal_urls_exposed": False,
                "tokens_exposed": False,
                "auth_identity_exposed": False,
                "local_database_ids_exposed": False,
                "blocked_gets_embed_url": False,
                "identity_mismatch_gets_embed_url": False,
                "demo_is_ephemeral": True,
                "viewer_forces_read_only": True,
                "public_viewer_forces_read_only": True,
                "public_editor_requires_verified_readonly_context": True,
                "global_account_role_is_project_role": False,
                "extra_params_can_elevate_access": False,
                "cad_project_data_forwarded": False,
                "lv_only_public_project_id_forwarded": True,
                "admin_is_never_external_embed": True,
                "editor_project_id_is_chunk_project_id": True,
                "editor_world_id_is_chunk_world_id": True,
                "chunk_ids_force_ready": False,
                "chunk_world_default_invented": False,
                "chunk_access_sync_gates_editor": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "service": "workspace_embed_service",
            "error": {"type": exc.__class__.__name__, "code": "workspace_embed_status_failed"},
        }


# Compatibility aliases.
build_3d_embed_result = build_editor3d_embed_result
build_3d_embed_url = build_editor3d_embed_url
build_editor_embed_result = build_editor3d_embed_result
build_editor_embed_url = build_editor3d_embed_url


__all__ = [
    "WORKSPACE_PROJECT",
    "WORKSPACE_MAP",
    "WORKSPACE_EDITOR3D",
    "WORKSPACE_CAD2D",
    "WORKSPACE_LV",
    "WORKSPACE_VERSIONS",
    "WORKSPACE_ADMIN",
    "WorkspaceTargetConfig",
    "WorkspaceAccessContract",
    "WorkspaceEmbedResult",
    "normalize_workspace",
    "clear_workspace_embed_cache",
    "get_workspace_target_config",
    "get_workspace_embed_status",
    "is_external_workspace",
    "build_workspace_embed_result",
    "build_workspace_embed_url",
    "build_editor3d_embed_result",
    "build_editor3d_embed_url",
    "build_3d_embed_result",
    "build_3d_embed_url",
    "build_editor_embed_result",
    "build_editor_embed_url",
    "build_map_embed_result",
]
