# services/vectoplan-app/routes/ui/projects.py
from __future__ import annotations

"""
VECTOPLAN project UI shell routes.

Zweck:
- Root-URL / rendert die App-Shell.
- /project=<project_public_id> rendert dieselbe App-Shell mit ausgewähltem Projekt.
- /project=new rendert die Shell mit Projekterstellung als initialem Workspace.
- Im Demo-Kontext wird genau das temporäre Demo-Projekt geladen.
- Die eigentliche Projekt-Workspace-Seite wird über routes.viewer bereitgestellt:
    /ui/project/<project_id>/project
    /ui/project/<project_id>/<workspace>
    /ui/project/<project_id>/context.json
- Diese Datei bleibt Shell-/Kompatibilitätsschicht.

Wichtig:
- vectoplan-app besitzt hier nur Projekt-Metadaten und UI-Kontext.
- vectoplan-auth ist die Wahrheit für Login, Guest, Blocked, Account, Plan und Entitlements.
- Diese Datei erzeugt keine echten Benutzeraccounts.
- Diese Datei erzeugt keinen Default-User.
- Kein Fallback auf AppUser id=1.
- Demo erzeugt keine persistenten Conversations.
- Auth-unavailable bekommt keinen Demo-Fallback und rendert 503.
- Echte Blocked/Banned-User bekommen 403.
- Chunk-, Editor-, 2D-, Map- und LV-Fachdaten bleiben in ihren Microservices.
"""

from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)
from werkzeug.wrappers import Response


# ─────────────────────────────────────────────────────────────
# Robust imports
# ─────────────────────────────────────────────────────────────

try:
    from extensions import db
except Exception:  # pragma: no cover
    db = None  # type: ignore


try:
    from models import Conversation
except Exception:  # pragma: no cover
    Conversation = None  # type: ignore


try:
    from services.current_user import (
        get_current_user_context,
        get_current_user_id_optional,
        get_current_user_status,
        get_platform_auth_context,
    )
except Exception:  # pragma: no cover
    get_current_user_status = None  # type: ignore
    get_platform_auth_context = None  # type: ignore

    def get_current_user_id_optional() -> Optional[int]:  # type: ignore
        return None

    def get_current_user_context(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore
        return {
            "id": None,
            "user_id": None,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "auth_unavailable": True,
            "access_blocked": True,
            "user_blocked": False,
            "blocked_kind": "auth_unavailable",
            "blocked_reason": "current_user_unavailable",
            "denial_status_code": 503,
            "source": "fallback_auth_unavailable",
        }


try:
    from services.auth_dependency_service import get_auth_dependency_status
except Exception:  # pragma: no cover
    get_auth_dependency_status = None  # type: ignore


try:
    from services.project_permissions import (
        PERMISSION_VIEW,
        PermissionDenied,
        require_project_permission,
        serialize_project_permissions,
    )
except Exception:  # pragma: no cover
    PERMISSION_VIEW = "view"  # type: ignore
    serialize_project_permissions = None  # type: ignore

    class PermissionDenied(RuntimeError):  # type: ignore
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

    def require_project_permission(*args: Any, **kwargs: Any) -> bool:  # type: ignore
        return False


try:
    from services.project_service import (
        get_or_create_project_conversation,
        list_project_sidebar_items,
        project_public_url,
        project_workspace_path,
        resolve_project,
        serialize_project,
        serialize_project_sidebar_item,
    )
except Exception:  # pragma: no cover
    get_or_create_project_conversation = None  # type: ignore
    list_project_sidebar_items = None  # type: ignore
    project_public_url = None  # type: ignore
    project_workspace_path = None  # type: ignore
    resolve_project = None  # type: ignore
    serialize_project = None  # type: ignore
    serialize_project_sidebar_item = None  # type: ignore


try:
    from services.demo_project_service import (
        demo_project_public_payload,
        ensure_demo_project_for_context,
        get_current_demo_project,
        is_demo_project as service_is_demo_project,
    )
except Exception:  # pragma: no cover
    demo_project_public_payload = None  # type: ignore
    ensure_demo_project_for_context = None  # type: ignore
    get_current_demo_project = None  # type: ignore
    service_is_demo_project = None  # type: ignore


try:
    from services.project_publication_service import get_project_publication
except Exception:  # pragma: no cover
    get_project_publication = None  # type: ignore


try:
    from services.ui_notice_service import (
        build_notice_stream,
        get_notice_stream_status,
    )
except Exception:  # pragma: no cover
    build_notice_stream = None  # type: ignore
    get_notice_stream_status = None  # type: ignore


try:
    from services.project_access_context import (
        ProjectAccessContext,
        apply_project_access_context,
        get_project_access_context_status,
        resolve_project_shell_access,
    )
except Exception:  # pragma: no cover
    ProjectAccessContext = None  # type: ignore
    apply_project_access_context = None  # type: ignore
    get_project_access_context_status = None  # type: ignore
    resolve_project_shell_access = None  # type: ignore

bp = Blueprint("ui_projects", __name__)

ui_projects_bp = bp
projects_ui_bp = bp


# ─────────────────────────────────────────────────────────────
# Safe helpers
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
    "current_user_unavailable",
    "ui_projects_current_user_unavailable",
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
        if value is None or isinstance(value, bool):
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

        text = _safe_str(value, "", 40).lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable", "active", "ok"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable", "inactive", "error", "failed"}:
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


def _safe_list(value: Any) -> List[Any]:
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


def _safe_quote(value: Any) -> str:
    try:
        return quote(str(value), safe="")
    except Exception:
        return ""


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


def _url_for_safe(endpoint: str, fallback: str, **values: Any) -> str:
    try:
        return str(url_for(endpoint, **values))
    except Exception:
        return fallback


def _is_dev() -> bool:
    try:
        env = str(current_app.config.get("FLASK_ENV", "") or "").lower()
        debug = bool(current_app.config.get("DEBUG", False))
        return debug or env.startswith("dev") or env.startswith("development")
    except Exception:
        return False


def _config_url(name: str, default: str = "") -> str:
    try:
        return _safe_str(current_app.config.get(name, default), default, 4000).rstrip("/")
    except Exception:
        return default.rstrip("/")


def _request_wants_json(default: bool = False) -> bool:
    try:
        if request.path.endswith(".json"):
            return True
        if request.headers.get("X-Requested-With", "").lower() in {"xmlhttprequest", "fetch", "service"}:
            return True
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return True
        if request.is_json:
            return True
        return default
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Auth / user helpers
# ─────────────────────────────────────────────────────────────

def _current_user_payload(*, ensure: bool = False) -> Dict[str, Any]:
    try:
        context = get_current_user_context(ensure=ensure)

        if hasattr(context, "to_dict"):
            return _safe_dict(context.to_dict())

        return _safe_dict(context)

    except Exception:
        return {
            "id": None,
            "user_id": None,
            "authenticated": False,
            "demo_mode": False,
            "persistent": False,
            "blocked": True,
            "auth_unavailable": True,
            "access_blocked": True,
            "user_blocked": False,
            "blocked_kind": "auth_unavailable",
            "blocked_reason": "ui_projects_current_user_unavailable",
            "denial_status_code": 503,
            "source": "ui_projects_fallback_auth_unavailable",
        }


def _platform_auth_context() -> Any:
    try:
        if callable(get_platform_auth_context):
            return get_platform_auth_context(force_refresh=False)
    except Exception:
        return None

    return None


def _context_code(current_user: Optional[Mapping[str, Any]] = None) -> str:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    return _safe_str(
        data.get("blocked_reason")
        or data.get("blockedReason")
        or data.get("reason_code")
        or data.get("reasonCode")
        or data.get("auth_state")
        or data.get("authState")
        or data.get("code"),
        "",
        160,
    ).lower()


def _is_auth_unavailable_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    code = _context_code(data)
    blocked_kind = _safe_str(data.get("blocked_kind") or data.get("blockedKind"), "", 80).lower()
    status = _safe_int(data.get("denial_status_code") or data.get("denialStatusCode") or data.get("status_code"), 0)

    return bool(
        _safe_bool(data.get("auth_unavailable") or data.get("authUnavailable"), False)
        or blocked_kind == "auth_unavailable"
        or code in AUTH_UNAVAILABLE_CODES
        or status == 503
    )


def _is_user_blocked_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    code = _context_code(data)
    blocked_kind = _safe_str(data.get("blocked_kind") or data.get("blockedKind"), "", 80).lower()

    return bool(
        not _is_auth_unavailable_context(data)
        and (
            _safe_bool(data.get("user_blocked") or data.get("userBlocked"), False)
            or blocked_kind == "user_blocked"
            or code in USER_BLOCKED_CODES
        )
    )


def _is_access_blocked_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    return bool(
        _is_auth_unavailable_context(data)
        or _is_user_blocked_context(data)
        or _safe_bool(data.get("access_blocked") or data.get("accessBlocked"), False)
        or _safe_bool(data.get("blocked"), False)
    )


def _is_blocked_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    return _is_access_blocked_context(current_user)


def _context_status_code(current_user: Optional[Mapping[str, Any]] = None) -> int:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)

    if _is_auth_unavailable_context(data):
        return 503

    status = _safe_int(data.get("denial_status_code") or data.get("denialStatusCode") or data.get("status_code"), 0)
    if status > 0:
        return status

    if _is_user_blocked_context(data) or _is_access_blocked_context(data):
        return 403

    if not _safe_bool(data.get("authenticated") or data.get("is_authenticated") or data.get("isAuthenticated"), False):
        return 401

    return 403


def _is_demo_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)

    if _is_access_blocked_context(data):
        return False

    return bool(
        _safe_bool(data.get("demo_mode") or data.get("demoMode") or data.get("is_demo"), False)
        and _safe_bool(data.get("can_demo") or data.get("canDemo"), True)
    )


def _is_persistent_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)

    if _is_access_blocked_context(data):
        return False

    if _is_demo_context(data):
        return False

    user_id = _safe_int(data.get("user_id") or data.get("userId") or data.get("id"), 0)
    return bool(_safe_bool(data.get("persistent"), False) and user_id > 0)


def _is_authenticated_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)

    if _is_access_blocked_context(data):
        return False

    return _safe_bool(data.get("authenticated") or data.get("is_authenticated") or data.get("isAuthenticated"), False)


