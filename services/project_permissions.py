# services/vectoplan-app/services/project_permissions.py
from __future__ import annotations

"""
VECTOPLAN project permission service.

Zweck:
- Zentrale Rechteprüfung für Projekte in vectoplan-app.
- Verwaltet App-seitige Projektrollen und Projektberechtigungen.
- Bleibt kompatibel mit dem aktuellen Entwicklungsuser id=1.
- Unterstützt den vorbereiteten Auth-/Demo-Kontext aus current_user.py.
- Verhindert, dass unberechtigte User Projektsettings, Teamverwaltung oder
  technische Bereiche sehen.
- Verhindert persistente Projektänderungen im Demo-Modus.
- Erzeugt KEINE echten Benutzeraccounts.

Rollen:
- owner  : alles inklusive löschen, Rechte ändern, Besitz übertragen
- admin  : ansehen, bearbeiten, verwalten, einbetten
- editor : ansehen und bearbeiten
- viewer : nur ansehen

Wichtige Architekturregel:
- vectoplan-app verwaltet Rollen, Sichtbarkeit, Veröffentlichungen und
  Projektfrontend.
- Registrierung, Login, Abo-Status und Bigdata-Zugriff liegen später im
  separaten Auth-/Registrierungsdienst.
- Chunk-, Editor-, LV-, 2D- und Library-Fachdaten bleiben in ihren Microservices.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False


try:
    from extensions import db
except Exception:  # pragma: no cover
    db = None  # type: ignore


try:
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
except Exception:  # pragma: no cover
    AppUser = None  # type: ignore
    Project = None  # type: ignore
    ProjectMembership = None  # type: ignore

    def safe_bool(value: Any, default: bool = False) -> bool:  # type: ignore
        try:
            if isinstance(value, bool):
                return value
            text = str(value if value is not None else "").strip().lower()
            if text in {"1", "true", "yes", "y", "on", "ja"}:
                return True
            if text in {"0", "false", "no", "n", "off", "nein"}:
                return False
            return default
        except Exception:
            return default

    def safe_dict(value: Any) -> Dict[str, Any]:  # type: ignore
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

    def safe_int(value: Any, default: int = 0) -> int:  # type: ignore
        try:
            if value is None or isinstance(value, bool):
                return default
            parsed = int(str(value).strip())
            return parsed
        except Exception:
            return default

    def safe_str(value: Any, default: str = "", max_len: int = 240) -> str:  # type: ignore
        try:
            text = str(value if value is not None else default).strip()
            if not text:
                text = default
            return text[:max_len] if max_len > 0 else text
        except Exception:
            return default

    def utcnow() -> Any:  # type: ignore
        import datetime as _dt
        return _dt.datetime.utcnow()


try:
    from services.current_user import (
        current_user_can_persist,
        get_current_user_context,
        get_current_user_id_from_g_or_default,
        get_current_user_id_optional,
        is_current_user_demo,
    )
except Exception:  # pragma: no cover

    def get_current_user_id_from_g_or_default() -> int:  # type: ignore
        return 1

    def get_current_user_id_optional() -> Optional[int]:  # type: ignore
        return 1

    def get_current_user_context(*args: Any, **kwargs: Any) -> Any:  # type: ignore
        return {
            "user_id": 1,
            "id": 1,
            "authenticated": True,
            "demo_mode": False,
            "persistent": True,
            "source": "fallback",
        }

    def is_current_user_demo() -> bool:  # type: ignore
        return False

    def current_user_can_persist() -> bool:  # type: ignore
        return True


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

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

# UI-/Settings-spezifische Berechtigungen.
# Diese sind bewusst abgeleitet und nicht zwingend DB-Spalten.
PERMISSION_VIEW_SETTINGS = "view_settings"
PERMISSION_MANAGE_SETTINGS = "manage_settings"
PERMISSION_VIEW_TEAM = "view_team"
PERMISSION_MANAGE_TEAM = "manage_team"
PERMISSION_VIEW_ADMIN = "view_admin"

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
    PERMISSION_VIEW_SETTINGS,
    PERMISSION_MANAGE_SETTINGS,
    PERMISSION_VIEW_TEAM,
    PERMISSION_MANAGE_TEAM,
    PERMISSION_VIEW_ADMIN,
}

BASE_PROJECT_PERMISSIONS = {
    PERMISSION_VIEW,
    PERMISSION_EDIT,
    PERMISSION_MANAGE,
    PERMISSION_DELETE,
    PERMISSION_TRANSFER,
    PERMISSION_EMBED,
}

SETTINGS_PERMISSIONS = {
    PERMISSION_VIEW_SETTINGS,
    PERMISSION_MANAGE_SETTINGS,
    PERMISSION_VIEW_TEAM,
    PERMISSION_MANAGE_TEAM,
    PERMISSION_VIEW_ADMIN,
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

PROJECT_VISIBILITY_PRIVATE = "private"
PROJECT_VISIBILITY_SHARED = "shared"
PROJECT_VISIBILITY_UNLISTED = "unlisted"
PROJECT_VISIBILITY_PUBLIC = "public"

PUBLIC_VIEW_VISIBILITIES = {
    PROJECT_VISIBILITY_PUBLIC,
    PROJECT_VISIBILITY_UNLISTED,
}

ACTIVE_MEMBERSHIP_STATUSES = {
    "active",
    "accepted",
    "enabled",
}

INACTIVE_MEMBERSHIP_STATUSES = {
    "revoked",
    "removed",
    "deleted",
    "disabled",
    "inactive",
    "rejected",
    "expired",
}

MANAGER_ROLES = {
    ROLE_OWNER,
    ROLE_ADMIN,
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
            "message": self.message,
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

    # Settings/UI visibility.
    can_view_settings: bool = False
    can_manage_settings: bool = False
    can_view_team: bool = False
    can_manage_team: bool = False
    can_view_admin: bool = False

    is_owner: bool = False
    is_public_viewer: bool = False
    is_unlisted_viewer: bool = False
    is_member: bool = False

    authenticated: bool = True
    demo_mode: bool = False
    persistent: bool = True

    source: str = "unknown"
    reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def permissions(self) -> Dict[str, bool]:
        return {
            PERMISSION_VIEW: bool(self.can_view),
            PERMISSION_EDIT: bool(self.can_edit),
            PERMISSION_MANAGE: bool(self.can_manage),
            PERMISSION_DELETE: bool(self.can_delete),
            PERMISSION_TRANSFER: bool(self.can_transfer),
            PERMISSION_EMBED: bool(self.can_embed),
            PERMISSION_VIEW_SETTINGS: bool(self.can_view_settings),
            PERMISSION_MANAGE_SETTINGS: bool(self.can_manage_settings),
            PERMISSION_VIEW_TEAM: bool(self.can_view_team),
            PERMISSION_MANAGE_TEAM: bool(self.can_manage_team),
            PERMISSION_VIEW_ADMIN: bool(self.can_view_admin),
        }

    @property
    def base_permissions(self) -> Dict[str, bool]:
        return {
            PERMISSION_VIEW: bool(self.can_view),
            PERMISSION_EDIT: bool(self.can_edit),
            PERMISSION_MANAGE: bool(self.can_manage),
            PERMISSION_DELETE: bool(self.can_delete),
            PERMISSION_TRANSFER: bool(self.can_transfer),
            PERMISSION_EMBED: bool(self.can_embed),
        }

    @property
    def settings_permissions(self) -> Dict[str, bool]:
        return {
            PERMISSION_VIEW_SETTINGS: bool(self.can_view_settings),
            PERMISSION_MANAGE_SETTINGS: bool(self.can_manage_settings),
            PERMISSION_VIEW_TEAM: bool(self.can_view_team),
            PERMISSION_MANAGE_TEAM: bool(self.can_manage_team),
            PERMISSION_VIEW_ADMIN: bool(self.can_view_admin),
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
            "base_permissions": self.base_permissions,
            "settings_permissions": self.settings_permissions,
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
            "is_owner": self.is_owner,
            "is_public_viewer": self.is_public_viewer,
            "is_unlisted_viewer": self.is_unlisted_viewer,
            "is_member": self.is_member,
            "authenticated": self.authenticated,
            "demo_mode": self.demo_mode,
            "persistent": self.persistent,
            "source": self.source,
            "reason": self.reason,
            "extra": dict(self.extra or {}),
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        return safe_str(value, default, max_len)
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
        return safe_int(value, default)
    except Exception:
        try:
            if value is None or isinstance(value, bool):
                return default
            return int(str(value).strip())
        except Exception:
            return default


def _safe_int_optional(value: Any) -> Optional[int]:
    try:
        parsed = _safe_int(value, 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        return safe_bool(value, default)
    except Exception:
        try:
            if isinstance(value, bool):
                return value
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
        return safe_dict(value)
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
        if db is not None:
            db.session.add(obj)
    except Exception:
        pass


def _db_flush_or_commit(commit: bool = False) -> None:
    if db is None:
        return

    if commit:
        db.session.commit()
    else:
        db.session.flush()


def _db_rollback_safely() -> None:
    try:
        if db is not None:
            db.session.rollback()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Auth/current actor helpers
# ─────────────────────────────────────────────────────────────

def get_actor_context(user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Liefert aktuellen Actor-Kontext als Dict.

    Bei explizitem user_id wird bewusst ein persistenter Kontext angenommen.
    Das hält bestehende Service-Aufrufe kompatibel.
    """
    if user_id is not None:
        parsed = _safe_int_optional(user_id)
        return {
            "user_id": parsed,
            "id": parsed,
            "authenticated": bool(parsed),
            "demo_mode": False,
            "persistent": bool(parsed),
            "source": "explicit_user_id",
        }

    try:
        context = get_current_user_context(ensure=False)
        data = _safe_dict(context)
        if not data and hasattr(context, "to_dict"):
            data = _safe_dict(context.to_dict())
        return data
    except Exception:
        uid = _safe_int_optional(get_current_user_id_from_g_or_default())
        return {
            "user_id": uid,
            "id": uid,
            "authenticated": bool(uid),
            "demo_mode": False,
            "persistent": bool(uid),
            "source": "fallback",
        }


