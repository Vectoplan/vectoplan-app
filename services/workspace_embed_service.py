# services/vectoplan-app/services/workspace_embed_service.py
from __future__ import annotations

"""
VECTOPLAN workspace embed service.

Zweck:
- Baut browserfähige Embed-/Redirect-URLs für externe Workspace-Services.
- Zentrale Gateway-Logik für routes.viewer.
- Der Browser bekommt immer PUBLIC_URL-Ziele.
- INTERNAL_URL-Ziele bleiben ausschließlich für Server-zu-Server-Kommunikation.
- Aktuell wichtigster Workspace: editor3d → vectoplan-editor.
- Später erweiterbar für Map/OpenLayer, 2D, LV, Versionen.

Sicherheitsregeln:
- Keine Tokens, Secrets oder INTERNAL_URLs an den Browser geben.
- App-interne Rechteprüfung bleibt in routes.viewer / project_permissions.
- Diese Datei baut nur Ziel-URLs nach erfolgreicher Zugriffskontrolle.
- Admin/System/Settings/Team werden nicht als externe Public-Embeds gebaut.
"""

import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

try:
    from flask import current_app, request
except Exception:  # pragma: no cover
    current_app = None  # type: ignore
    request = None  # type: ignore


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

WORKSPACE_PROJECT = "project"
WORKSPACE_MAP = "map"
WORKSPACE_EDITOR3D = "editor3d"
WORKSPACE_CAD2D = "cad2d"
WORKSPACE_LV = "lv"
WORKSPACE_VERSIONS = "versions"
WORKSPACE_ADMIN = "admin"

EXTERNAL_WORKSPACES = {
    WORKSPACE_EDITOR3D,
    WORKSPACE_MAP,
}

FORBIDDEN_EXTERNAL_WORKSPACES = {
    WORKSPACE_ADMIN,
    "settings",
    "team",
    "permissions",
    "system",
}

DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"

DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"
DEFAULT_OPENLAYER_ROUTE = "/map"

DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"

DEFAULT_CONTEXT_PATH_TEMPLATE = "/ui/project/{project_public_id}/context.json"
DEFAULT_RETURN_PATH_TEMPLATE = "/project={project_public_id}"

_CACHE_MAX_AGE_SECONDS = 15

_MODULE_CACHE: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

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
            "enabled": self.enabled,
            "public_base_url": self.public_base_url,
            "route": self.route,
            "public_route_url": self.public_route_url,
            "source": self.source,
            "warnings": list(self.warnings),
            "uses_public_url": True,
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

    def __bool__(self) -> bool:
        return bool(self.ok and self.url)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "workspace": self.workspace,
            "url": self.url,
            "target_url": self.target_url,
            "public_base_url": self.public_base_url,
            "route": self.route,
            "code": self.code,
            "message": self.message,
            "project_public_id": self.project_public_id,
            "app_project_public_id": self.app_project_public_id,
            "params": dict(self.params or {}),
            "warnings": list(self.warnings or []),
            "error": self.error,
            "uses_public_url": self.uses_public_url,
        }


# ─────────────────────────────────────────────────────────────
# Generic safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        text = _safe_str(value, "", 80).lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled"}:
            return False

        return default

    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, dict):
            return dict(value)

        if isinstance(value, Mapping):
            return dict(value)

        if hasattr(value, "to_dict") and callable(value.to_dict):
            data = value.to_dict()
            return dict(data) if isinstance(data, Mapping) else {}

        return {}

    except Exception:
        return {}


def _safe_list(value: Any) -> list[Any]:
    try:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        return []
    except Exception:
        return []


def _safe_quote(value: Any) -> str:
    try:
        return quote(_safe_str(value, "", 1000), safe="")
    except Exception:
        return ""


def _now() -> float:
    try:
        return time.monotonic()
    except Exception:
        return time.time()


def _log_warning(message: str, *args: Any) -> None:
    try:
        if current_app is not None:
            current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        if current_app is not None:
            if exc is None:
                current_app.logger.exception(message)
            else:
                current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Cached normalization helpers
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
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
        }

        return aliases.get(text, default)

    except Exception:
        return default


