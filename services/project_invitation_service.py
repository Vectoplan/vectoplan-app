# services/vectoplan-app/services/project_invitation_service.py
from __future__ import annotations

"""
VECTOPLAN project invitation service.

Zweck:
- Fachliche Service-Schicht für Projekt-Einladungen.
- Prüft Projektberechtigungen.
- Prüft E-Mail-Adressen gegen vectoplan-auth.
- Erzeugt keine Auth-Benutzerkonten; verifizierte Auth-Identitäten erhalten nur einen lokalen AppUser-Link.
- Erzeugt keinen Default-User.
- Nutzt AppUser ausschließlich als lokalen Link und Projekt-FK für verifizierte Auth-Identitäten.
- Speichert ProjectInvitation-Datensätze.
- Stößt optional Einladungsversand über vectoplan-auth an.
- Nimmt Einladungen nur an, wenn der eingeloggte Auth-User bereits einen lokalen
  AppUser-Link in vectoplan-app besitzt.
- Schreibt, wenn verfügbar, Audit-Events.

Architekturregel:
- vectoplan-auth verwaltet Registrierung, Login, Account, Plan, Entitlements,
  Blocked/Banned und User-Identität.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Veröffentlichungen und
  Projektfrontend.
- ProjectInvitation ist App-seitiger Projektzugang, nicht Auth-Wahrheit.
- Demo-Guests dürfen keine Einladungen oder Rollenänderungen ausführen.
- Blocked/Banned/Auth-unavailable erhält keinen Fallback.
- Auth-unavailable ist 503, nicht Ban/Forbidden.
"""

import datetime as _dt
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Tuple


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
        except Exception:
            db = None  # type: ignore


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
        try:
            from models import ProjectMembership  # type: ignore
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
        try:
            from models import AppUser  # type: ignore
        except Exception:
            AppUser = None  # type: ignore


try:
    from models.project_invitations import (  # type: ignore
        DEFAULT_INVITATION_EXPIRY_DAYS,
        DEFAULT_INVITATION_ROLE,
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
    except Exception:
        DEFAULT_INVITATION_EXPIRY_DAYS = 14
        ROLE_OWNER = "owner"
        ROLE_ADMIN = "admin"
        ROLE_EDITOR = "editor"
        ROLE_VIEWER = "viewer"
        DEFAULT_INVITATION_ROLE = ROLE_VIEWER
        INVITABLE_PROJECT_ROLES = {ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN}
        STATUS_PENDING = "pending"
        STATUS_ACCEPTED = "accepted"
        STATUS_REJECTED = "rejected"
        STATUS_REVOKED = "revoked"
        STATUS_EXPIRED = "expired"
        STATUS_FAILED = "failed"
        ProjectInvitation = None  # type: ignore

        def normalize_email(value: Any) -> str:  # type: ignore
            try:
                return str(value or "").strip().lower()
            except Exception:
                return ""

        def is_valid_email(value: Any) -> bool:  # type: ignore
            try:
                text = normalize_email(value)
                return bool(text and len(text) <= 320 and "@" in text and "." in text.rsplit("@", 1)[-1])
            except Exception:
                return False

        def normalize_invitation_role(value: Any, allow_owner: bool = False) -> str:  # type: ignore
            try:
                role = str(value or DEFAULT_INVITATION_ROLE).strip().lower()
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
                }
                role = aliases.get(role, role)
                if role == ROLE_OWNER and not allow_owner:
                    return ROLE_ADMIN
                if role in {ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}:
                    return role
                return DEFAULT_INVITATION_ROLE
            except Exception:
                return DEFAULT_INVITATION_ROLE

        def hash_invitation_token(token: Any) -> str:  # type: ignore
            return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

        def serialize_project_invitation(invitation: Any, include_private: bool = False, include_auth: bool = False, include_raw: bool = False) -> Dict[str, Any]:  # type: ignore
            if invitation is None:
                return {}
            if hasattr(invitation, "to_dict") and callable(invitation.to_dict):
                try:
                    return dict(invitation.to_dict(include_private=include_private, include_auth=include_auth, include_raw=include_raw))
                except TypeError:
                    try:
                        return dict(invitation.to_dict())
                    except Exception:
                        pass
            data: Dict[str, Any] = {}
            for key in (
                "id",
                "public_id",
                "project_id",
                "project_public_id",
                "email",
                "email_normalized",
                "role",
                "status",
                "invitation_url",
                "expires_at",
                "created_at",
                "updated_at",
            ):
                try:
                    value = getattr(invitation, key, None)
                    if hasattr(value, "isoformat"):
                        value = value.isoformat()
                    data[key] = value
                except Exception:
                    pass
            return data

        def serialize_project_invitations(invitations: Any, include_private: bool = False, include_auth: bool = False, include_raw: bool = False) -> list:  # type: ignore
            return [
                serialize_project_invitation(item, include_private=include_private, include_auth=include_auth, include_raw=include_raw)
                for item in list(invitations or [])
            ]

        def invitation_status_counts(invitations: Any) -> Dict[str, int]:  # type: ignore
            result: Dict[str, int] = {}
            for item in list(invitations or []):
                try:
                    status = str(getattr(item, "status", "") or "unknown").lower()
                    result[status] = result.get(status, 0) + 1
                except Exception:
                    continue
            return result


try:
    from services.auth_identity_client import (  # type: ignore
        dispatch_project_invitation_identity as legacy_dispatch_project_invitation_identity,
        get_auth_identity_status as legacy_get_auth_identity_status,
        require_registered_email_identity as legacy_require_registered_email_identity,
    )
except Exception:  # pragma: no cover
    try:
        from .auth_identity_client import (  # type: ignore
            dispatch_project_invitation_identity as legacy_dispatch_project_invitation_identity,
            get_auth_identity_status as legacy_get_auth_identity_status,
            require_registered_email_identity as legacy_require_registered_email_identity,
        )
    except Exception:
        legacy_dispatch_project_invitation_identity = None  # type: ignore
        legacy_get_auth_identity_status = None  # type: ignore
        legacy_require_registered_email_identity = None  # type: ignore


try:
    from services.auth_context_client import get_auth_context_client  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .auth_context_client import get_auth_context_client  # type: ignore
    except Exception:
        get_auth_context_client = None  # type: ignore


try:
    from services.project_permissions import (  # type: ignore
        PERMISSION_MANAGE_TEAM,
        PermissionDenied,
        can_manage_project,
        can_manage_project_team,
        get_project_permission_result,
        require_project_permission,
    )
except Exception:  # pragma: no cover
    try:
        from .project_permissions import (  # type: ignore
            PERMISSION_MANAGE_TEAM,
            PermissionDenied,
            can_manage_project,
            can_manage_project_team,
            get_project_permission_result,
            require_project_permission,
        )
    except Exception:
        PERMISSION_MANAGE_TEAM = "manage_team"  # type: ignore
        can_manage_project = None  # type: ignore
        can_manage_project_team = None  # type: ignore
        get_project_permission_result = None  # type: ignore
        require_project_permission = None  # type: ignore

        class PermissionDenied(RuntimeError):  # type: ignore
            def __init__(
                self,
                message: str = "permission denied",
                *,
                code: str = "project_permission_denied",
                status_code: int = 403,
                permission: str = "manage",
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
                    "status_code": self.status_code,
                    "permission": self.permission,
                    "project_id": self.project_id,
                    "user_id": self.user_id,
                }


try:
    from services.current_user import get_current_user_context  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .current_user import get_current_user_context  # type: ignore
    except Exception:
        get_current_user_context = None  # type: ignore


try:
    from services.project_chunk_access_sync_service import (  # type: ignore
        ProjectChunkAccessSyncError,
        serialize_project_chunk_access_sync_status,
        sync_project_chunk_access,
    )
except Exception:  # pragma: no cover
    try:
        from .project_chunk_access_sync_service import (  # type: ignore
            ProjectChunkAccessSyncError,
            serialize_project_chunk_access_sync_status,
            sync_project_chunk_access,
        )
    except Exception:
        ProjectChunkAccessSyncError = RuntimeError  # type: ignore
        serialize_project_chunk_access_sync_status = None  # type: ignore
        sync_project_chunk_access = None  # type: ignore


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
ACTION_INVITATION_ACCESS_SYNCED = "invitation_access_synced"
ACTION_INVITATION_ACCESS_SYNC_FAILED = "invitation_access_sync_failed"

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

DEFAULT_AUTH_IDENTITY_LOOKUP_PATH = "/auth/identity/lookup"
DEFAULT_AUTH_INVITATION_DISPATCH_PATH = "/auth/project-invitations/dispatch"

AUTH_UNAVAILABLE_CODES = {
    "auth_unavailable",
    "auth_service_unavailable",
    "current_user_unavailable",
    "current_user_service_unavailable",
    "current_user_context_unavailable",
    "auth_context_client_unavailable",
    "service_unavailable",
    "dependency_unavailable",
    "upstream_unavailable",
    "storage_unavailable",
    "dns_failed",
    "connection_refused",
    "timeout",
    "http_5xx",
    "http_error",
    "access_denied",
    "invalid_payload",
    "not_configured",
    "request_failed",
    "requests_unavailable",
}

_SENSITIVE_KEY_PARTS = frozenset({
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "session",
})

_IDENTITY_CACHE_LOCK = threading.RLock()
_IDENTITY_CACHE: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_KEYED_LOCKS_GUARD = threading.RLock()
_KEYED_LOCKS: "OrderedDict[str, threading.RLock]" = OrderedDict()