def actor_user_id(user_id: Optional[int] = None) -> Optional[int]:
    try:
        context = get_actor_context(user_id)
        return _safe_int_optional(context.get("user_id") or context.get("id"))
    except Exception:
        return None


def actor_is_authenticated(user_id: Optional[int] = None) -> bool:
    try:
        context = get_actor_context(user_id)
        return _safe_bool(
            context.get("authenticated")
            or context.get("is_authenticated")
            or context.get("logged_in"),
            default=bool(_safe_int_optional(context.get("user_id") or context.get("id"))),
        )
    except Exception:
        return False


def actor_is_demo(user_id: Optional[int] = None) -> bool:
    try:
        if user_id is not None:
            return False

        context = get_actor_context(user_id)
        return _safe_bool(
            context.get("demo_mode")
            or context.get("is_demo")
            or context.get("demo"),
            default=False,
        )
    except Exception:
        try:
            return bool(is_current_user_demo())
        except Exception:
            return False


def actor_can_persist(user_id: Optional[int] = None) -> bool:
    try:
        if user_id is not None:
            return True

        context = get_actor_context(user_id)
        return _safe_bool(
            context.get("persistent"),
            default=bool(_safe_int_optional(context.get("user_id") or context.get("id"))) and not actor_is_demo(),
        )
    except Exception:
        try:
            return bool(current_user_can_persist())
        except Exception:
            return False