@lru_cache(maxsize=512)
def _normalize_base_url_cached(value: str, default: str = "") -> str:
    try:
        text = _safe_str(value, default, 4000)
        if not text:
            return default.rstrip("/")

        return text.rstrip("/")

    except Exception:
        return default.rstrip("/")


@lru_cache(maxsize=512)
def _normalize_route_cached(value: str, default: str = "/") -> str:
    try:
        text = _safe_str(value, default, 2000)

        if not text:
            text = default

        if text.startswith("http://") or text.startswith("https://"):
            return text

        if not text.startswith("/"):
            text = "/" + text

        return text

    except Exception:
        return default if default.startswith("/") else "/" + default


@lru_cache(maxsize=1024)
def _join_url_cached(base_url: str, route: str) -> str:
    try:
        route_text = _normalize_route_cached(route, "/")

        if route_text.startswith("http://") or route_text.startswith("https://"):
            return route_text

        base = _normalize_base_url_cached(base_url, "")

        if not base:
            return route_text

        return base.rstrip("/") + "/" + route_text.lstrip("/")

    except Exception:
        return ""


@lru_cache(maxsize=512)
def _is_absolute_url_cached(value: str) -> bool:
    try:
        parts = urlsplit(_safe_str(value, "", 4000))
        return parts.scheme in {"http", "https"} and bool(parts.netloc)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────

def _config_get(key: str, default: Any = None) -> Any:
    try:
        if current_app is not None:
            return current_app.config.get(key, os.environ.get(key, default))
    except Exception:
        pass

    try:
        return os.environ.get(key, default)
    except Exception:
        return default


def _config_str(key: str, default: str = "", max_len: int = 4000) -> str:
    try:
        return _safe_str(_config_get(key, default), default, max_len)
    except Exception:
        return default


def _config_bool(key: str, default: bool = False) -> bool:
    try:
        return _safe_bool(_config_get(key, default), default)
    except Exception:
        return default


def _cache_get(name: str) -> Optional[Any]:
    try:
        item = _MODULE_CACHE.get(name)

        if not item:
            return None

        ts = float(item.get("ts") or 0)
        if _now() - ts > _CACHE_MAX_AGE_SECONDS:
            _MODULE_CACHE.pop(name, None)
            return None

        return item.get("value")

    except Exception:
        return None


def _cache_set(name: str, value: Any) -> Any:
    try:
        _MODULE_CACHE[name] = {
            "ts": _now(),
            "value": value,
        }
    except Exception:
        pass

    return value


def clear_workspace_embed_cache() -> None:
    try:
        _MODULE_CACHE.clear()
    except Exception:
        pass

    try:
        normalize_workspace.cache_clear()
    except Exception:
        pass

    try:
        _normalize_base_url_cached.cache_clear()
    except Exception:
        pass

    try:
        _normalize_route_cached.cache_clear()
    except Exception:
        pass

    try:
        _join_url_cached.cache_clear()
    except Exception:
        pass

    try:
        _is_absolute_url_cached.cache_clear()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Project extraction helpers
# ─────────────────────────────────────────────────────────────

