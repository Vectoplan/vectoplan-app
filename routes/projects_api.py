# services/vectoplan-app/routes/projects_api.py
from __future__ import annotations

"""
VECTOPLAN projects API.

Zweck:
- JSON-API für die Projektverwaltung in vectoplan-app.
- Hält Routen dünn und delegiert Fachlogik an services/.
- Unterstützt:
    - Projekt erstellen/bearbeiten/löschen
    - eine sichtbare Adressbox im Projektformular
    - Sichtbarkeit private/unlisted/public
    - Team-/Rollenverwaltung für persistente Projekte
    - Einladungen per registrierter E-Mail für persistente Projekte
    - Veröffentlichte Workspace-Reiter für persistente Projekte
    - Embed-/Publication-Policy für persistente Projekte
    - Chunk-Referenzen, Earth/Flat-Provisionierungsstatus und Retry/Reconcile
    - Chunk-Project-Access-Synchronisation für owner/admin/editor/viewer
    - Viewer als read-only Kontext
    - Demo-/Auth-Kontext

Wichtige Architekturregeln:
- vectoplan-app erzeugt keine echten Benutzeraccounts.
- vectoplan-app erzeugt keinen Default-User.
- Kein Fallback auf AppUser id=1.
- Login, Registrierung, Account, Plan, Entitlements und Blocked/Banned liegen in vectoplan-auth.
- Auth-Service-Ausfall ist 503 und kein echter User-Ban.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Veröffentlichungen und Projektfrontend.
- Demo-Guests bekommen höchstens ein temporäres Demo-Projekt, aber nur wenn vectoplan-auth Demo erlaubt.
- Demo darf keine Team-/Invitation-/Publication-/Admin-/Policy-Aktionen ausführen.
- Diese API speichert keine Chunk-Daten.
- Diese API speichert keine 3D-Welt-Wahrheit.
- Diese API speichert keine 2D-Geometrie.
- Diese API speichert keine LV-Fachdaten.
- Systemreferenzen sind API-/Admin-Daten und nicht normales Projektformular.
"""

import uuid
from typing import Any, Dict, Mapping, Optional, Tuple

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.wrappers import Response


# ─────────────────────────────────────────────────────────────
# Robust service imports
# ─────────────────────────────────────────────────────────────

try:
    from services.current_user import (
        get_current_user_context,
        get_current_user_id_optional,
        get_current_user_status,
    )
except Exception:  # pragma: no cover
    get_current_user_context = None  # type: ignore
    get_current_user_id_optional = None  # type: ignore
    get_current_user_status = None  # type: ignore


try:
    from services.auth_dependency_service import get_auth_dependency_status
except Exception:  # pragma: no cover
    get_auth_dependency_status = None  # type: ignore


try:
    from services.auth_requirements import get_auth_requirements_status
except Exception:  # pragma: no cover
    get_auth_requirements_status = None  # type: ignore


try:
    from services.project_permissions import (
        PERMISSION_EMBED,
        PERMISSION_MANAGE,
        PERMISSION_VIEW,
        PermissionDenied,
        can_manage_project,
        get_permission_service_status,
        normalize_role,
        require_project_permission,
        serialize_project_permissions,
    )
except Exception:  # pragma: no cover
    PERMISSION_VIEW = "view"
    PERMISSION_MANAGE = "manage"
    PERMISSION_EMBED = "embed"

    class PermissionDenied(PermissionError):  # type: ignore
        def __init__(
            self,
            message: str = "permission denied",
            *,
            code: str = "permission_denied",
            status_code: int = 403,
            permission: Optional[str] = None,
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
                "error": self.message,
                "message": self.message,
                "code": self.code,
                "status_code": self.status_code,
                "permission": self.permission,
                "project_id": self.project_id,
                "user_id": self.user_id,
            }

    def can_manage_project(*args: Any, **kwargs: Any) -> bool:  # type: ignore
        return False

    def get_permission_service_status() -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "code": "permission_service_unavailable"}

    def normalize_role(value: Any) -> str:  # type: ignore
        return _safe_str(value, "viewer", 40).lower()

    def require_project_permission(*args: Any, **kwargs: Any) -> bool:  # type: ignore
        raise PermissionDenied("permission service unavailable", code="permission_service_unavailable", status_code=503)

    def serialize_project_permissions(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "permissions": {}, "code": "permission_service_unavailable"}


try:
    from services.project_service import (
        create_project_result,
        create_project_version_link,
        delete_project_result,
        ensure_project_chunk_link_result,
        get_or_create_embed_policy,
        get_project_result,
        get_project_service_status,
        list_project_memberships,
        list_project_service_links,
        list_project_versions,
        list_projects_result,
        resolve_project,
        revoke_project_member,
        serialize_project,
        serialize_project_sidebar_item,
        set_project_member_role,
        sync_project_chunk_access_result,
        transfer_project_owner,
        update_project_embed_policy,
        update_project_result,
        upsert_project_service_link,
    )
except Exception:  # pragma: no cover
    create_project_result = None  # type: ignore
    create_project_version_link = None  # type: ignore
    delete_project_result = None  # type: ignore
    ensure_project_chunk_link_result = None  # type: ignore
    get_or_create_embed_policy = None  # type: ignore
    get_project_result = None  # type: ignore
    list_project_memberships = None  # type: ignore
    list_project_service_links = None  # type: ignore
    list_project_versions = None  # type: ignore
    list_projects_result = None  # type: ignore
    resolve_project = None  # type: ignore
    revoke_project_member = None  # type: ignore
    serialize_project = None  # type: ignore
    serialize_project_sidebar_item = None  # type: ignore
    set_project_member_role = None  # type: ignore
    sync_project_chunk_access_result = None  # type: ignore
    transfer_project_owner = None  # type: ignore
    update_project_embed_policy = None  # type: ignore
    update_project_result = None  # type: ignore
    upsert_project_service_link = None  # type: ignore

    def get_project_service_status() -> Dict[str, Any]:  # type: ignore
        return {"ok": False, "code": "project_service_unavailable"}


try:
    from services.project_invitation_service import (
        accept_project_invitation,
        expire_project_invitations,
        get_project_invitation_service_status,
        invite_registered_email_to_project,
        list_project_invitations,
        reject_project_invitation,
        revoke_project_invitation,
    )
except Exception:  # pragma: no cover
    accept_project_invitation = None  # type: ignore
    expire_project_invitations = None  # type: ignore
    get_project_invitation_service_status = None  # type: ignore
    invite_registered_email_to_project = None  # type: ignore
    list_project_invitations = None  # type: ignore
    reject_project_invitation = None  # type: ignore
    revoke_project_invitation = None  # type: ignore


try:
    from services.project_publication_service import (
        can_access_project_workspace,
        get_project_publication,
        get_project_publication_service_status,
        normalize_workspace_key,
        update_project_publication,
    )
except Exception:  # pragma: no cover
    can_access_project_workspace = None  # type: ignore
    get_project_publication = None  # type: ignore
    get_project_publication_service_status = None  # type: ignore
    normalize_workspace_key = None  # type: ignore
    update_project_publication = None  # type: ignore


try:
    from services.project_chunk_access_sync_service import (
        get_project_chunk_access_sync_service_status,
        serialize_project_chunk_access_sync_status,
    )
except Exception:  # pragma: no cover
    get_project_chunk_access_sync_service_status = None  # type: ignore
    serialize_project_chunk_access_sync_status = None  # type: ignore


try:
    from config import get_project_chunk_config_status
except Exception:  # pragma: no cover
    get_project_chunk_config_status = None  # type: ignore


bp = Blueprint("projects_api", __name__)

projects_api_bp = bp
project_api_bp = bp


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()
        if not text:
            text = default
        if max_len > 0 and len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return int(text)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable", "active", "ok"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable", "inactive", "error", "failed"}:
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
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        return []
    except Exception:
        return []


def _request_json(default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = dict(default or {})

    try:
        data = request.get_json(silent=True)

        if isinstance(data, dict):
            return data

        if request.form:
            return dict(request.form.items())

        return fallback
    except Exception:
        return fallback


def _request_bool(name: str, default: bool = False) -> bool:
    try:
        if name in request.args:
            return _safe_bool(request.args.get(name), default)

        if name in request.form:
            return _safe_bool(request.form.get(name), default)

        data = request.get_json(silent=True)
        if isinstance(data, dict) and name in data:
            return _safe_bool(data.get(name), default)

        return default
    except Exception:
        return default


def _request_int(name: str, default: int = 0) -> int:
    try:
        if name in request.args:
            return _safe_int(request.args.get(name), default)

        if name in request.form:
            return _safe_int(request.form.get(name), default)

        data = request.get_json(silent=True)
        if isinstance(data, dict) and name in data:
            return _safe_int(data.get(name), default)

        return default
    except Exception:
        return default


def _request_str(name: str, default: str = "", max_len: int = 240) -> str:
    try:
        if name in request.args:
            return _safe_str(request.args.get(name), default, max_len)

        if name in request.form:
            return _safe_str(request.form.get(name), default, max_len)

        data = request.get_json(silent=True)
        if isinstance(data, dict) and name in data:
            return _safe_str(data.get(name), default, max_len)

        return default
    except Exception:
        return default


def _config_bool(name: str, default: bool = False) -> bool:
    try:
        return _safe_bool(current_app.config.get(name, default), default)
    except Exception:
        return default


def _config_str(name: str, default: str = "", max_len: int = 4000) -> str:
    try:
        return _safe_str(current_app.config.get(name, default), default, max_len)
    except Exception:
        return default


def _log_warning(message: str, *args: Any) -> None:
    try:
        current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        if exc is not None:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
        else:
            current_app.logger.exception(message)
    except Exception:
        pass


def _request_id() -> str:
    """Return one stable request/correlation id for the current request."""
    try:
        existing = _safe_str(getattr(g, "vectoplan_request_id", None), "", 160)
        if existing:
            return existing
    except Exception:
        pass

    try:
        value = _safe_str(
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Vectoplan-Request-Id")
            or request.headers.get("X-Correlation-ID"),
            "",
            160,
        )
    except Exception:
        value = ""

    value = value or f"req_{uuid.uuid4().hex}"
    try:
        g.vectoplan_request_id = value
    except Exception:
        pass
    return value


def _project_access_sync_payload(project: Any) -> Dict[str, Any]:
    """Serialize local App->Chunk access-sync state without exposing secrets."""
    try:
        if callable(serialize_project_chunk_access_sync_status):
            return _safe_dict(serialize_project_chunk_access_sync_status(project))
    except Exception as exc:
        _log_warning("project chunk access status serialization failed: %s", exc.__class__.__name__)

    try:
        metadata = _safe_dict(getattr(project, "metadata_json", None))
        chunk_meta = _safe_dict(metadata.get("chunk"))
        access_sync = _safe_dict(chunk_meta.get("accessSync") or chunk_meta.get("access_sync"))
        status = _safe_str(
            getattr(project, "chunk_access_sync_status", None)
            or access_sync.get("status"),
            "pending",
            80,
        )
        return {
            "enabled": _config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True),
            "status": status,
            "ready": status == "ready",
            "repairRequired": status == "repair_required",
            "chunkProjectId": getattr(project, "chunk_project_id", None),
            "ownerAuthUserId": getattr(project, "auth_owner_user_id", None),
        }
    except Exception:
        return {
            "enabled": _config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True),
            "status": "unknown",
            "ready": False,
        }