def current_user_id(user_id: Optional[int] = None) -> int:
    """
    Legacy-kompatibler Resolver.

    Achtung:
    - Für Demo-/Auth-sensible Logik besser actor_user_id() verwenden.
    - Diese Funktion gibt aus Kompatibilitätsgründen 1 zurück, wenn kein User
      auflösbar ist.
    """
    try:
        parsed = _safe_int(user_id, 0)
        if parsed > 0:
            return parsed

        optional = get_current_user_id_optional()
        if optional:
            return _safe_int(optional, 1)

        return _safe_int(get_current_user_id_from_g_or_default(), 1)

    except Exception:
        return 1


# ─────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────

def normalize_role(role: Any, default: str = ROLE_VIEWER) -> str:
    try:
        text = _safe_str(role, default, 40).lower().replace("-", "_").strip()

        aliases = {
            "owner": ROLE_OWNER,
            "besitzer": ROLE_OWNER,
            "eigentümer": ROLE_OWNER,
            "eigentuemer": ROLE_OWNER,
            "admin": ROLE_ADMIN,
            "administrator": ROLE_ADMIN,
            "manager": ROLE_ADMIN,
            "manage": ROLE_ADMIN,
            "editor": ROLE_EDITOR,
            "bearbeiter": ROLE_EDITOR,
            "edit": ROLE_EDITOR,
            "write": ROLE_EDITOR,
            "writer": ROLE_EDITOR,
            "member": ROLE_EDITOR,
            "viewer": ROLE_VIEWER,
            "zuschauer": ROLE_VIEWER,
            "view": ROLE_VIEWER,
            "reader": ROLE_VIEWER,
            "readonly": ROLE_VIEWER,
            "read_only": ROLE_VIEWER,
            "gast": ROLE_VIEWER,
            "guest": ROLE_VIEWER,
        }

        normalized = aliases.get(text, text)

        if normalized in VALID_PROJECT_ROLES:
            return normalized

        return default if default in VALID_PROJECT_ROLES else ROLE_VIEWER

    except Exception:
        return ROLE_VIEWER


def normalize_permission(permission: Any, default: str = PERMISSION_VIEW) -> str:
    try:
        text = _safe_str(permission, default, 60).lower().replace("-", "_").strip()

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

            "settings": PERMISSION_VIEW_SETTINGS,
            "view_settings": PERMISSION_VIEW_SETTINGS,
            "settings_view": PERMISSION_VIEW_SETTINGS,
            "einstellungen": PERMISSION_VIEW_SETTINGS,

            "manage_settings": PERMISSION_MANAGE_SETTINGS,
            "settings_manage": PERMISSION_MANAGE_SETTINGS,
            "edit_settings": PERMISSION_MANAGE_SETTINGS,

            "team": PERMISSION_VIEW_TEAM,
            "members": PERMISSION_VIEW_TEAM,
            "view_team": PERMISSION_VIEW_TEAM,

            "manage_team": PERMISSION_MANAGE_TEAM,
            "edit_team": PERMISSION_MANAGE_TEAM,
            "members_manage": PERMISSION_MANAGE_TEAM,

            "admin_view": PERMISSION_VIEW_ADMIN,
            "view_admin": PERMISSION_VIEW_ADMIN,
        }

        normalized = aliases.get(text, text)

        if normalized in VALID_PROJECT_PERMISSIONS:
            return normalized

        return default if default in VALID_PROJECT_PERMISSIONS else PERMISSION_VIEW

    except Exception:
        return PERMISSION_VIEW


def normalize_visibility(value: Any, default: str = PROJECT_VISIBILITY_PRIVATE) -> str:
    try:
        text = _safe_str(value, default, 40).lower().replace("-", "_").strip()

        aliases = {
            "private": PROJECT_VISIBILITY_PRIVATE,
            "privat": PROJECT_VISIBILITY_PRIVATE,
            "internal": PROJECT_VISIBILITY_PRIVATE,
            "intern": PROJECT_VISIBILITY_PRIVATE,
            "closed": PROJECT_VISIBILITY_PRIVATE,
            "shared": PROJECT_VISIBILITY_PRIVATE,
            "geteilt": PROJECT_VISIBILITY_PRIVATE,

            "unlisted": PROJECT_VISIBILITY_UNLISTED,
            "not_listed": PROJECT_VISIBILITY_UNLISTED,
            "nicht_gelistet": PROJECT_VISIBILITY_UNLISTED,
            "link": PROJECT_VISIBILITY_UNLISTED,
            "linkshare": PROJECT_VISIBILITY_UNLISTED,
            "link_shared": PROJECT_VISIBILITY_UNLISTED,

            "public": PROJECT_VISIBILITY_PUBLIC,
            "öffentlich": PROJECT_VISIBILITY_PUBLIC,
            "oeffentlich": PROJECT_VISIBILITY_PUBLIC,
            "open": PROJECT_VISIBILITY_PUBLIC,
            "listed": PROJECT_VISIBILITY_PUBLIC,
        }

        normalized = aliases.get(text, text)

        if normalized in {
            PROJECT_VISIBILITY_PRIVATE,
            PROJECT_VISIBILITY_SHARED,
            PROJECT_VISIBILITY_UNLISTED,
            PROJECT_VISIBILITY_PUBLIC,
        }:
            if normalized == PROJECT_VISIBILITY_SHARED:
                return PROJECT_VISIBILITY_PRIVATE
            return normalized

        return default

    except Exception:
        return default


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