def _mapping_value(mapping: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    try:
        for key in keys:
            if key in mapping and mapping.get(key) is not None:
                return mapping.get(key)

        return default

    except Exception:
        return default


def _object_value(obj: Any, *keys: str, default: Any = "") -> Any:
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


def _project_value(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    keys: tuple[str, ...],
    default: Any = "",
) -> Any:
    try:
        payload = _safe_dict(project_payload)

        if payload:
            value = _mapping_value(payload, *keys, default=None)
            if value is not None and value != "":
                return value

        return _object_value(project, *keys, default=default)

    except Exception:
        return default


def _nested_payload_value(
    payload: Mapping[str, Any],
    section: str,
    *keys: str,
    default: Any = "",
) -> Any:
    try:
        nested = _safe_dict(payload.get(section))
        if not nested:
            return default

        return _mapping_value(nested, *keys, default=default)

    except Exception:
        return default


def _project_public_id(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
) -> str:
    try:
        value = _project_value(
            project=project,
            project_payload=project_payload,
            keys=(
                "public_id",
                "publicId",
                "project_public_id",
                "projectPublicId",
                "app_project_public_id",
                "appProjectPublicId",
            ),
            default="",
        )

        text = _safe_str(value, "", 160)
        return "" if text.lower() in {"", "none", "null"} else text

    except Exception:
        return ""


def _project_internal_id(
    *,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
) -> str:
    try:
        value = _project_value(
            project=project,
            project_payload=project_payload,
            keys=("id", "project_id", "projectId"),
            default="",
        )
        return _safe_str(value, "", 160)
    except Exception:
        return ""


def _is_new_project_id(project_public_id: Any) -> bool:
    try:
        text = _safe_str(project_public_id, "", 160).lower()
        return text in {"", "new", "create", "neu", "none", "null"}
    except Exception:
        return True


def _project_access_payload(project_payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    try:
        payload = _safe_dict(project_payload)
        return _safe_dict(payload.get("access"))
    except Exception:
        return {}


def _current_user_payload(current_user: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    try:
        return _safe_dict(current_user)
    except Exception:
        return {}


def _is_demo_mode(
    *,
    current_user: Optional[Mapping[str, Any]] = None,
    project_payload: Optional[Mapping[str, Any]] = None,
) -> bool:
    try:
        user = _current_user_payload(current_user)
        payload = _safe_dict(project_payload)

        return _safe_bool(
            user.get("demo_mode")
            or user.get("demoMode")
            or user.get("is_demo")
            or payload.get("demo_mode")
            or payload.get("demoMode"),
            False,
        )

    except Exception:
        return False


def _can_edit_project(project_payload: Optional[Mapping[str, Any]]) -> bool:
    try:
        access = _project_access_payload(project_payload)
        permissions = _safe_dict(access.get("permissions"))

        return _safe_bool(
            access.get("can_edit")
            or permissions.get("edit")
            or permissions.get("write"),
            False,
        )

    except Exception:
        return False


def _chunk_hint_payload(project_payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    try:
        payload = _safe_dict(project_payload)
        chunk = _safe_dict(payload.get("chunk"))

        service_refs = _safe_dict(payload.get("service_refs") or payload.get("serviceRefs"))
        service_chunk = _safe_dict(service_refs.get("chunk"))

        metadata = _safe_dict(payload.get("metadata") or payload.get("metadata_json") or payload.get("metadataJson"))
        metadata_chunk = _safe_dict(metadata.get("chunk"))

        def first(*values: Any) -> str:
            for value in values:
                text = _safe_str(value, "", 240)
                if text:
                    return text
            return ""

        chunk_project_id = first(
            payload.get("chunk_project_id"),
            payload.get("chunkProjectId"),
            chunk.get("chunk_project_id"),
            chunk.get("chunkProjectId"),
            chunk.get("project_id"),
            chunk.get("projectId"),
            service_chunk.get("chunk_project_id"),
            service_chunk.get("chunkProjectId"),
            metadata_chunk.get("chunk_project_id"),
            metadata_chunk.get("chunkProjectId"),
        )

        chunk_universe_id = first(
            payload.get("chunk_universe_id"),
            payload.get("chunkUniverseId"),
            chunk.get("chunk_universe_id"),
            chunk.get("chunkUniverseId"),
            chunk.get("universe_id"),
            chunk.get("universeId"),
            service_chunk.get("chunk_universe_id"),
            service_chunk.get("chunkUniverseId"),
            metadata_chunk.get("chunk_universe_id"),
            metadata_chunk.get("chunkUniverseId"),
        )

        chunk_world_id = first(
            payload.get("chunk_world_id"),
            payload.get("chunkWorldId"),
            chunk.get("chunk_world_id"),
            chunk.get("chunkWorldId"),
            chunk.get("world_id"),
            chunk.get("worldId"),
            service_chunk.get("chunk_world_id"),
            service_chunk.get("chunkWorldId"),
            metadata_chunk.get("chunk_world_id"),
            metadata_chunk.get("chunkWorldId"),
        )

        result: Dict[str, Any] = {}

        if chunk_project_id:
            result["chunk_project_id"] = chunk_project_id

        if chunk_universe_id:
            result["chunk_universe_id"] = chunk_universe_id

        if chunk_world_id:
            result["chunk_world_id"] = chunk_world_id

        return result

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────

def _request_obj_explicit(request_obj: Any = None) -> Any:
    if request_obj is not None:
        return request_obj

    try:
        return request
    except Exception:
        return None


def _request_url_root(request_obj: Any = None) -> str:
    try:
        req = _request_obj_explicit(request_obj)

        if req is None:
            return ""

        value = getattr(req, "url_root", "")
        return _normalize_base_url_cached(_safe_str(value, "", 4000), "")

    except Exception:
        return ""


def _app_public_base_url(request_obj: Any = None, prefer_request_host: Optional[bool] = None) -> str:
    try:
        if prefer_request_host is None:
            prefer_request_host = _config_bool("VECTOPLAN_EMBED_PREFER_REQUEST_HOST", True)

        request_root = _request_url_root(request_obj)

        if prefer_request_host and request_root:
            return request_root

        configured = (
            _config_str("VECTOPLAN_APP_PUBLIC_URL", "", 4000)
            or _config_str("VECTOPLAN_APP_PUBLIC_BASE_URL", "", 4000)
            or _config_str("APP_PUBLIC_URL", "", 4000)
            or DEFAULT_APP_PUBLIC_URL
        )

        return _normalize_base_url_cached(configured, DEFAULT_APP_PUBLIC_URL)

    except Exception:
        return DEFAULT_APP_PUBLIC_URL


def _absolute_app_url(path_or_url: str, request_obj: Any = None, prefer_request_host: Optional[bool] = None) -> str:
    try:
        value = _safe_str(path_or_url, "", 4000)

        if not value:
            return ""

        if _is_absolute_url_cached(value):
            return value

        base = _app_public_base_url(request_obj, prefer_request_host)

        if not value.startswith("/"):
            value = "/" + value

        return _join_url_cached(base, value)

    except Exception:
        return ""


def _context_path(project_public_id: str) -> str:
    try:
        template = _config_str("VECTOPLAN_PROJECT_CONTEXT_PATH_TEMPLATE", DEFAULT_CONTEXT_PATH_TEMPLATE, 4000)
        return template.format(project_public_id=_safe_quote(project_public_id))
    except Exception:
        return DEFAULT_CONTEXT_PATH_TEMPLATE.format(project_public_id=_safe_quote(project_public_id))


def _return_path(project_public_id: str) -> str:
    try:
        template = _config_str("VECTOPLAN_PROJECT_RETURN_PATH_TEMPLATE", DEFAULT_RETURN_PATH_TEMPLATE, 4000)
        return template.format(project_public_id=_safe_quote(project_public_id))
    except Exception:
        return DEFAULT_RETURN_PATH_TEMPLATE.format(project_public_id=_safe_quote(project_public_id))


def _clean_query_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}

    try:
        for key, value in dict(params or {}).items():
            key_text = _safe_str(key, "", 180)

            if not key_text:
                continue

            if value is None:
                continue

            if isinstance(value, bool):
                clean[key_text] = "1" if value else "0"
                continue

            if isinstance(value, (list, tuple)):
                values = []
                for item in value:
                    item_text = _safe_str(item, "", 2000)
                    if item_text:
                        values.append(item_text)
                if values:
                    clean[key_text] = values
                continue

            value_text = _safe_str(value, "", 4000)
            if value_text:
                clean[key_text] = value_text

        return clean

    except Exception:
        return clean


def _append_query_params(url: str, params: Mapping[str, Any]) -> str:
    try:
        target = _safe_str(url, "", 4000)
        if not target:
            return ""

        clean = _clean_query_params(params)
        if not clean:
            return target

        split = urlsplit(target)
        existing = parse_qsl(split.query, keep_blank_values=True)

        merged: Dict[str, Any] = {}

        for key, value in existing:
            if key:
                merged[key] = value

        for key, value in clean.items():
            merged[key] = value

        query = urlencode(merged, doseq=True)

        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                query,
                split.fragment,
            )
        )

    except Exception:
        return url


# ─────────────────────────────────────────────────────────────
# Target config
# ─────────────────────────────────────────────────────────────

def _editor_target_config() -> WorkspaceTargetConfig:
    cache_key = "target:editor3d"

    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []

    try:
        enabled = _config_bool("VECTOPLAN_EDITOR_EMBED_ENABLED", True)

        public_base_url = (
            _config_str("VECTOPLAN_EDITOR_PUBLIC_URL", "", 4000)
            or _config_str("VECTOPLAN_EDITOR_PUBLIC_BASE_URL", "", 4000)
            or DEFAULT_EDITOR_PUBLIC_URL
        )
        public_base_url = _normalize_base_url_cached(public_base_url, DEFAULT_EDITOR_PUBLIC_URL)

        route = (
            _config_str("VECTOPLAN_EDITOR_EMBED_ROUTE", "", 2000)
            or _config_str("VECTOPLAN_EDITOR_ROUTE", "", 2000)
            or DEFAULT_EDITOR_ROUTE
        )
        route = _normalize_route_cached(route, DEFAULT_EDITOR_ROUTE)

        if not public_base_url:
            warnings.append("VECTOPLAN_EDITOR_PUBLIC_URL fehlt; Default wird verwendet.")
            public_base_url = DEFAULT_EDITOR_PUBLIC_URL

        public_route_url = _join_url_cached(public_base_url, route)

        if not public_route_url:
            enabled = False
            warnings.append("Editor-Public-URL konnte nicht gebaut werden.")

        result = WorkspaceTargetConfig(
            workspace=WORKSPACE_EDITOR3D,
            service_name="vectoplan-editor",
            enabled=enabled,
            public_base_url=public_base_url,
            route=route,
            public_route_url=public_route_url,
            source="VECTOPLAN_EDITOR_PUBLIC_URL",
            warnings=tuple(warnings),
        )

        return _cache_set(cache_key, result)

    except Exception as exc:
        _log_warning("editor target config failed: %s", exc.__class__.__name__)

        result = WorkspaceTargetConfig(
            workspace=WORKSPACE_EDITOR3D,
            service_name="vectoplan-editor",
            enabled=False,
            public_base_url=DEFAULT_EDITOR_PUBLIC_URL,
            route=DEFAULT_EDITOR_ROUTE,
            public_route_url=_join_url_cached(DEFAULT_EDITOR_PUBLIC_URL, DEFAULT_EDITOR_ROUTE),
            source="fallback_error",
            warnings=("Editor-Konfiguration konnte nicht gelesen werden.",),
        )

        return _cache_set(cache_key, result)


def _map_target_config() -> WorkspaceTargetConfig:
    cache_key = "target:map"

    cached = _cache_get(cache_key)
    if isinstance(cached, WorkspaceTargetConfig):
        return cached

    warnings: list[str] = []

    try:
        enabled = _config_bool("OPENLAYER_EMBED_ENABLED", True)

        public_base_url = (
            _config_str("OPENLAYER_PUBLIC_URL", "", 4000)
            or _config_str("OPENLAYER_PUBLIC_BASE_URL", "", 4000)
            or DEFAULT_OPENLAYER_PUBLIC_URL
        )
        public_base_url = _normalize_base_url_cached(public_base_url, DEFAULT_OPENLAYER_PUBLIC_URL)

        route = _config_str("OPENLAYER_ROUTE", DEFAULT_OPENLAYER_ROUTE, 2000)
        route = _normalize_route_cached(route, DEFAULT_OPENLAYER_ROUTE)

        public_route_url = _join_url_cached(public_base_url, route)

        if not public_route_url:
            enabled = False
            warnings.append("OpenLayer-Public-URL konnte nicht gebaut werden.")

        result = WorkspaceTargetConfig(
            workspace=WORKSPACE_MAP,
            service_name="vectoplan-openlayer",
            enabled=enabled,
            public_base_url=public_base_url,
            route=route,
            public_route_url=public_route_url,
            source="OPENLAYER_PUBLIC_URL",
            warnings=tuple(warnings),
        )

        return _cache_set(cache_key, result)

    except Exception as exc:
        _log_warning("map target config failed: %s", exc.__class__.__name__)

        result = WorkspaceTargetConfig(
            workspace=WORKSPACE_MAP,
            service_name="vectoplan-openlayer",
            enabled=False,
            public_base_url=DEFAULT_OPENLAYER_PUBLIC_URL,
            route=DEFAULT_OPENLAYER_ROUTE,
            public_route_url=_join_url_cached(DEFAULT_OPENLAYER_PUBLIC_URL, DEFAULT_OPENLAYER_ROUTE),
            source="fallback_error",
            warnings=("OpenLayer-Konfiguration konnte nicht gelesen werden.",),
        )

        return _cache_set(cache_key, result)


def get_workspace_target_config(workspace: Any) -> WorkspaceTargetConfig:
    normalized = normalize_workspace(workspace)

    if normalized == WORKSPACE_EDITOR3D:
        return _editor_target_config()

    if normalized == WORKSPACE_MAP:
        return _map_target_config()

    return WorkspaceTargetConfig(
        workspace=normalized,
        service_name="vectoplan-app",
        enabled=False,
        public_base_url="",
        route="",
        public_route_url="",
        source="not_external",
        warnings=(f"Workspace {normalized!r} ist kein externer Embed-Workspace.",),
    )


def is_external_workspace(workspace: Any) -> bool:
    try:
        return normalize_workspace(workspace) in EXTERNAL_WORKSPACES
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Embed URL builders
# ─────────────────────────────────────────────────────────────

def _base_embed_params(
    *,
    workspace: str,
    project: Any = None,
    project_payload: Optional[Mapping[str, Any]] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    request_obj: Any = None,
    include_context: bool = True,
    include_return_url: bool = True,
    include_chunk_hints: Optional[bool] = None,
    prefer_request_host: Optional[bool] = None,
) -> Dict[str, Any]:
    try:
        payload = _safe_dict(project_payload)
        project_public_id = _project_public_id(project=project, project_payload=payload)
        project_id = _project_internal_id(project=project, project_payload=payload)

        demo_mode = _is_demo_mode(current_user=current_user, project_payload=payload)
        can_edit = _can_edit_project(payload)
        read_only = bool(demo_mode or not can_edit)

        params: Dict[str, Any] = {
            "embed": "1",
            "source": "vectoplan-app",
            "workspace": workspace,
            "app_project_public_id": project_public_id,
            "project_public_id": project_public_id,
            "read_only": read_only,
        }

        if _config_bool("VECTOPLAN_EMBED_INCLUDE_INTERNAL_PROJECT_ID", False) and project_id:
            params["app_project_id"] = project_id

        if include_context and project_public_id:
            context_path = _context_path(project_public_id)
            params["context_url"] = _absolute_app_url(
                context_path,
                request_obj=request_obj,
                prefer_request_host=prefer_request_host,
            )

        if include_return_url and project_public_id:
            return_path = _return_path(project_public_id)
            params["return_url"] = _absolute_app_url(
                return_path,
                request_obj=request_obj,
                prefer_request_host=prefer_request_host,
            )

        app_public_url = _app_public_base_url(request_obj=request_obj, prefer_request_host=prefer_request_host)
        if app_public_url:
            params["app_public_url"] = app_public_url

        if demo_mode:
            params["demo_mode"] = "1"

        if include_chunk_hints is None:
            include_chunk_hints = _config_bool("VECTOPLAN_EMBED_INCLUDE_CHUNK_QUERY_PARAMS", True)

        if include_chunk_hints:
            params.update(_chunk_hint_payload(payload))

        return _clean_query_params(params)

    except Exception:
        return {}


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

    try:
        if normalized_workspace in FORBIDDEN_EXTERNAL_WORKSPACES:
            return WorkspaceEmbedResult(
                ok=False,
                workspace=normalized_workspace,
                code="workspace_forbidden",
                message="Dieser Workspace darf nicht extern eingebettet werden.",
                error="forbidden_workspace",
            )

        if normalized_workspace not in EXTERNAL_WORKSPACES:
            return WorkspaceEmbedResult(
                ok=False,
                workspace=normalized_workspace,
                code="workspace_not_external",
                message="Dieser Workspace wird nicht über einen externen Embed-Service geladen.",
                error="workspace_not_external",
            )

        target = get_workspace_target_config(normalized_workspace)

        project_public_id = _project_public_id(
            project=project,
            project_payload=project_payload,
        )

        if _is_new_project_id(project_public_id):
            return WorkspaceEmbedResult(
                ok=False,
                workspace=normalized_workspace,
                code="project_public_id_missing",
                message="Für externe Workspaces muss das Projekt zuerst gespeichert sein.",
                project_public_id=project_public_id,
                app_project_public_id=project_public_id,
                warnings=list(target.warnings),
                error="project_public_id_missing",
            )

        if not target.enabled:
            return WorkspaceEmbedResult(
                ok=False,
                workspace=normalized_workspace,
                code="embed_disabled",
                message=f"Embed für Workspace {normalized_workspace!r} ist deaktiviert.",
                project_public_id=project_public_id,
                app_project_public_id=project_public_id,
                public_base_url=target.public_base_url,
                route=target.route,
                target_url=target.public_route_url,
                warnings=list(target.warnings),
                error="embed_disabled",
            )

        if not target.public_route_url:
            return WorkspaceEmbedResult(
                ok=False,
                workspace=normalized_workspace,
                code="target_url_missing",
                message="Public-Embed-Ziel konnte nicht gebaut werden.",
                project_public_id=project_public_id,
                app_project_public_id=project_public_id,
                public_base_url=target.public_base_url,
                route=target.route,
                warnings=list(target.warnings),
                error="target_url_missing",
            )

        params = _base_embed_params(
            workspace=normalized_workspace,
            project=project,
            project_payload=project_payload,
            current_user=current_user,
            request_obj=request_obj,
            include_context=include_context,
            include_return_url=include_return_url,
            include_chunk_hints=include_chunk_hints,
            prefer_request_host=prefer_request_host,
        )

        extra = _clean_query_params(extra_params or {})
        params.update(extra)

        url = _append_query_params(target.public_route_url, params)

        return WorkspaceEmbedResult(
            ok=bool(url),
            workspace=normalized_workspace,
            url=url,
            target_url=target.public_route_url,
            public_base_url=target.public_base_url,
            route=target.route,
            code="ok" if url else "url_build_failed",
            message="Embed-URL wurde gebaut." if url else "Embed-URL konnte nicht gebaut werden.",
            project_public_id=project_public_id,
            app_project_public_id=project_public_id,
            params=params,
            warnings=list(target.warnings),
            error=None if url else "url_build_failed",
            uses_public_url=True,
        )

    except Exception as exc:
        _log_exception("build_workspace_embed_result failed", exc)

        return WorkspaceEmbedResult(
            ok=False,
            workspace=normalized_workspace,
            code="embed_url_build_exception",
            message="Embed-URL konnte wegen eines internen Fehlers nicht gebaut werden.",
            error=f"{exc.__class__.__name__}: {exc}",
        )


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


# ─────────────────────────────────────────────────────────────
# Status / diagnostics
# ─────────────────────────────────────────────────────────────

def get_workspace_embed_status() -> Dict[str, Any]:
    try:
        editor = get_workspace_target_config(WORKSPACE_EDITOR3D)
        map_target = get_workspace_target_config(WORKSPACE_MAP)

        return {
            "ok": True,
            "service": "workspace_embed_service",
            "cache": {
                "module_cache_keys": sorted(_MODULE_CACHE.keys()),
                "ttl_seconds": _CACHE_MAX_AGE_SECONDS,
            },
            "targets": {
                WORKSPACE_EDITOR3D: editor.to_dict(),
                WORKSPACE_MAP: map_target.to_dict(),
            },
            "external_workspaces": sorted(EXTERNAL_WORKSPACES),
            "forbidden_external_workspaces": sorted(FORBIDDEN_EXTERNAL_WORKSPACES),
            "rules": {
                "browser_uses_public_url": True,
                "internal_urls_exposed": False,
                "admin_is_never_external_embed": True,
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "service": "workspace_embed_service",
            "error": f"{exc.__class__.__name__}: {exc}",
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