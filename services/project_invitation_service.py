# services/vectoplan-app/services/project_invitation_service.py
"""
VECTOPLAN project invitation service.

Zweck:
- Fachliche Service-Schicht für Projekt-Einladungen.
- Prüft Projektberechtigungen.
- Prüft E-Mail-Adressen gegen den späteren Auth-/Registrierungsdienst.
- Erzeugt KEINE lokalen Benutzeraccounts.
- Speichert ProjectInvitation-Datensätze.
- Stößt optional einen externen/platzhalterhaften Einladungsversand an.
- Bereitet die spätere Annahme einer Einladung durch bereits verknüpfte AppUser vor.
- Schreibt, wenn verfügbar, Audit-Events.

Architekturregel:
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Veröffentlichungen und Projektfrontend.
- Registrierung, Login, Account-Typ, Abo-Status und Bigdata-Zugriff liegen außerhalb von vectoplan-app.
- Eine Einladung per E-Mail ist nur möglich, wenn der Auth-/Registrierungsdienst diese E-Mail als registriert kennt.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
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
                "project_invitation_service requires SQLAlchemy db."
            ) from exc


try:
    from models.projects import Project  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.projects import Project  # type: ignore
    except Exception:
        Project = None  # type: ignore


try:
    from models.project_access import ProjectMembership  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.project_access import ProjectMembership  # type: ignore
    except Exception:
        ProjectMembership = None  # type: ignore


try:
    from models.project_audit import ProjectAuditEvent  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.project_audit import ProjectAuditEvent  # type: ignore
    except Exception:
        ProjectAuditEvent = None  # type: ignore


try:
    from models.users import AppUser  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..models.users import AppUser  # type: ignore
    except Exception:
        AppUser = None  # type: ignore


try:
    from models.project_invitations import (  # type: ignore
        DEFAULT_INVITATION_EXPIRY_DAYS,
        DEFAULT_INVITATION_ROLE,
        DISPATCH_FAILED,
        INVITABLE_PROJECT_ROLES,
        ProjectInvitation,
        ROLE_ADMIN,
        ROLE_EDITOR,
        ROLE_OWNER,
        ROLE_VIEWER,
        STATUS_ACCEPTED,
        STATUS_EXPIRED,
        STATUS_FAILED,
        STATUS_PENDING,
        STATUS_REJECTED,
        STATUS_REVOKED,
        hash_invitation_token,
        invitation_status_counts,
        is_valid_email,
        normalize_email,
        normalize_invitation_role,
        serialize_project_invitation,
        serialize_project_invitations,
    )
except Exception:  # pragma: no cover
    try:
        from ..models.project_invitations import (  # type: ignore
            DEFAULT_INVITATION_EXPIRY_DAYS,
            DEFAULT_INVITATION_ROLE,
            DISPATCH_FAILED,
            INVITABLE_PROJECT_ROLES,
            ProjectInvitation,
            ROLE_ADMIN,
            ROLE_EDITOR,
            ROLE_OWNER,
            ROLE_VIEWER,
            STATUS_ACCEPTED,
            STATUS_EXPIRED,
            STATUS_FAILED,
            STATUS_PENDING,
            STATUS_REJECTED,
            STATUS_REVOKED,
            hash_invitation_token,
            invitation_status_counts,
            is_valid_email,
            normalize_email,
            normalize_invitation_role,
            serialize_project_invitation,
            serialize_project_invitations,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "project_invitation_service requires models.project_invitations."
        ) from exc


try:
    from services.auth_identity_client import (  # type: ignore
        dispatch_project_invitation_identity,
        get_auth_identity_status,
        require_registered_email_identity,
    )
except Exception:  # pragma: no cover
    try:
        from .auth_identity_client import (  # type: ignore
            dispatch_project_invitation_identity,
            get_auth_identity_status,
            require_registered_email_identity,
        )
    except Exception:
        dispatch_project_invitation_identity = None  # type: ignore
        get_auth_identity_status = None  # type: ignore
        require_registered_email_identity = None  # type: ignore


try:
    from services.project_permissions import (  # type: ignore
        can_manage_project,
        get_project_permission_result,
        require_project_permission,
    )
except Exception:  # pragma: no cover
    try:
        from .project_permissions import (  # type: ignore
            can_manage_project,
            get_project_permission_result,
            require_project_permission,
        )
    except Exception:
        can_manage_project = None  # type: ignore
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

LOGGER_NAME = "vectoplan.project_invitation_service"

ACTION_INVITATION_CREATED = "invitation_created"
ACTION_INVITATION_DISPATCHED = "invitation_dispatched"
ACTION_INVITATION_FAILED = "invitation_failed"
ACTION_INVITATION_REVOKED = "invitation_revoked"
ACTION_INVITATION_REJECTED = "invitation_rejected"
ACTION_INVITATION_ACCEPTED = "invitation_accepted"
ACTION_INVITATION_EXPIRED = "invitation_expired"

AUDIT_CATEGORY_ACCESS = "project_access"

DEFAULT_INVITATION_ROUTE = "/project-invitations"

MEMBERSHIP_STATUS_ACTIVE = "active"
MEMBERSHIP_STATUS_REVOKED = "revoked"
MEMBERSHIP_STATUS_PENDING = "pending"

ROLE_PERMISSION_MATRIX = {
    ROLE_OWNER: {
        "view": True,
        "edit": True,
        "manage": True,
        "delete": True,
        "transfer": True,
        "embed": True,
    },
    ROLE_ADMIN: {
        "view": True,
        "edit": True,
        "manage": True,
        "delete": False,
        "transfer": False,
        "embed": True,
    },
    ROLE_EDITOR: {
        "view": True,
        "edit": True,
        "manage": False,
        "delete": False,
        "transfer": False,
        "embed": False,
    },
    ROLE_VIEWER: {
        "view": True,
        "edit": False,
        "manage": False,
        "delete": False,
        "transfer": False,
        "embed": False,
    },
}


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
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
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


def _getattr_any(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        try:
            if isinstance(obj, Mapping) and name in obj:
                return obj.get(name)
            if hasattr(obj, name):
                return getattr(obj, name)
        except Exception:
            continue
    return default


def _setattr_if_present(obj: Any, name: str, value: Any) -> None:
    try:
        if hasattr(obj, name):
            setattr(obj, name, value)
    except Exception:
        pass


def _json_clone(value: Any) -> Dict[str, Any]:
    try:
        return json.loads(json.dumps(_safe_dict(value), ensure_ascii=False))
    except Exception:
        return _safe_dict(value)


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


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass
class ProjectInvitationServiceResult:
    ok: bool
    code: str
    message: str = ""
    project: Any = None
    invitation: Optional[ProjectInvitation] = None
    invitations: list = field(default_factory=list)
    membership: Any = None
    identity: Dict[str, Any] = field(default_factory=dict)
    dispatch: Dict[str, Any] = field(default_factory=dict)
    access: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    error: Optional[str] = None

    def to_dict(
        self,
        include_private: bool = False,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        project_id = None
        project_public_id = None

        try:
            if self.project is not None:
                project_id = getattr(self.project, "id", None)
                project_public_id = getattr(self.project, "public_id", None)
        except Exception:
            pass

        result: Dict[str, Any] = {
            "ok": bool(self.ok),
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "project_id": project_id,
            "project_public_id": project_public_id,
            "identity": self.identity,
            "dispatch": self.dispatch,
            "access": self.access,
            "data": self.data,
            "error": self.error,
        }

        if self.invitation is not None:
            result["invitation"] = serialize_project_invitation(
                self.invitation,
                include_private=include_private,
                include_auth=True,
                include_raw=include_raw,
            )

        if self.invitations:
            result["invitations"] = serialize_project_invitations(
                self.invitations,
                include_private=include_private,
                include_auth=True,
                include_raw=include_raw,
            )
            result["invitation_counts"] = invitation_status_counts(self.invitations)

        if self.membership is not None:
            result["membership"] = _serialize_membership(self.membership)

        return result


def _result(
    ok: bool,
    code: str,
    message: str = "",
    status_code: int = 200,
    **kwargs: Any,
) -> ProjectInvitationServiceResult:
    return ProjectInvitationServiceResult(
        ok=ok,
        code=code,
        message=message,
        status_code=status_code,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Actor / Auth context helpers
# ---------------------------------------------------------------------------


def get_actor_context(user_id: Any = None) -> Dict[str, Any]:
    """
    Liefert aktuellen Actor-Kontext robust.

    Aktuell kann current_user.py noch den Dev-User id=1 liefern.
    Später soll diese Funktion automatisch AuthContext-Werte aus Login/
    Registrierungsdienst durchreichen.
    """
    context: Dict[str, Any] = {}

    try:
        if get_current_user_context is not None:
            maybe_context = get_current_user_context()
            context = _safe_dict(maybe_context)
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

    actor_user_id = _safe_int(
        context.get("user_id") or context.get("id"),
        default=None,
    )

    auth_user_id = _safe_str(
        context.get("auth_user_id")
        or context.get("external_user_id")
        or context.get("sub")
        or context.get("subject"),
        default="",
        max_len=160,
    )

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

    account_plan = _safe_str(
        context.get("account_plan")
        or context.get("plan")
        or context.get("subscription_plan"),
        default="",
        max_len=80,
    )

    return {
        **context,
        "user_id": actor_user_id,
        "id": actor_user_id,
        "auth_user_id": auth_user_id or None,
        "demo_mode": bool(demo_mode),
        "authenticated": bool(authenticated),
        "account_plan": account_plan or None,
    }


def _actor_user_id(actor_context: Optional[Mapping[str, Any]]) -> Optional[int]:
    data = _safe_dict(actor_context)
    return _safe_int(data.get("user_id") or data.get("id"), default=None)


def _actor_auth_user_id(actor_context: Optional[Mapping[str, Any]]) -> Optional[str]:
    data = _safe_dict(actor_context)
    value = _safe_str(
        data.get("auth_user_id")
        or data.get("external_user_id")
        or data.get("sub")
        or data.get("subject"),
        default="",
        max_len=160,
    )
    return value or None


def _actor_is_demo(actor_context: Optional[Mapping[str, Any]]) -> bool:
    data = _safe_dict(actor_context)
    return _safe_bool(data.get("demo_mode") or data.get("is_demo") or data.get("demo"), default=False)


# ---------------------------------------------------------------------------
# Project resolving
# ---------------------------------------------------------------------------


def resolve_project(project_or_id: Any) -> Optional[Any]:
    """
    Akzeptiert:
    - Project-Objekt
    - numerische DB-ID
    - public_id
    """
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


def _project_public_id(project: Any) -> str:
    return _safe_str(getattr(project, "public_id", ""), default="", max_len=100)


def _project_id(project: Any) -> Optional[int]:
    return _safe_int(getattr(project, "id", None), default=None)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


def _permission_denied_result(project: Any, permission: str = "manage") -> ProjectInvitationServiceResult:
    return _result(
        ok=False,
        code="project_permission_denied",
        message=f"Du hast keine Berechtigung für diese Projektaktion: {permission}.",
        project=project,
        status_code=403,
        access={"permission": permission},
    )


def _require_manage_permission(project: Any, actor_context: Mapping[str, Any]) -> Optional[ProjectInvitationServiceResult]:
    """
    Gibt None zurück, wenn Zugriff erlaubt ist, sonst ein Result.
    """
    actor_user_id = _actor_user_id(actor_context)

    if not actor_user_id:
        return _result(
            ok=False,
            code="authentication_required",
            message="Für diese Aktion ist ein eingeloggter Benutzer erforderlich.",
            project=project,
            status_code=401,
        )

    if _actor_is_demo(actor_context):
        return _result(
            ok=False,
            code="demo_mode_not_allowed",
            message="Im Demo-Modus können keine Projekt-Einladungen oder Rollenänderungen gespeichert werden.",
            project=project,
            status_code=403,
            data={"demo_mode": True},
        )

    try:
        if require_project_permission is not None:
            maybe = require_project_permission(project, "manage", user_id=actor_user_id)
            if maybe is None:
                return None

            maybe_dict = _safe_dict(maybe)
            if not maybe_dict:
                return None

            if _safe_bool(maybe_dict.get("ok"), default=True):
                return None

            return _permission_denied_result(project, "manage")
    except Exception:
        # Einige Implementierungen werfen bei Verweigerung. Dann fallback prüfen.
        pass

    try:
        if can_manage_project is not None:
            if bool(can_manage_project(project, actor_user_id)):
                return None
            return _permission_denied_result(project, "manage")
    except Exception:
        pass

    try:
        if get_project_permission_result is not None:
            perm = get_project_permission_result(project, actor_user_id)
            perm_dict = _safe_dict(perm)

            if _safe_bool(perm_dict.get("can_manage"), default=False):
                return None

            permissions = _safe_dict(perm_dict.get("permissions"))
            if _safe_bool(permissions.get("manage"), default=False):
                return None

            if _safe_bool(perm_dict.get("ok"), default=True) is False:
                return _permission_denied_result(project, "manage")
    except Exception:
        pass

    # Minimaler Fallback für Dev: owner_user_id darf verwalten.
    try:
        owner_user_id = _safe_int(getattr(project, "owner_user_id", None))
        if owner_user_id is not None and actor_user_id == owner_user_id:
            return None
    except Exception:
        pass

    # Fallback über ProjectMembership.
    try:
        membership = _find_membership(_project_id(project), actor_user_id)
        if membership is not None and _membership_has_manage(membership):
            return None
    except Exception:
        pass

    return _permission_denied_result(project, "manage")


# ---------------------------------------------------------------------------
# AppUser lookup only, no creation
# ---------------------------------------------------------------------------


def find_linked_app_user(
    auth_user_id: Any = None,
    email: Any = None,
) -> Optional[Any]:
    """
    Sucht einen bereits existierenden lokalen AppUser-Link.

    Wichtig:
    Diese Funktion erzeugt KEINEN AppUser.
    """
    if AppUser is None:
        return None

    safe_auth_user_id = _safe_str(auth_user_id, default="", max_len=160)
    safe_email = normalize_email(email)

    if not safe_auth_user_id and not safe_email:
        return None

    try:
        if safe_auth_user_id and hasattr(AppUser, "auth_user_id"):
            found = AppUser.query.filter(AppUser.auth_user_id == safe_auth_user_id).first()
            if found is not None:
                return found
    except Exception:
        pass

    try:
        if safe_email and hasattr(AppUser, "email"):
            found = AppUser.query.filter(AppUser.email == safe_email).first()
            if found is not None:
                return found
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Membership helpers
# ---------------------------------------------------------------------------


def _permission_flags_for_role(role: Any) -> Dict[str, bool]:
    normalized = normalize_invitation_role(role, allow_owner=True)
    return dict(ROLE_PERMISSION_MATRIX.get(normalized, ROLE_PERMISSION_MATRIX[ROLE_VIEWER]))


def _find_membership(project_id: Any, user_id: Any) -> Optional[Any]:
    safe_project_id = _safe_int(project_id)
    safe_user_id = _safe_int(user_id)

    if ProjectMembership is None or not safe_project_id or not safe_user_id:
        return None

    try:
        return ProjectMembership.query.filter(
            ProjectMembership.project_id == safe_project_id,
            ProjectMembership.user_id == safe_user_id,
        ).first()
    except Exception:
        return None


def _membership_status(membership: Any) -> str:
    return _safe_str(getattr(membership, "status", MEMBERSHIP_STATUS_ACTIVE), default=MEMBERSHIP_STATUS_ACTIVE)


def _membership_is_active(membership: Any) -> bool:
    try:
        if membership is None:
            return False
        if _safe_bool(getattr(membership, "is_deleted", False), default=False):
            return False
        status = _membership_status(membership).lower()
        return status not in {"deleted", "removed", "revoked", "inactive", "disabled"}
    except Exception:
        return False


def _membership_has_manage(membership: Any) -> bool:
    try:
        if not _membership_is_active(membership):
            return False

        role = _safe_str(getattr(membership, "role", ""), default="").lower()
        if role in {ROLE_OWNER, ROLE_ADMIN}:
            return True

        if _safe_bool(getattr(membership, "can_manage", False), default=False):
            return True

        permissions = _safe_dict(getattr(membership, "permissions", None))
        if _safe_bool(permissions.get("manage"), default=False):
            return True

        return False
    except Exception:
        return False


def _serialize_membership(membership: Any) -> Dict[str, Any]:
    if membership is None:
        return {}

    try:
        if hasattr(membership, "to_dict") and callable(membership.to_dict):
            return _safe_dict(membership.to_dict())
        if hasattr(membership, "serialize") and callable(membership.serialize):
            return _safe_dict(membership.serialize())
    except Exception:
        pass

    data: Dict[str, Any] = {}
    for key in [
        "id",
        "project_id",
        "user_id",
        "role",
        "status",
        "can_view",
        "can_edit",
        "can_manage",
        "can_delete",
        "can_transfer",
        "can_embed",
        "accepted_at",
        "revoked_at",
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


def _apply_role_to_membership(membership: Any, role: Any) -> None:
    normalized_role = normalize_invitation_role(role, allow_owner=True)
    flags = _permission_flags_for_role(normalized_role)

    try:
        membership.role = normalized_role
    except Exception:
        pass

    mapping = {
        "can_view": "view",
        "can_edit": "edit",
        "can_manage": "manage",
        "can_delete": "delete",
        "can_transfer": "transfer",
        "can_embed": "embed",
    }

    for attr, perm in mapping.items():
        try:
            if hasattr(membership, attr):
                setattr(membership, attr, bool(flags.get(perm, False)))
        except Exception:
            pass

    try:
        if hasattr(membership, "status"):
            membership.status = MEMBERSHIP_STATUS_ACTIVE
    except Exception:
        pass

    try:
        if hasattr(membership, "accepted_at") and getattr(membership, "accepted_at", None) is None:
            membership.accepted_at = utcnow()
    except Exception:
        pass


def _create_or_update_membership_from_invitation(
    invitation: ProjectInvitation,
    local_user_id: Any,
    actor_context: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, Optional[Any], str]:
    """
    Erstellt oder reaktiviert ProjectMembership.

    Wichtig:
    local_user_id muss bereits existieren. Diese Funktion erzeugt keinen AppUser.
    """
    if ProjectMembership is None:
        return False, None, "project_membership_model_unavailable"

    safe_project_id = _safe_int(invitation.project_id)
    safe_user_id = _safe_int(local_user_id)

    if not safe_project_id or not safe_user_id:
        return False, None, "local_user_link_required"

    existing = _find_membership(safe_project_id, safe_user_id)

    if existing is not None:
        _apply_role_to_membership(existing, invitation.role)
        return True, existing, "membership_updated"

    kwargs = {
        "project_id": safe_project_id,
        "user_id": safe_user_id,
        "role": normalize_invitation_role(invitation.role),
        "status": MEMBERSHIP_STATUS_ACTIVE,
    }

    try:
        actor_user_id = _actor_user_id(actor_context or {})
        if actor_user_id is not None:
            kwargs["invited_by_user_id"] = actor_user_id
    except Exception:
        pass

    try:
        membership = ProjectMembership(**kwargs)
    except Exception:
        try:
            membership = ProjectMembership()
            for key, value in kwargs.items():
                try:
                    setattr(membership, key, value)
                except Exception:
                    pass
        except Exception:
            return False, None, "membership_create_failed"

    _apply_role_to_membership(membership, invitation.role)
    _session_add(membership)

    return True, membership, "membership_created"


# ---------------------------------------------------------------------------
# Audit helpers
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

    safe_project_id = _project_id(project)
    safe_actor_user_id = _safe_int(actor_user_id)

    if not safe_project_id:
        return

    payload = _safe_dict(metadata)
    payload.setdefault("source", "project_invitation_service")

    kwargs = {
        "project_id": safe_project_id,
        "category": AUDIT_CATEGORY_ACCESS,
        "action": _safe_str(action, max_len=120),
        "actor_user_id": safe_actor_user_id,
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
# Invitation URL
# ---------------------------------------------------------------------------


def build_invitation_url(
    invitation: ProjectInvitation,
    plain_token: Optional[str] = None,
    include_token: bool = False,
) -> str:
    """
    Baut eine vorbereitete Einladungs-URL.

    Standardmäßig OHNE Token, damit keine geheimen Tokens versehentlich in
    dispatch_response_json, Logs oder UI landen.

    Der spätere echte Mail-Service kann include_token=True verwenden, wenn der
    Token kontrolliert und nicht persistiert weitergegeben wird.
    """
    public_base = _safe_str(
        _read_config("VECTOPLAN_APP_PUBLIC_URL", "")
        or _read_config("APP_PUBLIC_URL", ""),
        default="",
    ).rstrip("/")

    route = _safe_str(
        _read_config("PROJECT_INVITATION_PUBLIC_ROUTE", DEFAULT_INVITATION_ROUTE),
        default=DEFAULT_INVITATION_ROUTE,
    ).strip("/")

    public_id = _safe_str(getattr(invitation, "public_id", ""), max_len=100)

    if not public_id:
        return ""

    path = f"/{route}/{public_id}"

    if include_token and plain_token:
        path = path + "?token=" + _safe_str(plain_token)

    if public_base:
        return public_base + path

    return path


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------


class ProjectInvitationService:
    """
    Service-Objekt für Projekt-Einladungen.

    Die Klasse ist zustandslos. Sie kann direkt oder über Modul-Funktionen
    verwendet werden.
    """

    def status(self) -> Dict[str, Any]:
        auth_status: Dict[str, Any] = {}

        try:
            if get_auth_identity_status is not None:
                auth_status = _safe_dict(get_auth_identity_status())
        except Exception as exc:
            auth_status = {
                "ok": False,
                "code": "auth_identity_status_failed",
                "error": str(exc),
            }

        return {
            "ok": True,
            "service": "project_invitation_service",
            "project_model_available": Project is not None,
            "membership_model_available": ProjectMembership is not None,
            "audit_model_available": ProjectAuditEvent is not None,
            "app_user_model_available": AppUser is not None,
            "project_invitation_model_available": ProjectInvitation is not None,
            "auth_identity": auth_status,
            "invitable_roles": sorted(INVITABLE_PROJECT_ROLES),
        }

    def list_invitations(
        self,
        project_or_id: Any,
        actor_user_id: Any = None,
        include_terminal: bool = True,
        include_private: bool = False,
    ) -> ProjectInvitationServiceResult:
        project = resolve_project(project_or_id)
        if project is None:
            return _result(
                ok=False,
                code="project_not_found",
                message="Projekt nicht gefunden.",
                status_code=404,
            )

        actor_context = get_actor_context(actor_user_id)
        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        try:
            ProjectInvitation.expire_old_pending(_project_id(project))
            invitations = ProjectInvitation.list_for_project(
                _project_id(project),
                include_terminal=include_terminal,
                include_deleted=False,
            )

            return _result(
                ok=True,
                code="project_invitations_loaded",
                message="Einladungen wurden geladen.",
                project=project,
                invitations=invitations,
                access={"can_manage": True},
                data={
                    "include_terminal": bool(include_terminal),
                    "include_private": bool(include_private),
                    "total": len(invitations),
                },
            )
        except Exception as exc:
            _log_exception("list_invitations failed", project_id=_project_id(project))
            return _result(
                ok=False,
                code="project_invitations_load_failed",
                message="Einladungen konnten nicht geladen werden.",
                project=project,
                status_code=500,
                error=str(exc),
            )

    def invite_by_email(
        self,
        project_or_id: Any,
        email: Any,
        role: Any = DEFAULT_INVITATION_ROLE,
        actor_user_id: Any = None,
        message: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
        expires_in_days: int = DEFAULT_INVITATION_EXPIRY_DAYS,
        dispatch: bool = True,
        commit: bool = True,
        include_token_in_result: bool = False,
        include_token_in_dispatch_url: bool = False,
    ) -> ProjectInvitationServiceResult:
        """
        Erstellt eine pending ProjectInvitation für eine registrierte E-Mail.

        Wichtig:
        - Erzeugt keinen AppUser.
        - Lehnt nicht registrierte E-Mails ab.
        - Lehnt Demo-Modus ab.
        - Schreibt ProjectInvitation und optional Audit.
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
        actor_auth_user_id = _actor_auth_user_id(actor_context)

        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        normalized_email = normalize_email(email)
        if not is_valid_email(normalized_email):
            return _result(
                ok=False,
                code="invalid_email",
                message="Die E-Mail-Adresse ist ungültig.",
                project=project,
                status_code=400,
                data={"email": normalized_email},
            )

        normalized_role = normalize_invitation_role(role, allow_owner=False)
        if normalized_role == ROLE_OWNER:
            normalized_role = ROLE_ADMIN

        if normalized_role not in INVITABLE_PROJECT_ROLES:
            normalized_role = DEFAULT_INVITATION_ROLE

        if require_registered_email_identity is None:
            return _result(
                ok=False,
                code="auth_identity_client_unavailable",
                message="Der Auth-Identity-Client ist nicht verfügbar.",
                project=project,
                status_code=503,
            )

        try:
            identity = _safe_dict(require_registered_email_identity(normalized_email))
        except Exception as exc:
            _log_exception("auth identity lookup failed", email=normalized_email)
            identity = {
                "ok": False,
                "code": "auth_identity_lookup_failed",
                "message": "Die Registrierungsprüfung ist fehlgeschlagen.",
                "error": str(exc),
            }

        if not _safe_bool(identity.get("ok"), default=False) or not _safe_bool(identity.get("registered"), default=False):
            code = _safe_str(identity.get("code"), default="user_not_registered")
            message_text = _safe_str(
                identity.get("message"),
                default="Einladungen sind nur an bereits registrierte Accounts möglich.",
            )

            _write_audit_event(
                project,
                ACTION_INVITATION_FAILED,
                actor_user_id=actor_id,
                message="Project invitation rejected.",
                metadata={
                    "email": normalized_email,
                    "role": normalized_role,
                    "code": code,
                    "identity": identity,
                },
            )

            try:
                _commit_or_flush(commit=commit)
            except Exception:
                pass

            return _result(
                ok=False,
                code=code,
                message=message_text,
                project=project,
                identity=identity,
                status_code=404 if code == "user_not_registered" else 400,
            )

        existing_pending = ProjectInvitation.find_active_for_email(
            _project_id(project),
            normalized_email,
        )

        if existing_pending is not None:
            return _result(
                ok=True,
                code="invitation_already_pending",
                message="Für diese E-Mail-Adresse existiert bereits eine aktive Einladung.",
                project=project,
                invitation=existing_pending,
                identity=identity,
                status_code=200,
            )

        linked_user = find_linked_app_user(
            auth_user_id=identity.get("auth_user_id"),
            email=normalized_email,
        )

        if linked_user is not None:
            linked_user_id = _safe_int(getattr(linked_user, "id", None))
            existing_membership = _find_membership(_project_id(project), linked_user_id)
            if existing_membership is not None and _membership_is_active(existing_membership):
                return _result(
                    ok=True,
                    code="user_already_project_member",
                    message="Dieser registrierte User ist bereits Projektmitglied.",
                    project=project,
                    membership=existing_membership,
                    identity=identity,
                    status_code=200,
                )

        invitation: Optional[ProjectInvitation] = None
        plain_token: Optional[str] = None

        try:
            invitation, plain_token = ProjectInvitation.create_pending(
                project_id=_project_id(project),
                project_public_id=_project_public_id(project),
                email=normalized_email,
                role=normalized_role,
                invited_by_user_id=actor_id,
                invited_by_auth_user_id=actor_auth_user_id,
                identity=identity,
                message=message,
                metadata={
                    **_safe_dict(metadata),
                    "created_by_service": "project_invitation_service",
                },
                expires_in_days=expires_in_days,
                generate_token=True,
            )

            if linked_user is not None:
                invitation.target_user_id = _safe_int(getattr(linked_user, "id", None))

            invitation_url = build_invitation_url(
                invitation,
                plain_token=plain_token,
                include_token=include_token_in_dispatch_url,
            )
            if invitation_url:
                invitation.invitation_url = build_invitation_url(
                    invitation,
                    plain_token=None,
                    include_token=False,
                )

            _session_add(invitation)

            dispatch_result: Dict[str, Any] = {}

            if dispatch:
                if dispatch_project_invitation_identity is None:
                    dispatch_result = {
                        "ok": False,
                        "code": "auth_invitation_dispatch_client_unavailable",
                        "message": "Der externe Einladungsversand ist nicht verfügbar.",
                    }
                else:
                    dispatch_result = _safe_dict(
                        dispatch_project_invitation_identity(
                            email=normalized_email,
                            project_public_id=_project_public_id(project),
                            role=normalized_role,
                            invited_by_auth_user_id=actor_auth_user_id,
                            invitation_id=invitation.public_id,
                            invitation_url=invitation_url,
                            message=_safe_str(message, default=None),  # type: ignore[arg-type]
                            metadata={
                                "project_id": _project_id(project),
                                "project_public_id": _project_public_id(project),
                                "invitation_public_id": invitation.public_id,
                            },
                            require_registered=False,
                        )
                    )

                invitation.apply_dispatch_result(dispatch_result)

            else:
                dispatch_result = {
                    "ok": True,
                    "code": "invitation_dispatch_skipped",
                    "message": "Einladungsversand wurde übersprungen.",
                    "external_sent": False,
                    "placeholder": False,
                }

            if dispatch and not _safe_bool(dispatch_result.get("ok"), default=False):
                invitation.mark_failed(dispatch_result.get("message") or dispatch_result.get("error"))
                audit_action = ACTION_INVITATION_FAILED
                audit_message = "Project invitation created but dispatch failed."
                result_ok = False
                result_code = _safe_str(dispatch_result.get("code"), default="invitation_dispatch_failed")
                result_status = 502
                result_message = _safe_str(
                    dispatch_result.get("message"),
                    default="Die Einladung konnte nicht versendet werden.",
                )
            else:
                audit_action = ACTION_INVITATION_CREATED
                audit_message = "Project invitation created."
                result_ok = True
                result_code = "project_invitation_created"
                result_status = 201
                result_message = "Einladung wurde erstellt."

            _write_audit_event(
                project,
                audit_action,
                actor_user_id=actor_id,
                message=audit_message,
                metadata={
                    "invitation_id": invitation.public_id,
                    "email": normalized_email,
                    "role": normalized_role,
                    "identity": identity,
                    "dispatch": dispatch_result,
                },
            )

            _commit_or_flush(commit=commit)

            data: Dict[str, Any] = {
                "email": normalized_email,
                "role": normalized_role,
                "dispatch_requested": bool(dispatch),
                "linked_app_user_found": linked_user is not None,
            }

            if include_token_in_result and plain_token:
                data["invitation_token"] = plain_token
                data["invitation_url_with_token"] = build_invitation_url(
                    invitation,
                    plain_token=plain_token,
                    include_token=True,
                )

            return _result(
                ok=result_ok,
                code=result_code,
                message=result_message,
                project=project,
                invitation=invitation,
                identity=identity,
                dispatch=dispatch_result,
                status_code=result_status,
                data=data,
            )

        except Exception as exc:
            _rollback_safely()
            _log_exception(
                "invite_by_email failed",
                project_id=_project_id(project),
                email=normalized_email,
                role=normalized_role,
            )
            return _result(
                ok=False,
                code="project_invitation_create_failed",
                message="Die Einladung konnte nicht erstellt werden.",
                project=project,
                invitation=invitation,
                identity=identity,
                status_code=500,
                error=str(exc),
            )

    def revoke_invitation(
        self,
        project_or_id: Any,
        invitation_id: Any,
        actor_user_id: Any = None,
        reason: Any = None,
        commit: bool = True,
    ) -> ProjectInvitationServiceResult:
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
        actor_auth_user_id = _actor_auth_user_id(actor_context)

        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        invitation = ProjectInvitation.find_by_public_id(invitation_id)
        if invitation is None:
            return _result(
                ok=False,
                code="project_invitation_not_found",
                message="Einladung nicht gefunden.",
                project=project,
                status_code=404,
            )

        if _safe_int(invitation.project_id) != _project_id(project):
            return _result(
                ok=False,
                code="project_invitation_project_mismatch",
                message="Diese Einladung gehört nicht zu diesem Projekt.",
                project=project,
                invitation=invitation,
                status_code=409,
            )

        if not invitation.can_revoke():
            return _result(
                ok=False,
                code="project_invitation_not_revokable",
                message="Diese Einladung kann nicht mehr widerrufen werden.",
                project=project,
                invitation=invitation,
                status_code=409,
            )

        try:
            invitation.mark_revoked(
                revoked_by_user_id=actor_id,
                revoked_by_auth_user_id=actor_auth_user_id,
                reason=reason,
            )

            _write_audit_event(
                project,
                ACTION_INVITATION_REVOKED,
                actor_user_id=actor_id,
                message="Project invitation revoked.",
                metadata={
                    "invitation_id": invitation.public_id,
                    "email": invitation.email_normalized,
                    "role": invitation.role,
                    "reason": _safe_str(reason),
                },
            )

            _commit_or_flush(commit=commit)

            return _result(
                ok=True,
                code="project_invitation_revoked",
                message="Einladung wurde widerrufen.",
                project=project,
                invitation=invitation,
                status_code=200,
            )

        except Exception as exc:
            _rollback_safely()
            _log_exception(
                "revoke_invitation failed",
                project_id=_project_id(project),
                invitation_id=_safe_str(invitation_id),
            )
            return _result(
                ok=False,
                code="project_invitation_revoke_failed",
                message="Einladung konnte nicht widerrufen werden.",
                project=project,
                invitation=invitation,
                status_code=500,
                error=str(exc),
            )

    def reject_invitation(
        self,
        invitation_id: Any,
        auth_user_id: Any = None,
        email: Any = None,
        reason: Any = None,
        commit: bool = True,
    ) -> ProjectInvitationServiceResult:
        invitation = ProjectInvitation.find_by_public_id(invitation_id)
        if invitation is None:
            return _result(
                ok=False,
                code="project_invitation_not_found",
                message="Einladung nicht gefunden.",
                status_code=404,
            )

        project = resolve_project(invitation.project_id)

        if not invitation.can_accept(auth_user_id=auth_user_id, email=email):
            return _result(
                ok=False,
                code="project_invitation_not_rejectable",
                message="Diese Einladung kann durch diese Identität nicht abgelehnt werden.",
                project=project,
                invitation=invitation,
                status_code=409,
            )

        try:
            invitation.mark_rejected(reason=reason)

            _write_audit_event(
                project,
                ACTION_INVITATION_REJECTED,
                actor_user_id=None,
                message="Project invitation rejected.",
                metadata={
                    "invitation_id": invitation.public_id,
                    "email": invitation.email_normalized,
                    "auth_user_id": _safe_str(auth_user_id),
                    "reason": _safe_str(reason),
                },
            )

            _commit_or_flush(commit=commit)

            return _result(
                ok=True,
                code="project_invitation_rejected",
                message="Einladung wurde abgelehnt.",
                project=project,
                invitation=invitation,
                status_code=200,
            )

        except Exception as exc:
            _rollback_safely()
            return _result(
                ok=False,
                code="project_invitation_reject_failed",
                message="Einladung konnte nicht abgelehnt werden.",
                project=project,
                invitation=invitation,
                status_code=500,
                error=str(exc),
            )

    def accept_invitation(
        self,
        invitation_id: Any,
        auth_user_id: Any = None,
        email: Any = None,
        local_user_id: Any = None,
        plain_token: Any = None,
        actor_user_id: Any = None,
        commit: bool = True,
    ) -> ProjectInvitationServiceResult:
        """
        Nimmt eine Einladung an.

        Wichtig:
        - Erzeugt keinen AppUser.
        - local_user_id muss bereits durch den Login-/Auth-Sync existieren
          oder über auth_user_id/email auffindbar sein.
        - Erst dann wird ProjectMembership erzeugt/aktiviert.
        """
        invitation = ProjectInvitation.find_by_public_id(invitation_id)
        if invitation is None:
            return _result(
                ok=False,
                code="project_invitation_not_found",
                message="Einladung nicht gefunden.",
                status_code=404,
            )

        project = resolve_project(invitation.project_id)

        try:
            if invitation.ensure_not_expired():
                _write_audit_event(
                    project,
                    ACTION_INVITATION_EXPIRED,
                    actor_user_id=None,
                    message="Project invitation expired.",
                    metadata={
                        "invitation_id": invitation.public_id,
                        "email": invitation.email_normalized,
                    },
                )
                _commit_or_flush(commit=commit)

                return _result(
                    ok=False,
                    code="project_invitation_expired",
                    message="Diese Einladung ist abgelaufen.",
                    project=project,
                    invitation=invitation,
                    status_code=410,
                )
        except Exception:
            pass

        if plain_token:
            if not invitation.verify_plain_token(plain_token):
                return _result(
                    ok=False,
                    code="invalid_invitation_token",
                    message="Der Einladungstoken ist ungültig.",
                    project=project,
                    invitation=invitation,
                    status_code=403,
                )

        if not invitation.can_accept(auth_user_id=auth_user_id, email=email):
            return _result(
                ok=False,
                code="project_invitation_identity_mismatch",
                message="Diese Einladung gehört nicht zur aktuellen Auth-Identität.",
                project=project,
                invitation=invitation,
                status_code=403,
            )

        resolved_local_user_id = _safe_int(local_user_id)

        if not resolved_local_user_id:
            linked_user = find_linked_app_user(
                auth_user_id=auth_user_id or invitation.auth_user_id,
                email=email or invitation.email_normalized,
            )
            if linked_user is not None:
                resolved_local_user_id = _safe_int(getattr(linked_user, "id", None))

        if not resolved_local_user_id:
            return _result(
                ok=False,
                code="local_user_link_required",
                message=(
                    "Die Einladung ist gültig, aber es existiert noch keine lokale "
                    "AppUser-Verknüpfung. Der Login-/Registrierungsdienst muss den "
                    "User zuerst mit vectoplan-app synchronisieren."
                ),
                project=project,
                invitation=invitation,
                status_code=409,
                data={
                    "auth_user_id": auth_user_id or invitation.auth_user_id,
                    "email": email or invitation.email_normalized,
                    "no_user_created": True,
                },
            )

        actor_context = get_actor_context(actor_user_id or resolved_local_user_id)

        try:
            ok, membership, membership_code = _create_or_update_membership_from_invitation(
                invitation,
                local_user_id=resolved_local_user_id,
                actor_context=actor_context,
            )

            if not ok or membership is None:
                return _result(
                    ok=False,
                    code=membership_code,
                    message="Projektmitgliedschaft konnte nicht erstellt werden.",
                    project=project,
                    invitation=invitation,
                    status_code=500,
                )

            invitation.mark_accepted(
                accepted_by_user_id=resolved_local_user_id,
                accepted_by_auth_user_id=auth_user_id or invitation.auth_user_id,
                membership_id=getattr(membership, "id", None),
            )

            _write_audit_event(
                project,
                ACTION_INVITATION_ACCEPTED,
                actor_user_id=resolved_local_user_id,
                message="Project invitation accepted.",
                metadata={
                    "invitation_id": invitation.public_id,
                    "email": invitation.email_normalized,
                    "role": invitation.role,
                    "membership_code": membership_code,
                    "membership_id": getattr(membership, "id", None),
                },
            )

            _commit_or_flush(commit=commit)

            return _result(
                ok=True,
                code="project_invitation_accepted",
                message="Einladung wurde angenommen.",
                project=project,
                invitation=invitation,
                membership=membership,
                status_code=200,
            )

        except Exception as exc:
            _rollback_safely()
            _log_exception(
                "accept_invitation failed",
                invitation_id=_safe_str(invitation_id),
                auth_user_id=_safe_str(auth_user_id),
                local_user_id=resolved_local_user_id,
            )
            return _result(
                ok=False,
                code="project_invitation_accept_failed",
                message="Einladung konnte nicht angenommen werden.",
                project=project,
                invitation=invitation,
                status_code=500,
                error=str(exc),
            )

    def expire_pending(
        self,
        project_or_id: Any = None,
        commit: bool = True,
    ) -> ProjectInvitationServiceResult:
        project = resolve_project(project_or_id) if project_or_id is not None else None

        try:
            changed = ProjectInvitation.expire_old_pending(
                _project_id(project) if project is not None else None
            )

            if changed:
                if project is not None:
                    _write_audit_event(
                        project,
                        ACTION_INVITATION_EXPIRED,
                        actor_user_id=None,
                        message="Expired project invitations marked.",
                        metadata={"changed": changed},
                    )

                _commit_or_flush(commit=commit)

            return _result(
                ok=True,
                code="project_invitations_expired",
                message="Abgelaufene Einladungen wurden aktualisiert.",
                project=project,
                status_code=200,
                data={"changed": changed},
            )
        except Exception as exc:
            _rollback_safely()
            return _result(
                ok=False,
                code="project_invitations_expire_failed",
                message="Abgelaufene Einladungen konnten nicht aktualisiert werden.",
                project=project,
                status_code=500,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Singleton / module-level API
# ---------------------------------------------------------------------------

_SERVICE_SINGLETON: Optional[ProjectInvitationService] = None


def get_project_invitation_service(refresh: bool = False) -> ProjectInvitationService:
    global _SERVICE_SINGLETON

    try:
        if refresh or _SERVICE_SINGLETON is None:
            _SERVICE_SINGLETON = ProjectInvitationService()
        return _SERVICE_SINGLETON
    except Exception:
        return ProjectInvitationService()


def get_project_invitation_service_status() -> Dict[str, Any]:
    try:
        return get_project_invitation_service().status()
    except Exception as exc:
        return {
            "ok": False,
            "code": "project_invitation_service_status_failed",
            "error": str(exc),
        }


def list_project_invitations(
    project_or_id: Any,
    actor_user_id: Any = None,
    include_terminal: bool = True,
    include_private: bool = False,
) -> Dict[str, Any]:
    result = get_project_invitation_service().list_invitations(
        project_or_id=project_or_id,
        actor_user_id=actor_user_id,
        include_terminal=include_terminal,
        include_private=include_private,
    )
    return result.to_dict(include_private=include_private, include_raw=include_private)


def invite_registered_email_to_project(
    project_or_id: Any,
    email: Any,
    role: Any = DEFAULT_INVITATION_ROLE,
    actor_user_id: Any = None,
    message: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
    expires_in_days: int = DEFAULT_INVITATION_EXPIRY_DAYS,
    dispatch: bool = True,
    commit: bool = True,
    include_token_in_result: bool = False,
) -> Dict[str, Any]:
    result = get_project_invitation_service().invite_by_email(
        project_or_id=project_or_id,
        email=email,
        role=role,
        actor_user_id=actor_user_id,
        message=message,
        metadata=metadata,
        expires_in_days=expires_in_days,
        dispatch=dispatch,
        commit=commit,
        include_token_in_result=include_token_in_result,
    )
    return result.to_dict(
        include_private=include_token_in_result,
        include_raw=include_token_in_result,
    )


def revoke_project_invitation(
    project_or_id: Any,
    invitation_id: Any,
    actor_user_id: Any = None,
    reason: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_invitation_service().revoke_invitation(
        project_or_id=project_or_id,
        invitation_id=invitation_id,
        actor_user_id=actor_user_id,
        reason=reason,
        commit=commit,
    )
    return result.to_dict(include_private=True, include_raw=False)


def reject_project_invitation(
    invitation_id: Any,
    auth_user_id: Any = None,
    email: Any = None,
    reason: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_invitation_service().reject_invitation(
        invitation_id=invitation_id,
        auth_user_id=auth_user_id,
        email=email,
        reason=reason,
        commit=commit,
    )
    return result.to_dict(include_private=False, include_raw=False)


def accept_project_invitation(
    invitation_id: Any,
    auth_user_id: Any = None,
    email: Any = None,
    local_user_id: Any = None,
    plain_token: Any = None,
    actor_user_id: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_invitation_service().accept_invitation(
        invitation_id=invitation_id,
        auth_user_id=auth_user_id,
        email=email,
        local_user_id=local_user_id,
        plain_token=plain_token,
        actor_user_id=actor_user_id,
        commit=commit,
    )
    return result.to_dict(include_private=True, include_raw=False)


def expire_project_invitations(
    project_or_id: Any = None,
    commit: bool = True,
) -> Dict[str, Any]:
    result = get_project_invitation_service().expire_pending(
        project_or_id=project_or_id,
        commit=commit,
    )
    return result.to_dict(include_private=True, include_raw=False)


__all__ = [
    "ACTION_INVITATION_ACCEPTED",
    "ACTION_INVITATION_CREATED",
    "ACTION_INVITATION_DISPATCHED",
    "ACTION_INVITATION_EXPIRED",
    "ACTION_INVITATION_FAILED",
    "ACTION_INVITATION_REJECTED",
    "ACTION_INVITATION_REVOKED",
    "ProjectInvitationService",
    "ProjectInvitationServiceResult",
    "accept_project_invitation",
    "build_invitation_url",
    "expire_project_invitations",
    "find_linked_app_user",
    "get_actor_context",
    "get_project_invitation_service",
    "get_project_invitation_service_status",
    "invite_registered_email_to_project",
    "list_project_invitations",
    "reject_project_invitation",
    "resolve_project",
    "revoke_project_invitation",
]