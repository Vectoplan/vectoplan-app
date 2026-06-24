# services/vectoplan-app/services/project_service.py
"""
VECTOPLAN project service.

Zweck:
- Zentrale Projektverwaltungslogik für vectoplan-app.
- Nutzt die modularen Models ohne Alt-Kompatibilitäts-Mapping im DB-Layer.
- Erstellt Projekte inklusive Conversation, Owner-Membership und Embed-Policy.
- Verwaltet Metadaten, Adresse, Koordinaten, Sichtbarkeit, Soft Delete,
  Besitzübertragung, Service-Links und zentrale Version-Links.
- Verknüpft App-Projekte serverseitig mit vectoplan-chunk-Projekten.

Wichtig:
- vectoplan-app verwaltet Projekt-Metadaten, Rechte und Workspace-Shell.
- vectoplan-chunk verwaltet Chunk-Projekt, Universe, WorldInstance, Snapshots,
  Command Logs und Chunk Events.
- Externe Microservices werden in vectoplan-app nur referenziert.
- App und Chunk haben getrennte Datenbanken.
- Es gibt keine verteilte Transaktion zwischen App-DB und Chunk-DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

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

from extensions import db
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
    ensure_owner_membership as model_ensure_owner_membership,
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

from services.current_user import (
    ensure_default_user,
    get_current_user_id_from_g_or_default,
    get_default_user_id,
)

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
except Exception:  # pragma: no cover - project service must still import without chunk client.
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


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_DELETED = "deleted"
PROJECT_STATUS_ARCHIVED = "archived"

PROJECT_SETUP_DRAFT = "draft"
PROJECT_SETUP_DEFINED = "defined"
PROJECT_SETUP_CONFIGURED = "configured"

PROJECT_VISIBILITY_PRIVATE = "private"
PROJECT_VISIBILITY_PUBLIC = "public"
PROJECT_VISIBILITY_UNLISTED = "unlisted"
PROJECT_VISIBILITY_SHARED = "shared"

DEFAULT_PROJECT_NAME = "Neues Projekt"
DEFAULT_ADDRESS_COUNTRY = "DE"
DEFAULT_COORDINATE_SRID = "EPSG:4326"

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

PERMISSION_VIEW = "view"
PERMISSION_EDIT = "edit"
PERMISSION_MANAGE = "manage"
PERMISSION_DELETE = "delete"
PERMISSION_TRANSFER = "transfer"
PERMISSION_EMBED = "embed"

CHUNK_STATUS_DISABLED = "disabled"
CHUNK_STATUS_PENDING = "pending"
CHUNK_STATUS_READY = "ready"
CHUNK_STATUS_ERROR = "error"


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

        if self.project is not None and "project" not in body:
            try:
                body["project"] = serialize_project(self.project)
            except Exception:
                pass

        if self.error:
            body["error"] = self.error

        return body


@dataclass(frozen=True)
class ProjectPermissionResult:
    can_view: bool = False
    can_edit: bool = False
    can_manage: bool = False
    can_delete: bool = False
    can_transfer: bool = False
    can_embed: bool = False
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
        }


class PermissionDenied(Exception):
    def __init__(
        self,
        message: str = "missing project permission",
        *,
        code: str = "project_permission_denied",
        status_code: int = 403,
        permission: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.permission = permission

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "error": self.message,
            "permission": self.permission,
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _utcnow():
    try:
        return model_utcnow()
    except Exception:
        from datetime import datetime

        return datetime.utcnow()


def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    return model_safe_str(value, default, max_len)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return model_safe_int(value, default)
    except Exception:
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return model_safe_float(value, default)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        return model_safe_bool(value, default)
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return model_safe_dict(value)


def _safe_list(value: Any) -> List[Any]:
    return model_safe_list(value)


def _iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat() + "Z"
        return str(value)
    except Exception:
        return None


def _config_value(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            return current_app.config.get(name, default)
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
        text = _normalize_slug(value, default, 40)

        if text in {
            PROJECT_VISIBILITY_PRIVATE,
            PROJECT_VISIBILITY_PUBLIC,
            PROJECT_VISIBILITY_UNLISTED,
            PROJECT_VISIBILITY_SHARED,
        }:
            return text

        return default

    except Exception:
        return default


def _normalize_status(value: Any, default: str = PROJECT_STATUS_ACTIVE) -> str:
    try:
        text = _normalize_slug(value, default, 40)

        if text in {
            PROJECT_STATUS_ACTIVE,
            PROJECT_STATUS_DELETED,
            PROJECT_STATUS_ARCHIVED,
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


# ─────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────

def _role_permissions(role: str) -> Dict[str, bool]:
    clean = _normalize_slug(role, ROLE_VIEWER, 40)

    if clean == ROLE_OWNER:
        return {
            PERMISSION_VIEW: True,
            PERMISSION_EDIT: True,
            PERMISSION_MANAGE: True,
            PERMISSION_DELETE: True,
            PERMISSION_TRANSFER: True,
            PERMISSION_EMBED: True,
        }

    if clean == ROLE_ADMIN:
        return {
            PERMISSION_VIEW: True,
            PERMISSION_EDIT: True,
            PERMISSION_MANAGE: True,
            PERMISSION_DELETE: False,
            PERMISSION_TRANSFER: False,
            PERMISSION_EMBED: True,
        }

    if clean == ROLE_EDITOR:
        return {
            PERMISSION_VIEW: True,
            PERMISSION_EDIT: True,
            PERMISSION_MANAGE: False,
            PERMISSION_DELETE: False,
            PERMISSION_TRANSFER: False,
            PERMISSION_EMBED: False,
        }

    return {
        PERMISSION_VIEW: True,
        PERMISSION_EDIT: False,
        PERMISSION_MANAGE: False,
        PERMISSION_DELETE: False,
        PERMISSION_TRANSFER: False,
        PERMISSION_EMBED: False,
    }


def normalize_role(value: Any, default: str = ROLE_VIEWER) -> str:
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
    }
    clean = aliases.get(clean, clean)
    return clean if clean in {
        PERMISSION_VIEW,
        PERMISSION_EDIT,
        PERMISSION_MANAGE,
        PERMISSION_DELETE,
        PERMISSION_TRANSFER,
        PERMISSION_EMBED,
    } else default


def get_current_user_id(user_id: Optional[int] = None) -> int:
    try:
        parsed = _safe_int(user_id, 0)
        if parsed > 0:
            return parsed

        return _safe_int(get_current_user_id_from_g_or_default(), get_default_user_id())

    except Exception:
        return 1


def ensure_project_user() -> Any:
    return ensure_default_user()


def _get_membership(project: Any, user_id: int) -> Optional[Any]:
    try:
        project_id = _safe_int(getattr(project, "id", None), 0)
        uid = _safe_int(user_id, 0)

        if not project_id or not uid:
            return None

        return ProjectMembership.query.filter_by(project_id=project_id, user_id=uid).one_or_none()

    except Exception:
        return None


def get_project_permission_result(
    project: Any,
    *,
    user_id: Optional[int] = None,
    allow_public_view: bool = True,
) -> ProjectPermissionResult:
    try:
        if project is None:
            return ProjectPermissionResult()

        uid = get_current_user_id(user_id)
        owner_user_id = _safe_int(getattr(project, "owner_user_id", None), 0)

        if owner_user_id and uid == owner_user_id:
            permissions = _role_permissions(ROLE_OWNER)
            return ProjectPermissionResult(
                can_view=permissions[PERMISSION_VIEW],
                can_edit=permissions[PERMISSION_EDIT],
                can_manage=permissions[PERMISSION_MANAGE],
                can_delete=permissions[PERMISSION_DELETE],
                can_transfer=permissions[PERMISSION_TRANSFER],
                can_embed=permissions[PERMISSION_EMBED],
                role=ROLE_OWNER,
                source="owner",
            )

        membership = _get_membership(project, uid)

        if membership is not None:
            try:
                is_active = bool(getattr(membership, "is_active", True))
            except Exception:
                is_active = True

            if is_active:
                role = normalize_role(getattr(membership, "role", ROLE_VIEWER))
                return ProjectPermissionResult(
                    can_view=bool(getattr(membership, "can_view", False)),
                    can_edit=bool(getattr(membership, "can_edit", False)),
                    can_manage=bool(getattr(membership, "can_manage", False)),
                    can_delete=bool(getattr(membership, "can_delete", False)),
                    can_transfer=bool(getattr(membership, "can_transfer", False)),
                    can_embed=bool(getattr(membership, "can_embed", False)),
                    role=role,
                    source="membership",
                )

        is_public = bool(getattr(project, "is_public", False)) or getattr(project, "visibility", "") == PROJECT_VISIBILITY_PUBLIC

        if allow_public_view and is_public:
            return ProjectPermissionResult(
                can_view=True,
                role=ROLE_VIEWER,
                source="public",
            )

        return ProjectPermissionResult()

    except Exception:
        return ProjectPermissionResult()


def serialize_project_permissions(project: Any, *, user_id: Optional[int] = None) -> Dict[str, Any]:
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
    result = get_project_permission_result(
        project,
        user_id=user_id,
        allow_public_view=allow_public_view,
    )

    if not result.can(clean_permission):
        raise PermissionDenied(
            f"missing project permission: {clean_permission}",
            permission=clean_permission,
        )

    return result


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
        return f"/ui/project/{public_id}/editor" if public_id else "/ui/editor"
    except Exception:
        return "/ui/editor"


def project_map_path(project: Any) -> str:
    try:
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/map" if public_id else "/ui/map"
    except Exception:
        return "/ui/map"


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
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)
        return f"/ui/project/{public_id}/admin" if public_id else "/ui/project/new"
    except Exception:
        return "/ui/project/new"


def build_project_paths(project: Any) -> Dict[str, str]:
    try:
        return {
            "projectPagePath": project_workspace_path(project),
            "projectUrl": project_workspace_path(project),
            "projectPublicUrl": project_public_url(project),
            "editorPagePath": project_editor_path(project),
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
# Chunk reference helpers
# ─────────────────────────────────────────────────────────────

def _project_chunk_refs(project: Any) -> Dict[str, Any]:
    try:
        service_refs = _safe_dict(getattr(project, "service_refs", {}))
        chunk_refs = _safe_dict(service_refs.get(SERVICE_CHUNK))

        metadata = _get_project_metadata(project)
        chunk_metadata = _safe_dict(metadata.get("chunk"))

        chunk_project_id = (
            _safe_str(getattr(project, "chunk_project_id", None), "", 160)
            or _safe_str(chunk_refs.get("chunk_project_id"), "", 160)
            or _safe_str(chunk_refs.get("chunkProjectId"), "", 160)
            or _safe_str(chunk_metadata.get("chunk_project_id"), "", 160)
            or _safe_str(chunk_metadata.get("chunkProjectId"), "", 160)
        )

        chunk_universe_id = (
            _safe_str(getattr(project, "chunk_universe_id", None), "", 160)
            or _safe_str(chunk_refs.get("chunk_universe_id"), "", 160)
            or _safe_str(chunk_refs.get("chunkUniverseId"), "", 160)
            or _safe_str(chunk_metadata.get("chunk_universe_id"), "", 160)
            or _safe_str(chunk_metadata.get("chunkUniverseId"), "", 160)
        )

        chunk_world_id = (
            _safe_str(getattr(project, "chunk_world_id", None), "", 160)
            or _safe_str(chunk_refs.get("chunk_world_id"), "", 160)
            or _safe_str(chunk_refs.get("chunkWorldId"), "", 160)
            or _safe_str(chunk_metadata.get("chunk_world_id"), "", 160)
            or _safe_str(chunk_metadata.get("chunkWorldId"), "", 160)
        )

        route_hints = (
            _safe_dict(chunk_refs.get("route_hints"))
            or _safe_dict(chunk_refs.get("routeHints"))
            or _safe_dict(chunk_metadata.get("route_hints"))
            or _safe_dict(chunk_metadata.get("routeHints"))
        )

        status = (
            _safe_str(chunk_refs.get("status"), "", 40)
            or _safe_str(chunk_metadata.get("status"), "", 40)
        )

        if not status:
            status = CHUNK_STATUS_READY if chunk_project_id and chunk_world_id else CHUNK_STATUS_PENDING

        return {
            "status": status,
            "ready": bool(chunk_project_id and chunk_world_id and status == CHUNK_STATUS_READY),
            "chunk_project_id": chunk_project_id or None,
            "chunk_universe_id": chunk_universe_id or None,
            "chunk_world_id": chunk_world_id or None,
            "route_hints": route_hints,
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
            "ready": False,
            "chunk_project_id": None,
            "chunk_universe_id": None,
            "chunk_world_id": None,
            "route_hints": {},
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
) -> None:
    try:
        clean_chunk_project_id = _safe_str(chunk_project_id, "", 160) or None
        clean_chunk_universe_id = _safe_str(chunk_universe_id, "", 160) or None
        clean_chunk_world_id = _safe_str(chunk_world_id, "", 160) or None
        clean_status = _normalize_status(status, CHUNK_STATUS_PENDING)

        if hasattr(project, "chunk_project_id"):
            project.chunk_project_id = clean_chunk_project_id

        if hasattr(project, "chunk_universe_id"):
            project.chunk_universe_id = clean_chunk_universe_id

        if hasattr(project, "chunk_world_id"):
            project.chunk_world_id = clean_chunk_world_id

        service_refs = _safe_dict(getattr(project, "service_refs", {}))
        service_refs[SERVICE_CHUNK] = {
            **_safe_dict(service_refs.get(SERVICE_CHUNK)),
            "status": clean_status,
            "ready": bool(clean_chunk_project_id and clean_chunk_world_id and clean_status == CHUNK_STATUS_READY),
            "chunk_project_id": clean_chunk_project_id,
            "chunk_universe_id": clean_chunk_universe_id,
            "chunk_world_id": clean_chunk_world_id,
            "route_hints": _safe_dict(route_hints),
            "updated_at": _iso(_utcnow()),
        }

        if error:
            service_refs[SERVICE_CHUNK]["error"] = _safe_dict(error)

        if hasattr(project, "service_refs"):
            project.service_refs = service_refs

        metadata = _get_project_metadata(project)
        metadata["chunk"] = {
            **_safe_dict(metadata.get("chunk")),
            "status": clean_status,
            "ready": bool(clean_chunk_project_id and clean_chunk_world_id and clean_status == CHUNK_STATUS_READY),
            "chunk_project_id": clean_chunk_project_id,
            "chunk_universe_id": clean_chunk_universe_id,
            "chunk_world_id": clean_chunk_world_id,
            "route_hints": _safe_dict(route_hints),
            "updated_at": _iso(_utcnow()),
        }

        if error:
            metadata["chunk"]["error"] = _safe_dict(error)

        _set_project_metadata(project, metadata)

        if hasattr(project, "updated_at"):
            project.updated_at = _utcnow()

    except Exception as exc:
        _log_warning("set project chunk refs failed: %s", exc.__class__.__name__)


def _set_project_chunk_status(
    project: Any,
    *,
    status: str,
    error: Optional[Dict[str, Any]] = None,
) -> None:
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
    """
    Internal unchecked service-link upsert.

    This avoids permission checks because it is used from trusted project
    creation/provisioning flows that already operate on the current project.
    """
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
                    "user_id": get_current_user_id(user_id),
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
                    "user_id": get_current_user_id(user_id),
                },
            )
        )

    return rows


def _chunk_client_available() -> bool:
    return callable(ensure_chunk_project_for_project) and callable(extract_chunk_refs)


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


def _should_attempt_chunk_provision(project: Any, *, force: bool = False) -> bool:
    if project is None:
        return False

    if not _chunk_provisioning_enabled():
        return False

    if bool(getattr(project, "is_deleted", False)):
        return False

    if force:
        return True

    refs = _project_chunk_refs(project)
    return not bool(refs.get("chunk_project_id") and refs.get("chunk_world_id"))


def ensure_project_chunk_link(
    project: Any,
    *,
    user_id: Optional[int] = None,
    force: bool = False,
    commit: bool = True,
) -> ProjectOperationResult:
    """
    Ensure a chunk-side project/universe/world exists for this app project.

    This function:
    - calls vectoplan-chunk through services.chunk_client,
    - stores chunk_project_id / chunk_world_id on the app Project,
    - updates service_refs,
    - upserts ProjectServiceLink rows,
    - records audit events.

    It does not write directly into the chunk database.
    """
    try:
        if project is None:
            return ProjectOperationResult(
                ok=False,
                status_code=400,
                code="project_required",
                error="project required",
            )

        uid = get_current_user_id(user_id)

        if not _chunk_provisioning_enabled():
            _set_project_chunk_status(project, status=CHUNK_STATUS_DISABLED)
            db.session.add(project)

            _record_project_event(
                project,
                action="chunk_provisioning_disabled",
                category="service_link",
                actor_user_id=uid,
                payload={
                    "service": SERVICE_CHUNK,
                    "reason": "disabled_by_config",
                },
                commit=False,
            )

            if commit:
                db.session.commit()
            else:
                db.session.flush()

            return ProjectOperationResult(
                ok=True,
                project=project,
                payload={
                    "ok": True,
                    "chunk": _project_chunk_refs(project),
                },
                status_code=200,
                code="chunk_provisioning_disabled",
            )

        existing_refs = _project_chunk_refs(project)

        if not force and existing_refs.get("chunk_project_id") and existing_refs.get("chunk_world_id"):
            _set_project_chunk_refs(
                project,
                chunk_project_id=existing_refs.get("chunk_project_id"),
                chunk_universe_id=existing_refs.get("chunk_universe_id"),
                chunk_world_id=existing_refs.get("chunk_world_id"),
                route_hints=_safe_dict(existing_refs.get("route_hints")),
                status=CHUNK_STATUS_READY,
            )

            _upsert_chunk_service_links_from_refs(project, refs=_project_chunk_refs(project), user_id=uid)
            db.session.add(project)

            if commit:
                db.session.commit()
            else:
                db.session.flush()

            return ProjectOperationResult(
                ok=True,
                project=project,
                payload={
                    "ok": True,
                    "chunk": _project_chunk_refs(project),
                    "existing": True,
                },
                status_code=200,
                code="chunk_link_exists",
            )

        if not _chunk_client_available():
            error = {
                "code": "chunk_client_unavailable",
                "message": "services.chunk_client could not be imported.",
            }
            _set_project_chunk_status(project, status=CHUNK_STATUS_ERROR, error=error)
            db.session.add(project)

            _record_project_event(
                project,
                action="chunk_provision_failed",
                category="service_link",
                actor_user_id=uid,
                payload={
                    "service": SERVICE_CHUNK,
                    "error": error,
                },
                commit=False,
            )

            if commit:
                db.session.commit()
            else:
                db.session.flush()

            if _chunk_provisioning_required():
                raise RuntimeError(error["message"])

            return ProjectOperationResult(
                ok=False,
                project=project,
                payload={
                    "ok": False,
                    "chunk": _project_chunk_refs(project),
                    "error": error,
                },
                status_code=503,
                code="chunk_client_unavailable",
                error=error["message"],
            )

        result = ensure_chunk_project_for_project(
            project,
            apply_to_project=True,
            raise_on_error=_chunk_provisioning_required(),
        )

        refs = extract_chunk_refs(result) if callable(extract_chunk_refs) else {}
        route_hints = _safe_dict(refs.get("route_hints"))

        if result.ok:
            chunk_project_id = _safe_str(refs.get("chunk_project_id"), "", 160)
            chunk_universe_id = _safe_str(refs.get("chunk_universe_id"), "", 160)
            chunk_world_id = _safe_str(refs.get("chunk_world_id"), "", 160)

            _set_project_chunk_refs(
                project,
                chunk_project_id=chunk_project_id,
                chunk_universe_id=chunk_universe_id,
                chunk_world_id=chunk_world_id,
                route_hints=route_hints,
                status=CHUNK_STATUS_READY,
            )

            _upsert_chunk_service_links_from_refs(
                project,
                refs=_project_chunk_refs(project),
                user_id=uid,
            )

            db.session.add(project)

            _record_project_event(
                project,
                action="chunk_project_linked",
                category="service_link",
                actor_user_id=uid,
                before=existing_refs,
                after=_project_chunk_refs(project),
                payload={
                    "service": SERVICE_CHUNK,
                    "chunk_result": result.to_dict(include_raw=False, include_request_body=False)
                    if hasattr(result, "to_dict")
                    else {},
                },
                commit=False,
            )

            if commit:
                db.session.commit()
            else:
                db.session.flush()

            return ProjectOperationResult(
                ok=True,
                project=project,
                payload={
                    "ok": True,
                    "chunk": _project_chunk_refs(project),
                    "chunk_result": result.to_dict(include_raw=False, include_request_body=False)
                    if hasattr(result, "to_dict")
                    else {},
                },
                status_code=200,
                code="chunk_project_linked",
            )

        error = _safe_dict(getattr(result, "error", None)) or {
            "code": "chunk_provision_failed",
            "message": getattr(result, "message", "chunk provisioning failed"),
        }

        _set_project_chunk_status(project, status=CHUNK_STATUS_ERROR, error=error)
        db.session.add(project)

        _record_project_event(
            project,
            action="chunk_provision_failed",
            category="service_link",
            actor_user_id=uid,
            before=existing_refs,
            after=_project_chunk_refs(project),
            payload={
                "service": SERVICE_CHUNK,
                "error": error,
                "chunk_result": result.to_dict(include_raw=False, include_request_body=False)
                if hasattr(result, "to_dict")
                else {},
            },
            commit=False,
        )

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        if _chunk_provisioning_required():
            raise RuntimeError(_safe_str(error.get("message"), "chunk provisioning failed"))

        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={
                "ok": False,
                "chunk": _project_chunk_refs(project),
                "error": error,
            },
            status_code=502,
            code="chunk_provision_failed",
            error=_safe_str(error.get("message"), "chunk provisioning failed"),
        )

    except Exception as exc:
        if commit:
            try:
                db.session.rollback()
            except Exception:
                pass

        _log_exception("ensure_project_chunk_link failed", exc)

        if _chunk_provisioning_required():
            raise

        return ProjectOperationResult(
            ok=False,
            project=project,
            payload={
                "ok": False,
                "chunk": _project_chunk_refs(project),
                "error": {
                    "code": "chunk_provision_exception",
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                },
            },
            status_code=502,
            code="chunk_provision_exception",
            error=str(exc),
        )


def retry_project_chunk_link(
    project: Any,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> ProjectOperationResult:
    """Force retry of app-project -> chunk-project provisioning."""
    return ensure_project_chunk_link(
        project,
        user_id=user_id,
        force=True,
        commit=commit,
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

        return project

    except Exception as exc:
        _log_warning("get_project_by_conversation_id failed: %s", exc.__class__.__name__)
        return None


def resolve_project(identifier: Any, *, include_deleted: bool = False) -> Optional[Any]:
    try:
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

        chunk_refs = _project_chunk_refs(project)

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

        if include_permissions:
            payload["access"] = serialize_project_permissions(project, user_id=user_id)

        if include_members:
            payload["members"] = list_project_memberships(project)

        if include_service_links:
            payload["service_links"] = list_project_service_links(project)

        if include_versions:
            payload["versions"] = list_project_versions(project)

        if include_embed_policy:
            policy = get_or_create_embed_policy(project, user_id=user_id, commit=False)
            payload["embed_policy"] = (
                model_serialize_embed_policy(policy)
                if policy is not None
                else {}
            )

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
                "subtitle": getattr(project, "address_text", None) or getattr(project, "setup_status", None) or "Projekt",
                "href": f"/project={public_id}" if public_id else "/project=new",
                "source": "projects_api",
            }

        chunk_refs = _project_chunk_refs(project)

        item["permissions"] = serialize_project_permissions(project, user_id=user_id).get("permissions", {})
        item["isConfigured"] = bool(getattr(project, "is_configured", False))
        item["is_configured"] = bool(getattr(project, "is_configured", False))
        item["conversationId"] = getattr(project, "conversation_id", None)
        item["conversation_id"] = getattr(project, "conversation_id", None)

        item["chunkReady"] = bool(chunk_refs.get("ready"))
        item["chunk_ready"] = bool(chunk_refs.get("ready"))
        item["chunkStatus"] = chunk_refs.get("status")
        item["chunk_status"] = chunk_refs.get("status")
        item["chunkProjectId"] = chunk_refs.get("chunk_project_id")
        item["chunk_project_id"] = chunk_refs.get("chunk_project_id")
        item["chunkWorldId"] = chunk_refs.get("chunk_world_id")
        item["chunk_world_id"] = chunk_refs.get("chunk_world_id")

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
    include_public: bool = True,
    include_deleted: bool = False,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Any]:
    try:
        uid = get_current_user_id(user_id)
        limit_value = _query_limit(limit, 100, 500)
        offset_value = max(_safe_int(offset, 0), 0)

        membership_project_ids: List[int] = []

        try:
            memberships = ProjectMembership.query.filter_by(user_id=uid, status="active").all()
            for membership in memberships:
                project_id = _safe_int(getattr(membership, "project_id", None), 0)
                if project_id and project_id not in membership_project_ids:
                    membership_project_ids.append(project_id)
        except Exception:
            membership_project_ids = []

        query = Project.query

        conditions = [Project.owner_user_id == uid]

        if membership_project_ids:
            conditions.append(Project.id.in_(membership_project_ids))

        if include_public:
            conditions.append(Project.is_public.is_(True))
            conditions.append(Project.visibility == PROJECT_VISIBILITY_PUBLIC)

        if or_ is not None:
            query = query.filter(or_(*conditions))
        else:
            query = query.filter(Project.owner_user_id == uid)

        if not include_deleted:
            query = query.filter(Project.status != PROJECT_STATUS_DELETED)

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

        query = query.order_by(Project.updated_at.desc(), Project.created_at.desc())

        if offset_value:
            query = query.offset(offset_value)

        return list(query.limit(limit_value).all())

    except Exception as exc:
        _log_exception("list_projects_for_user failed", exc)
        return []


def list_project_sidebar_items(
    user_id: Optional[int] = None,
    *,
    include_public: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    try:
        projects = list_projects_for_user(
            user_id=user_id,
            include_public=include_public,
            include_deleted=False,
            limit=limit,
        )

        return [
            serialize_project_sidebar_item(project, user_id=user_id)
            for project in projects
        ]

    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Project creation / update
# ─────────────────────────────────────────────────────────────

def _project_has_minimum_definition(project: Any) -> bool:
    try:
        has_name = bool(_safe_str(getattr(project, "name", None), "", 255))
        has_address = bool(
            _safe_str(getattr(project, "address_text", None), "", 2000)
            or _safe_str(getattr(project, "street", None), "", 255)
            or _safe_str(getattr(project, "city", None), "", 160)
            or (
                getattr(project, "latitude", None) is not None
                and getattr(project, "longitude", None) is not None
            )
        )

        return has_name and has_address

    except Exception:
        return False


def _normalize_project_payload(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = _safe_dict(data)

    try:
        raw_address = source.get("address")
        address = _safe_dict(raw_address)
        coordinates = _safe_dict(source.get("coordinates"))

        name = (
            source.get("name")
            or source.get("title")
            or source.get("project_name")
            or DEFAULT_PROJECT_NAME
        )

        address_text_value = source.get("address_text")

        if address_text_value is None:
            if isinstance(raw_address, dict):
                address_text_value = address.get("text")
            else:
                address_text_value = raw_address

        latitude_value = (
            source.get("latitude")
            if source.get("latitude") is not None
            else source.get("lat")
            if source.get("lat") is not None
            else coordinates.get("latitude")
            if coordinates.get("latitude") is not None
            else coordinates.get("lat")
        )

        longitude_value = (
            source.get("longitude")
            if source.get("longitude") is not None
            else source.get("lng")
            if source.get("lng") is not None
            else source.get("lon")
            if source.get("lon") is not None
            else coordinates.get("longitude")
            if coordinates.get("longitude") is not None
            else coordinates.get("lng")
            if coordinates.get("lng") is not None
            else coordinates.get("lon")
        )

        payload = {
            "name": _safe_str(name, DEFAULT_PROJECT_NAME, 255) or DEFAULT_PROJECT_NAME,
            "description": _safe_str(source.get("description"), "", 10000) or None,
            "address_text": _safe_str(address_text_value, "", 2000) or None,
            "street": _safe_str(
                source.get("street") or source.get("address_street") or address.get("street"),
                "",
                255,
            ) or None,
            "house_number": _safe_str(
                source.get("house_number") or source.get("address_house_number") or address.get("house_number"),
                "",
                80,
            ) or None,
            "postal_code": _safe_str(
                source.get("postal_code") or source.get("address_postal_code") or address.get("postal_code"),
                "",
                40,
            ) or None,
            "city": _safe_str(
                source.get("city") or source.get("address_city") or address.get("city"),
                "",
                160,
            ) or None,
            "region": _safe_str(
                source.get("region") or source.get("address_region") or address.get("region"),
                "",
                160,
            ) or None,
            "country": _safe_str(
                source.get("country") or source.get("address_country") or address.get("country"),
                DEFAULT_ADDRESS_COUNTRY,
                160,
            ) or DEFAULT_ADDRESS_COUNTRY,
            "latitude": _safe_float(latitude_value, None),
            "longitude": _safe_float(longitude_value, None),
            "coordinate_srid": _safe_str(
                source.get("coordinate_srid")
                or source.get("srid")
                or address.get("coordinate_srid")
                or coordinates.get("srid"),
                DEFAULT_COORDINATE_SRID,
                40,
            ) or DEFAULT_COORDINATE_SRID,
            "visibility": _normalize_visibility(source.get("visibility"), PROJECT_VISIBILITY_PRIVATE),
            "is_public": _safe_bool(source.get("is_public", source.get("public")), False),
            "chunk_project_id": _safe_str(source.get("chunk_project_id") or source.get("chunkProjectId"), "", 160) or None,
            "chunk_universe_id": _safe_str(source.get("chunk_universe_id") or source.get("chunkUniverseId"), "", 160) or None,
            "chunk_world_id": _safe_str(source.get("chunk_world_id") or source.get("chunkWorldId"), "", 160) or None,
            "plan2d_id": _safe_str(source.get("plan2d_id") or source.get("plan2dId"), "", 160) or None,
            "lv_id": _safe_str(source.get("lv_id") or source.get("lvId"), "", 160) or None,
            "service_refs": _safe_dict(source.get("service_refs") or source.get("serviceRefs")),
            "artifact_refs": _safe_dict(source.get("artifact_refs") or source.get("artifactRefs")),
            "settings": _safe_dict(source.get("settings")),
            "metadata": _safe_dict(source.get("metadata") or source.get("meta")),
        }

        if payload["is_public"]:
            payload["visibility"] = PROJECT_VISIBILITY_PUBLIC

        is_defined = bool(
            payload["name"]
            and (
                payload["address_text"]
                or payload["street"]
                or payload["city"]
                or (payload["latitude"] is not None and payload["longitude"] is not None)
            )
        )

        payload["setup_status"] = _safe_str(
            source.get("setup_status") or source.get("setupStatus"),
            PROJECT_SETUP_CONFIGURED if is_defined else PROJECT_SETUP_DRAFT,
            40,
        )

        if payload["setup_status"] not in {PROJECT_SETUP_DRAFT, PROJECT_SETUP_DEFINED, PROJECT_SETUP_CONFIGURED}:
            payload["setup_status"] = PROJECT_SETUP_CONFIGURED if is_defined else PROJECT_SETUP_DRAFT

        return payload

    except Exception:
        return {
            "name": DEFAULT_PROJECT_NAME,
            "description": None,
            "address_text": None,
            "street": None,
            "house_number": None,
            "postal_code": None,
            "city": None,
            "region": None,
            "country": DEFAULT_ADDRESS_COUNTRY,
            "latitude": None,
            "longitude": None,
            "coordinate_srid": DEFAULT_COORDINATE_SRID,
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
        if hasattr(project, "update_from_payload"):
            project.update_from_payload(payload)
        else:
            for key, value in payload.items():
                if hasattr(project, key):
                    setattr(project, key, value)

        if _project_has_minimum_definition(project):
            if hasattr(project, "mark_configured"):
                project.mark_configured()
            else:
                project.setup_status = PROJECT_SETUP_CONFIGURED
                project.setup_completed_at = getattr(project, "setup_completed_at", None) or _utcnow()
        elif getattr(project, "setup_status", None) == PROJECT_SETUP_CONFIGURED:
            project.setup_status = PROJECT_SETUP_DEFINED

        if hasattr(project, "normalize_lifecycle"):
            project.normalize_lifecycle()

        return project

    except Exception:
        return project


def _create_conversation_for_project(project: Any, *, title: Optional[str] = None) -> Any:
    conv = Conversation()

    try:
        conv.project_id = str(project.id)
        conv.title = _safe_str(title or getattr(project, "name", None), "Projekt-Chat", 255)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return conv

    except Exception:
        if commit:
            db.session.rollback()
        raise


def get_or_create_embed_policy(project: Any, *, user_id: Optional[int] = None, commit: bool = False) -> Any:
    try:
        if project is None:
            return None

        policy = ProjectEmbedPolicy.query.filter_by(project_id=project.id).one_or_none()

        if policy is None:
            policy = model_build_embed_policy(
                project_id=project.id,
                created_by_user_id=get_current_user_id(user_id),
            )
            db.session.add(policy)

            if commit:
                db.session.commit()
            else:
                db.session.flush()

        return policy

    except Exception:
        if commit:
            db.session.rollback()
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


def create_project(
    data: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
    provision_chunk: Optional[bool] = None,
) -> Any:
    """
    Create an app project.

    Transaction strategy:
    1. Create and commit the app project graph first.
    2. Then call vectoplan-chunk through INTERNAL_URL.
    3. Store returned chunk refs in a second app DB update.

    This avoids pretending that app DB and chunk DB share a distributed
    transaction.
    """
    try:
        user = ensure_project_user()
        uid = get_current_user_id(user_id)
        payload = _normalize_project_payload(data)

        project = Project(
            owner_user_id=uid,
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
            chunk_project_id=payload.get("chunk_project_id"),
            chunk_world_id=payload.get("chunk_world_id"),
            plan2d_id=payload.get("plan2d_id"),
            lv_id=payload.get("lv_id"),
            service_refs=_safe_dict(payload.get("service_refs")),
            artifact_refs=_safe_dict(payload.get("artifact_refs")),
            visibility=payload.get("visibility") or PROJECT_VISIBILITY_PRIVATE,
            is_public=bool(payload.get("is_public")),
            status=PROJECT_STATUS_ACTIVE,
            setup_status=payload.get("setup_status") or PROJECT_SETUP_DRAFT,
            settings=_safe_dict(payload.get("settings")),
            metadata_json={
                "created_via": "project_service.create_project",
                **_safe_dict(payload.get("metadata")),
            },
        )

        if payload.get("chunk_universe_id") and hasattr(project, "chunk_universe_id"):
            project.chunk_universe_id = payload.get("chunk_universe_id")

        if _project_has_minimum_definition(project):
            if hasattr(project, "mark_configured"):
                project.mark_configured()
            else:
                project.setup_status = PROJECT_SETUP_CONFIGURED
                project.setup_completed_at = _utcnow()

        if hasattr(project, "normalize_lifecycle"):
            project.normalize_lifecycle()

        db.session.add(project)
        db.session.flush()

        conv = _create_conversation_for_project(project, title=project.name)

        model_ensure_owner_membership(
            project_id=project.id,
            owner_user_id=uid,
            commit=False,
        )

        get_or_create_embed_policy(project, user_id=uid, commit=False)

        _record_project_event(
            project,
            action="created",
            category="project",
            actor_user_id=uid,
            before={},
            after=serialize_project(project, user_id=uid, include_permissions=False),
            payload={
                "conversation_id": getattr(conv, "id", None),
                "placeholder_user_id": getattr(user, "id", uid) if user is not None else uid,
            },
            commit=False,
        )

        should_provision = _chunk_provisioning_enabled() if provision_chunk is None else bool(provision_chunk)

        if should_provision:
            _set_project_chunk_status(project, status=CHUNK_STATUS_PENDING)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        if should_provision and commit:
            ensure_project_chunk_link(project, user_id=uid, force=False, commit=True)

        return project

    except Exception:
        if commit:
            db.session.rollback()
        else:
            try:
                db.session.rollback()
            except Exception:
                pass
        raise


def update_project(
    project: Any,
    data: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
    provision_chunk_if_missing: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        uid = get_current_user_id(user_id)
        require_project_permission(project, PERMISSION_EDIT, uid, allow_public_view=False)

        before = serialize_project(project, user_id=uid, include_permissions=False)
        payload = _normalize_project_payload(data)

        _apply_project_payload(project, payload)

        conv = get_or_create_project_conversation(project, commit=False)

        if conv is not None:
            try:
                conv.title = project.name or conv.title
                conv.project_id = str(project.id)
                conv.updated_at = _utcnow()
                db.session.add(conv)
            except Exception:
                pass

        if payload.get("chunk_project_id") or payload.get("chunk_world_id"):
            _set_project_chunk_refs(
                project,
                chunk_project_id=payload.get("chunk_project_id") or getattr(project, "chunk_project_id", None),
                chunk_universe_id=payload.get("chunk_universe_id") or getattr(project, "chunk_universe_id", None),
                chunk_world_id=payload.get("chunk_world_id") or getattr(project, "chunk_world_id", None),
                route_hints=_safe_dict(_project_chunk_refs(project).get("route_hints")),
                status=CHUNK_STATUS_READY,
            )

        db.session.add(project)

        _record_project_event(
            project,
            action="updated",
            category="project",
            actor_user_id=uid,
            before=before,
            after=serialize_project(project, user_id=uid, include_permissions=False),
            payload={"source": "project_service.update_project"},
            commit=False,
        )

        should_provision = bool(provision_chunk_if_missing and _should_attempt_chunk_provision(project))

        if should_provision:
            _set_project_chunk_status(project, status=CHUNK_STATUS_PENDING)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        if should_provision and commit:
            ensure_project_chunk_link(project, user_id=uid, force=False, commit=True)

        return project

    except Exception:
        if commit:
            db.session.rollback()
        raise


def create_or_update_project(
    data: Optional[Dict[str, Any]] = None,
    *,
    project_identifier: Optional[str] = None,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        project = resolve_project(project_identifier) if project_identifier else None

        if project is None:
            return create_project(data, user_id=user_id, commit=commit)

        return update_project(project, data, user_id=user_id, commit=commit)

    except Exception:
        if commit:
            db.session.rollback()
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

        uid = get_current_user_id(user_id)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return True

    except Exception:
        if commit:
            db.session.rollback()
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

        uid = get_current_user_id(user_id)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return project

    except Exception:
        if commit:
            db.session.rollback()
        raise


def transfer_project_owner(
    project: Any,
    *,
    new_owner_user_id: int,
    actor_user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        actor_id = get_current_user_id(actor_user_id)
        new_owner_id = _safe_int(new_owner_user_id, 0)

        if not new_owner_id:
            raise ValueError("new_owner_user_id required")

        require_project_permission(project, PERMISSION_TRANSFER, actor_id, allow_public_view=False)

        before = serialize_project(project, user_id=actor_id, include_permissions=True)
        old_owner_user_id = _safe_int(getattr(project, "owner_user_id", None), 0)

        if hasattr(project, "transfer_ownership"):
            project.transfer_ownership(new_owner_id)
        else:
            project.owner_user_id = new_owner_id
            project.transferred_from_user_id = old_owner_user_id or None
            project.transferred_at = _utcnow()
            project.updated_at = _utcnow()

        model_ensure_owner_membership(
            project_id=project.id,
            owner_user_id=new_owner_id,
            commit=False,
        )

        if old_owner_user_id and old_owner_user_id != new_owner_id:
            old_membership = model_get_project_membership(project.id, old_owner_user_id)
            if old_membership is not None:
                try:
                    old_membership.apply_role(ROLE_ADMIN)
                except Exception:
                    old_membership.role = ROLE_ADMIN
                db.session.add(old_membership)

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
            },
            commit=False,
        )

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return project

    except Exception:
        if commit:
            db.session.rollback()
        raise


# ─────────────────────────────────────────────────────────────
# Membership wrappers
# ─────────────────────────────────────────────────────────────

def list_project_memberships(project: Any, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
    try:
        if project is None:
            return []

        project_id = _safe_int(getattr(project, "id", None), 0)
        if not project_id:
            return []

        query = ProjectMembership.query.filter_by(project_id=project_id)

        if not include_inactive:
            query = query.filter_by(status="active")

        rows = query.order_by(ProjectMembership.created_at.asc()).all()

        return [
            model_serialize_membership(row, include_private=True)
            for row in rows
        ]

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
    try:
        if project is None:
            raise ValueError("project required")

        actor_id = get_current_user_id(actor_user_id)
        target_uid = _safe_int(target_user_id, 0)

        if not target_uid:
            raise ValueError("target_user_id required")

        require_project_permission(project, PERMISSION_MANAGE, actor_id, allow_public_view=False)

        before = {}
        membership = model_get_project_membership(project.id, target_uid)

        if membership is not None:
            before = model_serialize_membership(membership, include_private=True)

        clean_role = normalize_role(role)

        if membership is None:
            membership = model_build_membership(
                project_id=project.id,
                user_id=target_uid,
                role=clean_role,
                permissions=_safe_dict(overrides),
            )
        else:
            if hasattr(membership, "apply_role"):
                membership.apply_role(clean_role, permissions=_safe_dict(overrides))
            else:
                membership.role = clean_role

        db.session.add(membership)

        _record_project_event(
            project,
            action="permission_changed",
            category="access",
            actor_user_id=actor_id,
            before=before,
            after=model_serialize_membership(membership, include_private=True),
            payload={"target_user_id": target_uid, "role": clean_role},
            commit=False,
        )

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return membership

    except Exception:
        if commit:
            db.session.rollback()
        raise


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

        uid = get_current_user_id(user_id)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return row

    except Exception:
        if commit:
            db.session.rollback()
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

        uid = get_current_user_id(user_id)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return row

    except Exception:
        if commit:
            db.session.rollback()
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

        uid = get_current_user_id(user_id)
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

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return policy

    except Exception:
        if commit:
            db.session.rollback()
        raise


# ─────────────────────────────────────────────────────────────
# High-level API helpers
# ─────────────────────────────────────────────────────────────

def create_project_result(data: Optional[Dict[str, Any]], *, user_id: Optional[int] = None) -> ProjectOperationResult:
    try:
        project = create_project(data, user_id=user_id, commit=True)

        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={
                "ok": True,
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_embed_policy=True,
                    include_service_links=True,
                ),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
                "redirect_url": project_public_url(project),
                "chunk": _project_chunk_refs(project),
            },
            status_code=201,
            code="project_created",
        )

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
                    include_embed_policy=True,
                    include_service_links=True,
                ),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
                "redirect_url": project_public_url(project),
                "chunk": _project_chunk_refs(project),
            },
            status_code=200,
            code="project_updated",
        )

    except PermissionDenied as exc:
        return ProjectOperationResult(
            ok=False,
            status_code=exc.status_code,
            code=exc.code,
            error=exc.message,
            payload=exc.to_dict(),
        )

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

        uid = get_current_user_id(user_id)
        require_project_permission(project, PERMISSION_MANAGE, uid, allow_public_view=False)

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
                    include_embed_policy=True,
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
        return ProjectOperationResult(
            ok=False,
            status_code=exc.status_code,
            code=exc.code,
            error=exc.message,
            payload=exc.to_dict(),
        )

    except Exception as exc:
        _log_exception("ensure_project_chunk_link_result failed", exc)

        return ProjectOperationResult(
            ok=False,
            status_code=500,
            code="chunk_link_failed",
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

        return ProjectOperationResult(
            ok=True,
            project=project,
            payload={
                "ok": True,
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_members=permissions.can_manage,
                    include_service_links=True,
                    include_versions=True,
                    include_embed_policy=permissions.can_manage or permissions.can_embed,
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
        uid = get_current_user_id(user_id)

        projects = list_projects_for_user(
            user_id=uid,
            include_public=True,
            include_deleted=False,
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
        return ProjectOperationResult(
            ok=False,
            status_code=exc.status_code,
            code=exc.code,
            error=exc.message,
            payload=exc.to_dict(),
        )

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

        return {
            "ok": True,
            "service": "project_service",
            "phase": "app-project-management-with-chunk-provisioning",
            "current_user_id": get_current_user_id(),
            "counts": counts,
            "chunk": {
                "clientAvailable": _chunk_client_available(),
                "provisioningEnabled": _chunk_provisioning_enabled(),
                "provisioningRequired": _chunk_provisioning_required(),
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
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }


__all__ = [
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_DELETED",
    "PROJECT_STATUS_ARCHIVED",
    "PROJECT_SETUP_DRAFT",
    "PROJECT_SETUP_DEFINED",
    "PROJECT_SETUP_CONFIGURED",
    "PROJECT_VISIBILITY_PRIVATE",
    "PROJECT_VISIBILITY_PUBLIC",
    "PROJECT_VISIBILITY_UNLISTED",
    "PROJECT_VISIBILITY_SHARED",
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
    "ProjectOperationResult",
    "ProjectPermissionResult",
    "PermissionDenied",
    "project_public_url",
    "project_workspace_path",
    "project_editor_path",
    "project_map_path",
    "project_cad2d_path",
    "project_lv_path",
    "project_admin_path",
    "build_project_paths",
    "get_current_user_id",
    "ensure_project_user",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_by_conversation_id",
    "resolve_project",
    "get_project_permission_result",
    "serialize_project_permissions",
    "require_project_permission",
    "serialize_project",
    "serialize_project_sidebar_item",
    "serialize_project_list",
    "list_projects_for_user",
    "list_project_sidebar_items",
    "ensure_project_chunk_link",
    "retry_project_chunk_link",
    "create_project",
    "update_project",
    "create_or_update_project",
    "get_or_create_project_conversation",
    "get_or_create_embed_policy",
    "delete_project",
    "archive_project",
    "transfer_project_owner",
    "list_project_memberships",
    "set_project_member_role",
    "list_project_service_links",
    "upsert_project_service_link",
    "list_project_versions",
    "create_project_version_link",
    "update_project_embed_policy",
    "create_project_result",
    "update_project_result",
    "ensure_project_chunk_link_result",
    "get_project_result",
    "list_projects_result",
    "delete_project_result",
    "get_project_service_status",
]