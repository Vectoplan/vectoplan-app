# services/vectoplan-app/services/project_service.py
"""
VECTOPLAN project service.

Zweck:
- Zentrale Projektverwaltungslogik für vectoplan-app.
- Erstellt und aktualisiert App-Projekte.
- Verwaltet Projekt-Stammdaten, einfache Adressbox, Sichtbarkeit, Soft Delete,
  Besitzübertragung, Service-Links, zentrale Version-Links und Chunk-Referenzen.
- Verknüpft App-Projekte serverseitig mit vectoplan-chunk-Projekten.
- Nutzt vectoplan-auth als Wahrheit für Loginstatus, User-ID, Blocked/Banned,
  Account, Plan und Entitlements.
- Nutzt AppUser nur als lokalen Foreign-Key-Link innerhalb vectoplan-app.
- Unterstützt ein temporäres Demo-Projekt für Guests mit demo_project_access.
- Erzeugt keine echten Benutzeraccounts.
- Enthält keinen Default-User, keinen Dev-Placeholder und keinen Fallback auf id=1.

Projektformular-Zielzustand:
- Sichtbar im UI:
    name
    description
    address_text
    visibility = private | unlisted | public
- Nicht mehr sichtbar im normalen Projektformular:
    street
    house_number
    postal_code
    city
    region
    country
    latitude
    longitude
    coordinate_srid
    Systemreferenzen
- Diese technischen/strukturierten Felder bleiben im Model erhalten, damit der
  spätere Geocoder sie setzen kann.

Wichtig:
- vectoplan-app verwaltet Projekt-Metadaten, Rechte, Sichtbarkeit,
  Veröffentlichungen und Workspace-Shell.
- vectoplan-auth verwaltet Login, Account, Plan, Subscription, Blocked/Banned,
  Rollen und Entitlements.
- vectoplan-chunk verwaltet Chunk-Projekt, Universe, WorldInstance, Snapshots,
  Command Logs und Chunk Events.
- Externe Microservices werden in vectoplan-app nur referenziert.
- App und Chunk haben getrennte Datenbanken.
- Es gibt keine verteilte Transaktion zwischen App-DB und Chunk-DB.
"""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set


try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False


try:
    from sqlalchemy import or_
except Exception:  # pragma: no cover
    or_ = None  # type: ignore


try:
    from extensions import db
except Exception as exc:  # pragma: no cover
    raise RuntimeError("project_service requires extensions.db") from exc


try:
    from models import (
        AppUser,
        Conversation,
        Project,
        ProjectAuditEvent,
        ProjectEmbedPolicy,
        ProjectMembership,
        ProjectServiceLink,
        ProjectVersion,
        build_embed_policy as model_build_embed_policy,
        build_membership as model_build_membership,
        create_project_version as model_create_project_version,
        get_project_membership as model_get_project_membership,
        normalize_resource_type as model_normalize_resource_type,
        normalize_service as model_normalize_service,
        normalize_version_kind as model_normalize_version_kind,
        normalize_version_status as model_normalize_version_status,
        record_project_audit_event as model_record_project_audit_event,
        safe_bool as model_safe_bool,
        safe_dict as model_safe_dict,
        safe_float as model_safe_float,
        safe_int as model_safe_int,
        safe_list as model_safe_list,
        safe_str as model_safe_str,
        serialize_embed_policy as model_serialize_embed_policy,
        serialize_membership as model_serialize_membership,
        serialize_project_version as model_serialize_project_version,
        serialize_service_link as model_serialize_service_link,
        utcnow as model_utcnow,
    )
except Exception as exc:  # pragma: no cover
    raise RuntimeError("project_service requires modular models package") from exc


try:
    from services.current_user import (
        get_current_user,
        get_current_user_context,
        get_current_user_id_optional,
        get_platform_auth_context,
        require_persistent_current_user,
        serialize_current_user,
    )
except Exception:  # pragma: no cover
    get_current_user = None  # type: ignore
    serialize_current_user = None  # type: ignore
    get_platform_auth_context = None  # type: ignore

    def get_current_user_id_optional() -> Optional[int]:  # type: ignore
        return None

    def get_current_user_context(*args: Any, **kwargs: Any) -> Any:  # type: ignore
        return {
            "user_id": None,
            "id": None,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "blocked_reason": "current_user_unavailable",
            "source": "fallback_blocked",
        }

    def require_persistent_current_user() -> Any:  # type: ignore
        raise RuntimeError("persistent user required")


try:
    from services.project_permissions import (
        PERMISSION_DELETE,
        PERMISSION_EDIT,
        PERMISSION_EMBED,
        PERMISSION_MANAGE,
        PERMISSION_TRANSFER,
        PERMISSION_VIEW,
        PermissionDenied,
        can_manage_project,
        can_view_project,
        get_project_permission_result as permissions_get_project_permission_result,
        grant_project_role as permissions_grant_project_role,
        normalize_role as permissions_normalize_role,
        require_project_permission as permissions_require_project_permission,
        revoke_project_membership as permissions_revoke_project_membership,
        serialize_membership as permissions_serialize_membership,
        serialize_project_permissions as permissions_serialize_project_permissions,
        transfer_project_ownership as permissions_transfer_project_ownership,
    )
except Exception:  # pragma: no cover
    PERMISSION_VIEW = "view"
    PERMISSION_EDIT = "edit"
    PERMISSION_MANAGE = "manage"
    PERMISSION_DELETE = "delete"
    PERMISSION_TRANSFER = "transfer"
    PERMISSION_EMBED = "embed"

    class PermissionDenied(Exception):  # type: ignore
        def __init__(
            self,
            message: str = "missing project permission",
            *,
            code: str = "project_permission_denied",
            status_code: int = 403,
            permission: str = "",
            project_id: Any = None,
            user_id: Any = None,
        ) -> None:
            super().__init__(message)
            self.message = message
            self.code = code
            self.status_code = status_code
            self.permission = permission
            self.project_id = project_id
            self.user_id = user_id

        def to_dict(self) -> Dict[str, Any]:
            return {
                "ok": False,
                "code": self.code,
                "error": self.message,
                "message": self.message,
                "permission": self.permission,
                "project_id": self.project_id,
                "user_id": self.user_id,
                "status_code": self.status_code,
            }

    permissions_get_project_permission_result = None  # type: ignore
    permissions_require_project_permission = None  # type: ignore
    permissions_serialize_project_permissions = None  # type: ignore
    permissions_serialize_membership = None  # type: ignore
    permissions_grant_project_role = None  # type: ignore
    permissions_revoke_project_membership = None  # type: ignore
    permissions_transfer_project_ownership = None  # type: ignore
    permissions_normalize_role = None  # type: ignore
    can_view_project = None  # type: ignore
    can_manage_project = None  # type: ignore


try:
    from services.project_publication_service import (
        VISIBILITY_PRIVATE as PUB_VISIBILITY_PRIVATE,
        VISIBILITY_PUBLIC as PUB_VISIBILITY_PUBLIC,
        VISIBILITY_UNLISTED as PUB_VISIBILITY_UNLISTED,
        get_project_publication,
        normalize_publication_visibility,
        set_project_visibility as publication_set_project_visibility,
    )
except Exception:  # pragma: no cover
    PUB_VISIBILITY_PRIVATE = "private"
    PUB_VISIBILITY_PUBLIC = "public"
    PUB_VISIBILITY_UNLISTED = "unlisted"
    get_project_publication = None  # type: ignore
    publication_set_project_visibility = None  # type: ignore

    def normalize_publication_visibility(value: Any, default: str = "private") -> str:  # type: ignore
        text = str(value or default).strip().lower().replace("-", "_")
        if text in {"private", "privat", "shared"}:
            return "private"
        if text in {"unlisted", "not_listed", "nicht_gelistet", "link"}:
            return "unlisted"
        if text in {"public", "öffentlich", "oeffentlich", "open"}:
            return "public"
        return default


try:
    from services.demo_project_service import (
        cleanup_expired_demo_projects,
        demo_project_public_payload,
        ensure_demo_project_for_context,
        get_current_demo_project,
        is_demo_project as service_is_demo_project,
    )
except Exception:  # pragma: no cover
    cleanup_expired_demo_projects = None  # type: ignore
    demo_project_public_payload = None  # type: ignore
    ensure_demo_project_for_context = None  # type: ignore
    get_current_demo_project = None  # type: ignore
    service_is_demo_project = None  # type: ignore


try:
    from services.chunk_client import (
        ChunkClientError,
        apply_chunk_refs_to_project,
        build_chunk_project_payload,
        ensure_chunk_project_for_project,
        extract_chunk_refs,
        get_chunk_health,
        get_chunk_project_for_app_project_id,
        is_chunk_provisioning_enabled,
        is_chunk_provisioning_required,
        preview_chunk_project_for_app_project_id,
    )
except Exception:  # pragma: no cover
    ChunkClientError = Exception  # type: ignore
    apply_chunk_refs_to_project = None  # type: ignore
    build_chunk_project_payload = None  # type: ignore
    ensure_chunk_project_for_project = None  # type: ignore
    extract_chunk_refs = None  # type: ignore
    get_chunk_health = None  # type: ignore
    get_chunk_project_for_app_project_id = None  # type: ignore
    is_chunk_provisioning_enabled = None  # type: ignore
    is_chunk_provisioning_required = None  # type: ignore
    preview_chunk_project_for_app_project_id = None  # type: ignore


try:
    from services.project_chunk_provisioning_service import (
        ProjectChunkProvisioningError,
        ProjectChunkProvisioningResult,
        provision_project_chunk_graph,
        retry_project_chunk_provisioning,
        serialize_project_chunk_provisioning_status,
    )
except Exception:  # pragma: no cover
    ProjectChunkProvisioningError = RuntimeError  # type: ignore
    ProjectChunkProvisioningResult = None  # type: ignore
    provision_project_chunk_graph = None  # type: ignore
    retry_project_chunk_provisioning = None  # type: ignore
    serialize_project_chunk_provisioning_status = None  # type: ignore


try:
    from services.project_chunk_access_sync_service import (
        ProjectChunkAccessSyncError,
        serialize_project_chunk_access_sync_status,
        sync_project_chunk_access,
    )
except Exception:  # pragma: no cover
    ProjectChunkAccessSyncError = RuntimeError  # type: ignore
    serialize_project_chunk_access_sync_status = None  # type: ignore
    sync_project_chunk_access = None  # type: ignore


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_DELETED = "deleted"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUS_EXPIRED = "expired"

PROJECT_SETUP_DRAFT = "draft"
PROJECT_SETUP_DEFINED = "defined"
PROJECT_SETUP_CONFIGURED = "configured"

PROJECT_VISIBILITY_PRIVATE = "private"
PROJECT_VISIBILITY_PUBLIC = "public"
PROJECT_VISIBILITY_UNLISTED = "unlisted"
PROJECT_VISIBILITY_SHARED = "shared"

PROJECT_SCOPE_PERSONAL = "personal"
PROJECT_SCOPE_ACCOUNT = "account"
PROJECT_SCOPE_DEMO = "demo"

DEFAULT_PROJECT_NAME = "Neues Projekt"
DEFAULT_ADDRESS_COUNTRY = "DE"
DEFAULT_COORDINATE_SRID = "EPSG:4326"

GEOCODE_STATUS_NONE = "none"
GEOCODE_STATUS_PENDING = "pending"
GEOCODE_STATUS_STALE = "stale"
GEOCODE_STATUS_RESOLVED = "resolved"
GEOCODE_STATUS_FAILED = "failed"

SERVICE_CHUNK = "chunk"
SERVICE_EDITOR = "editor3d"
SERVICE_OPENLAYER = "openlayer"
SERVICE_APP = "app"
SERVICE_2D = "cad2d"
SERVICE_LV = "lv"
SERVICE_LIBRARY = "library"

RESOURCE_CHUNK_PROJECT = "chunk_project"
RESOURCE_CHUNK_UNIVERSE = "universe"
RESOURCE_CHUNK_WORLD = "world"
RESOURCE_PLAN2D = "plan2d"
RESOURCE_LV = "lv"
RESOURCE_EDITOR_STATE = "editor_state"
RESOURCE_APP_PROJECT = "project"

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

CHUNK_STATUS_DISABLED = "disabled"
CHUNK_STATUS_PENDING = "pending"
CHUNK_STATUS_READY = "ready"
CHUNK_STATUS_ERROR = "error"

CHUNK_PROVISIONING_PENDING = "pending"
CHUNK_PROVISIONING_PROVISIONING = "provisioning"
CHUNK_PROVISIONING_READY = "ready"
CHUNK_PROVISIONING_FALLBACK_READY = "fallback_ready"
CHUNK_PROVISIONING_FAILED = "failed"
CHUNK_PROVISIONING_REPAIR_REQUIRED = "repair_required"
CHUNK_PROVISIONING_DISABLED = "disabled"

CHUNK_ACCESS_SYNC_PENDING = "pending"
CHUNK_ACCESS_SYNC_SYNCING = "syncing"
CHUNK_ACCESS_SYNC_READY = "ready"
CHUNK_ACCESS_SYNC_FAILED = "failed"
CHUNK_ACCESS_SYNC_REPAIR_REQUIRED = "repair_required"
CHUNK_ACCESS_SYNC_DISABLED = "disabled"

CHUNK_WORLD_TEMPLATE_EARTH = "earth"
CHUNK_WORLD_TEMPLATE_FLAT = "flat"


# ─────────────────────────────────────────────────────────────
# Result / permission objects
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProjectOperationResult:
    ok: bool
    project: Optional[Any] = None
    payload: Optional[Dict[str, Any]] = None
    status_code: int = 200
    code: str = "ok"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        body = dict(self.payload or {})
        body.setdefault("ok", bool(self.ok))
        body.setdefault("code", self.code)
        body.setdefault("status_code", self.status_code)

        if self.project is not None and "project" not in body:
            try:
                body["project"] = serialize_project(self.project)
            except Exception:
                pass

        if self.error:
            body["error"] = self.error
            body.setdefault("message", self.error)

        return body


@dataclass(frozen=True)
class ProjectPermissionResult:
    can_view: bool = False
    can_edit: bool = False
    can_manage: bool = False
    can_delete: bool = False
    can_transfer: bool = False
    can_embed: bool = False
    can_view_settings: bool = False
    can_manage_settings: bool = False
    can_view_team: bool = False
    can_manage_team: bool = False
    can_view_admin: bool = False
    role: str = ROLE_VIEWER
    source: str = "none"

    @property
    def permissions(self) -> Dict[str, bool]:
        return {
            PERMISSION_VIEW: bool(self.can_view),
            PERMISSION_EDIT: bool(self.can_edit),
            PERMISSION_MANAGE: bool(self.can_manage),
            PERMISSION_DELETE: bool(self.can_delete),
            PERMISSION_TRANSFER: bool(self.can_transfer),
            PERMISSION_EMBED: bool(self.can_embed),
            "view_settings": bool(self.can_view_settings),
            "manage_settings": bool(self.can_manage_settings),
            "view_team": bool(self.can_view_team),
            "manage_team": bool(self.can_manage_team),
            "view_admin": bool(self.can_view_admin),
        }

    def can(self, permission: str) -> bool:
        return bool(self.permissions.get(permission, False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "source": self.source,
            "permissions": self.permissions,
            "can_view": self.can_view,
            "can_edit": self.can_edit,
            "can_manage": self.can_manage,
            "can_delete": self.can_delete,
            "can_transfer": self.can_transfer,
            "can_embed": self.can_embed,
            "can_view_settings": self.can_view_settings,
            "can_manage_settings": self.can_manage_settings,
            "can_view_team": self.can_view_team,
            "can_manage_team": self.can_manage_team,
            "can_view_admin": self.can_view_admin,
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _utcnow() -> Any:
    try:
        return model_utcnow()
    except Exception:
        from datetime import datetime
        return datetime.utcnow()


def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        return model_safe_str(value, default, max_len)
    except Exception:
        try:
            text = str(value if value is not None else default).strip()
            if not text:
                text = default
            return text[:max_len] if max_len > 0 else text
        except Exception:
            return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return model_safe_int(value, default)
    except Exception:
        try:
            if value is None or isinstance(value, bool):
                return default
            return int(str(value).strip())
        except Exception:
            return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return model_safe_float(value, default)
    except Exception:
        try:
            if value is None or value == "":
                return default
            return float(str(value).strip().replace(",", "."))
        except Exception:
            return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        return model_safe_bool(value, default)
    except Exception:
        try:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            text = str(value if value is not None else "").strip().lower()
            if text in {"1", "true", "yes", "y", "on", "ja"}:
                return True
            if text in {"0", "false", "no", "n", "off", "nein"}:
                return False
            return default
        except Exception:
            return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        return model_safe_dict(value)
    except Exception:
        try:
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, Mapping):
                return dict(value)
            if hasattr(value, "to_dict") and callable(value.to_dict):
                return dict(value.to_dict())
            return {}
        except Exception:
            return {}


def _safe_list(value: Any) -> List[Any]:
    try:
        return model_safe_list(value)
    except Exception:
        try:
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, set):
                return list(value)
            return []
        except Exception:
            return []


def _iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


