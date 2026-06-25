# services/vectoplan-app/routes/ui/projects.py
from __future__ import annotations

"""
VECTOPLAN project UI shell routes.

Zweck:
- Root-URL / rendert die App-Shell.
- /project=<project_public_id> rendert dieselbe App-Shell mit ausgewähltem Projekt.
- /project=new rendert die Shell mit Projekterstellung als initialem Workspace.
- Die eigentliche Projekt-Workspace-Seite wird über routes.viewer bereitgestellt:
    /ui/project/<project_id>/project
    /ui/project/<project_id>/<workspace>
    /ui/project/<project_id>/context.json
- Diese Datei bleibt Shell-/Kompatibilitätsschicht.

Wichtig:
- vectoplan-app besitzt hier nur Projekt-Metadaten und UI-Kontext.
- Chunk-, Editor-, 2D-, Map- und LV-Fachdaten bleiben in ihren Microservices.
- Diese Datei erzeugt keine echten Benutzeraccounts.
- Im Demo-Kontext werden keine persistenten Conversations erzeugt.
"""

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

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
        ensure_default_user,
        get_current_user_context,
        get_current_user_id_optional,
        get_current_user_status,
    )
except Exception:  # pragma: no cover
    ensure_default_user = None  # type: ignore
    get_current_user_status = None  # type: ignore

    def get_current_user_id_optional() -> Optional[int]:  # type: ignore
        return 1

    def get_current_user_context(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore
        return {
            "id": 1,
            "user_id": 1,
            "authenticated": True,
            "demo_mode": False,
            "persistent": True,
            "source": "fallback",
        }


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
        ) -> None:
            super().__init__(message)
            self.message = message
            self.code = code
            self.status_code = status_code

        def to_dict(self) -> Dict[str, Any]:
            return {
                "ok": False,
                "error": self.message,
                "code": self.code,
                "status_code": self.status_code,
            }

    def require_project_permission(*args: Any, **kwargs: Any) -> bool:  # type: ignore
        return True


try:
    from services.project_service import (
        build_project_paths,
        get_or_create_project_conversation,
        list_project_sidebar_items,
        project_public_url,
        project_workspace_path,
        resolve_project,
        serialize_project,
        serialize_project_sidebar_item,
    )
except Exception:  # pragma: no cover
    build_project_paths = None  # type: ignore
    get_or_create_project_conversation = None  # type: ignore
    list_project_sidebar_items = None  # type: ignore
    project_public_url = None  # type: ignore
    project_workspace_path = None  # type: ignore
    resolve_project = None  # type: ignore
    serialize_project = None  # type: ignore
    serialize_project_sidebar_item = None  # type: ignore


try:
    from services.project_publication_service import get_project_publication
except Exception:  # pragma: no cover
    get_project_publication = None  # type: ignore


bp = Blueprint("ui_projects", __name__)

ui_projects_bp = bp
projects_ui_bp = bp


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
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        text = _safe_str(value, "", 40).lower()

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
        user_id = _current_user_id_optional()
        return {
            "id": user_id,
            "user_id": user_id,
            "authenticated": bool(user_id),
            "demo_mode": False,
            "persistent": bool(user_id),
            "source": "ui_projects_fallback",
        }


def _current_user_id_optional() -> Optional[int]:
    try:
        value = get_current_user_id_optional()
        parsed = _safe_int(value, 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _is_demo_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    return _safe_bool(data.get("demo_mode") or data.get("demoMode") or data.get("is_demo"), False)


def _is_persistent_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)

    return _safe_bool(
        data.get("persistent"),
        bool(data.get("user_id") or data.get("id")) and not _is_demo_context(data),
    )