def derive_settings_permissions(base_permissions: Mapping[str, Any], role: Any) -> Dict[str, bool]:
    """
    Settings/Admin/Team werden nur aus manage-Recht abgeleitet.

    Konsequenz:
    - viewer/editor sehen Projektsettings nicht.
    - public/unlisted viewer sehen Projektsettings nicht.
    - admin/owner sehen Team/Veröffentlichung/Admin.
    """
    clean_role = normalize_role(role)
    can_manage = _safe_bool(base_permissions.get(PERMISSION_MANAGE), False)

    if clean_role in {ROLE_OWNER, ROLE_ADMIN}:
        can_manage = True

    return {
        PERMISSION_VIEW_SETTINGS: bool(can_manage),
        PERMISSION_MANAGE_SETTINGS: bool(can_manage),
        PERMISSION_VIEW_TEAM: bool(can_manage),
        PERMISSION_MANAGE_TEAM: bool(can_manage),
        PERMISSION_VIEW_ADMIN: bool(can_manage),
    }


# ─────────────────────────────────────────────────────────────
# Project/user helpers
# ─────────────────────────────────────────────────────────────

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


def project_visibility(project: Any) -> str:
    try:
        if project is None:
            return PROJECT_VISIBILITY_PRIVATE

        return normalize_visibility(getattr(project, "visibility", PROJECT_VISIBILITY_PRIVATE))

    except Exception:
        return PROJECT_VISIBILITY_PRIVATE


def is_project_public(project: Any) -> bool:
    try:
        if project is None or is_project_deleted(project):
            return False

        visibility = project_visibility(project)
        public_flag = _safe_bool(getattr(project, "is_public", False), False)

        return public_flag or visibility == PROJECT_VISIBILITY_PUBLIC

    except Exception:
        return False


def is_project_unlisted(project: Any) -> bool:
    try:
        if project is None or is_project_deleted(project):
            return False

        return project_visibility(project) == PROJECT_VISIBILITY_UNLISTED

    except Exception:
        return False


def is_project_publicly_viewable(project: Any) -> bool:
    try:
        if project is None or is_project_deleted(project):
            return False

        return is_project_public(project) or is_project_unlisted(project)

    except Exception:
        return False


def is_project_owner(project: Any, user_id: Optional[int] = None) -> bool:
    try:
        uid = actor_user_id(user_id)

        if project is None or not uid:
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
            try:
                return bool(getattr(membership, "is_active"))
            except Exception:
                pass

        if getattr(membership, "revoked_at", None) is not None:
            return False

        if getattr(membership, "deleted_at", None) is not None:
            return False

        if _safe_bool(getattr(membership, "is_deleted", False), False):
            return False

        status = _safe_str(getattr(membership, "status", "active"), "active", 40).lower()

        if status in INACTIVE_MEMBERSHIP_STATUSES:
            return False

        return status in ACTIVE_MEMBERSHIP_STATUSES or not status

    except Exception:
        return False


def get_project_membership(
    project: Any,
    user_id: Optional[int] = None,
    *,
    include_inactive: bool = False,
) -> Any:
    try:
        if project is None or ProjectMembership is None:
            return None

        uid = actor_user_id(user_id)
        project_id, _ = project_identity(project)

        if not project_id or not uid:
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
            return {permission: False for permission in BASE_PROJECT_PERMISSIONS}

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
        return {permission: False for permission in BASE_PROJECT_PERMISSIONS}


def project_public_view_permissions(project: Any) -> Dict[str, bool]:
    try:
        if is_project_publicly_viewable(project):
            return {
                PERMISSION_VIEW: True,
                PERMISSION_EDIT: False,
                PERMISSION_MANAGE: False,
                PERMISSION_DELETE: False,
                PERMISSION_TRANSFER: False,
                PERMISSION_EMBED: False,
            }

        return {permission: False for permission in BASE_PROJECT_PERMISSIONS}

    except Exception:
        return {permission: False for permission in BASE_PROJECT_PERMISSIONS}