def _current_user_id_optional() -> Optional[int]:
    try:
        current_user = _current_user_payload(ensure=False)

        if not _is_persistent_context(current_user):
            return None

        value = get_current_user_id_optional()
        parsed = _safe_int(value, 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _auth_dependency_payload() -> Dict[str, Any]:
    try:
        if callable(get_auth_dependency_status):
            return _safe_dict(get_auth_dependency_status(include_private=False))
    except Exception as exc:
        return {
            "ok": False,
            "code": "auth_dependency_status_failed",
            "error": str(exc),
        }

    return {
        "ok": False,
        "code": "auth_dependency_status_unavailable",
    }


@bp.before_request
def _ui_projects_before_request() -> None:
    """
    Preload auth context only.

    No default user creation.
    No local fallback identity.
    """
    try:
        _current_user_payload(ensure=False)
    except Exception as exc:
        _log_warning("project UI auth preload failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Response / security helpers
# ─────────────────────────────────────────────────────────────

def _workspace_csp_header_value() -> str:
    try:
        auth_public = _config_url("VECTOPLAN_AUTH_PUBLIC_URL", "http://localhost:5000")
        editor_public = _config_url("VECTOPLAN_EDITOR_PUBLIC_URL", "http://localhost:5100")
        cad_public = _config_url("VECTOPLAN_CAD_PUBLIC_URL", "http://localhost:5104")
        lv_public = _config_url("VECTOPLAN_LV_PUBLIC_URL", "http://localhost:5105")
        openlayer_public = _config_url("OPENLAYER_PUBLIC_URL", "http://localhost:5190")
        chunk_public = _config_url("VECTOPLAN_CHUNK_PUBLIC_URL", "http://localhost:5102")
        library_public = _config_url("VECTOPLAN_LIBRARY_PUBLIC_URL", "http://localhost:5101")
        app_public = _config_url("VECTOPLAN_APP_PUBLIC_URL", "http://localhost:5103")

        frame_src_items = [
            "'self'",
            auth_public,
            "http://127.0.0.1:5000",
            editor_public,
            "http://127.0.0.1:5100",
            cad_public,
            "http://127.0.0.1:5104",
            lv_public,
            "http://127.0.0.1:5105",
            openlayer_public,
            "http://127.0.0.1:5190",
        ]

        connect_src_items = [
            "'self'",
            auth_public,
            "http://127.0.0.1:5000",
            editor_public,
            "http://127.0.0.1:5100",
            cad_public,
            "http://127.0.0.1:5104",
            chunk_public,
            "http://127.0.0.1:5102",
            library_public,
            "http://127.0.0.1:5101",
            openlayer_public,
            "http://127.0.0.1:5190",
        ]

        parents = [
            "'self'",
            app_public,
            "http://localhost:5103",
            "http://127.0.0.1:5103",
        ]

        frame_src: List[str] = []
        connect_src: List[str] = []
        frame_ancestors: List[str] = []

        for item in frame_src_items:
            text = _safe_str(item, "", 240)
            if text and text not in frame_src:
                frame_src.append(text)

        for item in connect_src_items:
            text = _safe_str(item, "", 240)
            if text and text not in connect_src:
                connect_src.append(text)

        for item in parents:
            text = _safe_str(item, "", 240)
            if text and text not in frame_ancestors:
                frame_ancestors.append(text)

        return (
            f"frame-src {' '.join(frame_src)}; "
            f"child-src {' '.join(frame_src)}; "
            f"connect-src {' '.join(connect_src)}; "
            f"frame-ancestors {' '.join(frame_ancestors)}"
        )

    except Exception:
        return (
            "frame-src 'self' http://localhost:5000 http://127.0.0.1:5000 "
            "http://localhost:5104 http://127.0.0.1:5104 "
            "http://localhost:5105 http://127.0.0.1:5105 "
            "http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "child-src 'self' http://localhost:5000 http://127.0.0.1:5000 "
            "http://localhost:5104 http://127.0.0.1:5104 "
            "http://localhost:5105 http://127.0.0.1:5105 "
            "http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "connect-src 'self' http://localhost:5000 http://127.0.0.1:5000 "
            "http://localhost:5104 http://127.0.0.1:5104 "
            "http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5102 http://127.0.0.1:5102 "
            "http://localhost:5101 http://127.0.0.1:5101 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "frame-ancestors 'self' http://localhost:5103 http://127.0.0.1:5103"
        )


def _finalize_json_response(resp: Response, *, no_store: bool = True) -> Response:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    except Exception:
        pass

    try:
        if no_store or _is_dev():
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass

    return resp


def _finalize_html_response(
    resp: Response,
    *,
    no_store: bool = False,
    workspace_shell: bool = False,
    allow_embed: bool = False,
) -> Response:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    except Exception:
        pass

    try:
        if no_store or _is_dev():
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass

    try:
        if workspace_shell:
            resp.headers["Content-Security-Policy"] = _workspace_csp_header_value()
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        elif allow_embed:
            resp.headers["Content-Security-Policy"] = (
                "frame-ancestors 'self' http://localhost:5103 http://127.0.0.1:5103"
            )
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    except Exception:
        pass

    return resp


def _json_response(payload: Dict[str, Any], status: int = 200) -> Response:
    try:
        body = _safe_dict(payload)
        body.setdefault("status_code", int(status))
        resp = jsonify(body)
        resp.status_code = int(status)
        return _finalize_json_response(resp, no_store=True)

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
        return _finalize_json_response(fallback, no_store=True)


def _json_error(
    message: str,
    status: int = 500,
    *,
    code: str = "error",
    extra: Optional[Dict[str, Any]] = None,
) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "error": message,
        "message": message,
        "code": code,
        "status_code": status,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status)


def _exception_response(message: str, exc: Exception, *, code: str = "internal_error") -> Response:
    _log_exception(message, exc)
    return _json_error(str(exc), 500, code=code)


def _permission_error_response(exc: PermissionDenied) -> Response:
    try:
        status = _safe_int(getattr(exc, "status_code", None), 403)
        if status <= 0:
            status = 403

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
                "status_code": status,
            }

        payload.setdefault("status_code", status)

        if _request_wants_json(default=False):
            return _json_response(payload, status)

        if status == 503:
            return _auth_problem_html_response(
                title="Auth-Service nicht erreichbar",
                message="vectoplan-auth ist nicht erreichbar. Die Projektoberfläche ist vorübergehend gesperrt.",
                status_code=503,
                reason=_safe_str(payload.get("code"), "auth_service_unavailable", 160),
                current_user=_safe_dict(payload.get("auth")),
            )

        return _auth_problem_html_response(
            title="Zugriff verweigert",
            message=_safe_str(payload.get("message") or payload.get("error"), "Zugriff verweigert.", 500),
            status_code=status,
            reason=_safe_str(payload.get("code"), "permission_denied", 160),
            current_user=_safe_dict(payload.get("auth")),
        )

    except Exception:
        return _json_error("permission denied", 403, code="permission_denied")


def _auth_problem_html_response(
    *,
    title: str,
    message: str,
    status_code: int,
    reason: str,
    current_user: Optional[Mapping[str, Any]] = None,
) -> Response:
    auth = _safe_dict(current_user)
    auth_dependency = _auth_dependency_payload() if status_code == 503 else {}

    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f7fb;
      color: #172033;
    }
    * {
      box-sizing: border-box;
    }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background: #f4f7fb;
    }
    main {
      width: min(680px, 100%);
      border: 1px solid #d8e0ec;
      border-top: 4px solid #2563eb;
      border-radius: 10px;
      padding: clamp(22px, 4vw, 34px);
      background: #ffffff;
      box-shadow: 0 18px 48px rgba(31, 48, 77, .10);
    }
    h1 {
      margin: 0 0 12px;
      color: #111827;
      font-size: clamp(22px, 4vw, 28px);
      line-height: 1.2;
      letter-spacing: -.02em;
    }
    p {
      margin: 0 0 16px;
      color: #536176;
      line-height: 1.6;
    }
    code {
      padding: 2px 6px;
      border: 1px solid #d8e0ec;
      border-radius: 4px;
      background: #eef3f9;
      color: #26364d;
    }
    a {
      color: #155eef;
      font-weight: 650;
      text-underline-offset: 3px;
    }
    .meta {
      margin-top: 20px;
      padding-top: 14px;
      border-top: 1px solid #e2e8f0;
      font-size: 13px;
      color: #68758a;
    }
    @media (max-width: 540px) {
      body {
        place-items: start stretch;
        padding: 12px;
      }
      main {
        margin-top: 8vh;
      }
    }
  </style>
</head>
<body>
  <main>
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    {% if status_code == 503 %}
      <p>Das ist kein Benutzer-Ban. Die App kann den zentralen Auth-Service aktuell nicht erreichen und sperrt deshalb fail-closed.</p>
    {% endif %}
    {% if login_url %}
      <p><a href="{{ login_url }}">Zur Anmeldung</a></p>
    {% endif %}
    <div class="meta">
      Status {{ status_code }} · <code>{{ reason }}</code>
    </div>
  </main>