USER_BLOCKED_CODES = {
    "blocked",
    "banned",
    "user_blocked",
    "user_banned",
    "account_blocked",
    "account_banned",
    "subscription_blocked",
    "plan_blocked",
    "security_blocked",
    "disabled",
    "inactive",
    "suspended",
    "deleted",
    "locked",
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
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
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
        if value is None or value == "" or isinstance(value, bool):
            return default
        parsed = int(value)
        return parsed if parsed > 0 else default
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
        if text in {"1", "true", "yes", "y", "on", "enabled", "active", "ok", "ja"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "inactive", "none", "null", "nein", ""}:
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
            return list(value)
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
        return json.loads(json.dumps(_safe_dict(value), ensure_ascii=False, default=str))
    except Exception:
        return _safe_dict(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _redact_sensitive(value: Any, *, preserve_invitation_token: bool = False) -> Any:
    """Return a JSON-safe copy without secrets suitable for logs/audit/results."""
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _safe_str(raw_key, "", 200)
            lowered = key.lower().replace("-", "_")
            sensitive = any(part in lowered for part in _SENSITIVE_KEY_PARTS)
            if preserve_invitation_token and lowered in {
                "invitation_token",
                "invitationtoken",
                "invitation_url_with_token",
                "invitationurlwithtoken",
            }:
                sensitive = False
            result[key] = "[REDACTED]" if sensitive else _redact_sensitive(
                raw_value, preserve_invitation_token=preserve_invitation_token
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _redact_sensitive(item, preserve_invitation_token=preserve_invitation_token)
            for item in value
        ]
    if isinstance(value, (_dt.datetime, _dt.date)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


def _cache_seconds() -> float:
    return max(0.0, min(60.0, _safe_float(
        _read_config("VECTOPLAN_PROJECT_INVITATION_IDENTITY_CACHE_SECONDS", 5.0), 5.0
    )))


def _cache_max_entries() -> int:
    return max(16, min(4096, int(_safe_int(
        _read_config("VECTOPLAN_PROJECT_INVITATION_IDENTITY_CACHE_MAX_ENTRIES", 512), 512
    ) or 512)))


def clear_project_invitation_identity_cache(email: Any = None) -> None:
    key = normalize_email(email) if email is not None else ""
    with _IDENTITY_CACHE_LOCK:
        if key:
            _IDENTITY_CACHE.pop(key, None)
        else:
            _IDENTITY_CACHE.clear()


def _identity_cache_get(email: str) -> Optional[Dict[str, Any]]:
    ttl = _cache_seconds()
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _IDENTITY_CACHE_LOCK:
        entry = _IDENTITY_CACHE.get(email)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= now:
            _IDENTITY_CACHE.pop(email, None)
            return None
        _IDENTITY_CACHE.move_to_end(email)
        return _json_clone(payload)


def _identity_cache_set(email: str, payload: Mapping[str, Any]) -> None:
    ttl = _cache_seconds()
    clean = _safe_dict(payload)
    if (
        ttl <= 0
        or not _safe_bool(clean.get("ok"), False)
        or not _safe_bool(clean.get("registered"), False)
        or not _safe_str(clean.get("auth_user_id"), "", 160)
    ):
        return
    safe_payload = _safe_dict(_redact_sensitive(clean))
    with _IDENTITY_CACHE_LOCK:
        _IDENTITY_CACHE[email] = (time.monotonic() + ttl, safe_payload)
        _IDENTITY_CACHE.move_to_end(email)
        while len(_IDENTITY_CACHE) > _cache_max_entries():
            _IDENTITY_CACHE.popitem(last=False)


def _lock_timeout_seconds() -> float:
    return max(0.1, min(30.0, _safe_float(
        _read_config("VECTOPLAN_PROJECT_INVITATION_LOCK_TIMEOUT_SECONDS", 5.0), 5.0
    )))


@contextmanager
def _keyed_lock(key: str) -> Iterator[None]:
    clean_key = _safe_str(key, "invitation", 500) or "invitation"
    with _KEYED_LOCKS_GUARD:
        lock = _KEYED_LOCKS.get(clean_key)
        if lock is None:
            lock = threading.RLock()
            _KEYED_LOCKS[clean_key] = lock
        _KEYED_LOCKS.move_to_end(clean_key)
        while len(_KEYED_LOCKS) > 1024:
            _KEYED_LOCKS.popitem(last=False)
    acquired = lock.acquire(timeout=_lock_timeout_seconds())
    if not acquired:
        raise TimeoutError("project_invitation_lock_timeout")
    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _request_id(prefix: str = "inv") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _commit_or_flush(commit: bool = True) -> None:
    if db is None:
        raise RuntimeError("database_unavailable")
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
        if db is not None:
            db.session.rollback()
    except Exception:
        pass


def _session_add(obj: Any) -> None:
    try:
        if db is not None:
            db.session.add(obj)
    except Exception:
        pass


def _project_is_demo(project: Any) -> bool:
    try:
        if project is None:
            return False
        if _safe_bool(_getattr_any(project, ("is_demo", "isDemo", "demo", "demo_mode", "demoMode"), False), False):
            return True
        if _safe_str(_getattr_any(project, ("project_scope", "projectScope", "scope"), ""), default="", max_len=40).lower() == "demo":
            return True
        metadata = _safe_dict(_getattr_any(project, ("metadata_json", "metadataJson", "metadata", "settings"), {}))
        demo_meta = _safe_dict(metadata.get("vectoplan_demo") or metadata.get("demo") or metadata.get("demo_project"))
        return _safe_bool(demo_meta.get("enabled") or demo_meta.get("is_demo") or demo_meta.get("isDemo"), False)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auth client adapters
# ---------------------------------------------------------------------------

def _auth_client() -> Any:
    try:
        if get_auth_context_client is None:
            return None
        return get_auth_context_client()
    except Exception:
        return None


def _auth_client_post(path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    client = _auth_client()
    if client is None:
        return {
            "ok": False,
            "registered": False,
            "code": "auth_context_client_unavailable",
            "message": "vectoplan-auth client unavailable.",
            "auth_unavailable": True,
            "status_code": 503,
        }

    clean_path = _safe_str(path, default="", max_len=500)
    clean_payload = _safe_dict(payload)

    for kwargs in (
        {"json": clean_payload},
        {"body": clean_payload},
        {"payload": clean_payload},
        {"data": clean_payload},
    ):
        try:
            response = client.post(clean_path, **kwargs)
            data = _safe_dict(response)
            if data:
                return data
            return {"ok": True, "data": response}
        except TypeError:
            continue
        except Exception as exc:
            return {
                "ok": False,
                "code": "auth_client_post_failed",
                "message": str(exc),
                "error": str(exc),
                "auth_unavailable": True,
                "status_code": 503,
            }

    try:
        response = client.post(clean_path, clean_payload)
        data = _safe_dict(response)
        if data:
            return data
        return {"ok": True, "data": response}
    except Exception as exc:
        return {
            "ok": False,
            "code": "auth_client_post_failed",
            "message": str(exc),
            "error": str(exc),
            "auth_unavailable": True,
            "status_code": 503,
        }


def _auth_client_get_status() -> Dict[str, Any]:
    client = _auth_client()
    if client is None:
        return {
            "ok": False,
            "code": "auth_context_client_unavailable",
            "message": "vectoplan-auth client unavailable.",
            "auth_unavailable": True,
            "status_code": 503,
        }

    try:
        if hasattr(client, "ready"):
            ready = client.ready()
            ready_payload = _safe_dict(ready)
            if ready_payload:
                ready_payload.setdefault("ok", True)
                return ready_payload
            return {"ok": bool(ready), "ready": bool(ready)}
    except Exception as exc:
        return {
            "ok": False,
            "code": "auth_ready_check_failed",
            "error": str(exc),
            "auth_unavailable": True,
            "status_code": 503,
        }

    return {
        "ok": True,
        "code": "auth_client_available",
    }


def get_auth_identity_status() -> Dict[str, Any]:
    if legacy_get_auth_identity_status is not None:
        try:
            return _safe_dict(legacy_get_auth_identity_status())
        except Exception:
            pass

    return {
        "ok": True,
        "source": "vectoplan-auth",
        "client": _auth_client_get_status(),
        "identity_lookup_path": _safe_str(
            _read_config("VECTOPLAN_AUTH_IDENTITY_LOOKUP_PATH", DEFAULT_AUTH_IDENTITY_LOOKUP_PATH),
            default=DEFAULT_AUTH_IDENTITY_LOOKUP_PATH,
            max_len=500,
        ),
        "invitation_dispatch_path": _safe_str(
            _read_config("VECTOPLAN_AUTH_INVITATION_DISPATCH_PATH", DEFAULT_AUTH_INVITATION_DISPATCH_PATH),
            default=DEFAULT_AUTH_INVITATION_DISPATCH_PATH,
            max_len=500,
        ),
    }


def _identity_failure_status(payload: Mapping[str, Any]) -> int:
    code = _safe_str(payload.get("code"), "", 120)
    if _safe_bool(payload.get("auth_unavailable"), False) or code in AUTH_UNAVAILABLE_CODES:
        return 503
    status = _safe_int(payload.get("status_code"), None)
    if status:
        return status
    if code == "user_not_registered":
        return 404
    if code == "invalid_email":
        return 400
    return 400


def _require_registered_email_identity_uncached(email: Any) -> Dict[str, Any]:
    normalized_email = normalize_email(email)

    if not is_valid_email(normalized_email):
        return {
            "ok": False,
            "registered": False,
            "code": "invalid_email",
            "message": "Die E-Mail-Adresse ist ungültig.",
            "email": normalized_email,
            "status_code": 400,
        }

    if legacy_require_registered_email_identity is not None:
        try:
            result = _safe_dict(legacy_require_registered_email_identity(normalized_email))
            result.setdefault("email", normalized_email)
            result.setdefault("status_code", _identity_failure_status(result) if not _safe_bool(result.get("ok"), False) else 200)
            return result
        except Exception as exc:
            return {
                "ok": False,
                "registered": False,
                "code": "auth_identity_lookup_failed",
                "message": "Die Registrierungsprüfung ist fehlgeschlagen.",
                "error": str(exc),
                "email": normalized_email,
                "auth_unavailable": True,
                "status_code": 503,
            }

    path = _safe_str(
        _read_config("VECTOPLAN_AUTH_IDENTITY_LOOKUP_PATH", DEFAULT_AUTH_IDENTITY_LOOKUP_PATH),
        default=DEFAULT_AUTH_IDENTITY_LOOKUP_PATH,
        max_len=500,
    )

    payload = _auth_client_post(
        path,
        {
            "email": normalized_email,
            "require_registered": True,
            "source": "vectoplan-app.project_invitation_service",
            "idempotency_key": "identity-lookup:" + hashlib.sha256(normalized_email.encode("utf-8")).hexdigest(),
            "request_id": _request_id("identity_lookup"),
        },
    )

    identity = _safe_dict(payload.get("identity") or payload.get("user") or payload.get("data"))
    registered = _safe_bool(
        payload.get("registered")
        if "registered" in payload
        else identity.get("registered")
        if "registered" in identity
        else bool(identity.get("id") or identity.get("auth_user_id") or identity.get("user_id")),
        False,
    )

    auth_user_id = (
        _safe_str(payload.get("auth_user_id"), default="", max_len=160)
        or _safe_str(identity.get("auth_user_id"), default="", max_len=160)
        or _safe_str(identity.get("user_id"), default="", max_len=160)
        or _safe_str(identity.get("id"), default="", max_len=160)
    )

    result = {
        **payload,
        "ok": _safe_bool(payload.get("ok"), registered),
        "registered": registered,
        "email": normalized_email,
        "auth_user_id": auth_user_id or None,
        "identity": identity,
        "code": _safe_str(payload.get("code"), "registered_identity_loaded" if registered else "user_not_registered", 120),
        "message": _safe_str(
            payload.get("message"),
            "Registrierte Identität gefunden." if registered else "Einladungen sind nur an bereits registrierte Accounts möglich.",
            500,
        ),
    }
    result.setdefault("status_code", 200 if result["ok"] and registered else _identity_failure_status(result))
    return result


def require_registered_email_identity(email: Any, *, use_cache: bool = True) -> Dict[str, Any]:
    normalized_email = normalize_email(email)
    if use_cache:
        cached = _identity_cache_get(normalized_email)
        if cached is not None:
            cached["cache_hit"] = True
            return cached
    result = _require_registered_email_identity_uncached(normalized_email)
    result = _safe_dict(result)
    result.setdefault("cache_hit", False)
    _identity_cache_set(normalized_email, result)
    return result


def dispatch_project_invitation_identity(
    *,
    email: Any,
    project_public_id: Any,
    role: Any,
    invited_by_auth_user_id: Any = None,
    invitation_id: Any = None,
    invitation_url: Any = None,
    message: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
    require_registered: bool = False,
) -> Dict[str, Any]:
    normalized_email = normalize_email(email)
    dispatch_idempotency_key = "project-invitation:" + (_safe_str(invitation_id, "", 160) or hashlib.sha256((normalized_email + ":" + _safe_str(project_public_id, "", 160)).encode("utf-8")).hexdigest())

    if legacy_dispatch_project_invitation_identity is not None:
        try:
            result = _safe_dict(
                legacy_dispatch_project_invitation_identity(
                    email=normalized_email,
                    project_public_id=project_public_id,
                    role=role,
                    invited_by_auth_user_id=invited_by_auth_user_id,
                    invitation_id=invitation_id,
                    invitation_url=invitation_url,
                    message=message,
                    metadata=metadata,
                    require_registered=require_registered,
                )
            )
            result.setdefault("status_code", 200 if _safe_bool(result.get("ok"), False) else _identity_failure_status(result))
            return result
        except Exception as exc:
            return {
                "ok": False,
                "code": "auth_invitation_dispatch_failed",
                "message": "Der externe Einladungsversand ist fehlgeschlagen.",
                "error": str(exc),
                "auth_unavailable": True,
                "status_code": 503,
            }

    path = _safe_str(
        _read_config("VECTOPLAN_AUTH_INVITATION_DISPATCH_PATH", DEFAULT_AUTH_INVITATION_DISPATCH_PATH),
        default=DEFAULT_AUTH_INVITATION_DISPATCH_PATH,
        max_len=500,
    )

    result = _auth_client_post(
        path,
        {
            "email": normalized_email,
            "project_public_id": _safe_str(project_public_id, default="", max_len=160),
            "role": normalize_invitation_role(role, allow_owner=False),
            "invited_by_auth_user_id": _safe_str(invited_by_auth_user_id, default="", max_len=160) or None,
            "invitation_id": _safe_str(invitation_id, default="", max_len=160) or None,
            "invitation_url": _safe_str(invitation_url, default="", max_len=2000) or None,
            "message": _safe_str(message, default="", max_len=4000) or None,
            "metadata": _safe_dict(metadata),
            "require_registered": bool(require_registered),
            "source": "vectoplan-app.project_invitation_service",
            "idempotency_key": dispatch_idempotency_key,
            "request_id": _request_id("invite_dispatch"),
        },
    )
    result.setdefault("status_code", 200 if _safe_bool(result.get("ok"), False) else _identity_failure_status(result))
    return result


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class ProjectInvitationServiceResult:
    ok: bool
    code: str
    message: str = ""
    project: Any = None
    invitation: Optional[Any] = None
    invitations: list = field(default_factory=list)
    membership: Any = None
    identity: Dict[str, Any] = field(default_factory=dict)
    dispatch: Dict[str, Any] = field(default_factory=dict)
    access: Dict[str, Any] = field(default_factory=dict)
    chunk_access_sync: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    error: Optional[str] = None

    def to_dict(self, include_private: bool = False, include_raw: bool = False) -> Dict[str, Any]:
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
            "identity": _redact_sensitive(self.identity),
            "dispatch": _redact_sensitive(self.dispatch),
            "access": _redact_sensitive(self.access),
            "chunk_access_sync": _redact_sensitive(self.chunk_access_sync),
            "chunkAccessSync": _redact_sensitive(self.chunk_access_sync),
            "data": _redact_sensitive(self.data, preserve_invitation_token=include_private),
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
            result["items"] = result["invitations"]
            result["total"] = len(result["invitations"])
            result["invitation_counts"] = invitation_status_counts(self.invitations)
        else:
            result.setdefault("invitations", [])
            result.setdefault("items", [])
            result.setdefault("total", 0)

        if self.membership is not None:
            result["membership"] = _serialize_membership(self.membership)

        return result


def _result(ok: bool, code: str, message: str = "", status_code: int = 200, **kwargs: Any) -> ProjectInvitationServiceResult:
    return ProjectInvitationServiceResult(ok=ok, code=code, message=message, status_code=status_code, **kwargs)


# ---------------------------------------------------------------------------
# Actor / Auth context helpers
# ---------------------------------------------------------------------------

def _context_to_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return _safe_dict(value.to_dict())
        return _safe_dict(value)
    except Exception:
        return {}


def _context_code(context: Mapping[str, Any]) -> str:
    return _safe_str(
        context.get("blocked_reason")
        or context.get("blockedReason")
        or context.get("reason_code")
        or context.get("reasonCode")
        or context.get("auth_state")
        or context.get("authState")
        or context.get("code"),
        "",
        160,
    ).lower()


def _context_auth_unavailable(context: Mapping[str, Any]) -> bool:
    code = _context_code(context)
    blocked_kind = _safe_str(context.get("blocked_kind") or context.get("blockedKind"), "", 80).lower()
    status = _safe_int(context.get("denial_status_code") or context.get("denialStatusCode") or context.get("status_code"), 0)
    return bool(
        _safe_bool(context.get("auth_unavailable") or context.get("authUnavailable"), False)
        or blocked_kind == "auth_unavailable"
        or code in AUTH_UNAVAILABLE_CODES
        or status == 503
    )


def _context_user_blocked(context: Mapping[str, Any]) -> bool:
    code = _context_code(context)
    blocked_kind = _safe_str(context.get("blocked_kind") or context.get("blockedKind"), "", 80).lower()
    return bool(
        not _context_auth_unavailable(context)
        and (
            _safe_bool(context.get("user_blocked") or context.get("userBlocked"), False)
            or blocked_kind == "user_blocked"
            or code in USER_BLOCKED_CODES
        )
    )


def _context_access_blocked(context: Mapping[str, Any]) -> bool:
    return bool(
        _context_auth_unavailable(context)
        or _context_user_blocked(context)
        or _safe_bool(context.get("access_blocked") or context.get("accessBlocked"), False)
        or _safe_bool(context.get("blocked"), False)
    )


def _context_denial_status(context: Mapping[str, Any]) -> int:
    if _context_auth_unavailable(context):
        return 503
    status = _safe_int(context.get("denial_status_code") or context.get("denialStatusCode") or context.get("status_code"), 0)
    if status:
        return status
    if _context_user_blocked(context) or _context_access_blocked(context):
        return 403
    if not _safe_bool(context.get("authenticated") or context.get("is_authenticated") or context.get("isAuthenticated"), False):
        return 401
    return 403


def get_actor_context(user_id: Any = None) -> Dict[str, Any]:
    """Return the current authenticated actor; an explicit local id is only a verifier.

    The caller may pass a local ``AppUser.id`` for compatibility, but that value
    never creates an authenticated context. It must match the server-side current
    user context or the result is fail-closed.
    """
    context: Dict[str, Any] = {}
    try:
        if get_current_user_context is not None:
            try:
                maybe_context = get_current_user_context(ensure=False)
            except TypeError:
                maybe_context = get_current_user_context()
            context = _context_to_dict(maybe_context)
    except Exception:
        context = {}

    if not context:
        context = {
            "user_id": None,
            "id": None,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "auth_unavailable": True,
            "access_blocked": True,
            "user_blocked": False,
            "blocked_kind": "auth_unavailable",
            "blocked_reason": "current_user_context_unavailable",
            "denial_status_code": 503,
            "source": "fallback_auth_unavailable",
        }

    explicit_user_id = _safe_int(user_id, default=None)
    current_user_id = _safe_int(
        context.get("user_id") or context.get("userId") or context.get("id"),
        default=None,
    )
    if explicit_user_id is not None and current_user_id != explicit_user_id:
        return {
            **context,
            "user_id": None,
            "id": None,
            "authenticated": False,
            "persistent": False,
            "blocked": True,
            "access_blocked": True,
            "auth_unavailable": False,
            "user_blocked": False,
            "blocked_kind": "identity_mismatch",
            "blocked_reason": "actor_user_id_mismatch",
            "denial_status_code": 403,
            "source": "explicit_user_id_verification_failed",
        }

    auth_unavailable = _context_auth_unavailable(context)
    user_blocked = _context_user_blocked(context)
    access_blocked = _context_access_blocked(context)
    demo_mode = _safe_bool(
        context.get("demo_mode") or context.get("is_demo") or context.get("demo") or context.get("demoMode"),
        default=False,
    )
    authenticated = _safe_bool(
        context.get("authenticated")
        or context.get("is_authenticated")
        or context.get("isAuthenticated")
        or context.get("logged_in"),
        default=False,
    )
    persistent = _safe_bool(
        context.get("persistent"),
        default=bool(current_user_id and authenticated and not demo_mode and not access_blocked),
    )
    actor_user_id = current_user_id
    if access_blocked or demo_mode or not persistent:
        actor_user_id = None

    auth_user_id = _safe_str(
        context.get("auth_user_id")
        or context.get("authUserId")
        or context.get("external_user_id")
        or context.get("sub")
        or context.get("subject"),
        default="",
        max_len=160,
    )
    email = _safe_str(
        context.get("email") or context.get("auth_email") or context.get("authEmail"),
        default="",
        max_len=320,
    ).lower()
    account_plan = _safe_str(
        context.get("account_plan")
        or context.get("accountPlan")
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
        "email": email or None,
        "demo_mode": bool(demo_mode and not access_blocked),
        "authenticated": bool(authenticated and not access_blocked),
        "persistent": bool(persistent and actor_user_id and auth_user_id and not access_blocked and not demo_mode),
        "blocked": bool(access_blocked),
        "auth_unavailable": bool(auth_unavailable),
        "user_blocked": bool(user_blocked),
        "access_blocked": bool(access_blocked),
        "blocked_reason": context.get("blocked_reason") or context.get("blockedReason"),
        "denial_status_code": _context_denial_status(context),
        "account_plan": account_plan or None,
    }



def _actor_user_id(actor_context: Optional[Mapping[str, Any]]) -> Optional[int]:
    data = _safe_dict(actor_context)
    if _context_access_blocked(data):
        return None
    if _safe_bool(data.get("demo_mode") or data.get("is_demo"), False):
        return None
    if not _safe_bool(data.get("persistent"), False):
        return None
    return _safe_int(data.get("user_id") or data.get("userId") or data.get("id"), default=None)


def _actor_auth_user_id(actor_context: Optional[Mapping[str, Any]]) -> Optional[str]:
    data = _safe_dict(actor_context)
    if _context_access_blocked(data):
        return None
    value = _safe_str(
        data.get("auth_user_id")
        or data.get("authUserId")
        or data.get("external_user_id")
        or data.get("sub")
        or data.get("subject"),
        default="",
        max_len=160,
    )
    return value or None


def _actor_email(actor_context: Optional[Mapping[str, Any]]) -> Optional[str]:
    data = _safe_dict(actor_context)
    if _context_access_blocked(data):
        return None
    value = _safe_str(data.get("email") or data.get("auth_email") or data.get("authEmail"), default="", max_len=320).lower()
    return value or None


def _actor_is_demo(actor_context: Optional[Mapping[str, Any]]) -> bool:
    data = _safe_dict(actor_context)
    if _context_access_blocked(data):
        return False
    return _safe_bool(data.get("demo_mode") or data.get("is_demo") or data.get("demo") or data.get("demoMode"), default=False)


def _actor_is_blocked(actor_context: Optional[Mapping[str, Any]]) -> bool:
    data = _safe_dict(actor_context)
    return _context_access_blocked(data)


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

def _permission_exception_result(project: Any, exc: Exception, permission: str = "manage") -> ProjectInvitationServiceResult:
    payload = _safe_dict(exc.to_dict() if hasattr(exc, "to_dict") else {})
    code = _safe_str(payload.get("code") or getattr(exc, "code", None), default="project_permission_denied", max_len=120)
    status = _safe_int(payload.get("status_code") or getattr(exc, "status_code", None), default=403) or 403
    message = _safe_str(payload.get("message") or payload.get("error") or getattr(exc, "message", None) or str(exc), default="Projektberechtigung fehlt.", max_len=1000)
    return _result(
        ok=False,
        code=code,
        message=message,
        project=project,
        status_code=status,
        access={**payload, "permission": permission},
    )


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

    if _context_auth_unavailable(actor_context):
        return _result(
            ok=False,
            code=_context_code(actor_context) or "auth_service_unavailable",
            message="vectoplan-auth ist nicht erreichbar.",
            project=project,
            status_code=503,
            data={"blocked": True, "auth_unavailable": True},
        )

    if _context_user_blocked(actor_context):
        return _result(
            ok=False,
            code=_context_code(actor_context) or "auth_blocked",
            message="Dieser Zugang ist gesperrt.",
            project=project,
            status_code=403,
            data={"blocked": True, "user_blocked": True},
        )

    if _context_access_blocked(actor_context):
        return _result(
            ok=False,
            code=_context_code(actor_context) or "access_blocked",
            message="Der Zugriff ist gesperrt.",
            project=project,
            status_code=_context_denial_status(actor_context),
            data={"blocked": True, "access_blocked": True},
        )

    if _project_is_demo(project) or _actor_is_demo(actor_context):
        return _result(
            ok=False,
            code="demo_mode_not_allowed",
            message="Im Demo-Modus können keine Projekt-Einladungen oder Rollenänderungen gespeichert werden.",
            project=project,
            status_code=403,
            data={"demo_mode": True},
        )

    if not _safe_bool(actor_context.get("authenticated"), False):
        return _result(
            ok=False,
            code="authentication_required",
            message="Für diese Aktion ist ein eingeloggter Benutzer erforderlich.",
            project=project,
            status_code=401,
        )

    if not actor_user_id or not _safe_bool(actor_context.get("persistent"), False):
        return _result(
            ok=False,
            code="local_user_link_required",
            message="Für diese Aktion ist eine lokale AppUser-Verknüpfung erforderlich.",
            project=project,
            status_code=403,
        )

    try:
        if require_project_permission is not None:
            try:
                require_project_permission(project, PERMISSION_MANAGE_TEAM, user_id=actor_user_id, allow_public_view=False)
            except TypeError:
                require_project_permission(project, "manage_team", actor_user_id)
            return None
    except PermissionDenied as exc:
        return _permission_exception_result(project, exc, "manage_team")
    except Exception:
        pass

    try:
        if can_manage_project_team is not None and bool(can_manage_project_team(project, actor_user_id)):
            return None
    except Exception:
        pass

    try:
        if can_manage_project is not None and bool(can_manage_project(project, actor_user_id)):
            return None
    except Exception:
        pass

    try:
        if get_project_permission_result is not None:
            try:
                perm = get_project_permission_result(project, user_id=actor_user_id, allow_public_view=False)
            except TypeError:
                perm = get_project_permission_result(project, actor_user_id)
            perm_dict = _safe_dict(perm.to_dict() if hasattr(perm, "to_dict") else perm)
            if _safe_bool(perm_dict.get("can_manage_team"), default=False):
                return None
            if _safe_bool(perm_dict.get("can_manage"), default=False):
                return None
            permissions = _safe_dict(perm_dict.get("permissions"))
            if _safe_bool(permissions.get("manage_team"), default=False) or _safe_bool(permissions.get("manage"), default=False):
                return None
            if _safe_bool(perm_dict.get("ok"), default=True) is False:
                return _permission_denied_result(project, "manage_team")
    except Exception:
        pass

    try:
        owner_user_id = _safe_int(getattr(project, "owner_user_id", None))
        if owner_user_id is not None and actor_user_id == owner_user_id:
            return None
    except Exception:
        pass

    try:
        membership = _find_membership(_project_id(project), actor_user_id)
        if membership is not None and _membership_has_manage(membership):
            return None
    except Exception:
        pass

    return _permission_denied_result(project, "manage_team")


# ---------------------------------------------------------------------------
# AppUser lookup only, no creation
# ---------------------------------------------------------------------------

def find_linked_app_user(auth_user_id: Any = None, email: Any = None) -> Optional[Any]:
    """
    Sucht einen bereits existierenden lokalen AppUser-Link.

    Wichtig:
    Diese Funktion erzeugt keinen AppUser.
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


def ensure_linked_app_user(identity: Mapping[str, Any], email: Any) -> Tuple[Optional[Any], bool]:
    """Create only the local FK/profile shadow for a verified auth identity."""
    if AppUser is None or db is None:
        return None, False

    payload = _safe_dict(identity)
    detail = _safe_dict(payload.get("identity") or payload.get("user") or payload.get("data"))
    auth_user_id = (
        _safe_str(payload.get("auth_user_id"), "", 160)
        or _safe_str(detail.get("auth_user_id"), "", 160)
        or _safe_str(detail.get("user_id"), "", 160)
        or _safe_str(detail.get("id"), "", 160)
    )
    normalized_email = normalize_email(email)
    if not auth_user_id or not _safe_bool(payload.get("registered"), False):
        return None, False

    linked = find_linked_app_user(auth_user_id=auth_user_id, email=None)
    if linked is not None:
        return linked, False

    email_match = find_linked_app_user(auth_user_id=None, email=normalized_email)
    if email_match is not None:
        existing_auth_id = _safe_str(getattr(email_match, "auth_user_id", None), "", 160)
        if existing_auth_id and existing_auth_id != auth_user_id:
            raise ValueError("email_is_linked_to_different_auth_identity")
        linked = email_match
    else:
        linked = AppUser()

    display_name = (
        _safe_str(detail.get("display_name"), "", 255)
        or _safe_str(detail.get("displayName"), "", 255)
        or _safe_str(detail.get("name"), "", 255)
        or normalized_email
    )
    username = _safe_str(detail.get("username") or detail.get("handle"), "", 120) or None

    if hasattr(linked, "link_auth") and callable(linked.link_auth):
        linked.link_auth(
            auth_user_id=auth_user_id,
            auth_account_id=detail.get("auth_account_id") or detail.get("account_id"),
            email=normalized_email,
            display_name=display_name,
            username=username,
            plan=detail.get("plan"),
            plan_status=detail.get("plan_status"),
            roles=detail.get("roles"),
            entitlements=detail.get("entitlements"),
            auth_state="verified_by_email_lookup",
            auth_status="linked",
            raw_auth={"source": "project_invitation", "verified": True},
        )
    else:
        _setattr_if_present(linked, "auth_user_id", auth_user_id)
        _setattr_if_present(linked, "email", normalized_email)
        _setattr_if_present(linked, "display_name", display_name)
        _setattr_if_present(linked, "auth_status", "linked")
    _setattr_if_present(linked, "is_placeholder", False)
    _setattr_if_present(linked, "is_active", True)
    if hasattr(linked, "normalize") and callable(linked.normalize):
        linked.normalize()
    _session_add(linked)
    db.session.flush()
    return linked, email_match is None


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
        if getattr(membership, "revoked_at", None) is not None:
            return False
        status = _membership_status(membership).lower()
        return status not in {"deleted", "removed", "revoked", "inactive", "disabled", "rejected", "expired"}
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
        if _safe_bool(permissions.get("manage"), default=False) or _safe_bool(permissions.get("manage_team"), default=False):
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
    normalized_role = normalize_invitation_role(role, allow_owner=False)
    if normalized_role == ROLE_OWNER:
        raise ValueError("owner_invitation_not_allowed")
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

    for attr_name, perm in mapping.items():
        try:
            if hasattr(membership, attr_name):
                setattr(membership, attr_name, bool(flags.get(perm, False)))
        except Exception:
            pass

    try:
        if hasattr(membership, "status"):
            membership.status = MEMBERSHIP_STATUS_ACTIVE
        for field_name in ("revoked_at", "revoked_by_user_id", "revoke_reason", "expires_at"):
            if hasattr(membership, field_name):
                setattr(membership, field_name, None)
    except Exception:
        pass

    try:
        if hasattr(membership, "accepted_at") and getattr(membership, "accepted_at", None) is None:
            membership.accepted_at = utcnow()
    except Exception:
        pass


def _create_or_update_membership_from_invitation(
    invitation: Any,
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

    safe_project_id = _safe_int(getattr(invitation, "project_id", None))
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
        "role": normalize_invitation_role(invitation.role, allow_owner=False),
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
# Invitation helpers
# ---------------------------------------------------------------------------

def build_invitation_url(invitation: Any, plain_token: Optional[str] = None, include_token: bool = False) -> str:
    """
    Baut eine vorbereitete Einladungs-URL.

    Standardmäßig ohne Token, damit keine geheimen Tokens versehentlich in
    dispatch_response_json, Logs oder UI landen.
    """
    public_base = _safe_str(
        _read_config("VECTOPLAN_APP_PUBLIC_URL", "") or _read_config("APP_PUBLIC_URL", ""),
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


def _model_unavailable_result(code: str = "project_invitation_model_unavailable") -> ProjectInvitationServiceResult:
    return _result(
        ok=False,
        code=code,
        message="ProjectInvitation-Modell oder Datenbank ist nicht verfügbar.",
        status_code=503,
        data={
            "project_invitation_model_available": ProjectInvitation is not None,
            "db_available": db is not None,
        },
    )


def _manual_create_pending_invitation(
    *,
    project_id: Any,
    project_public_id: Any,
    email: str,
    role: str,
    invited_by_user_id: Any,
    invited_by_auth_user_id: Any,
    identity: Mapping[str, Any],
    message: Any,
    metadata: Mapping[str, Any],
    expires_in_days: int,
) -> Tuple[Any, str]:
    if ProjectInvitation is None:
        raise RuntimeError("ProjectInvitation unavailable")

    plain_token = secrets.token_urlsafe(32)
    public_id = uuid.uuid4().hex

    invitation = ProjectInvitation()
    values = {
        "public_id": public_id,
        "project_id": project_id,
        "project_public_id": project_public_id,
        "email": email,
        "email_normalized": email,
        "auth_user_id": _safe_str(identity.get("auth_user_id"), "", 160) or None,
        "role": role,
        "status": STATUS_PENDING,
        "token_hash": hash_invitation_token(plain_token),
        "invited_by_user_id": invited_by_user_id,
        "invited_by_auth_user_id": invited_by_auth_user_id,
        "message": _safe_str(message, "", 4000) or None,
        "identity_json": _safe_dict(identity),
        "metadata_json": _safe_dict(metadata),
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "expires_at": utcnow() + _dt.timedelta(days=int(expires_in_days or DEFAULT_INVITATION_EXPIRY_DAYS)),
    }

    for key, value in values.items():
        try:
            if hasattr(invitation, key):
                setattr(invitation, key, value)
        except Exception:
            pass

    return invitation, plain_token


def _find_active_invitation_for_email(project_id: Any, email: str) -> Any:
    if ProjectInvitation is None:
        return None
    try:
        if hasattr(ProjectInvitation, "find_active_for_email"):
            return ProjectInvitation.find_active_for_email(project_id, email)
    except Exception:
        pass
    try:
        return ProjectInvitation.query.filter(
            ProjectInvitation.project_id == project_id,
            ProjectInvitation.email_normalized == email,
            ProjectInvitation.status == STATUS_PENDING,
        ).first()
    except Exception:
        return None


def _find_invitation_by_public_id(invitation_id: Any) -> Any:
    if ProjectInvitation is None:
        return None
    try:
        if hasattr(ProjectInvitation, "find_by_public_id"):
            return ProjectInvitation.find_by_public_id(invitation_id)
    except Exception:
        pass
    try:
        return ProjectInvitation.query.filter(ProjectInvitation.public_id == _safe_str(invitation_id)).first()
    except Exception:
        return None


def _invitation_can_revoke(invitation: Any) -> bool:
    try:
        if hasattr(invitation, "can_revoke") and callable(invitation.can_revoke):
            return bool(invitation.can_revoke())
        status = _safe_str(getattr(invitation, "status", ""), "", 40).lower()
        return status == STATUS_PENDING
    except Exception:
        return False


def _invitation_can_accept(invitation: Any, *, auth_user_id: Any = None, email: Any = None) -> bool:
    """Match canonical auth id first; email fallback is legacy-only."""
    try:
        candidate_auth = _safe_str(auth_user_id, "", 160)
        candidate_email = normalize_email(email)
        inv_auth = _safe_str(getattr(invitation, "auth_user_id", ""), "", 160)
        inv_email = normalize_email(getattr(invitation, "email_normalized", "") or getattr(invitation, "email", ""))
        if inv_auth:
            return bool(candidate_auth and secrets.compare_digest(inv_auth, candidate_auth))
        if inv_email:
            return bool(candidate_email and secrets.compare_digest(inv_email, candidate_email))
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Invitation security / Chunk access synchronization
# ---------------------------------------------------------------------------


def _strict_invitation_role(value: Any) -> Tuple[Optional[str], Optional[ProjectInvitationServiceResult]]:
    raw = _safe_str(value, DEFAULT_INVITATION_ROLE, 80).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "administrator": ROLE_ADMIN,
        "manager": ROLE_ADMIN,
        "edit": ROLE_EDITOR,
        "writer": ROLE_EDITOR,
        "view": ROLE_VIEWER,
        "reader": ROLE_VIEWER,
    }
    normalized = aliases.get(raw, raw or DEFAULT_INVITATION_ROLE)
    if normalized == ROLE_OWNER:
        return None, _result(
            ok=False,
            code="owner_invitation_not_allowed",
            message="Die Owner-Rolle kann nur über die dedizierte Besitzübertragung vergeben werden.",
            status_code=400,
        )
    allowed = {ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}
    if normalized not in allowed:
        return None, _result(
            ok=False,
            code="invalid_invitation_role",
            message="Ungültige Projektrolle für eine Einladung.",
            status_code=400,
            data={"allowed_roles": sorted(allowed)},
        )
    return normalized, None


def _identity_override_error(
    actor_context: Mapping[str, Any],
    *,
    auth_user_id: Any = None,
    email: Any = None,
    local_user_id: Any = None,
) -> Optional[ProjectInvitationServiceResult]:
    actor_auth = _actor_auth_user_id(actor_context)
    actor_email = normalize_email(_actor_email(actor_context))
    actor_local = _actor_user_id(actor_context)
    supplied_auth = _safe_str(auth_user_id, "", 160)
    supplied_email = normalize_email(email)
    supplied_local = _safe_int(local_user_id, None)
    mismatch = bool(
        (supplied_auth and supplied_auth != actor_auth)
        or (supplied_email and supplied_email != actor_email)
        or (supplied_local and supplied_local != actor_local)
    )
    if not mismatch:
        return None
    return _result(
        ok=False,
        code="identity_override_denied",
        message="Identitätswerte dürfen nicht vom aktuellen serverseitigen Auth-Kontext abweichen.",
        status_code=403,
        data={"identity_override_denied": True},
    )


def _verify_invitation_plain_token(invitation: Any, plain_token: Any) -> bool:
    token = _safe_str(plain_token, "", 4096)
    if not token:
        return True
    try:
        verifier = getattr(invitation, "verify_plain_token", None)
        if callable(verifier):
            return bool(verifier(token))
    except Exception:
        return False
    expected = _safe_str(getattr(invitation, "token_hash", None), "", 256)
    if not expected:
        return False
    try:
        return secrets.compare_digest(hash_invitation_token(token), expected)
    except Exception:
        return False


def _access_sync_enabled() -> bool:
    return _safe_bool(_read_config("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True), True)


def _access_sync_on_accept() -> bool:
    return _safe_bool(_read_config("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_INVITATION_ACCEPT", True), True)


def _access_sync_required() -> bool:
    return _safe_bool(_read_config("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED", False), False)


def _serialize_access_sync_status(project: Any) -> Dict[str, Any]:
    if callable(serialize_project_chunk_access_sync_status):
        try:
            return _safe_dict(serialize_project_chunk_access_sync_status(project))
        except Exception:
            pass
    metadata = _safe_dict(getattr(project, "metadata_json", None))
    chunk_meta = _safe_dict(metadata.get("chunk"))
    return _safe_dict(chunk_meta.get("accessSync") or chunk_meta.get("access_sync"))


def _mark_access_sync_local(
    project: Any,
    *,
    status: str,
    code: str = "",
    message: str = "",
    request_id: str = "",
    repair_required: bool = False,
) -> None:
    try:
        if status in {"failed", "repair_required"} and hasattr(project, "mark_chunk_access_sync_failed"):
            project.mark_chunk_access_sync_failed(
                code=code or "chunk_access_sync_failed",
                message=message or "Chunk access synchronization failed.",
                repair_required=repair_required or status == "repair_required",
            )
        elif status == "syncing" and hasattr(project, "mark_chunk_access_sync_started"):
            project.mark_chunk_access_sync_started(request_id=request_id or None)
        elif status == "ready" and hasattr(project, "mark_chunk_access_synced"):
            project.mark_chunk_access_synced()
        else:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = status
            if code and hasattr(project, "chunk_access_sync_error_code"):
                project.chunk_access_sync_error_code = code
            if message and hasattr(project, "chunk_access_sync_error_message"):
                project.chunk_access_sync_error_message = message
        metadata = _safe_dict(getattr(project, "metadata_json", None))
        chunk_meta = _safe_dict(metadata.get("chunk"))
        chunk_meta["accessSync"] = {
            **_safe_dict(chunk_meta.get("accessSync") or chunk_meta.get("access_sync")),
            "status": status,
            "code": code or None,
            "message": message or None,
            "requestId": request_id or None,
            "repairRequired": bool(repair_required),
            "updatedAt": utcnow().isoformat(),
            "source": "project_invitation_service",
        }
        metadata["chunk"] = chunk_meta
        if hasattr(project, "metadata_json"):
            project.metadata_json = metadata
        _session_add(project)
    except Exception:
        pass


def _sync_access_after_invitation_accept(
    project: Any,
    *,
    actor_auth_user_id: str,
    commit: bool,
) -> Dict[str, Any]:
    request_id = _request_id("invite_access")
    if not commit:
        _mark_access_sync_local(
            project, status="pending", code="chunk_access_sync_deferred_until_commit",
            message="Access synchronization is deferred until the app transaction is committed.",
            request_id=request_id,
        )
        return {
            "ok": False,
            "code": "chunk_access_sync_deferred_until_commit",
            "statusCode": 202,
            "retryable": True,
            "deferred": True,
            "requestId": request_id,
        }
    if not _access_sync_enabled() or not _access_sync_on_accept():
        return {
            "ok": True,
            "code": "chunk_access_sync_disabled_for_invitation_accept",
            "statusCode": 200,
            "disabled": True,
            "requestId": request_id,
        }
    if not callable(sync_project_chunk_access):
        _mark_access_sync_local(
            project, status="repair_required", code="chunk_access_sync_service_unavailable",
            message="project_chunk_access_sync_service is unavailable.",
            request_id=request_id, repair_required=True,
        )
        try:
            _commit_or_flush(commit=True)
        except Exception:
            pass
        return {
            "ok": False,
            "code": "chunk_access_sync_service_unavailable",
            "statusCode": 503,
            "retryable": True,
            "repairRequired": True,
            "requestId": request_id,
        }
    try:
        raw = sync_project_chunk_access(
            project,
            actor_auth_user_id=actor_auth_user_id,
            force=True,
            commit=True,
            request_id=request_id,
            raise_on_error=False,
        )
        result = _safe_dict(raw.to_dict() if hasattr(raw, "to_dict") else raw)
        result.setdefault("requestId", request_id)
        return _safe_dict(_redact_sensitive(result))
    except Exception as exc:
        error_payload = _safe_dict(exc.to_dict() if hasattr(exc, "to_dict") else {})
        code = _safe_str(error_payload.get("code") or getattr(exc, "code", None), "chunk_access_sync_failed", 160)
        message = _safe_str(error_payload.get("message") or getattr(exc, "message", None) or str(exc), "Chunk access synchronization failed.", 2000)
        status_code = _safe_int(error_payload.get("statusCode") or getattr(exc, "status_code", None), 502) or 502
        repair_required = _safe_bool(error_payload.get("repairRequired") or getattr(exc, "repair_required", False), True)
        retryable = _safe_bool(error_payload.get("retryable") or getattr(exc, "retryable", False), True)
        _mark_access_sync_local(
            project, status="repair_required" if repair_required else "failed",
            code=code, message=message, request_id=request_id, repair_required=repair_required,
        )
        try:
            _commit_or_flush(commit=True)
        except Exception:
            pass
        return {
            "ok": False,
            "code": code,
            "message": message,
            "statusCode": status_code,
            "retryable": retryable,
            "repairRequired": repair_required,
            "requestId": request_id,
        }


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
            auth_status = get_auth_identity_status()
        except Exception as exc:
            auth_status = {
                "ok": False,
                "code": "auth_identity_status_failed",
                "error": str(exc),
                "status_code": 503,
            }

        return {
            "ok": True,
            "service": "project_invitation_service",
            "phase": "vectoplan-auth-invitation-safe-no-default-user",
            "default_user_removed": True,
            "project_model_available": Project is not None,
            "membership_model_available": ProjectMembership is not None,
            "audit_model_available": ProjectAuditEvent is not None,
            "app_user_model_available": AppUser is not None,
            "project_invitation_model_available": ProjectInvitation is not None,
            "db_available": db is not None,
            "auth_identity": auth_status,
            "invitable_roles": sorted(list(INVITABLE_PROJECT_ROLES)),
            "auth_unavailable_returns_503": True,
            "chunk_access_sync": {
                "service_available": callable(sync_project_chunk_access),
                "enabled": _access_sync_enabled(),
                "on_invitation_accept": _access_sync_on_accept(),
                "required": _access_sync_required(),
            },
            "identity_cache": {
                "ttl_seconds": _cache_seconds(),
                "max_entries": _cache_max_entries(),
                "entries": len(_IDENTITY_CACHE),
                "positive_only": True,
            },
            "rules": {
                "creates_app_user": False,
                "allows_demo": False,
                "requires_manage_permission": True,
                "requires_registered_email": True,
                "accept_requires_local_app_user_link": True,
                "accept_uses_server_auth_identity_only": True,
                "owner_invitation_allowed": False,
                "chunk_sync_after_local_commit": True,
                "remote_mutation_with_commit_false": False,
                "default_user": False,
            },
        }

    def list_invitations(
        self,
        project_or_id: Any,
        actor_user_id: Any = None,
        include_terminal: bool = True,
        include_private: bool = False,
    ) -> ProjectInvitationServiceResult:
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()

        project = resolve_project(project_or_id)
        if project is None:
            return _result(ok=False, code="project_not_found", message="Projekt nicht gefunden.", status_code=404)

        actor_context = get_actor_context(actor_user_id)
        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        try:
            try:
                ProjectInvitation.expire_old_pending(_project_id(project))
            except Exception:
                pass

            if hasattr(ProjectInvitation, "list_for_project"):
                invitations = ProjectInvitation.list_for_project(
                    _project_id(project),
                    include_terminal=include_terminal,
                    include_deleted=False,
                )
            else:
                query = ProjectInvitation.query.filter(ProjectInvitation.project_id == _project_id(project))
                if not include_terminal:
                    query = query.filter(ProjectInvitation.status == STATUS_PENDING)
                invitations = query.all()

            return _result(
                ok=True,
                code="project_invitations_loaded",
                message="Einladungen wurden geladen.",
                project=project,
                invitations=list(invitations or []),
                access={"can_manage": True, "can_manage_team": True},
                data={"include_terminal": bool(include_terminal), "include_private": bool(include_private), "total": len(list(invitations or []))},
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
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()
        project = resolve_project(project_or_id)
        if project is None:
            return _result(ok=False, code="project_not_found", message="Projekt nicht gefunden.", status_code=404)

        actor_context = get_actor_context(actor_user_id)
        actor_id = _actor_user_id(actor_context)
        actor_auth_user_id = _actor_auth_user_id(actor_context)
        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        normalized_email = normalize_email(email)
        if not is_valid_email(normalized_email):
            return _result(
                ok=False, code="invalid_email", message="Die E-Mail-Adresse ist ungültig.",
                project=project, status_code=400, data={"email": normalized_email},
            )
        normalized_role, role_error = _strict_invitation_role(role)
        if role_error is not None:
            role_error.project = project
            return role_error
        assert normalized_role is not None

        identity = _safe_dict(require_registered_email_identity(normalized_email, use_cache=True))
        identity_registered = _safe_bool(identity.get("ok"), False) and _safe_bool(identity.get("registered"), False)
        identity_code = _safe_str(identity.get("code"), "user_not_registered", 120)
        is_unregistered = identity_code in {"user_not_registered", "not_registered", "identity_not_found"} or _safe_int(identity.get("status_code"), 0) == 404

        if not identity_registered and not is_unregistered:
            status = _safe_int(identity.get("status_code"), None) or (503 if identity_code in AUTH_UNAVAILABLE_CODES else 400)
            _write_audit_event(
                project, ACTION_INVITATION_FAILED, actor_user_id=actor_id,
                message="Project email access lookup failed.",
                metadata={"email": normalized_email, "role": normalized_role, "code": identity_code},
            )
            try:
                _commit_or_flush(commit=commit)
            except Exception:
                pass
            return _result(
                ok=False, code=identity_code,
                message=_safe_str(identity.get("message"), "Die E-Mail-Adresse konnte nicht geprüft werden.", 1000),
                project=project, identity=_safe_dict(_redact_sensitive(identity)), status_code=status,
            )

        target_auth_user_id = _safe_str(identity.get("auth_user_id"), "", 160) if identity_registered else ""
        if identity_registered and not target_auth_user_id:
            return _result(
                ok=False, code="auth_identity_incomplete",
                message="vectoplan-auth hat keine kanonische User-ID für diese Identität geliefert.",
                project=project, identity=_safe_dict(_redact_sensitive(identity)), status_code=503,
            )

        lock_key = f"invite:{_project_id(project)}:{normalized_email}"
        try:
            with _keyed_lock(lock_key):
                if identity_registered:
                    try:
                        linked_user, local_link_created = ensure_linked_app_user(identity, normalized_email)
                        linked_user_id = _safe_int(getattr(linked_user, "id", None))
                        if linked_user is None or not linked_user_id:
                            raise RuntimeError("local_user_link_required")

                        existing_membership = _find_membership(_project_id(project), linked_user_id)
                        if existing_membership is not None and _safe_str(getattr(existing_membership, "role", ""), "", 40).lower() == ROLE_OWNER:
                            return _result(
                                ok=True, code="user_already_project_member",
                                message="Der Projektbesitzer hat bereits Zugriff.",
                                project=project, membership=existing_membership,
                                identity=_safe_dict(_redact_sensitive(identity)), status_code=200,
                                data={"idempotent": True, "direct_access": True, "registered": True},
                            )

                        membership_input = SimpleNamespace(project_id=_project_id(project), role=normalized_role)
                        membership_ok, membership, membership_code = _create_or_update_membership_from_invitation(
                            membership_input, local_user_id=linked_user_id, actor_context=actor_context
                        )
                        if not membership_ok or membership is None:
                            raise RuntimeError(membership_code or "membership_create_failed")
                        _session_add(membership)
                        previous_invitation = _find_active_invitation_for_email(_project_id(project), normalized_email)
                        if previous_invitation is not None:
                            if hasattr(previous_invitation, "mark_accepted"):
                                previous_invitation.mark_accepted(
                                    accepted_by_user_id=linked_user_id,
                                    accepted_by_auth_user_id=target_auth_user_id,
                                    membership_id=getattr(membership, "id", None),
                                )
                            else:
                                _setattr_if_present(previous_invitation, "status", STATUS_ACCEPTED)
                                _setattr_if_present(previous_invitation, "accepted_at", utcnow())
                                _setattr_if_present(previous_invitation, "accepted_by_user_id", linked_user_id)
                                _setattr_if_present(previous_invitation, "accepted_by_auth_user_id", target_auth_user_id)
                            _session_add(previous_invitation)
                        _write_audit_event(
                            project, "member_access_granted_by_email", actor_user_id=actor_id,
                            message="Project access granted to registered auth identity.",
                            metadata={
                                "email": normalized_email,
                                "role": normalized_role,
                                "auth_user_id": target_auth_user_id,
                                "membership_code": membership_code,
                                "local_link_created": bool(local_link_created),
                            },
                        )
                        _commit_or_flush(commit=commit)
                    except Exception as exc:
                        _rollback_safely()
                        _log_exception("direct project access failed", project_id=_project_id(project), email=normalized_email)
                        return _result(
                            ok=False, code="project_member_access_failed",
                            message="Der Projektzugriff konnte nicht erteilt werden.",
                            project=project, identity=_safe_dict(_redact_sensitive(identity)),
                            status_code=500, error=str(exc),
                        )

                    access_sync = _sync_access_after_invitation_accept(
                        project, actor_auth_user_id=actor_auth_user_id, commit=commit
                    )
                    code = "project_member_access_updated" if membership_code == "membership_updated" else "project_member_access_granted"
                    return _result(
                        ok=True, code=code,
                        message="Projektzugriff wurde erteilt.",
                        project=project, membership=membership,
                        identity=_safe_dict(_redact_sensitive(identity)),
                        chunk_access_sync=access_sync,
                        status_code=200 if membership_code == "membership_updated" else 201,
                        data={
                            "email": normalized_email,
                            "role": normalized_role,
                            "registered": True,
                            "direct_access": True,
                            "local_user_link_created": bool(local_link_created),
                            "email_dispatch_required": False,
                            "local_committed": bool(commit),
                        },
                    )

                existing_pending = _find_active_invitation_for_email(_project_id(project), normalized_email)
                if existing_pending is not None:
                    return _result(
                        ok=True, code="invitation_placeholder_already_pending",
                        message="Diese E-Mail-Adresse ist bereits vorgemerkt.",
                        project=project, invitation=existing_pending,
                        identity=_safe_dict(_redact_sensitive(identity)), status_code=200,
                        data={"idempotent": True, "registered": False, "email_dispatch_placeholder": True},
                    )

                invitation = None
                plain_token = None
                placeholder_identity = {
                    **identity,
                    "ok": True,
                    "registered": False,
                    "email": normalized_email,
                    "code": "email_invitation_placeholder",
                    "message": "E-Mail-Einladung ist vorgemerkt.",
                }
                invitation_metadata = {
                    **_safe_dict(metadata),
                    "created_by_service": "project_invitation_service",
                    "email_dispatch_placeholder": True,
                    "dispatch_implemented": False,
                }
                try:
                    if hasattr(ProjectInvitation, "create_pending"):
                        invitation, plain_token = ProjectInvitation.create_pending(
                            project_id=_project_id(project), project_public_id=_project_public_id(project),
                            email=normalized_email, role=normalized_role, invited_by_user_id=actor_id,
                            invited_by_auth_user_id=actor_auth_user_id, identity=placeholder_identity,
                            message=message, metadata=invitation_metadata,
                            expires_in_days=expires_in_days, generate_token=True,
                        )
                    else:
                        invitation, plain_token = _manual_create_pending_invitation(
                            project_id=_project_id(project), project_public_id=_project_public_id(project),
                            email=normalized_email, role=normalized_role, invited_by_user_id=actor_id,
                            invited_by_auth_user_id=actor_auth_user_id, identity=placeholder_identity,
                            message=message, metadata=invitation_metadata,
                            expires_in_days=expires_in_days,
                        )
                    if invitation is None:
                        raise RuntimeError("project_invitation_create_returned_none")
                    safe_public_url = build_invitation_url(invitation, plain_token=None, include_token=False)
                    if safe_public_url:
                        _setattr_if_present(invitation, "invitation_url", safe_public_url)
                    _session_add(invitation)
                    _write_audit_event(
                        project, ACTION_INVITATION_CREATED, actor_user_id=actor_id,
                        message="Unregistered email invitation placeholder created.",
                        metadata={"invitation_id": getattr(invitation, "public_id", None), "email": normalized_email, "role": normalized_role},
                    )
                    _commit_or_flush(commit=commit)
                except Exception as exc:
                    _rollback_safely()
                    _log_exception("invitation placeholder persistence failed", project_id=_project_id(project), email=normalized_email)
                    return _result(
                        ok=False, code="project_invitation_placeholder_failed",
                        message="Die E-Mail-Adresse konnte nicht vorgemerkt werden.",
                        project=project, invitation=invitation,
                        identity=_safe_dict(_redact_sensitive(identity)), status_code=500, error=str(exc),
                    )

                data = {
                    "email": normalized_email,
                    "role": normalized_role,
                    "registered": False,
                    "direct_access": False,
                    "email_dispatch_placeholder": True,
                    "email_dispatch_implemented": False,
                    "dispatch_requested": bool(dispatch),
                    "dispatch_sent": False,
                    "invitation_persisted": True,
                    "local_committed": bool(commit),
                }
                if include_token_in_result and plain_token:
                    data["invitation_token"] = plain_token
                    data["invitation_url_with_token"] = build_invitation_url(invitation, plain_token=plain_token, include_token=True)
                return _result(
                    ok=True, code="project_invitation_placeholder_created",
                    message="E-Mail-Adresse wurde vorgemerkt. Der Versand wird später ergänzt.",
                    project=project, invitation=invitation,
                    identity=_safe_dict(_redact_sensitive(identity)),
                    dispatch={
                        "ok": True,
                        "code": "email_dispatch_placeholder",
                        "message": "Der E-Mail-Versand ist noch nicht implementiert.",
                        "external_sent": False,
                    },
                    status_code=201, data=data,
                )
        except TimeoutError:
            return _result(
                ok=False, code="project_invitation_busy",
                message="Eine parallele Zugriffsaktion für diese E-Mail-Adresse läuft bereits.",
                project=project, status_code=409, data={"retryable": True},
            )
    def revoke_invitation(
        self,
        project_or_id: Any,
        invitation_id: Any,
        actor_user_id: Any = None,
        reason: Any = None,
        commit: bool = True,
    ) -> ProjectInvitationServiceResult:
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()

        project = resolve_project(project_or_id)
        if project is None:
            return _result(ok=False, code="project_not_found", message="Projekt nicht gefunden.", status_code=404)

        actor_context = get_actor_context(actor_user_id)
        actor_id = _actor_user_id(actor_context)
        actor_auth_user_id = _actor_auth_user_id(actor_context)

        denied = _require_manage_permission(project, actor_context)
        if denied is not None:
            return denied

        invitation = _find_invitation_by_public_id(invitation_id)
        if invitation is None:
            return _result(ok=False, code="project_invitation_not_found", message="Einladung nicht gefunden.", project=project, status_code=404)

        if _safe_int(getattr(invitation, "project_id", None)) != _project_id(project):
            return _result(
                ok=False,
                code="project_invitation_project_mismatch",
                message="Diese Einladung gehört nicht zu diesem Projekt.",
                project=project,
                invitation=invitation,
                status_code=409,
            )

        if not _invitation_can_revoke(invitation):
            return _result(
                ok=False,
                code="project_invitation_not_revokable",
                message="Diese Einladung kann nicht mehr widerrufen werden.",
                project=project,
                invitation=invitation,
                status_code=409,
            )

        try:
            if hasattr(invitation, "mark_revoked"):
                invitation.mark_revoked(revoked_by_user_id=actor_id, revoked_by_auth_user_id=actor_auth_user_id, reason=reason)
            else:
                _setattr_if_present(invitation, "status", STATUS_REVOKED)
                _setattr_if_present(invitation, "revoked_at", utcnow())
                _setattr_if_present(invitation, "revoked_by_user_id", actor_id)
                _setattr_if_present(invitation, "revoked_by_auth_user_id", actor_auth_user_id)
                _setattr_if_present(invitation, "revoke_reason", _safe_str(reason, "", 1000) or None)

            _write_audit_event(
                project,
                ACTION_INVITATION_REVOKED,
                actor_user_id=actor_id,
                message="Project invitation revoked.",
                metadata={
                    "invitation_id": getattr(invitation, "public_id", None),
                    "email": getattr(invitation, "email_normalized", None),
                    "role": getattr(invitation, "role", None),
                    "reason": _safe_str(reason),
                },
            )

            _commit_or_flush(commit=commit)

            return _result(ok=True, code="project_invitation_revoked", message="Einladung wurde widerrufen.", project=project, invitation=invitation, status_code=200)

        except Exception as exc:
            _rollback_safely()
            _log_exception("revoke_invitation failed", project_id=_project_id(project), invitation_id=_safe_str(invitation_id))
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
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()
        actor_context = get_actor_context(None)
        if _context_auth_unavailable(actor_context):
            return _result(ok=False, code=_context_code(actor_context) or "auth_service_unavailable", message="vectoplan-auth ist nicht erreichbar.", status_code=503, data={"auth_unavailable": True})
        if _actor_is_blocked(actor_context):
            return _result(ok=False, code=_context_code(actor_context) or "auth_blocked", message="Der Zugriff ist gesperrt.", status_code=_context_denial_status(actor_context), data={"blocked": True})
        if not _safe_bool(actor_context.get("authenticated"), False):
            return _result(ok=False, code="authentication_required", message="Zum Ablehnen der Einladung ist Login erforderlich.", status_code=401)
        override_error = _identity_override_error(actor_context, auth_user_id=auth_user_id, email=email)
        if override_error is not None:
            return override_error
        effective_auth_user_id = _actor_auth_user_id(actor_context)
        effective_email = _actor_email(actor_context)
        if not effective_auth_user_id:
            return _result(ok=False, code="auth_identity_required", message="Zum Ablehnen der Einladung ist eine kanonische Auth-User-ID erforderlich.", status_code=401)
        try:
            with _keyed_lock(f"reject:{_safe_str(invitation_id, '', 180)}"):
                invitation = _find_invitation_by_public_id(invitation_id)
                if invitation is None:
                    return _result(ok=False, code="project_invitation_not_found", message="Einladung nicht gefunden.", status_code=404)
                project = resolve_project(getattr(invitation, "project_id", None))
                if _safe_str(getattr(invitation, "status", ""), "", 40).lower() == STATUS_REJECTED:
                    return _result(ok=True, code="project_invitation_already_rejected", message="Einladung wurde bereits abgelehnt.", project=project, invitation=invitation, status_code=200, data={"idempotent": True})
                if not _invitation_can_accept(invitation, auth_user_id=effective_auth_user_id, email=effective_email):
                    return _result(ok=False, code="project_invitation_not_rejectable", message="Diese Einladung kann durch diese Identität nicht abgelehnt werden.", project=project, invitation=invitation, status_code=403)
                try:
                    if hasattr(invitation, "mark_rejected"):
                        invitation.mark_rejected(reason=reason)
                    else:
                        _setattr_if_present(invitation, "status", STATUS_REJECTED)
                        _setattr_if_present(invitation, "rejected_at", utcnow())
                        _setattr_if_present(invitation, "reject_reason", _safe_str(reason, "", 1000) or None)
                    _session_add(invitation)
                    _write_audit_event(
                        project, ACTION_INVITATION_REJECTED, actor_user_id=_actor_user_id(actor_context),
                        message="Project invitation rejected.",
                        metadata={
                            "invitation_id": getattr(invitation, "public_id", None),
                            "email": getattr(invitation, "email_normalized", None),
                            "auth_user_id": effective_auth_user_id,
                            "reason": _safe_str(reason),
                        },
                    )
                    _commit_or_flush(commit=commit)
                    return _result(ok=True, code="project_invitation_rejected", message="Einladung wurde abgelehnt.", project=project, invitation=invitation, status_code=200, data={"local_committed": bool(commit)})
                except Exception as exc:
                    _rollback_safely()
                    return _result(ok=False, code="project_invitation_reject_failed", message="Einladung konnte nicht abgelehnt werden.", project=project, invitation=invitation, status_code=500, error=str(exc))
        except TimeoutError:
            return _result(ok=False, code="project_invitation_busy", message="Diese Einladung wird bereits verarbeitet.", status_code=409, data={"retryable": True})

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
        """Accept an invitation using only the server-side current Auth identity.

        Local membership/invitation changes are committed before Chunk access is
        synchronized. A Chunk failure never rolls back the accepted App membership.
        """
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()
        actor_context = get_actor_context(actor_user_id)
        if _context_auth_unavailable(actor_context):
            return _result(ok=False, code=_context_code(actor_context) or "auth_service_unavailable", message="vectoplan-auth ist nicht erreichbar.", status_code=503, data={"auth_unavailable": True})
        if _actor_is_blocked(actor_context):
            return _result(ok=False, code=_context_code(actor_context) or "auth_blocked", message="Der Zugriff ist gesperrt.", status_code=_context_denial_status(actor_context), data={"blocked": True})
        if _actor_is_demo(actor_context):
            return _result(ok=False, code="demo_mode_not_allowed", message="Im Demo-Modus können keine Projekteinladungen angenommen werden.", status_code=403, data={"demo_mode": True})
        if not _safe_bool(actor_context.get("authenticated"), False):
            return _result(ok=False, code="authentication_required", message="Zum Annehmen der Einladung ist Login erforderlich.", status_code=401)
        override_error = _identity_override_error(
            actor_context, auth_user_id=auth_user_id, email=email, local_user_id=local_user_id
        )
        if override_error is not None:
            return override_error
        effective_auth_user_id = _actor_auth_user_id(actor_context)
        effective_email = _actor_email(actor_context)
        actor_local_user_id = _actor_user_id(actor_context)
        if not effective_auth_user_id:
            return _result(ok=False, code="auth_identity_required", message="Zum Annehmen der Einladung ist eine kanonische Auth-User-ID erforderlich.", status_code=401)
        if not actor_local_user_id or not _safe_bool(actor_context.get("persistent"), False):
            return _result(
                ok=False, code="local_user_link_required",
                message="Zum Annehmen der Einladung ist eine lokale AppUser-Verknüpfung erforderlich.",
                status_code=409, data={"auth_user_id": effective_auth_user_id, "no_user_created": True},
            )
        try:
            with _keyed_lock(f"accept:{_safe_str(invitation_id, '', 180)}"):
                invitation = _find_invitation_by_public_id(invitation_id)
                if invitation is None:
                    return _result(ok=False, code="project_invitation_not_found", message="Einladung nicht gefunden.", status_code=404)
                project = resolve_project(getattr(invitation, "project_id", None))
                if project is None:
                    return _result(ok=False, code="project_not_found", message="Projekt nicht gefunden.", invitation=invitation, status_code=404)
                invitation_role, role_error = _strict_invitation_role(getattr(invitation, "role", DEFAULT_INVITATION_ROLE))
                if role_error is not None:
                    role_error.project = project
                    role_error.invitation = invitation
                    return role_error
                try:
                    if hasattr(invitation, "ensure_not_expired") and invitation.ensure_not_expired():
                        _write_audit_event(project, ACTION_INVITATION_EXPIRED, actor_user_id=actor_local_user_id, message="Project invitation expired.", metadata={"invitation_id": getattr(invitation, "public_id", None)})
                        _commit_or_flush(commit=commit)
                        return _result(ok=False, code="project_invitation_expired", message="Diese Einladung ist abgelaufen.", project=project, invitation=invitation, status_code=410)
                except Exception:
                    pass
                if not _verify_invitation_plain_token(invitation, plain_token):
                    return _result(ok=False, code="invalid_invitation_token", message="Der Einladungstoken ist ungültig.", project=project, invitation=invitation, status_code=403)
                if not _invitation_can_accept(invitation, auth_user_id=effective_auth_user_id, email=effective_email):
                    return _result(ok=False, code="project_invitation_identity_mismatch", message="Diese Einladung gehört nicht zur aktuellen Auth-Identität.", project=project, invitation=invitation, status_code=403)
                linked_user = find_linked_app_user(auth_user_id=effective_auth_user_id, email=None)
                linked_user_id = _safe_int(getattr(linked_user, "id", None), None) if linked_user is not None else None
                linked_auth_user_id = _safe_str(getattr(linked_user, "auth_user_id", None), "", 160) if linked_user is not None else ""
                if not linked_user_id or linked_user_id != actor_local_user_id or linked_auth_user_id != effective_auth_user_id:
                    return _result(
                        ok=False, code="local_user_link_mismatch",
                        message="Die lokale AppUser-Verknüpfung passt nicht zur aktuellen Auth-Identität.",
                        project=project, invitation=invitation, status_code=409,
                        data={"auth_user_id": effective_auth_user_id, "no_user_created": True},
                    )
                current_status = _safe_str(getattr(invitation, "status", ""), "", 40).lower()
                existing_membership = _find_membership(_project_id(project), actor_local_user_id)
                if current_status == STATUS_ACCEPTED and existing_membership is not None and _membership_is_active(existing_membership):
                    access_sync = _sync_access_after_invitation_accept(
                        project, actor_auth_user_id=effective_auth_user_id, commit=commit
                    )
                    return _result(
                        ok=True, code="project_invitation_already_accepted",
                        message="Einladung wurde bereits angenommen.", project=project, invitation=invitation,
                        membership=existing_membership, chunk_access_sync=access_sync, status_code=200,
                        data={"idempotent": True, "local_committed": bool(commit)},
                    )
                try:
                    _setattr_if_present(invitation, "role", invitation_role)
                    ok, membership, membership_code = _create_or_update_membership_from_invitation(
                        invitation, local_user_id=actor_local_user_id, actor_context=actor_context
                    )
                    if not ok or membership is None:
                        return _result(ok=False, code=membership_code, message="Projektmitgliedschaft konnte nicht erstellt werden.", project=project, invitation=invitation, status_code=500)
                    if hasattr(invitation, "mark_accepted"):
                        invitation.mark_accepted(
                            accepted_by_user_id=actor_local_user_id,
                            accepted_by_auth_user_id=effective_auth_user_id,
                            membership_id=getattr(membership, "id", None),
                        )
                    else:
                        _setattr_if_present(invitation, "status", STATUS_ACCEPTED)
                        _setattr_if_present(invitation, "accepted_at", utcnow())
                        _setattr_if_present(invitation, "accepted_by_user_id", actor_local_user_id)
                        _setattr_if_present(invitation, "accepted_by_auth_user_id", effective_auth_user_id)
                        _setattr_if_present(invitation, "membership_id", getattr(membership, "id", None))
                    _session_add(membership)
                    _session_add(invitation)
                    _write_audit_event(
                        project, ACTION_INVITATION_ACCEPTED, actor_user_id=actor_local_user_id,
                        message="Project invitation accepted.",
                        metadata={
                            "invitation_id": getattr(invitation, "public_id", None),
                            "email": getattr(invitation, "email_normalized", None),
                            "role": invitation_role, "membership_code": membership_code,
                            "membership_id": getattr(membership, "id", None),
                            "auth_user_id": effective_auth_user_id,
                        },
                    )
                    _commit_or_flush(commit=commit)
                except Exception as exc:
                    _rollback_safely()
                    _log_exception("accept_invitation local persistence failed", invitation_id=_safe_str(invitation_id))
                    return _result(ok=False, code="project_invitation_accept_failed", message="Einladung konnte nicht angenommen werden.", project=project, invitation=invitation, status_code=500, error=str(exc))
                access_sync = _sync_access_after_invitation_accept(
                    project, actor_auth_user_id=effective_auth_user_id, commit=commit
                )
                sync_ok = _safe_bool(access_sync.get("ok"), False)
                _write_audit_event(
                    project, ACTION_INVITATION_ACCESS_SYNCED if sync_ok else ACTION_INVITATION_ACCESS_SYNC_FAILED,
                    actor_user_id=actor_local_user_id,
                    message="Chunk project access synchronized after invitation acceptance." if sync_ok else "Chunk project access synchronization pending after invitation acceptance.",
                    metadata={
                        "invitation_id": getattr(invitation, "public_id", None),
                        "membership_id": getattr(membership, "id", None),
                        "chunk_access_sync": _redact_sensitive(access_sync),
                        "local_membership_committed": bool(commit),
                    },
                )
                if commit:
                    try:
                        _commit_or_flush(commit=True)
                    except Exception:
                        _rollback_safely()
                if sync_ok or not commit:
                    return _result(
                        ok=True,
                        code="project_invitation_accepted" if sync_ok else "project_invitation_accepted_chunk_sync_deferred",
                        message="Einladung wurde angenommen." if sync_ok else "Einladung wurde lokal angenommen; die Chunk-Synchronisierung ist bis nach dem Commit zurückgestellt.",
                        project=project, invitation=invitation, membership=membership,
                        chunk_access_sync=access_sync, status_code=200,
                        data={
                            "membership_code": membership_code,
                            "local_committed": bool(commit),
                            "invitation_accepted": True,
                            "chunk_access_sync_deferred": not commit,
                        },
                    )
                sync_status = _safe_int(access_sync.get("statusCode") or access_sync.get("status_code"), 502) or 502
                required = _access_sync_required()
                return _result(
                    ok=not required,
                    code="project_invitation_accepted_chunk_sync_failed" if required else "project_invitation_accepted_chunk_sync_pending",
                    message="Einladung wurde angenommen, aber der Chunk-Zugriff konnte noch nicht synchronisiert werden.",
                    project=project, invitation=invitation, membership=membership,
                    chunk_access_sync=access_sync, status_code=sync_status if required else 202,
                    error=_safe_str(access_sync.get("message") or access_sync.get("code"), "Chunk access synchronization pending.", 2000) if required else None,
                    data={
                        "membership_code": membership_code,
                        "local_committed": bool(commit),
                        "invitation_accepted": True,
                        "membership_persisted": bool(commit),
                        "repair_required": _safe_bool(access_sync.get("repairRequired"), True),
                        "retryable": _safe_bool(access_sync.get("retryable"), True),
                    },
                )
        except TimeoutError:
            return _result(ok=False, code="project_invitation_busy", message="Diese Einladung wird bereits verarbeitet.", status_code=409, data={"retryable": True})

    def expire_pending(self, project_or_id: Any = None, commit: bool = True) -> ProjectInvitationServiceResult:
        if ProjectInvitation is None or db is None:
            return _model_unavailable_result()

        project = resolve_project(project_or_id) if project_or_id is not None else None

        try:
            if hasattr(ProjectInvitation, "expire_old_pending"):
                changed = ProjectInvitation.expire_old_pending(_project_id(project) if project is not None else None)
            else:
                changed = 0

            if changed:
                if project is not None:
                    _write_audit_event(project, ACTION_INVITATION_EXPIRED, actor_user_id=None, message="Expired project invitations marked.", metadata={"changed": changed})
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
        return {"ok": False, "code": "project_invitation_service_status_failed", "error": str(exc)}


def list_project_invitations(project_or_id: Any, actor_user_id: Any = None, include_terminal: bool = True, include_private: bool = False) -> Dict[str, Any]:
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
    return result.to_dict(include_private=include_token_in_result, include_raw=include_token_in_result)


def revoke_project_invitation(project_or_id: Any, invitation_id: Any, actor_user_id: Any = None, reason: Any = None, commit: bool = True) -> Dict[str, Any]:
    result = get_project_invitation_service().revoke_invitation(
        project_or_id=project_or_id,
        invitation_id=invitation_id,
        actor_user_id=actor_user_id,
        reason=reason,
        commit=commit,
    )
    return result.to_dict(include_private=True, include_raw=False)


def reject_project_invitation(invitation_id: Any, auth_user_id: Any = None, email: Any = None, reason: Any = None, commit: bool = True) -> Dict[str, Any]:
    result = get_project_invitation_service().reject_invitation(invitation_id=invitation_id, auth_user_id=auth_user_id, email=email, reason=reason, commit=commit)
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


def expire_project_invitations(project_or_id: Any = None, commit: bool = True) -> Dict[str, Any]:
    result = get_project_invitation_service().expire_pending(project_or_id=project_or_id, commit=commit)
    return result.to_dict(include_private=True, include_raw=False)


__all__ = [
    "ACTION_INVITATION_ACCEPTED",
    "ACTION_INVITATION_CREATED",
    "ACTION_INVITATION_DISPATCHED",
    "ACTION_INVITATION_EXPIRED",
    "ACTION_INVITATION_ACCESS_SYNCED",
    "ACTION_INVITATION_ACCESS_SYNC_FAILED",
    "ACTION_INVITATION_FAILED",
    "ACTION_INVITATION_REJECTED",
    "ACTION_INVITATION_REVOKED",
    "ProjectInvitationService",
    "ProjectInvitationServiceResult",
    "accept_project_invitation",
    "build_invitation_url",
    "clear_project_invitation_identity_cache",
    "dispatch_project_invitation_identity",
    "expire_project_invitations",
    "find_linked_app_user",
    "get_actor_context",
    "get_auth_identity_status",
    "get_project_invitation_service",
    "get_project_invitation_service_status",
    "invite_registered_email_to_project",
    "list_project_invitations",
    "reject_project_invitation",
    "require_registered_email_identity",
    "resolve_project",
    "revoke_project_invitation",
]