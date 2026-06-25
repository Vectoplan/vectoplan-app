# services/vectoplan-app/routes/viewer.py
from __future__ import annotations

"""
VECTOPLAN viewer/project workspace routes.

Zweck:
- Rendert den Projekt-Workspace unter /ui/project/...
- Liefert context.json für Projekt-/Workspace-Shells.
- Unterstützt neue Projektseite:
    templates/viewer/project.html
- Unterstützt Demo-/Auth-Kontext.
- Schützt Admin-/Settings-Workspaces.
- Prüft Public-/Unlisted-Workspace-Zugriff über project_publication_service.
- Leitet externe Workspaces wie 3D und Map nach erfolgreicher Prüfung an
  ihre eigentlichen Browser-PUBLIC_URL-Ziele weiter.
- Hält HTML-Routen dünn; Fachlogik bleibt in services/.

Wichtig:
- Diese Route speichert keine Projekt-/Chunk-/3D-/2D-/LV-Daten.
- Diese Route erzeugt keine Benutzeraccounts.
- Login/Registrierung/Abo liegen später im Auth-/Registrierungsdienst.
- Browser-iframe/redirect nutzt PUBLIC_URL, niemals INTERNAL_URL.
"""

from html import escape
from typing import Any, Dict, Mapping, Optional, Tuple

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request
from jinja2 import TemplateNotFound
from werkzeug.wrappers import Response

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
            "user_id": 1,
            "id": 1,
            "authenticated": True,
            "demo_mode": False,
            "persistent": True,
            "source": "fallback",
        }


try:
    from services.project_service import (
        get_project_result,
        get_project_service_status,
        resolve_project,
        serialize_project,
        serialize_project_sidebar_item,
    )
except Exception:  # pragma: no cover
    get_project_result = None  # type: ignore
    get_project_service_status = None  # type: ignore
    resolve_project = None  # type: ignore
    serialize_project = None  # type: ignore
    serialize_project_sidebar_item = None  # type: ignore


try:
    from services.project_permissions import (
        PERMISSION_MANAGE,
        PERMISSION_VIEW,
        PermissionDenied,
        can_manage_project,
        get_project_permission_result,
        serialize_project_permissions,
    )
except Exception:  # pragma: no cover
    PERMISSION_VIEW = "view"  # type: ignore
    PERMISSION_MANAGE = "manage"  # type: ignore
    can_manage_project = None  # type: ignore
    get_project_permission_result = None  # type: ignore
    serialize_project_permissions = None  # type: ignore

    class PermissionDenied(RuntimeError):  # type: ignore
        def __init__(
            self,
            message: str = "permission denied",
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
                "error": self.message,
                "code": self.code,
                "status_code": self.status_code,
                "permission": self.permission,
            }


try:
    from services.project_publication_service import (
        can_access_project_workspace,
        get_project_publication,
        normalize_workspace_key,
    )
except Exception:  # pragma: no cover
    can_access_project_workspace = None  # type: ignore
    get_project_publication = None  # type: ignore

    def normalize_workspace_key(value: Any, default: str = "project") -> str:  # type: ignore
        text = str(value or default).strip().lower().replace("-", "_")
        aliases = {
            "": "project",
            "project": "project",
            "projekt": "project",
            "info": "project",
            "map": "map",
            "karte": "map",
            "openlayer": "map",
            "3d": "editor3d",
            "editor": "editor3d",
            "editor3d": "editor3d",
            "editor_3d": "editor3d",
            "viewer": "editor3d",
            "viewer3d": "editor3d",
            "2d": "cad2d",
            "cad": "cad2d",
            "cad2d": "cad2d",
            "lv": "lv",
            "versions": "versions",
            "versionen": "versions",
            "admin": "admin",
            "settings": "admin",
            "team": "admin",
        }
        return aliases.get(text, default)


try:
    from services.workspace_embed_service import (
        build_editor3d_embed_result,
        build_map_embed_result,
        build_workspace_embed_result,
        get_workspace_embed_status,
        is_external_workspace,
    )
except Exception:  # pragma: no cover
    build_editor3d_embed_result = None  # type: ignore
    build_map_embed_result = None  # type: ignore
    build_workspace_embed_result = None  # type: ignore
    get_workspace_embed_status = None  # type: ignore

    def is_external_workspace(value: Any) -> bool:  # type: ignore
        return str(value or "").strip().lower().replace("-", "_") in {"3d", "editor", "editor3d", "editor_3d", "map", "karte", "openlayer"}


bp = Blueprint("viewer", __name__)

viewer_bp = bp
project_viewer_bp = bp


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

WORKSPACE_PROJECT = "project"
WORKSPACE_MAP = "map"
WORKSPACE_EDITOR3D = "editor3d"
WORKSPACE_CAD2D = "cad2d"
WORKSPACE_LV = "lv"
WORKSPACE_VERSIONS = "versions"
WORKSPACE_ADMIN = "admin"