def _permission_result_from_base(
    *,
    user_id: Optional[int],
    project_id: Optional[int],
    project_public_id: Optional[str],
    role: str,
    base_permissions: Mapping[str, Any],
    is_owner: bool = False,
    is_public_viewer: bool = False,
    is_unlisted_viewer: bool = False,
    is_member: bool = False,
    authenticated: bool = True,
    demo_mode: bool = False,
    persistent: bool = True,
    source: str = "unknown",
    reason: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> PermissionResult:
    clean_role = normalize_role(role)
    base = {permission: _safe_bool(base_permissions.get(permission), False) for permission in BASE_PROJECT_PERMISSIONS}

    if demo_mode or not persistent:
        # Demo und nicht verknüpfte Auth-User dürfen nur öffentlich sehen.
        if not is_public_viewer and not is_unlisted_viewer:
            base = {permission: False for permission in BASE_PROJECT_PERMISSIONS}
        else:
            base[PERMISSION_EDIT] = False
            base[PERMISSION_MANAGE] = False
            base[PERMISSION_DELETE] = False
            base[PERMISSION_TRANSFER] = False
            base[PERMISSION_EMBED] = False

    settings = derive_settings_permissions(base, clean_role)

    if is_public_viewer or is_unlisted_viewer:
        # Öffentliche/ungelistete Betrachter sehen keine Einstellungen.
        settings = {permission: False for permission in SETTINGS_PERMISSIONS}

    if demo_mode or not persistent:
        settings = {permission: False for permission in SETTINGS_PERMISSIONS}

    return PermissionResult(
        user_id=user_id,
        project_id=project_id,
        project_public_id=project_public_id,
        role=clean_role,
        can_view=base[PERMISSION_VIEW],
        can_edit=base[PERMISSION_EDIT],
        can_manage=base[PERMISSION_MANAGE],
        can_delete=base[PERMISSION_DELETE],
        can_transfer=base[PERMISSION_TRANSFER],
        can_embed=base[PERMISSION_EMBED],
        can_view_settings=settings[PERMISSION_VIEW_SETTINGS],
        can_manage_settings=settings[PERMISSION_MANAGE_SETTINGS],
        can_view_team=settings[PERMISSION_VIEW_TEAM],
        can_manage_team=settings[PERMISSION_MANAGE_TEAM],
        can_view_admin=settings[PERMISSION_VIEW_ADMIN],
        is_owner=is_owner,
        is_public_viewer=is_public_viewer,
        is_unlisted_viewer=is_unlisted_viewer,
        is_member=is_member,
        authenticated=authenticated,
        demo_mode=demo_mode,
        persistent=persistent,
        source=source,
        reason=reason,
        extra=dict(extra or {}),
    )


# ─────────────────────────────────────────────────────────────
# Main permission resolution
# ─────────────────────────────────────────────────────────────

def get_project_permission_result(
    project: Any,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = True,
    membership: Any = None,
    actor_context: Optional[Mapping[str, Any]] = None,
) -> PermissionResult:
    context = _safe_dict(actor_context) if actor_context is not None else get_actor_context(user_id)
    uid = _safe_int_optional(context.get("user_id") or context.get("id"))
    project_id, public_id = project_identity(project)

    authenticated = _safe_bool(
        context.get("authenticated")
        or context.get("is_authenticated")
        or context.get("logged_in"),
        default=bool(uid),
    )
    demo_mode = _safe_bool(
        context.get("demo_mode")
        or context.get("is_demo")
        or context.get("demo"),
        default=False,
    )
    persistent = _safe_bool(
        context.get("persistent"),
        default=bool(uid) and not demo_mode,
    )

    try:
        if project is None or is_project_deleted(project):
            return _permission_result_from_base(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_VIEWER,
                base_permissions={},
                authenticated=authenticated,
                demo_mode=demo_mode,
                persistent=persistent,
                source="deleted_or_missing",
                reason="project_deleted_or_missing",
            )

        if uid and persistent and is_project_owner(project, uid):
            return _permission_result_from_base(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_OWNER,
                base_permissions=role_permission_defaults(ROLE_OWNER),
                is_owner=True,
                is_member=True,
                authenticated=authenticated,
                demo_mode=demo_mode,
                persistent=persistent,
                source="owner",
            )

        row = membership if membership is not None else get_project_membership(project, uid)

        if uid and persistent and row is not None and membership_is_active(row):
            role = normalize_role(getattr(row, "role", ROLE_VIEWER))
            permissions = membership_permissions(row)

            return _permission_result_from_base(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=role,
                base_permissions=permissions,
                is_owner=role == ROLE_OWNER,
                is_member=True,
                authenticated=authenticated,
                demo_mode=demo_mode,
                persistent=persistent,
                source="membership",
                extra={"membership_id": getattr(row, "id", None)},
            )

        if allow_public_view and is_project_publicly_viewable(project):
            permissions = project_public_view_permissions(project)
            visibility = project_visibility(project)

            return _permission_result_from_base(
                user_id=uid,
                project_id=project_id,
                project_public_id=public_id,
                role=ROLE_VIEWER,
                base_permissions=permissions,
                is_owner=False,
                is_member=False,
                is_public_viewer=visibility == PROJECT_VISIBILITY_PUBLIC,
                is_unlisted_viewer=visibility == PROJECT_VISIBILITY_UNLISTED,
                authenticated=authenticated,
                demo_mode=demo_mode,
                persistent=persistent,
                source="public_visibility" if visibility == PROJECT_VISIBILITY_PUBLIC else "unlisted_visibility",
                reason="public_or_unlisted_view_only",
                extra={"visibility": visibility},
            )

        return _permission_result_from_base(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            base_permissions={},
            authenticated=authenticated,
            demo_mode=demo_mode,
            persistent=persistent,
            source="none",
            reason="no_membership_or_public_access",
        )

    except Exception as exc:
        _log_exception("get_project_permission_result failed", exc)

        return _permission_result_from_base(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            base_permissions={},
            authenticated=authenticated,
            demo_mode=demo_mode,
            persistent=persistent,
            source="error",
            reason=exc.__class__.__name__,
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


def can_view_project_settings(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_VIEW_SETTINGS, user_id, allow_public_view=False)


def can_manage_project_settings(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_MANAGE_SETTINGS, user_id, allow_public_view=False)


def can_view_project_team(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_VIEW_TEAM, user_id, allow_public_view=False)


def can_manage_project_team(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_MANAGE_TEAM, user_id, allow_public_view=False)


def can_view_project_admin(project: Any, user_id: Optional[int] = None) -> bool:
    return has_project_permission(project, PERMISSION_VIEW_ADMIN, user_id, allow_public_view=False)


def require_project_permission(
    project: Any,
    permission: Any = PERMISSION_VIEW,
    user_id: Optional[int] = None,
    *,
    allow_public_view: bool = True,
    message: Optional[str] = None,
) -> PermissionResult:
    normalized_permission = normalize_permission(permission)
    uid = actor_user_id(user_id)
    project_id, public_id = project_identity(project)

    result = get_project_permission_result(
        project,
        user_id=user_id,
        allow_public_view=allow_public_view,
    )

    if result.allows(normalized_permission):
        return result

    if result.demo_mode:
        code = "demo_mode_not_allowed"
        default_message = "Im Demo-Modus ist diese Projektaktion nicht erlaubt."
    elif not result.authenticated:
        code = "authentication_required"
        default_message = "Für diese Projektaktion ist Login erforderlich."
    elif not result.persistent:
        code = "persistent_user_required"
        default_message = "Für diese Projektaktion ist eine lokale AppUser-Verknüpfung erforderlich."
    else:
        code = "project_permission_denied"
        default_message = f"missing project permission: {normalized_permission}"

    raise PermissionDenied(
        message or default_message,
        permission=normalized_permission,
        project_id=public_id or project_id,
        user_id=uid,
        status_code=401 if code == "authentication_required" else 403,
        code=code,
    )


def require_project_settings_permission(
    project: Any,
    user_id: Optional[int] = None,
    *,
    message: Optional[str] = None,
) -> PermissionResult:
    return require_project_permission(
        project,
        PERMISSION_MANAGE_SETTINGS,
        user_id=user_id,
        allow_public_view=False,
        message=message or "missing project settings permission",
    )


def require_project_team_permission(
    project: Any,
    user_id: Optional[int] = None,
    *,
    message: Optional[str] = None,
) -> PermissionResult:
    return require_project_permission(
        project,
        PERMISSION_MANAGE_TEAM,
        user_id=user_id,
        allow_public_view=False,
        message=message or "missing project team permission",
    )


# ─────────────────────────────────────────────────────────────
# Membership mutation safeguards
# ─────────────────────────────────────────────────────────────

def apply_role_to_membership(
    membership: Any,
    role: Any = ROLE_VIEWER,
    *,
    permissions: Optional[Dict[str, Any]] = None,
    allow_owner: bool = True,
) -> Any:
    try:
        clean_role = normalize_role(role)
        if clean_role == ROLE_OWNER and not allow_owner:
            clean_role = ROLE_ADMIN

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
    allow_owner: bool = True,
) -> Any:
    if ProjectMembership is None:
        raise RuntimeError("ProjectMembership model unavailable")

    clean_project_id = _safe_int(project_id, 0)
    clean_user_id = _safe_int(user_id, 0)
    clean_role = normalize_role(role)
    if clean_role == ROLE_OWNER and not allow_owner:
        clean_role = ROLE_ADMIN

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
        allow_owner=allow_owner,
    )

    return membership