def _config_value(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(name, default)
            if value is not None:
                return value
    except Exception:
        pass

    return default


def _config_bool(name: str, default: bool = False) -> bool:
    return _safe_bool(_config_value(name, default), default)


def _config_str(name: str, default: str = "", max_len: int = 4000) -> str:
    return _safe_str(_config_value(name, default), default, max_len)


def _normalize_slug(value: Any, default: str = "item", max_len: int = 80) -> str:
    try:
        text = _safe_str(value, default, max_len).lower()
        out: List[str] = []
        last_sep = False

        for char in text:
            if char.isalnum():
                out.append(char)
                last_sep = False
            elif char in {"-", "_"}:
                out.append(char)
                last_sep = False
            elif not last_sep:
                out.append("_")
                last_sep = True

        result = "".join(out).strip("_-") or default

        if max_len > 0 and len(result) > max_len:
            result = result[:max_len].strip("_-")

        return result or default

    except Exception:
        return default


def _normalize_visibility(value: Any, default: str = PROJECT_VISIBILITY_PRIVATE) -> str:
    try:
        normalized = normalize_publication_visibility(value, default)
        if normalized in {PROJECT_VISIBILITY_PRIVATE, PROJECT_VISIBILITY_UNLISTED, PROJECT_VISIBILITY_PUBLIC}:
            return normalized

        return default
    except Exception:
        try:
            text = _normalize_slug(value, default, 40)
            aliases = {
                "private": PROJECT_VISIBILITY_PRIVATE,
                "privat": PROJECT_VISIBILITY_PRIVATE,
                "shared": PROJECT_VISIBILITY_PRIVATE,
                "geteilt": PROJECT_VISIBILITY_PRIVATE,
                "unlisted": PROJECT_VISIBILITY_UNLISTED,
                "not_listed": PROJECT_VISIBILITY_UNLISTED,
                "nicht_gelistet": PROJECT_VISIBILITY_UNLISTED,
                "link": PROJECT_VISIBILITY_UNLISTED,
                "public": PROJECT_VISIBILITY_PUBLIC,
                "öffentlich": PROJECT_VISIBILITY_PUBLIC,
                "oeffentlich": PROJECT_VISIBILITY_PUBLIC,
                "open": PROJECT_VISIBILITY_PUBLIC,
            }
            return aliases.get(text, default)
        except Exception:
            return default


def _normalize_status(value: Any, default: str = PROJECT_STATUS_ACTIVE) -> str:
    try:
        text = _normalize_slug(value, default, 40)

        if text in {
            PROJECT_STATUS_ACTIVE,
            PROJECT_STATUS_DELETED,
            PROJECT_STATUS_ARCHIVED,
            PROJECT_STATUS_EXPIRED,
            CHUNK_STATUS_DISABLED,
            CHUNK_STATUS_PENDING,
            CHUNK_STATUS_READY,
            CHUNK_STATUS_ERROR,
            "inactive",
            "disabled",
            "pending",
            "draft",
            "stored",
            "complete",
            "published",
        }:
            return text

        return default

    except Exception:
        return default


def _query_limit(value: Any, default: int = 100, max_value: int = 500) -> int:
    try:
        parsed = _safe_int(value, default)
        if parsed <= 0:
            return default
        if parsed > max_value:
            return max_value
        return parsed
    except Exception:
        return default


def _log_warning(message: str, *args: Any) -> None:
    try:
        if has_app_context() and current_app is not None:
            current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        if has_app_context() and current_app is not None:
            if exc is not None:
                current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
            else:
                current_app.logger.exception(message)
    except Exception:
        pass


def _db_add(obj: Any) -> None:
    try:
        db.session.add(obj)
    except Exception:
        pass


def _db_flush_or_commit(commit: bool = True) -> None:
    if commit:
        db.session.commit()
    else:
        db.session.flush()


def _db_rollback_safely() -> None:
    try:
        db.session.rollback()
    except Exception:
        pass


def _project_public_id(project: Any) -> str:
    try:
        return _safe_str(getattr(project, "public_id", None), "", 160) or _safe_str(getattr(project, "id", None), "", 160)
    except Exception:
        return ""


def _get_project_metadata(project: Any) -> Dict[str, Any]:
    try:
        return _safe_dict(getattr(project, "metadata_json", {}))
    except Exception:
        return {}


def _set_project_metadata(project: Any, metadata: Dict[str, Any]) -> None:
    try:
        if hasattr(project, "metadata_json"):
            project.metadata_json = _safe_dict(metadata)
    except Exception:
        pass


def _merge_project_metadata(project: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _get_project_metadata(project)
    metadata.update(_safe_dict(values))
    _set_project_metadata(project, metadata)
    return metadata


def _payload_has(source: Mapping[str, Any], *keys: str) -> bool:
    try:
        return any(key in source for key in keys)
    except Exception:
        return False


def _project_is_demo(project: Any) -> bool:
    try:
        if project is None:
            return False

        if callable(service_is_demo_project):
            try:
                return bool(service_is_demo_project(project))
            except Exception:
                pass

        if _safe_bool(getattr(project, "is_demo", False), False):
            return True

        if _safe_str(getattr(project, "project_scope", ""), "", 40).lower() == PROJECT_SCOPE_DEMO:
            return True

        metadata = _get_project_metadata(project)
        demo_meta = _safe_dict(metadata.get("vectoplan_demo"))
        return _safe_bool(demo_meta.get("enabled"), False)

    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Auth / current user helpers
# ─────────────────────────────────────────────────────────────

def _app_user_by_local_id(local_user_id: Any) -> Optional[Any]:
    try:
        uid = _safe_int(local_user_id, 0)
        if uid <= 0:
            return None
        return AppUser.query.get(uid)
    except Exception:
        return None


def _app_user_by_auth_user_id(auth_user_id: Any) -> Optional[Any]:
    try:
        value = _safe_str(auth_user_id, "", 160)
        if not value:
            return None

        for field_name in ("auth_user_id", "platform_user_id", "external_user_id"):
            try:
                field = getattr(AppUser, field_name, None)
                if field is not None:
                    row = AppUser.query.filter(field == value).one_or_none()
                    if row is not None:
                        return row
            except Exception:
                continue
        return None
    except Exception:
        return None


def _app_user_auth_user_id(app_user: Any) -> Optional[str]:
    try:
        if app_user is None:
            return None
        for field_name in ("auth_user_id", "platform_user_id", "external_user_id"):
            value = _safe_str(getattr(app_user, field_name, None), "", 160)
            if value:
                return value
        return None
    except Exception:
        return None


def _context_to_dict(context: Any) -> Dict[str, Any]:
    try:
        data = _safe_dict(context)
        if data:
            return data
        if hasattr(context, "to_dict") and callable(context.to_dict):
            return _safe_dict(context.to_dict())
    except Exception:
        pass
    return {}


def _live_actor_context() -> Dict[str, Any]:
    try:
        return _context_to_dict(get_current_user_context(ensure=False))
    except Exception:
        return {}


def get_current_user_id(user_id: Optional[int] = None) -> int:
    """Resolve the local AppUser id. No default or placeholder user exists."""
    try:
        parsed = _safe_int(user_id, 0)
        if parsed > 0:
            return parsed

        optional = get_current_user_id_optional()
        if optional:
            return _safe_int(optional, 0)

        return 0
    except Exception:
        return 0


def get_actor_context(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Build a fail-closed actor context without converting a raw local id into auth truth.

    A supplied ``user_id`` is only a local AppUser selector. Authentication remains
    sourced from ``services.current_user``. For explicit trusted maintenance calls,
    local-id-only operation can be enabled deliberately through
    ``VECTOPLAN_PROJECT_ALLOW_TRUSTED_LOCAL_ACTOR_IDS``; it still requires an
    AppUser row carrying a canonical ``auth_user_id``.
    """

    explicit_uid = _safe_int(user_id, 0) or None
    live = _live_actor_context()

    try:
        if _safe_bool(live.get("blocked"), False):
            return live

        live_uid = _safe_int(live.get("user_id") or live.get("id"), 0) or None
        if explicit_uid and live_uid and explicit_uid != live_uid:
            return {
                **live,
                "blocked": True,
                "blocked_reason": "actor_user_mismatch",
                "persistent": False,
                "authenticated": False,
                "source": "actor_user_mismatch",
            }

        uid = explicit_uid or live_uid
        app_user = _app_user_by_local_id(uid)
        app_auth_user_id = _app_user_auth_user_id(app_user)
        auth_user_id = (
            _safe_str(live.get("auth_user_id") or live.get("authUserId"), "", 160)
            or (
                _safe_str(_safe_dict(live.get("user")).get("id"), "", 160)
                if live.get("user") is not None
                else ""
            )
            or app_auth_user_id
            or None
        )

        authenticated = _safe_bool(
            live.get("authenticated") or live.get("is_authenticated"),
            False,
        )
        persistent = bool(
            _safe_bool(live.get("persistent"), False)
            and uid
            and auth_user_id
            and authenticated
        )

        if live:
            result = dict(live)
            result.update(
                {
                    "user_id": uid,
                    "id": uid,
                    "auth_user_id": auth_user_id,
                    "authUserId": auth_user_id,
                    "authenticated": authenticated,
                    "persistent": persistent,
                    "demo_mode": _safe_bool(
                        live.get("demo_mode") or live.get("is_demo"),
                        False,
                    ),
                    "blocked": False,
                    "source": _safe_str(live.get("source"), "current_user", 120),
                }
            )
            return result

        # Explicit local ids without a request auth context are denied by default.
        trusted_local = _config_bool(
            "VECTOPLAN_PROJECT_ALLOW_TRUSTED_LOCAL_ACTOR_IDS",
            False,
        )
        if explicit_uid and trusted_local and app_auth_user_id:
            return {
                "user_id": explicit_uid,
                "id": explicit_uid,
                "auth_user_id": app_auth_user_id,
                "authUserId": app_auth_user_id,
                "authenticated": True,
                "persistent": True,
                "demo_mode": False,
                "blocked": False,
                "service_actor": True,
                "source": "trusted_local_app_user",
            }

        return {
            "user_id": explicit_uid,
            "id": explicit_uid,
            "auth_user_id": app_auth_user_id,
            "authUserId": app_auth_user_id,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "blocked_reason": "auth_context_unavailable",
            "source": "fallback_blocked",
        }

    except Exception:
        return {
            "user_id": explicit_uid,
            "id": explicit_uid,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "blocked_reason": "actor_context_unavailable",
            "source": "fallback_blocked",
        }


def get_actor_user_id_optional(user_id: Optional[int] = None) -> Optional[int]:
    try:
        context = get_actor_context(user_id)
        if _safe_bool(context.get("blocked"), False):
            return None
        if _safe_bool(context.get("demo_mode") or context.get("is_demo"), False):
            return None
        if not _safe_bool(context.get("persistent"), False):
            return None
        parsed = _safe_int(context.get("user_id") or context.get("id"), 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def get_actor_auth_user_id(user_id: Optional[int] = None) -> Optional[str]:
    try:
        context = get_actor_context(user_id)
        if _safe_bool(context.get("blocked"), False):
            return None
        if not _safe_bool(context.get("authenticated"), False):
            return None
        return _safe_str(
            context.get("auth_user_id") or context.get("authUserId"),
            "",
            160,
        ) or None
    except Exception:
        return None


def get_actor_account_id(user_id: Optional[int] = None) -> Optional[str]:
    try:
        context = get_actor_context(user_id)
        if _safe_bool(context.get("blocked"), False):
            return None
        return _safe_str(
            context.get("account_id") or context.get("accountId"),
            "",
            160,
        ) or None
    except Exception:
        return None


def actor_is_blocked(user_id: Optional[int] = None) -> bool:
    try:
        return _safe_bool(get_actor_context(user_id).get("blocked"), False)
    except Exception:
        return True


def actor_is_demo(user_id: Optional[int] = None) -> bool:
    try:
        context = get_actor_context(user_id)
        if _safe_bool(context.get("blocked"), False):
            return False
        return _safe_bool(context.get("demo_mode") or context.get("is_demo"), False)
    except Exception:
        return False


def actor_can_persist(user_id: Optional[int] = None) -> bool:
    try:
        context = get_actor_context(user_id)
        return bool(
            not _safe_bool(context.get("blocked"), False)
            and _safe_bool(context.get("authenticated"), False)
            and _safe_bool(context.get("persistent"), False)
            and not _safe_bool(context.get("demo_mode") or context.get("is_demo"), False)
            and _safe_int(context.get("user_id") or context.get("id"), 0) > 0
            and _safe_str(context.get("auth_user_id") or context.get("authUserId"), "", 160)
        )
    except Exception:
        return False


def require_persistent_actor(user_id: Optional[int] = None) -> int:
    """Require authenticated, unblocked, persistent AppUser-linked actor."""
    context = get_actor_context(user_id)

    if _safe_bool(context.get("blocked"), False):
        raise PermissionDenied(
            "Der Zugriff ist gesperrt oder der Auth-Kontext ist nicht verfügbar.",
            code=_safe_str(context.get("blocked_reason"), "auth_blocked", 120),
            status_code=503
            if _safe_str(context.get("blocked_reason"), "", 120) in {
                "auth_context_unavailable",
                "actor_context_unavailable",
                "auth_unavailable",
            }
            else 403,
            permission=PERMISSION_EDIT,
        )

    if _safe_bool(context.get("demo_mode") or context.get("is_demo"), False):
        raise PermissionDenied(
            "Im Demo-Modus können Projekte nicht dauerhaft gespeichert werden.",
            code="demo_mode_not_allowed",
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    if not _safe_bool(context.get("authenticated") or context.get("is_authenticated"), False):
        raise PermissionDenied(
            "Für diese Projektaktion ist Login erforderlich.",
            code="authentication_required",
            status_code=401,
            permission=PERMISSION_EDIT,
        )

    if not _safe_bool(context.get("persistent"), False):
        raise PermissionDenied(
            "Für diese Projektaktion ist eine lokale AppUser-Verknüpfung erforderlich.",
            code="persistent_user_required",
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    uid = _safe_int(context.get("user_id") or context.get("id"), 0)
    if uid <= 0:
        raise PermissionDenied(
            "Für diese Projektaktion ist ein lokaler AppUser erforderlich.",
            code="local_user_link_required",
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    auth_user_id = _safe_str(
        context.get("auth_user_id") or context.get("authUserId"),
        "",
        160,
    )
    if not auth_user_id:
        raise PermissionDenied(
            "Der lokale AppUser besitzt keine kanonische Auth-User-ID.",
            code="auth_user_link_required",
            status_code=409,
            permission=PERMISSION_EDIT,
        )

    return uid


def require_actor_auth_user_id(user_id: Optional[int] = None) -> str:
    uid = require_persistent_actor(user_id)
    value = get_actor_auth_user_id(uid)
    if not value:
        raise PermissionDenied(
            "Der lokale AppUser besitzt keine kanonische Auth-User-ID.",
            code="auth_user_link_required",
            status_code=409,
            permission=PERMISSION_EDIT,
            user_id=uid,
        )
    return value


def ensure_project_user() -> Any:
    """Return the current local AppUser link without creating a fallback user."""
    if not actor_can_persist():
        return None
    try:
        if callable(get_current_user):
            return get_current_user(ensure=True)
    except Exception:
        return None
    return None


# ─────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────

def normalize_role(value: Any, default: str = ROLE_VIEWER) -> str:
    try:
        if permissions_normalize_role is not None:
            return permissions_normalize_role(value, default)
    except Exception:
        pass

    clean = _normalize_slug(value, default, 40)
    return clean if clean in {ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER} else default


def _normalize_permission(value: Any, default: str = PERMISSION_VIEW) -> str:
    clean = _normalize_slug(value, default, 40)
    aliases = {
        "read": PERMISSION_VIEW,
        "write": PERMISSION_EDIT,
        "admin": PERMISSION_MANAGE,
        "remove": PERMISSION_DELETE,
        "iframe": PERMISSION_EMBED,
        "settings": "manage_settings",
        "team": "manage_team",
    }
    clean = aliases.get(clean, clean)
    return clean if clean in {
        PERMISSION_VIEW,
        PERMISSION_EDIT,
        PERMISSION_MANAGE,
        PERMISSION_DELETE,
        PERMISSION_TRANSFER,
        PERMISSION_EMBED,
        "view_settings",
        "manage_settings",
        "view_team",
        "manage_team",
        "view_admin",
    } else default


def get_project_permission_result(
    project: Any,
    *,
    user_id: Optional[int] = None,
    allow_public_view: bool = True,
) -> ProjectPermissionResult:
    try:
        if permissions_get_project_permission_result is not None:
            result = permissions_get_project_permission_result(
                project,
                user_id=user_id,
                allow_public_view=allow_public_view,
            )
            data = result.to_dict() if hasattr(result, "to_dict") else _safe_dict(result)
            permissions = _safe_dict(data.get("permissions"))

            return ProjectPermissionResult(
                can_view=_safe_bool(data.get("can_view", permissions.get(PERMISSION_VIEW)), False),
                can_edit=_safe_bool(data.get("can_edit", permissions.get(PERMISSION_EDIT)), False),
                can_manage=_safe_bool(data.get("can_manage", permissions.get(PERMISSION_MANAGE)), False),
                can_delete=_safe_bool(data.get("can_delete", permissions.get(PERMISSION_DELETE)), False),
                can_transfer=_safe_bool(data.get("can_transfer", permissions.get(PERMISSION_TRANSFER)), False),
                can_embed=_safe_bool(data.get("can_embed", permissions.get(PERMISSION_EMBED)), False),
                can_view_settings=_safe_bool(data.get("can_view_settings", permissions.get("view_settings")), False),
                can_manage_settings=_safe_bool(data.get("can_manage_settings", permissions.get("manage_settings")), False),
                can_view_team=_safe_bool(data.get("can_view_team", permissions.get("view_team")), False),
                can_manage_team=_safe_bool(data.get("can_manage_team", permissions.get("manage_team")), False),
                can_view_admin=_safe_bool(data.get("can_view_admin", permissions.get("view_admin")), False),
                role=_safe_str(data.get("role"), ROLE_VIEWER, 40),
                source=_safe_str(data.get("source"), "permissions_service", 80),
            )
    except Exception:
        pass

    return ProjectPermissionResult()


def serialize_project_permissions(project: Any, *, user_id: Optional[int] = None) -> Dict[str, Any]:
    try:
        if permissions_serialize_project_permissions is not None:
            return _safe_dict(permissions_serialize_project_permissions(project, user_id=user_id))
    except Exception:
        pass

    try:
        result = get_project_permission_result(project, user_id=user_id)
        return result.to_dict()
    except Exception:
        return ProjectPermissionResult().to_dict()


def require_project_permission(
    project: Any,
    permission: str,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = False,
) -> ProjectPermissionResult:
    clean_permission = _normalize_permission(permission)

    try:
        if permissions_require_project_permission is not None:
            permissions_require_project_permission(
                project,
                clean_permission,
                user_id=user_id,
                allow_public_view=allow_public_view,
            )
            return get_project_permission_result(
                project,
                user_id=user_id,
                allow_public_view=allow_public_view,
            )
    except PermissionDenied:
        raise
    except Exception:
        pass

    result = get_project_permission_result(
        project,
        user_id=user_id,
        allow_public_view=allow_public_view,
    )

    if not result.can(clean_permission):
        raise PermissionDenied(
            f"missing project permission: {clean_permission}",
            permission=clean_permission,
            status_code=403,
            code="project_permission_denied",
        )

    return result


def _get_membership(project: Any, user_id: int) -> Optional[Any]:
    try:
        project_id = _safe_int(getattr(project, "id", None), 0)
        uid = _safe_int(user_id, 0)

        if not project_id or not uid:
            return None

        return ProjectMembership.query.filter_by(project_id=project_id, user_id=uid).one_or_none()

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────

def project_public_url(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/project={public_id}" if public_id else "/project=new"
    except Exception:
        return "/project=new"


def project_workspace_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/project" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def project_editor_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/editor3d" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def project_map_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/map" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def project_cad2d_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/cad2d" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def project_lv_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/lv" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def project_admin_path(project: Any) -> str:
    try:
        if _project_is_demo(project):
            return ""
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/admin" if public_id else "/ui/project/new"
    except Exception:
        return ""


def build_project_paths(project: Any) -> Dict[str, str]:
    try:
        return {
            "projectPagePath": project_workspace_path(project),
            "projectUrl": project_workspace_path(project),
            "projectPublicUrl": project_public_url(project),
            "editorPagePath": project_editor_path(project),
            "editor3dPagePath": project_editor_path(project),
            "initialEditorUrl": project_editor_path(project),
            "mapPagePath": project_map_path(project),
            "cad2dPagePath": project_cad2d_path(project),
            "lvPagePath": project_lv_path(project),
            "adminPagePath": project_admin_path(project),
        }
    except Exception:
        return {
            "projectPagePath": "/ui/project/new",
            "projectUrl": "/ui/project/new",
            "projectPublicUrl": "/project=new",
        }


# ─────────────────────────────────────────────────────────────
# Address / geocoder helpers
# ─────────────────────────────────────────────────────────────

def build_address_text_from_parts(project_or_payload: Any) -> str:
    try:
        if isinstance(project_or_payload, Mapping):
            data = _safe_dict(project_or_payload)
            street = _safe_str(data.get("street") or data.get("address_street"), "", 255)
            house_number = _safe_str(data.get("house_number") or data.get("address_house_number"), "", 80)
            postal_code = _safe_str(data.get("postal_code") or data.get("address_postal_code"), "", 40)
            city = _safe_str(data.get("city") or data.get("address_city"), "", 160)
            region = _safe_str(data.get("region") or data.get("address_region"), "", 160)
            country = _safe_str(data.get("country") or data.get("address_country"), "", 160)
        else:
            street = _safe_str(getattr(project_or_payload, "street", None), "", 255)
            house_number = _safe_str(getattr(project_or_payload, "house_number", None), "", 80)
            postal_code = _safe_str(getattr(project_or_payload, "postal_code", None), "", 40)
            city = _safe_str(getattr(project_or_payload, "city", None), "", 160)
            region = _safe_str(getattr(project_or_payload, "region", None), "", 160)
            country = _safe_str(getattr(project_or_payload, "country", None), "", 160)

        line1 = " ".join(part for part in [street, house_number] if part).strip()
        line2 = " ".join(part for part in [postal_code, city] if part).strip()
        parts = [part for part in [line1, line2, region, country] if part]
        return ", ".join(parts).strip()
    except Exception:
        return ""


def get_project_address_text(project: Any, *, allow_structured_fallback: bool = True) -> str:
    try:
        direct = _safe_str(getattr(project, "address_text", None), "", 2000)
        if direct:
            return direct

        if allow_structured_fallback:
            return build_address_text_from_parts(project)

        return ""
    except Exception:
        return ""


def _project_has_minimum_definition(project: Any) -> bool:
    try:
        has_name = bool(_safe_str(getattr(project, "name", None), "", 255))
        has_address_text = bool(_safe_str(getattr(project, "address_text", None), "", 2000))

        if has_name and has_address_text:
            return True

        legacy_address = build_address_text_from_parts(project)
        return bool(has_name and legacy_address)

    except Exception:
        return False


def _geocode_status_from_project(project: Any) -> str:
    try:
        metadata = _get_project_metadata(project)
        geocode = _safe_dict(metadata.get("geocode"))
        return _safe_str(geocode.get("status"), GEOCODE_STATUS_NONE, 40)
    except Exception:
        return GEOCODE_STATUS_NONE


def _mark_geocode_pending_or_stale(
    project: Any,
    *,
    old_address_text: Optional[str],
    new_address_text: Optional[str],
    source: str,
) -> None:
    try:
        old_value = _safe_str(old_address_text, "", 2000)
        new_value = _safe_str(new_address_text, "", 2000)

        if not new_value:
            return

        metadata = _get_project_metadata(project)
        geocode = _safe_dict(metadata.get("geocode"))

        if not old_value:
            status = GEOCODE_STATUS_PENDING
        elif old_value != new_value:
            status = GEOCODE_STATUS_STALE
        else:
            status = _safe_str(geocode.get("status"), GEOCODE_STATUS_PENDING, 40)

        geocode.update(
            {
                "status": status,
                "source": source,
                "address_text": new_value,
                "updated_at": _iso(_utcnow()),
                "note": "Geocoder is not connected yet. Structured address and coordinates are intentionally preserved.",
            }
        )

        metadata["geocode"] = geocode
        _set_project_metadata(project, metadata)
    except Exception:
        pass


def _structured_address_payload_from_geocoder(source: Mapping[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    data = _safe_dict(source)

    geocoder = (
        _safe_dict(data.get("geocoder"))
        or _safe_dict(data.get("geocode"))
        or _safe_dict(data.get("geocoded"))
        or _safe_dict(data.get("address_components"))
        or _safe_dict(data.get("structured_address"))
    )

    address = _safe_dict(data.get("address"))
    coordinates = _safe_dict(data.get("coordinates") or data.get("location") or geocoder.get("coordinates"))

    candidates = {
        "street": geocoder.get("street") or geocoder.get("road") or geocoder.get("address_street"),
        "house_number": geocoder.get("house_number") or geocoder.get("houseNumber") or geocoder.get("address_house_number"),
        "postal_code": geocoder.get("postal_code") or geocoder.get("postcode") or geocoder.get("zip") or geocoder.get("address_postal_code"),
        "city": geocoder.get("city") or geocoder.get("town") or geocoder.get("village") or geocoder.get("municipality") or geocoder.get("address_city"),
        "region": geocoder.get("region") or geocoder.get("state") or geocoder.get("bundesland") or geocoder.get("address_region"),
        "country": geocoder.get("country") or geocoder.get("country_code") or geocoder.get("address_country"),
    }

    if isinstance(data.get("address"), Mapping):
        candidates.update(
            {
                "street": candidates["street"] or address.get("street"),
                "house_number": candidates["house_number"] or address.get("house_number"),
                "postal_code": candidates["postal_code"] or address.get("postal_code"),
                "city": candidates["city"] or address.get("city"),
                "region": candidates["region"] or address.get("region"),
                "country": candidates["country"] or address.get("country"),
            }
        )

    for key, value in candidates.items():
        if value is not None:
            max_len = 80 if key == "house_number" else 40 if key == "postal_code" else 160
            if key == "street":
                max_len = 255
            payload[key] = _safe_str(value, "", max_len) or None

    lat_value = (
        geocoder.get("latitude")
        if geocoder.get("latitude") is not None
        else geocoder.get("lat")
        if geocoder.get("lat") is not None
        else coordinates.get("latitude")
        if coordinates.get("latitude") is not None
        else coordinates.get("lat")
    )

    lon_value = (
        geocoder.get("longitude")
        if geocoder.get("longitude") is not None
        else geocoder.get("lng")
        if geocoder.get("lng") is not None
        else geocoder.get("lon")
        if geocoder.get("lon") is not None
        else coordinates.get("longitude")
        if coordinates.get("longitude") is not None
        else coordinates.get("lng")
        if coordinates.get("lng") is not None
        else coordinates.get("lon")
    )

    if lat_value is not None:
        payload["latitude"] = _safe_float(lat_value, None)
    if lon_value is not None:
        payload["longitude"] = _safe_float(lon_value, None)

    srid = (
        geocoder.get("coordinate_srid")
        or geocoder.get("srid")
        or coordinates.get("srid")
        or coordinates.get("coordinate_srid")
    )

    if srid is not None:
        payload["coordinate_srid"] = _safe_str(srid, DEFAULT_COORDINATE_SRID, 40) or DEFAULT_COORDINATE_SRID

    if payload:
        payload["geocode_status"] = _safe_str(
            geocoder.get("status") or data.get("geocode_status"),
            GEOCODE_STATUS_RESOLVED if (payload.get("latitude") is not None and payload.get("longitude") is not None) else GEOCODE_STATUS_PENDING,
            40,
        )
        payload["geocode_payload"] = geocoder

    return payload


# ─────────────────────────────────────────────────────────────
# Chunk reference helpers
# ─────────────────────────────────────────────────────────────

def _project_chunk_refs(project: Any) -> Dict[str, Any]:
    """Return normalized references plus provisioning and access-sync state."""
    try:
        direct = _safe_dict(getattr(project, "chunk_refs", {}))
        service_refs = _safe_dict(getattr(project, "service_refs", {}))
        service_chunk = _safe_dict(service_refs.get(SERVICE_CHUNK))
        metadata = _get_project_metadata(project)
        metadata_chunk = _safe_dict(metadata.get("chunk"))

        chunk_project_id = (
            _safe_str(getattr(project, "chunk_project_id", None), "", 160)
            or _safe_str(direct.get("chunk_project_id") or direct.get("chunkProjectId"), "", 160)
            or _safe_str(service_chunk.get("chunk_project_id") or service_chunk.get("chunkProjectId"), "", 160)
            or _safe_str(metadata_chunk.get("chunk_project_id") or metadata_chunk.get("chunkProjectId"), "", 160)
        )
        chunk_universe_id = (
            _safe_str(getattr(project, "chunk_universe_id", None), "", 160)
            or _safe_str(direct.get("chunk_universe_id") or direct.get("chunkUniverseId"), "", 160)
            or _safe_str(service_chunk.get("chunk_universe_id") or service_chunk.get("chunkUniverseId"), "", 160)
            or _safe_str(metadata_chunk.get("chunk_universe_id") or metadata_chunk.get("chunkUniverseId"), "", 160)
        )
        chunk_world_id = (
            _safe_str(getattr(project, "chunk_world_id", None), "", 160)
            or _safe_str(direct.get("chunk_world_id") or direct.get("chunkWorldId"), "", 160)
            or _safe_str(service_chunk.get("chunk_world_id") or service_chunk.get("chunkWorldId"), "", 160)
            or _safe_str(metadata_chunk.get("chunk_world_id") or metadata_chunk.get("chunkWorldId"), "", 160)
        )
        route_hints = (
            _safe_dict(getattr(project, "chunk_route_hints", {}))
            or _safe_dict(direct.get("route_hints") or direct.get("routeHints"))
            or _safe_dict(service_chunk.get("route_hints") or service_chunk.get("routeHints"))
            or _safe_dict(metadata_chunk.get("route_hints") or metadata_chunk.get("routeHints"))
        )

        if callable(serialize_project_chunk_provisioning_status):
            try:
                provisioning = _safe_dict(serialize_project_chunk_provisioning_status(project))
            except Exception:
                provisioning = {}
        else:
            provisioning = _safe_dict(getattr(project, "chunk_provisioning", {}))

        if not provisioning:
            provisioning = (
                _safe_dict(service_chunk.get("provisioning"))
                or _safe_dict(metadata_chunk.get("provisioning"))
            )

        if callable(serialize_project_chunk_access_sync_status):
            try:
                access_sync = _safe_dict(serialize_project_chunk_access_sync_status(project))
            except Exception:
                access_sync = {}
        else:
            access_sync = _safe_dict(getattr(project, "chunk_access_sync", {}))

        if not access_sync:
            access_sync = (
                _safe_dict(service_chunk.get("accessSync"))
                or _safe_dict(metadata_chunk.get("accessSync"))
            )

        provisioning_status = _safe_str(
            provisioning.get("status")
            or getattr(project, "chunk_provisioning_status", None),
            CHUNK_PROVISIONING_PENDING,
            40,
        )
        legacy_status = _safe_str(
            getattr(project, "chunk_status", None)
            or direct.get("status")
            or service_chunk.get("status")
            or metadata_chunk.get("status"),
            CHUNK_STATUS_READY if chunk_project_id and chunk_world_id else CHUNK_STATUS_PENDING,
            40,
        )
        ready = bool(
            chunk_project_id
            and chunk_world_id
            and (
                provisioning_status in {
                    CHUNK_PROVISIONING_READY,
                    CHUNK_PROVISIONING_FALLBACK_READY,
                }
                or legacy_status == CHUNK_STATUS_READY
            )
        )

        requested = _safe_str(
            provisioning.get("requestedWorldTemplate")
            or provisioning.get("requested_world_template")
            or getattr(project, "chunk_world_template_requested", None),
            CHUNK_WORLD_TEMPLATE_EARTH,
            40,
        )
        effective = _safe_str(
            provisioning.get("effectiveWorldTemplate")
            or provisioning.get("effective_world_template")
            or getattr(project, "chunk_world_template_effective", None),
            "",
            40,
        ) or None
        fallback = _safe_str(
            provisioning.get("fallbackWorldTemplate")
            or provisioning.get("fallback_world_template")
            or getattr(project, "chunk_world_template_fallback", None),
            CHUNK_WORLD_TEMPLATE_FLAT,
            40,
        )
        fallback_reason = _safe_str(
            provisioning.get("fallbackReason")
            or provisioning.get("fallback_reason")
            or getattr(project, "chunk_world_fallback_reason", None),
            "",
            160,
        ) or None

        return {
            "status": legacy_status,
            "ready": ready,
            "chunk_project_id": chunk_project_id or None,
            "chunk_universe_id": chunk_universe_id or None,
            "chunk_world_id": chunk_world_id or None,
            "route_hints": route_hints,
            "provisioning_status": provisioning_status,
            "requested_world_template": requested,
            "fallback_world_template": fallback,
            "effective_world_template": effective,
            "fallback_used": bool(requested and effective and requested != effective),
            "fallback_reason": fallback_reason,
            "earth_reference_fingerprint": (
                provisioning.get("earthReferenceFingerprint")
                or provisioning.get("earth_reference_fingerprint")
                or getattr(project, "earth_reference_fingerprint", None)
            ),
            "provisioning": provisioning,
            "access_sync": access_sync,
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
            "ready": False,
            "chunk_project_id": None,
            "chunk_universe_id": None,
            "chunk_world_id": None,
            "route_hints": {},
            "provisioning_status": CHUNK_PROVISIONING_FAILED,
            "requested_world_template": CHUNK_WORLD_TEMPLATE_EARTH,
            "fallback_world_template": CHUNK_WORLD_TEMPLATE_FLAT,
            "effective_world_template": None,
            "fallback_used": False,
            "fallback_reason": None,
            "earth_reference_fingerprint": None,
            "provisioning": {},
            "access_sync": {},
        }


def _set_project_chunk_refs(
    project: Any,
    *,
    chunk_project_id: Optional[str],
    chunk_universe_id: Optional[str] = None,
    chunk_world_id: Optional[str],
    route_hints: Optional[Dict[str, Any]] = None,
    status: str = CHUNK_STATUS_READY,
    error: Optional[Dict[str, Any]] = None,
    requested_world_template: Optional[str] = None,
    fallback_world_template: Optional[str] = None,
    effective_world_template: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    earth_reference_fingerprint: Optional[str] = None,
) -> None:
    try:
        if hasattr(project, "set_chunk_refs") and callable(project.set_chunk_refs):
            project.set_chunk_refs(
                chunk_project_id=chunk_project_id,
                chunk_universe_id=chunk_universe_id,
                chunk_world_id=chunk_world_id,
                route_hints=_safe_dict(route_hints),
                status=status,
                error=_safe_dict(error),
                requested_world_template=requested_world_template,
                fallback_world_template=fallback_world_template,
                effective_world_template=effective_world_template,
                fallback_reason=fallback_reason,
                earth_reference_fingerprint=earth_reference_fingerprint,
            )
            return

        clean_chunk_project_id = _safe_str(chunk_project_id, "", 160) or None
        clean_chunk_universe_id = _safe_str(chunk_universe_id, "", 160) or None
        clean_chunk_world_id = _safe_str(chunk_world_id, "", 160) or None

        for key, value in (
            ("chunk_project_id", clean_chunk_project_id),
            ("chunk_universe_id", clean_chunk_universe_id),
            ("chunk_world_id", clean_chunk_world_id),
            ("chunk_status", status),
            ("chunk_route_hints", _safe_dict(route_hints)),
            ("chunk_last_error", _safe_dict(error) or None),
            ("chunk_world_template_requested", requested_world_template),
            ("chunk_world_template_fallback", fallback_world_template),
            ("chunk_world_template_effective", effective_world_template),
            ("chunk_world_fallback_reason", fallback_reason),
            ("earth_reference_fingerprint", earth_reference_fingerprint),
        ):
            if value is not None and hasattr(project, key):
                setattr(project, key, value)

        if hasattr(project, "chunk_ready"):
            project.chunk_ready = bool(
                clean_chunk_project_id
                and clean_chunk_world_id
                and status == CHUNK_STATUS_READY
            )

        metadata = _get_project_metadata(project)
        metadata["chunk"] = {
            **_safe_dict(metadata.get("chunk")),
            "status": status,
            "ready": bool(clean_chunk_project_id and clean_chunk_world_id and status == CHUNK_STATUS_READY),
            "chunk_project_id": clean_chunk_project_id,
            "chunk_universe_id": clean_chunk_universe_id,
            "chunk_world_id": clean_chunk_world_id,
            "route_hints": _safe_dict(route_hints),
            "updated_at": _iso(_utcnow()),
        }
        if error:
            metadata["chunk"]["error"] = _safe_dict(error)
        _set_project_metadata(project, metadata)

    except Exception as exc:
        _log_warning("set project chunk refs failed: %s", exc.__class__.__name__)


def _set_project_chunk_status(
    project: Any,
    *,
    status: str,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        if status == CHUNK_STATUS_DISABLED and hasattr(project, "mark_chunk_disabled"):
            project.mark_chunk_disabled()
            return
        if status == CHUNK_STATUS_PENDING and hasattr(project, "mark_chunk_pending"):
            project.mark_chunk_pending()
            return
        if status == CHUNK_STATUS_ERROR and hasattr(project, "mark_chunk_error"):
            project.mark_chunk_error(_safe_dict(error))
            return

        refs = _project_chunk_refs(project)
        _set_project_chunk_refs(
            project,
            chunk_project_id=refs.get("chunk_project_id"),
            chunk_universe_id=refs.get("chunk_universe_id"),
            chunk_world_id=refs.get("chunk_world_id"),
            route_hints=_safe_dict(refs.get("route_hints")),
            status=status,
            error=error,
        )
    except Exception:
        pass


def _mark_chunk_access_sync_pending(project: Any) -> None:
    try:
        if hasattr(project, "chunk_access_sync_status"):
            project.chunk_access_sync_status = CHUNK_ACCESS_SYNC_PENDING
        if hasattr(project, "chunk_access_sync_error_code"):
            project.chunk_access_sync_error_code = None
        if hasattr(project, "chunk_access_sync_error_message"):
            project.chunk_access_sync_error_message = None
    except Exception:
        pass


def _mark_chunk_access_sync_failed(
    project: Any,
    *,
    code: str,
    message: str,
    repair_required: bool = True,
) -> None:
    try:
        if hasattr(project, "mark_chunk_access_sync_failed"):
            project.mark_chunk_access_sync_failed(
                code=code,
                message=message,
                repair_required=repair_required,
            )
            return
        if hasattr(project, "chunk_access_sync_status"):
            project.chunk_access_sync_status = (
                CHUNK_ACCESS_SYNC_REPAIR_REQUIRED
                if repair_required
                else CHUNK_ACCESS_SYNC_FAILED
            )
        if hasattr(project, "chunk_access_sync_error_code"):
            project.chunk_access_sync_error_code = code
        if hasattr(project, "chunk_access_sync_error_message"):
            project.chunk_access_sync_error_message = message
    except Exception:
        pass


def _internal_upsert_project_service_link(
    project: Any,
    *,
    service_name: str,
    resource_kind: str,
    resource_id: str,
    external_id: Optional[str] = None,
    external_project_id: Optional[str] = None,
    external_world_id: Optional[str] = None,
    external_url: Optional[str] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    if project is None:
        raise ValueError("project required")

    clean_service = model_normalize_service(service_name)
    clean_resource_type = model_normalize_resource_type(resource_kind)
    clean_resource_id = _safe_str(resource_id, "", 255)

    if not clean_resource_id:
        raise ValueError("resource_id required")

    row = (
        ProjectServiceLink.query.filter_by(
            project_id=project.id,
            service=clean_service,
            resource_type=clean_resource_type,
            resource_id=clean_resource_id,
        )
        .one_or_none()
    )

    if row is None:
        row = ProjectServiceLink(
            project_id=project.id,
            service=clean_service,
            service_name=clean_service,
            resource_type=clean_resource_type,
            resource_id=clean_resource_id,
        )
        db.session.add(row)

    row.external_id = _safe_str(external_id, "", 255) or row.external_id
    row.title = row.title or f"{clean_service}:{clean_resource_type}"
    row.status = _normalize_status(status, "active")
    row.is_enabled = row.status == "active"
    row.public_url = _safe_str(external_url, "", 4000) or row.public_url
    row.reference = {
        **_safe_dict(getattr(row, "reference", {})),
        "external_id": external_id,
        "external_project_id": external_project_id,
        "external_world_id": external_world_id,
    }
    row.metadata_json = _safe_dict(metadata)

    if hasattr(row, "normalize"):
        row.normalize()

    db.session.add(row)
    return row


def _upsert_chunk_service_links_from_refs(
    project: Any,
    *,
    refs: Dict[str, Any],
    user_id: Optional[int] = None,
) -> List[Any]:
    rows: List[Any] = []

    chunk_project_id = _safe_str(refs.get("chunk_project_id"), "", 160)
    chunk_universe_id = _safe_str(refs.get("chunk_universe_id"), "", 160)
    chunk_world_id = _safe_str(refs.get("chunk_world_id"), "", 160)
    route_hints = _safe_dict(refs.get("route_hints"))

    chunk_public_url = _config_str("VECTOPLAN_CHUNK_PUBLIC_URL", "", 4000)

    actor_id = get_current_user_id(user_id) or None

    if chunk_project_id:
        rows.append(
            _internal_upsert_project_service_link(
                project,
                service_name=SERVICE_CHUNK,
                resource_kind=RESOURCE_CHUNK_PROJECT,
                resource_id=chunk_project_id,
                external_id=chunk_project_id,
                external_project_id=chunk_project_id,
                external_url=chunk_public_url,
                status="active",
                metadata={
                    "source": "project_service.ensure_project_chunk_link",
                    "app_project_public_id": _project_public_id(project),
                    "chunk_project_id": chunk_project_id,
                    "chunk_universe_id": chunk_universe_id,
                    "chunk_world_id": chunk_world_id,
                    "route_hints": route_hints,
                    "user_id": actor_id,
                    "is_demo": _project_is_demo(project),
                },
            )
        )

    if chunk_world_id:
        rows.append(
            _internal_upsert_project_service_link(
                project,
                service_name=SERVICE_CHUNK,
                resource_kind=RESOURCE_CHUNK_WORLD,
                resource_id=chunk_world_id,
                external_id=chunk_world_id,
                external_project_id=chunk_project_id,
                external_world_id=chunk_world_id,
                external_url=chunk_public_url,
                status="active",
                metadata={
                    "source": "project_service.ensure_project_chunk_link",
                    "app_project_public_id": _project_public_id(project),
                    "chunk_project_id": chunk_project_id,
                    "chunk_universe_id": chunk_universe_id,
                    "chunk_world_id": chunk_world_id,
                    "route_hints": route_hints,
                    "user_id": actor_id,
                    "is_demo": _project_is_demo(project),
                },
            )
        )

    return rows


def _chunk_client_available() -> bool:
    return callable(provision_project_chunk_graph)


def _chunk_access_sync_available() -> bool:
    return callable(sync_project_chunk_access)


def _chunk_provisioning_enabled() -> bool:
    try:
        if callable(is_chunk_provisioning_enabled):
            return bool(is_chunk_provisioning_enabled())
    except Exception:
        pass
    return _config_bool("VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True)


def _chunk_provisioning_required() -> bool:
    try:
        if callable(is_chunk_provisioning_required):
            return bool(is_chunk_provisioning_required())
    except Exception:
        pass
    return _config_bool("VECTOPLAN_CHUNK_PROVISION_REQUIRED", False)


def _chunk_access_sync_enabled() -> bool:
    return _config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True)


def _should_attempt_chunk_provision(project: Any, *, force: bool = False) -> bool:
    if project is None or _project_is_demo(project):
        return False
    if not _chunk_provisioning_enabled():
        return False
    if bool(getattr(project, "is_deleted", False)):
        return False
    if _safe_str(getattr(project, "status", ""), "", 40) in {
        PROJECT_STATUS_DELETED,
        PROJECT_STATUS_EXPIRED,
    }:
        return False
    if force:
        return True
    refs = _project_chunk_refs(project)
    return not bool(refs.get("ready"))


def _local_user_id_for_auth_user_id(auth_user_id: Any) -> Optional[int]:
    row = _app_user_by_auth_user_id(auth_user_id)
    return _safe_int(getattr(row, "id", None), 0) or None


def _chunk_provisioning_audit_writer(**kwargs: Any) -> Optional[Any]:
    project = kwargs.get("project")
    action = _safe_str(kwargs.get("action"), "chunk_provisioning", 160)
    auth_user_id = _safe_str(
        kwargs.get("actor_auth_user_id") or kwargs.get("actor_user_id"),
        "",
        160,
    )
    return _record_project_event(
        project,
        action=action,
        category="service_link",
        actor_user_id=_local_user_id_for_auth_user_id(auth_user_id),
        payload={
            **_safe_dict(kwargs.get("payload") or kwargs.get("metadata")),
            "actor_auth_user_id": auth_user_id or None,
            "service": SERVICE_CHUNK,
        },
        commit=False,
    )


def _sync_project_chunk_access_best_effort(
    project: Any,
    *,
    actor_user_id: Optional[int],
    actor_auth_user_id: Optional[str],
    force: bool = False,
    commit: bool = True,
) -> Dict[str, Any]:
    """Synchronize app memberships after the local transaction is durable.

    Until ``project_chunk_access_sync_service.py`` is installed, the project is
    marked pending/repair-required instead of pretending synchronization worked.
    """
    if project is None or _project_is_demo(project):
        return {"ok": True, "code": "chunk_access_sync_not_applicable"}
    if not _chunk_access_sync_enabled():
        try:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = CHUNK_ACCESS_SYNC_DISABLED
            db.session.add(project)
            _db_flush_or_commit(commit)
        except Exception:
            if commit:
                _db_rollback_safely()
        return {"ok": True, "code": "chunk_access_sync_disabled"}

    refs = _project_chunk_refs(project)

    # A caller-owned transaction must not trigger a remote mutation. The local
    # project is marked pending and the synchronization can run after commit.
    if not commit:
        _mark_chunk_access_sync_pending(project)
        try:
            db.session.add(project)
            db.session.flush()
        except Exception:
            pass
        return {
            "ok": False,
            "code": "chunk_access_sync_deferred_until_commit",
            "retryable": True,
        }

    if not refs.get("chunk_project_id"):
        _mark_chunk_access_sync_pending(project)
        try:
            db.session.add(project)
            _db_flush_or_commit(commit)
        except Exception:
            if commit:
                _db_rollback_safely()
        return {"ok": False, "code": "chunk_project_not_ready", "retryable": True}

    if not callable(sync_project_chunk_access):
        _mark_chunk_access_sync_failed(
            project,
            code="chunk_access_sync_service_unavailable",
            message="project_chunk_access_sync_service is not available.",
            repair_required=True,
        )
        try:
            db.session.add(project)
            _record_project_event(
                project,
                action="chunk_access_sync_failed",
                category="access",
                actor_user_id=actor_user_id,
                payload={
                    "code": "chunk_access_sync_service_unavailable",
                    "actor_auth_user_id": actor_auth_user_id,
                },
                commit=False,
            )
            _db_flush_or_commit(commit)
        except Exception:
            if commit:
                _db_rollback_safely()
        return {
            "ok": False,
            "code": "chunk_access_sync_service_unavailable",
            "retryable": True,
        }

    try:
        raw = sync_project_chunk_access(
            project,
            actor_auth_user_id=actor_auth_user_id,
            force=force,
            commit=commit,
        )
        if hasattr(raw, "to_dict"):
            return _safe_dict(raw.to_dict())
        return _safe_dict(raw) or {"ok": bool(getattr(raw, "ok", False))}
    except Exception as exc:
        _db_rollback_safely()
        _mark_chunk_access_sync_failed(
            project,
            code=_safe_str(getattr(exc, "code", None), "chunk_access_sync_failed", 160),
            message=_safe_str(getattr(exc, "message", None) or str(exc), "Chunk access synchronization failed.", 2000),
            repair_required=True,
        )
        try:
            db.session.add(project)
            _record_project_event(
                project,
                action="chunk_access_sync_failed",
                category="access",
                actor_user_id=actor_user_id,
                payload={
                    "code": _safe_str(getattr(exc, "code", None), "chunk_access_sync_failed", 160),
                    "message": _safe_str(str(exc), "", 1000),
                    "actor_auth_user_id": actor_auth_user_id,
                },
                commit=False,
            )
            db.session.commit()
        except Exception:
            _db_rollback_safely()
        return {
            "ok": False,
            "code": _safe_str(getattr(exc, "code", None), "chunk_access_sync_failed", 160),
            "message": _safe_str(str(exc), "", 1000),
            "retryable": bool(getattr(exc, "retryable", True)),
        }


def ensure_project_chunk_link(
    project: Any,
    *,
    user_id: Optional[int] = None,
    force: bool = False,
    commit: bool = True,
) -> ProjectOperationResult:
    """Provision the chunk graph after the app project transaction is durable."""
    if project is None:
        return ProjectOperationResult(
            ok=False,
            status_code=400,
            code="project_required",
            error="project required",
        )

    if _project_is_demo(project):
        return ProjectOperationResult(
            ok=False,
            project=project,
            status_code=403,
            code="demo_chunk_provisioning_not_allowed",
            error="Persistent Chunk provisioning requires an authenticated user.",
        )

    uid = require_persistent_actor(user_id)
    auth_user_id = require_actor_auth_user_id(uid)
    require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)

    if not _chunk_provisioning_enabled():
        _set_project_chunk_status(project, status=CHUNK_STATUS_DISABLED)
        db.session.add(project)
        _record_project_event(
            project,
            action="chunk_provisioning_disabled",
            category="service_link",
            actor_user_id=uid,
            payload={"service": SERVICE_CHUNK, "reason": "disabled_by_config"},
            commit=False,
        )
        _db_flush_or_commit(commit)
        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={"ok": True, "chunk": _project_chunk_refs(project)},
            status_code=200,
            code="chunk_provisioning_disabled",
        )

    existing = _project_chunk_refs(project)
    if not force and existing.get("ready"):
        _upsert_chunk_service_links_from_refs(project, refs=existing, user_id=uid)
        db.session.add(project)
        _db_flush_or_commit(commit)
        access_result = _sync_project_chunk_access_best_effort(
            project,
            actor_user_id=uid,
            actor_auth_user_id=auth_user_id,
            force=False,
            commit=commit,
        )
        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={
                "ok": True,
                "chunk": _project_chunk_refs(project),
                "existing": True,
                "access_sync": access_result,
            },
            status_code=200,
            code="chunk_link_exists",
        )

    if not commit:
        _set_project_chunk_status(project, status=CHUNK_STATUS_PENDING)
        try:
            db.session.add(project)
            db.session.flush()
        except Exception:
            pass
        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={
                "ok": False,
                "chunk": _project_chunk_refs(project),
                "deferred": True,
            },
            status_code=409,
            code="chunk_provisioning_requires_committed_project",
            error="Chunk provisioning is deferred until the App project transaction is committed.",
        )

    if not callable(provision_project_chunk_graph):
        error = {
            "code": "chunk_provisioning_service_unavailable",
            "message": "project_chunk_provisioning_service could not be imported.",
        }
        _set_project_chunk_status(project, status=CHUNK_STATUS_ERROR, error=error)
        db.session.add(project)
        _record_project_event(
            project,
            action="chunk_provisioning_failed",
            category="service_link",
            actor_user_id=uid,
            payload={"service": SERVICE_CHUNK, "error": error},
            commit=False,
        )
        _db_flush_or_commit(commit)
        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={"ok": False, "chunk": _project_chunk_refs(project), "error": error},
            status_code=503,
            code=error["code"],
            error=error["message"],
        )

    operation = provision_project_chunk_graph(
        project,
        owner_auth_user_id=auth_user_id,
        force=force,
        commit=commit,
        raise_on_error=False,
        request_id=uuid.uuid4().hex,
        audit_writer=_chunk_provisioning_audit_writer,
    )
    operation_payload = (
        _safe_dict(operation.to_dict())
        if hasattr(operation, "to_dict")
        else _safe_dict(operation)
    )

    if not bool(getattr(operation, "ok", operation_payload.get("ok"))):
        error = _safe_dict(operation_payload.get("error")) or {
            "code": _safe_str(operation_payload.get("code"), "chunk_provisioning_failed", 160),
            "message": "Chunk provisioning failed.",
        }
        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={
                "ok": False,
                "chunk": _project_chunk_refs(project),
                "chunk_result": operation_payload,
                "error": error,
            },
            status_code=_safe_int(operation_payload.get("statusCode"), 502),
            code=_safe_str(operation_payload.get("code"), "chunk_provisioning_failed", 160),
            error=_safe_str(error.get("message"), "Chunk provisioning failed.", 2000),
        )

    refs = _project_chunk_refs(project)
    try:
        _upsert_chunk_service_links_from_refs(project, refs=refs, user_id=uid)
        db.session.add(project)
        _record_project_event(
            project,
            action="chunk_project_linked",
            category="service_link",
            actor_user_id=uid,
            before=existing,
            after=refs,
            payload={
                "service": SERVICE_CHUNK,
                "actor_auth_user_id": auth_user_id,
                "chunk_result": operation_payload,
            },
            commit=False,
        )
        _db_flush_or_commit(commit)
    except Exception as exc:
        _db_rollback_safely()
        try:
            if hasattr(project, "mark_chunk_provisioning_failed"):
                project.mark_chunk_provisioning_failed(
                    code="chunk_link_metadata_repair_required",
                    message="Chunk exists, but App service-link metadata could not be persisted.",
                    repair_required=True,
                )
            db.session.add(project)
            db.session.commit()
        except Exception:
            _db_rollback_safely()
        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={
                "ok": False,
                "chunk": _project_chunk_refs(project),
                "chunk_result": operation_payload,
                "error": {
                    "code": "chunk_link_metadata_repair_required",
                    "message": str(exc),
                },
            },
            status_code=500,
            code="chunk_link_metadata_repair_required",
            error="Chunk exists, but App metadata requires repair.",
        )

    access_result = _sync_project_chunk_access_best_effort(
        project,
        actor_user_id=uid,
        actor_auth_user_id=auth_user_id,
        force=force,
        commit=commit,
    )
    final_refs = _project_chunk_refs(project)
    code = (
        "chunk_project_linked_with_flat_fallback"
        if final_refs.get("fallback_used")
        else "chunk_project_linked"
    )
    return ProjectOperationResult(
        ok=True,
        project=project,
        payload={
            "ok": True,
            "chunk": final_refs,
            "chunk_result": operation_payload,
            "access_sync": access_result,
        },
        status_code=200,
        code=code,
    )


def retry_project_chunk_link(
    project: Any,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> ProjectOperationResult:
    return ensure_project_chunk_link(
        project,
        user_id=user_id,
        force=True,
        commit=commit,
    )


def sync_project_chunk_access_for_project(
    project: Any,
    *,
    user_id: Optional[int] = None,
    force: bool = True,
    commit: bool = True,
) -> ProjectOperationResult:
    if project is None:
        return ProjectOperationResult(False, status_code=400, code="project_required", error="project required")
    uid = require_persistent_actor(user_id)
    auth_user_id = require_actor_auth_user_id(uid)
    require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)
    result = _sync_project_chunk_access_best_effort(
        project,
        actor_user_id=uid,
        actor_auth_user_id=auth_user_id,
        force=force,
        commit=commit,
    )
    return ProjectOperationResult(
        ok=_safe_bool(result.get("ok"), False),
        project=project,
        payload={
            "ok": _safe_bool(result.get("ok"), False),
            "chunk": _project_chunk_refs(project),
            "access_sync": result,
        },
        status_code=_safe_int(result.get("statusCode") or result.get("status_code"), 200 if result.get("ok") else 502),
        code=_safe_str(result.get("code"), "chunk_access_synced" if result.get("ok") else "chunk_access_sync_failed", 160),
        error=None if result.get("ok") else _safe_str(result.get("message") or result.get("error"), "Chunk access synchronization failed.", 2000),
    )


# ─────────────────────────────────────────────────────────────
# Project loading
# ─────────────────────────────────────────────────────────────

def get_project_by_id(project_id: Any, *, include_deleted: bool = False) -> Optional[Any]:
    try:
        value = _safe_str(project_id, "", 120)

        if not value:
            return None

        project = None

        numeric_id = _safe_int(value, 0)
        if numeric_id:
            project = Project.query.get(numeric_id)

        if project is None:
            project = Project.query.filter_by(public_id=value).one_or_none()

        if project is None:
            return None

        if not include_deleted and bool(getattr(project, "is_deleted", False)):
            return None

        if not include_deleted and _safe_str(getattr(project, "status", ""), "", 40) == PROJECT_STATUS_DELETED:
            return None

        return project

    except Exception as exc:
        _log_warning("get_project_by_id failed: %s", exc.__class__.__name__)
        return None


def get_project_by_public_id(public_id: Any, *, include_deleted: bool = False) -> Optional[Any]:
    try:
        value = _safe_str(public_id, "", 120)

        if not value:
            return None

        project = Project.query.filter_by(public_id=value).one_or_none()

        if project is None:
            numeric_id = _safe_int(value, 0)
            if numeric_id:
                project = Project.query.get(numeric_id)

        if project is None:
            return None

        if not include_deleted and bool(getattr(project, "is_deleted", False)):
            return None

        if not include_deleted and _safe_str(getattr(project, "status", ""), "", 40) == PROJECT_STATUS_DELETED:
            return None

        return project

    except Exception as exc:
        _log_warning("get_project_by_public_id failed: %s", exc.__class__.__name__)
        return None


def get_project_by_conversation_id(conversation_id: Any, *, include_deleted: bool = False) -> Optional[Any]:
    try:
        value = _safe_str(conversation_id, "", 80)

        if not value:
            return None

        project = Project.query.filter_by(conversation_id=value).one_or_none()

        if project is None:
            return None

        if not include_deleted and bool(getattr(project, "is_deleted", False)):
            return None

        if not include_deleted and _safe_str(getattr(project, "status", ""), "", 40) == PROJECT_STATUS_DELETED:
            return None

        return project

    except Exception as exc:
        _log_warning("get_project_by_conversation_id failed: %s", exc.__class__.__name__)
        return None


def resolve_project(identifier: Any, *, include_deleted: bool = False) -> Optional[Any]:
    try:
        if identifier is None:
            return None

        try:
            if isinstance(identifier, Project):
                return identifier
        except Exception:
            pass

        try:
            if hasattr(identifier, "id") and hasattr(identifier, "public_id"):
                return identifier
        except Exception:
            pass

        value = _safe_str(identifier, "", 160)

        if not value or value.lower() in {"new", "create", "neu"}:
            return None

        project = get_project_by_public_id(value, include_deleted=include_deleted)

        if project is not None:
            return project

        return get_project_by_conversation_id(value, include_deleted=include_deleted)

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Serialization
# ─────────────────────────────────────────────────────────────

def serialize_project(
    project: Any,
    *,
    user_id: Optional[int] = None,
    include_permissions: bool = True,
    include_members: bool = False,
    include_service_links: bool = False,
    include_versions: bool = False,
    include_embed_policy: bool = False,
    include_publication: bool = True,
) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        if hasattr(project, "normalize_lifecycle"):
            try:
                project.normalize_lifecycle()
            except Exception:
                pass

        if hasattr(project, "to_dict"):
            try:
                payload = project.to_dict(
                    include_private=True,
                    include_paths=True,
                    include_refs=True,
                    include_address=True,
                )
            except TypeError:
                payload = project.to_dict(include_private=True)
        else:
            payload = {
                "id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "name": getattr(project, "name", None),
                "description": getattr(project, "description", None),
                "status": getattr(project, "status", None),
            }

        address_text = get_project_address_text(project, allow_structured_fallback=True)
        visibility = _normalize_visibility(getattr(project, "visibility", PROJECT_VISIBILITY_PRIVATE))
        chunk_refs = _project_chunk_refs(project)
        is_demo = _project_is_demo(project)

        payload["address_text"] = address_text
        payload["addressText"] = address_text
        payload["address"] = {
            "text": address_text,
            "street": getattr(project, "street", None),
            "house_number": getattr(project, "house_number", None),
            "postal_code": getattr(project, "postal_code", None),
            "city": getattr(project, "city", None),
            "region": getattr(project, "region", None),
            "country": getattr(project, "country", None),
        }
        payload["coordinates"] = {
            "latitude": getattr(project, "latitude", None),
            "longitude": getattr(project, "longitude", None),
            "srid": getattr(project, "coordinate_srid", None),
            "source": "geocoder_or_legacy",
        }
        payload["geocode"] = _safe_dict(_get_project_metadata(project).get("geocode"))
        payload["geocode_status"] = _geocode_status_from_project(project)
        payload["visibility"] = PROJECT_VISIBILITY_PRIVATE if is_demo else visibility
        payload["is_public"] = False if is_demo else visibility == PROJECT_VISIBILITY_PUBLIC
        payload["isPublic"] = False if is_demo else visibility == PROJECT_VISIBILITY_PUBLIC
        payload["is_unlisted"] = False if is_demo else visibility == PROJECT_VISIBILITY_UNLISTED
        payload["isUnlisted"] = False if is_demo else visibility == PROJECT_VISIBILITY_UNLISTED

        payload["is_demo"] = is_demo
        payload["isDemo"] = is_demo
        payload["project_scope"] = getattr(project, "project_scope", PROJECT_SCOPE_DEMO if is_demo else PROJECT_SCOPE_PERSONAL)
        payload["projectScope"] = payload["project_scope"]

        if is_demo and callable(demo_project_public_payload):
            try:
                payload["demo"] = demo_project_public_payload(project, get_current_user_context(ensure=False))
            except Exception:
                payload["demo"] = {
                    "is_demo": True,
                    "expires_at": _iso(getattr(project, "demo_expires_at", None)),
                }

        payload["url"] = project_public_url(project)
        payload["href"] = project_public_url(project)
        payload["paths"] = build_project_paths(project)

        payload["chunk"] = chunk_refs
        payload["chunk_ready"] = bool(chunk_refs.get("ready"))
        payload["chunkReady"] = bool(chunk_refs.get("ready"))
        payload["chunk_status"] = chunk_refs.get("status")
        payload["chunkStatus"] = chunk_refs.get("status")
        payload["chunk_project_id"] = chunk_refs.get("chunk_project_id")
        payload["chunkProjectId"] = chunk_refs.get("chunk_project_id")
        payload["chunk_universe_id"] = chunk_refs.get("chunk_universe_id")
        payload["chunkUniverseId"] = chunk_refs.get("chunk_universe_id")
        payload["chunk_world_id"] = chunk_refs.get("chunk_world_id")
        payload["chunkWorldId"] = chunk_refs.get("chunk_world_id")
        payload["chunk_provisioning"] = _safe_dict(chunk_refs.get("provisioning"))
        payload["chunkProvisioning"] = _safe_dict(chunk_refs.get("provisioning"))
        payload["chunk_access_sync"] = _safe_dict(chunk_refs.get("access_sync"))
        payload["chunkAccessSync"] = _safe_dict(chunk_refs.get("access_sync"))
        payload["chunk_provisioning_status"] = chunk_refs.get("provisioning_status")
        payload["chunkProvisioningStatus"] = chunk_refs.get("provisioning_status")
        payload["chunk_world_template_requested"] = chunk_refs.get("requested_world_template")
        payload["chunkWorldTemplateRequested"] = chunk_refs.get("requested_world_template")
        payload["chunk_world_template_effective"] = chunk_refs.get("effective_world_template")
        payload["chunkWorldTemplateEffective"] = chunk_refs.get("effective_world_template")
        payload["chunk_fallback_used"] = bool(chunk_refs.get("fallback_used"))
        payload["chunkFallbackUsed"] = bool(chunk_refs.get("fallback_used"))
        payload["chunk_fallback_reason"] = chunk_refs.get("fallback_reason")
        payload["chunkFallbackReason"] = chunk_refs.get("fallback_reason")

        if include_permissions:
            payload["access"] = serialize_project_permissions(project, user_id=user_id)

        if include_members and not is_demo:
            payload["members"] = list_project_memberships(project)

        if include_service_links:
            payload["service_links"] = list_project_service_links(project)

        if include_versions:
            payload["versions"] = list_project_versions(project)

        if include_embed_policy and not is_demo:
            policy = get_or_create_embed_policy(project, user_id=user_id, commit=False)
            payload["embed_policy"] = (
                model_serialize_embed_policy(policy)
                if policy is not None
                else {}
            )

        if include_publication and callable(get_project_publication) and not is_demo:
            try:
                publication_result = get_project_publication(
                    project,
                    actor_user_id=user_id,
                    include_private=bool(
                        include_embed_policy
                        or _safe_bool(payload.get("access", {}).get("can_manage"), False)
                    ),
                    for_public=False,
                    use_cache=False,
                )
                payload["publication"] = _safe_dict(publication_result.get("publication"))
            except Exception:
                payload["publication"] = {}
        elif is_demo:
            payload["publication"] = {
                "visibility": PROJECT_VISIBILITY_PRIVATE,
                "published_workspaces": [],
                "public": False,
                "reason": "demo_not_public",
            }

        return payload

    except Exception as exc:
        _log_warning("serialize_project failed: %s", exc.__class__.__name__)
        return {
            "id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "name": getattr(project, "name", None),
            "error": "serialize_failed",
        }


def serialize_project_sidebar_item(project: Any, *, user_id: Optional[int] = None) -> Dict[str, Any]:
    try:
        if hasattr(project, "to_sidebar_item"):
            item = project.to_sidebar_item()
        else:
            public_id = _safe_str(getattr(project, "public_id", None), "", 120)
            item = {
                "id": public_id or getattr(project, "id", ""),
                "projectId": public_id or getattr(project, "id", ""),
                "public_id": public_id,
                "title": getattr(project, "name", None) or "Unbenanntes Projekt",
                "subtitle": get_project_address_text(project, allow_structured_fallback=True)
                or getattr(project, "setup_status", None)
                or "Projekt",
                "href": f"/project={public_id}" if public_id else "/project=new",
                "source": "projects_api",
            }

        chunk_refs = _project_chunk_refs(project)
        access = serialize_project_permissions(project, user_id=user_id)
        is_demo = _project_is_demo(project)

        item["permissions"] = access.get("permissions", {})
        item["access"] = access
        item["isConfigured"] = bool(getattr(project, "is_configured", False))
        item["is_configured"] = bool(getattr(project, "is_configured", False))
        item["isDemo"] = is_demo
        item["is_demo"] = is_demo
        item["demoExpiresAt"] = _iso(getattr(project, "demo_expires_at", None))
        item["conversationId"] = getattr(project, "conversation_id", None)
        item["conversation_id"] = getattr(project, "conversation_id", None)
        item["visibility"] = PROJECT_VISIBILITY_PRIVATE if is_demo else _normalize_visibility(getattr(project, "visibility", PROJECT_VISIBILITY_PRIVATE))

        item["chunkReady"] = bool(chunk_refs.get("ready"))
        item["chunk_ready"] = bool(chunk_refs.get("ready"))
        item["chunkStatus"] = chunk_refs.get("status")
        item["chunk_status"] = chunk_refs.get("status")
        item["chunkProjectId"] = chunk_refs.get("chunk_project_id")
        item["chunk_project_id"] = chunk_refs.get("chunk_project_id")
        item["chunkWorldId"] = chunk_refs.get("chunk_world_id")
        item["chunk_world_id"] = chunk_refs.get("chunk_world_id")
        item["chunkProvisioningStatus"] = chunk_refs.get("provisioning_status")
        item["chunk_provisioning_status"] = chunk_refs.get("provisioning_status")
        item["chunkWorldTemplate"] = chunk_refs.get("effective_world_template")
        item["chunk_world_template"] = chunk_refs.get("effective_world_template")
        item["chunkFallbackUsed"] = bool(chunk_refs.get("fallback_used"))
        item["chunk_fallback_used"] = bool(chunk_refs.get("fallback_used"))
        item["chunkAccessSync"] = _safe_dict(chunk_refs.get("access_sync"))

        return item

    except Exception:
        return {
            "id": getattr(project, "public_id", None) or getattr(project, "id", ""),
            "title": getattr(project, "name", None) or "Projekt",
            "href": project_public_url(project),
            "source": "projects_api",
        }


def serialize_project_list(
    projects: Iterable[Any],
    *,
    user_id: Optional[int] = None,
    include_permissions: bool = True,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    try:
        for project in list(projects or []):
            try:
                result.append(
                    serialize_project(
                        project,
                        user_id=user_id,
                        include_permissions=include_permissions,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass

    return result


# ─────────────────────────────────────────────────────────────
# Project list / query
# ─────────────────────────────────────────────────────────────

def list_projects_for_user(
    user_id: Optional[int] = None,
    *,
    include_public: bool = False,
    include_unlisted: bool = False,
    include_deleted: bool = False,
    include_demo: bool = False,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Any]:
    try:
        context = get_actor_context(user_id)

        if _safe_bool(context.get("blocked"), False):
            return []

        if _safe_bool(context.get("demo_mode"), False):
            return []

        uid = get_actor_user_id_optional(user_id)
        auth_user_id = _safe_str(context.get("auth_user_id") or context.get("authUserId"), "", 160)
        account_id = _safe_str(context.get("account_id") or context.get("accountId"), "", 160)

        if not uid and not auth_user_id and not include_public:
            return []

        limit_value = _query_limit(limit, 100, 500)
        offset_value = max(_safe_int(offset, 0), 0)

        membership_project_ids: List[int] = []

        if uid:
            try:
                memberships = ProjectMembership.query.filter_by(user_id=uid, status="active").all()
                for membership in memberships:
                    project_id = _safe_int(getattr(membership, "project_id", None), 0)
                    if project_id and project_id not in membership_project_ids:
                        membership_project_ids.append(project_id)
            except Exception:
                membership_project_ids = []

        query = Project.query
        conditions = []

        if uid:
            conditions.append(Project.owner_user_id == uid)

        if auth_user_id and hasattr(Project, "auth_owner_user_id"):
            conditions.append(Project.auth_owner_user_id == auth_user_id)

        if account_id and hasattr(Project, "auth_account_id"):
            conditions.append(Project.auth_account_id == account_id)

        if membership_project_ids:
            conditions.append(Project.id.in_(membership_project_ids))

        if include_public:
            conditions.append(Project.is_public.is_(True))
            conditions.append(Project.visibility == PROJECT_VISIBILITY_PUBLIC)

        if include_unlisted:
            conditions.append(Project.visibility == PROJECT_VISIBILITY_UNLISTED)

        if conditions:
            if or_ is not None:
                query = query.filter(or_(*conditions))
            elif uid:
                query = query.filter(Project.owner_user_id == uid)
        else:
            return []

        if not include_demo and hasattr(Project, "is_demo"):
            query = query.filter(Project.is_demo.is_(False))

        if not include_deleted:
            query = query.filter(Project.status != PROJECT_STATUS_DELETED)
            query = query.filter(Project.status != PROJECT_STATUS_EXPIRED)

        if search:
            needle = f"%{_safe_str(search, '', 120)}%"
            try:
                if or_ is not None:
                    query = query.filter(
                        or_(
                            Project.name.ilike(needle),
                            Project.description.ilike(needle),
                            Project.address_text.ilike(needle),
                            Project.street.ilike(needle),
                            Project.city.ilike(needle),
                            Project.postal_code.ilike(needle),
                        )
                    )
            except Exception:
                pass

        try:
            query = query.order_by(Project.updated_at.desc(), Project.created_at.desc())
        except Exception:
            pass

        if offset_value:
            query = query.offset(offset_value)

        return list(query.limit(limit_value).all())

    except Exception as exc:
        _log_exception("list_projects_for_user failed", exc)
        return []


def list_project_sidebar_items(
    user_id: Optional[int] = None,
    *,
    include_public: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    try:
        projects = list_projects_for_user(
            user_id=user_id,
            include_public=include_public,
            include_unlisted=False,
            include_deleted=False,
            include_demo=False,
            limit=limit,
        )

        return [
            serialize_project_sidebar_item(project, user_id=user_id)
            for project in projects
        ]

    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Project creation / update payload
# ─────────────────────────────────────────────────────────────

def _normalize_project_payload(
    data: Optional[Mapping[str, Any]],
    *,
    for_update: bool = False,
    existing_project: Any = None,
) -> Dict[str, Any]:
    source = _safe_dict(data)
    present: Set[str] = set()

    try:
        payload: Dict[str, Any] = {
            "__present": present,
            "metadata": _safe_dict(source.get("metadata") or source.get("meta")),
            "settings": _safe_dict(source.get("settings")),
            "service_refs": {},
            "artifact_refs": {},
        }

        if not for_update or _payload_has(source, "name", "title", "project_name"):
            name = (
                source.get("name")
                or source.get("title")
                or source.get("project_name")
                or DEFAULT_PROJECT_NAME
            )
            payload["name"] = _safe_str(name, DEFAULT_PROJECT_NAME, 255) or DEFAULT_PROJECT_NAME
            present.add("name")

        if not for_update or _payload_has(source, "description"):
            description = _safe_str(source.get("description"), "", 10000)
            payload["description"] = description or None
            present.add("description")

        raw_address = source.get("address")
        address = _safe_dict(raw_address)
        address_text_value = source.get("address_text")

        if address_text_value is None and _payload_has(source, "addressText"):
            address_text_value = source.get("addressText")

        if address_text_value is None:
            if isinstance(raw_address, Mapping):
                address_text_value = address.get("text")
            elif isinstance(raw_address, str):
                address_text_value = raw_address

        if not for_update or address_text_value is not None:
            payload["address_text"] = _safe_str(address_text_value, "", 2000) or None
            present.add("address_text")

        if not for_update or _payload_has(source, "visibility", "is_public", "public"):
            visibility_input = source.get("visibility")

            if visibility_input is None and _safe_bool(source.get("is_public", source.get("public")), False):
                visibility_input = PROJECT_VISIBILITY_PUBLIC

            visibility = _normalize_visibility(visibility_input, PROJECT_VISIBILITY_PRIVATE)
            payload["visibility"] = visibility
            payload["is_public"] = visibility == PROJECT_VISIBILITY_PUBLIC
            present.add("visibility")
            present.add("is_public")

        structured = _structured_address_payload_from_geocoder(source)
        if structured:
            for key, value in structured.items():
                payload[key] = value
                present.add(key)

        allow_system_refs = _safe_bool(
            source.get("allow_system_refs")
            if "allow_system_refs" in source
            else _config_bool("VECTOPLAN_PROJECT_PAYLOAD_ALLOW_SYSTEM_REFS", False),
            False,
        )

        if allow_system_refs:
            system_fields = {
                "chunk_project_id": source.get("chunk_project_id") or source.get("chunkProjectId"),
                "chunk_universe_id": source.get("chunk_universe_id") or source.get("chunkUniverseId"),
                "chunk_world_id": source.get("chunk_world_id") or source.get("chunkWorldId"),
                "plan2d_id": source.get("plan2d_id") or source.get("plan2dId"),
                "lv_id": source.get("lv_id") or source.get("lvId"),
                "service_refs": source.get("service_refs") or source.get("serviceRefs"),
                "artifact_refs": source.get("artifact_refs") or source.get("artifactRefs"),
            }

            for key, value in system_fields.items():
                if value is None:
                    continue
                if key in {"service_refs", "artifact_refs"}:
                    payload[key] = _safe_dict(value)
                else:
                    payload[key] = _safe_str(value, "", 160) or None
                present.add(key)

        if not for_update:
            payload.setdefault("name", DEFAULT_PROJECT_NAME)
            payload.setdefault("description", None)
            payload.setdefault("address_text", None)
            payload.setdefault("visibility", PROJECT_VISIBILITY_PRIVATE)
            payload.setdefault("is_public", False)
            payload.setdefault("country", DEFAULT_ADDRESS_COUNTRY)
            payload.setdefault("coordinate_srid", DEFAULT_COORDINATE_SRID)
            present.update({"name", "description", "address_text", "visibility", "is_public"})

        projected_name = payload.get("name")
        if for_update and projected_name is None and existing_project is not None:
            projected_name = getattr(existing_project, "name", None)

        projected_address = payload.get("address_text")
        if for_update and "address_text" not in present and existing_project is not None:
            projected_address = get_project_address_text(existing_project, allow_structured_fallback=True)

        is_defined = bool(
            _safe_str(projected_name, "", 255)
            and _safe_str(projected_address, "", 2000)
        )

        payload["setup_status"] = PROJECT_SETUP_CONFIGURED if is_defined else PROJECT_SETUP_DRAFT
        present.add("setup_status")

        return payload

    except Exception:
        return {
            "__present": {"name", "description", "address_text", "visibility", "is_public", "setup_status"},
            "name": DEFAULT_PROJECT_NAME,
            "description": None,
            "address_text": None,
            "visibility": PROJECT_VISIBILITY_PRIVATE,
            "is_public": False,
            "setup_status": PROJECT_SETUP_DRAFT,
            "service_refs": {},
            "artifact_refs": {},
            "settings": {},
            "metadata": {},
        }


def _apply_project_payload(project: Any, payload: Dict[str, Any]) -> Any:
    try:
        present = set(payload.get("__present") or [])
        before_address_text = get_project_address_text(project, allow_structured_fallback=False)
        is_demo = _project_is_demo(project)

        for key in ["name", "description", "address_text", "settings"]:
            if key not in present:
                continue
            if hasattr(project, key):
                setattr(project, key, payload.get(key))

        if not is_demo:
            for key in ["visibility", "is_public"]:
                if key not in present:
                    continue
                if hasattr(project, key):
                    setattr(project, key, payload.get(key))

        structured_fields = [
            "street",
            "house_number",
            "postal_code",
            "city",
            "region",
            "country",
            "latitude",
            "longitude",
            "coordinate_srid",
        ]

        for key in structured_fields:
            if key in present and hasattr(project, key):
                setattr(project, key, payload.get(key))

        if "visibility" in present:
            visibility = PROJECT_VISIBILITY_PRIVATE if is_demo else _normalize_visibility(payload.get("visibility"), PROJECT_VISIBILITY_PRIVATE)
            project.visibility = visibility
            if hasattr(project, "is_public"):
                project.is_public = False if is_demo else visibility == PROJECT_VISIBILITY_PUBLIC

        if "setup_status" in present:
            project.setup_status = payload.get("setup_status") or PROJECT_SETUP_DRAFT

        if _project_has_minimum_definition(project):
            if hasattr(project, "mark_configured"):
                project.mark_configured()
            else:
                project.setup_status = PROJECT_SETUP_CONFIGURED
                project.setup_completed_at = getattr(project, "setup_completed_at", None) or _utcnow()
        else:
            if getattr(project, "setup_status", None) == PROJECT_SETUP_CONFIGURED:
                project.setup_status = PROJECT_SETUP_DEFINED

        new_address_text = get_project_address_text(project, allow_structured_fallback=False)
        if "address_text" in present:
            _mark_geocode_pending_or_stale(
                project,
                old_address_text=before_address_text,
                new_address_text=new_address_text,
                source="project_form",
            )

        if "geocode_payload" in present or "geocode_status" in present:
            metadata = _get_project_metadata(project)
            geocode = _safe_dict(metadata.get("geocode"))
            geocode.update(
                {
                    "status": _safe_str(payload.get("geocode_status"), GEOCODE_STATUS_RESOLVED, 40),
                    "payload": _safe_dict(payload.get("geocode_payload")),
                    "updated_at": _iso(_utcnow()),
                    "source": "geocoder_payload",
                }
            )
            metadata["geocode"] = geocode
            _set_project_metadata(project, metadata)

        metadata_patch = _safe_dict(payload.get("metadata"))
        if metadata_patch:
            _merge_project_metadata(project, metadata_patch)

        if hasattr(project, "updated_at"):
            project.updated_at = _utcnow()

        if hasattr(project, "normalize_lifecycle"):
            project.normalize_lifecycle()

        return project

    except Exception:
        return project


# ─────────────────────────────────────────────────────────────
# Conversation / embed / audit
# ─────────────────────────────────────────────────────────────

def _create_conversation_for_project(project: Any, *, title: Optional[str] = None) -> Any:
    conv = Conversation()

    try:
        conv.project_id = str(project.id)
        conv.title = _safe_str(title or getattr(project, "name", None), "Projekt-Status", 255)
        conv.owner_user_id = _safe_int(getattr(project, "owner_user_id", None), 0) or None
        conv.transcript = []
        conv.state = {}
        conv.status = "active"

        if hasattr(conv, "normalize"):
            conv.normalize()

        db.session.add(conv)
        db.session.flush()

        project.conversation_id = conv.id
        db.session.add(project)

        return conv

    except Exception:
        raise


def get_or_create_project_conversation(project: Any, *, commit: bool = False) -> Any:
    try:
        if project is None:
            return None

        conversation_id = _safe_str(getattr(project, "conversation_id", None), "", 80)

        if conversation_id:
            conv = Conversation.query.get(conversation_id)
            if conv is not None:
                return conv

        conv = _create_conversation_for_project(project, title=getattr(project, "name", None))

        _db_flush_or_commit(commit)
        return conv

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


def get_or_create_embed_policy(project: Any, *, user_id: Optional[int] = None, commit: bool = False) -> Any:
    try:
        if project is None or _project_is_demo(project):
            return None

        policy = ProjectEmbedPolicy.query.filter_by(project_id=project.id).one_or_none()

        if policy is None:
            policy = model_build_embed_policy(
                project_id=project.id,
                created_by_user_id=get_current_user_id(user_id) or None,
            )
            db.session.add(policy)
            _db_flush_or_commit(commit)

        return policy

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


def _record_project_event(
    project: Any,
    *,
    action: str,
    category: str = "project",
    actor_user_id: Optional[int] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    changes: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    commit: bool = False,
    **kwargs: Any,
) -> Optional[Any]:
    try:
        return model_record_project_audit_event(
            project,
            action=action,
            category=category,
            actor_user_id=actor_user_id,
            before=before,
            after=after,
            changes=changes,
            payload=payload,
            commit=commit,
            **kwargs,
        )
    except Exception as exc:
        _log_warning("record project audit failed: %s", exc.__class__.__name__)
        return None


# ─────────────────────────────────────────────────────────────
# Demo project helpers
# ─────────────────────────────────────────────────────────────

def _ensure_or_update_demo_project(
    data: Optional[Dict[str, Any]] = None,
    *,
    commit: bool = True,
    provision_chunk: Optional[bool] = None,
) -> Any:
    context = get_current_user_context(ensure=False)

    if _safe_bool(_safe_dict(context).get("blocked"), False):
        raise PermissionDenied(
            "Der Zugriff ist gesperrt.",
            code="auth_blocked",
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    context_dict = _safe_dict(context)
    if not context_dict and hasattr(context, "to_dict"):
        context_dict = _safe_dict(context.to_dict())

    if not _safe_bool(context_dict.get("demo_mode") or context_dict.get("is_demo"), False):
        raise PermissionDenied(
            "Demo-Projekt kann nur im Demo-Modus genutzt werden.",
            code="demo_context_required",
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    if ensure_demo_project_for_context is None:
        raise PermissionDenied(
            "Demo-Projekt-Service ist nicht verfügbar.",
            code="demo_project_service_unavailable",
            status_code=503,
            permission=PERMISSION_EDIT,
        )

    result = ensure_demo_project_for_context(
        context=context,
        reset=False,
        commit=commit,
        provision_chunk=provision_chunk,
    )

    if not getattr(result, "ok", False) or getattr(result, "project", None) is None:
        raise PermissionDenied(
            getattr(result, "error", None) or getattr(result, "reason", "demo_project_unavailable"),
            code=getattr(result, "reason", "demo_project_unavailable"),
            status_code=403,
            permission=PERMISSION_EDIT,
        )

    project = result.project

    if data:
        payload = _normalize_project_payload(data, for_update=True, existing_project=project)
        _apply_project_payload(project, payload)

        if hasattr(project, "mark_demo"):
            try:
                project.mark_demo(
                    demo_client_identity_id=getattr(getattr(result, "demo_identity", None), "raw_identity", None),
                    demo_session_id=getattr(getattr(result, "demo_identity", None), "demo_session_id", None),
                    demo_expires_at=getattr(result, "expires_at", None),
                    reason="demo_update",
                )
            except Exception:
                pass

        get_or_create_project_conversation(project, commit=False)
        db.session.add(project)

        _record_project_event(
            project,
            action="demo_updated",
            category="project",
            actor_user_id=None,
            before={},
            after=serialize_project(project, user_id=None, include_permissions=False),
            payload={
                "source": "project_service._ensure_or_update_demo_project",
                "demo": True,
                "payload_fields": sorted(list(payload.get("__present") or [])),
            },
            commit=False,
        )

        _db_flush_or_commit(commit)

    return project


# ─────────────────────────────────────────────────────────────
# Project creation / update
# ─────────────────────────────────────────────────────────────

def create_project(
    data: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
    provision_chunk: Optional[bool] = None,
) -> Any:
    """Create the App project first, then provision Chunk in a second transaction.

    This ordering deliberately avoids pretending that the App and Chunk databases
    share one transaction. A Chunk failure never deletes the already-created App
    project; instead the project retains a retryable provisioning state.
    """
    uid = require_persistent_actor(user_id)
    context = get_actor_context(uid)
    auth_user_id = require_actor_auth_user_id(uid)
    auth_account_id = _safe_str(
        context.get("account_id") or context.get("accountId"),
        "",
        160,
    ) or None

    payload = _normalize_project_payload(data, for_update=False)
    visibility = _normalize_visibility(payload.get("visibility"), PROJECT_VISIBILITY_PRIVATE)
    project_scope = PROJECT_SCOPE_ACCOUNT if auth_account_id else PROJECT_SCOPE_PERSONAL
    should_provision = _chunk_provisioning_enabled() if provision_chunk is None else bool(provision_chunk)

    project = Project(
        owner_user_id=uid,
        auth_owner_user_id=auth_user_id,
        auth_account_id=auth_account_id,
        owner_subject_type="user",
        project_scope=project_scope,
        is_demo=False,
        name=payload.get("name") or DEFAULT_PROJECT_NAME,
        description=payload.get("description"),
        address_text=payload.get("address_text"),
        street=payload.get("street"),
        house_number=payload.get("house_number"),
        postal_code=payload.get("postal_code"),
        city=payload.get("city"),
        region=payload.get("region"),
        country=payload.get("country") or DEFAULT_ADDRESS_COUNTRY,
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        coordinate_srid=payload.get("coordinate_srid") or DEFAULT_COORDINATE_SRID,
        service_refs=_safe_dict(payload.get("service_refs")),
        artifact_refs=_safe_dict(payload.get("artifact_refs")),
        visibility=visibility,
        is_public=visibility == PROJECT_VISIBILITY_PUBLIC,
        status=PROJECT_STATUS_ACTIVE,
        setup_status=payload.get("setup_status") or PROJECT_SETUP_DRAFT,
        settings=_safe_dict(payload.get("settings")),
        metadata_json={
            "created_via": "project_service.create_project",
            "project_form_version": 4,
            "address_input_mode": "single_box",
            "system_refs_hidden_in_project_form": True,
            "auth_owner_user_id": auth_user_id,
            "auth_account_id": auth_account_id,
            "project_scope": project_scope,
            "chunkProvisioningContract": "earth-default-flat-fallback.v1",
            **_safe_dict(payload.get("metadata")),
        },
    )

    try:
        if payload.get("geocode_payload") or payload.get("geocode_status"):
            metadata = _get_project_metadata(project)
            metadata["geocode"] = {
                "status": _safe_str(payload.get("geocode_status"), GEOCODE_STATUS_PENDING, 40),
                "payload": _safe_dict(payload.get("geocode_payload")),
                "updated_at": _iso(_utcnow()),
                "source": "create_payload",
            }
            _set_project_metadata(project, metadata)
        elif payload.get("address_text"):
            _mark_geocode_pending_or_stale(
                project,
                old_address_text=None,
                new_address_text=payload.get("address_text"),
                source="project_create",
            )

        if _project_has_minimum_definition(project):
            if hasattr(project, "mark_configured"):
                project.mark_configured()
            else:
                project.setup_status = PROJECT_SETUP_CONFIGURED
                project.setup_completed_at = _utcnow()

        if should_provision:
            _set_project_chunk_status(project, status=CHUNK_STATUS_PENDING)
            _mark_chunk_access_sync_pending(project)
        else:
            _set_project_chunk_status(project, status=CHUNK_STATUS_DISABLED)
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = CHUNK_ACCESS_SYNC_DISABLED

        if hasattr(project, "normalize_lifecycle"):
            project.normalize_lifecycle()

        db.session.add(project)
        db.session.flush()
        conv = _create_conversation_for_project(project, title=project.name)

        try:
            if callable(permissions_grant_project_role):
                permissions_grant_project_role(
                    project,
                    user_id=uid,
                    role=ROLE_OWNER,
                    actor_user_id=uid,
                    commit=False,
                    allow_owner=True,
                )
            else:
                db.session.add(
                    model_build_membership(
                        project_id=project.id,
                        user_id=uid,
                        role=ROLE_OWNER,
                        permissions={},
                    )
                )
        except Exception as exc:
            _log_warning("owner membership creation failed: %s", exc.__class__.__name__)

        get_or_create_embed_policy(project, user_id=uid, commit=False)

        if callable(publication_set_project_visibility):
            try:
                publication_set_project_visibility(project, visibility, actor_user_id=uid, commit=False)
            except Exception:
                pass

        _record_project_event(
            project,
            action="created",
            category="project",
            actor_user_id=uid,
            before={},
            after=serialize_project(project, user_id=uid, include_permissions=False),
            payload={
                "conversation_id": getattr(conv, "id", None),
                "address_input_mode": "single_box",
                "visibility": visibility,
                "auth_owner_user_id": auth_user_id,
                "auth_account_id": auth_account_id,
                "project_scope": project_scope,
                "chunk_provisioning_requested": should_provision,
                "requested_world_template": CHUNK_WORLD_TEMPLATE_EARTH,
                "fallback_world_template": CHUNK_WORLD_TEMPLATE_FLAT,
            },
            commit=False,
        )

        # No external side effect is allowed while an outer caller owns the DB transaction.
        _db_flush_or_commit(commit)

    except Exception:
        _db_rollback_safely()
        raise

    if should_provision and commit:
        try:
            chunk_result = ensure_project_chunk_link(
                project,
                user_id=uid,
                force=False,
                commit=True,
            )
            try:
                setattr(project, "_last_chunk_operation_result", chunk_result)
            except Exception:
                pass
        except Exception as exc:
            # The App project is already durable. Record a repairable state instead
            # of attempting to roll it back across the service boundary.
            _db_rollback_safely()
            _set_project_chunk_status(
                project,
                status=CHUNK_STATUS_ERROR,
                error={
                    "code": _safe_str(getattr(exc, "code", None), "chunk_provision_exception", 160),
                    "message": _safe_str(str(exc), "Chunk provisioning failed.", 2000),
                },
            )
            try:
                db.session.add(project)
                _record_project_event(
                    project,
                    action="chunk_provisioning_failed",
                    category="service_link",
                    actor_user_id=uid,
                    payload={
                        "actor_auth_user_id": auth_user_id,
                        "exception_type": exc.__class__.__name__,
                        "message": _safe_str(str(exc), "", 1000),
                    },
                    commit=False,
                )
                db.session.commit()
            except Exception:
                _db_rollback_safely()

    return project


def update_project(
    project: Any,
    data: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
    provision_chunk_if_missing: bool = True,
) -> Any:
    if project is None:
        raise ValueError("project required")

    if _project_is_demo(project):
        result = get_project_permission_result(project, user_id=user_id, allow_public_view=False)
        if not result.can_edit:
            raise PermissionDenied(
                "Demo-Projekt ist für diesen Guest nicht bearbeitbar.",
                permission=PERMISSION_EDIT,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_project_permission_denied",
            )
        uid = None
        auth_user_id = None
    else:
        uid = require_persistent_actor(user_id)
        auth_user_id = require_actor_auth_user_id(uid)
        require_project_permission(project, PERMISSION_EDIT, uid, allow_public_view=False)

    before = serialize_project(project, user_id=uid, include_permissions=False)
    payload = _normalize_project_payload(data, for_update=True, existing_project=project)
    old_visibility = _normalize_visibility(getattr(project, "visibility", PROJECT_VISIBILITY_PRIVATE))
    _apply_project_payload(project, payload)
    new_visibility = _normalize_visibility(getattr(project, "visibility", PROJECT_VISIBILITY_PRIVATE))
    conv = get_or_create_project_conversation(project, commit=False)

    try:
        if conv is not None:
            conv.title = project.name or conv.title
            conv.project_id = str(project.id)
            conv.updated_at = _utcnow()
            db.session.add(conv)
    except Exception:
        pass

    if not _project_is_demo(project) and old_visibility != new_visibility and callable(publication_set_project_visibility):
        try:
            publication_set_project_visibility(project, new_visibility, actor_user_id=uid, commit=False)
        except Exception:
            pass

    should_provision = bool(
        not _project_is_demo(project)
        and provision_chunk_if_missing
        and _should_attempt_chunk_provision(project)
    )
    if should_provision:
        _set_project_chunk_status(project, status=CHUNK_STATUS_PENDING)

    db.session.add(project)
    _record_project_event(
        project,
        action="demo_updated" if _project_is_demo(project) else "updated",
        category="project",
        actor_user_id=uid,
        before=before,
        after=serialize_project(project, user_id=uid, include_permissions=False),
        payload={
            "source": "project_service.update_project",
            "address_input_mode": "single_box",
            "visibility": new_visibility,
            "payload_fields": sorted(list(payload.get("__present") or [])),
            "is_demo": _project_is_demo(project),
            "chunk_provisioning_requested": should_provision,
        },
        commit=False,
    )

    try:
        _db_flush_or_commit(commit)
    except Exception:
        _db_rollback_safely()
        raise

    if should_provision and commit:
        try:
            result = ensure_project_chunk_link(project, user_id=uid, force=False, commit=True)
            setattr(project, "_last_chunk_operation_result", result)
        except Exception as exc:
            _log_exception("post-update chunk provisioning failed", exc)

    return project


def create_or_update_project(
    data: Optional[Dict[str, Any]] = None,
    *,
    project_identifier: Optional[str] = None,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        context = get_actor_context(user_id)
        if _safe_bool(context.get("demo_mode"), False):
            return _ensure_or_update_demo_project(data, commit=commit)

        project = resolve_project(project_identifier) if project_identifier else None

        if project is None:
            return create_project(data, user_id=user_id, commit=commit)

        return update_project(project, data, user_id=user_id, commit=commit)

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


# ─────────────────────────────────────────────────────────────
# Delete / archive / transfer
# ─────────────────────────────────────────────────────────────

def delete_project(
    project: Any,
    *,
    user_id: Optional[int] = None,
    hard_delete: bool = False,
    commit: bool = True,
) -> bool:
    try:
        if project is None:
            return False

        if _project_is_demo(project):
            raise PermissionDenied(
                "Demo-Projekte werden automatisch bereinigt und können nicht als persistente Projekte gelöscht werden.",
                permission=PERMISSION_DELETE,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_delete_not_allowed",
            )

        uid = require_persistent_actor(user_id)
        require_project_permission(project, PERMISSION_DELETE, uid, allow_public_view=False)

        before = serialize_project(project, user_id=uid, include_permissions=False)

        if hard_delete:
            _record_project_event(
                project,
                action="deleted",
                category="project",
                actor_user_id=uid,
                before=before,
                after={},
                payload={"hard_delete": True},
                commit=False,
            )
            db.session.delete(project)
        else:
            try:
                project.mark_deleted(user_id=uid)
            except TypeError:
                try:
                    project.mark_deleted(uid)
                except Exception:
                    pass
            except Exception:
                pass

            project.status = PROJECT_STATUS_DELETED
            project.deleted_at = getattr(project, "deleted_at", None) or _utcnow()
            project.deleted_by_user_id = uid

            db.session.add(project)

            _record_project_event(
                project,
                action="deleted",
                category="project",
                actor_user_id=uid,
                before=before,
                after=serialize_project(project, user_id=uid, include_permissions=False),
                payload={"soft_delete": True},
                commit=False,
            )

        _db_flush_or_commit(commit)
        return True

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


def archive_project(
    project: Any,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        if _project_is_demo(project):
            raise PermissionDenied(
                "Demo-Projekte können nicht archiviert werden.",
                permission=PERMISSION_MANAGE,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_archive_not_allowed",
            )

        uid = require_persistent_actor(user_id)
        require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)

        before = serialize_project(project, user_id=uid, include_permissions=False)

        if hasattr(project, "archive"):
            project.archive(user_id=uid)
        else:
            project.status = PROJECT_STATUS_ARCHIVED
            project.archived_at = _utcnow()
            project.archived_by_user_id = uid
            project.updated_at = _utcnow()

        db.session.add(project)

        _record_project_event(
            project,
            action="archived",
            category="project",
            actor_user_id=uid,
            before=before,
            after=serialize_project(project, user_id=uid, include_permissions=False),
            commit=False,
        )

        _db_flush_or_commit(commit)
        return project

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


def transfer_project_owner(
    project: Any,
    *,
    new_owner_user_id: int,
    actor_user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    if project is None:
        raise ValueError("project required")
    if _project_is_demo(project):
        raise PermissionDenied(
            "Demo-Projekte können nicht übertragen werden.",
            permission=PERMISSION_TRANSFER,
            project_id=_project_public_id(project),
            user_id=None,
            status_code=403,
            code="demo_transfer_not_allowed",
        )

    actor_id = require_persistent_actor(actor_user_id)
    actor_auth_user_id = require_actor_auth_user_id(actor_id)
    new_owner_id = _safe_int(new_owner_user_id, 0)
    if not new_owner_id:
        raise ValueError("new_owner_user_id required")

    new_owner = _app_user_by_local_id(new_owner_id)
    new_owner_auth_user_id = _app_user_auth_user_id(new_owner)
    if not new_owner_auth_user_id:
        raise ValueError("new owner requires canonical AppUser.auth_user_id")

    require_project_permission(project, PERMISSION_TRANSFER, actor_id, allow_public_view=False)

    before = serialize_project(project, user_id=actor_id, include_permissions=True)
    old_owner_user_id = _safe_int(getattr(project, "owner_user_id", None), 0)
    old_owner_auth_user_id = _safe_str(getattr(project, "auth_owner_user_id", None), "", 160) or None

    try:
        if callable(permissions_transfer_project_ownership):
            permissions_transfer_project_ownership(
                project,
                new_owner_user_id=new_owner_id,
                actor_user_id=actor_id,
                commit=False,
            )
        elif hasattr(project, "transfer_ownership"):
            project.transfer_ownership(
                new_owner_user_id=new_owner_id,
                new_auth_owner_user_id=new_owner_auth_user_id,
            )
        else:
            project.owner_user_id = new_owner_id
            project.transferred_from_user_id = old_owner_user_id or None
            project.transferred_at = _utcnow()

        project.auth_owner_user_id = new_owner_auth_user_id
        project.owner_subject_type = "user"
        project.updated_at = _utcnow()
        _mark_chunk_access_sync_pending(project)
        db.session.add(project)

        _record_project_event(
            project,
            action="transferred",
            category="project",
            actor_user_id=actor_id,
            before=before,
            after=serialize_project(project, user_id=actor_id, include_permissions=True),
            payload={
                "old_owner_user_id": old_owner_user_id,
                "new_owner_user_id": new_owner_id,
                "old_owner_auth_user_id": old_owner_auth_user_id,
                "new_owner_auth_user_id": new_owner_auth_user_id,
                "actor_auth_user_id": actor_auth_user_id,
            },
            commit=False,
        )
        _db_flush_or_commit(commit)
    except Exception:
        _db_rollback_safely()
        raise

    if commit:
        _sync_project_chunk_access_best_effort(
            project,
            actor_user_id=actor_id,
            actor_auth_user_id=actor_auth_user_id,
            force=True,
            commit=True,
        )

    return project


# ─────────────────────────────────────────────────────────────
# Membership wrappers
# ─────────────────────────────────────────────────────────────

def list_project_memberships(project: Any, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
    try:
        if project is None or _project_is_demo(project):
            return []

        project_id = _safe_int(getattr(project, "id", None), 0)
        if not project_id:
            return []

        query = ProjectMembership.query.filter_by(project_id=project_id)

        if not include_inactive:
            query = query.filter_by(status="active")

        rows = query.order_by(ProjectMembership.created_at.asc()).all()

        result: List[Dict[str, Any]] = []
        for row in rows:
            if callable(permissions_serialize_membership):
                result.append(permissions_serialize_membership(row, include_private=True))
            else:
                result.append(model_serialize_membership(row, include_private=True))

        return result

    except Exception as exc:
        _log_warning("list_project_memberships failed: %s", exc.__class__.__name__)
        return []


def set_project_member_role(
    project: Any,
    *,
    target_user_id: int,
    role: str,
    actor_user_id: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> Any:
    if project is None:
        raise ValueError("project required")
    if _project_is_demo(project):
        raise PermissionDenied(
            "Demo-Projekte unterstützen keine Teamverwaltung.",
            permission=PERMISSION_MANAGE,
            project_id=_project_public_id(project),
            user_id=None,
            status_code=403,
            code="demo_team_not_allowed",
        )

    actor_id = require_persistent_actor(actor_user_id)
    actor_auth_user_id = require_actor_auth_user_id(actor_id)
    target_uid = _safe_int(target_user_id, 0)
    if not target_uid:
        raise ValueError("target_user_id required")

    target_user = _app_user_by_local_id(target_uid)
    target_auth_user_id = _app_user_auth_user_id(target_user)
    if not target_auth_user_id:
        raise ValueError("target AppUser requires canonical auth_user_id")

    require_project_permission(project, PERMISSION_MANAGE, actor_id, allow_public_view=False)
    clean_role = normalize_role(role)
    if clean_role == ROLE_OWNER:
        raise ValueError("owner role must be changed through transfer_project_owner")

    before: Dict[str, Any] = {}
    membership = model_get_project_membership(project.id, target_uid)
    if membership is not None:
        before = (
            permissions_serialize_membership(membership, include_private=True)
            if callable(permissions_serialize_membership)
            else model_serialize_membership(membership, include_private=True)
        )

    try:
        if callable(permissions_grant_project_role):
            membership = permissions_grant_project_role(
                project,
                user_id=target_uid,
                role=clean_role,
                actor_user_id=actor_id,
                permissions=_safe_dict(overrides),
                commit=False,
                allow_owner=False,
            )
        elif membership is None:
            membership = model_build_membership(
                project_id=project.id,
                user_id=target_uid,
                role=clean_role,
                permissions=_safe_dict(overrides),
            )
        elif hasattr(membership, "apply_role"):
            membership.apply_role(clean_role, permissions=_safe_dict(overrides))
        else:
            membership.role = clean_role

        db.session.add(membership)
        _mark_chunk_access_sync_pending(project)
        db.session.add(project)

        after = (
            permissions_serialize_membership(membership, include_private=True)
            if callable(permissions_serialize_membership)
            else model_serialize_membership(membership, include_private=True)
        )
        _record_project_event(
            project,
            action="permission_changed",
            category="access",
            actor_user_id=actor_id,
            before=before,
            after=after,
            payload={
                "target_user_id": target_uid,
                "target_auth_user_id": target_auth_user_id,
                "actor_auth_user_id": actor_auth_user_id,
                "role": clean_role,
            },
            commit=False,
        )
        _db_flush_or_commit(commit)
    except Exception:
        _db_rollback_safely()
        raise

    if commit:
        _sync_project_chunk_access_best_effort(
            project,
            actor_user_id=actor_id,
            actor_auth_user_id=actor_auth_user_id,
            force=True,
            commit=True,
        )

    return membership


def revoke_project_member(
    project: Any,
    *,
    target_user_id: int,
    actor_user_id: Optional[int] = None,
    hard_delete: bool = False,
    commit: bool = True,
) -> bool:
    if project is None:
        raise ValueError("project required")
    if _project_is_demo(project):
        raise PermissionDenied(
            "Demo-Projekte unterstützen keine Teamverwaltung.",
            permission=PERMISSION_MANAGE,
            project_id=_project_public_id(project),
            user_id=None,
            status_code=403,
            code="demo_team_not_allowed",
        )

    actor_id = require_persistent_actor(actor_user_id)
    actor_auth_user_id = require_actor_auth_user_id(actor_id)
    target_uid = _safe_int(target_user_id, 0)
    if not target_uid:
        raise ValueError("target_user_id required")

    if target_uid == _safe_int(getattr(project, "owner_user_id", None), 0):
        raise ValueError("project owner cannot be revoked; transfer ownership first")

    target_auth_user_id = _app_user_auth_user_id(_app_user_by_local_id(target_uid))
    if not target_auth_user_id:
        raise ValueError("target AppUser requires canonical auth_user_id")

    require_project_permission(project, PERMISSION_MANAGE, actor_id, allow_public_view=False)

    try:
        if callable(permissions_revoke_project_membership):
            ok = permissions_revoke_project_membership(
                project,
                user_id=target_uid,
                actor_user_id=actor_id,
                hard_delete=hard_delete,
                commit=False,
            )
        else:
            membership = model_get_project_membership(project.id, target_uid)
            if membership is None:
                return False
            membership.status = "revoked"
            if hasattr(membership, "revoked_at"):
                membership.revoked_at = _utcnow()
            if hasattr(membership, "revoked_by_user_id"):
                membership.revoked_by_user_id = actor_id
            db.session.add(membership)
            ok = True

        _mark_chunk_access_sync_pending(project)
        db.session.add(project)
        _record_project_event(
            project,
            action="member_removed",
            category="access",
            actor_user_id=actor_id,
            payload={
                "target_user_id": target_uid,
                "target_auth_user_id": target_auth_user_id,
                "actor_auth_user_id": actor_auth_user_id,
                "hard_delete": bool(hard_delete),
            },
            commit=False,
        )
        _db_flush_or_commit(commit)
    except Exception:
        _db_rollback_safely()
        raise

    if commit and ok:
        _sync_project_chunk_access_best_effort(
            project,
            actor_user_id=actor_id,
            actor_auth_user_id=actor_auth_user_id,
            force=True,
            commit=True,
        )

    return bool(ok)


# ─────────────────────────────────────────────────────────────
# Service links
# ─────────────────────────────────────────────────────────────

def list_project_service_links(project: Any) -> List[Dict[str, Any]]:
    try:
        if project is None:
            return []

        rows = (
            ProjectServiceLink.query.filter_by(project_id=project.id)
            .order_by(ProjectServiceLink.created_at.asc())
            .all()
        )

        return [
            model_serialize_service_link(row, include_private=True)
            for row in rows
        ]

    except Exception:
        return []


def upsert_project_service_link(
    project: Any,
    *,
    service_name: str,
    resource_kind: str,
    external_id: Optional[str] = None,
    external_project_id: Optional[str] = None,
    external_world_id: Optional[str] = None,
    external_url: Optional[str] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        if _project_is_demo(project):
            raise PermissionDenied(
                "Demo-Projekte unterstützen keine manuelle Service-Link-Verwaltung.",
                permission=PERMISSION_MANAGE,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_service_link_not_allowed",
            )

        uid = require_persistent_actor(user_id)
        require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)

        clean_service = model_normalize_service(service_name)
        clean_resource_type = model_normalize_resource_type(resource_kind)

        resource_id = (
            _safe_str(external_id, "", 255)
            or _safe_str(external_project_id, "", 255)
            or _safe_str(external_world_id, "", 255)
        )

        if not resource_id:
            raise ValueError("external_id or external_project_id or external_world_id required")

        row = _internal_upsert_project_service_link(
            project,
            service_name=clean_service,
            resource_kind=clean_resource_type,
            resource_id=resource_id,
            external_id=external_id,
            external_project_id=external_project_id,
            external_world_id=external_world_id,
            external_url=external_url,
            status=status,
            metadata=metadata,
        )

        if clean_service == SERVICE_CHUNK and clean_resource_type in {RESOURCE_CHUNK_PROJECT, "chunk_project"}:
            project.chunk_project_id = external_project_id or external_id or resource_id

        if clean_service == SERVICE_CHUNK and clean_resource_type in {RESOURCE_CHUNK_WORLD, "world", "chunk_world"}:
            project.chunk_world_id = external_world_id or external_id or resource_id

        if clean_resource_type in {RESOURCE_PLAN2D, "plan2d"}:
            project.plan2d_id = external_id or external_project_id or resource_id

        if clean_resource_type in {RESOURCE_LV, "lv"}:
            project.lv_id = external_id or external_project_id or resource_id

        project.updated_at = _utcnow()

        db.session.add(row)
        db.session.add(project)

        _record_project_event(
            project,
            action="linked",
            category="service_link",
            actor_user_id=uid,
            before={},
            after=model_serialize_service_link(row, include_private=True),
            payload={"service": clean_service, "resource_type": clean_resource_type},
            commit=False,
        )

        _db_flush_or_commit(commit)
        return row

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


# ─────────────────────────────────────────────────────────────
# Project versions
# ─────────────────────────────────────────────────────────────

def list_project_versions(
    project: Any,
    *,
    kind: Optional[str] = None,
    service_name: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    try:
        if project is None:
            return []

        query = ProjectVersion.query.filter_by(project_id=project.id)

        if kind:
            query = query.filter_by(kind=model_normalize_version_kind(kind))

        if service_name:
            query = query.filter_by(service=model_normalize_service(service_name))

        rows = (
            query.order_by(ProjectVersion.created_at.desc())
            .limit(_query_limit(limit, 100, 500))
            .all()
        )

        return [
            model_serialize_project_version(row, include_private=True)
            for row in rows
        ]

    except Exception:
        return []


def create_project_version_link(
    project: Any,
    *,
    label: Optional[str] = None,
    description: Optional[str] = None,
    service_name: Optional[str] = None,
    service_version_id: Optional[str] = None,
    service_snapshot_id: Optional[str] = None,
    service_artifact_id: Optional[str] = None,
    kind: Optional[str] = None,
    status: str = "stored",
    artifact_ref: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        if _project_is_demo(project):
            raise PermissionDenied(
                "Demo-Projekte unterstützen keine dauerhaften Versionslinks.",
                permission=PERMISSION_EDIT,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_versions_not_allowed",
            )

        uid = require_persistent_actor(user_id)
        require_project_permission(project, PERMISSION_EDIT, uid, allow_public_view=False)

        clean_service = model_normalize_service(service_name or SERVICE_APP)
        clean_kind = model_normalize_version_kind(kind or "metadata")
        clean_status = model_normalize_version_status(status or "stored")

        row = model_create_project_version(
            project_id=project.id,
            project_public_id=getattr(project, "public_id", ""),
            conversation_id=getattr(project, "conversation_id", ""),
            kind=clean_kind,
            status=clean_status,
            label=_safe_str(label, "", 255) or None,
            change_summary=_safe_str(description, "", 8000) or "",
            created_by=str(uid),
            created_by_user_id=uid,
            payload={
                "service": clean_service,
                "service_name": clean_service,
                "service_version_id": service_version_id,
                "service_snapshot_id": service_snapshot_id,
                "service_artifact_id": service_artifact_id,
                "artifact_ref": _safe_dict(artifact_ref),
                "metadata": _safe_dict(metadata),
            },
            commit=False,
        )

        _record_project_event(
            project,
            action="version_created",
            category="version",
            actor_user_id=uid,
            before={},
            after=model_serialize_project_version(row, include_private=True),
            payload={"service": clean_service, "kind": clean_kind},
            commit=False,
        )

        _db_flush_or_commit(commit)
        return row

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


# ─────────────────────────────────────────────────────────────
# Embed policy
# ─────────────────────────────────────────────────────────────

def update_project_embed_policy(
    project: Any,
    data: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        if _project_is_demo(project):
            raise PermissionDenied(
                "Demo-Projekte unterstützen keine Embed-Policy-Verwaltung.",
                permission=PERMISSION_EMBED,
                project_id=_project_public_id(project),
                user_id=None,
                status_code=403,
                code="demo_embed_policy_not_allowed",
            )

        uid = require_persistent_actor(user_id)
        require_project_permission(project, PERMISSION_EMBED, uid, allow_public_view=False)

        payload = _safe_dict(data)
        policy = get_or_create_embed_policy(project, user_id=uid, commit=False)

        before = model_serialize_embed_policy(policy, include_private=True)

        permissions = _safe_dict(payload.get("permissions"))

        normalized_payload = {
            "enabled": payload.get("enabled", payload.get("embed_enabled")),
            "allow_iframe": payload.get("allow_iframe", payload.get("allowIframe")),
            "allow_public_embed": payload.get("allow_public_embed", payload.get("allowPublicEmbed", payload.get("public"))),
            "mode": payload.get("mode", payload.get("embed_mode")),
            "default_mode": payload.get("default_mode", payload.get("defaultMode", payload.get("mode", payload.get("embed_mode")))),
            "spectator_only": payload.get("spectator_only", payload.get("spectatorOnly")),
            "readonly": payload.get("readonly", payload.get("read_only")),
            "allow_interaction": payload.get("allow_interaction", payload.get("allowInteraction")),
            "allow_map": payload.get("allow_map", permissions.get("map")),
            "allow_editor3d": payload.get("allow_editor3d", payload.get("allow_3d", permissions.get("3d"))),
            "allow_2d": payload.get("allow_2d", permissions.get("2d")),
            "allow_lv": payload.get("allow_lv", permissions.get("lv")),
            "allow_versions": payload.get("allow_versions", permissions.get("versions")),
            "allow_downloads": payload.get("allow_downloads", permissions.get("downloads")),
            "require_auth": payload.get("require_auth", payload.get("requireAuth")),
            "require_project_permission": payload.get("require_project_permission", payload.get("requireProjectPermission")),
            "require_token": payload.get("require_token", payload.get("requireToken")),
            "token_hash": payload.get("token_hash", payload.get("embed_token_hash")),
            "allowed_modes": payload.get("allowed_modes", payload.get("allowedModes")),
            "allowed_origins": payload.get("allowed_origins", payload.get("allowedOrigins")),
            "denied_origins": payload.get("denied_origins", payload.get("deniedOrigins")),
            "settings": {
                **_safe_dict(getattr(policy, "settings", {})),
                "show_toolbar": payload.get("show_toolbar"),
                "show_sidebar": payload.get("show_sidebar"),
                "show_project_metadata": payload.get("show_project_metadata"),
            },
            "metadata": _safe_dict(payload.get("metadata") or payload.get("meta")),
            "updated_by_user_id": uid,
        }

        if hasattr(policy, "update_from_payload"):
            policy.update_from_payload(normalized_payload)
        else:
            for key, value in normalized_payload.items():
                if value is not None and hasattr(policy, key):
                    setattr(policy, key, value)

        db.session.add(policy)

        _record_project_event(
            project,
            action="embed_changed",
            category="embed",
            actor_user_id=uid,
            before=before,
            after=model_serialize_embed_policy(policy, include_private=True),
            commit=False,
        )

        _db_flush_or_commit(commit)
        return policy

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


# ─────────────────────────────────────────────────────────────
# High-level API helpers
# ─────────────────────────────────────────────────────────────

def _permission_denied_result(exc: PermissionDenied) -> ProjectOperationResult:
    return ProjectOperationResult(
        ok=False,
        status_code=getattr(exc, "status_code", 403),
        code=getattr(exc, "code", "project_permission_denied"),
        error=getattr(exc, "message", str(exc)),
        payload=exc.to_dict() if hasattr(exc, "to_dict") else {"ok": False, "error": str(exc)},
    )


def create_project_result(data: Optional[Dict[str, Any]], *, user_id: Optional[int] = None) -> ProjectOperationResult:
    try:
        context = get_actor_context(user_id)

        if _safe_bool(context.get("blocked"), False):
            raise PermissionDenied(
                "Der Zugriff ist gesperrt.",
                code=_safe_str(context.get("blocked_reason"), "auth_blocked", 120),
                status_code=503
                if _safe_str(context.get("blocked_reason"), "", 120) in {
                    "auth_context_unavailable",
                    "actor_context_unavailable",
                    "auth_unavailable",
                }
                else 403,
                permission=PERMISSION_EDIT,
            )

        if _safe_bool(context.get("demo_mode"), False):
            project = _ensure_or_update_demo_project(data, commit=True)
            return ProjectOperationResult(
                ok=True,
                project=project,
                payload={
                    "ok": True,
                    "demo_mode": True,
                    "auth": context,
                    "project": serialize_project(
                        project,
                        user_id=None,
                        include_permissions=True,
                        include_embed_policy=False,
                        include_service_links=True,
                        include_publication=True,
                    ),
                    "sidebar_item": serialize_project_sidebar_item(project, user_id=None),
                    "redirect_url": project_public_url(project),
                    "chunk": _project_chunk_refs(project),
                },
                status_code=201,
                code="demo_project_ready",
            )

        project = create_project(data, user_id=user_id, commit=True)
        chunk = _project_chunk_refs(project)
        last_result = getattr(project, "_last_chunk_operation_result", None)
        last_result_payload = last_result.to_dict() if hasattr(last_result, "to_dict") else {}

        provisioning_status = _safe_str(
            chunk.get("provisioning_status"),
            CHUNK_PROVISIONING_PENDING,
            40,
        )
        required = _chunk_provisioning_required()
        response_ok = True
        status_code = 201
        code = "project_created"
        error = None

        if provisioning_status == CHUNK_PROVISIONING_FALLBACK_READY:
            code = "project_created_with_flat_fallback"
        elif provisioning_status in {
            CHUNK_PROVISIONING_FAILED,
            CHUNK_PROVISIONING_REPAIR_REQUIRED,
        }:
            code = "project_created_chunk_repair_required"
            if required:
                response_ok = False
                status_code = 503
                error = _safe_str(
                    _safe_dict(chunk.get("provisioning")).get("errorMessage")
                    or _safe_dict(last_result_payload).get("error"),
                    "Das App-Projekt wurde gespeichert, aber das Chunk-Projekt konnte nicht bereitgestellt werden.",
                    2000,
                )
        elif provisioning_status in {
            CHUNK_PROVISIONING_PENDING,
            CHUNK_PROVISIONING_PROVISIONING,
        }:
            code = "project_created_chunk_pending"

        payload = {
            "ok": response_ok,
            "project_persisted": True,
            "project": serialize_project(
                project,
                user_id=user_id,
                include_permissions=True,
                include_embed_policy=True,
                include_service_links=True,
                include_publication=True,
            ),
            "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
            "redirect_url": project_public_url(project),
            "chunk": chunk,
        }
        if last_result_payload:
            payload["chunk_operation"] = last_result_payload

        return ProjectOperationResult(
            ok=response_ok,
            project=project,
            payload=payload,
            status_code=status_code,
            code=code,
            error=error,
        )

    except PermissionDenied as exc:
        return _permission_denied_result(exc)
    except Exception as exc:
        _log_exception("create_project_result failed", exc)
        return ProjectOperationResult(
            ok=False,
            payload={"ok": False},
            status_code=500,
            code="project_create_failed",
            error=str(exc),
        )


def update_project_result(
    project_identifier: str,
    data: Optional[Dict[str, Any]],
    *,
    user_id: Optional[int] = None,
) -> ProjectOperationResult:
    try:
        project = resolve_project(project_identifier)

        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=404,
                code="project_not_found",
                error="project not found",
            )

        project = update_project(project, data, user_id=user_id, commit=True)

        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={
                "ok": True,
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_embed_policy=not _project_is_demo(project),
                    include_service_links=True,
                    include_publication=True,
                ),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
                "redirect_url": project_public_url(project),
                "chunk": _project_chunk_refs(project),
            },
            status_code=200,
            code="project_updated",
        )

    except PermissionDenied as exc:
        return _permission_denied_result(exc)

    except Exception as exc:
        _log_exception("update_project_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="project_update_failed",
            error=str(exc),
        )


def ensure_project_chunk_link_result(
    project_identifier: str,
    *,
    user_id: Optional[int] = None,
    force: bool = True,
) -> ProjectOperationResult:
    try:
        project = resolve_project(project_identifier)

        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=404,
                code="project_not_found",
                error="project not found",
            )

        if not _project_is_demo(project):
            uid = require_persistent_actor(user_id)
            require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)
        else:
            uid = None

        result = ensure_project_chunk_link(
            project,
            user_id=uid,
            force=force,
            commit=True,
        )

        return ProjectOperationResult(
            ok=bool(result.ok),
            project=project,
            payload={
                "ok": bool(result.ok),
                "project": serialize_project(
                    project,
                    user_id=uid,
                    include_permissions=True,
                    include_service_links=True,
                    include_embed_policy=not _project_is_demo(project),
                    include_publication=True,
                ),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=uid),
                "chunk": _project_chunk_refs(project),
                "chunk_result": result.to_dict(),
            },
            status_code=result.status_code,
            code=result.code,
            error=result.error,
        )

    except PermissionDenied as exc:
        return _permission_denied_result(exc)

    except Exception as exc:
        _log_exception("ensure_project_chunk_link_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="chunk_link_failed",
            error=str(exc),
        )



def sync_project_chunk_access_result(
    project_identifier: str,
    *,
    user_id: Optional[int] = None,
    force: bool = True,
) -> ProjectOperationResult:
    try:
        project = resolve_project(project_identifier)
        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=404,
                code="project_not_found",
                error="project not found",
            )
        return sync_project_chunk_access_for_project(
            project,
            user_id=user_id,
            force=force,
            commit=True,
        )
    except PermissionDenied as exc:
        return _permission_denied_result(exc)
    except Exception as exc:
        _log_exception("sync_project_chunk_access_result failed", exc)
        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="chunk_access_sync_failed",
            error=str(exc),
        )


def get_project_result(
    project_identifier: str,
    *,
    user_id: Optional[int] = None,
    include_deleted: bool = False,
) -> ProjectOperationResult:
    try:
        project = resolve_project(project_identifier, include_deleted=include_deleted)

        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=404,
                code="project_not_found",
                error="project not found",
            )

        permissions = get_project_permission_result(project, user_id=user_id)

        if not permissions.can_view:
            return ProjectOperationResult(
                ok=False,
                status_code=403,
                code="project_permission_denied",
                error="missing project permission: view",
                payload={"ok": False, "access": permissions.to_dict()},
            )

        include_manage_data = bool(permissions.can_manage)

        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={
                "ok": True,
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_members=include_manage_data and not _project_is_demo(project),
                    include_service_links=include_manage_data or _project_is_demo(project),
                    include_versions=True,
                    include_embed_policy=(include_manage_data or permissions.can_embed) and not _project_is_demo(project),
                    include_publication=True,
                ),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
                "chunk": _project_chunk_refs(project),
            },
            status_code=200,
            code="project_loaded",
        )

    except Exception as exc:
        _log_exception("get_project_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="project_load_failed",
            error=str(exc),
        )


def list_projects_result(
    *,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> ProjectOperationResult:
    try:
        context = get_actor_context(user_id)

        if _safe_bool(context.get("blocked"), False):
            return ProjectOperationResult(
                ok=False,
                payload={
                    "ok": False,
                    "auth": context,
                    "items": [],
                    "projects": [],
                    "sidebar_items": [],
                    "total": 0,
                    "message": "Der Zugriff ist gesperrt.",
                },
                status_code=403,
                code=_safe_str(context.get("blocked_reason"), "auth_blocked", 120),
                error="Der Zugriff ist gesperrt.",
            )

        if _safe_bool(context.get("demo_mode"), False):
            project = None

            if callable(get_current_demo_project):
                try:
                    demo_result = get_current_demo_project(context=get_current_user_context(ensure=False), create=True, commit=True)
                    if getattr(demo_result, "ok", False):
                        project = getattr(demo_result, "project", None)
                except Exception:
                    project = None

            if project is None and callable(ensure_demo_project_for_context):
                try:
                    demo_result = ensure_demo_project_for_context(context=get_current_user_context(ensure=False), commit=True)
                    if getattr(demo_result, "ok", False):
                        project = getattr(demo_result, "project", None)
                except Exception:
                    project = None

            items = []
            sidebar_items = []

            if project is not None:
                items = [serialize_project(project, user_id=None, include_permissions=True)]
                sidebar_items = [serialize_project_sidebar_item(project, user_id=None)]

            return ProjectOperationResult(
                ok=True,
                payload={
                    "ok": True,
                    "user_id": None,
                    "auth": context,
                    "demo_mode": True,
                    "items": items,
                    "projects": items,
                    "sidebar_items": sidebar_items,
                    "total": len(items),
                    "limit": _query_limit(limit, 100, 500),
                    "offset": max(_safe_int(offset, 0), 0),
                    "message": "Demo-Modus: Es wird genau ein temporäres Demo-Projekt geladen.",
                },
                status_code=200,
                code="projects_loaded_demo_mode",
            )

        uid = get_actor_user_id_optional(user_id)

        projects = list_projects_for_user(
            user_id=uid,
            include_public=False,
            include_unlisted=False,
            include_deleted=False,
            include_demo=False,
            search=search,
            limit=limit,
            offset=offset,
        )

        items = [
            serialize_project(project, user_id=uid, include_permissions=True)
            for project in projects
        ]

        sidebar_items = [
            serialize_project_sidebar_item(project, user_id=uid)
            for project in projects
        ]

        return ProjectOperationResult(
            ok=True,
            payload={
                "ok": True,
                "user_id": uid,
                "auth": context,
                "items": items,
                "projects": items,
                "sidebar_items": sidebar_items,
                "total": len(items),
                "limit": _query_limit(limit, 100, 500),
                "offset": max(_safe_int(offset, 0), 0),
            },
            status_code=200,
            code="projects_loaded",
        )

    except Exception as exc:
        _log_exception("list_projects_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="projects_load_failed",
            error=str(exc),
        )


def delete_project_result(
    project_identifier: str,
    *,
    user_id: Optional[int] = None,
    hard_delete: bool = False,
) -> ProjectOperationResult:
    try:
        project = resolve_project(project_identifier, include_deleted=True)

        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=404,
                code="project_not_found",
                error="project not found",
            )

        ok = delete_project(
            project,
            user_id=user_id,
            hard_delete=hard_delete,
            commit=True,
        )

        return ProjectOperationResult(
            ok=ok,
            payload={
                "ok": ok,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "deleted": ok,
            },
            status_code=200 if ok else 400,
            code="project_deleted" if ok else "project_delete_failed",
        )

    except PermissionDenied as exc:
        return _permission_denied_result(exc)

    except Exception as exc:
        _log_exception("delete_project_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="project_delete_failed",
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_project_service_status() -> Dict[str, Any]:
    try:
        counts: Dict[str, Any] = {}

        try:
            counts["projects"] = Project.query.count()
        except Exception:
            counts["projects"] = None

        try:
            counts["users"] = AppUser.query.count()
        except Exception:
            counts["users"] = None

        try:
            counts["memberships"] = ProjectMembership.query.count()
        except Exception:
            counts["memberships"] = None

        try:
            counts["conversations"] = Conversation.query.count()
        except Exception:
            counts["conversations"] = None

        try:
            counts["service_links"] = ProjectServiceLink.query.count()
        except Exception:
            counts["service_links"] = None

        try:
            counts["chunk_linked_projects"] = Project.query.filter(Project.chunk_project_id.isnot(None)).count()
        except Exception:
            counts["chunk_linked_projects"] = None

        try:
            counts["demo_projects"] = Project.query.filter(Project.is_demo.is_(True)).count()
        except Exception:
            counts["demo_projects"] = None

        chunk_health_payload: Dict[str, Any] = {
            "checked": False,
            "available": callable(get_chunk_health),
        }

        if callable(get_chunk_health) and _config_bool("VECTOPLAN_CHUNK_STATUS_CHECK_IN_APP_STATUS", False):
            try:
                health = get_chunk_health(raise_on_error=False)
                chunk_health_payload = health.to_dict(include_raw=False) if hasattr(health, "to_dict") else {}
                chunk_health_payload["checked"] = True
            except Exception as exc:
                chunk_health_payload = {
                    "checked": True,
                    "ok": False,
                    "error": {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                }

        context = get_actor_context()

        return {
            "ok": True,
            "service": "project_service",
            "phase": "vectoplan-auth-project-management-no-default-user",
            "current_user_id": get_actor_user_id_optional(),
            "auth": context,
            "default_user_removed": True,
            "counts": counts,
            "project_form": {
                "address_input_mode": "single_box",
                "visibility_mode": "cards_private_unlisted_public",
                "system_refs_visible_in_project_form": False,
                "structured_address_reserved_for_geocoder": True,
                "coordinates_reserved_for_geocoder": True,
            },
            "demo": {
                "enabled": _safe_bool(_config_value("VECTOPLAN_DEMO_PROJECTS_ENABLED", True), True),
                "ttl_seconds": _safe_int(_config_value("VECTOPLAN_DEMO_PROJECT_TTL_SECONDS", 3600), 3600),
                "serviceAvailable": callable(ensure_demo_project_for_context),
                "cleanupAvailable": callable(cleanup_expired_demo_projects),
            },
            "chunk": {
                "clientAvailable": callable(ensure_chunk_project_for_project),
                "orchestratorAvailable": _chunk_client_available(),
                "accessSyncServiceAvailable": _chunk_access_sync_available(),
                "accessSyncEnabled": _chunk_access_sync_enabled(),
                "provisioningEnabled": _chunk_provisioning_enabled(),
                "provisioningRequired": _chunk_provisioning_required(),
                "requestedWorldTemplate": _config_str("VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE", CHUNK_WORLD_TEMPLATE_EARTH, 40),
                "fallbackWorldTemplate": _config_str("VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE", CHUNK_WORLD_TEMPLATE_FLAT, 40),
                "worldFallbackEnabled": _config_bool("VECTOPLAN_APP_ALLOW_WORLD_FALLBACK", True),
                "canonicalAuthUserIdRequired": True,
                "distributedTransaction": False,
                "appProjectCommittedBeforeChunkCall": True,
                "internalUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_INTERNAL_URL", "")),
                "publicUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_PUBLIC_URL", "")),
                "health": chunk_health_payload,
            },
            "models": {
                "AppUser": AppUser is not None,
                "Project": Project is not None,
                "Conversation": Conversation is not None,
                "ProjectMembership": ProjectMembership is not None,
                "ProjectEmbedPolicy": ProjectEmbedPolicy is not None,
                "ProjectServiceLink": ProjectServiceLink is not None,
                "ProjectVersion": ProjectVersion is not None,
                "ProjectAuditEvent": ProjectAuditEvent is not None,
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "service": "project_service",
            "default_user_removed": True,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }


__all__ = [
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_DELETED",
    "PROJECT_STATUS_ARCHIVED",
    "PROJECT_STATUS_EXPIRED",
    "PROJECT_SETUP_DRAFT",
    "PROJECT_SETUP_DEFINED",
    "PROJECT_SETUP_CONFIGURED",
    "PROJECT_VISIBILITY_PRIVATE",
    "PROJECT_VISIBILITY_PUBLIC",
    "PROJECT_VISIBILITY_UNLISTED",
    "PROJECT_VISIBILITY_SHARED",
    "PROJECT_SCOPE_PERSONAL",
    "PROJECT_SCOPE_ACCOUNT",
    "PROJECT_SCOPE_DEMO",
    "GEOCODE_STATUS_NONE",
    "GEOCODE_STATUS_PENDING",
    "GEOCODE_STATUS_STALE",
    "GEOCODE_STATUS_RESOLVED",
    "GEOCODE_STATUS_FAILED",
    "SERVICE_CHUNK",
    "SERVICE_EDITOR",
    "SERVICE_OPENLAYER",
    "SERVICE_APP",
    "SERVICE_2D",
    "SERVICE_LV",
    "SERVICE_LIBRARY",
    "RESOURCE_CHUNK_PROJECT",
    "RESOURCE_CHUNK_UNIVERSE",
    "RESOURCE_CHUNK_WORLD",
    "CHUNK_STATUS_DISABLED",
    "CHUNK_STATUS_PENDING",
    "CHUNK_STATUS_READY",
    "CHUNK_STATUS_ERROR",
    "CHUNK_PROVISIONING_PENDING",
    "CHUNK_PROVISIONING_PROVISIONING",
    "CHUNK_PROVISIONING_READY",
    "CHUNK_PROVISIONING_FALLBACK_READY",
    "CHUNK_PROVISIONING_FAILED",
    "CHUNK_PROVISIONING_REPAIR_REQUIRED",
    "CHUNK_PROVISIONING_DISABLED",
    "CHUNK_ACCESS_SYNC_PENDING",
    "CHUNK_ACCESS_SYNC_SYNCING",
    "CHUNK_ACCESS_SYNC_READY",
    "CHUNK_ACCESS_SYNC_FAILED",
    "CHUNK_ACCESS_SYNC_REPAIR_REQUIRED",
    "CHUNK_ACCESS_SYNC_DISABLED",
    "CHUNK_WORLD_TEMPLATE_EARTH",
    "CHUNK_WORLD_TEMPLATE_FLAT",
    "ProjectOperationResult",
    "ProjectPermissionResult",
    "PermissionDenied",
    "actor_can_persist",
    "actor_is_demo",
    "build_address_text_from_parts",
    "build_project_paths",
    "create_or_update_project",
    "create_project",
    "create_project_result",
    "create_project_version_link",
    "delete_project",
    "delete_project_result",
    "ensure_project_chunk_link",
    "ensure_project_chunk_link_result",
    "ensure_project_user",
    "get_actor_account_id",
    "get_actor_auth_user_id",
    "get_actor_context",
    "get_actor_user_id_optional",
    "get_current_user_id",
    "get_or_create_embed_policy",
    "get_or_create_project_conversation",
    "get_project_address_text",
    "get_project_by_conversation_id",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_permission_result",
    "get_project_result",
    "get_project_service_status",
    "list_project_memberships",
    "list_project_service_links",
    "list_project_sidebar_items",
    "list_project_versions",
    "list_projects_for_user",
    "list_projects_result",
    "normalize_role",
    "project_admin_path",
    "project_cad2d_path",
    "project_editor_path",
    "project_lv_path",
    "project_map_path",
    "project_public_url",
    "project_workspace_path",
    "require_persistent_actor",
    "require_actor_auth_user_id",
    "require_project_permission",
    "resolve_project",
    "retry_project_chunk_link",
    "sync_project_chunk_access_for_project",
    "sync_project_chunk_access_result",
    "revoke_project_member",
    "serialize_project",
    "serialize_project_list",
    "serialize_project_permissions",
    "serialize_project_sidebar_item",
    "set_project_member_role",
    "transfer_project_owner",
    "update_project",
    "update_project_embed_policy",
    "update_project_result",
    "upsert_project_service_link",
]