ALLOWED_WORKSPACES = {
    WORKSPACE_PROJECT,
    WORKSPACE_MAP,
    WORKSPACE_EDITOR3D,
    WORKSPACE_CAD2D,
    WORKSPACE_LV,
    WORKSPACE_VERSIONS,
    WORKSPACE_ADMIN,
}

PUBLIC_WORKSPACES = {
    WORKSPACE_PROJECT,
    WORKSPACE_MAP,
    WORKSPACE_EDITOR3D,
    WORKSPACE_CAD2D,
    WORKSPACE_LV,
    WORKSPACE_VERSIONS,
}

EXTERNAL_REDIRECT_WORKSPACES = {
    WORKSPACE_EDITOR3D,
    WORKSPACE_MAP,
}

ADMIN_WORKSPACES = {
    WORKSPACE_ADMIN,
}

WORKSPACE_TEMPLATE_MAP = {
    WORKSPACE_PROJECT: "viewer/project.html",
}

WORKSPACE_LABELS = {
    WORKSPACE_PROJECT: "Projekt",
    WORKSPACE_MAP: "Map",
    WORKSPACE_EDITOR3D: "3D",
    WORKSPACE_CAD2D: "2D",
    WORKSPACE_LV: "LV",
    WORKSPACE_VERSIONS: "Versionen",
    WORKSPACE_ADMIN: "Admin",
}


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


def _safe_list(value: Any) -> list[Any]:
    try:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        return []
    except Exception:
        return []


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


def _apply_no_cache_headers(response: Response) -> Response:
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return response


def _json_response(payload: Mapping[str, Any], status: int = 200) -> Response:
    response = jsonify(dict(payload))
    response.status_code = int(status)
    return _apply_no_cache_headers(response)


def _html_response(html: str, status: int = 200) -> Response:
    response = make_response(html, int(status))
    response.headers.setdefault("Content-Type", "text/html; charset=utf-8")
    return _apply_no_cache_headers(response)


def _redirect_response(url: str, status: int = 302, *, workspace: str = "") -> Response:
    response = redirect(url, code=int(status))

    try:
        if workspace:
            response.headers["X-VECTOPLAN-Workspace"] = workspace
        response.headers["X-VECTOPLAN-Workspace-Target"] = "external-public-url"
    except Exception:
        pass

    return _apply_no_cache_headers(response)


def _json_error(
    message: str,
    status: int = 500,
    *,
    code: str = "error",
    extra: Optional[Mapping[str, Any]] = None,
) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "code": code,
        "error": message,
        "message": message,
        "status_code": status,
    }

    if isinstance(extra, Mapping):
        payload.update(dict(extra))

    return _json_response(payload, status=status)


def _wants_json() -> bool:
    try:
        if request.path.endswith(".json"):
            return True

        if _safe_str(request.args.get("format"), "", 20).lower() == "json":
            return True

        accept = _safe_str(request.headers.get("Accept"), "", 500).lower()
        return "application/json" in accept and "text/html" not in accept

    except Exception:
        return False