def _project_read_only(project: Any, user_id: Optional[int]) -> bool:
    """Viewer/public/demo contexts are read-only; editors and managers are not."""
    try:
        access = _safe_dict(serialize_project_permissions(project, user_id=user_id))
        permissions = _safe_dict(access.get("permissions"))
        can_edit = _safe_bool(access.get("can_edit", permissions.get("edit")), False)
        return not can_edit
    except Exception:
        return True


def _sanitize_project_payload_for_route(
    payload: Mapping[str, Any],
    *,
    include_private: bool,
) -> Dict[str, Any]:
    """Remove internal identities, raw geocoder data and service metadata for viewers."""
    result = _safe_dict(payload)
    if include_private:
        return result

    for key in (
        "auth_owner_user_id",
        "auth_account_id",
        "settings",
        "metadata",
        "metadata_json",
        "service_refs",
        "serviceRefs",
        "artifact_refs",
        "artifactRefs",
        "geocode_raw",
        "chunk_last_error",
        "chunkLastError",
        "demo_client_identity_id",
        "demo_session_id",
        "deleted_by_user_id",
        "archived_by_user_id",
        "transferred_from_user_id",
    ):
        result.pop(key, None)

    chunk = _safe_dict(result.get("chunk"))
    chunk.pop("error", None)
    provisioning = _safe_dict(chunk.get("provisioning"))
    for key in ("requestPayload", "request_payload", "raw", "response", "errorDetails"):
        provisioning.pop(key, None)
    if provisioning:
        chunk["provisioning"] = provisioning
    result["chunk"] = chunk
    return result


def _identity_override_error(data: Mapping[str, Any]) -> Optional[Tuple[Any, int]]:
    """Reject caller-controlled identity overrides on invitation accept/reject routes."""
    payload = _safe_dict(data)
    current_auth_user_id = _current_auth_user_id()
    current_local_user_id = _current_user_id_optional()
    current_email = _current_email()

    supplied_auth = _safe_str(payload.get("auth_user_id") or payload.get("authUserId"), "", 160)
    supplied_local = _safe_int(payload.get("local_user_id") or payload.get("localUserId"), 0)
    supplied_email = _safe_str(payload.get("email"), "", 320).lower()

    if supplied_auth and current_auth_user_id and supplied_auth != current_auth_user_id:
        return _json_error(
            "auth_user_id darf nicht für einen anderen Benutzer überschrieben werden.",
            403,
            code="identity_override_denied",
        )
    if supplied_local and current_local_user_id and supplied_local != current_local_user_id:
        return _json_error(
            "local_user_id darf nicht für einen anderen Benutzer überschrieben werden.",
            403,
            code="identity_override_denied",
        )
    if supplied_email and current_email and supplied_email != current_email.lower():
        return _json_error(
            "E-Mail-Identität stimmt nicht mit dem aktuellen Auth-Kontext überein.",
            403,
            code="identity_override_denied",
        )
    return None


# ─────────────────────────────────────────────────────────────
# Auth context helpers
# ─────────────────────────────────────────────────────────────

AUTH_UNAVAILABLE_CODES = {
    "auth_unavailable",
    "auth_service_unavailable",
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


def _current_user_context_dict(*, ensure: bool = False) -> Dict[str, Any]:
    try:
        if get_current_user_context is None:
            return {
                "user_id": None,
                "id": None,
                "authenticated": False,
                "demo_mode": False,
                "persistent": False,
                "blocked": True,
                "auth_unavailable": True,
                "access_blocked": True,
                "user_blocked": False,
                "blocked_reason": "current_user_service_unavailable",
                "blocked_kind": "auth_unavailable",
                "denial_status_code": 503,
                "source": "route_fallback_auth_unavailable",
            }

        context = get_current_user_context(ensure=ensure)
        if hasattr(context, "to_dict"):
            return _safe_dict(context.to_dict())
        return _safe_dict(context)
    except Exception:
        return {
            "user_id": None,
            "id": None,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "auth_unavailable": True,
            "access_blocked": True,
            "user_blocked": False,
            "blocked_reason": "current_user_context_unavailable",
            "blocked_kind": "auth_unavailable",
            "denial_status_code": 503,
            "source": "route_fallback_auth_unavailable",
        }


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


def _context_auth_unavailable(context: Optional[Mapping[str, Any]] = None) -> bool:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)
    code = _context_code(ctx)
    blocked_kind = _safe_str(ctx.get("blocked_kind") or ctx.get("blockedKind"), "", 80).lower()
    status = _safe_int(ctx.get("denial_status_code") or ctx.get("denialStatusCode") or ctx.get("status_code"), 0)

    return bool(
        _safe_bool(ctx.get("auth_unavailable") or ctx.get("authUnavailable"), False)
        or blocked_kind == "auth_unavailable"
        or code in AUTH_UNAVAILABLE_CODES
        or status == 503
    )


def _context_user_blocked(context: Optional[Mapping[str, Any]] = None) -> bool:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)
    code = _context_code(ctx)
    blocked_kind = _safe_str(ctx.get("blocked_kind") or ctx.get("blockedKind"), "", 80).lower()

    return bool(
        not _context_auth_unavailable(ctx)
        and (
            _safe_bool(ctx.get("user_blocked") or ctx.get("userBlocked"), False)
            or blocked_kind == "user_blocked"
            or code in USER_BLOCKED_CODES
        )
    )


def _context_access_blocked(context: Optional[Mapping[str, Any]] = None) -> bool:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)

    return bool(
        _context_auth_unavailable(ctx)
        or _context_user_blocked(ctx)
        or _safe_bool(ctx.get("access_blocked") or ctx.get("accessBlocked"), False)
        or _safe_bool(ctx.get("blocked"), False)
    )


def _context_denial_status(context: Optional[Mapping[str, Any]] = None) -> int:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)

    if _context_auth_unavailable(ctx):
        return 503

    status = _safe_int(ctx.get("denial_status_code") or ctx.get("denialStatusCode") or ctx.get("status_code"), 0)
    if status > 0:
        return status

    if _context_user_blocked(ctx) or _context_access_blocked(ctx):
        return 403

    if not _safe_bool(ctx.get("authenticated") or ctx.get("is_authenticated") or ctx.get("isAuthenticated"), False):
        return 401

    return 403


def _context_is_demo(context: Optional[Mapping[str, Any]] = None) -> bool:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)

    return bool(
        not _context_auth_unavailable(ctx)
        and not _context_access_blocked(ctx)
        and _safe_bool(ctx.get("demo_mode") or ctx.get("is_demo") or ctx.get("demoMode"), False)
        and _safe_bool(ctx.get("can_demo") or ctx.get("canDemo"), False)
    )


def _context_is_persistent(context: Optional[Mapping[str, Any]] = None) -> bool:
    ctx = _safe_dict(context) if context is not None else _current_user_context_dict(ensure=False)

    return bool(
        not _context_auth_unavailable(ctx)
        and not _context_access_blocked(ctx)
        and _safe_bool(ctx.get("authenticated") or ctx.get("is_authenticated") or ctx.get("isAuthenticated"), False)
        and _safe_bool(ctx.get("persistent"), False)
        and _safe_int(ctx.get("user_id") or ctx.get("userId") or ctx.get("id"), 0) > 0
    )


