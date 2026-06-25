# services/vectoplan-app/services/project_publication_service.py
"""
VECTOPLAN project publication service.

Zweck:
- Trennt Projekt-Sichtbarkeit von veröffentlichten Workspace-Reitern.
- Verwaltet, welche Reiter für externe/öffentliche/ungelistete Zugriffe sichtbar sind.
- Nutzt und erweitert die vorhandene ProjectEmbedPolicy.
- Hält Admin-, Team- und Systembereiche grundsätzlich nicht öffentlich.
- Bleibt kompatibel mit vorhandenen Projektfeldern wie visibility/is_public.
- Speichert keine Fachdaten aus 3D/Map/2D/LV.
- Erzeugt keine Benutzeraccounts.

Architektur:
- vectoplan-app ist zuständig für Projektfrontend, Rollen, Sichtbarkeit,
  Veröffentlichungs-/Embed-Policy und Workspace-Orchestrierung.
- Auth/Login/Registrierung/Abo/Bigdata-Zugriff liegen später in separaten Services.
- Fachliche Daten liegen in Editor, Chunk, OpenLayer, 2D, LV, Library usw.

Begriffe:
- visibility:
    private  = nur berechtigte Projektmitglieder
    unlisted = nicht gelistet, aber über Link/Embed öffentlich erreichbar, wenn Reiter veröffentlicht sind
    public   = öffentlich sichtbar, wenn Reiter veröffentlicht sind
- published_workspaces:
    Projektinfo, Map, 3D, 2D, LV, Versionen
- nie öffentlich:
    Admin, Team, Systemreferenzen, Berechtigungsverwaltung
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Robust imports
# ---------------------------------------------------------------------------

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False


try:
    from models.base import db  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.base import db  # type: ignore
    except Exception:
        try:
            from extensions import db  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "project_publication_service requires SQLAlchemy db."
            ) from exc


try:
    from models.projects import Project  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.projects import Project  # type: ignore
    except Exception:
        Project = None  # type: ignore


try:
    from models.project_embed import (  # type: ignore
        ProjectEmbedPolicy,
        get_or_create_embed_policy as _model_get_or_create_embed_policy,
        serialize_embed_policy as _model_serialize_embed_policy,
    )
except Exception:  # pragma: no cover
    try:
        from ..models.project_embed import (  # type: ignore
            ProjectEmbedPolicy,
            get_or_create_embed_policy as _model_get_or_create_embed_policy,
            serialize_embed_policy as _model_serialize_embed_policy,
        )
    except Exception:
        ProjectEmbedPolicy = None  # type: ignore
        _model_get_or_create_embed_policy = None  # type: ignore
        _model_serialize_embed_policy = None  # type: ignore


try:
    from models.project_audit import ProjectAuditEvent  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.project_audit import ProjectAuditEvent  # type: ignore
    except Exception:
        ProjectAuditEvent = None  # type: ignore


try:
    from services.project_permissions import (  # type: ignore
        can_manage_project,
        can_view_project,
        get_project_permission_result,
        require_project_permission,
    )
except Exception:  # pragma: no cover
    try:
        from .project_permissions import (  # type: ignore
            can_manage_project,
            can_view_project,
            get_project_permission_result,
            require_project_permission,
        )
    except Exception:
        can_manage_project = None  # type: ignore
        can_view_project = None  # type: ignore
        get_project_permission_result = None  # type: ignore
        require_project_permission = None  # type: ignore


try:
    from services.current_user import (  # type: ignore
        get_current_user_context,
        get_current_user_id,
    )
except Exception:  # pragma: no cover
    try:
        from .current_user import (  # type: ignore
            get_current_user_context,
            get_current_user_id,
        )
    except Exception:
        get_current_user_context = None  # type: ignore
        get_current_user_id = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGGER_NAME = "vectoplan.project_publication_service"

VISIBILITY_PRIVATE = "private"
VISIBILITY_UNLISTED = "unlisted"
VISIBILITY_PUBLIC = "public"

VALID_PUBLICATION_VISIBILITIES = {
    VISIBILITY_PRIVATE,
    VISIBILITY_UNLISTED,
    VISIBILITY_PUBLIC,
}

LEGACY_VISIBILITY_SHARED = "shared"

WORKSPACE_PROJECT = "project"
WORKSPACE_MAP = "map"
WORKSPACE_EDITOR3D = "editor3d"
WORKSPACE_CAD2D = "cad2d"
WORKSPACE_LV = "lv"
WORKSPACE_VERSIONS = "versions"

WORKSPACE_ADMIN = "admin"
WORKSPACE_TEAM = "team"
WORKSPACE_SETTINGS = "settings"
WORKSPACE_SYSTEM = "system"
WORKSPACE_PERMISSIONS = "permissions"

PUBLICATION_WORKSPACES: Tuple[str, ...] = (
    WORKSPACE_PROJECT,
    WORKSPACE_MAP,
    WORKSPACE_EDITOR3D,
    WORKSPACE_CAD2D,
    WORKSPACE_LV,
    WORKSPACE_VERSIONS,
)

NEVER_PUBLIC_WORKSPACES = {
    WORKSPACE_ADMIN,
    WORKSPACE_TEAM,
    WORKSPACE_SETTINGS,
    WORKSPACE_SYSTEM,
    WORKSPACE_PERMISSIONS,
}

WORKSPACE_ALIASES = {
    "project": WORKSPACE_PROJECT,
    "project_info": WORKSPACE_PROJECT,
    "projectinfo": WORKSPACE_PROJECT,
    "info": WORKSPACE_PROJECT,
    "overview": WORKSPACE_PROJECT,
    "basis": WORKSPACE_PROJECT,
    "details": WORKSPACE_PROJECT,

    "map": WORKSPACE_MAP,
    "maps": WORKSPACE_MAP,
    "karte": WORKSPACE_MAP,
    "openlayer": WORKSPACE_MAP,
    "openlayers": WORKSPACE_MAP,
    "gis": WORKSPACE_MAP,

    "3d": WORKSPACE_EDITOR3D,
    "editor": WORKSPACE_EDITOR3D,
    "editor3d": WORKSPACE_EDITOR3D,
    "editor_3d": WORKSPACE_EDITOR3D,
    "viewer3d": WORKSPACE_EDITOR3D,
    "viewer_3d": WORKSPACE_EDITOR3D,
    "world": WORKSPACE_EDITOR3D,

    "2d": WORKSPACE_CAD2D,
    "cad": WORKSPACE_CAD2D,
    "cad2d": WORKSPACE_CAD2D,
    "cad_2d": WORKSPACE_CAD2D,
    "plan": WORKSPACE_CAD2D,
    "plan2d": WORKSPACE_CAD2D,

    "lv": WORKSPACE_LV,
    "boq": WORKSPACE_LV,
    "leistungsverzeichnis": WORKSPACE_LV,
    "bill_of_quantities": WORKSPACE_LV,

    "versions": WORKSPACE_VERSIONS,
    "versionen": WORKSPACE_VERSIONS,
    "history": WORKSPACE_VERSIONS,
    "snapshots": WORKSPACE_VERSIONS,

    "admin": WORKSPACE_ADMIN,
    "management": WORKSPACE_ADMIN,
    "settings": WORKSPACE_SETTINGS,
    "einstellungen": WORKSPACE_SETTINGS,
    "team": WORKSPACE_TEAM,
    "members": WORKSPACE_TEAM,
    "permissions": WORKSPACE_PERMISSIONS,
    "rechte": WORKSPACE_PERMISSIONS,
    "system": WORKSPACE_SYSTEM,
    "systemrefs": WORKSPACE_SYSTEM,
    "system_references": WORKSPACE_SYSTEM,
}

WORKSPACE_LABELS = {
    WORKSPACE_PROJECT: "Projekt",
    WORKSPACE_MAP: "Map",
    WORKSPACE_EDITOR3D: "3D",
    WORKSPACE_CAD2D: "2D",
    WORKSPACE_LV: "LV",
    WORKSPACE_VERSIONS: "Versionen",
    WORKSPACE_ADMIN: "Admin",
    WORKSPACE_TEAM: "Team",
    WORKSPACE_SETTINGS: "Einstellungen",
    WORKSPACE_SYSTEM: "System",
    WORKSPACE_PERMISSIONS: "Rechte",
}

WORKSPACE_POLICY_FIELDS = {
    WORKSPACE_PROJECT: "allow_project_info",
    WORKSPACE_MAP: "allow_map",
    WORKSPACE_EDITOR3D: "allow_editor3d",
    WORKSPACE_CAD2D: "allow_2d",
    WORKSPACE_LV: "allow_lv",
    WORKSPACE_VERSIONS: "allow_versions",
}

DEFAULT_DESIRED_WORKSPACES = {
    WORKSPACE_PROJECT: True,
    WORKSPACE_MAP: False,
    WORKSPACE_EDITOR3D: False,
    WORKSPACE_CAD2D: False,
    WORKSPACE_LV: False,
    WORKSPACE_VERSIONS: False,
}

PUBLICATION_METADATA_KEY = "publication"

AUDIT_CATEGORY_PUBLICATION = "project_publication"
AUDIT_ACTION_PUBLICATION_UPDATED = "publication_updated"
AUDIT_ACTION_VISIBILITY_UPDATED = "visibility_updated"

DEFAULT_CACHE_TTL_SECONDS = 10


# ---------------------------------------------------------------------------
# Small safe helpers
# ---------------------------------------------------------------------------


def utcnow() -> _dt.datetime:
    try:
        return _dt.datetime.now(_dt.timezone.utc)
    except Exception:  # pragma: no cover
        return _dt.datetime.utcnow()


def _logger() -> logging.Logger:
    try:
        if has_app_context() and current_app is not None:
            return current_app.logger  # type: ignore[union-attr]
    except Exception:
        pass
    return logging.getLogger(LOGGER_NAME)


def _log_debug(message: str, **extra: Any) -> None:
    try:
        _logger().debug("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_info(message: str, **extra: Any) -> None:
    try:
        _logger().info("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_warning(message: str, **extra: Any) -> None:
    try:
        _logger().warning("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_exception(message: str, **extra: Any) -> None:
    try:
        _logger().exception("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _safe_str(value: Any, default: str = "", max_len: Optional[int] = None) -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        if max_len is not None and max_len > 0:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "public", "published"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "private", "hidden"}:
            return False
        return default
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
            return dict(value.to_dict())
        return {}
    except Exception:
        return {}


def _safe_list(value: Any) -> list:
    try:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        return []
    except Exception:
        return []


def _read_config(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(name)  # type: ignore[union-attr]
            if value is not None:
                return value
    except Exception:
        pass

    try:
        value = os.environ.get(name)
        if value is not None:
            return value
    except Exception:
        pass

    return default


def _commit_or_flush(commit: bool = True) -> None:
    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        raise


def _rollback_safely() -> None:
    try:
        db.session.rollback()
    except Exception:
        pass


def _session_add(obj: Any) -> None:
    try:
        db.session.add(obj)
    except Exception:
        pass


def _maybe_iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float

    def expired(self) -> bool:
        try:
            return time.time() >= self.expires_at
        except Exception:
            return True


class _TTLCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Tuple[bool, Any]:
        safe_key = _safe_str(key)
        if not safe_key:
            return False, None

        try:
            with self._lock:
                entry = self._items.get(safe_key)
                if entry is None:
                    return False, None
                if entry.expired():
                    self._items.pop(safe_key, None)
                    return False, None
                return True, entry.value
        except Exception:
            return False, None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        safe_key = _safe_str(key)
        ttl = _safe_int(ttl_seconds, 0) or 0
        if not safe_key or ttl <= 0:
            return

        try:
            with self._lock:
                self._items[safe_key] = _CacheEntry(
                    value=value,
                    expires_at=time.time() + ttl,
                )
        except Exception:
            pass

    def clear(self) -> None:
        try:
            with self._lock:
                self._items.clear()
        except Exception:
            pass

    def delete_prefix(self, prefix: str) -> None:
        safe_prefix = _safe_str(prefix)
        if not safe_prefix:
            return

        try:
            with self._lock:
                for key in list(self._items.keys()):
                    if key.startswith(safe_prefix):
                        self._items.pop(key, None)
        except Exception:
            pass


_PUBLICATION_CACHE = _TTLCache()


def _cache_ttl_seconds() -> int:
    return _safe_int(
        _read_config("PROJECT_PUBLICATION_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
        DEFAULT_CACHE_TTL_SECONDS,
    ) or DEFAULT_CACHE_TTL_SECONDS


def clear_project_publication_cache(project_id: Any = None) -> None:
    if project_id is None:
        _PUBLICATION_CACHE.clear()
        return

    safe_project_id = _safe_str(project_id)
    if safe_project_id:
        _PUBLICATION_CACHE.delete_prefix(f"publication:{safe_project_id}:")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_publication_visibility(value: Any, default: str = VISIBILITY_PRIVATE) -> str:
    text = _safe_str(value, default=default, max_len=40).lower().replace("-", "_")

    aliases = {
        "private": VISIBILITY_PRIVATE,
        "privat": VISIBILITY_PRIVATE,
        "closed": VISIBILITY_PRIVATE,
        "internal": VISIBILITY_PRIVATE,
        "intern": VISIBILITY_PRIVATE,
        "members": VISIBILITY_PRIVATE,
        "member": VISIBILITY_PRIVATE,
        "team": VISIBILITY_PRIVATE,
        # Wichtig: altes "shared" nicht automatisch öffentlich machen.
        # Teilen über Mitglieder bleibt privat/projektbasiert.
        "shared": VISIBILITY_PRIVATE,
        "geteilt": VISIBILITY_PRIVATE,

        "unlisted": VISIBILITY_UNLISTED,
        "not_listed": VISIBILITY_UNLISTED,
        "nicht_gelistet": VISIBILITY_UNLISTED,
        "hidden_link": VISIBILITY_UNLISTED,
        "link": VISIBILITY_UNLISTED,
        "linkshare": VISIBILITY_UNLISTED,
        "link_shared": VISIBILITY_UNLISTED,
        "share_link": VISIBILITY_UNLISTED,

        "public": VISIBILITY_PUBLIC,
        "öffentlich": VISIBILITY_PUBLIC,
        "oeffentlich": VISIBILITY_PUBLIC,
        "open": VISIBILITY_PUBLIC,
        "listed": VISIBILITY_PUBLIC,
    }

    normalized = aliases.get(text, text)
    if normalized not in VALID_PUBLICATION_VISIBILITIES:
        return default
    return normalized


def normalize_workspace_key(value: Any, default: str = "") -> str:
    text = _safe_str(value, default=default, max_len=80).lower().replace("-", "_").replace(" ", "_")
    if not text:
        return default

    normalized = WORKSPACE_ALIASES.get(text, text)

    if normalized in PUBLICATION_WORKSPACES:
        return normalized

    if normalized in NEVER_PUBLIC_WORKSPACES:
        return normalized

    return default


def is_publication_workspace(value: Any) -> bool:
    return normalize_workspace_key(value) in PUBLICATION_WORKSPACES


def is_never_public_workspace(value: Any) -> bool:
    return normalize_workspace_key(value) in NEVER_PUBLIC_WORKSPACES


def workspace_label(workspace: Any) -> str:
    key = normalize_workspace_key(workspace)
    return WORKSPACE_LABELS.get(key, _safe_str(workspace, default="Workspace"))


def normalize_published_workspaces(
    value: Any,
    existing: Optional[Mapping[str, Any]] = None,
    default: Optional[Mapping[str, Any]] = None,
) -> Dict[str, bool]:
    """
    Normalisiert published_workspaces.

    Akzeptiert:
    - dict: {"map": true, "3d": false}
    - list: ["project", "map"]
    - string: "project,map,3d"
    - None: existing oder default

    Admin/Team/System/Rechte werden nie als publizierbar übernommen.
    """
    result: Dict[str, bool] = {}

    base = _safe_dict(default) or dict(DEFAULT_DESIRED_WORKSPACES)
    base.update(_safe_dict(existing))

    for key in PUBLICATION_WORKSPACES:
        result[key] = _safe_bool(base.get(key), default=False)

    if value is None:
        return result

    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = normalize_workspace_key(raw_key)
            if key in PUBLICATION_WORKSPACES:
                result[key] = _safe_bool(raw_value, default=False)
        return result

    items: list = []

    if isinstance(value, str):
        raw_parts = value.replace(";", ",").replace("|", ",").split(",")
        items = [part.strip() for part in raw_parts if part.strip()]
    else:
        items = _safe_list(value)

    # Wenn eine Liste übergeben wird, bedeutet Vorkommen = true.
    list_result = {key: False for key in PUBLICATION_WORKSPACES}
    for raw_item in items:
        key = normalize_workspace_key(raw_item)
        if key in PUBLICATION_WORKSPACES:
            list_result[key] = True

    return list_result


def effective_published_workspaces(
    visibility: Any,
    desired_workspaces: Mapping[str, Any],
) -> Dict[str, bool]:
    normalized_visibility = normalize_publication_visibility(visibility)
    desired = normalize_published_workspaces(desired_workspaces)

    if normalized_visibility == VISIBILITY_PRIVATE:
        return {key: False for key in PUBLICATION_WORKSPACES}

    return {key: bool(desired.get(key, False)) for key in PUBLICATION_WORKSPACES}


def publication_enabled(
    visibility: Any,
    desired_workspaces: Mapping[str, Any],
) -> bool:
    effective = effective_published_workspaces(visibility, desired_workspaces)
    return normalize_publication_visibility(visibility) != VISIBILITY_PRIVATE and any(effective.values())


# ---------------------------------------------------------------------------
# Actor and permission helpers
# ---------------------------------------------------------------------------


def get_actor_context(user_id: Any = None) -> Dict[str, Any]:
    context: Dict[str, Any] = {}

    try:
        if get_current_user_context is not None:
            context = _safe_dict(get_current_user_context())
    except Exception:
        context = {}

    if user_id is not None:
        context["user_id"] = _safe_int(user_id)
        context["id"] = _safe_int(user_id)

    if not context.get("user_id") and not context.get("id"):
        try:
            if get_current_user_id is not None:
                resolved_user_id = get_current_user_id()
                if resolved_user_id is not None:
                    context["user_id"] = _safe_int(resolved_user_id)
                    context["id"] = _safe_int(resolved_user_id)
        except Exception:
            pass

    actor_user_id = _safe_int(context.get("user_id") or context.get("id"), default=None)

    demo_mode = _safe_bool(
        context.get("demo_mode")
        or context.get("is_demo")
        or context.get("demo"),
        default=False,
    )

    authenticated = _safe_bool(
        context.get("authenticated")
        or context.get("is_authenticated")
        or context.get("logged_in"),
        default=bool(actor_user_id),
    )

    return {
        **context,
        "user_id": actor_user_id,
        "id": actor_user_id,
        "demo_mode": bool(demo_mode),
        "authenticated": bool(authenticated),
    }


def _actor_user_id(actor_context: Optional[Mapping[str, Any]]) -> Optional[int]:
    data = _safe_dict(actor_context)
    return _safe_int(data.get("user_id") or data.get("id"), default=None)


def _actor_is_demo(actor_context: Optional[Mapping[str, Any]]) -> bool:
    data = _safe_dict(actor_context)
    return _safe_bool(data.get("demo_mode") or data.get("is_demo") or data.get("demo"), default=False)


def _actor_is_authenticated(actor_context: Optional[Mapping[str, Any]]) -> bool:
    data = _safe_dict(actor_context)
    return _safe_bool(
        data.get("authenticated")
        or data.get("is_authenticated")
        or data.get("logged_in"),
        default=bool(_actor_user_id(data)),
    )


def _project_owner_user_id(project: Any) -> Optional[int]:
    return _safe_int(getattr(project, "owner_user_id", None), default=None)


def _can_view_project(project: Any, actor_context: Optional[Mapping[str, Any]]) -> bool:
    actor_user_id = _actor_user_id(actor_context)

    if not actor_user_id:
        return False

    try:
        if can_view_project is not None:
            return bool(can_view_project(project, actor_user_id))
    except Exception:
        pass

    try:
        if get_project_permission_result is not None:
            permission = get_project_permission_result(project, actor_user_id)
            data = _safe_dict(permission)
            if _safe_bool(data.get("can_view"), default=False):
                return True
            permissions = _safe_dict(data.get("permissions"))
            if _safe_bool(permissions.get("view"), default=False):
                return True
    except Exception:
        pass

    try:
        if _project_owner_user_id(project) == actor_user_id:
            return True
    except Exception:
        pass

    return False


def _can_manage_project(project: Any, actor_context: Optional[Mapping[str, Any]]) -> bool:
    actor_user_id = _actor_user_id(actor_context)

    if not actor_user_id:
        return False

    try:
        if can_manage_project is not None:
            return bool(can_manage_project(project, actor_user_id))
    except Exception:
        pass

    try:
        if require_project_permission is not None:
            maybe = require_project_permission(project, "manage", user_id=actor_user_id)
            if maybe is None:
                return True

            maybe_dict = _safe_dict(maybe)
            if not maybe_dict:
                return True

            if _safe_bool(maybe_dict.get("ok"), default=True):
                return True
    except Exception:
        pass

    try:
        if get_project_permission_result is not None:
            permission = get_project_permission_result(project, actor_user_id)
            data = _safe_dict(permission)
            if _safe_bool(data.get("can_manage"), default=False):
                return True
            permissions = _safe_dict(data.get("permissions"))
            if _safe_bool(permissions.get("manage"), default=False):
                return True
    except Exception:
        pass

    try:
        if _project_owner_user_id(project) == actor_user_id:
            return True
    except Exception:
        pass

    return False


def _access_payload(project: Any, actor_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "authenticated": _actor_is_authenticated(actor_context),
        "demo_mode": _actor_is_demo(actor_context),
        "user_id": _actor_user_id(actor_context),
        "can_view": _can_view_project(project, actor_context),
        "can_manage": _can_manage_project(project, actor_context),
    }


# ---------------------------------------------------------------------------
# Project / policy resolving
# ---------------------------------------------------------------------------


def resolve_project(project_or_id: Any) -> Optional[Any]:
    if project_or_id is None:
        return None

    try:
        if Project is not None and isinstance(project_or_id, Project):
            return project_or_id
    except Exception:
        pass

    try:
        if hasattr(project_or_id, "id") and hasattr(project_or_id, "public_id"):
            return project_or_id
    except Exception:
        pass

    if Project is None:
        return None

    raw = _safe_str(project_or_id)
    if not raw:
        return None

    try:
        numeric_id = _safe_int(raw)
        if numeric_id is not None and str(numeric_id) == raw:
            found = Project.query.get(numeric_id)
            if found is not None:
                return found
    except Exception:
        pass

    try:
        return Project.query.filter(Project.public_id == raw).first()
    except Exception:
        return None


def _project_id(project: Any) -> Optional[int]:
    return _safe_int(getattr(project, "id", None), default=None)


def _project_public_id(project: Any) -> str:
    return _safe_str(getattr(project, "public_id", ""), default="", max_len=120)


def get_or_create_publication_policy(project: Any) -> Optional[Any]:
    if project is None or ProjectEmbedPolicy is None:
        return None

    try:
        if _model_get_or_create_embed_policy is not None:
            policy = _model_get_or_create_embed_policy(project)
            if policy is not None:
                return policy
    except Exception:
        pass

    project_id = _project_id(project)
    if not project_id:
        return None

    try:
        policy = ProjectEmbedPolicy.query.filter(ProjectEmbedPolicy.project_id == project_id).first()
        if policy is not None:
            return policy
    except Exception:
        policy = None

    try:
        policy = ProjectEmbedPolicy(project_id=project_id)
    except Exception:
        try:
            policy = ProjectEmbedPolicy()
            policy.project_id = project_id
        except Exception:
            return None

    try:
        _session_add(policy)
    except Exception:
        pass

    return policy


def _serialize_policy(policy: Any) -> Dict[str, Any]:
    if policy is None:
        return {}

    try:
        if _model_serialize_embed_policy is not None:
            data = _model_serialize_embed_policy(policy)
            return _safe_dict(data)
    except Exception:
        pass

    try:
        if hasattr(policy, "to_dict") and callable(policy.to_dict):
            return _safe_dict(policy.to_dict())
    except Exception:
        pass

    data: Dict[str, Any] = {}
    for key in [
        "id",
        "project_id",
        "enabled",
        "allow_iframe",
        "mode",
        "allowed_modes",
        "allow_project_info",
        "allow_map",
        "allow_editor3d",
        "allow_2d",
        "allow_lv",
        "allow_versions",
        "require_auth",
        "require_project_permission",
        "metadata_json",
        "settings",
        "created_at",
        "updated_at",
    ]:
        try:
            value = getattr(policy, key, None)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            data[key] = value
        except Exception:
            pass

    return data


def _json_attr(policy: Any, attr: str) -> Dict[str, Any]:
    try:
        if hasattr(policy, attr):
            return _safe_dict(getattr(policy, attr))
    except Exception:
        pass
    return {}


def _set_json_attr(policy: Any, attr: str, value: Mapping[str, Any]) -> bool:
    try:
        if hasattr(policy, attr):
            setattr(policy, attr, _safe_dict(value))
            return True
    except Exception:
        pass
    return False


def _publication_extra(policy: Any) -> Dict[str, Any]:
    """
    Liest zusätzliche Publication-Daten aus metadata_json/settings/service_payload.

    Nicht alle bestehenden Models haben dieselben JSON-Felder. Deshalb wird
    defensiv über mehrere mögliche Felder gelesen.
    """
    if policy is None:
        return {}

    for attr in ("metadata_json", "metadata", "settings", "policy_json"):
        data = _json_attr(policy, attr)
        publication = _safe_dict(data.get(PUBLICATION_METADATA_KEY))
        if publication:
            return publication

    return {}


def _set_publication_extra(policy: Any, publication_data: Mapping[str, Any]) -> None:
    if policy is None:
        return

    payload = _safe_dict(publication_data)

    for attr in ("metadata_json", "metadata", "settings", "policy_json"):
        try:
            if hasattr(policy, attr):
                data = _json_attr(policy, attr)
                data[PUBLICATION_METADATA_KEY] = payload
                if _set_json_attr(policy, attr, data):
                    return
        except Exception:
            continue


def _get_policy_bool(policy: Any, attr: str, default: bool = False) -> bool:
    try:
        if hasattr(policy, attr):
            return _safe_bool(getattr(policy, attr), default=default)
    except Exception:
        pass

    extra = _publication_extra(policy)
    return _safe_bool(extra.get(attr), default=default)


def _set_policy_value(policy: Any, attr: str, value: Any) -> None:
    try:
        if hasattr(policy, attr):
            setattr(policy, attr, value)
            return
    except Exception:
        pass

    extra = _publication_extra(policy)
    extra[attr] = value
    _set_publication_extra(policy, extra)


def _desired_workspaces_from_policy(policy: Any) -> Dict[str, bool]:
    extra = _publication_extra(policy)
    stored = _safe_dict(extra.get("published_workspaces") or extra.get("desired_workspaces"))

    if stored:
        return normalize_published_workspaces(stored)

    result = dict(DEFAULT_DESIRED_WORKSPACES)

    for workspace in PUBLICATION_WORKSPACES:
        field = WORKSPACE_POLICY_FIELDS.get(workspace)
        if not field:
            continue
        result[workspace] = _get_policy_bool(policy, field, default=result.get(workspace, False))

    return result


def _visibility_from_project(project: Any) -> str:
    raw = getattr(project, "visibility", VISIBILITY_PRIVATE)
    return normalize_publication_visibility(raw)


def _apply_visibility_to_project(project: Any, visibility: str) -> None:
    normalized = normalize_publication_visibility(visibility)

    try:
        if hasattr(project, "visibility"):
            project.visibility = normalized
    except Exception:
        pass

    try:
        if hasattr(project, "is_public"):
            project.is_public = normalized == VISIBILITY_PUBLIC
    except Exception:
        pass

    try:
        if hasattr(project, "updated_at"):
            project.updated_at = utcnow()
    except Exception:
        pass


def _apply_policy(
    policy: Any,
    visibility: str,
    desired_workspaces: Mapping[str, Any],
    require_auth: Optional[bool] = None,
    require_project_permission: Optional[bool] = None,
) -> Dict[str, Any]:
    normalized_visibility = normalize_publication_visibility(visibility)
    desired = normalize_published_workspaces(desired_workspaces)
    effective = effective_published_workspaces(normalized_visibility, desired)
    enabled = publication_enabled(normalized_visibility, desired)

    if normalized_visibility == VISIBILITY_PRIVATE:
        final_require_auth = True
        final_require_project_permission = True
    else:
        final_require_auth = _safe_bool(require_auth, default=False)
        final_require_project_permission = _safe_bool(require_project_permission, default=False)

    _set_policy_value(policy, "enabled", enabled)
    _set_policy_value(policy, "allow_iframe", enabled)
    _set_policy_value(policy, "mode", "readonly" if enabled else "private")
    _set_policy_value(policy, "require_auth", final_require_auth)
    _set_policy_value(policy, "require_project_permission", final_require_project_permission)

    try:
        if hasattr(policy, "allowed_modes"):
            existing_modes = getattr(policy, "allowed_modes", None)
            if not existing_modes:
                setattr(policy, "allowed_modes", ["readonly", "spectator"])
    except Exception:
        pass

    for workspace in PUBLICATION_WORKSPACES:
        field = WORKSPACE_POLICY_FIELDS.get(workspace)
        if field:
            _set_policy_value(policy, field, bool(desired.get(workspace, False)))

    # Harte Sicherheitsgrenze: Admin/System/Team nie öffentlich.
    for forbidden in ("allow_admin", "allow_team", "allow_settings", "allow_system", "allow_permissions"):
        _set_policy_value(policy, forbidden, False)

    publication_extra = {
        "visibility": normalized_visibility,
        "published_workspaces": desired,
        "effective_published_workspaces": effective,
        "publication_enabled": enabled,
        "require_auth": final_require_auth,
        "require_project_permission": final_require_project_permission,
        "never_public_workspaces": sorted(NEVER_PUBLIC_WORKSPACES),
        "updated_at": _maybe_iso(utcnow()),
    }

    _set_publication_extra(policy, publication_extra)

    try:
        if hasattr(policy, "updated_at"):
            policy.updated_at = utcnow()
    except Exception:
        pass

    return publication_extra


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def build_workspace_publication_items(
    desired: Mapping[str, Any],
    effective: Mapping[str, Any],
    include_never_public: bool = False,
) -> list:
    result = []

    for workspace in PUBLICATION_WORKSPACES:
        result.append(
            {
                "key": workspace,
                "label": workspace_label(workspace),
                "published": bool(desired.get(workspace, False)),
                "effectivePublished": bool(effective.get(workspace, False)),
                "canBePublished": True,
                "neverPublic": False,
            }
        )

    if include_never_public:
        for workspace in sorted(NEVER_PUBLIC_WORKSPACES):
            result.append(
                {
                    "key": workspace,
                    "label": workspace_label(workspace),
                    "published": False,
                    "effectivePublished": False,
                    "canBePublished": False,
                    "neverPublic": True,
                }
            )

    return result


def build_publication_payload(
    project: Any,
    policy: Any,
    actor_context: Optional[Mapping[str, Any]] = None,
    include_private: bool = False,
    for_public: bool = False,
) -> Dict[str, Any]:
    visibility = _visibility_from_project(project)
    desired = _desired_workspaces_from_policy(policy)
    effective = effective_published_workspaces(visibility, desired)
    enabled = publication_enabled(visibility, desired)

    access = _access_payload(project, actor_context)

    require_auth = _get_policy_bool(policy, "require_auth", default=visibility == VISIBILITY_PRIVATE)
    require_project_permission = _get_policy_bool(
        policy,
        "require_project_permission",
        default=visibility == VISIBILITY_PRIVATE,
    )

    public_payload = {
        "visibility": visibility,
        "is_public": visibility == VISIBILITY_PUBLIC,
        "is_unlisted": visibility == VISIBILITY_UNLISTED,
        "publication_enabled": enabled,
        "require_auth": bool(require_auth),
        "require_project_permission": bool(require_project_permission),
        "effective_published_workspaces": effective,
        "workspaces": build_workspace_publication_items(
            desired=desired if include_private and not for_public else effective,
            effective=effective,
            include_never_public=include_private and not for_public,
        ),
    }

    if for_public and not include_private:
        return {
            "ok": True,
            "project_id": _project_public_id(project),
            "project_public_id": _project_public_id(project),
            "publication": public_payload,
        }

    private_payload = {
        "ok": True,
        "project_id": _project_id(project),
        "project_public_id": _project_public_id(project),
        "visibility": visibility,
        "is_public": visibility == VISIBILITY_PUBLIC,
        "is_unlisted": visibility == VISIBILITY_UNLISTED,
        "publication_enabled": enabled,
        "published_workspaces": desired,
        "effective_published_workspaces": effective,
        "require_auth": bool(require_auth),
        "require_project_permission": bool(require_project_permission),
        "access": access,
        "workspaces": build_workspace_publication_items(
            desired=desired,
            effective=effective,
            include_never_public=True,
        ),
    }

    if include_private:
        private_payload["embed_policy"] = _serialize_policy(policy)
        private_payload["publication_extra"] = _publication_extra(policy)

    return private_payload


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass
class ProjectPublicationServiceResult:
    ok: bool
    code: str
    message: str = ""
    project: Any = None
    policy: Any = None
    publication: Dict[str, Any] = field(default_factory=dict)
    access: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    error: Optional[str] = None

    def to_dict(self, include_private: bool = False, for_public: bool = False) -> Dict[str, Any]:
        result = {
            "ok": bool(self.ok),
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "publication": self.publication,
            "access": self.access,
            "data": self.data,
            "error": self.error,
        }

        try:
            if self.project is not None:
                result["project_id"] = _project_id(self.project)
                result["project_public_id"] = _project_public_id(self.project)
        except Exception:
            pass

        if include_private and self.policy is not None and not for_public:
            result["embed_policy"] = _serialize_policy(self.policy)

        return result


def _result(
    ok: bool,
    code: str,
    message: str = "",
    status_code: int = 200,
    **kwargs: Any,
) -> ProjectPublicationServiceResult:
    return ProjectPublicationServiceResult(
        ok=ok,
        code=code,
        message=message,
        status_code=status_code,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _write_audit_event(
    project: Any,
    action: str,
    actor_user_id: Any = None,
    message: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    if ProjectAuditEvent is None:
        return

    project_id = _project_id(project)
    if not project_id:
        return

    payload = _safe_dict(metadata)
    payload.setdefault("source", "project_publication_service")

    kwargs = {
        "project_id": project_id,
        "category": AUDIT_CATEGORY_PUBLICATION,
        "action": _safe_str(action, max_len=120),
        "actor_user_id": _safe_int(actor_user_id),
        "message": _safe_str(message, max_len=1000),
        "metadata_json": payload,
    }

    try:
        event = ProjectAuditEvent(**kwargs)
    except Exception:
        try:
            event = ProjectAuditEvent()
            for key, value in kwargs.items():
                try:
                    setattr(event, key, value)
                except Exception:
                    pass
        except Exception:
            return

    try:
        _session_add(event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------


class ProjectPublicationService:
    """
    Zustandsloser Service für Sichtbarkeit und Veröffentlichung von Workspaces.
    """

    def status(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "project_publication_service",
            "project_model_available": Project is not None,
            "embed_policy_model_available": ProjectEmbedPolicy is not None,
            "audit_model_available": ProjectAuditEvent is not None,
            "valid_visibilities": sorted(VALID_PUBLICATION_VISIBILITIES),
            "publication_workspaces": list(PUBLICATION_WORKSPACES),
            "never_public_workspaces": sorted(NEVER_PUBLIC_WORKSPACES),
            "cache_ttl_seconds": _cache_ttl_seconds(),
        }

    def get_publication(
        self,
        project_or_id: Any,
        actor_user_id: Any = None,
        include_private: bool = False,
        for_public: bool = False,
        use_cache: bool = True,
    ) -> ProjectPublicationServiceResult:
        project = resolve_project(project_or_id)
        if project is None:
            return _result(
                ok=False,
                code="project_not_found",
                message="Projekt nicht gefunden.",
                status_code=404,
            )

        actor_context = get_actor_context(actor_user_id)
        access = _access_payload(project, actor_context)

        if include_private and not access.get("can_manage"):
            return _result(
                ok=False,
                code="project_permission_denied",
                message="Du hast keine Berechtigung, Veröffentlichungseinstellungen dieses Projekts zu sehen.",
                project=project,
                access=access,
                status_code=403,
            )

        if not for_public and not include_private and not access.get("can_view") and not access.get("can_manage"):
            # Normale interne Route: mindestens view.
            # Öffentliche Routen sollen for_public=True verwenden.
            return _result(
                ok=False,
                code="project_permission_denied",
                message="Du hast keine Berechtigung, dieses Projekt zu sehen.",
                project=project,
                access=access,
                status_code=403,
            )

        cache_key = (
            f"publication:{_project_id(project)}:"
            f"actor={_actor_user_id(actor_context)}:"
            f"private={int(include_private)}:"
            f"public={int(for_public)}"
        )

        if use_cache:
            hit, cached = _PUBLICATION_CACHE.get(cache_key)
            if hit and isinstance(cached, ProjectPublicationServiceResult):
                return cached

        policy = get_or_create_publication_policy(project)
        publication = build_publication_payload(
            project=project,
            policy=policy,
            actor_context=actor_context,
            include_private=include_private,
            for_public=for_public,
        )

        result = _result(
            ok=True,
            code="project_publication_loaded",
            message="Veröffentlichungseinstellungen wurden geladen.",
            project=project,
            policy=policy,
            publication=publication,
            access=access,
            status_code=200,
        )

        if use_cache:
            _PUBLICATION_CACHE.set(cache_key, result, _cache_ttl_seconds())

        return result

    def update_publication(
        self,
        project_or_id: Any,
        data: Optional[Mapping[str, Any]],
        actor_user_id: Any = None,
        commit: bool = True,
    ) -> ProjectPublicationServiceResult:
        """
        Aktualisiert Sichtbarkeit und veröffentlichte Reiter.

        Nur can_manage darf diese Aktion ausführen.
        Demo-Modus wird abgelehnt, weil Veröffentlichung eine persistente
        Projekteinstellung ist.
        """
        project = resolve_project(project_or_id)
        if project is None:
            return _result(
                ok=False,
                code="project_not_found",
                message="Projekt nicht gefunden.",
                status_code=404,
            )

        actor_context = get_actor_context(actor_user_id)
        actor_id = _actor_user_id(actor_context)
        access = _access_payload(project, actor_context)

        if _actor_is_demo(actor_context):
            return _result(
                ok=False,
                code="demo_mode_not_allowed",
                message="Im Demo-Modus können keine Veröffentlichungseinstellungen gespeichert werden.",
                project=project,
                access=access,
                status_code=403,
                data={"demo_mode": True},
            )

        if not access.get("can_manage"):
            return _result(
                ok=False,
                code="project_permission_denied",
                message="Du hast keine Berechtigung, Veröffentlichungseinstellungen zu ändern.",
                project=project,
                access=access,
                status_code=403,
            )

        payload = _safe_dict(data)

        old_visibility = _visibility_from_project(project)

        visibility = normalize_publication_visibility(
            payload.get("visibility", old_visibility),
            default=old_visibility,
        )

        policy = get_or_create_publication_policy(project)
        if policy is None:
            return _result(
                ok=False,
                code="project_embed_policy_unavailable",
                message="Embed-/Veröffentlichungspolicy konnte nicht geladen werden.",
                project=project,
                access=access,
                status_code=500,
            )

        existing_desired = _desired_workspaces_from_policy(policy)
        published_payload = (
            payload.get("published_workspaces")
            or payload.get("publishedWorkspaces")
            or payload.get("workspaces")
            or payload.get("tabs")
            or payload.get("published_tabs")
        )

        desired_workspaces = normalize_published_workspaces(
            published_payload,
            existing=existing_desired,
            default=DEFAULT_DESIRED_WORKSPACES,
        )

        require_auth_raw = payload.get("require_auth")
        if require_auth_raw is None:
            require_auth_raw = payload.get("requireAuth")

        require_permission_raw = payload.get("require_project_permission")
        if require_permission_raw is None:
            require_permission_raw = payload.get("requireProjectPermission")

        if visibility == VISIBILITY_PRIVATE:
            require_auth = True
            require_project_permission = True
        else:
            # Für unlisted/public ist default: veröffentlichte Reiter ohne
            # Projektmitgliedschaft sichtbar. Auth kann später optional erzwungen werden.
            require_auth = _safe_bool(require_auth_raw, default=False)
            require_project_permission = _safe_bool(require_permission_raw, default=False)

        try:
            _apply_visibility_to_project(project, visibility)

            publication_extra = _apply_policy(
                policy=policy,
                visibility=visibility,
                desired_workspaces=desired_workspaces,
                require_auth=require_auth,
                require_project_permission=require_project_permission,
            )

            _write_audit_event(
                project,
                AUDIT_ACTION_PUBLICATION_UPDATED,
                actor_user_id=actor_id,
                message="Project publication settings updated.",
                metadata={
                    "old_visibility": old_visibility,
                    "new_visibility": visibility,
                    "published_workspaces": desired_workspaces,
                    "effective_published_workspaces": publication_extra.get("effective_published_workspaces"),
                    "require_auth": require_auth,
                    "require_project_permission": require_project_permission,
                },
            )

            if old_visibility != visibility:
                _write_audit_event(
                    project,
                    AUDIT_ACTION_VISIBILITY_UPDATED,
                    actor_user_id=actor_id,
                    message="Project visibility updated.",
                    metadata={
                        "old_visibility": old_visibility,
                        "new_visibility": visibility,
                    },
                )

            _session_add(project)
            _session_add(policy)
            _commit_or_flush(commit=commit)

            clear_project_publication_cache(_project_id(project))

            publication = build_publication_payload(
                project=project,
                policy=policy,
                actor_context=actor_context,
                include_private=True,
                for_public=False,
            )

            return _result(
                ok=True,
                code="project_publication_updated",
                message="Veröffentlichungseinstellungen wurden gespeichert.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=200,
            )

        except Exception as exc:
            _rollback_safely()
            _log_exception(
                "update_publication failed",
                project_id=_project_id(project),
                visibility=visibility,
            )
            return _result(
                ok=False,
                code="project_publication_update_failed",
                message="Veröffentlichungseinstellungen konnten nicht gespeichert werden.",
                project=project,
                policy=policy,
                access=access,
                status_code=500,
                error=str(exc),
            )

    def set_visibility(
        self,
        project_or_id: Any,
        visibility: Any,
        actor_user_id: Any = None,
        commit: bool = True,
    ) -> ProjectPublicationServiceResult:
        """
        Convenience-Methode nur für Sichtbarkeit.
        Bestehende Reiter-Veröffentlichungen bleiben erhalten.
        """
        project = resolve_project(project_or_id)
        if project is None:
            return _result(
                ok=False,
                code="project_not_found",
                message="Projekt nicht gefunden.",
                status_code=404,
            )

        policy = get_or_create_publication_policy(project)
        existing = _desired_workspaces_from_policy(policy)

        return self.update_publication(
            project_or_id=project,
            data={
                "visibility": visibility,
                "published_workspaces": existing,
            },
            actor_user_id=actor_user_id,
            commit=commit,
        )

    def can_access_workspace(
        self,
        project_or_id: Any,
        workspace: Any,
        actor_user_id: Any = None,
        public_request: bool = False,
    ) -> ProjectPublicationServiceResult:
        """
        Prüft, ob ein Workspace für den aktuellen Kontext zugänglich ist.

        Intern:
          Projektmitglieder mit view dürfen grundsätzlich fachliche Reiter sehen.
          Admin/Team/System brauchen manage.

        Öffentlich/ungelistet:
          Nur veröffentlichte Reiter.
          Admin/Team/System nie.
        """
        project = resolve_project(project_or_id)
        if project is None:
            return _result(
                ok=False,
                code="project_not_found",
                message="Projekt nicht gefunden.",
                status_code=404,
            )

        workspace_key = normalize_workspace_key(workspace)
        actor_context = get_actor_context(actor_user_id)
        access = _access_payload(project, actor_context)

        if not workspace_key:
            return _result(
                ok=False,
                code="workspace_unknown",
                message="Unbekannter Workspace.",
                project=project,
                access=access,
                status_code=404,
                data={"workspace": _safe_str(workspace)},
            )

        if workspace_key in NEVER_PUBLIC_WORKSPACES:
            if access.get("can_manage") and not public_request:
                return _result(
                    ok=True,
                    code="workspace_access_allowed_manage",
                    message="Workspace ist für Projektverwaltung zugänglich.",
                    project=project,
                    access=access,
                    status_code=200,
                    data={"workspace": workspace_key},
                )

            return _result(
                ok=False,
                code="workspace_never_public",
                message="Dieser Workspace kann nicht öffentlich oder ohne Verwaltungsrechte geöffnet werden.",
                project=project,
                access=access,
                status_code=403,
                data={"workspace": workspace_key},
            )

        if workspace_key not in PUBLICATION_WORKSPACES:
            return _result(
                ok=False,
                code="workspace_not_publishable",
                message="Dieser Workspace ist nicht als veröffentlichbarer Reiter bekannt.",
                project=project,
                access=access,
                status_code=404,
                data={"workspace": workspace_key},
            )

        policy = get_or_create_publication_policy(project)
        publication = build_publication_payload(
            project=project,
            policy=policy,
            actor_context=actor_context,
            include_private=False,
            for_public=public_request,
        )

        visibility = publication.get("visibility") or publication.get("publication", {}).get("visibility")
        visibility = normalize_publication_visibility(visibility)

        effective = _safe_dict(
            publication.get("effective_published_workspaces")
            or publication.get("publication", {}).get("effective_published_workspaces")
        )

        require_auth = _safe_bool(
            publication.get("require_auth")
            or publication.get("publication", {}).get("require_auth"),
            default=visibility == VISIBILITY_PRIVATE,
        )
        require_project_permission = _safe_bool(
            publication.get("require_project_permission")
            or publication.get("publication", {}).get("require_project_permission"),
            default=visibility == VISIBILITY_PRIVATE,
        )

        if access.get("can_view") and not public_request:
            return _result(
                ok=True,
                code="workspace_access_allowed_member",
                message="Workspace ist für Projektmitglied sichtbar.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=200,
                data={
                    "workspace": workspace_key,
                    "access_source": "project_permission",
                },
            )

        if visibility == VISIBILITY_PRIVATE:
            return _result(
                ok=False,
                code="workspace_private",
                message="Dieses Projekt ist privat.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=403,
                data={"workspace": workspace_key},
            )

        if require_auth and not _actor_is_authenticated(actor_context):
            return _result(
                ok=False,
                code="authentication_required",
                message="Für diesen veröffentlichten Bereich ist Login erforderlich.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=401,
                data={"workspace": workspace_key},
            )

        if require_project_permission and not access.get("can_view"):
            return _result(
                ok=False,
                code="project_permission_required",
                message="Für diesen Bereich ist Projektberechtigung erforderlich.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=403,
                data={"workspace": workspace_key},
            )

        if not _safe_bool(effective.get(workspace_key), default=False):
            return _result(
                ok=False,
                code="workspace_not_published",
                message="Dieser Workspace ist nicht veröffentlicht.",
                project=project,
                policy=policy,
                publication=publication,
                access=access,
                status_code=403,
                data={"workspace": workspace_key},
            )

        return _result(
            ok=True,
            code="workspace_access_allowed_publication",
            message="Workspace ist über Veröffentlichung sichtbar.",
            project=project,
            policy=policy,
            publication=publication,
            access=access,
            status_code=200,
            data={
                "workspace": workspace_key,
                "access_source": "publication",
                "visibility": visibility,
            },
        )


# ---------------------------------------------------------------------------
# Singleton / module-level API
# ---------------------------------------------------------------------------

_SERVICE_SINGLETON: Optional[ProjectPublicationService] = None
_SERVICE_LOCK = threading.RLock()


def get_project_publication_service(refresh: bool = False) -> ProjectPublicationService:
    global _SERVICE_SINGLETON

    try:
        with _SERVICE_LOCK:
            if refresh or _SERVICE_SINGLETON is None:
                _SERVICE_SINGLETON = ProjectPublicationService()
            return _SERVICE_SINGLETON
    except Exception:
        return ProjectPublicationService()


def get_project_publication_service_status() -> Dict[str, Any]:
    try:
        return get_project_publication_service().status()
    except Exception as exc:
        return {
            "ok": False,
            "code": "project_publication_service_status_failed",
            "error": str(exc),
        }


def get_project_publication(
    project_or_id: Any,
    actor_user_id: Any = None,
    include_private: bool = False,
    for_public: bool = False,
    use_cache: bool = True,
) -> Dict[str, Any]:
    result = get_project_publication_service().get_publication(
        project_or_id=project_or_id,
        actor_user_id=actor_user_id,
        include_private=include_private,
        for_public=for_public,
        use_cache=use_cache,
    )
    return result.to_dict(include_private=include_private, for_public=for_public)


def update_project_publication(
    project_or_id: Any,
    data: Optional[Mapping[str, Any]],
    actor_user_id: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_publication_service().update_publication(
        project_or_id=project_or_id,
        data=data,
        actor_user_id=actor_user_id,
        commit=commit,
    )
    return result.to_dict(include_private=True, for_public=False)


def set_project_visibility(
    project_or_id: Any,
    visibility: Any,
    actor_user_id: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_publication_service().set_visibility(
        project_or_id=project_or_id,
        visibility=visibility,
        actor_user_id=actor_user_id,
        commit=commit,
    )
    return result.to_dict(include_private=True, for_public=False)


def can_access_project_workspace(
    project_or_id: Any,
    workspace: Any,
    actor_user_id: Any = None,
    public_request: bool = False,
) -> Dict[str, Any]:
    result = get_project_publication_service().can_access_workspace(
        project_or_id=project_or_id,
        workspace=workspace,
        actor_user_id=actor_user_id,
        public_request=public_request,
    )
    return result.to_dict(include_private=False, for_public=public_request)


__all__ = [
    "DEFAULT_DESIRED_WORKSPACES",
    "NEVER_PUBLIC_WORKSPACES",
    "PUBLICATION_WORKSPACES",
    "ProjectPublicationService",
    "ProjectPublicationServiceResult",
    "VALID_PUBLICATION_VISIBILITIES",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_UNLISTED",
    "WORKSPACE_ADMIN",
    "WORKSPACE_CAD2D",
    "WORKSPACE_EDITOR3D",
    "WORKSPACE_LV",
    "WORKSPACE_MAP",
    "WORKSPACE_PROJECT",
    "WORKSPACE_VERSIONS",
    "build_publication_payload",
    "build_workspace_publication_items",
    "can_access_project_workspace",
    "clear_project_publication_cache",
    "effective_published_workspaces",
    "get_or_create_publication_policy",
    "get_project_publication",
    "get_project_publication_service",
    "get_project_publication_service_status",
    "is_never_public_workspace",
    "is_publication_workspace",
    "normalize_publication_visibility",
    "normalize_published_workspaces",
    "normalize_workspace_key",
    "publication_enabled",
    "resolve_project",
    "set_project_visibility",
    "update_project_publication",
    "workspace_label",
]