def _error_response(
    message: str,
    status: int,
    *,
    code: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> Response:
    if _wants_json():
        return _json_error(message, status, code=code, extra=extra)

    title = {
        401: "Login erforderlich",
        403: "Zugriff nicht erlaubt",
        404: "Nicht gefunden",
        409: "Projekt nicht bereit",
        502: "Workspace nicht erreichbar",
        503: "Workspace deaktiviert",
    }.get(status, "Fehler")

    extra_dict = _safe_dict(extra)
    project = _safe_dict(extra_dict.get("project", {}))
    project_name = _safe_str(project.get("name"), "Projekt", 200)

    html = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{escape(title)} · VECTOPLAN</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f7fb;
      color: #0b0f1e;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(720px, calc(100vw - 32px));
      padding: 28px;
      border: 1px solid #e1e6ef;
      border-radius: 20px;
      background: #fff;
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.12);
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 1.45rem;
      line-height: 1.15;
    }}
    p {{
      margin: 0;
      color: #5b647a;
      line-height: 1.5;
    }}
    .meta {{
      margin-top: 14px;
      font-size: 0.82rem;
      color: #7b8496;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.86em;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(title)}</h1>
    <p>{escape(message)}</p>
    <p class="meta">{escape(project_name)} · <code>{escape(code)}</code></p>
  </main>
</body>
</html>"""

    return _html_response(html, status=status)


# ─────────────────────────────────────────────────────────────
# Auth / context
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
            "user_id": user_id,
            "id": user_id,
            "authenticated": bool(user_id),
            "demo_mode": False,
            "persistent": bool(user_id),
            "source": "viewer_route_fallback",
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
    return _safe_bool(data.get("demo_mode") or data.get("is_demo") or data.get("demo"), False)


def _is_persistent_context(current_user: Optional[Mapping[str, Any]] = None) -> bool:
    data = _safe_dict(current_user) if current_user is not None else _current_user_payload(ensure=False)
    return _safe_bool(
        data.get("persistent"),
        default=bool(data.get("user_id") or data.get("id")) and not _is_demo_context(data),
    )


@bp.before_request
def _viewer_before_request() -> None:
    try:
        current_user = _current_user_payload(ensure=False)

        if _is_demo_context(current_user):
            return

        if not _is_persistent_context(current_user):
            return

        if ensure_default_user is not None:
            ensure_default_user()

    except Exception as exc:
        _log_warning("viewer ensure_default_user failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Workspace / project helpers
# ─────────────────────────────────────────────────────────────

def _normalize_workspace(value: Any, default: str = WORKSPACE_PROJECT) -> str:
    try:
        normalized = normalize_workspace_key(value, default)
        if normalized == "editor":
            normalized = WORKSPACE_EDITOR3D
        if normalized == "3d":
            normalized = WORKSPACE_EDITOR3D
        if normalized == "editor_3d":
            normalized = WORKSPACE_EDITOR3D
        if normalized == "2d":
            normalized = WORKSPACE_CAD2D
        if normalized in ALLOWED_WORKSPACES:
            return normalized
        return default
    except Exception:
        text = _safe_str(value, default, 80).lower().replace("-", "_")
        aliases = {
            "": WORKSPACE_PROJECT,
            "project": WORKSPACE_PROJECT,
            "projekt": WORKSPACE_PROJECT,
            "info": WORKSPACE_PROJECT,
            "map": WORKSPACE_MAP,
            "karte": WORKSPACE_MAP,
            "openlayer": WORKSPACE_MAP,
            "3d": WORKSPACE_EDITOR3D,
            "editor": WORKSPACE_EDITOR3D,
            "editor3d": WORKSPACE_EDITOR3D,
            "editor_3d": WORKSPACE_EDITOR3D,
            "viewer": WORKSPACE_EDITOR3D,
            "viewer3d": WORKSPACE_EDITOR3D,
            "2d": WORKSPACE_CAD2D,
            "cad": WORKSPACE_CAD2D,
            "cad2d": WORKSPACE_CAD2D,
            "lv": WORKSPACE_LV,
            "versions": WORKSPACE_VERSIONS,
            "versionen": WORKSPACE_VERSIONS,
            "admin": WORKSPACE_ADMIN,
            "settings": WORKSPACE_ADMIN,
            "team": WORKSPACE_ADMIN,
        }
        return aliases.get(text, default)


def _workspace_label(workspace: Any) -> str:
    return WORKSPACE_LABELS.get(_normalize_workspace(workspace), "Workspace")


def _is_new_identifier(identifier: Any) -> bool:
    text = _safe_str(identifier, "new", 120).lower()
    return text in {"", "new", "create", "neu"}


def _is_project_configured(project_payload: Mapping[str, Any]) -> bool:
    try:
        return _safe_bool(
            project_payload.get("is_configured")
            or project_payload.get("isConfigured")
            or project_payload.get("configured")
            or project_payload.get("project_configured")
            or project_payload.get("projectConfigured"),
            False,
        ) or _safe_str(project_payload.get("setup_status") or project_payload.get("setupStatus"), "", 80).lower() == "configured"
    except Exception:
        return False


def _default_new_project_payload(current_user: Mapping[str, Any]) -> Dict[str, Any]:
    demo_mode = _is_demo_context(current_user)
    persistent = _is_persistent_context(current_user)

    can_edit = bool(persistent and not demo_mode)
    can_manage = bool(can_edit)

    return {
        "id": "",
        "project_id": "",
        "public_id": "new",
        "publicId": "new",
        "name": "",
        "display_name": "Neues Projekt",
        "displayName": "Neues Projekt",
        "description": "",
        "address_text": "",
        "addressText": "",
        "address": {"text": ""},
        "visibility": "private",
        "is_public": False,
        "isPublic": False,
        "is_unlisted": False,
        "isUnlisted": False,
        "status": "draft",
        "setup_status": "draft",
        "setupStatus": "draft",
        "is_configured": False,
        "isConfigured": False,
        "is_new": True,
        "isNew": True,
        "demo_mode": demo_mode,
        "demoMode": demo_mode,
        "access": {
            "role": "owner" if can_manage else "viewer",
            "source": "new_project_context",
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
        },
        "publication": {
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
            "effective_published_workspaces": {
                "project": False,
                "map": False,
                "editor3d": False,
                "cad2d": False,
                "lv": False,
                "versions": False,
            },
            "require_auth": True,
            "require_project_permission": True,
        },
        "members": [],
        "invitations": [],
        "paths": {
            "projectPagePath": "/ui/project/new/project",
            "projectUrl": "/ui/project/new/project",
            "projectPublicUrl": "/project=new",
        },
    }


def _resolve_project_object(project_id: Any) -> Optional[Any]:
    if resolve_project is None:
        return None

    try:
        if _is_new_identifier(project_id):
            return None

        return resolve_project(project_id)

    except Exception:
        return None


def _serialize_access(project: Any, user_id: Optional[int]) -> Dict[str, Any]:
    try:
        if serialize_project_permissions is not None:
            return _safe_dict(serialize_project_permissions(project, user_id=user_id))
    except Exception:
        pass

    try:
        if get_project_permission_result is not None:
            result = get_project_permission_result(project, user_id=user_id, allow_public_view=True)
            if hasattr(result, "to_dict"):
                return _safe_dict(result.to_dict())
            return _safe_dict(result)
    except Exception:
        pass

    return {
        "can_view": False,
        "can_edit": False,
        "can_manage": False,
        "permissions": {},
        "source": "unavailable",
    }


def _can_manage(project: Any, user_id: Optional[int]) -> bool:
    try:
        if project is None or not user_id:
            return False

        if can_manage_project is not None:
            return bool(can_manage_project(project, user_id))
    except Exception:
        pass

    access = _serialize_access(project, user_id)
    permissions = _safe_dict(access.get("permissions"))
    return _safe_bool(access.get("can_manage") or permissions.get("manage"), False)


def _serialize_project_payload_fallback(
    *,
    project: Any,
    user_id: Optional[int],
) -> Dict[str, Any]:
    try:
        if serialize_project is None:
            return {
                "id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "publicId": getattr(project, "public_id", None),
                "name": getattr(project, "name", None),
                "display_name": getattr(project, "name", None),
                "displayName": getattr(project, "name", None),
                "description": getattr(project, "description", None),
                "address_text": getattr(project, "address_text", None),
                "addressText": getattr(project, "address_text", None),
                "address": {"text": getattr(project, "address_text", None)},
                "visibility": getattr(project, "visibility", "private"),
                "setup_status": getattr(project, "setup_status", "draft"),
                "setupStatus": getattr(project, "setup_status", "draft"),
                "is_configured": getattr(project, "is_configured", False),
                "isConfigured": getattr(project, "is_configured", False),
                "access": _serialize_access(project, user_id),
            }

        try:
            return _safe_dict(
                serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_members=_can_manage(project, user_id),
                    include_service_links=_can_manage(project, user_id),
                    include_versions=True,
                    include_embed_policy=_can_manage(project, user_id),
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
                        include_members=_can_manage(project, user_id),
                        include_service_links=_can_manage(project, user_id),
                        include_versions=True,
                        include_embed_policy=_can_manage(project, user_id),
                    )
                )
            except TypeError:
                return _safe_dict(serialize_project(project, user_id=user_id))

    except Exception:
        return {}


def _load_project_payload(
    project_id: Any,
    *,
    current_user: Mapping[str, Any],
) -> Tuple[Optional[Any], Optional[Dict[str, Any]], Optional[Response]]:
    user_id = _current_user_id_optional()

    if _is_new_identifier(project_id):
        return None, _default_new_project_payload(current_user), None

    project = _resolve_project_object(project_id)

    if project is None:
        return None, None, _error_response(
            "Projekt nicht gefunden.",
            404,
            code="project_not_found",
            extra={"project_id": _safe_str(project_id, "", 160)},
        )

    try:
        if get_project_result is not None:
            result = get_project_result(
                _safe_str(project_id, "", 160),
                user_id=user_id,
                include_deleted=False,
            )
            payload = result.to_dict() if hasattr(result, "to_dict") else _safe_dict(result)

            if not _safe_bool(payload.get("ok"), False):
                status = _safe_int(payload.get("status_code"), _safe_int(getattr(result, "status_code", 403), 403))
                return project, None, _error_response(
                    _safe_str(payload.get("error") or payload.get("message"), "Projektzugriff nicht erlaubt.", 500),
                    status,
                    code=_safe_str(payload.get("code"), "project_permission_denied", 120),
                    extra={"payload": payload},
                )

            project_payload = _safe_dict(payload.get("project"))
            if project_payload:
                project_payload["is_new"] = False
                project_payload["isNew"] = False
                project_payload["demo_mode"] = _is_demo_context(current_user)
                project_payload["demoMode"] = _is_demo_context(current_user)
                return project, project_payload, None
    except Exception as exc:
        _log_warning("get_project_result failed; falling back to serialize_project: %s", exc.__class__.__name__)

    try:
        project_payload = _serialize_project_payload_fallback(project=project, user_id=user_id)

        if not project_payload:
            project_payload = {
                "id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "publicId": getattr(project, "public_id", None),
                "name": getattr(project, "name", None),
                "description": getattr(project, "description", None),
                "address_text": getattr(project, "address_text", None),
                "visibility": getattr(project, "visibility", "private"),
                "access": _serialize_access(project, user_id),
            }

        if not project_payload.get("access"):
            project_payload["access"] = _serialize_access(project, user_id)

        access = _safe_dict(project_payload.get("access"))

        if not _safe_bool(access.get("can_view"), False):
            return project, None, _error_response(
                "Du hast keine Berechtigung, dieses Projekt zu sehen.",
                403,
                code="project_permission_denied",
                extra={"project": project_payload, "access": access},
            )

        project_payload["is_new"] = False
        project_payload["isNew"] = False
        project_payload["demo_mode"] = _is_demo_context(current_user)
        project_payload["demoMode"] = _is_demo_context(current_user)

        return project, project_payload, None

    except Exception as exc:
        _log_exception("project payload serialization failed", exc)
        return project, None, _error_response(
            "Projekt konnte nicht geladen werden.",
            500,
            code="project_load_failed",
            extra={"error_type": exc.__class__.__name__},
        )


def _apply_publication_payload(
    *,
    project: Any,
    project_payload: Dict[str, Any],
    current_user: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        user_id = _current_user_id_optional()
        include_private = bool(project is not None and user_id and _can_manage(project, user_id))

        if project is not None and callable(get_project_publication):
            publication_result = get_project_publication(
                project,
                actor_user_id=user_id,
                include_private=include_private,
                for_public=not bool(user_id),
                use_cache=False,
            )
            publication_result_payload = _safe_dict(publication_result)
            publication_payload = _safe_dict(publication_result_payload.get("publication"))

            if publication_payload:
                project_payload["publication"] = publication_payload

        return project_payload

    except Exception:
        return project_payload


def _check_workspace_access(
    *,
    project: Any,
    project_payload: Mapping[str, Any],
    workspace: str,
    current_user: Mapping[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    normalized_workspace = _normalize_workspace(workspace)
    user_id = _current_user_id_optional()
    access = _safe_dict(project_payload.get("access"))

    if _is_new_identifier(project_payload.get("public_id") or project_payload.get("publicId")):
        return normalized_workspace == WORKSPACE_PROJECT, {
            "ok": normalized_workspace == WORKSPACE_PROJECT,
            "code": "new_project_workspace",
            "workspace": normalized_workspace,
            "status_code": 200 if normalized_workspace == WORKSPACE_PROJECT else 409,
        }

    if normalized_workspace not in {WORKSPACE_PROJECT, WORKSPACE_ADMIN} and not _is_project_configured(project_payload):
        return False, {
            "ok": False,
            "code": "project_not_configured",
            "workspace": normalized_workspace,
            "status_code": 409,
            "message": "Projekt muss zuerst konfiguriert werden.",
        }

    if normalized_workspace in ADMIN_WORKSPACES:
        can_manage = _safe_bool(access.get("can_manage"), False) or bool(project is not None and user_id and _can_manage(project, user_id))
        allowed = bool(can_manage and not _is_demo_context(current_user))
        return allowed, {
            "ok": allowed,
            "code": "admin_requires_manage" if not allowed else "admin_allowed",
            "workspace": normalized_workspace,
            "status_code": 200 if allowed else 403,
            "access": access,
        }

    if _safe_bool(access.get("can_view"), False) and not _is_demo_context(current_user):
        return True, {
            "ok": True,
            "code": "project_permission",
            "workspace": normalized_workspace,
            "status_code": 200,
            "access": access,
        }

    if callable(can_access_project_workspace) and project is not None:
        try:
            result = can_access_project_workspace(
                project,
                normalized_workspace,
                actor_user_id=user_id,
                public_request=not bool(user_id),
            )
            result_payload = _safe_dict(result)
            result_payload.setdefault("workspace", normalized_workspace)
            result_payload.setdefault("status_code", 200 if _safe_bool(result_payload.get("ok"), False) else 403)
            return _safe_bool(result_payload.get("ok"), False), result_payload
        except Exception as exc:
            return False, {
                "ok": False,
                "code": "workspace_access_check_failed",
                "error": str(exc),
                "workspace": normalized_workspace,
                "status_code": 500,
            }

    return False, {
        "ok": False,
        "code": "workspace_permission_denied",
        "workspace": normalized_workspace,
        "status_code": 403,
        "access": access,
    }


def _routes_payload(project_payload: Mapping[str, Any], workspace: str) -> Dict[str, str]:
    public_id = _safe_str(
        project_payload.get("public_id") or project_payload.get("publicId"),
        "new",
        160,
    )

    if not public_id:
        public_id = "new"

    base = f"/ui/project/{public_id}"

    return {
        "project": f"{base}/project",
        "context": f"{base}/context.json",
        "workspace": f"{base}/{_normalize_workspace(workspace)}",
        "publication": f"/v1/projects/{public_id}/publication" if public_id != "new" else "",
        "members": f"/v1/projects/{public_id}/members" if public_id != "new" else "",
        "invitations": f"/v1/projects/{public_id}/invitations" if public_id != "new" else "",
        "api": f"/v1/projects/{public_id}" if public_id != "new" else "/v1/projects",
        "public": f"/project={public_id}",
    }


def _build_template_context(
    project_id: Any,
    *,
    workspace: Any = WORKSPACE_PROJECT,
) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    current_user = _current_user_payload(ensure=False)
    normalized_workspace = _normalize_workspace(workspace)

    project, project_payload, error_response = _load_project_payload(
        project_id,
        current_user=current_user,
    )

    if error_response is not None:
        return None, error_response

    if project_payload is None:
        return None, _error_response(
            "Projekt konnte nicht geladen werden.",
            500,
            code="project_payload_missing",
        )

    project_payload = _apply_publication_payload(
        project=project,
        project_payload=project_payload,
        current_user=current_user,
    )

    allowed, workspace_access = _check_workspace_access(
        project=project,
        project_payload=project_payload,
        workspace=normalized_workspace,
        current_user=current_user,
    )

    if not allowed:
        default_status = 401 if not _safe_bool(current_user.get("authenticated") or current_user.get("is_authenticated"), False) else 403
        status = _safe_int(workspace_access.get("status_code"), default_status)

        return None, _error_response(
            _safe_str(workspace_access.get("message"), "Dieser Workspace ist für dich nicht verfügbar.", 300),
            status,
            code=_safe_str(workspace_access.get("code"), "workspace_permission_denied", 160),
            extra={
                "workspace": normalized_workspace,
                "workspace_access": workspace_access,
                "project": {
                    "id": project_payload.get("id"),
                    "public_id": project_payload.get("public_id") or project_payload.get("publicId"),
                    "name": project_payload.get("name"),
                },
            },
        )

    project_payload["current_workspace"] = normalized_workspace
    project_payload["currentWorkspace"] = normalized_workspace
    project_payload["routes"] = {
        **_safe_dict(project_payload.get("routes")),
        **_routes_payload(project_payload, normalized_workspace),
    }

    access_payload = _safe_dict(project_payload.get("access"))

    context: Dict[str, Any] = {
        "ok": True,
        "project": project_payload,
        "current_project": project_payload,
        "current_user": current_user,
        "auth": current_user,
        "auth_context": current_user,
        "is_new": _is_new_identifier(project_payload.get("public_id") or project_payload.get("publicId")),
        "workspace": normalized_workspace,
        "workspace_label": _workspace_label(normalized_workspace),
        "workspace_access": workspace_access,
        "can_edit": _safe_bool(access_payload.get("can_edit"), False),
        "can_manage": _safe_bool(access_payload.get("can_manage"), False),
        "demo_mode": _is_demo_context(current_user),
        "demo_ttl_seconds": current_user.get("ttl_seconds") or current_user.get("ttlSeconds"),
        "demo_expires_at": current_user.get("expires_at") or current_user.get("expiresAt"),
        "routes": project_payload["routes"],
        "_project_obj": project,
    }

    return context, None


# ─────────────────────────────────────────────────────────────
# External workspace helpers
# ─────────────────────────────────────────────────────────────

def _is_external_workspace_key(workspace: Any) -> bool:
    normalized = _normalize_workspace(workspace)

    try:
        if callable(is_external_workspace):
            return bool(is_external_workspace(normalized))
    except Exception:
        pass

    return normalized in EXTERNAL_REDIRECT_WORKSPACES


def _request_extra_embed_params() -> Dict[str, Any]:
    """
    Forward only harmless UI/debug params from the app gateway to the target service.
    Do not forward tokens, auth headers, arbitrary redirect targets or secrets.
    """
    allowed = {
        "theme",
        "lang",
        "locale",
        "debug",
        "debug_ui",
        "debug_chunks",
        "devtools",
        "camera",
        "view",
        "spawn",
        "mode",
        "quality",
        "renderer",
    }

    blocked = {
        "token",
        "access_token",
        "refresh_token",
        "jwt",
        "secret",
        "password",
        "api_key",
        "apikey",
        "key",
        "authorization",
        "auth",
        "redirect",
        "redirect_url",
        "return_url",
        "next",
        "url",
    }

    result: Dict[str, Any] = {}

    try:
        for key in request.args.keys():
            key_text = _safe_str(key, "", 160)
            key_lower = key_text.lower()

            if not key_text or key_lower in blocked or key_lower not in allowed:
                continue

            values = request.args.getlist(key)
            clean_values = [_safe_str(value, "", 2000) for value in values if _safe_str(value, "", 2000)]

            if not clean_values:
                continue

            result[key_text] = clean_values if len(clean_values) > 1 else clean_values[0]

    except Exception:
        pass

    return result


def _embed_result_to_dict(result: Any) -> Dict[str, Any]:
    try:
        if hasattr(result, "to_dict") and callable(result.to_dict):
            return _safe_dict(result.to_dict())

        return _safe_dict(result)

    except Exception:
        return {}


def _embed_result_ok(result: Any) -> bool:
    try:
        if hasattr(result, "ok"):
            return bool(getattr(result, "ok"))

        return _safe_bool(_safe_dict(result).get("ok"), False)

    except Exception:
        return False


def _embed_result_url(result: Any) -> str:
    try:
        if hasattr(result, "url"):
            return _safe_str(getattr(result, "url"), "", 4000)

        data = _safe_dict(result)
        return _safe_str(data.get("url") or data.get("target_url"), "", 4000)

    except Exception:
        return ""


def _build_external_workspace_result(context: Mapping[str, Any]) -> Any:
    workspace = _normalize_workspace(context.get("workspace"))
    project_payload = _safe_dict(context.get("project"))
    current_user = _safe_dict(context.get("current_user") or context.get("auth") or context.get("auth_context"))
    project_obj = context.get("_project_obj")
    extra_params = _request_extra_embed_params()

    try:
        if workspace == WORKSPACE_EDITOR3D and callable(build_editor3d_embed_result):
            return build_editor3d_embed_result(
                project=project_obj,
                project_payload=project_payload,
                current_user=current_user,
                request_obj=request,
                extra_params=extra_params,
                include_context=True,
                include_return_url=True,
                include_chunk_hints=None,
                prefer_request_host=True,
            )

        if workspace == WORKSPACE_MAP and callable(build_map_embed_result):
            return build_map_embed_result(
                project=project_obj,
                project_payload=project_payload,
                current_user=current_user,
                request_obj=request,
                extra_params=extra_params,
                include_context=True,
                include_return_url=True,
                prefer_request_host=True,
            )

        if callable(build_workspace_embed_result):
            return build_workspace_embed_result(
                workspace,
                project=project_obj,
                project_payload=project_payload,
                current_user=current_user,
                request_obj=request,
                extra_params=extra_params,
                include_context=True,
                include_return_url=True,
                include_chunk_hints=None,
                prefer_request_host=True,
            )

    except Exception as exc:
        _log_exception("external workspace embed build failed", exc)
        return {
            "ok": False,
            "workspace": workspace,
            "code": "embed_build_exception",
            "error": f"{exc.__class__.__name__}: {exc}",
            "message": "Embed-Ziel konnte nicht gebaut werden.",
        }

    return {
        "ok": False,
        "workspace": workspace,
        "code": "workspace_embed_service_unavailable",
        "error": "workspace_embed_service_unavailable",
        "message": "Workspace-Embed-Service ist nicht verfügbar.",
    }


def _external_workspace_status_code(embed_result: Mapping[str, Any]) -> int:
    code = _safe_str(embed_result.get("code"), "", 160)

    if code in {"project_public_id_missing", "project_not_configured"}:
        return 409

    if code in {"embed_disabled", "workspace_embed_service_unavailable"}:
        return 503

    if code in {"workspace_forbidden", "workspace_not_external"}:
        return 403

    return 502


def _render_external_workspace_redirect(context: Mapping[str, Any]) -> Response:
    workspace = _normalize_workspace(context.get("workspace"))
    project_payload = _safe_dict(context.get("project"))

    result = _build_external_workspace_result(context)
    result_payload = _embed_result_to_dict(result)
    target_url = _embed_result_url(result)

    if _embed_result_ok(result) and target_url:
        return _redirect_response(target_url, status=302, workspace=workspace)

    message = _safe_str(
        result_payload.get("message"),
        f"Workspace {workspace!r} konnte nicht geöffnet werden.",
        500,
    )

    return _error_response(
        message,
        _external_workspace_status_code(result_payload),
        code=_safe_str(result_payload.get("code"), "workspace_embed_failed", 160),
        extra={
            "workspace": workspace,
            "embed": result_payload,
            "project": {
                "id": project_payload.get("id"),
                "public_id": project_payload.get("public_id") or project_payload.get("publicId"),
                "name": project_payload.get("name"),
            },
        },
    )


# ─────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────

def _render_project_workspace(context: Mapping[str, Any]) -> Response:
    try:
        html = render_template("viewer/project.html", **dict(context))
        return _html_response(html, status=200)
    except TemplateNotFound:
        return _error_response(
            "Template viewer/project.html wurde nicht gefunden.",
            500,
            code="template_missing",
        )
    except Exception as exc:
        _log_exception("render project workspace failed", exc)
        return _error_response(
            "Projekt-Workspace konnte nicht gerendert werden.",
            500,
            code="project_workspace_render_failed",
            extra={"error_type": exc.__class__.__name__},
        )


def _render_generic_workspace(context: Mapping[str, Any]) -> Response:
    project = _safe_dict(context.get("project"))
    workspace = _safe_str(context.get("workspace"), WORKSPACE_PROJECT, 80)
    label = _safe_str(context.get("workspace_label"), _workspace_label(workspace), 80)
    project_name = _safe_str(project.get("name") or project.get("display_name") or project.get("displayName"), "Projekt", 200)
    public_id = _safe_str(project.get("public_id") or project.get("publicId"), "", 160)

    html = f"""<!doctype html>
<html lang="de" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="referrer" content="no-referrer">
  <title>{escape(label)} · {escape(project_name)} · VECTOPLAN</title>
  <link rel="stylesheet" href="/static/css/project_workspace.css?v={escape(public_id or 'new')}">
</head>
<body>
  <main class="vp-project-workspace" data-project-workspace data-project-public-id="{escape(public_id)}" data-project-current-workspace="{escape(workspace)}">
    <section class="vp-project-hero" aria-labelledby="workspaceTitle">
      <div class="vp-project-hero__content">
        <div class="vp-project-hero__eyebrow">Workspace</div>
        <h1 id="workspaceTitle" class="vp-project-hero__title">{escape(label)}</h1>
        <p class="vp-project-hero__text">
          Dieser Workspace ist freigeschaltet, wird aber in vectoplan-app nur als Shell gerendert.
          Die fachliche Oberfläche wird später vom zuständigen Microservice bereitgestellt.
        </p>
      </div>
      <div class="vp-project-hero__status">
        <span class="vp-project-status vp-project-status--configured">Verfügbar</span>
      </div>
    </section>

    <section class="vp-project-card">
      <header class="vp-project-card__header">
        <div>
          <h2 class="vp-project-card__title">{escape(project_name)}</h2>
          <p class="vp-project-card__text">
            vectoplan-app speichert hier keine {escape(label)}-Fachdaten. Projektrollen, Sichtbarkeit und Shell-Kontext kommen aus der App; Fachdaten kommen aus den separaten Services.
          </p>
        </div>
      </header>
    </section>
  </main>
</body>
</html>"""

    return _html_response(html, status=200)


def _render_workspace(context: Mapping[str, Any]) -> Response:
    workspace = _normalize_workspace(context.get("workspace"))

    if _is_external_workspace_key(workspace):
        return _render_external_workspace_redirect(context)

    if workspace == WORKSPACE_PROJECT or workspace == WORKSPACE_ADMIN:
        return _render_project_workspace(context)

    return _render_generic_workspace(context)


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/_status")
@bp.get("/ui/project/_status")
def viewer_status() -> Response:
    payload: Dict[str, Any] = {
        "ok": True,
        "service": "viewer",
        "blueprint": "viewer",
        "routes": {
            "new_project": "/ui/project/new/project",
            "project_workspace": "/ui/project/<project_id>/project",
            "workspace": "/ui/project/<project_id>/<workspace>",
            "context": "/ui/project/<project_id>/context.json",
        },
        "current_user": _current_user_payload(ensure=False),
        "workspaces": sorted(ALLOWED_WORKSPACES),
        "external_workspaces": sorted(EXTERNAL_REDIRECT_WORKSPACES),
    }

    try:
        if get_current_user_status is not None:
            payload["current_user_status"] = get_current_user_status()
    except Exception:
        pass

    try:
        if get_project_service_status is not None:
            payload["project_service"] = get_project_service_status()
    except Exception:
        pass

    try:
        if callable(get_workspace_embed_status):
            payload["workspace_embed"] = get_workspace_embed_status()
    except Exception:
        pass

    return _json_response(payload, status=200)


@bp.get("/ui/project/new/context.json")
def project_new_context_json() -> Response:
    context, error = _build_template_context("new", workspace=WORKSPACE_PROJECT)

    if error is not None:
        return error

    return _json_response(_public_context_payload(context or {}), status=200)


@bp.get("/ui/project/<project_id>/context.json")
def project_context_json(project_id: str) -> Response:
    context, error = _build_template_context(project_id, workspace=WORKSPACE_PROJECT)

    if error is not None:
        return error

    return _json_response(_public_context_payload(context or {}), status=200)


@bp.get("/ui/project/<project_id>/<workspace>/context.json")
def project_workspace_context_json(project_id: str, workspace: str) -> Response:
    context, error = _build_template_context(project_id, workspace=workspace)

    if error is not None:
        return error

    return _json_response(_public_context_payload(context or {}), status=200)


def _public_context_payload(context: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Strip internal Python objects before returning context.json.
    """
    payload = _safe_dict(context)

    try:
        payload.pop("_project_obj", None)
    except Exception:
        pass

    return payload


@bp.get("/ui/project/new")
@bp.get("/ui/project/new/project")
def project_new_workspace() -> Response:
    context, error = _build_template_context("new", workspace=WORKSPACE_PROJECT)

    if error is not None:
        return error

    return _render_project_workspace(context or {})


@bp.get("/ui/project/<project_id>")
@bp.get("/ui/project/<project_id>/")
@bp.get("/ui/project/<project_id>/project")
def project_workspace(project_id: str) -> Response:
    context, error = _build_template_context(project_id, workspace=WORKSPACE_PROJECT)

    if error is not None:
        return error

    return _render_project_workspace(context or {})


@bp.get("/ui/project/<project_id>/<workspace>")
def project_named_workspace(project_id: str, workspace: str) -> Response:
    normalized_workspace = _normalize_workspace(workspace)
    context, error = _build_template_context(project_id, workspace=normalized_workspace)

    if error is not None:
        return error

    return _render_workspace(context or {})


# Compatibility aliases for direct imports in older code.
project_view = project_workspace
project_new = project_new_workspace


__all__ = [
    "bp",
    "viewer_bp",
    "project_viewer_bp",
    "viewer_status",
    "project_new_context_json",
    "project_context_json",
    "project_workspace_context_json",
    "project_new_workspace",
    "project_workspace",
    "project_named_workspace",
]