def _current_user_id_optional() -> Optional[int]:
    try:
        context = _current_user_context_dict(ensure=False)

        if not _context_is_persistent(context):
            return None

        if get_current_user_id_optional is None:
            parsed = _safe_int(context.get("user_id") or context.get("userId") or context.get("id"), 0)
            return parsed if parsed > 0 else None

        value = get_current_user_id_optional()
        parsed = _safe_int(value, 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _current_auth_user_id() -> Optional[str]:
    try:
        context = _current_user_context_dict(ensure=False)
        user = _safe_dict(context.get("user"))
        subject = _safe_dict(context.get("subject"))
        value = _safe_str(
            context.get("auth_user_id")
            or context.get("authUserId")
            or user.get("id")
            or user.get("user_id")
            or subject.get("id")
            or context.get("sub"),
            "",
            160,
        )
        return value or None
    except Exception:
        return None


def _current_email() -> Optional[str]:
    try:
        context = _current_user_context_dict(ensure=False)
        user = _safe_dict(context.get("user"))
        subject = _safe_dict(context.get("subject"))
        value = _safe_str(
            context.get("email")
            or context.get("auth_email")
            or context.get("authEmail")
            or user.get("email")
            or subject.get("email"),
            "",
            320,
        ).lower()
        return value or None
    except Exception:
        return None


def _is_demo_mode() -> bool:
    return _context_is_demo()


def _is_blocked() -> bool:
    return _context_access_blocked()


def _make_permission_denied(
    message: str,
    *,
    code: str,
    status_code: int,
    permission: str,
    project_id: Any = None,
    user_id: Any = None,
) -> PermissionDenied:
    try:
        return PermissionDenied(
            message,
            code=code,
            status_code=status_code,
            permission=permission,
            project_id=project_id,
            user_id=user_id,
        )
    except TypeError:
        exc = PermissionDenied(message)  # type: ignore
        try:
            setattr(exc, "code", code)
            setattr(exc, "status_code", status_code)
            setattr(exc, "permission", permission)
            setattr(exc, "project_id", project_id)
            setattr(exc, "user_id", user_id)
        except Exception:
            pass
        return exc


def _raise_if_blocked(permission: str = PERMISSION_VIEW) -> None:
    context = _current_user_context_dict(ensure=False)

    if _context_auth_unavailable(context):
        raise _make_permission_denied(
            "vectoplan-auth ist nicht erreichbar.",
            code=_context_code(context) or "auth_service_unavailable",
            status_code=503,
            permission=permission,
            project_id=None,
            user_id=None,
        )

    if _context_user_blocked(context):
        raise _make_permission_denied(
            "Dieser Zugang ist gesperrt.",
            code=_context_code(context) or "auth_blocked",
            status_code=403,
            permission=permission,
            project_id=None,
            user_id=None,
        )

    if _context_access_blocked(context):
        raise _make_permission_denied(
            "Der Zugriff ist gesperrt.",
            code=_context_code(context) or "access_blocked",
            status_code=_context_denial_status(context),
            permission=permission,
            project_id=None,
            user_id=None,
        )


def _require_auth_available() -> Optional[Any]:
    context = _current_user_context_dict(ensure=False)

    if _context_auth_unavailable(context):
        return _json_error(
            "vectoplan-auth ist nicht erreichbar.",
            503,
            code=_context_code(context) or "auth_service_unavailable",
            extra={"auth": context},
        )

    if _context_user_blocked(context):
        return _json_error(
            "Dieser Zugang ist gesperrt.",
            403,
            code=_context_code(context) or "auth_blocked",
            extra={"auth": context},
        )

    if _context_access_blocked(context):
        return _json_error(
            "Der Zugriff ist gesperrt.",
            _context_denial_status(context),
            code=_context_code(context) or "access_blocked",
            extra={"auth": context},
        )

    return None


def _require_persistent_context() -> Optional[Any]:
    auth_error = _require_auth_available()
    if auth_error is not None:
        return auth_error

    context = _current_user_context_dict(ensure=False)
    if not _context_is_persistent(context):
        return _json_error(
            "Persistenter authentifizierter User erforderlich.",
            401 if not _safe_bool(context.get("authenticated") or context.get("is_authenticated"), False) else 403,
            code="persistent_user_required",
            extra={"auth": context},
        )

    return None


def _project_is_demo(project: Any) -> bool:
    try:
        if project is None:
            return False

        if _safe_bool(getattr(project, "is_demo", False), False):
            return True

        if _safe_str(getattr(project, "project_scope", ""), "", 40).lower() == "demo":
            return True

        metadata = _safe_dict(getattr(project, "metadata_json", None))
        demo_meta = _safe_dict(metadata.get("vectoplan_demo"))
        return _safe_bool(demo_meta.get("enabled"), False)
    except Exception:
        return False


def _raise_if_demo_restricted(
    project: Any,
    permission: str = PERMISSION_MANAGE,
    message: str = "Diese Aktion ist im Demo-Modus nicht erlaubt.",
) -> None:
    if _project_is_demo(project) or _is_demo_mode():
        raise _make_permission_denied(
            message,
            code="demo_action_not_allowed",
            status_code=403,
            permission=permission,
            project_id=getattr(project, "public_id", None) or getattr(project, "id", None),
            user_id=None,
        )


def _require_project_permission_checked(
    project: Any,
    permission: str,
    user_id: Optional[int],
    *,
    allow_public_view: bool = False,
) -> Any:
    result = require_project_permission(
        project,
        permission,
        user_id,
        allow_public_view=allow_public_view,
    )

    if result is False:
        raise _make_permission_denied(
            "permission denied",
            code="project_permission_denied",
            status_code=403,
            permission=permission,
            project_id=getattr(project, "public_id", None) or getattr(project, "id", None),
            user_id=user_id,
        )

    data = _safe_dict(result)
    if data and data.get("ok") is False:
        raise _make_permission_denied(
            _safe_str(data.get("message") or data.get("error"), "permission denied", 500),
            code=_safe_str(data.get("code"), "project_permission_denied", 120),
            status_code=_safe_int(data.get("status_code"), 403),
            permission=permission,
            project_id=getattr(project, "public_id", None) or getattr(project, "id", None),
            user_id=user_id,
        )

    return result


# ─────────────────────────────────────────────────────────────
# Serialization helpers
# ─────────────────────────────────────────────────────────────

def _serialize_model(item: Any, *, include_private: bool = False) -> Dict[str, Any]:
    try:
        if item is None:
            return {}

        if hasattr(item, "to_dict"):
            try:
                return item.to_dict(include_private=include_private)
            except TypeError:
                try:
                    return item.to_dict(include_secret=include_private)
                except TypeError:
                    return item.to_dict()

        return {
            "id": getattr(item, "id", None),
            "public_id": getattr(item, "public_id", None),
            "status": getattr(item, "status", None),
        }
    except Exception:
        return {}


def _extract_chunk_from_project_payload(project_payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = _safe_dict(project_payload)
        chunk = _safe_dict(payload.get("chunk"))

        chunk_project_id = (
            chunk.get("chunk_project_id")
            or chunk.get("chunkProjectId")
            or payload.get("chunk_project_id")
            or payload.get("chunkProjectId")
        )

        chunk_universe_id = (
            chunk.get("chunk_universe_id")
            or chunk.get("chunkUniverseId")
            or payload.get("chunk_universe_id")
            or payload.get("chunkUniverseId")
        )

        chunk_world_id = (
            chunk.get("chunk_world_id")
            or chunk.get("chunkWorldId")
            or payload.get("chunk_world_id")
            or payload.get("chunkWorldId")
        )

        status = (
            chunk.get("status")
            or payload.get("chunk_status")
            or payload.get("chunkStatus")
            or ("ready" if chunk_project_id and chunk_world_id else "pending")
        )

        route_hints = (
            _safe_dict(chunk.get("route_hints"))
            or _safe_dict(chunk.get("routeHints"))
            or _safe_dict(payload.get("chunk_route_hints"))
            or _safe_dict(payload.get("chunkRouteHints"))
        )

        ready = _safe_bool(
            chunk.get("ready")
            if "ready" in chunk
            else payload.get("chunk_ready")
            if "chunk_ready" in payload
            else payload.get("chunkReady"),
            bool(chunk_project_id and chunk_world_id and status == "ready"),
        )

        provisioning = _safe_dict(
            chunk.get("provisioning")
            or payload.get("chunk_provisioning")
            or payload.get("chunkProvisioning")
        )
        access_sync = _safe_dict(
            chunk.get("access_sync")
            or chunk.get("accessSync")
            or payload.get("chunk_access_sync")
            or payload.get("chunkAccessSync")
        )

        return {
            "status": status,
            "ready": ready,
            "chunk_project_id": chunk_project_id,
            "chunkProjectId": chunk_project_id,
            "chunk_universe_id": chunk_universe_id,
            "chunkUniverseId": chunk_universe_id,
            "chunk_world_id": chunk_world_id,
            "chunkWorldId": chunk_world_id,
            "route_hints": route_hints,
            "routeHints": route_hints,
            "provisioning": provisioning,
            "access_sync": access_sync,
            "accessSync": access_sync,
            "worldTemplateRequested": provisioning.get("requestedWorldTemplate")
            or payload.get("chunkWorldTemplateRequested"),
            "worldTemplateEffective": provisioning.get("effectiveWorldTemplate")
            or payload.get("chunkWorldTemplateEffective"),
            "fallbackUsed": _safe_bool(
                provisioning.get("fallbackUsed")
                if "fallbackUsed" in provisioning
                else payload.get("chunkFallbackUsed"),
                False,
            ),
            "fallbackReason": provisioning.get("fallbackReason")
            or payload.get("chunkFallbackReason"),
            "error": _safe_dict(chunk.get("error") or payload.get("chunk_last_error") or payload.get("chunkLastError")),
        }

    except Exception:
        return {
            "status": "error",
            "ready": False,
            "chunk_project_id": None,
            "chunkProjectId": None,
            "chunk_universe_id": None,
            "chunkUniverseId": None,
            "chunk_world_id": None,
            "chunkWorldId": None,
            "route_hints": {},
            "routeHints": {},
            "error": {},
        }


def _serialize_project_chunk_payload(
    project: Any,
    *,
    user_id: Optional[int] = None,
    include_private: bool = False,
) -> Dict[str, Any]:
    try:
        project_payload = serialize_project(
            project,
            user_id=user_id,
            include_permissions=True,
            include_service_links=include_private,
            include_publication=True,
        )
        project_payload = _sanitize_project_payload_for_route(
            project_payload,
            include_private=include_private,
        )

        chunk = _extract_chunk_from_project_payload(project_payload)

        access_sync = _project_access_sync_payload(project)
        read_only = _project_read_only(project, user_id)

        result = {
            "ok": True,
            "project_id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "appProjectPublicId": getattr(project, "public_id", None),
            "is_demo": _project_is_demo(project),
            "isDemo": _project_is_demo(project),
            "read_only": read_only,
            "readOnly": read_only,
            "chunk": chunk,
            "chunk_ready": chunk.get("ready"),
            "chunkReady": chunk.get("ready"),
            "chunk_status": chunk.get("status"),
            "chunkStatus": chunk.get("status"),
            "chunk_project_id": chunk.get("chunk_project_id"),
            "chunkProjectId": chunk.get("chunk_project_id"),
            "chunk_universe_id": chunk.get("chunk_universe_id"),
            "chunkUniverseId": chunk.get("chunk_universe_id"),
            "chunk_world_id": chunk.get("chunk_world_id"),
            "chunkWorldId": chunk.get("chunk_world_id"),
            "chunk_provisioning": chunk.get("provisioning") or {},
            "chunkProvisioning": chunk.get("provisioning") or {},
            "chunk_access_sync": access_sync,
            "chunkAccessSync": access_sync,
            "project": project_payload,
        }

        if include_private:
            result["service_links"] = list_project_service_links(project) if callable(list_project_service_links) else []
            result["chunkInternalUrlConfigured"] = bool(_config_str("VECTOPLAN_CHUNK_INTERNAL_URL", ""))
            result["chunkPublicUrl"] = _config_str("VECTOPLAN_CHUNK_PUBLIC_URL", "")

        return result

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "code": "chunk_payload_serialization_failed",
        }


def _normalize_service_link_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(data)

    service_name = (
        payload.get("service_name")
        or payload.get("serviceName")
        or payload.get("service")
    )

    resource_kind = (
        payload.get("resource_kind")
        or payload.get("resourceKind")
        or payload.get("resource_type")
        or payload.get("resourceType")
        or payload.get("kind")
        or payload.get("type")
    )

    external_id = (
        payload.get("external_id")
        or payload.get("externalId")
        or payload.get("resource_id")
        or payload.get("resourceId")
    )

    external_project_id = (
        payload.get("external_project_id")
        or payload.get("externalProjectId")
        or payload.get("chunk_project_id")
        or payload.get("chunkProjectId")
    )

    external_universe_id = (
        payload.get("external_universe_id")
        or payload.get("externalUniverseId")
        or payload.get("chunk_universe_id")
        or payload.get("chunkUniverseId")
        or payload.get("universe_id")
        or payload.get("universeId")
    )

    external_world_id = (
        payload.get("external_world_id")
        or payload.get("externalWorldId")
        or payload.get("chunk_world_id")
        or payload.get("chunkWorldId")
        or payload.get("world_id")
        or payload.get("worldId")
    )

    external_url = (
        payload.get("external_url")
        or payload.get("externalUrl")
        or payload.get("public_url")
        or payload.get("publicUrl")
        or payload.get("browser_url")
        or payload.get("browserUrl")
        or payload.get("url")
        or payload.get("href")
    )

    route_hints = (
        _safe_dict(payload.get("route_hints"))
        or _safe_dict(payload.get("routeHints"))
        or _safe_dict(payload.get("routes"))
    )

    metadata = _safe_dict(payload.get("metadata") or payload.get("meta"))

    if external_universe_id:
        metadata.setdefault("external_universe_id", external_universe_id)
        metadata.setdefault("chunk_universe_id", external_universe_id)

    if route_hints:
        metadata.setdefault("route_hints", route_hints)

    return {
        "service_name": service_name,
        "resource_kind": resource_kind,
        "external_id": external_id,
        "external_project_id": external_project_id,
        "external_universe_id": external_universe_id,
        "external_world_id": external_world_id,
        "external_url": external_url,
        "status": payload.get("status") or "active",
        "metadata": metadata,
    }


def _normalize_version_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(data)

    return {
        "label": payload.get("label") or payload.get("title"),
        "description": payload.get("description") or payload.get("change_summary") or payload.get("summary"),
        "service_name": payload.get("service_name") or payload.get("serviceName") or payload.get("service"),
        "service_version_id": payload.get("service_version_id") or payload.get("serviceVersionId"),
        "service_snapshot_id": payload.get("service_snapshot_id") or payload.get("serviceSnapshotId"),
        "service_artifact_id": payload.get("service_artifact_id") or payload.get("serviceArtifactId"),
        "kind": payload.get("kind") or payload.get("type"),
        "status": payload.get("status") or "stored",
        "artifact_ref": _safe_dict(payload.get("artifact_ref") or payload.get("artifactRef")),
        "metadata": _safe_dict(payload.get("metadata") or payload.get("meta")),
    }


def _demo_publication_payload(project: Any) -> Dict[str, Any]:
    return {
        "ok": True,
        "project_id": getattr(project, "id", None),
        "public_id": getattr(project, "public_id", None),
        "publication": {
            "visibility": "private",
            "public": False,
            "is_public": False,
            "published": False,
            "published_workspaces": [],
            "reason": "demo_projects_are_not_publishable",
        },
        "access": serialize_project_permissions(project, user_id=None),
    }


# ─────────────────────────────────────────────────────────────
# Publication request/response helpers
# ─────────────────────────────────────────────────────────────

PUBLICATION_ROUTE_WORKSPACES = (
    "project",
    "map",
    "editor3d",
    "cad2d",
    "lv",
    "versions",
)

NEVER_PUBLIC_ROUTE_WORKSPACES = {
    "admin",
    "team",
    "settings",
    "permissions",
    "system",
    "system_refs",
}

PUBLICATION_VISIBILITY_ALIASES = {
    "private": "private",
    "privat": "private",
    "closed": "private",
    "internal": "private",
    "intern": "private",
    "members": "private",
    "member": "private",
    "shared": "private",
    "unlisted": "unlisted",
    "not_listed": "unlisted",
    "notlisted": "unlisted",
    "hidden_link": "unlisted",
    "link": "unlisted",
    "link_shared": "unlisted",
    "share_link": "unlisted",
    "nicht_gelistet": "unlisted",
    "public": "public",
    "open": "public",
    "listed": "public",
    "öffentlich": "public",
    "oeffentlich": "public",
}

PUBLICATION_WORKSPACE_ALIASES = {
    "project": "project",
    "projekt": "project",
    "project_info": "project",
    "projectinfo": "project",
    "info": "project",
    "overview": "project",
    "details": "project",
    "map": "map",
    "maps": "map",
    "karte": "map",
    "openlayer": "map",
    "openlayers": "map",
    "gis": "map",
    "3d": "editor3d",
    "editor": "editor3d",
    "editor3d": "editor3d",
    "editor_3d": "editor3d",
    "viewer": "editor3d",
    "viewer3d": "editor3d",
    "viewer_3d": "editor3d",
    "world": "editor3d",
    "2d": "cad2d",
    "cad": "cad2d",
    "cad2d": "cad2d",
    "cad_2d": "cad2d",
    "plan": "cad2d",
    "plan2d": "cad2d",
    "lv": "lv",
    "boq": "lv",
    "leistungsverzeichnis": "lv",
    "versions": "versions",
    "version": "versions",
    "versionen": "versions",
    "history": "versions",
    "snapshots": "versions",
    "admin": "admin",
    "team": "team",
    "settings": "settings",
    "permissions": "permissions",
    "system": "system",
    "system_refs": "system_refs",
}


def _normalize_publication_visibility_for_route(value: Any, default: str = "private") -> str:
    try:
        text = _safe_str(value, default, 80).strip().lower().replace("-", "_").replace(" ", "_")
        return PUBLICATION_VISIBILITY_ALIASES.get(text, default)
    except Exception:
        return default


def _normalize_publication_workspace_for_route(value: Any, default: str = "") -> str:
    try:
        if callable(normalize_workspace_key):
            try:
                normalized = normalize_workspace_key(value)
                if normalized:
                    return _safe_str(normalized, default, 80)
            except Exception:
                pass

        text = _safe_str(value, default, 120).strip().lower().replace("-", "_").replace(" ", "_")
        if not text:
            return default
        return PUBLICATION_WORKSPACE_ALIASES.get(text, default)
    except Exception:
        return default


def _normalize_publication_workspace_map(value: Any, *, existing: Optional[Mapping[str, Any]] = None) -> Dict[str, bool]:
    result = {key: False for key in PUBLICATION_ROUTE_WORKSPACES}

    try:
        existing_dict = _safe_dict(existing)
        for key in PUBLICATION_ROUTE_WORKSPACES:
            if key in existing_dict:
                result[key] = _safe_bool(existing_dict.get(key), False)

        if value is None:
            return result

        if isinstance(value, Mapping):
            for raw_key, raw_value in value.items():
                key = _normalize_publication_workspace_for_route(raw_key)
                if key in PUBLICATION_ROUTE_WORKSPACES:
                    result[key] = _safe_bool(raw_value, False)
            return result

        items = []
        if isinstance(value, str):
            parts = value.replace(";", ",").replace("|", ",").split(",")
            items = [item.strip() for item in parts if item.strip()]
        else:
            items = _safe_list(value)

        explicit_list_result = {key: False for key in PUBLICATION_ROUTE_WORKSPACES}
        has_explicit_item = False

        for item in items:
            item_payload = _safe_dict(item)
            if item_payload:
                raw_key = (
                    item_payload.get("key")
                    or item_payload.get("workspace")
                    or item_payload.get("name")
                    or item_payload.get("id")
                )
                key = _normalize_publication_workspace_for_route(raw_key)
                if key in PUBLICATION_ROUTE_WORKSPACES:
                    has_explicit_item = True
                    explicit_list_result[key] = _safe_bool(
                        item_payload.get("published")
                        if "published" in item_payload
                        else item_payload.get("enabled")
                        if "enabled" in item_payload
                        else item_payload.get("value")
                        if "value" in item_payload
                        else True,
                        True,
                    )
                continue

            key = _normalize_publication_workspace_for_route(item)
            if key in PUBLICATION_ROUTE_WORKSPACES:
                has_explicit_item = True
                explicit_list_result[key] = True

        return explicit_list_result if has_explicit_item else result

    except Exception:
        return result


def _extract_publication_payload_root(data: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        payload = _safe_dict(data)
        nested = _safe_dict(payload.get("publication"))

        if not nested:
            return payload

        merged = dict(nested)
        for key, value in payload.items():
            if key == "publication":
                continue
            merged[key] = value
        return merged
    except Exception:
        return _safe_dict(data)


def _normalize_publication_update_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Normalisiert Frontend-Varianten für PATCH/PUT /publication.

    Akzeptiert:
    - {visibility, published_workspaces, require_auth, require_project_permission}
    - {publication: {...}}
    - {workspaces: [{key, published}]}
    - {workspaces: ["project", "map", "editor3d"]}
    - camelCase Varianten aus JS.
    """
    try:
        payload = _extract_publication_payload_root(data)

        visibility_raw = (
            payload.get("visibility")
            or payload.get("project_visibility")
            or payload.get("projectVisibility")
            or payload.get("mode")
        )
        visibility = _normalize_publication_visibility_for_route(visibility_raw, default="private")

        published_raw = (
            payload.get("published_workspaces")
            if "published_workspaces" in payload
            else payload.get("publishedWorkspaces")
            if "publishedWorkspaces" in payload
            else payload.get("workspaces")
            if "workspaces" in payload
            else payload.get("tabs")
            if "tabs" in payload
            else payload.get("published_tabs")
            if "published_tabs" in payload
            else payload.get("publishedTabs")
            if "publishedTabs" in payload
            else None
        )

        workspace_candidates = {}
        for key in PUBLICATION_ROUTE_WORKSPACES:
            if key in payload:
                workspace_candidates[key] = payload.get(key)

        published_workspaces = _normalize_publication_workspace_map(published_raw)
        if workspace_candidates:
            published_workspaces.update(_normalize_publication_workspace_map(workspace_candidates, existing=published_workspaces))

        require_auth_raw = payload.get("require_auth") if "require_auth" in payload else payload.get("requireAuth")
        require_permission_raw = (
            payload.get("require_project_permission")
            if "require_project_permission" in payload
            else payload.get("requireProjectPermission")
        )

        if visibility == "private":
            require_auth = True
            require_project_permission = True
        else:
            require_auth = _safe_bool(require_auth_raw, False)
            require_project_permission = _safe_bool(require_permission_raw, False)

        normalized = {
            "visibility": visibility,
            "published_workspaces": published_workspaces,
            "publishedWorkspaces": published_workspaces,
            "require_auth": require_auth,
            "requireAuth": require_auth,
            "require_project_permission": require_project_permission,
            "requireProjectPermission": require_project_permission,
        }

        if payload.get("reason"):
            normalized["reason"] = _safe_str(payload.get("reason"), "", 500)

        metadata = _safe_dict(payload.get("metadata") or payload.get("meta"))
        if metadata:
            normalized["metadata"] = metadata

        return normalized
    except Exception:
        return {
            "visibility": "private",
            "published_workspaces": {key: False for key in PUBLICATION_ROUTE_WORKSPACES},
            "publishedWorkspaces": {key: False for key in PUBLICATION_ROUTE_WORKSPACES},
            "require_auth": True,
            "requireAuth": True,
            "require_project_permission": True,
            "requireProjectPermission": True,
        }


def _publication_response(
    payload: Mapping[str, Any],
    *,
    project: Any = None,
    default_status: int = 200,
    no_store: bool = True,
):
    try:
        body = _safe_dict(payload)
        status = _safe_int(body.get("status_code"), default_status)
        if status <= 0:
            status = default_status

        if project is not None:
            body.setdefault("project_id", getattr(project, "id", None))
            body.setdefault("project_public_id", getattr(project, "public_id", None))
            body.setdefault("public_id", getattr(project, "public_id", None))

        body.setdefault("ok", status < 400)
        body.setdefault("status_code", status)

        publication_payload = _safe_dict(body.get("publication"))
        if publication_payload:
            body.setdefault("visibility", publication_payload.get("visibility"))
            body.setdefault("published_workspaces", publication_payload.get("published_workspaces") or publication_payload.get("publishedWorkspaces"))
            body.setdefault("effective_published_workspaces", publication_payload.get("effective_published_workspaces") or publication_payload.get("effectivePublishedWorkspaces"))

        return _json_response(body, status, no_store=no_store)
    except Exception as exc:
        return _exception_response("_publication_response failed", exc, code="publication_response_failed")



# ─────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────

def _finalize_json_response(resp: Response, *, no_store: bool = True) -> Response:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        request_id = _request_id()
        resp.headers.setdefault("X-Request-ID", request_id)
        resp.headers.setdefault("X-Correlation-ID", request_id)
    except Exception:
        pass

    try:
        if no_store:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass

    return resp


def _json_response(payload: Dict[str, Any], status: int = 200, *, no_store: bool = True):
    try:
        body = _safe_dict(payload)
        body.setdefault("status_code", int(status))
        body.setdefault("request_id", _request_id())
        body.setdefault("requestId", body.get("request_id"))
        resp = jsonify(body)
        resp.status_code = int(status)
        _finalize_json_response(resp, no_store=no_store)
        return resp, status

    except Exception:
        fallback = jsonify(
            {
                "ok": False,
                "error": "failed to serialize response",
                "code": "response_serialization_failed",
                "status_code": 500,
            }
        )
        fallback.status_code = 500
        _finalize_json_response(fallback, no_store=True)
        return fallback, 500


def _json_error(
    message: str,
    status: int = 500,
    *,
    code: str = "error",
    extra: Optional[Dict[str, Any]] = None,
):
    payload: Dict[str, Any] = {
        "ok": False,
        "error": message,
        "message": message,
        "code": code,
        "status_code": status,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status, no_store=True)


def _result_response(result: Any):
    try:
        payload = result.to_dict() if hasattr(result, "to_dict") else _safe_dict(result)
        status = int(getattr(result, "status_code", payload.get("status_code", 200)) or 200)
        return _json_response(payload, status, no_store=True)

    except Exception as exc:
        _log_exception("_result_response failed", exc)
        return _json_error(str(exc), 500, code="result_response_failed")


def _service_dict_response(payload: Dict[str, Any], default_status: int = 200):
    try:
        body = _safe_dict(payload)
        status = _safe_int(body.get("status_code"), default_status)
        if status <= 0:
            status = default_status
        return _json_response(body, status, no_store=True)
    except Exception as exc:
        return _exception_response("_service_dict_response failed", exc, code="service_response_failed")


def _permission_error_response(exc: PermissionDenied):
    try:
        status_code = _safe_int(getattr(exc, "status_code", None), 403)
        if status_code <= 0:
            status_code = 403

        if hasattr(exc, "to_dict"):
            payload = _safe_dict(exc.to_dict())
        else:
            payload = {}

        if not payload:
            payload = {
                "ok": False,
                "error": str(exc),
                "message": str(exc),
                "code": _safe_str(getattr(exc, "code", None), "permission_denied", 120),
                "status_code": status_code,
                "permission": getattr(exc, "permission", None),
                "project_id": getattr(exc, "project_id", None),
                "user_id": getattr(exc, "user_id", None),
            }

        payload.setdefault("status_code", status_code)
        return _json_response(payload, status_code, no_store=True)
    except Exception:
        return _json_error(
            "permission denied",
            403,
            code="permission_denied",
            extra={
                "permission": getattr(exc, "permission", None),
                "project_id": getattr(exc, "project_id", None),
                "user_id": getattr(exc, "user_id", None),
            },
        )


def _exception_response(message: str, exc: Exception, *, code: str = "internal_error"):
    _log_exception(message, exc)
    return _json_error(str(exc), 500, code=code)


def _service_unavailable_if_missing(service: Any, service_name: str):
    if callable(service):
        return None
    return _json_error(
        f"{service_name} unavailable",
        503,
        code=f"{service_name}_unavailable",
    )


# ─────────────────────────────────────────────────────────────
# Request lifecycle
# ─────────────────────────────────────────────────────────────

@bp.before_request
def _projects_api_before_request():
    """
    No default user creation.

    The request context is only read/cached so downstream services see the same
    AuthContext. Access decisions remain inside the route/service handlers.
    """
    try:
        _request_id()
        _current_user_context_dict(ensure=False)
    except Exception as exc:
        _log_warning("projects API auth context preload failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Status / diagnostics
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/_status")
def projects_status():
    try:
        invitation_status = {}
        publication_status = {}
        access_sync_status = {}
        chunk_config_status = {}
        auth_dependency = {}
        auth_requirements = {}

        try:
            if callable(get_project_invitation_service_status):
                invitation_status = get_project_invitation_service_status()
        except Exception as exc:
            invitation_status = {
                "ok": False,
                "code": "invitation_status_failed",
                "error": str(exc),
            }

        try:
            if callable(get_project_publication_service_status):
                publication_status = get_project_publication_service_status()
        except Exception as exc:
            publication_status = {
                "ok": False,
                "code": "publication_status_failed",
                "error": str(exc),
            }

        try:
            if callable(get_auth_dependency_status):
                auth_dependency = get_auth_dependency_status(include_private=False)
        except Exception as exc:
            auth_dependency = {
                "ok": False,
                "code": "auth_dependency_status_failed",
                "error": str(exc),
            }

        try:
            if callable(get_auth_requirements_status):
                auth_requirements = get_auth_requirements_status()
        except Exception as exc:
            auth_requirements = {
                "ok": False,
                "code": "auth_requirements_status_failed",
                "error": str(exc),
            }

        try:
            if callable(get_project_chunk_access_sync_service_status):
                access_sync_status = get_project_chunk_access_sync_service_status()
        except Exception as exc:
            access_sync_status = {
                "ok": False,
                "code": "chunk_access_sync_status_failed",
                "error": str(exc),
            }

        try:
            if callable(get_project_chunk_config_status):
                chunk_config_status = get_project_chunk_config_status()
        except Exception as exc:
            chunk_config_status = {
                "ok": False,
                "code": "chunk_config_status_failed",
                "error": str(exc),
            }

        payload = {
            "ok": True,
            "service": "projects_api",
            "blueprint": "projects_api",
            "phase": "project-management-earth-default-chunk-access-sync",
            "default_user_removed": True,
            "auth_unavailable_returns_503": True,
            "auth_dependency": auth_dependency,
            "auth_requirements": auth_requirements,
            "current_user": get_current_user_status() if callable(get_current_user_status) else {"ok": False, "code": "current_user_status_unavailable"},
            "project_service": get_project_service_status() if callable(get_project_service_status) else {"ok": False, "code": "project_service_unavailable"},
            "permissions": get_permission_service_status() if callable(get_permission_service_status) else {"ok": False, "code": "permission_service_unavailable"},
            "invitations": invitation_status,
            "publication": publication_status,
            "chunk_access_sync": access_sync_status,
            "chunk_config": chunk_config_status,
            "project_form": {
                "address_input_mode": "single_box",
                "visibility_mode": "cards_private_unlisted_public",
                "system_refs_in_normal_form": False,
            },
            "demo": {
                "one_demo_project": True,
                "persistent": False,
                "team_actions": False,
                "publication_actions": False,
                "invitation_actions": False,
                "requires_vectoplan_auth_demo_access": True,
            },
            "chunk": {
                "provisioningEnabled": _config_bool("VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True),
                "provisioningRequired": _config_bool("VECTOPLAN_CHUNK_PROVISION_REQUIRED", False),
                "internalUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_INTERNAL_URL", "")),
                "publicUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_PUBLIC_URL", "")),
                "defaultWorldTemplate": _config_str("VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE", "earth", 40),
                "fallbackWorldTemplate": _config_str("VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE", "flat", 40),
                "accessSyncEnabled": _config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True),
                "viewerReadOnly": True,
            },
            "routes": {
                "list": "/v1/projects",
                "create": "/v1/projects",
                "detail": "/v1/projects/<project_id>",
                "sidebar": "/v1/projects/sidebar",
                "current_user": "/v1/projects/current-user",
                "chunk_get": "/v1/projects/<project_id>/chunk",
                "chunk_ensure": "/v1/projects/<project_id>/chunk/ensure",
                "chunk_retry": "/v1/projects/<project_id>/chunk/retry",
                "chunk_reconcile": "/v1/projects/<project_id>/chunk/reconcile",
                "chunk_access_status": "/v1/projects/<project_id>/chunk/access",
                "chunk_access_sync": "/v1/projects/<project_id>/chunk/access/sync",
                "members": "/v1/projects/<project_id>/members",
                "invitations": "/v1/projects/<project_id>/invitations",
                "publication": "/v1/projects/<project_id>/publication",
                "service_links": "/v1/projects/<project_id>/service-links",
                "embed_policy": "/v1/projects/<project_id>/embed-policy",
            },
        }

        return _json_response(payload, 200, no_store=True)

    except Exception as exc:
        return _exception_response("projects_status failed", exc, code="projects_status_failed")


@bp.get("/v1/projects/current-user")
def projects_current_user():
    try:
        return _json_response(
            {
                "ok": True,
                "user": _current_user_context_dict(ensure=True),
            },
            200,
            no_store=True,
        )

    except Exception as exc:
        return _exception_response("projects_current_user failed", exc, code="current_user_failed")


# ─────────────────────────────────────────────────────────────
# Project list / sidebar
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects")
def projects_list():
    try:
        service_error = _service_unavailable_if_missing(list_projects_result, "project_service")
        if service_error is not None:
            return service_error

        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        context = _current_user_context_dict(ensure=False)
        if not _context_is_persistent(context) and not _context_is_demo(context):
            return _json_error(
                "Login oder Demo-Zugriff erforderlich.",
                401,
                code="login_or_demo_required",
                extra={"auth": context},
            )

        user_id = _current_user_id_optional()
        search = _request_str("q", "", 160) or _request_str("search", "", 160)
        limit = _request_int("limit", 100)
        offset = _request_int("offset", 0)

        result = list_projects_result(
            user_id=user_id,
            search=search or None,
            limit=limit,
            offset=offset,
        )

        return _result_response(result)

    except Exception as exc:
        return _exception_response("projects_list failed", exc, code="projects_list_failed")


@bp.get("/v1/projects/sidebar")
def projects_sidebar():
    try:
        service_error = _service_unavailable_if_missing(list_projects_result, "project_service")
        if service_error is not None:
            return service_error

        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        context = _current_user_context_dict(ensure=False)
        if not _context_is_persistent(context) and not _context_is_demo(context):
            return _json_error(
                "Login oder Demo-Zugriff erforderlich.",
                401,
                code="login_or_demo_required",
                extra={"auth": context, "items": [], "sidebar_items": [], "total": 0},
            )

        user_id = _current_user_id_optional()
        limit = _request_int("limit", 100)
        search = _request_str("q", "", 160) or _request_str("search", "", 160)

        result = list_projects_result(
            user_id=user_id,
            search=search or None,
            limit=limit,
            offset=0,
        )

        payload = result.to_dict() if hasattr(result, "to_dict") else _safe_dict(result)

        items = _safe_list(payload.get("sidebar_items"))
        if not items:
            items = _safe_list(payload.get("items"))

        return _json_response(
            {
                "ok": bool(payload.get("ok", True)),
                "user_id": user_id,
                "auth": context,
                "demo_mode": _context_is_demo(context),
                "items": items,
                "sidebar_items": items,
                "total": len(items),
            },
            _safe_int(payload.get("status_code"), 200),
            no_store=True,
        )

    except Exception as exc:
        return _exception_response("projects_sidebar failed", exc, code="projects_sidebar_failed")


# ─────────────────────────────────────────────────────────────
# Project create / detail / update / delete
# ─────────────────────────────────────────────────────────────

@bp.post("/v1/projects")
def projects_create():
    try:
        service_error = _service_unavailable_if_missing(create_project_result, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        data = _request_json({})
        user_id = _current_user_id_optional()

        result = create_project_result(data, user_id=user_id)

        return _result_response(result)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("projects_create failed", exc, code="project_create_failed")


@bp.get("/v1/projects/<project_id>")
def projects_get(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(get_project_result, "project_service")
        if service_error is not None:
            return service_error

        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        user_id = _current_user_id_optional()
        include_deleted = _request_bool("include_deleted", False)

        result = get_project_result(
            project_id,
            user_id=user_id,
            include_deleted=include_deleted,
        )

        return _result_response(result)

    except Exception as exc:
        return _exception_response("projects_get failed", exc, code="project_get_failed")


@bp.patch("/v1/projects/<project_id>")
@bp.put("/v1/projects/<project_id>")
def projects_update(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(update_project_result, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        data = _request_json({})
        user_id = _current_user_id_optional()

        result = update_project_result(
            project_id,
            data,
            user_id=user_id,
        )

        return _result_response(result)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("projects_update failed", exc, code="project_update_failed")


@bp.delete("/v1/projects/<project_id>")
def projects_delete(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(delete_project_result, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        user_id = _current_user_id_optional()
        hard_delete = _request_bool("hard_delete", False)

        result = delete_project_result(
            project_id,
            user_id=user_id,
            hard_delete=hard_delete,
        )

        return _result_response(result)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("projects_delete failed", exc, code="project_delete_failed")


# ─────────────────────────────────────────────────────────────
# Project chunk linkage
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/chunk")
def project_chunk_get(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        include_private = _request_bool("include_private", False) and bool(
            user_id and callable(can_manage_project) and can_manage_project(project, user_id)
        )

        payload = _serialize_project_chunk_payload(
            project,
            user_id=user_id,
            include_private=include_private,
        )

        payload["access"] = serialize_project_permissions(project, user_id=user_id)
        payload["routes"] = {
            "self": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk",
            "ensure": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk/ensure",
            "retry": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk/retry",
            "reconcile": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk/reconcile",
            "access": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk/access",
            "accessSync": f"/v1/projects/{getattr(project, 'public_id', project_id)}/chunk/access/sync",
        }

        return _json_response(payload, 200, no_store=True)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_chunk_get failed", exc, code="project_chunk_get_failed")


@bp.post("/v1/projects/<project_id>/chunk/ensure")
@bp.post("/v1/projects/<project_id>/chunk/provision")
def project_chunk_ensure(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(ensure_project_chunk_link_result, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        user_id = _current_user_id_optional()
        force = _request_bool("force", False)

        result = ensure_project_chunk_link_result(
            project_id,
            user_id=user_id,
            force=force,
        )

        return _result_response(result)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_chunk_ensure failed", exc, code="project_chunk_ensure_failed")


@bp.post("/v1/projects/<project_id>/chunk/retry")
def project_chunk_retry(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(ensure_project_chunk_link_result, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        user_id = _current_user_id_optional()

        result = ensure_project_chunk_link_result(
            project_id,
            user_id=user_id,
            force=True,
        )

        return _result_response(result)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_chunk_retry failed", exc, code="project_chunk_retry_failed")


@bp.post("/v1/projects/<project_id>/chunk/reconcile")
def project_chunk_reconcile(project_id: str):
    """Ensure the Chunk graph and then reconcile all direct project assignments."""
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(ensure_project_chunk_link_result):
            return _json_error("project service unavailable", 503, code="project_service_unavailable")
        if not callable(sync_project_chunk_access_result):
            return _json_error("chunk access sync service unavailable", 503, code="chunk_access_sync_unavailable")

        user_id = _current_user_id_optional()
        data = _request_json({})
        force = _safe_bool(data.get("force", request.args.get("force")), True)

        provision = ensure_project_chunk_link_result(
            project_id,
            user_id=user_id,
            force=force,
        )
        provision_payload = provision.to_dict() if hasattr(provision, "to_dict") else _safe_dict(provision)
        if not _safe_bool(provision_payload.get("ok"), False):
            return _result_response(provision)

        access_sync = sync_project_chunk_access_result(
            project_id,
            user_id=user_id,
            force=True,
        )
        access_payload = access_sync.to_dict() if hasattr(access_sync, "to_dict") else _safe_dict(access_sync)
        project = resolve_project(project_id) if callable(resolve_project) else None

        status = _safe_int(
            access_payload.get("status_code"),
            200 if _safe_bool(access_payload.get("ok"), False) else 502,
        )
        return _json_response(
            {
                "ok": _safe_bool(access_payload.get("ok"), False),
                "code": access_payload.get("code") or "chunk_reconciled",
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_service_links=True,
                    include_publication=True,
                ) if project is not None else None,
                "chunk": _serialize_project_chunk_payload(
                    project,
                    user_id=user_id,
                    include_private=True,
                ) if project is not None else {},
                "provisioning": provision_payload,
                "access_sync": access_payload,
                "accessSync": access_payload,
            },
            status,
            no_store=True,
        )
    except PermissionDenied as exc:
        return _permission_error_response(exc)
    except Exception as exc:
        return _exception_response("project_chunk_reconcile failed", exc, code="project_chunk_reconcile_failed")


@bp.get("/v1/projects/<project_id>/chunk/access")
@bp.get("/v1/projects/<project_id>/chunk/access/status")
def project_chunk_access_status(project_id: str):
    try:
        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        project = resolve_project(project_id) if callable(resolve_project) else None
        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=False)
        access = serialize_project_permissions(project, user_id=user_id)
        access_sync = _project_access_sync_payload(project)
        permissions = _safe_dict(_safe_dict(access).get("permissions"))
        can_manage = _safe_bool(_safe_dict(access).get("can_manage", permissions.get("manage")), False)
        if not can_manage:
            access_sync.pop("ownerAuthUserId", None)
            access_sync.pop("owner_auth_user_id", None)

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "read_only": _project_read_only(project, user_id),
                "readOnly": _project_read_only(project, user_id),
                "access": access,
                "chunk_access_sync": access_sync,
                "chunkAccessSync": access_sync,
                "chunk": _extract_chunk_from_project_payload(
                    serialize_project(
                        project,
                        user_id=user_id,
                        include_permissions=True,
                        include_service_links=False,
                        include_publication=True,
                    )
                ),
            },
            200,
            no_store=True,
        )
    except PermissionDenied as exc:
        return _permission_error_response(exc)
    except Exception as exc:
        return _exception_response("project_chunk_access_status failed", exc, code="chunk_access_status_failed")


@bp.post("/v1/projects/<project_id>/chunk/access/sync")
@bp.post("/v1/projects/<project_id>/chunk/access/reconcile")
def project_chunk_access_sync(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error
        if not callable(sync_project_chunk_access_result):
            return _json_error("chunk access sync service unavailable", 503, code="chunk_access_sync_unavailable")

        user_id = _current_user_id_optional()
        force = _request_bool("force", True)
        result = sync_project_chunk_access_result(
            project_id,
            user_id=user_id,
            force=force,
        )
        return _result_response(result)
    except PermissionDenied as exc:
        return _permission_error_response(exc)
    except Exception as exc:
        return _exception_response("project_chunk_access_sync failed", exc, code="chunk_access_sync_failed")


# ─────────────────────────────────────────────────────────────
# Project members / permissions
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/access")
def project_access_get(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=False)

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "auth": _current_user_context_dict(ensure=False),
                "access": serialize_project_permissions(project, user_id=user_id),
                "read_only": _project_read_only(project, user_id),
                "readOnly": _project_read_only(project, user_id),
                "chunk_access_sync": _project_access_sync_payload(project),
                "chunkAccessSync": _project_access_sync_payload(project),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_access_get failed", exc, code="project_access_failed")


@bp.get("/v1/projects/<project_id>/members")
def project_members_list(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Teamverwaltung.")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        include_inactive = _request_bool("include_inactive", False)
        members = list_project_memberships(project, include_inactive=include_inactive) if callable(list_project_memberships) else []

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "items": members,
                "members": members,
                "total": len(members),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_members_list failed", exc, code="members_list_failed")


@bp.put("/v1/projects/<project_id>/members/<int:target_user_id>")
@bp.patch("/v1/projects/<project_id>/members/<int:target_user_id>")
def project_member_set(project_id: str, target_user_id: int):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Teamverwaltung.")

        user_id = _current_user_id_optional()
        data = _request_json({})

        role = normalize_role(data.get("role") or request.args.get("role") or "viewer")
        if role == "owner":
            return _json_error(
                "Die Owner-Rolle darf nur über die Eigentumsübertragung vergeben werden.",
                409,
                code="owner_role_requires_transfer",
            )
        permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}

        membership = set_project_member_role(
            project,
            target_user_id=target_user_id,
            role=role,
            actor_user_id=user_id,
            overrides=permissions,
            commit=True,
        )

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "member": _serialize_model(membership, include_private=True),
                "access": serialize_project_permissions(project, user_id=user_id),
                "chunk_access_sync": _project_access_sync_payload(project),
                "chunkAccessSync": _project_access_sync_payload(project),
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_service_links=False,
                    include_publication=True,
                ),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_member_set failed", exc, code="member_set_failed")


@bp.delete("/v1/projects/<project_id>/members/<int:target_user_id>")
def project_member_delete(project_id: str, target_user_id: int):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Teamverwaltung.")

        user_id = _current_user_id_optional()
        hard_delete = _request_bool("hard_delete", False)

        ok = revoke_project_member(
            project,
            target_user_id=target_user_id,
            actor_user_id=user_id,
            hard_delete=hard_delete,
            commit=True,
        )

        return _json_response(
            {
                "ok": bool(ok),
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "target_user_id": target_user_id,
                "deleted": bool(ok),
                "chunk_access_sync": _project_access_sync_payload(project),
                "chunkAccessSync": _project_access_sync_payload(project),
                "project": serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_service_links=False,
                    include_publication=True,
                ),
            },
            200 if ok else 404,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_member_delete failed", exc, code="member_delete_failed")


@bp.post("/v1/projects/<project_id>/transfer")
def project_transfer(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(resolve_project, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte können nicht übertragen werden.")

        data = _request_json({})
        user_id = _current_user_id_optional()
        new_owner_user_id = _safe_int(
            data.get("new_owner_user_id")
            or data.get("newOwnerUserId")
            or data.get("owner_user_id")
            or data.get("ownerUserId")
            or data.get("target_user_id")
            or request.args.get("new_owner_user_id"),
            0,
        )

        if new_owner_user_id <= 0:
            return _json_error("new_owner_user_id required", 400, code="new_owner_required")

        updated = transfer_project_owner(
            project,
            new_owner_user_id=new_owner_user_id,
            actor_user_id=user_id,
            commit=True,
        )

        return _json_response(
            {
                "ok": True,
                "project": serialize_project(updated, user_id=user_id, include_permissions=True),
                "new_owner_user_id": new_owner_user_id,
                "new_owner_auth_user_id": getattr(updated, "auth_owner_user_id", None),
                "chunk_access_sync": _project_access_sync_payload(updated),
                "chunkAccessSync": _project_access_sync_payload(updated),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_transfer failed", exc, code="project_transfer_failed")


# ─────────────────────────────────────────────────────────────
# Project invitations
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/invitations")
def project_invitations_list(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(list_project_invitations):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Einladungen.")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        include_terminal = _request_bool("include_terminal", True)
        include_private = _request_bool("include_private", False)

        result = list_project_invitations(
            project,
            actor_user_id=user_id,
            include_terminal=include_terminal,
            include_private=include_private,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitations_list failed", exc, code="invitations_list_failed")


@bp.post("/v1/projects/<project_id>/invitations")
def project_invitations_create(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(invite_registered_email_to_project):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Einladungen.")

        data = _request_json({})
        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        email = (
            data.get("email")
            or data.get("user_email")
            or data.get("userEmail")
            or data.get("invitee")
            or data.get("recipient")
            or request.args.get("email")
        )
        role = normalize_role(data.get("role") or request.args.get("role") or "viewer")
        if role == "owner":
            return _json_error(
                "Owner kann nicht per Einladung vergeben werden.",
                409,
                code="owner_role_requires_transfer",
            )
        message = data.get("message") or data.get("note")
        metadata = _safe_dict(data.get("metadata") or data.get("meta"))

        if not _safe_str(email, "", 320):
            return _json_error("email required", 400, code="email_required")

        result = invite_registered_email_to_project(
            project,
            email=email,
            role=role,
            actor_user_id=user_id,
            message=message,
            metadata=metadata,
            dispatch=True,
            commit=True,
            include_token_in_result=_request_bool("include_token", False),
        )

        return _service_dict_response(result, default_status=201)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitations_create failed", exc, code="invitation_create_failed")


@bp.delete("/v1/projects/<project_id>/invitations/<invitation_id>")
@bp.post("/v1/projects/<project_id>/invitations/<invitation_id>/revoke")
def project_invitations_revoke(project_id: str, invitation_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(revoke_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Einladungen.")

        data = _request_json({})
        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        reason = data.get("reason") or request.args.get("reason")

        result = revoke_project_invitation(
            project,
            invitation_id=invitation_id,
            actor_user_id=user_id,
            reason=reason,
            commit=True,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitations_revoke failed", exc, code="invitation_revoke_failed")


@bp.post("/v1/project-invitations/<invitation_id>/accept")
def project_invitation_accept(invitation_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(accept_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        data = _request_json({})
        identity_error = _identity_override_error(data)
        if identity_error is not None:
            return identity_error

        user_id = _current_user_id_optional()
        auth_user_id = _current_auth_user_id()
        email = _current_email()
        if not user_id or not auth_user_id:
            return _json_error(
                "Persistente lokale und kanonische Auth-Identität erforderlich.",
                403,
                code="persistent_identity_required",
            )

        result = accept_project_invitation(
            invitation_id=invitation_id,
            auth_user_id=auth_user_id,
            email=email,
            local_user_id=user_id,
            plain_token=data.get("token") or request.args.get("token"),
            actor_user_id=user_id,
            commit=True,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitation_accept failed", exc, code="invitation_accept_failed")


@bp.post("/v1/project-invitations/<invitation_id>/reject")
def project_invitation_reject(invitation_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(reject_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        data = _request_json({})
        identity_error = _identity_override_error(data)
        if identity_error is not None:
            return identity_error

        auth_user_id = _current_auth_user_id()
        if not auth_user_id:
            return _json_error(
                "Kanonische Auth-Identität erforderlich.",
                403,
                code="persistent_identity_required",
            )

        result = reject_project_invitation(
            invitation_id=invitation_id,
            auth_user_id=auth_user_id,
            email=_current_email(),
            reason=data.get("reason") or request.args.get("reason"),
            commit=True,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitation_reject failed", exc, code="invitation_reject_failed")


@bp.post("/v1/projects/invitations/expire")
@bp.post("/v1/project-invitations/expire")
def project_invitations_expire():
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(expire_project_invitations):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project_id = _request_str("project_id", "", 120) or None
        project = resolve_project(project_id) if project_id else None

        if project is not None:
            _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine Einladungen.")
            user_id = _current_user_id_optional()
            _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        result = expire_project_invitations(project, commit=True)

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_invitations_expire failed", exc, code="invitations_expire_failed")


# ─────────────────────────────────────────────────────────────
# Project publication / workspace visibility
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/publication")
def project_publication_get(project_id: str):
    try:
        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        if not callable(get_project_publication):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        if not callable(resolve_project):
            return _json_error("project service unavailable", 503, code="project_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        public_request = not bool(user_id)

        if _project_is_demo(project):
            try:
                _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=False)
            except PermissionDenied as exc:
                return _permission_error_response(exc)
            return _json_response(_demo_publication_payload(project), 200, no_store=True)

        include_private_requested = _request_bool("include_private", False)
        include_private = False

        if include_private_requested and user_id and callable(can_manage_project):
            try:
                include_private = bool(can_manage_project(project, user_id))
            except Exception:
                include_private = False

        result = get_project_publication(
            project,
            actor_user_id=user_id,
            include_private=include_private,
            for_public=public_request,
            use_cache=False,
        )

        result_payload = _safe_dict(result)
        result_payload.setdefault("public_request", public_request)
        result_payload.setdefault("include_private", include_private)

        return _publication_response(result_payload, project=project, default_status=200, no_store=True)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_publication_get failed", exc, code="publication_get_failed")


@bp.put("/v1/projects/<project_id>/publication")
@bp.patch("/v1/projects/<project_id>/publication")
def project_publication_update(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        if not callable(update_project_publication):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        if not callable(resolve_project):
            return _json_error("project service unavailable", 503, code="project_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte können nicht veröffentlicht werden.")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        raw_data = _request_json({})
        data = _normalize_publication_update_payload(raw_data)

        result = update_project_publication(
            project,
            data,
            actor_user_id=user_id,
            commit=True,
        )

        result_payload = _safe_dict(result)
        result_payload.setdefault("request", data)
        result_payload.setdefault("raw_request_keys", sorted([_safe_str(key, "", 120) for key in raw_data.keys()]) if isinstance(raw_data, dict) else [])

        return _publication_response(result_payload, project=project, default_status=200, no_store=True)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_publication_update failed", exc, code="publication_update_failed")


@bp.get("/v1/projects/<project_id>/publication/workspaces/<workspace>")
@bp.get("/v1/projects/<project_id>/workspace-access/<workspace>")
def project_workspace_access_get(project_id: str, workspace: str):
    try:
        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        if not callable(can_access_project_workspace):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        if not callable(resolve_project):
            return _json_error("project service unavailable", 503, code="project_service_unavailable")

        user_id = _current_user_id_optional()
        public_request = _request_bool("public", False) or user_id is None
        workspace_key = _normalize_publication_workspace_for_route(workspace, default=_safe_str(workspace, "", 80))

        if workspace_key in NEVER_PUBLIC_ROUTE_WORKSPACES:
            project = resolve_project(project_id)
            if project is None:
                return _json_error("project not found", 404, code="project_not_found")

            if user_id and callable(can_manage_project):
                try:
                    if can_manage_project(project, user_id) and not public_request:
                        return _json_response(
                            {
                                "ok": True,
                                "allowed": True,
                                "project_id": getattr(project, "id", None),
                                "public_id": getattr(project, "public_id", None),
                                "project_public_id": getattr(project, "public_id", None),
                                "workspace": workspace_key,
                                "access_source": "project_manage_permission",
                                "public_request": public_request,
                                "read_only": False,
                            },
                            200,
                            no_store=True,
                        )
                except Exception:
                    pass

            return _json_error(
                "Dieser Workspace kann nicht öffentlich geöffnet werden.",
                403,
                code="workspace_never_public",
                extra={
                    "project_id": project_id,
                    "workspace": workspace_key,
                    "public_request": public_request,
                    "allowed": False,
                    "read_only": True,
                },
            )

        project = resolve_project(project_id)
        if project is not None and _project_is_demo(project):
            access = serialize_project_permissions(project, user_id=user_id)
            permissions = _safe_dict(access.get("permissions"))
            allowed = bool(_safe_bool(permissions.get(PERMISSION_VIEW), False) or _safe_bool(access.get("can_view"), False))
            return _json_response(
                {
                    "ok": allowed,
                    "allowed": allowed,
                    "project_id": getattr(project, "id", None),
                    "public_id": getattr(project, "public_id", None),
                    "project_public_id": getattr(project, "public_id", None),
                    "workspace": workspace_key,
                    "access": access,
                    "public_request": public_request,
                    "read_only": True,
                    "reason": "demo_project_workspace_access" if allowed else "demo_project_denied",
                },
                200 if allowed else 403,
                no_store=True,
            )

        result = can_access_project_workspace(
            project if project is not None else project_id,
            workspace_key,
            actor_user_id=user_id,
            public_request=public_request,
        )

        result_payload = _safe_dict(result)
        status = _safe_int(result_payload.get("status_code"), 200 if _safe_bool(result_payload.get("ok") or result_payload.get("allowed"), False) else 403)
        allowed = _safe_bool(result_payload.get("ok") or result_payload.get("allowed"), False)

        result_payload.setdefault("ok", allowed)
        result_payload.setdefault("allowed", allowed)
        result_payload.setdefault("workspace", workspace_key)
        result_payload.setdefault("project_public_id", getattr(project, "public_id", None) if project is not None else project_id)
        result_payload.setdefault("public_id", getattr(project, "public_id", None) if project is not None else project_id)
        result_payload.setdefault("public_request", public_request)
        result_payload.setdefault("read_only", bool(public_request and allowed))

        return _json_response(result_payload, status, no_store=True)

    except Exception as exc:
        return _exception_response("project_workspace_access_get failed", exc, code="workspace_access_failed")


# ─────────────────────────────────────────────────────────────
# Project versions
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/versions")
def project_versions_list(project_id: str):
    try:
        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        if _project_is_demo(project):
            return _json_response(
                {
                    "ok": True,
                    "project_id": getattr(project, "id", None),
                    "public_id": getattr(project, "public_id", None),
                    "items": [],
                    "versions": [],
                    "total": 0,
                    "access": serialize_project_permissions(project, user_id=user_id),
                    "reason": "demo_projects_do_not_have_persistent_versions",
                },
                200,
                no_store=True,
            )

        kind = _request_str("kind", "", 80) or None
        service_name = _request_str("service_name", "", 80) or _request_str("service", "", 80) or None
        limit = _request_int("limit", 100)

        items = list_project_versions(
            project,
            kind=kind,
            service_name=service_name,
            limit=limit,
        ) if callable(list_project_versions) else []

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "items": items,
                "versions": items,
                "total": len(items),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_versions_list failed", exc, code="versions_list_failed")


@bp.post("/v1/projects/<project_id>/versions")
def project_versions_create(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(create_project_version_link, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine dauerhaften Versionslinks.")

        data = _normalize_version_payload(_request_json({}))
        user_id = _current_user_id_optional()

        row = create_project_version_link(
            project,
            label=data.get("label"),
            description=data.get("description"),
            service_name=data.get("service_name"),
            service_version_id=data.get("service_version_id"),
            service_snapshot_id=data.get("service_snapshot_id"),
            service_artifact_id=data.get("service_artifact_id"),
            kind=data.get("kind"),
            status=data.get("status") or "stored",
            artifact_ref=_safe_dict(data.get("artifact_ref")),
            metadata=_safe_dict(data.get("metadata")),
            user_id=user_id,
            commit=True,
        )

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "version": _serialize_model(row, include_private=True),
            },
            201,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_versions_create failed", exc, code="version_create_failed")


# ─────────────────────────────────────────────────────────────
# Project service links
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/service-links")
def project_service_links_list(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte zeigen keine Systemreferenzen über diese Route.")

        user_id = _current_user_id_optional()

        _require_project_permission_checked(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        items = list_project_service_links(project) if callable(list_project_service_links) else []

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "items": items,
                "service_links": items,
                "total": len(items),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_service_links_list failed", exc, code="service_links_list_failed")


@bp.post("/v1/projects/<project_id>/service-links")
def project_service_links_upsert(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(upsert_project_service_link, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_MANAGE, "Demo-Projekte unterstützen keine manuelle Service-Link-Verwaltung.")

        data = _normalize_service_link_payload(_request_json({}))
        user_id = _current_user_id_optional()

        if not data.get("service_name"):
            return _json_error("service_name required", 400, code="service_name_required")

        if not data.get("resource_kind"):
            return _json_error("resource_kind required", 400, code="resource_kind_required")

        if not (
            data.get("external_id")
            or data.get("external_project_id")
            or data.get("external_universe_id")
            or data.get("external_world_id")
        ):
            return _json_error(
                "external_id or external_project_id or external_universe_id or external_world_id required",
                400,
                code="resource_id_required",
            )

        metadata = _safe_dict(data.get("metadata"))

        if data.get("external_universe_id"):
            metadata["external_universe_id"] = data.get("external_universe_id")
            metadata["chunk_universe_id"] = data.get("external_universe_id")

        row = upsert_project_service_link(
            project,
            service_name=data.get("service_name"),
            resource_kind=data.get("resource_kind"),
            external_id=data.get("external_id"),
            external_project_id=data.get("external_project_id"),
            external_world_id=data.get("external_world_id"),
            external_url=data.get("external_url"),
            status=data.get("status") or "active",
            metadata=metadata,
            user_id=user_id,
            commit=True,
        )

        project_payload = serialize_project(
            project,
            user_id=user_id,
            include_permissions=True,
            include_service_links=True,
            include_publication=True,
        )

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "service_link": _serialize_model(row, include_private=True),
                "project": project_payload,
                "chunk": _extract_chunk_from_project_payload(project_payload),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_service_links_upsert failed", exc, code="service_link_upsert_failed")


# ─────────────────────────────────────────────────────────────
# Project embed policy
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/embed-policy")
def project_embed_policy_get(project_id: str):
    try:
        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_EMBED, "Demo-Projekte unterstützen keine Embed-Policy-Verwaltung.")

        user_id = _current_user_id_optional()

        _require_project_permission_checked(project, PERMISSION_EMBED, user_id, allow_public_view=False)

        policy = get_or_create_embed_policy(project, user_id=user_id, commit=True)

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "embed_policy": _serialize_model(policy, include_private=True),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_embed_policy_get failed", exc, code="embed_policy_get_failed")


@bp.put("/v1/projects/<project_id>/embed-policy")
@bp.patch("/v1/projects/<project_id>/embed-policy")
def project_embed_policy_update(project_id: str):
    try:
        service_error = _service_unavailable_if_missing(update_project_embed_policy, "project_service")
        if service_error is not None:
            return service_error

        persistent_error = _require_persistent_context()
        if persistent_error is not None:
            return persistent_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        _raise_if_demo_restricted(project, PERMISSION_EMBED, "Demo-Projekte unterstützen keine Embed-Policy-Verwaltung.")

        data = _request_json({})
        user_id = _current_user_id_optional()

        policy = update_project_embed_policy(
            project,
            data,
            user_id=user_id,
            commit=True,
        )

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "embed_policy": _serialize_model(policy, include_private=True),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_embed_policy_update failed", exc, code="embed_policy_update_failed")


# ─────────────────────────────────────────────────────────────
# Lightweight helpers
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/sidebar-item")
def project_sidebar_item_get(project_id: str):
    try:
        auth_error = _require_auth_available()
        if auth_error is not None:
            return auth_error

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        _require_project_permission_checked(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        return _json_response(
            {
                "ok": True,
                "item": serialize_project_sidebar_item(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_sidebar_item_get failed", exc, code="sidebar_item_failed")


__all__ = [
    "bp",
    "projects_api_bp",
    "project_api_bp",
]