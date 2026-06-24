# services/vectoplan-app/services/project_permissions.py
from __future__ import annotations

"""
VECTOPLAN project permission service.

Zweck:
- Zentrale Rechteprüfung für Projekte in vectoplan-app.
- Nutzt die neuen modularen Models direkt.
- Phase 1 nutzt weiterhin den Platzhalter-User id=1.
- Keine Altmodell-Kompatibilität mehr.

Rollen:
- owner  : alles inklusive löschen, Rechte ändern, Besitz übertragen
- admin  : ansehen, bearbeiten, verwalten, einbetten
- editor : ansehen und bearbeiten
- viewer : nur ansehen

Wichtig:
- vectoplan-app verwaltet hier nur App-seitige Projektverwaltung und UI-Zugriff.
- Chunk-, Editor-, LV-, 2D- und Library-Daten bleiben in ihren Microservices.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False

from extensions import db

from models import (
    AppUser,
    Project,
    ProjectMembership,
    safe_bool,
    safe_dict,
    safe_int,
    safe_str,
    utcnow,
)

try:
    from services.current_user import get_current_user_id_from_g_or_default
except Exception:  # pragma: no cover
    def get_current_user_id_from_g_or_default() -> int:  # type: ignore
        return 1


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

VALID_PROJECT_ROLES = {
    ROLE_OWNER,
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_VIEWER,
}

VALID_PROJECT_PERMISSIONS = {
    PERMISSION_VIEW,
    PERMISSION_EDIT,
    PERMISSION_MANAGE,
    PERMISSION_DELETE,
    PERMISSION_TRANSFER,
    PERMISSION_EMBED,
}

ROLE_PERMISSION_DEFAULTS: Dict[str, Dict[str, bool]] = {
    ROLE_OWNER: {
        PERMISSION_VIEW: True,
        PERMISSION_EDIT: True,
        PERMISSION_MANAGE: True,
        PERMISSION_DELETE: True,
        PERMISSION_TRANSFER: True,
        PERMISSION_EMBED: True,
    },
    ROLE_ADMIN: {
        PERMISSION_VIEW: True,
        PERMISSION_EDIT: True,
        PERMISSION_MANAGE: True,
        PERMISSION_DELETE: False,
        PERMISSION_TRANSFER: False,
        PERMISSION_EMBED: True,
    },
    ROLE_EDITOR: {
        PERMISSION_VIEW: True,
        PERMISSION_EDIT: True,
        PERMISSION_MANAGE: False,
        PERMISSION_DELETE: False,
        PERMISSION_TRANSFER: False,
        PERMISSION_EMBED: False,
    },
    ROLE_VIEWER: {
        PERMISSION_VIEW: True,
        PERMISSION_EDIT: False,
        PERMISSION_MANAGE: False,
        PERMISSION_DELETE: False,
        PERMISSION_TRANSFER: False,
        PERMISSION_EMBED: False,
    },
}

MEMBERSHIP_PERMISSION_ATTRS = {
    PERMISSION_VIEW: "can_view",
    PERMISSION_EDIT: "can_edit",
    PERMISSION_MANAGE: "can_manage",
    PERMISSION_DELETE: "can_delete",
    PERMISSION_TRANSFER: "can_transfer",
    PERMISSION_EMBED: "can_embed",
}


# ─────────────────────────────────────────────────────────────
# Exceptions / result objects
# ─────────────────────────────────────────────────────────────

class PermissionDenied(RuntimeError):
    def __init__(
        self,
        message: str = "permission denied",
        *,
        permission: str = PERMISSION_VIEW,
        project_id: Optional[Any] = None,
        user_id: Optional[int] = None,
        status_code: int = 403,
        code: str = "project_permission_denied",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.permission = permission
        self.project_id = project_id
        self.user_id = user_id
        self.status_code = status_code
        self.code = code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.message,
            "code": self.code,
            "permission": self.permission,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "status_code": self.status_code,
        }


@dataclass(frozen=True)
class PermissionResult:
    user_id: Optional[int]
    project_id: Optional[int]
    project_public_id: Optional[str]
    role: str
    can_view: bool
    can_edit: bool
    can_manage: bool
    can_delete: bool
    can_transfer: bool
    can_embed: bool
    is_owner: bool = False
    is_public_viewer: bool = False
    source: str = "unknown"

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

    def allows(self, permission: Any) -> bool:
        normalized = normalize_permission(permission)
        return bool(self.permissions.get(normalized, False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "project_id": self.project_id,
            "project_public_id": self.project_public_id,
            "role": self.role,
            "permissions": self.permissions,
            "can_view": self.can_view,
            "can_edit": self.can_edit,
            "can_manage": self.can_manage,
            "can_delete": self.can_delete,
            "can_transfer": self.can_transfer,
            "can_embed": self.can_embed,
            "is_owner": self.is_owner,
            "is_public_viewer": self.is_public_viewer,
            "source": self.source,
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    return safe_str(value, default, max_len)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return safe_int(value, default)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        return safe_bool(value, default)
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return safe_dict(value)


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


# ─────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────

def normalize_role(role: Any, default: str = ROLE_VIEWER) -> str:
    try:
        text = _safe_str(role, default, 40).lower().replace("-", "_").strip()

        aliases = {
            "owner": ROLE_OWNER,
            "besitzer": ROLE_OWNER,
            "admin": ROLE_ADMIN,
            "administrator": ROLE_ADMIN,
            "manager": ROLE_ADMIN,
            "editor": ROLE_EDITOR,
            "bearbeiter": ROLE_EDITOR,
            "edit": ROLE_EDITOR,
            "viewer": ROLE_VIEWER,
            "zuschauer": ROLE_VIEWER,
            "view": ROLE_VIEWER,
            "reader": ROLE_VIEWER,
            "readonly": ROLE_VIEWER,
            "read_only": ROLE_VIEWER,
        }

        normalized = aliases.get(text, text)

        if normalized in VALID_PROJECT_ROLES:
            return normalized

        return default if default in VALID_PROJECT_ROLES else ROLE_VIEWER

    except Exception:
        return ROLE_VIEWER


def normalize_permission(permission: Any, default: str = PERMISSION_VIEW) -> str:
    try:
        text = _safe_str(permission, default, 40).lower().replace("-", "_").strip()

        aliases = {
            "read": PERMISSION_VIEW,
            "view": PERMISSION_VIEW,
            "viewer": PERMISSION_VIEW,
            "sehen": PERMISSION_VIEW,
            "anschauen": PERMISSION_VIEW,
            "write": PERMISSION_EDIT,
            "edit": PERMISSION_EDIT,
            "editor": PERMISSION_EDIT,
            "bearbeiten": PERMISSION_EDIT,
            "change": PERMISSION_EDIT,
            "modify": PERMISSION_EDIT,
            "manage": PERMISSION_MANAGE,
            "admin": PERMISSION_MANAGE,
            "verwalten": PERMISSION_MANAGE,
            "delete": PERMISSION_DELETE,
            "remove": PERMISSION_DELETE,
            "löschen": PERMISSION_DELETE,
            "loeschen": PERMISSION_DELETE,
            "transfer": PERMISSION_TRANSFER,
            "owner_transfer": PERMISSION_TRANSFER,
            "besitz_uebertragen": PERMISSION_TRANSFER,
            "besitz_übertragen": PERMISSION_TRANSFER,
            "embed": PERMISSION_EMBED,
            "iframe": PERMISSION_EMBED,
            "einbetten": PERMISSION_EMBED,
        }

        normalized = aliases.get(text, text)

        if normalized in VALID_PROJECT_PERMISSIONS:
            return normalized

        return default if default in VALID_PROJECT_PERMISSIONS else PERMISSION_VIEW

    except Exception:
        return PERMISSION_VIEW


def role_permission_defaults(role: Any) -> Dict[str, bool]:
    try:
        normalized = normalize_role(role)
        return dict(ROLE_PERMISSION_DEFAULTS.get(normalized, ROLE_PERMISSION_DEFAULTS[ROLE_VIEWER]))
    except Exception:
        return dict(ROLE_PERMISSION_DEFAULTS[ROLE_VIEWER])


def permission_attr(permission: Any) -> str:
    normalized = normalize_permission(permission)
    return MEMBERSHIP_PERMISSION_ATTRS.get(normalized, "can_view")


def normalize_permission_overrides(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    data = _safe_dict(overrides)
    result: Dict[str, bool] = {}

    try:
        for permission, attr in MEMBERSHIP_PERMISSION_ATTRS.items():
            if permission in data:
                result[permission] = _safe_bool(data.get(permission), False)
            elif attr in data:
                result[permission] = _safe_bool(data.get(attr), False)

        return result

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# User / project helpers
# ─────────────────────────────────────────────────────────────

def current_user_id(user_id: Optional[int] = None) -> int:
    try:
        parsed = _safe_int(user_id, 0)
        if parsed > 0:
            return parsed

        return _safe_int(get_current_user_id_from_g_or_default(), 1)

    except Exception:
        return 1


def project_identity(project: Any) -> Tuple[Optional[int], Optional[str]]:
    try:
        if project is None:
            return None, None

        project_id = _safe_int(getattr(project, "id", None), 0) or None
        public_id = _safe_str(getattr(project, "public_id", None), "", 120) or None

        return project_id, public_id

    except Exception:
        return None, None


def is_project_deleted(project: Any) -> bool:
    try:
        if project is None:
            return True

        if getattr(project, "deleted_at", None) is not None:
            return True

        status = _safe_str(getattr(project, "status", ""), "", 40).lower()
        if status == "deleted":
            return True

        if hasattr(project, "is_deleted"):
            return bool(getattr(project, "is_deleted"))

        return False

    except Exception:
        return True


def is_project_public(project: Any) -> bool:
    try:
        if project is None or is_project_deleted(project):
            return False

        visibility = _safe_str(getattr(project, "visibility", ""), "", 40).lower()
        public_flag = _safe_bool(getattr(project, "is_public", False), False)

        return public_flag or visibility == "public"

    except Exception:
        return False


def is_project_owner(project: Any, user_id: Optional[int] = None) -> bool:
    try:
        uid = current_user_id(user_id)

        if project is None or uid <= 0:
            return False

        owner_id = _safe_int(getattr(project, "owner_user_id", None), 0)
        return owner_id > 0 and owner_id == uid

    except Exception:
        return False


def membership_is_active(membership: Any) -> bool:
    try:
        if membership is None:
            return False

        if hasattr(membership, "is_active"):
            return bool(getattr(membership, "is_active"))

        if getattr(membership, "revoked_at", None) is not None:
            return False

        status = _safe_str(getattr(membership, "status", "active"), "active", 40).lower()
        return status in {"active", "accepted", "enabled"}

    except Exception:
        return False


def get_project_membership(
    project: Any,
    user_id: Optional[int] = None,
    *,
    include_inactive: bool = False,
) -> Any:
    try:
        if project is None:
            return None

        uid = current_user_id(user_id)
        project_id, _ = project_identity(project)

        if not project_id or uid <= 0:
            return None

        row = ProjectMembership.query.filter_by(
            project_id=project_id,
            user_id=uid,
        ).one_or_none()

        if row is None:
            return None

        if not include_inactive and not membership_is_active(row):
            return None

        return row

    except Exception as exc:
        _log_warning("get_project_membership failed: %s", exc.__class__.__name__)
        return None


def membership_permissions(membership: Any) -> Dict[str, bool]:
    try:
        if membership is None:
            return {
                PERMISSION_VIEW: False,
                PERMISSION_EDIT: False,
                PERMISSION_MANAGE: False,
                PERMISSION_DELETE: False,
                PERMISSION_TRANSFER: False,
                PERMISSION_EMBED: False,
            }

        role = normalize_role(getattr(membership, "role", ROLE_VIEWER))
        permissions = role_permission_defaults(role)

        for permission, attr in MEMBERSHIP_PERMISSION_ATTRS.items():
            permissions[permission] = _safe_bool(
                getattr(membership, attr, permissions.get(permission, False)),
                permissions.get(permission, False),
            )

        if membership_is_active(membership):
            permissions[PERMISSION_VIEW] = True

        if role == ROLE_OWNER:
            permissions = role_permission_defaults(ROLE_OWNER)

        return permissions

    except Exception:
        return {
            PERMISSION_VIEW: False,
            PERMISSION_EDIT: False,
            PERMISSION_MANAGE: False,
            PERMISSION_DELETE: False,
            PERMISSION_TRANSFER: False,
            PERMISSION_EMBED: False,
        }


def project_public_view_permissions(project: Any) -> Dict[str, bool]:
    try:
        if is_project_public(project):
            return {
                PERMISSION_VIEW: True,
                PERMISSION_EDIT: False,
                PERMISSION_MANAGE: False,
                PERMISSION_DELETE: False,
                PERMISSION_TRANSFER: False,
                PERMISSION_EMBED: False,
            }

        return {
            PERMISSION_VIEW: False,
            PERMISSION_EDIT: False,
            PERMISSION_MANAGE: False,
            PERMISSION_DELETE: False,
            PERMISSION_TRANSFER: False,
            PERMISSION_EMBED: False,
        }

    except Exception:
        return {
            PERMISSION_VIEW: False,
            PERMISSION_EDIT: False,
            PERMISSION_MANAGE: False,
            PERMISSION_DELETE: False,
            PERMISSION_TRANSFER: False,
            PERMISSION_EMBED: False,
        }


# ─────────────────────────────────────────────────────────────
# Main permission resolution
# ─────────────────────────────────────────────────────────────

def get_project_permission_result(
    project: Any,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = True,
    membership: Any = None,
) -> PermissionResult:
    uid = current_user_id(user_id)
    project_id, public_id = project_identity(project)

    try:
        if project is None or is_project_deleted(project):
            return PermissionResult(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_VIEWER,
                can_view=False,
                can_edit=False,
                can_manage=False,
                can_delete=False,
                can_transfer=False,
                can_embed=False,
                is_owner=False,
                is_public_viewer=False,
                source="deleted_or_missing",
            )

        if is_project_owner(project, uid):
            permissions = role_permission_defaults(ROLE_OWNER)

            return PermissionResult(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_OWNER,
                can_view=permissions[PERMISSION_VIEW],
                can_edit=permissions[PERMISSION_EDIT],
                can_manage=permissions[PERMISSION_MANAGE],
                can_delete=permissions[PERMISSION_DELETE],
                can_transfer=permissions[PERMISSION_TRANSFER],
                can_embed=permissions[PERMISSION_EMBED],
                is_owner=True,
                is_public_viewer=False,
                source="owner",
            )

        row = membership if membership is not None else get_project_membership(project, uid)

        if row is not None and membership_is_active(row):
            role = normalize_role(getattr(row, "role", ROLE_VIEWER))
            permissions = membership_permissions(row)

            return PermissionResult(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=role,
                can_view=permissions[PERMISSION_VIEW],
                can_edit=permissions[PERMISSION_EDIT],
                can_manage=permissions[PERMISSION_MANAGE],
                can_delete=permissions[PERMISSION_DELETE],
                can_transfer=permissions[PERMISSION_TRANSFER],
                can_embed=permissions[PERMISSION_EMBED],
                is_owner=role == ROLE_OWNER,
                is_public_viewer=False,
                source="membership",
            )

        if allow_public_view and is_project_public(project):
            permissions = project_public_view_permissions(project)

            return PermissionResult(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_VIEWER,
                can_view=permissions[PERMISSION_VIEW],
                can_edit=permissions[PERMISSION_EDIT],
                can_manage=permissions[PERMISSION_MANAGE],
                can_delete=permissions[PERMISSION_DELETE],
                can_transfer=permissions[PERMISSION_TRANSFER],
                can_embed=permissions[PERMISSION_EMBED],
                is_owner=False,
                is_public_viewer=True,
                source="public",
            )

        return PermissionResult(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            can_view=False,
            can_edit=False,
            can_manage=False,
            can_delete=False,
            can_transfer=False,
            can_embed=False,
            is_owner=False,
            is_public_viewer=False,
            source="none",
        )

    except Exception as exc:
        _log_exception("get_project_permission_result failed", exc)

        return PermissionResult(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            can_view=False,
            can_edit=False,
            can_manage=False,
            can_delete=False,
            can_transfer=False,
            can_embed=False,
            is_owner=False,
            is_public_viewer=False,
            source="error",
        )


def has_project_permission(
    project: Any,
    permission: Any = PERMISSION_VIEW,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = True,
) -> bool:
    try:
        result = get_project_permission_result(
            project,
            user_id=user_id,
            allow_public_view=allow_public_view,
        )
        return result.allows(permission)
    except Exception:
        return False


def can_view_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_VIEW, user_id)


def can_edit_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_EDIT, user_id, allow_public_view=False)


def can_manage_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_MANAGE, user_id, allow_public_view=False)


def can_delete_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_DELETE, user_id, allow_public_view=False)


def can_transfer_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_TRANSFER, user_id, allow_public_view=False)


def can_embed_project(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_EMBED, user_id, allow_public_view=False)


def require_project_permission(
    project: Any,
    permission: Any = PERMISSION_VIEW,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = True,
    message: Optional[str] = None,
) -> PermissionResult:
    normalized_permission = normalize_permission(permission)
    uid = current_user_id(user_id)
    project_id, public_id = project_identity(project)

    result = get_project_permission_result(
        project,
        user_id=uid,
        allow_public_view=allow_public_view,
    )

    if result.allows(normalized_permission):
        return result

    raise PermissionDenied(
        message or f"missing project permission: {normalized_permission}",
        permission=normalized_permission,
        project_id=public_id or project_id,
        user_id=uid,
        status_code=403,
        code="project_permission_denied",
    )


# ─────────────────────────────────────────────────────────────
# Membership mutation helpers
# ─────────────────────────────────────────────────────────────

def apply_role_to_membership(
    membership: Any,
    role: Any = ROLE_VIEWER,
    *,
    permissions: Optional[Dict[str, Any]] = None,
) -> Any:
    try:
        clean_role = normalize_role(role)
        defaults = role_permission_defaults(clean_role)
        overrides = normalize_permission_overrides(permissions)

        for permission, value in overrides.items():
            defaults[permission] = bool(value)

        membership.role = clean_role

        for permission, attr in MEMBERSHIP_PERMISSION_ATTRS.items():
            setattr(membership, attr, bool(defaults.get(permission, False)))

        if clean_role == ROLE_OWNER:
            for attr in MEMBERSHIP_PERMISSION_ATTRS.values():
                setattr(membership, attr, True)

        membership.status = "active"

        if hasattr(membership, "revoked_at"):
            membership.revoked_at = None
        if hasattr(membership, "revoked_by_user_id"):
            membership.revoked_by_user_id = None
        if hasattr(membership, "revoke_reason"):
            membership.revoke_reason = None
        if hasattr(membership, "accepted_at") and getattr(membership, "accepted_at", None) is None:
            membership.accepted_at = utcnow()

        if hasattr(membership, "normalize"):
            membership.normalize()

        return membership

    except Exception:
        return membership


def build_membership_for_role(
    *,
    project_id: Any,
    user_id: int,
    role: str = ROLE_VIEWER,
    created_by_user_id: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
    permissions: Optional[Dict[str, Any]] = None,
) -> Any:
    clean_project_id = _safe_int(project_id, 0)
    clean_user_id = _safe_int(user_id, 0)
    clean_role = normalize_role(role)
    clean_permissions = _safe_dict(permissions or overrides)

    if clean_project_id <= 0:
        raise ValueError("project_id required")

    if clean_user_id <= 0:
        raise ValueError("user_id required")

    try:
        if hasattr(ProjectMembership, "build"):
            return ProjectMembership.build(
                project_id=clean_project_id,
                user_id=clean_user_id,
                role=clean_role,
                permissions=clean_permissions,
                invited_by_user_id=created_by_user_id,
                status="active",
                metadata={},
            )
    except TypeError:
        pass
    except Exception:
        pass

    membership = ProjectMembership()
    membership.project_id = clean_project_id
    membership.user_id = clean_user_id
    membership.invited_by_user_id = _safe_int(created_by_user_id, 0) or None

    if hasattr(membership, "invited_at") and created_by_user_id:
        membership.invited_at = utcnow()

    if hasattr(membership, "accepted_at"):
        membership.accepted_at = utcnow()

    apply_role_to_membership(
        membership,
        clean_role,
        permissions=clean_permissions,
    )

    return membership


def ensure_owner_membership(
    project: Any = None,
    *,
    project_id: Optional[Any] = None,
    owner_user_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    commit: bool = False,
) -> Any:
    try:
        resolved_project_id = _safe_int(project_id, 0)

        if not resolved_project_id and project is not None:
            resolved_project_id = _safe_int(getattr(project, "id", None), 0)

        uid = _safe_int(owner_user_id, 0)

        if not uid and project is not None:
            uid = _safe_int(getattr(project, "owner_user_id", None), 0)

        if not uid:
            uid = 1

        if not resolved_project_id or uid <= 0:
            return None

        membership = ProjectMembership.query.filter_by(
            project_id=resolved_project_id,
            user_id=uid,
        ).one_or_none()

        if membership is None:
            membership = build_membership_for_role(
                project_id=resolved_project_id,
                user_id=uid,
                role=ROLE_OWNER,
                created_by_user_id=created_by_user_id or uid,
            )
        else:
            apply_role_to_membership(membership, ROLE_OWNER)

        db.session.add(membership)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return membership

    except Exception:
        if commit:
            db.session.rollback()
        raise


def grant_project_role(
    project: Any,
    *,
    user_id: int,
    role: str = ROLE_VIEWER,
    actor_user_id: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
    permissions: Optional[Dict[str, Any]] = None,
    commit: bool = False,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        project_id, _ = project_identity(project)
        uid = _safe_int(user_id, 0)

        if not project_id:
            raise ValueError("project.id required")

        if uid <= 0:
            raise ValueError("user_id required")

        clean_role = normalize_role(role)
        clean_permissions = _safe_dict(permissions or overrides)

        membership = ProjectMembership.query.filter_by(
            project_id=project_id,
            user_id=uid,
        ).one_or_none()

        if membership is None:
            membership = build_membership_for_role(
                project_id=project_id,
                user_id=uid,
                role=clean_role,
                created_by_user_id=actor_user_id,
                permissions=clean_permissions,
            )
        else:
            if hasattr(membership, "apply_role"):
                try:
                    membership.apply_role(clean_role, permissions=clean_permissions)
                except TypeError:
                    apply_role_to_membership(membership, clean_role, permissions=clean_permissions)
            else:
                apply_role_to_membership(membership, clean_role, permissions=clean_permissions)

        db.session.add(membership)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return membership

    except Exception:
        if commit:
            db.session.rollback()
        raise


def revoke_project_membership(
    project: Any,
    *,
    user_id: int,
    actor_user_id: Optional[int] = None,
    hard_delete: bool = False,
    commit: bool = False,
) -> bool:
    try:
        if project is None:
            return False

        uid = _safe_int(user_id, 0)
        project_id, public_id = project_identity(project)

        if uid <= 0 or not project_id:
            return False

        if is_project_owner(project, uid):
            raise PermissionDenied(
                "cannot revoke current owner membership",
                permission=PERMISSION_TRANSFER,
                project_id=public_id or project_id,
                user_id=actor_user_id,
                code="cannot_revoke_owner",
            )

        membership = ProjectMembership.query.filter_by(
            project_id=project_id,
            user_id=uid,
        ).one_or_none()

        if membership is None:
            return False

        if hard_delete:
            db.session.delete(membership)
        else:
            if hasattr(membership, "revoke"):
                membership.revoke(user_id=actor_user_id, reason="revoked")
            else:
                membership.status = "revoked"
                if hasattr(membership, "revoked_at"):
                    membership.revoked_at = utcnow()
                if hasattr(membership, "revoked_by_user_id"):
                    membership.revoked_by_user_id = _safe_int(actor_user_id, 0) or None

            db.session.add(membership)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return True

    except Exception:
        if commit:
            db.session.rollback()
        raise


def transfer_project_ownership(
    project: Any,
    *,
    new_owner_user_id: int,
    actor_user_id: Optional[int] = None,
    old_owner_role: str = ROLE_ADMIN,
    commit: bool = False,
) -> Any:
    try:
        if project is None:
            raise ValueError("project required")

        new_uid = _safe_int(new_owner_user_id, 0)
        if new_uid <= 0:
            raise ValueError("new_owner_user_id required")

        old_owner_id = _safe_int(getattr(project, "owner_user_id", None), 0)

        if hasattr(project, "transfer_ownership"):
            project.transfer_ownership(new_uid)
        else:
            project.owner_user_id = new_uid
            if hasattr(project, "transferred_from_user_id"):
                project.transferred_from_user_id = old_owner_id or None
            if hasattr(project, "transferred_at"):
                project.transferred_at = utcnow()

        ensure_owner_membership(
            project,
            owner_user_id=new_uid,
            created_by_user_id=actor_user_id,
            commit=False,
        )

        if old_owner_id > 0 and old_owner_id != new_uid:
            grant_project_role(
                project,
                user_id=old_owner_id,
                role=normalize_role(old_owner_role, ROLE_ADMIN),
                actor_user_id=actor_user_id,
                commit=False,
            )

        db.session.add(project)

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
# Query/list helpers
# ─────────────────────────────────────────────────────────────

def filter_viewable_projects(projects: Iterable[Any], user_id: Optional[int] = None) -> List[Any]:
    try:
        uid = current_user_id(user_id)
        result: List[Any] = []

        for project in list(projects or []):
            try:
                if can_view_project(project, uid):
                    result.append(project)
            except Exception:
                continue

        return result

    except Exception:
        return []


def serialize_project_permissions(project: Any, user_id: Optional[int] = None) -> Dict[str, Any]:
    try:
        return get_project_permission_result(project, user_id=user_id).to_dict()
    except Exception:
        uid = current_user_id(user_id)
        project_id, public_id = project_identity(project)

        return PermissionResult(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            can_view=False,
            can_edit=False,
            can_manage=False,
            can_delete=False,
            can_transfer=False,
            can_embed=False,
            source="serialize_error",
        ).to_dict()


def serialize_membership(membership: Any, *, include_private: bool = False) -> Dict[str, Any]:
    try:
        if membership is None:
            return {}

        if hasattr(membership, "to_dict"):
            try:
                return membership.to_dict(include_private=include_private)
            except TypeError:
                return membership.to_dict()

        role = normalize_role(getattr(membership, "role", ROLE_VIEWER))
        permissions = membership_permissions(membership)

        return {
            "id": getattr(membership, "id", None),
            "project_id": getattr(membership, "project_id", None),
            "user_id": getattr(membership, "user_id", None),
            "role": role,
            "permissions": permissions,
            "can_view": permissions[PERMISSION_VIEW],
            "can_edit": permissions[PERMISSION_EDIT],
            "can_manage": permissions[PERMISSION_MANAGE],
            "can_delete": permissions[PERMISSION_DELETE],
            "can_transfer": permissions[PERMISSION_TRANSFER],
            "can_embed": permissions[PERMISSION_EMBED],
            "status": getattr(membership, "status", None),
        }

    except Exception:
        return {}


def list_project_memberships(project: Any, *, include_inactive: bool = False) -> List[Any]:
    try:
        if project is None:
            return []

        project_id, _ = project_identity(project)

        if not project_id:
            return []

        query = ProjectMembership.query.filter_by(project_id=project_id)

        if not include_inactive:
            query = query.filter_by(status="active")

        return list(query.order_by(ProjectMembership.created_at.asc()).all())

    except Exception as exc:
        _log_warning("list_project_memberships failed: %s", exc.__class__.__name__)
        return []


def list_project_membership_dicts(project: Any, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_membership(row, include_private=True)
            for row in list_project_memberships(project, include_inactive=include_inactive)
        ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_permission_service_status() -> Dict[str, Any]:
    try:
        counts: Dict[str, Any] = {}

        try:
            counts["users"] = AppUser.query.count()
        except Exception:
            counts["users"] = None

        try:
            counts["projects"] = Project.query.count()
        except Exception:
            counts["projects"] = None

        try:
            counts["memberships"] = ProjectMembership.query.count()
        except Exception:
            counts["memberships"] = None

        return {
            "ok": True,
            "service": "project_permissions",
            "phase": "placeholder-user-project-permissions",
            "roles": sorted(VALID_PROJECT_ROLES),
            "permissions": sorted(VALID_PROJECT_PERMISSIONS),
            "models": {
                "AppUser": AppUser is not None,
                "Project": Project is not None,
                "ProjectMembership": ProjectMembership is not None,
            },
            "counts": counts,
            "current_user_id": current_user_id(),
        }

    except Exception as exc:
        return {
            "ok": False,
            "service": "project_permissions",
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }


__all__ = [
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "ROLE_VIEWER",
    "PERMISSION_VIEW",
    "PERMISSION_EDIT",
    "PERMISSION_MANAGE",
    "PERMISSION_DELETE",
    "PERMISSION_TRANSFER",
    "PERMISSION_EMBED",
    "VALID_PROJECT_ROLES",
    "VALID_PROJECT_PERMISSIONS",
    "ROLE_PERMISSION_DEFAULTS",
    "PermissionDenied",
    "PermissionResult",
    "normalize_role",
    "normalize_permission",
    "role_permission_defaults",
    "permission_attr",
    "normalize_permission_overrides",
    "current_user_id",
    "project_identity",
    "is_project_deleted",
    "is_project_public",
    "is_project_owner",
    "membership_is_active",
    "get_project_membership",
    "membership_permissions",
    "get_project_permission_result",
    "has_project_permission",
    "can_view_project",
    "can_edit_project",
    "can_manage_project",
    "can_delete_project",
    "can_transfer_project",
    "can_embed_project",
    "require_project_permission",
    "apply_role_to_membership",
    "build_membership_for_role",
    "ensure_owner_membership",
    "grant_project_role",
    "revoke_project_membership",
    "transfer_project_ownership",
    "filter_viewable_projects",
    "serialize_project_permissions",
    "serialize_membership",
    "list_project_memberships",
    "list_project_membership_dicts",
    "get_permission_service_status",
]