def active_manager_count(project: Any, *, exclude_user_id: Optional[int] = None) -> int:
    try:
        if project is None or ProjectMembership is None:
            return 0

        project_id, _ = project_identity(project)
        if not project_id:
            return 0

        owner_id = _safe_int(getattr(project, "owner_user_id", None), 0)
        excluded = _safe_int(exclude_user_id, 0)

        count = 0

        if owner_id > 0 and owner_id != excluded:
            count += 1

        rows = ProjectMembership.query.filter_by(project_id=project_id).all()

        for row in rows:
            try:
                uid = _safe_int(getattr(row, "user_id", None), 0)
                if uid <= 0 or uid == excluded:
                    continue

                if not membership_is_active(row):
                    continue

                role = normalize_role(getattr(row, "role", ROLE_VIEWER))
                perms = membership_permissions(row)

                if role in MANAGER_ROLES or _safe_bool(perms.get(PERMISSION_MANAGE), False):
                    count += 1
            except Exception:
                continue

        return count

    except Exception:
        return 0


def ensure_owner_membership(
    project: Any = None,
    *,
    project_id: Optional[Any] = None,
    owner_user_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    commit: bool = False,
) -> Any:
    try:
        if ProjectMembership is None or db is None:
            return None

        resolved_project_id = _safe_int(project_id, 0)

        if not resolved_project_id and project is not None:
            resolved_project_id = _safe_int(getattr(project, "id", None), 0)

        uid = _safe_int(owner_user_id, 0)

        if not uid and project is not None:
            uid = _safe_int(getattr(project, "owner_user_id", None), 0)

        if not uid:
            uid = actor_user_id() or 0

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
                allow_owner=True,
            )
        else:
            apply_role_to_membership(membership, ROLE_OWNER, allow_owner=True)

        _db_add(membership)
        _db_flush_or_commit(commit)

        return membership

    except Exception:
        if commit:
            _db_rollback_safely()
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
    allow_owner: bool = False,
) -> Any:
    try:
        if ProjectMembership is None or db is None:
            raise RuntimeError("ProjectMembership/db unavailable")

        if project is None:
            raise ValueError("project required")

        project_id, _ = project_identity(project)
        uid = _safe_int(user_id, 0)

        if not project_id:
            raise ValueError("project.id required")

        if uid <= 0:
            raise ValueError("user_id required")

        clean_role = normalize_role(role)
        if clean_role == ROLE_OWNER and not allow_owner:
            clean_role = ROLE_ADMIN

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
                allow_owner=allow_owner,
            )
        else:
            if hasattr(membership, "apply_role"):
                try:
                    membership.apply_role(clean_role, permissions=clean_permissions)
                except TypeError:
                    apply_role_to_membership(
                        membership,
                        clean_role,
                        permissions=clean_permissions,
                        allow_owner=allow_owner,
                    )
            else:
                apply_role_to_membership(
                    membership,
                    clean_role,
                    permissions=clean_permissions,
                    allow_owner=allow_owner,
                )

        _db_add(membership)
        _db_flush_or_commit(commit)

        return membership

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