</body>
</html>
"""

    login_url = None
    if status_code != 503 and auth:
        login_url = _safe_str(
            auth.get("login_url")
            or auth.get("loginUrl")
            or _safe_dict(auth.get("links")).get("login_url")
            or _safe_dict(auth.get("links")).get("login"),
            "",
            800,
        ) or None

    try:
        rendered = render_template_string(
            html,
            title=title,
            message=message,
            status_code=status_code,
            reason=reason,
            login_url=login_url,
            auth=auth,
            auth_dependency=auth_dependency,
        )
        resp = make_response(rendered, status_code)
        return _finalize_html_response(resp, no_store=True, workspace_shell=False)
    except Exception:
        return _json_error(
            message,
            status_code,
            code=reason,
            extra={
                "auth": auth,
                "auth_dependency": auth_dependency,
            },
        )


def _blocked_response(current_user: Mapping[str, Any]) -> Response:
    user = _safe_dict(current_user)

    if _is_auth_unavailable_context(user):
        if _request_wants_json(default=False):
            return _json_error(
                "vectoplan-auth ist nicht erreichbar.",
                503,
                code=_context_code(user) or "auth_service_unavailable",
                extra={
                    "auth": user,
                    "auth_unavailable": True,
                    "blocked": True,
                    "blocked_kind": "auth_unavailable",
                    "auth_dependency": _auth_dependency_payload(),
                },
            )

        return _auth_problem_html_response(
            title="Auth-Service nicht erreichbar",
            message="vectoplan-auth ist nicht erreichbar. Die Projektoberfläche ist vorübergehend gesperrt.",
            status_code=503,
            reason=_context_code(user) or "auth_service_unavailable",
            current_user=user,
        )

    if _is_user_blocked_context(user):
        if _request_wants_json(default=False):
            return _json_error(
                "Dieser Zugang ist gesperrt.",
                403,
                code=_context_code(user) or "auth_blocked",
                extra={
                    "auth": user,
                    "blocked": True,
                    "blocked_kind": "user_blocked",
                },
            )

        return _auth_problem_html_response(
            title="Zugriff gesperrt",
            message="Dieser Zugang ist gesperrt.",
            status_code=403,
            reason=_context_code(user) or "auth_blocked",
            current_user=user,
        )

    if _request_wants_json(default=False):
        return _json_error(
            "Der Zugriff ist gesperrt.",
            _context_status_code(user),
            code=_context_code(user) or "access_blocked",
            extra={
                "auth": user,
                "blocked": True,
                "blocked_kind": _safe_str(user.get("blocked_kind"), "access_blocked", 80),
            },
        )

    return _auth_problem_html_response(
        title="Zugriff verweigert",
        message="Der Zugriff ist gesperrt.",
        status_code=_context_status_code(user),
        reason=_context_code(user) or "access_blocked",
        current_user=user,
    )


# ─────────────────────────────────────────────────────────────
# Project / conversation helpers
# ─────────────────────────────────────────────────────────────

def _is_new_project_identifier(value: Any) -> bool:
    try:
        text = _safe_str(value, "", 80).lower()
        return text in {"", "new", "create", "neu", "projekt-neu", "project-new"}
    except Exception:
        return False


def _current_project_identifier_from_request() -> str:
    try:
        candidates = [
            request.args.get("project"),
            request.args.get("project_id"),
            request.args.get("p"),
            request.view_args.get("project_id") if request.view_args else None,
        ]

        for candidate in candidates:
            value = _safe_str(candidate, "", 160)
            if value:
                return value

        return ""

    except Exception:
        return ""


def _project_is_demo(project: Optional[Any]) -> bool:
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

        if _safe_str(getattr(project, "project_scope", ""), "", 80).lower() == "demo":
            return True

        metadata = _safe_dict(getattr(project, "metadata_json", None))
        demo_meta = _safe_dict(metadata.get("vectoplan_demo"))
        return _safe_bool(demo_meta.get("enabled"), False)

    except Exception:
        return False


def _load_selected_project(project_identifier: Optional[str]) -> Optional[Any]:
    try:
        if _is_new_project_identifier(project_identifier):
            return None

        if resolve_project is None:
            return None

        return resolve_project(project_identifier)

    except Exception:
        return None


def _platform_context_auth_unavailable(platform_context: Any) -> bool:
    try:
        return bool(getattr(platform_context, "auth_unavailable", False))
    except Exception:
        return False


def _load_current_demo_project() -> Optional[Any]:
    try:
        platform_context = _platform_auth_context()

        if platform_context is None:
            return None

        if _platform_context_auth_unavailable(platform_context):
            return None

        if not bool(getattr(platform_context, "can_demo", False)):
            return None

        if callable(get_current_demo_project):
            result = get_current_demo_project(
                context=platform_context,
                create=True,
                commit=True,
            )

            if getattr(result, "ok", False) and getattr(result, "project", None) is not None:
                return result.project

        if callable(ensure_demo_project_for_context):
            result = ensure_demo_project_for_context(
                context=platform_context,
                reset=False,
                commit=True,
            )

            if getattr(result, "ok", False) and getattr(result, "project", None) is not None:
                return result.project

        return None

    except Exception as exc:
        _log_warning("load current demo project failed: %s", exc.__class__.__name__)
        return None


def _project_public_id(project: Optional[Any], fallback: str = "new") -> str:
    try:
        if project is None:
            return fallback

        return (
            _safe_str(getattr(project, "public_id", None), "", 160)
            or _safe_str(getattr(project, "publicId", None), "", 160)
            or _safe_str(getattr(project, "id", None), fallback, 160)
        )

    except Exception:
        return fallback


def _new_conversation(
    *,
    title: str = "Projekt-Shell",
    project: Optional[Any] = None,
    project_id: Optional[str] = None,
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Any:
    if Conversation is None or db is None:
        return {"id": "local-shell", "title": title, "project_id": project_id}

    conv = Conversation()

    try:
        conv.title = _safe_str(title, "Projekt-Shell", 255)

        resolved_project_id = project_id

        if resolved_project_id is None and project is not None:
            resolved_project_id = str(getattr(project, "id", "") or "")

        if resolved_project_id:
            conv.project_id = _safe_str(resolved_project_id, "", 120) or None

        if user_id and hasattr(conv, "owner_user_id"):
            conv.owner_user_id = user_id
        elif project is not None and hasattr(conv, "owner_user_id"):
            conv.owner_user_id = getattr(project, "owner_user_id", None)

        if hasattr(conv, "transcript"):
            conv.transcript = []

        if hasattr(conv, "state"):
            conv.state = {}

        if hasattr(conv, "status"):
            conv.status = "active"

        if hasattr(conv, "metadata_json"):
            conv.metadata_json = {
                "source": "routes.ui.projects",
                "shell": project is None,
                "project_public_id": _project_public_id(project, ""),
                "default_user_removed": True,
            }

        if hasattr(conv, "normalize"):
            conv.normalize()

        db.session.add(conv)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return conv

    except Exception:
        if commit:
            try:
                db.session.rollback()
            except Exception:
                pass
        raise


def _get_or_create_shell_conversation(user_id: Optional[int]) -> Any:
    if not user_id:
        return {"id": "anonymous-shell", "title": "Projekt-Shell", "project_id": "__anonymous_project_shell__"}

    if Conversation is None or db is None:
        return {"id": "local-shell", "title": "Projekt-Shell", "project_id": "__project_shell__"}

    try:
        query = Conversation.query.filter_by(project_id="__project_shell__")

        if hasattr(Conversation, "owner_user_id"):
            query = query.filter_by(owner_user_id=user_id)

        if hasattr(Conversation, "created_at"):
            query = query.order_by(Conversation.created_at.desc())

        conv = query.first()

        if conv is not None:
            return conv

        return _new_conversation(
            title="Projekt-Shell",
            project_id="__project_shell__",
            user_id=user_id,
            commit=True,
        )

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return _new_conversation(
            title="Projekt-Shell",
            project_id="__project_shell__",
            user_id=user_id,
            commit=True,
        )


def _ensure_project_conversation(project: Optional[Any], *, current_user: Mapping[str, Any]) -> Any:
    user_id = _current_user_id_optional()

    if _is_auth_unavailable_context(current_user):
        return {
            "id": "auth-unavailable",
            "title": "Auth-Service nicht erreichbar",
            "project_id": _project_public_id(project, "new"),
        }

    if _project_is_demo(project) or not _is_persistent_context(current_user):
        return {
            "id": "demo" if _is_demo_context(current_user) else "anonymous",
            "title": "Demo-Projekt-Shell" if _is_demo_context(current_user) else "Projekt-Shell",
            "project_id": _project_public_id(project, "new"),
        }

    try:
        if project is None:
            return _get_or_create_shell_conversation(user_id)

        if get_or_create_project_conversation is not None:
            conv = get_or_create_project_conversation(project, commit=True)
            if conv is not None:
                return conv

        return _get_or_create_shell_conversation(user_id)

    except Exception:
        try:
            if db is not None:
                db.session.rollback()
        except Exception:
            pass

        return _get_or_create_shell_conversation(user_id)


def _conversation_id(conversation: Any) -> str:
    try:
        if isinstance(conversation, Mapping):
            return _safe_str(conversation.get("id"), "demo", 120)

        return _safe_str(getattr(conversation, "id", None), "demo", 120)
    except Exception:
        return "demo"


# ─────────────────────────────────────────────────────────────
# Project payload helpers
# ─────────────────────────────────────────────────────────────

def _new_publication_payload(*, demo: bool = False) -> Dict[str, Any]:
    return {
        "visibility": "private",
        "publication_enabled": False,
        "published": False,
        "public": False,
        "is_public": False,
        "reason": "demo_projects_are_not_publishable" if demo else "not_published",
        "published_workspaces": {
            "project": False,
            "map": False,
            "editor3d": False,
            "cad2d": False,
            "lv": False,
            "versions": False,
        },
        "publishedWorkspaces": {
            "project": False,
            "map": False,
            "editor3d": False,
            "cad2d": False,
            "lv": False,
            "versions": False,
        },
        "effective_published_workspaces": {
            "project": False,
            "map": False,
            "editor3d": False,
            "cad2d": False,
            "lv": False,
            "versions": False,
        },
        "effectivePublishedWorkspaces": {
            "project": False,
            "map": False,
            "editor3d": False,
            "cad2d": False,
            "lv": False,
            "versions": False,
        },
        "require_auth": not demo,
        "requireAuth": not demo,
        "require_project_permission": True,
        "requireProjectPermission": True,
    }


def _new_project_payload(current_user: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    user = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    demo_mode = _is_demo_context(user)
    persistent = _is_persistent_context(user)
    blocked = _is_access_blocked_context(user)

    can_view = not blocked
    can_edit = bool(persistent and not demo_mode and not blocked)
    can_manage = bool(can_edit)

    return {
        "isNew": True,
        "is_new": True,
        "id": None,
        "project_id": None,
        "public_id": "new",
        "publicId": "new",
        "projectPublicId": "new",
        "appProjectPublicId": "new",
        "name": "",
        "display_name": "Neues Projekt",
        "displayName": "Neues Projekt",
        "description": "",
        "address_text": "",
        "addressText": "",
        "address": {
            "text": "",
        },
        "visibility": "private",
        "is_public": False,
        "isPublic": False,
        "setup_status": "draft",
        "setupStatus": "draft",
        "is_configured": False,
        "isConfigured": False,
        "status": "draft",
        "demo_mode": demo_mode,
        "demoMode": demo_mode,
        "blocked": blocked,
        "auth_unavailable": _is_auth_unavailable_context(user),
        "publication": _new_publication_payload(demo=demo_mode),
        "members": [],
        "invitations": [],
        "url": "/project=new",
        "href": "/project=new",
        "paths": {
            "projectPagePath": "/ui/project/new/project",
            "projectUrl": "/ui/project/new/project",
            "projectPublicUrl": "/project=new",
            "contextPath": "/ui/project/new/context.json",
            "editorPagePath": "",
            "initialEditorUrl": "",
            "mapPagePath": "",
            "cad2dPagePath": "",
            "lvPagePath": "",
            "versionsPagePath": "",
            "adminPagePath": "",
            "publicationPath": "",
            "membersPath": "",
            "invitationsPath": "",
            "apiPath": "/v1/projects",
            "stateGetPath": "",
            "statePutPath": "",
        },
        "access": {
            "role": "owner" if can_manage else "viewer",
            "source": "new_project",
            "permissions": {
                "view": can_view,
                "edit": can_edit,
                "manage": can_manage,
                "delete": False,
                "transfer": False,
                "embed": can_manage,
                "view_settings": can_manage,
                "manage_settings": can_manage,
                "view_team": can_manage,
                "manage_team": can_manage,
                "view_admin": can_manage,
            },
            "can_view": can_view,
            "can_edit": can_edit,
            "can_manage": can_manage,
            "can_delete": False,
            "can_transfer": False,
            "can_embed": can_manage,
            "can_view_settings": can_manage,
            "can_manage_settings": can_manage,
            "can_view_team": can_manage,
            "can_manage_team": can_manage,
            "can_view_admin": can_manage,
            "is_owner": can_manage,
        },
    }


def _serialize_permissions(project: Any, user_id: Optional[int]) -> Dict[str, Any]:
    try:
        if serialize_project_permissions is not None:
            return _safe_dict(serialize_project_permissions(project, user_id=user_id))
    except Exception:
        pass

    return {
        "can_view": False,
        "can_edit": False,
        "can_manage": False,
        "permissions": {},
        "source": "permission_serializer_unavailable",
    }


def _can_manage_project(project: Any, user_id: Optional[int]) -> bool:
    access = _serialize_permissions(project, user_id)
    permissions = _safe_dict(access.get("permissions"))

    return _safe_bool(
        access.get("can_manage")
        or permissions.get("manage")
        or permissions.get("manage_settings"),
        False,
    )


def _serialize_project_safe(project: Any, *, user_id: Optional[int]) -> Dict[str, Any]:
    if serialize_project is None:
        return {
            "id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "publicId": getattr(project, "public_id", None),
            "name": getattr(project, "name", ""),
            "display_name": getattr(project, "name", ""),
            "displayName": getattr(project, "name", ""),
            "description": getattr(project, "description", ""),
            "address_text": getattr(project, "address_text", ""),
            "addressText": getattr(project, "address_text", ""),
            "address": {"text": getattr(project, "address_text", "")},
            "visibility": "private" if _project_is_demo(project) else getattr(project, "visibility", "private"),
            "setup_status": getattr(project, "setup_status", "draft"),
            "setupStatus": getattr(project, "setup_status", "draft"),
            "is_configured": bool(getattr(project, "is_configured", False)),
            "isConfigured": bool(getattr(project, "is_configured", False)),
            "is_demo": _project_is_demo(project),
            "isDemo": _project_is_demo(project),
            "access": _serialize_permissions(project, user_id),
        }

    include_private = bool(_can_manage_project(project, user_id) and not _project_is_demo(project))

    try:
        return _safe_dict(
            serialize_project(
                project,
                user_id=user_id,
                include_permissions=True,
                include_members=include_private,
                include_service_links=include_private,
                include_versions=True,
                include_embed_policy=include_private,
                include_publication=True,
            )
        )
    except TypeError:
        try:
            return _safe_dict(
                serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_members=include_private,
                    include_service_links=include_private,
                    include_versions=True,
                    include_embed_policy=include_private,
                )
            )
        except TypeError:
            return _safe_dict(serialize_project(project, user_id=user_id))


def _attach_publication(project: Any, payload: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    try:
        if _project_is_demo(project):
            payload["publication"] = _new_publication_payload(demo=True)
            return payload

        if get_project_publication is None:
            payload.setdefault("publication", _new_publication_payload())
            return payload

        publication_result = get_project_publication(
            project,
            actor_user_id=user_id,
            include_private=_can_manage_project(project, user_id),
            for_public=not bool(user_id),
            use_cache=False,
        )

        publication = _safe_dict(publication_result.get("publication"))

        if publication:
            payload["publication"] = publication
        else:
            payload.setdefault("publication", _new_publication_payload())

        return payload

    except Exception:
        payload.setdefault("publication", _new_publication_payload(demo=_project_is_demo(project)))
        return payload


def _attach_demo_payload(project: Any, payload: Dict[str, Any], current_user: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        if not _project_is_demo(project):
            return payload

        payload["is_demo"] = True
        payload["isDemo"] = True
        payload["demo_mode"] = True
        payload["demoMode"] = True
        payload["visibility"] = "private"
        payload["is_public"] = False
        payload["isPublic"] = False

        if callable(demo_project_public_payload):
            try:
                platform_context = _platform_auth_context()
                payload["demo"] = demo_project_public_payload(project, platform_context)
            except Exception:
                payload["demo"] = {
                    "is_demo": True,
                    "expires_at": _safe_str(getattr(project, "demo_expires_at", None), "", 120) or None,
                }
        else:
            payload["demo"] = {
                "is_demo": True,
                "expires_at": _safe_str(getattr(project, "demo_expires_at", None), "", 120) or None,
            }

        access = _safe_dict(payload.get("access"))
        permissions = _safe_dict(access.get("permissions"))
        permissions.update(
            {
                "view": True,
                "edit": True,
                "manage": False,
                "delete": False,
                "transfer": False,
                "embed": True,
                "view_settings": False,
                "manage_settings": False,
                "view_team": False,
                "manage_team": False,
                "view_admin": False,
            }
        )

        access.update(
            {
                "role": "editor",
                "source": access.get("source") or "demo_project",
                "permissions": permissions,
                "can_view": True,
                "can_edit": True,
                "can_manage": False,
                "can_delete": False,
                "can_transfer": False,
                "can_embed": True,
                "can_view_settings": False,
                "can_manage_settings": False,
                "can_view_team": False,
                "can_manage_team": False,
                "can_view_admin": False,
            }
        )

        payload["access"] = access

        return payload

    except Exception:
        return payload


def _project_payload_for_template(
    project: Optional[Any],
    *,
    current_user: Mapping[str, Any],
    is_new: bool = False,
    access_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    user_id = _current_user_id_optional()

    if project is None:
        payload = _new_project_payload(current_user)
        if access_context:
            payload = _apply_access_context_to_project_payload(payload, access_context)
        return payload

    try:
        payload = _serialize_project_safe(project, user_id=user_id)

        if not payload.get("access"):
            payload["access"] = _serialize_permissions(project, user_id)

        payload = _attach_publication(project, payload, user_id)
        payload = _attach_demo_payload(project, payload, current_user)

        if access_context:
            payload = _apply_access_context_to_project_payload(payload, access_context)

        public_id = _project_public_id(project, "new")
        paths = _project_paths(public_id, is_demo=_project_is_demo(project))

        existing_paths = _safe_dict(payload.get("paths"))
        existing_paths.update({key: value for key, value in paths.items() if value is not None})
        payload["paths"] = existing_paths

        payload["isNew"] = False
        payload["is_new"] = False
        payload["public_id"] = payload.get("public_id") or public_id
        payload["publicId"] = payload.get("publicId") or public_id
        payload["projectPublicId"] = public_id
        payload["appProjectPublicId"] = public_id
        payload["demo_mode"] = _is_demo_context(current_user)
        payload["demoMode"] = _is_demo_context(current_user)
        payload["url"] = f"/project={_safe_quote(public_id)}"
        payload["href"] = f"/project={_safe_quote(public_id)}"

        return payload

    except Exception:
        if is_new:
            return _new_project_payload(current_user)

        return {
            "isNew": False,
            "is_new": False,
            "public_id": "",
            "publicId": "",
            "name": "",
            "setup_status": "draft",
            "is_configured": False,
            "publication": _new_publication_payload(demo=_is_demo_context(current_user)),
            "access": {
                "can_view": False,
                "can_edit": False,
                "can_manage": False,
                "permissions": {},
            },
            "url": "/project=new",
            "href": "/project=new",
        }


# ─────────────────────────────────────────────────────────────
# Shell context helpers
# ─────────────────────────────────────────────────────────────

def _project_paths(public_id: str, *, is_demo: bool = False) -> Dict[str, str]:
    public_id = _safe_str(public_id, "new", 160)
    public_id_q = _safe_quote(public_id)

    if not public_id or public_id == "new":
        return {
            "projectPagePath": "/ui/project/new/project",
            "projectUrl": "/ui/project/new/project",
            "projectPublicUrl": "/project=new",
            "contextPath": "/ui/project/new/context.json",
            "editorPagePath": "",
            "initialEditorUrl": "",
            "mapPagePath": "",
            "cad2dPagePath": "",
            "lvPagePath": "",
            "versionsPagePath": "",
            "adminPagePath": "",
            "publicationPath": "",
            "membersPath": "",
            "invitationsPath": "",
            "apiPath": "/v1/projects",
            "stateGetPath": "",
            "statePutPath": "",
        }

    paths = {
        "projectPagePath": f"/ui/project/{public_id_q}/project",
        "projectUrl": f"/ui/project/{public_id_q}/project",
        "projectPublicUrl": f"/project={public_id_q}",
        "contextPath": f"/ui/project/{public_id_q}/context.json",
        "editorPagePath": f"/ui/project/{public_id_q}/editor3d",
        "initialEditorUrl": f"/ui/project/{public_id_q}/editor3d",
        "mapPagePath": f"/ui/project/{public_id_q}/map",
        "cad2dPagePath": f"/ui/project/{public_id_q}/cad2d",
        "lvPagePath": f"/ui/project/{public_id_q}/lv",
        "versionsPagePath": "" if is_demo else f"/ui/project/{public_id_q}/versions",
        "adminPagePath": "" if is_demo else f"/ui/project/{public_id_q}/admin",
        "publicationPath": "" if is_demo else f"/v1/projects/{public_id_q}/publication",
        "membersPath": "" if is_demo else f"/v1/projects/{public_id_q}/members",
        "invitationsPath": "" if is_demo else f"/v1/projects/{public_id_q}/invitations",
        "apiPath": f"/v1/projects/{public_id_q}",
        "stateGetPath": "",
        "statePutPath": "",
    }

    return paths


def _workspace_context_for_project(
    *,
    project: Optional[Any],
    project_payload: Mapping[str, Any],
    conversation: Any,
    current_user: Mapping[str, Any],
    is_new: bool,
) -> Dict[str, Any]:
    try:
        chat_id = _conversation_id(conversation)
        chat_id_q = _safe_quote(chat_id)

        project_is_demo = _project_is_demo(project)

        public_id = _safe_str(
            project_payload.get("public_id")
            or project_payload.get("publicId")
            or _project_public_id(project, "new"),
            "new",
            160,
        )

        paths = _project_paths(public_id, is_demo=project_is_demo)

        if _is_persistent_context(current_user) and chat_id and chat_id not in {"demo", "anonymous", "auth-unavailable"}:
            paths["stateGetPath"] = f"/v1/chats/{chat_id_q}/viewer/selection"
            paths["statePutPath"] = f"/v1/chats/{chat_id_q}/viewer/selection"

        existing_paths = _safe_dict(project_payload.get("paths"))
        existing_paths.update({key: value for key, value in paths.items() if value is not None})

        return {
            "chat_id": chat_id,
            "project_id": project_payload.get("id") or project_payload.get("project_id"),
            "project_public_id": public_id,
            "app_project_public_id": public_id,
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": existing_paths.get("projectPagePath") or paths["projectPagePath"],
            "project_public_url": existing_paths.get("projectPublicUrl") or paths["projectPublicUrl"],
            "project_configured": _safe_bool(
                project_payload.get("is_configured") or project_payload.get("isConfigured"),
                False,
            ),
            "is_new": bool(is_new),
            "is_demo": project_is_demo,
            "editor_url": existing_paths.get("editorPagePath", ""),
            "viewer_url": existing_paths.get("projectPagePath") or paths["projectPagePath"],
            "map_url": existing_paths.get("mapPagePath", ""),
            "cad2d_url": existing_paths.get("cad2dPagePath", ""),
            "lv_url": existing_paths.get("lvPagePath", ""),
            "versions_url": existing_paths.get("versionsPagePath", ""),
            "admin_url": existing_paths.get("adminPagePath", ""),
            "paths": existing_paths,
            "demo_mode": _is_demo_context(current_user),
            "persistent": _is_persistent_context(current_user),
            "auth_unavailable": _is_auth_unavailable_context(current_user),
        }

    except Exception:
        return {
            "chat_id": _conversation_id(conversation),
            "project_public_id": "new",
            "app_project_public_id": "new",
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": "/ui/project/new/project",
            "project_public_url": "/project=new",
            "project_configured": False,
            "is_new": True,
            "is_demo": False,
            "editor_url": "",
            "viewer_url": "/ui/project/new/project",
            "map_url": "",
            "paths": _project_paths("new"),
            "demo_mode": _is_demo_context(current_user),
            "persistent": _is_persistent_context(current_user),
            "auth_unavailable": _is_auth_unavailable_context(current_user),
        }


def _project_sidebar_context(
    *,
    selected_project: Optional[Any],
    conversation: Any,
    current_user: Mapping[str, Any],
    access_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        user_id = _current_user_id_optional()
        chat_id = _conversation_id(conversation)
        selected_public_id = _project_public_id(selected_project, "new")
        access_data = _safe_dict(access_context)
        is_public_viewer = _access_context_public_viewer(access_data)

        if is_public_viewer:
            title = getattr(selected_project, "name", None) if selected_project is not None else "Öffentliches Projekt"
            subtitle = getattr(selected_project, "address_text", None) if selected_project is not None else ""
            public_item = {
                "id": selected_public_id,
                "projectId": selected_public_id,
                "public_id": selected_public_id,
                "title": title or "Öffentliches Projekt",
                "subtitle": subtitle or "Öffentliche Ansicht",
                "href": f"/project={_safe_quote(selected_public_id)}",
                "project_url": f"/project={_safe_quote(selected_public_id)}",
                "isActive": True,
                "is_active": True,
                "isDemo": False,
                "is_demo": False,
                "isPublic": True,
                "is_public": True,
                "isPublicViewer": True,
                "is_public_viewer": True,
                "readOnly": True,
                "read_only": True,
                "source": "public_viewer",
            }

            return {
                "enabled": True,
                "currentChatId": chat_id,
                "currentProjectId": selected_public_id,
                "current_project_id": selected_public_id,
                "currentTitle": title or "Öffentliches Projekt",
                "currentSubtitle": subtitle or "Öffentliche Ansicht",
                "defaultCollapsed": False,
                "defaultWidth": 280,
                "minWidth": 220,
                "maxWidth": 420,
                "collapsedWidth": 64,
                "storageKey": "vectoplan.projectSidebar.v1",
                "routeBase": "/",
                "apiPath": "/v1/projects/sidebar",
                "items": [public_item] if selected_project is not None else [],
                "demo_mode": False,
                "public_viewer": True,
                "read_only": True,
                "access_mode": "public",
            }

        if _is_auth_unavailable_context(current_user):
            return {
                "enabled": True,
                "currentChatId": chat_id,
                "currentProjectId": "",
                "current_project_id": "",
                "currentTitle": "Auth-Service nicht erreichbar",
                "currentSubtitle": "Projektliste nicht verfügbar",
                "defaultCollapsed": False,
                "defaultWidth": 280,
                "minWidth": 220,
                "maxWidth": 420,
                "collapsedWidth": 64,
                "storageKey": "vectoplan.projectSidebar.v1",
                "routeBase": "/",
                "apiPath": "/v1/projects/sidebar",
                "items": [],
                "demo_mode": False,
                "auth_unavailable": True,
            }

        if _is_demo_context(current_user):
            items = []
            if selected_project is not None:
                if serialize_project_sidebar_item is not None:
                    try:
                        items = [serialize_project_sidebar_item(selected_project, user_id=None)]
                    except Exception:
                        items = []
                if not items:
                    items = [
                        {
                            "id": selected_public_id,
                            "projectId": selected_public_id,
                            "public_id": selected_public_id,
                            "title": getattr(selected_project, "name", None) or "Demo-Projekt",
                            "subtitle": "Temporäres Demo-Projekt",
                            "href": f"/project={_safe_quote(selected_public_id)}",
                            "isDemo": True,
                            "is_demo": True,
                            "isActive": True,
                            "is_active": True,
                            "source": "demo",
                        }
                    ]

                for item in items:
                    item["isActive"] = True
                    item["is_active"] = True
                    item["isDemo"] = True
                    item["is_demo"] = True

            return {
                "enabled": True,
                "currentChatId": chat_id,
                "currentProjectId": selected_public_id,
                "current_project_id": selected_public_id,
                "currentTitle": getattr(selected_project, "name", None) if selected_project is not None else "Demo-Projekt",
                "currentSubtitle": "Temporäres Demo-Projekt",
                "defaultCollapsed": False,
                "defaultWidth": 280,
                "minWidth": 220,
                "maxWidth": 420,
                "collapsedWidth": 64,
                "storageKey": "vectoplan.projectSidebar.v1",
                "routeBase": "/",
                "apiPath": "/v1/projects/sidebar",
                "items": items,
                "demo_mode": True,
            }

        if not user_id or not _is_persistent_context(current_user):
            return {
                "enabled": True,
                "currentChatId": chat_id,
                "currentProjectId": selected_public_id,
                "current_project_id": selected_public_id,
                "currentTitle": getattr(selected_project, "name", None) if selected_project is not None else "Neues Projekt",
                "currentSubtitle": getattr(selected_project, "address_text", None) if selected_project is not None else "Projekt definieren",
                "defaultCollapsed": False,
                "defaultWidth": 280,
                "minWidth": 220,
                "maxWidth": 420,
                "collapsedWidth": 64,
                "storageKey": "vectoplan.projectSidebar.v1",
                "routeBase": "/",
                "apiPath": "/v1/projects/sidebar",
                "items": [],
                "demo_mode": False,
            }

        items = []

        if list_project_sidebar_items is not None:
            items = list_project_sidebar_items(
                user_id=user_id,
                include_public=False,
                limit=200,
            ) or []

        enriched: List[Dict[str, Any]] = []

        for item in items:
            try:
                current_item = dict(item or {})
                public_id = (
                    _safe_str(current_item.get("public_id"), "", 120)
                    or _safe_str(current_item.get("publicId"), "", 120)
                    or _safe_str(current_item.get("projectId"), "", 120)
                    or _safe_str(current_item.get("id"), "", 120)
                )

                if public_id and public_id == selected_public_id:
                    current_item["isActive"] = True
                    current_item["is_active"] = True

                if not current_item.get("href") and public_id:
                    current_item["href"] = f"/project={_safe_quote(public_id)}"

                if not current_item.get("project_url") and public_id:
                    current_item["project_url"] = f"/project={_safe_quote(public_id)}"

                enriched.append(current_item)

            except Exception:
                continue

        return {
            "enabled": True,
            "currentChatId": chat_id,
            "currentProjectId": selected_public_id,
            "current_project_id": selected_public_id,
            "currentTitle": getattr(selected_project, "name", None) if selected_project is not None else "Neues Projekt",
            "currentSubtitle": getattr(selected_project, "address_text", None) if selected_project is not None else "Projekt definieren",
            "defaultCollapsed": False,
            "defaultWidth": 280,
            "minWidth": 220,
            "maxWidth": 420,
            "collapsedWidth": 64,
            "storageKey": "vectoplan.projectSidebar.v1",
            "routeBase": "/",
            "apiPath": "/v1/projects/sidebar",
            "items": enriched,
            "demo_mode": False,
        }

    except Exception:
        return {
            "enabled": True,
            "currentChatId": _conversation_id(conversation),
            "currentProjectId": "",
            "currentTitle": "Projekt",
            "currentSubtitle": "Projekt definieren",
            "items": [],
        }


def _notice_access_context(
    *,
    selected_project: Optional[Any],
    project_payload: Mapping[str, Any],
    workspace: Mapping[str, Any],
    current_user: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        access = _safe_dict(project_payload.get("access"))
        permissions = _safe_dict(access.get("permissions"))

        public_viewer = _safe_bool(
            access.get("public_viewer")
            or access.get("publicViewer")
            or access.get("is_public_viewer")
            or access.get("isPublicViewer")
            or access.get("is_unlisted_viewer")
            or access.get("isUnlistedViewer"),
            False,
        )

        read_only = _safe_bool(
            access.get("read_only")
            or access.get("readOnly")
            or access.get("readonly"),
            public_viewer,
        )

        if _is_demo_context(current_user):
            access_mode = "demo"
        elif public_viewer:
            access_mode = "public"
        elif _is_authenticated_context(current_user):
            access_mode = "authenticated"
        else:
            access_mode = "anonymous"

        return {
            "access_mode": access_mode,
            "mode": access_mode,
            "demo_mode": _is_demo_context(current_user),
            "public_viewer": public_viewer,
            "is_public_viewer": public_viewer,
            "read_only": read_only,
            "authenticated": _is_authenticated_context(current_user),
            "persistent": _is_persistent_context(current_user),
            "auth_unavailable": _is_auth_unavailable_context(current_user),
            "user_blocked": _is_user_blocked_context(current_user),
            "access_blocked": _is_access_blocked_context(current_user),
            "project_public_id": (
                _safe_str(project_payload.get("public_id"), "", 160)
                or _safe_str(project_payload.get("publicId"), "", 160)
                or _project_public_id(selected_project, "new")
            ),
            "project_id": project_payload.get("id") or project_payload.get("project_id"),
            "workspace_mode": _safe_str(workspace.get("workspace_mode"), "project", 80),
            "default_mode": _safe_str(workspace.get("default_mode"), "project", 80),
            "can_view": _safe_bool(access.get("can_view") or permissions.get("view"), False),
            "can_edit": _safe_bool(access.get("can_edit") or permissions.get("edit"), False),
            "can_manage": _safe_bool(
                access.get("can_manage")
                or permissions.get("manage")
                or permissions.get("manage_settings"),
                False,
            ),
            "source": "routes.ui.projects",
        }

    except Exception as exc:
        _log_warning("notice access context failed: %s", exc.__class__.__name__)
        return {
            "access_mode": "demo" if _is_demo_context(current_user) else "anonymous",
            "demo_mode": _is_demo_context(current_user),
            "public_viewer": False,
            "read_only": False,
            "authenticated": _is_authenticated_context(current_user),
            "auth_unavailable": _is_auth_unavailable_context(current_user),
            "user_blocked": _is_user_blocked_context(current_user),
            "access_blocked": _is_access_blocked_context(current_user),
            "project_public_id": _project_public_id(selected_project, "new"),
            "source": "routes.ui.projects.fallback",
        }


def _fallback_notice_stream(
    *,
    current_user: Mapping[str, Any],
    access_context: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        if not _is_demo_context(current_user):
            return {
                "ok": True,
                "enabled": False,
                "kind": "",
                "severity": "",
                "label": "",
                "title": "",
                "messages": [],
                "marquee_text": "",
                "access_mode": _safe_str(access_context.get("access_mode"), "", 80),
                "demo_mode": False,
                "public_viewer": _safe_bool(access_context.get("public_viewer"), False),
                "read_only": _safe_bool(access_context.get("read_only"), False),
                "source": "routes.ui.projects.fallback_notice",
                "reason": "no_active_notice",
                "metadata": {},
            }

        messages = [
            "Du befindest dich im Demo-Modus.",
            "Änderungen werden nur temporär gespeichert.",
            "Keine dauerhafte Projektspeicherung.",
            "Keine echten Team-Einladungen.",
            "Kein Zugriff auf Bigdata-/Abo-Datenquellen.",
            "Kein echter Account-Kontext.",
        ]
        label = "DEMO"
        marquee_text = "  ·  ".join([label] + messages)

        return {
            "ok": True,
            "enabled": True,
            "kind": "demo",
            "severity": "warning",
            "label": label,
            "title": "Demo-Modus",
            "messages": messages,
            "marquee_text": marquee_text,
            "separator": "  ·  ",
            "dismissible": False,
            "aria_label": ". ".join(["DEMO", "Demo-Modus"] + messages),
            "access_mode": "demo",
            "demo_mode": True,
            "public_viewer": False,
            "read_only": False,
            "source": "routes.ui.projects.fallback_notice",
            "metadata": {
                "project_public_id": _safe_str(access_context.get("project_public_id"), "", 160),
            },
        }

    except Exception:
        return {
            "ok": True,
            "enabled": False,
            "kind": "",
            "severity": "",
            "label": "",
            "title": "",
            "messages": [],
            "marquee_text": "",
            "source": "routes.ui.projects.fallback_notice_failed",
            "reason": "fallback_failed",
            "metadata": {},
        }


def _notice_stream_context(
    *,
    selected_project: Optional[Any],
    project_payload: Mapping[str, Any],
    workspace: Mapping[str, Any],
    current_user: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        access_context = _notice_access_context(
            selected_project=selected_project,
            project_payload=project_payload,
            workspace=workspace,
            current_user=current_user,
        )

        if callable(build_notice_stream):
            notice = build_notice_stream(
                current_user_context=current_user,
                access_context=access_context,
                project=selected_project if selected_project is not None else project_payload,
                force_refresh=False,
            )
            notice_payload = _safe_dict(notice)
            if notice_payload:
                return notice_payload

        return _fallback_notice_stream(
            current_user=current_user,
            access_context=access_context,
        )

    except Exception as exc:
        _log_warning("notice stream context failed: %s", exc.__class__.__name__)
        return _fallback_notice_stream(
            current_user=current_user,
            access_context={
                "access_mode": "demo" if _is_demo_context(current_user) else "anonymous",
                "project_public_id": _project_public_id(selected_project, "new"),
            },
        )



def _access_context_to_dict(access_context: Any) -> Dict[str, Any]:
    try:
        if access_context is None:
            return {}

        if hasattr(access_context, "to_dict") and callable(access_context.to_dict):
            return _safe_dict(access_context.to_dict())

        return _safe_dict(access_context)

    except Exception:
        return {}


def _access_context_allowed(access_context: Mapping[str, Any]) -> bool:
    return _safe_bool(access_context.get("allowed") or access_context.get("ok"), False)


def _access_context_public_viewer(access_context: Mapping[str, Any]) -> bool:
    return _safe_bool(
        access_context.get("public_viewer")
        or access_context.get("publicViewer")
        or access_context.get("is_public_viewer")
        or access_context.get("isPublicViewer"),
        False,
    )


def _access_context_demo_mode(access_context: Mapping[str, Any]) -> bool:
    return _safe_bool(access_context.get("demo_mode") or access_context.get("demoMode"), False)


def _access_context_read_only(access_context: Mapping[str, Any]) -> bool:
    return _safe_bool(access_context.get("read_only") or access_context.get("readOnly"), True)


def _access_context_status_code(access_context: Mapping[str, Any], default: int = 403) -> int:
    status = _safe_int(access_context.get("status_code") or access_context.get("statusCode"), default)
    if status <= 0:
        return default
    return status


def _access_context_code(access_context: Mapping[str, Any], default: str = "project_permission_denied") -> str:
    return _safe_str(access_context.get("code") or access_context.get("reason"), default, 160)


def _access_context_message(access_context: Mapping[str, Any], default: str = "Zugriff verweigert.") -> str:
    return _safe_str(access_context.get("message") or access_context.get("error"), default, 500)


def _fallback_project_access_context(project: Any, current_user: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        if project is None:
            return {
                "ok": True,
                "allowed": True,
                "access_mode": "demo" if _is_demo_context(current_user) else "authenticated" if _is_authenticated_context(current_user) else "anonymous",
                "code": "new_project_shell_allowed",
                "message": "Projekt-Shell erlaubt.",
                "status_code": 200,
                "workspace": "project",
                "action": "view",
                "read_only": False if _is_demo_context(current_user) or _is_persistent_context(current_user) else True,
                "public_viewer": False,
                "demo_mode": _is_demo_context(current_user),
                "authenticated": _is_authenticated_context(current_user),
                "persistent": _is_persistent_context(current_user),
                "permissions": {
                    "view": True,
                    "edit": bool(_is_demo_context(current_user) or _is_persistent_context(current_user)),
                    "manage": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                    "delete": False,
                    "transfer": False,
                    "embed": bool(_is_demo_context(current_user) or _is_persistent_context(current_user)),
                    "view_settings": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                    "manage_settings": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                    "view_team": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                    "manage_team": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                    "view_admin": bool(_is_persistent_context(current_user) and not _is_demo_context(current_user)),
                },
                "source": "routes.ui.projects.fallback_project_access_context",
            }

        if _is_auth_unavailable_context(current_user):
            return {
                "ok": False,
                "allowed": False,
                "access_mode": "auth_unavailable",
                "code": _context_code(current_user) or "auth_service_unavailable",
                "message": "vectoplan-auth ist nicht erreichbar.",
                "status_code": 503,
                "read_only": True,
                "public_viewer": False,
                "demo_mode": False,
                "permissions": {},
                "source": "routes.ui.projects.fallback_project_access_context",
            }

        if _is_user_blocked_context(current_user):
            return {
                "ok": False,
                "allowed": False,
                "access_mode": "blocked",
                "code": _context_code(current_user) or "auth_blocked",
                "message": "Dieser Zugang ist gesperrt.",
                "status_code": 403,
                "read_only": True,
                "public_viewer": False,
                "demo_mode": False,
                "permissions": {},
                "source": "routes.ui.projects.fallback_project_access_context",
            }

        if _project_is_demo(project) and _is_demo_context(current_user):
            return {
                "ok": True,
                "allowed": True,
                "access_mode": "demo",
                "code": "demo_project_allowed",
                "message": "Demo-Projektzugriff erlaubt.",
                "status_code": 200,
                "workspace": "project",
                "action": "view",
                "read_only": False,
                "public_viewer": False,
                "demo_mode": True,
                "authenticated": False,
                "persistent": False,
                "permissions": {
                    "view": True,
                    "edit": True,
                    "manage": False,
                    "delete": False,
                    "transfer": False,
                    "embed": True,
                    "view_settings": False,
                    "manage_settings": False,
                    "view_team": False,
                    "manage_team": False,
                    "view_admin": False,
                },
                "source": "routes.ui.projects.fallback_project_access_context",
            }

        try:
            user_id = _current_user_id_optional()
            result = require_project_permission(
                project,
                PERMISSION_VIEW,
                user_id,
                allow_public_view=True,
            )

            if result is False:
                return {
                    "ok": False,
                    "allowed": False,
                    "access_mode": "demo" if _is_demo_context(current_user) else "anonymous",
                    "code": "project_permission_denied",
                    "message": "permission denied",
                    "status_code": 403,
                    "read_only": True,
                    "public_viewer": False,
                    "demo_mode": _is_demo_context(current_user),
                    "permissions": {},
                    "source": "routes.ui.projects.fallback_project_access_context",
                }

            data = _safe_dict(result)
            if data and data.get("ok") is False:
                data.setdefault("allowed", False)
                data.setdefault("access_mode", "demo" if _is_demo_context(current_user) else "anonymous")
                data.setdefault("status_code", 403)
                data.setdefault("source", "routes.ui.projects.fallback_project_access_context")
                return data

            access = _serialize_permissions(project, user_id)
            permissions = _safe_dict(access.get("permissions"))
            if not permissions:
                permissions = {
                    "view": True,
                    "edit": _safe_bool(access.get("can_edit"), False),
                    "manage": _safe_bool(access.get("can_manage"), False),
                    "delete": False,
                    "transfer": False,
                    "embed": True,
                    "view_settings": _safe_bool(access.get("can_view_settings"), False),
                    "manage_settings": _safe_bool(access.get("can_manage_settings"), False),
                    "view_team": _safe_bool(access.get("can_view_team"), False),
                    "manage_team": _safe_bool(access.get("can_manage_team"), False),
                    "view_admin": _safe_bool(access.get("can_view_admin"), False),
                }

            return {
                "ok": True,
                "allowed": True,
                "access_mode": "authenticated" if _is_authenticated_context(current_user) else "demo" if _is_demo_context(current_user) else "anonymous",
                "code": "project_permission_allowed",
                "message": "Projektzugriff erlaubt.",
                "status_code": 200,
                "workspace": "project",
                "action": "view",
                "read_only": not _safe_bool(permissions.get("edit") or permissions.get("manage"), False),
                "public_viewer": False,
                "demo_mode": _is_demo_context(current_user),
                "authenticated": _is_authenticated_context(current_user),
                "persistent": _is_persistent_context(current_user),
                "permissions": permissions,
                "source": "routes.ui.projects.fallback_project_access_context",
            }

        except TypeError:
            result = require_project_permission(project, PERMISSION_VIEW, _current_user_id_optional())
            if result is False:
                return {
                    "ok": False,
                    "allowed": False,
                    "access_mode": "demo" if _is_demo_context(current_user) else "anonymous",
                    "code": "project_permission_denied",
                    "message": "permission denied",
                    "status_code": 403,
                    "read_only": True,
                    "public_viewer": False,
                    "demo_mode": _is_demo_context(current_user),
                    "permissions": {},
                    "source": "routes.ui.projects.fallback_project_access_context",
                }
            return {
                "ok": True,
                "allowed": True,
                "access_mode": "authenticated" if _is_authenticated_context(current_user) else "anonymous",
                "code": "project_permission_allowed",
                "message": "Projektzugriff erlaubt.",
                "status_code": 200,
                "read_only": True,
                "public_viewer": False,
                "demo_mode": _is_demo_context(current_user),
                "permissions": {"view": True, "embed": True},
                "source": "routes.ui.projects.fallback_project_access_context",
            }

    except Exception as exc:
        _log_warning("fallback project access context failed: %s", exc.__class__.__name__)
        return {
            "ok": False,
            "allowed": False,
            "access_mode": "error",
            "code": "project_access_context_failed",
            "message": "Projektzugriff konnte nicht geprüft werden.",
            "status_code": 500,
            "read_only": True,
            "public_viewer": False,
            "demo_mode": False,
            "permissions": {},
            "source": "routes.ui.projects.fallback_project_access_context",
            "error": str(exc),
        }


def _resolve_shell_access_context(project: Optional[Any], current_user: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        if callable(resolve_project_shell_access):
            resolved = resolve_project_shell_access(
                project,
                current_user_context=current_user,
                action="view",
                use_cache=True,
            )
            data = _access_context_to_dict(resolved)
            if data:
                return data

        return _fallback_project_access_context(project, current_user)

    except Exception as exc:
        _log_warning("resolve shell access context failed: %s", exc.__class__.__name__)
        return _fallback_project_access_context(project, current_user)


def _effective_current_user_for_access(
    current_user: Mapping[str, Any],
    access_context: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    user = _safe_dict(current_user)
    access = _safe_dict(access_context)

    try:
        if _access_context_public_viewer(access):
            user["demo_mode"] = False
            user["demoMode"] = False
            user["is_demo"] = False
            user["isDemo"] = False
            user["public_viewer"] = True
            user["publicViewer"] = True
            user["is_public_viewer"] = True
            user["isPublicViewer"] = True
            user["read_only"] = True
            user["readOnly"] = True
            user["persistent"] = False
            user["can_persist_projects"] = False
            user["canPersistProjects"] = False
            user["access_mode"] = "public"
            user["accessMode"] = "public"

            if not _safe_bool(user.get("authenticated") or user.get("is_authenticated") or user.get("isAuthenticated"), False):
                user["authenticated"] = False
                user["is_authenticated"] = False
                user["isAuthenticated"] = False
                user["id"] = None
                user["user_id"] = None
                user["userId"] = None

        elif access:
            user["access_mode"] = _safe_str(access.get("access_mode") or access.get("accessMode"), "", 80)
            user["accessMode"] = user["access_mode"]
            user["read_only"] = _access_context_read_only(access)
            user["readOnly"] = user["read_only"]

        return user

    except Exception:
        return user


def _apply_access_context_to_project_payload(
    project_payload: Mapping[str, Any],
    access_context: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = dict(project_payload or {})
    access = _safe_dict(access_context)

    try:
        if callable(apply_project_access_context):
            applied = apply_project_access_context(payload, access)
            payload = _safe_dict(applied) or payload
        else:
            existing_access = _safe_dict(payload.get("access"))
            permissions = _safe_dict(existing_access.get("permissions"))
            permissions.update(_safe_dict(access.get("permissions")))

            existing_access.update(
                {
                    "role": access.get("role") or existing_access.get("role") or "viewer",
                    "source": access.get("source") or existing_access.get("source") or "project_access_context",
                    "access_mode": access.get("access_mode") or access.get("accessMode") or "",
                    "accessMode": access.get("access_mode") or access.get("accessMode") or "",
                    "read_only": _access_context_read_only(access),
                    "readOnly": _access_context_read_only(access),
                    "public_viewer": _access_context_public_viewer(access),
                    "publicViewer": _access_context_public_viewer(access),
                    "is_public_viewer": _access_context_public_viewer(access),
                    "isPublicViewer": _access_context_public_viewer(access),
                    "demo_mode": _access_context_demo_mode(access),
                    "demoMode": _access_context_demo_mode(access),
                    "permissions": permissions,
                    "can_view": _safe_bool(access.get("can_view") or access.get("canView") or permissions.get("view"), False),
                    "can_edit": _safe_bool(access.get("can_edit") or access.get("canEdit") or permissions.get("edit"), False),
                    "can_manage": _safe_bool(access.get("can_manage") or access.get("canManage") or permissions.get("manage"), False),
                }
            )
            payload["access"] = existing_access

        if _access_context_public_viewer(access):
            payload["demo_mode"] = False
            payload["demoMode"] = False
            payload["is_demo"] = False
            payload["isDemo"] = False
            payload["public_viewer"] = True
            payload["publicViewer"] = True
            payload["read_only"] = True
            payload["readOnly"] = True

        if access.get("publication"):
            payload["publication"] = _safe_dict(access.get("publication"))

        payload["access_context"] = access
        payload["accessContext"] = access

        return payload

    except Exception as exc:
        _log_warning("apply access context to project payload failed: %s", exc.__class__.__name__)
        return payload


def _permission_denied_from_access_context(access_context: Mapping[str, Any]) -> PermissionDenied:
    return _make_permission_denied(
        _access_context_message(access_context),
        code=_access_context_code(access_context),
        status_code=_access_context_status_code(access_context),
    )

def _make_permission_denied(
    message: str,
    *,
    code: str,
    status_code: int,
) -> PermissionDenied:
    try:
        return PermissionDenied(message, code=code, status_code=status_code)
    except TypeError:
        exc = PermissionDenied(message)  # type: ignore
        try:
            setattr(exc, "code", code)
            setattr(exc, "status_code", status_code)
        except Exception:
            pass
        return exc


def _assert_project_view_allowed(project: Any, current_user: Mapping[str, Any]) -> None:
    access_context = _resolve_shell_access_context(project, current_user)

    if _access_context_allowed(access_context):
        return

    raise _permission_denied_from_access_context(access_context)


# ─────────────────────────────────────────────────────────────
# Render helpers
# ─────────────────────────────────────────────────────────────

def _render_project_shell(
    *,
    selected_project: Optional[Any],
    is_new: bool = False,
    status_code: int = 200,
) -> Response:
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_access_blocked_context(current_user):
            return _blocked_response(current_user)

        access_context: Dict[str, Any] = {}

        if selected_project is not None:
            access_context = _resolve_shell_access_context(selected_project, current_user)

            if not _access_context_allowed(access_context):
                raise _permission_denied_from_access_context(access_context)

        effective_current_user = _effective_current_user_for_access(current_user, access_context)

        conversation = _ensure_project_conversation(selected_project, current_user=effective_current_user)

        project_payload = _project_payload_for_template(
            selected_project,
            current_user=effective_current_user,
            is_new=is_new,
            access_context=access_context,
        )

        workspace = _workspace_context_for_project(
            project=selected_project,
            project_payload=project_payload,
            conversation=conversation,
            current_user=effective_current_user,
            is_new=is_new,
        )

        workspace_paths = dict(workspace.get("paths") or {})

        project_sidebar = _project_sidebar_context(
            selected_project=selected_project,
            conversation=conversation,
            current_user=effective_current_user,
            access_context=access_context,
        )

        notice_stream = _notice_stream_context(
            selected_project=selected_project,
            project_payload=project_payload,
            workspace=workspace,
            current_user=effective_current_user,
        )

        resp = make_response(
            render_template(
                "chat_viewer.html",
                chat_id=workspace.get("chat_id") or "demo",
                viewer_url=workspace.get("project_url") or "",
                project_url=workspace.get("project_url") or "",
                editor_url=workspace.get("editor_url") or "",
                initial_editor_url=workspace.get("editor_url") or "",
                map_url=workspace.get("map_url") or "",
                initial_map_url=workspace.get("map_url") or "",
                cad2d_url=workspace.get("cad2d_url") or "",
                lv_url=workspace.get("lv_url") or "",
                versions_url=workspace.get("versions_url") or "",
                admin_url=workspace.get("admin_url") or "",
                default_mode="project",
                workspace_mode="project",
                workspace=workspace,
                workspace_paths=workspace_paths,
                project=project_payload,
                current_project=project_payload,
                project_sidebar=project_sidebar,
                notice_stream=notice_stream,
                current_user=effective_current_user,
                auth=effective_current_user,
                auth_context=effective_current_user,
                demo_mode=_is_demo_context(effective_current_user),
            ),
            status_code,
        )

        return _finalize_html_response(resp, no_store=False, workspace_shell=True)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        _log_exception("render project shell failed", exc)
        resp = jsonify(
            {
                "ok": False,
                "error": f"project shell render failed: {exc}",
                "code": "project_shell_render_failed",
                "status_code": 500,
            }
        )
        resp.status_code = 500
        return _finalize_json_response(resp, no_store=True)



def _render_demo_shell_or_new(current_user: Mapping[str, Any]) -> Response:
    if _is_auth_unavailable_context(current_user):
        return _blocked_response(current_user)

    if _is_demo_context(current_user):
        demo_project = _load_current_demo_project()
        if demo_project is not None:
            return _render_project_shell(selected_project=demo_project, is_new=False)

    return _render_project_shell(selected_project=None, is_new=True)


# ─────────────────────────────────────────────────────────────
# Root / project shell routes
# ─────────────────────────────────────────────────────────────

@bp.get("/")
def project_root() -> Response:
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_access_blocked_context(current_user):
            return _blocked_response(current_user)

        project_id = _current_project_identifier_from_request()

        if project_id:
            project = _load_selected_project(project_id)

            if project is not None:
                return _render_project_shell(selected_project=project, is_new=False)

        if _is_demo_context(current_user):
            return _render_demo_shell_or_new(current_user)

        return _render_project_shell(selected_project=None, is_new=True)

    except Exception as exc:
        return _exception_response("project_root failed", exc, code="project_root_failed")


@bp.get("/project=<project_id>")
def project_by_equals(project_id: str) -> Response:
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_access_blocked_context(current_user):
            return _blocked_response(current_user)

        if _is_new_project_identifier(project_id):
            if _is_demo_context(current_user):
                return _render_demo_shell_or_new(current_user)
            return _render_project_shell(selected_project=None, is_new=True)

        project = _load_selected_project(project_id)

        if project is None:
            if _is_demo_context(current_user):
                return _render_demo_shell_or_new(current_user)

            return _json_error(
                "project not found",
                404,
                code="project_not_found",
                extra={"project_id": project_id},
            )

        return _render_project_shell(selected_project=project, is_new=False)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_by_equals failed", exc, code="project_route_failed")


@bp.get("/project/<project_id>")
def project_by_path(project_id: str) -> Response:
    try:
        return redirect(f"/project={_safe_quote(project_id)}", code=302)
    except Exception:
        return redirect("/", code=302)


@bp.get("/projects")
def projects_list_page() -> Response:
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_access_blocked_context(current_user):
            return _blocked_response(current_user)

        if _is_demo_context(current_user):
            return _render_demo_shell_or_new(current_user)

        return _render_project_shell(selected_project=None, is_new=True)
    except Exception as exc:
        return _exception_response("projects_list_page failed", exc, code="projects_page_failed")


# ─────────────────────────────────────────────────────────────
# Lightweight JSON helpers for UI shell
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/projects/sidebar.json")
def ui_projects_sidebar_json() -> Response:
    try:
        current_user = _current_user_payload(ensure=False)
        user_id = _current_user_id_optional()

        if _is_auth_unavailable_context(current_user):
            return _json_response(
                {
                    "ok": False,
                    "user_id": None,
                    "auth": current_user,
                    "auth_unavailable": True,
                    "blocked": True,
                    "blocked_kind": "auth_unavailable",
                    "items": [],
                    "sidebar_items": [],
                    "total": 0,
                    "code": _context_code(current_user) or "auth_service_unavailable",
                    "auth_dependency": _auth_dependency_payload(),
                },
                503,
            )

        if _is_access_blocked_context(current_user):
            return _json_response(
                {
                    "ok": False,
                    "user_id": None,
                    "auth": current_user,
                    "blocked": True,
                    "items": [],
                    "sidebar_items": [],
                    "total": 0,
                    "code": _context_code(current_user) or "auth_blocked",
                },
                _context_status_code(current_user),
            )

        if _is_demo_context(current_user):
            demo_project = _load_current_demo_project()
            items = []

            if demo_project is not None:
                if serialize_project_sidebar_item is not None:
                    try:
                        items = [serialize_project_sidebar_item(demo_project, user_id=None)]
                    except Exception:
                        items = []

                if not items:
                    public_id = _project_public_id(demo_project, "demo")
                    items = [
                        {
                            "id": public_id,
                            "projectId": public_id,
                            "public_id": public_id,
                            "title": getattr(demo_project, "name", None) or "Demo-Projekt",
                            "subtitle": "Temporäres Demo-Projekt",
                            "href": f"/project={_safe_quote(public_id)}",
                            "isDemo": True,
                            "is_demo": True,
                            "source": "demo",
                        }
                    ]

            return _json_response(
                {
                    "ok": True,
                    "user_id": None,
                    "auth": current_user,
                    "demo_mode": True,
                    "items": items,
                    "sidebar_items": items,
                    "total": len(items),
                },
                200,
            )

        if not user_id or not _is_persistent_context(current_user):
            return _json_response(
                {
                    "ok": True,
                    "user_id": user_id,
                    "auth": current_user,
                    "demo_mode": False,
                    "items": [],
                    "sidebar_items": [],
                    "total": 0,
                },
                200,
            )

        items = []

        if list_project_sidebar_items is not None:
            items = list_project_sidebar_items(
                user_id=user_id,
                include_public=False,
                limit=200,
            ) or []

        return _json_response(
            {
                "ok": True,
                "user_id": user_id,
                "auth": current_user,
                "demo_mode": False,
                "items": items,
                "sidebar_items": items,
                "total": len(items),
            },
            200,
        )

    except Exception as exc:
        return _exception_response("ui_projects_sidebar_json failed", exc, code="ui_sidebar_failed")


@bp.get("/ui/projects/status.json")
def ui_projects_status_json() -> Response:
    payload: Dict[str, Any] = {
        "ok": True,
        "service": "ui_projects",
        "purpose": "project_shell_and_compatibility_routes",
        "phase": "vectoplan-auth-public-access-context-demo-notice",
        "default_user_removed": True,
        "auth_unavailable_returns_503": True,
        "routes": {
            "root": "/",
            "project_shell": "/project=<project_public_id>",
            "new_project_shell": "/project=new",
            "sidebar": "/ui/projects/sidebar.json",
            "viewer_workspace": "/ui/project/<project_id>/project",
            "viewer_context": "/ui/project/<project_id>/context.json",
        },
        "current_user": _current_user_payload(ensure=False),
        "auth_dependency": _auth_dependency_payload(),
        "rules": {
            "auth_unavailable_demo_fallback": False,
            "auth_unavailable_status": 503,
            "user_blocked_status": 403,
            "default_user": False,
            "persistent_conversation_for_demo": False,
        },
    }

    try:
        if get_current_user_status is not None:
            payload["current_user_status"] = get_current_user_status()
    except Exception:
        pass

    try:
        if callable(get_notice_stream_status):
            payload["notice_stream"] = get_notice_stream_status()
    except Exception:
        payload["notice_stream"] = {
            "ok": False,
            "enabled": False,
            "code": "notice_stream_status_failed",
        }

    try:
        if callable(get_project_access_context_status):
            payload["project_access_context"] = get_project_access_context_status()
    except Exception:
        payload["project_access_context"] = {
            "ok": False,
            "code": "project_access_context_status_failed",
        }

    return _json_response(payload, 200)


# ─────────────────────────────────────────────────────────────
# Redirect helpers
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/project")
def ui_project_root_redirect() -> Response:
    try:
        project_id = _current_project_identifier_from_request()

        if project_id:
            return redirect(f"/project={_safe_quote(project_id)}", code=302)

        return redirect("/", code=302)

    except Exception:
        return redirect("/", code=302)


@bp.get("/ui/projects")
def ui_projects_root_redirect() -> Response:
    try:
        return redirect("/", code=302)
    except Exception:
        return redirect("/", code=302)


__all__ = [
    "bp",
    "ui_projects_bp",
    "projects_ui_bp",
    "project_root",
    "project_by_equals",
    "project_by_path",
    "projects_list_page",
    "ui_projects_sidebar_json",
    "ui_projects_status_json",
    "ui_project_root_redirect",
    "ui_projects_root_redirect",
]