@bp.before_request
def _ui_projects_before_request() -> None:
    """
    Keep dev placeholder behavior, but do not create local users in demo mode.
    """
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_demo_context(current_user):
            return

        if not _is_persistent_context(current_user):
            return

        if ensure_default_user is not None:
            ensure_default_user()

    except Exception as exc:
        _log_warning("ensure_default_user before project UI failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Response / security helpers
# ─────────────────────────────────────────────────────────────

def _workspace_csp_header_value() -> str:
    try:
        editor_public = str(
            current_app.config.get("VECTOPLAN_EDITOR_PUBLIC_URL", "http://localhost:5100")
            or "http://localhost:5100"
        ).rstrip("/")

        openlayer_public = str(
            current_app.config.get("OPENLAYER_PUBLIC_URL", "http://localhost:5190")
            or "http://localhost:5190"
        ).rstrip("/")

        chunk_public = str(
            current_app.config.get("VECTOPLAN_CHUNK_PUBLIC_URL", "http://localhost:5102")
            or "http://localhost:5102"
        ).rstrip("/")

        library_public = str(
            current_app.config.get("VECTOPLAN_LIBRARY_PUBLIC_URL", "http://localhost:5101")
            or "http://localhost:5101"
        ).rstrip("/")

        frame_src_items = [
            "'self'",
            editor_public,
            "http://127.0.0.1:5100",
            openlayer_public,
            "http://127.0.0.1:5190",
        ]

        connect_src_items = [
            "'self'",
            editor_public,
            "http://127.0.0.1:5100",
            chunk_public,
            "http://127.0.0.1:5102",
            library_public,
            "http://127.0.0.1:5101",
            openlayer_public,
            "http://127.0.0.1:5190",
        ]

        frame_src: List[str] = []
        connect_src: List[str] = []

        for item in frame_src_items:
            text = _safe_str(item, "", 240)
            if text and text not in frame_src:
                frame_src.append(text)

        for item in connect_src_items:
            text = _safe_str(item, "", 240)
            if text and text not in connect_src:
                connect_src.append(text)

        parents = [
            "'self'",
            "http://localhost:5103",
            "http://127.0.0.1:5103",
        ]

        return (
            f"frame-src {' '.join(frame_src)}; "
            f"child-src {' '.join(frame_src)}; "
            f"connect-src {' '.join(connect_src)}; "
            f"frame-ancestors {' '.join(parents)}"
        )

    except Exception:
        return (
            "frame-src 'self' http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "child-src 'self' http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "connect-src 'self' http://localhost:5100 http://127.0.0.1:5100 "
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
        resp = jsonify(payload)
        resp.status_code = int(status)
        return _finalize_json_response(resp, no_store=True)

    except Exception:
        fallback = jsonify(
            {
                "ok": False,
                "error": "failed to serialize response",
                "code": "response_serialization_failed",
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
        return _json_response(exc.to_dict(), exc.status_code)
    except Exception:
        return _json_error("permission denied", 403, code="permission_denied")


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


def _load_selected_project(project_identifier: Optional[str]) -> Optional[Any]:
    try:
        if _is_new_project_identifier(project_identifier):
            return None

        if resolve_project is None:
            return None

        return resolve_project(project_identifier)

    except Exception:
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
    if Conversation is None or db is None:
        return {"id": "local-shell", "title": "Projekt-Shell", "project_id": "__project_shell__"}

    try:
        conv = (
            Conversation.query
            .filter_by(project_id="__project_shell__")
            .order_by(Conversation.created_at.desc())
            .first()
        )

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

    if not _is_persistent_context(current_user):
        return {
            "id": "demo",
            "title": "Demo-Projekt-Shell",
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

def _new_publication_payload() -> Dict[str, Any]:
    return {
        "visibility": "private",
        "publication_enabled": False,
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
        "require_auth": True,
        "requireAuth": True,
        "require_project_permission": True,
        "requireProjectPermission": True,
    }


def _new_project_payload(current_user: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    user = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    demo_mode = _is_demo_context(user)
    persistent = _is_persistent_context(user)
    can_edit = bool(persistent and not demo_mode)
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
        "publication": _new_publication_payload(),
        "members": [],
        "invitations": [],
        "url": "/project=new",
        "href": "/project=new",
        "paths": {
            "projectPagePath": "/ui/project/new/project",
            "projectUrl": "/ui/project/new/project",
            "projectPublicUrl": "/project=new",
            "contextPath": "/ui/project/new/context.json",
        },
        "access": {
            "role": "owner" if can_manage else "viewer",
            "source": "new_project",
            "permissions": {
                "view": True,
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
            "can_view": True,
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
            "visibility": getattr(project, "visibility", "private"),
            "setup_status": getattr(project, "setup_status", "draft"),
            "setupStatus": getattr(project, "setup_status", "draft"),
            "is_configured": bool(getattr(project, "is_configured", False)),
            "isConfigured": bool(getattr(project, "is_configured", False)),
            "access": _serialize_permissions(project, user_id),
        }

    include_private = _can_manage_project(project, user_id)

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
        payload.setdefault("publication", _new_publication_payload())
        return payload


def _project_payload_for_template(
    project: Optional[Any],
    *,
    current_user: Mapping[str, Any],
    is_new: bool = False,
) -> Dict[str, Any]:
    user_id = _current_user_id_optional()

    if project is None:
        return _new_project_payload(current_user)

    try:
        payload = _serialize_project_safe(project, user_id=user_id)

        if not payload.get("access"):
            payload["access"] = _serialize_permissions(project, user_id)

        payload = _attach_publication(project, payload, user_id)

        public_id = _project_public_id(project, "new")
        paths = _project_paths(public_id)

        existing_paths = _safe_dict(payload.get("paths"))
        existing_paths.update({key: value for key, value in paths.items() if value})
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
            "publication": _new_publication_payload(),
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

def _project_paths(public_id: str) -> Dict[str, str]:
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
            "stateGetPath": "",
            "statePutPath": "",
        }

    return {
        "projectPagePath": f"/ui/project/{public_id_q}/project",
        "projectUrl": f"/ui/project/{public_id_q}/project",
        "projectPublicUrl": f"/project={public_id_q}",
        "contextPath": f"/ui/project/{public_id_q}/context.json",
        "editorPagePath": f"/ui/project/{public_id_q}/editor3d",
        "initialEditorUrl": f"/ui/project/{public_id_q}/editor3d",
        "mapPagePath": f"/ui/project/{public_id_q}/map",
        "cad2dPagePath": f"/ui/project/{public_id_q}/cad2d",
        "lvPagePath": f"/ui/project/{public_id_q}/lv",
        "versionsPagePath": f"/ui/project/{public_id_q}/versions",
        "adminPagePath": f"/ui/project/{public_id_q}/admin",
        "publicationPath": f"/v1/projects/{public_id_q}/publication",
        "membersPath": f"/v1/projects/{public_id_q}/members",
        "invitationsPath": f"/v1/projects/{public_id_q}/invitations",
        "apiPath": f"/v1/projects/{public_id_q}",
        "stateGetPath": "",
        "statePutPath": "",
    }


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

        public_id = _safe_str(
            project_payload.get("public_id")
            or project_payload.get("publicId")
            or _project_public_id(project, "new"),
            "new",
            160,
        )

        paths = _project_paths(public_id)

        if _is_persistent_context(current_user) and chat_id and chat_id != "demo":
            paths["stateGetPath"] = f"/v1/chats/{chat_id_q}/viewer/selection"
            paths["statePutPath"] = f"/v1/chats/{chat_id_q}/viewer/selection"

        existing_paths = _safe_dict(project_payload.get("paths"))
        existing_paths.update({key: value for key, value in paths.items() if value})

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
            "editor_url": "",
            "viewer_url": "/ui/project/new/project",
            "map_url": "",
            "paths": _project_paths("new"),
            "demo_mode": _is_demo_context(current_user),
            "persistent": _is_persistent_context(current_user),
        }


def _project_sidebar_context(
    *,
    selected_project: Optional[Any],
    conversation: Any,
    current_user: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        user_id = _current_user_id_optional()
        chat_id = _conversation_id(conversation)
        selected_public_id = _project_public_id(selected_project, "new")

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
                "demo_mode": _is_demo_context(current_user),
            }

        items = []

        if list_project_sidebar_items is not None:
            items = list_project_sidebar_items(
                user_id=user_id,
                include_public=True,
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
            "demo_mode": _is_demo_context(current_user),
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


def _assert_project_view_allowed(project: Any, current_user: Mapping[str, Any]) -> None:
    try:
        user_id = _current_user_id_optional()

        require_project_permission(
            project,
            PERMISSION_VIEW,
            user_id,
            allow_public_view=True,
        )
    except TypeError:
        require_project_permission(project, PERMISSION_VIEW, _current_user_id_optional())


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

        if selected_project is not None:
            _assert_project_view_allowed(selected_project, current_user)

        conversation = _ensure_project_conversation(selected_project, current_user=current_user)

        project_payload = _project_payload_for_template(
            selected_project,
            current_user=current_user,
            is_new=is_new,
        )

        workspace = _workspace_context_for_project(
            project=selected_project,
            project_payload=project_payload,
            conversation=conversation,
            current_user=current_user,
            is_new=is_new,
        )

        workspace_paths = dict(workspace.get("paths") or {})

        project_sidebar = _project_sidebar_context(
            selected_project=selected_project,
            conversation=conversation,
            current_user=current_user,
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
                current_user=current_user,
                auth=current_user,
                auth_context=current_user,
                demo_mode=_is_demo_context(current_user),
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
            }
        )
        resp.status_code = 500
        return _finalize_json_response(resp, no_store=True)


# ─────────────────────────────────────────────────────────────
# Root / project shell routes
# ─────────────────────────────────────────────────────────────

@bp.get("/")
def project_root() -> Response:
    try:
        project_id = _current_project_identifier_from_request()

        if project_id:
            project = _load_selected_project(project_id)

            if project is not None:
                return _render_project_shell(selected_project=project, is_new=False)

        return _render_project_shell(selected_project=None, is_new=True)

    except Exception as exc:
        return _exception_response("project_root failed", exc, code="project_root_failed")


@bp.get("/project=<project_id>")
def project_by_equals(project_id: str) -> Response:
    try:
        if _is_new_project_identifier(project_id):
            return _render_project_shell(selected_project=None, is_new=True)

        project = _load_selected_project(project_id)

        if project is None:
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

        if not user_id or not _is_persistent_context(current_user):
            return _json_response(
                {
                    "ok": True,
                    "user_id": user_id,
                    "demo_mode": _is_demo_context(current_user),
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
                include_public=True,
                limit=200,
            ) or []

        return _json_response(
            {
                "ok": True,
                "user_id": user_id,
                "demo_mode": _is_demo_context(current_user),
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
        "routes": {
            "root": "/",
            "project_shell": "/project=<project_public_id>",
            "new_project_shell": "/project=new",
            "sidebar": "/ui/projects/sidebar.json",
            "viewer_workspace": "/ui/project/<project_id>/project",
            "viewer_context": "/ui/project/<project_id>/context.json",
        },
        "current_user": _current_user_payload(ensure=False),
    }

    try:
        if get_current_user_status is not None:
            payload["current_user_status"] = get_current_user_status()
    except Exception:
        pass

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