def can_revoke_project_membership(
    project: Any,
    *,
    user_id: int,
    actor_user_id: Optional[int] = None,
) -> Tuple[bool, str]:
    try:
        uid = _safe_int(user_id, 0)
        actor_uid = _safe_int(actor_user_id, 0)
        project_id, _ = project_identity(project)

        if project is None or not project_id:
            return False, "project_required"

        if uid <= 0:
            return False, "user_id_required"

        if is_project_owner(project, uid):
            return False, "cannot_revoke_owner"

        membership = get_project_membership(project, uid, include_inactive=True)
        if membership is None:
            return False, "membership_not_found"

        role = normalize_role(getattr(membership, "role", ROLE_VIEWER))
        perms = membership_permissions(membership)
        target_is_manager = role in MANAGER_ROLES or _safe_bool(perms.get(PERMISSION_MANAGE), False)

        if target_is_manager and active_manager_count(project, exclude_user_id=uid) <= 0:
            return False, "cannot_remove_last_manager"

        if actor_uid and actor_uid == uid:
            return False, "cannot_revoke_self"

        return True, "ok"

    except Exception as exc:
        return False, exc.__class__.__name__


def revoke_project_membership(
    project: Any,
    *,
    user_id: int,
    actor_user_id: Optional[int] = None,
    hard_delete: bool = False,
    commit: bool = False,
) -> bool:
    try:
        if ProjectMembership is None or db is None:
            return False

        if project is None:
            return False

        uid = _safe_int(user_id, 0)
        project_id, public_id = project_identity(project)

        if uid <= 0 or not project_id:
            return False

        allowed, reason = can_revoke_project_membership(
            project,
            user_id=uid,
            actor_user_id=actor_user_id,
        )

        if not allowed:
            raise PermissionDenied(
                reason,
                permission=PERMISSION_MANAGE,
                project_id=public_id or project_id,
                user_id=actor_user_id,
                code=reason,
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
                try:
                    membership.revoke(user_id=actor_user_id, reason="revoked")
                except TypeError:
                    membership.revoke(actor_user_id)
            else:
                membership.status = "revoked"
                if hasattr(membership, "revoked_at"):
                    membership.revoked_at = utcnow()
                if hasattr(membership, "revoked_by_user_id"):
                    membership.revoked_by_user_id = _safe_int(actor_user_id, 0) or None
                if hasattr(membership, "revoke_reason"):
                    membership.revoke_reason = "revoked"

            _db_add(membership)

        _db_flush_or_commit(commit)

        return True

    except Exception:
        if commit:
            _db_rollback_safely()
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
        if db is None:
            raise RuntimeError("db unavailable")

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
                allow_owner=False,
            )

        _db_add(project)
        _db_flush_or_commit(commit)

        return project

    except Exception:
        if commit:
            _db_rollback_safely()
        raise


# ─────────────────────────────────────────────────────────────
# Query/list helpers
# ─────────────────────────────────────────────────────────────

def filter_viewable_projects(projects: Iterable[Any], user_id: Optional[int] = None) -> List[Any]:
    try:
        result: List[Any] = []

        for project in list(projects or []):
            try:
                if can_view_project(project, user_id):
                    result.append(project)
            except Exception:
                continue

        return result

    except Exception:
        return []


def filter_manageable_projects(projects: Iterable[Any], user_id: Optional[int] = None) -> List[Any]:
    try:
        result: List[Any] = []

        for project in list(projects or []):
            try:
                if can_manage_project(project, user_id):
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
        uid = actor_user_id(user_id)
        project_id, public_id = project_identity(project)

        return _permission_result_from_base(
            user_id=uid,
            project_id=project_id,
            project_public_id=public_id,
            role=ROLE_VIEWER,
            base_permissions={},
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
        settings_permissions = derive_settings_permissions(permissions, role)

        data = {
            "id": getattr(membership, "id", None),
            "project_id": getattr(membership, "project_id", None),
            "user_id": getattr(membership, "user_id", None),
            "role": role,
            "permissions": {
                **permissions,
                **settings_permissions,
            },
            "base_permissions": permissions,
            "settings_permissions": settings_permissions,
            "can_view": permissions[PERMISSION_VIEW],
            "can_edit": permissions[PERMISSION_EDIT],
            "can_manage": permissions[PERMISSION_MANAGE],
            "can_delete": permissions[PERMISSION_DELETE],
            "can_transfer": permissions[PERMISSION_TRANSFER],
            "can_embed": permissions[PERMISSION_EMBED],
            "can_view_settings": settings_permissions[PERMISSION_VIEW_SETTINGS],
            "can_manage_settings": settings_permissions[PERMISSION_MANAGE_SETTINGS],
            "can_view_team": settings_permissions[PERMISSION_VIEW_TEAM],
            "can_manage_team": settings_permissions[PERMISSION_MANAGE_TEAM],
            "can_view_admin": settings_permissions[PERMISSION_VIEW_ADMIN],
            "status": getattr(membership, "status", None),
            "active": membership_is_active(membership),
        }

        if include_private:
            for key in [
                "invited_by_user_id",
                "accepted_at",
                "revoked_at",
                "revoked_by_user_id",
                "created_at",
                "updated_at",
            ]:
                try:
                    value = getattr(membership, key, None)
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    data[key] = value
                except Exception:
                    pass

        return data

    except Exception:
        return {}


def list_project_memberships(project: Any, *, include_inactive: bool = False) -> List[Any]:
    try:
        if project is None or ProjectMembership is None:
            return []

        project_id, _ = project_identity(project)

        if not project_id:
            return []

        query = ProjectMembership.query.filter_by(project_id=project_id)

        if not include_inactive and hasattr(ProjectMembership, "status"):
            query = query.filter(ProjectMembership.status.in_(list(ACTIVE_MEMBERSHIP_STATUSES)))

        try:
            return list(query.order_by(ProjectMembership.created_at.asc()).all())
        except Exception:
            return list(query.all())

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
            counts["users"] = AppUser.query.count() if AppUser is not None else None
        except Exception:
            counts["users"] = None

        try:
            counts["projects"] = Project.query.count() if Project is not None else None
        except Exception:
            counts["projects"] = None

        try:
            counts["memberships"] = ProjectMembership.query.count() if ProjectMembership is not None else None
        except Exception:
            counts["memberships"] = None

        actor_context = get_actor_context()

        return {
            "ok": True,
            "service": "project_permissions",
            "phase": "auth-demo-aware-project-permissions",
            "roles": sorted(VALID_PROJECT_ROLES),
            "permissions": sorted(VALID_PROJECT_PERMISSIONS),
            "base_permissions": sorted(BASE_PROJECT_PERMISSIONS),
            "settings_permissions": sorted(SETTINGS_PERMISSIONS),
            "models": {
                "AppUser": AppUser is not None,
                "Project": Project is not None,
                "ProjectMembership": ProjectMembership is not None,
            },
            "counts": counts,
            "current_user_id": actor_user_id(),
            "actor_context": actor_context,
            "notes": {
                "settings_visibility": "Projektsettings/Team/Admin sind nur für manage/admin/owner sichtbar.",
                "demo_mode": "Demo-User dürfen keine persistenten Projektänderungen durchführen.",
                "public_view": "Public/unlisted erzeugt nur view-Recht, keine Settings-Rechte.",
                "user_creation": "Dieser Service erzeugt keine AppUser.",
            },
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


# ─────────────────────────────────────────────────────────────
# Public exports
# ─────────────────────────────────────────────────────────────

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
    "PERMISSION_VIEW_SETTINGS",
    "PERMISSION_MANAGE_SETTINGS",
    "PERMISSION_VIEW_TEAM",
    "PERMISSION_MANAGE_TEAM",
    "PERMISSION_VIEW_ADMIN",
    "VALID_PROJECT_ROLES",
    "VALID_PROJECT_PERMISSIONS",
    "BASE_PROJECT_PERMISSIONS",
    "SETTINGS_PERMISSIONS",
    "ROLE_PERMISSION_DEFAULTS",
    "PermissionDenied",
    "PermissionResult",
    "active_manager_count",
    "actor_can_persist",
    "actor_is_authenticated",
    "actor_is_demo",
    "actor_user_id",
    "apply_role_to_membership",
    "build_membership_for_role",
    "can_delete_project",
    "can_edit_project",
    "can_embed_project",
    "can_manage_project",
    "can_manage_project_settings",
    "can_manage_project_team",
    "can_revoke_project_membership",
    "can_transfer_project",
    "can_view_project",
    "can_view_project_admin",
    "can_view_project_settings",
    "can_view_project_team",
    "current_user_id",
    "derive_settings_permissions",
    "ensure_owner_membership",
    "filter_manageable_projects",
    "filter_viewable_projects",
    "get_actor_context",
    "get_permission_service_status",
    "get_project_membership",
    "get_project_permission_result",
    "grant_project_role",
    "has_project_permission",
    "is_project_deleted",
    "is_project_owner",
    "is_project_public",
    "is_project_publicly_viewable",
    "is_project_unlisted",
    "list_project_membership_dicts",
    "list_project_memberships",
    "membership_is_active",
    "membership_permissions",
    "normalize_permission",
    "normalize_permission_overrides",
    "normalize_role",
    "normalize_visibility",
    "permission_attr",
    "project_identity",
    "project_public_view_permissions",
    "project_visibility",
    "require_project_permission",
    "require_project_settings_permission",
    "require_project_team_permission",
    "revoke_project_membership",
    "role_permission_defaults",
    "serialize_membership",
    "serialize_project_permissions",
    "transfer_project_ownership",
]