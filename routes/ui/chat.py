# services/vectoplan-app/routes/ui/chat.py
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

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
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response

from extensions import db
from models import Blob, Conversation
import messages as msg


bp = Blueprint("ui_chat", __name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

ALLOWED_EXTS = {
    ".dxf",
    ".ifc",
    ".obj",
    ".stl",
    ".gltf",
    ".glb",
}

ALLOWED_MIMES = {
    "application/octet-stream",
    "text/plain",
    "model/ifc",
    "application/ifc",
    "application/x-ifc",
    "model/obj",
    "application/x-tgif",
    "model/stl",
    "application/sla",
    "model/x.stl-binary",
    "model/x.stl-ascii",
    "application/vnd.ms-pki.stl",
    "model/gltf+json",
    "model/gltf-binary",
    "application/gltf+json",
    "application/gltf-buffer",
    "application/dxf",
    "application/x-dxf",
    "image/vnd.dxf",
    "image/x-dxf",
}

DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"
DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"

DEFAULT_ALLOWED_FRAME_SRC = (
    "self",
    "http://localhost:5100",
    "http://127.0.0.1:5100",
    "http://localhost:5190",
    "http://127.0.0.1:5190",
)

DEFAULT_ALLOWED_FRAME_PARENTS = (
    "http://localhost:5103",
    "http://127.0.0.1:5103",
)

_SPLIT_RE = re.compile(r"[\s,;]+")


# ─────────────────────────────────────────────────────────────
# Cached parsing helpers
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _cached_split_text_list(raw: str, fallback: str = "") -> Tuple[str, ...]:
    try:
        text = str(raw or "").strip()

        if not text:
            text = str(fallback or "").strip()

        if not text:
            return tuple()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                result: List[str] = []
                for item in parsed:
                    item_text = str(item or "").strip()
                    if item_text and item_text not in result:
                        result.append(item_text)
                return tuple(result)
        except Exception:
            pass

        result = []
        for part in _SPLIT_RE.split(text):
            item_text = str(part or "").strip()
            if item_text and item_text not in result:
                result.append(item_text)

        return tuple(result)

    except Exception:
        try:
            return tuple(str(fallback or "").split())
        except Exception:
            return tuple()


@lru_cache(maxsize=128)
def _cached_origin_from_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())

        if parsed.scheme not in {"http", "https"}:
            return ""

        if not parsed.netloc:
            return ""

        return f"{parsed.scheme}://{parsed.netloc}"

    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────

def _cfg_raw(key: str, default: Any = None) -> Any:
    try:
        value = current_app.config.get(key)

        if value is not None and value != "":
            return value

        env_value = os.environ.get(key)
        if env_value is not None and str(env_value).strip() != "":
            return env_value

        return default

    except Exception:
        return default


def _cfg_str(key: str, default: str = "") -> str:
    try:
        value = _cfg_raw(key, default)
        return str(value if value is not None else default).strip()
    except Exception:
        return default


def _cfg_first(keys: Sequence[str], default: str = "") -> str:
    try:
        for key in keys:
            value = _cfg_str(str(key), "")
            if value:
                return value
        return default
    except Exception:
        return default


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        value = _cfg_raw(key, default)

        if isinstance(value, bool):
            return bool(value)

        text = str(value).strip().lower()

        if text in {"1", "true", "t", "yes", "y", "on", "ja"}:
            return True

        if text in {"0", "false", "f", "no", "n", "off", "nein"}:
            return False

        return default

    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        value = _cfg_raw(key, default)
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _cfg_text_list(keys: Sequence[str], default: Iterable[str]) -> List[str]:
    try:
        fallback = " ".join(str(item) for item in default)
        raw = _cfg_first(keys, fallback)

        parsed = list(_cached_split_text_list(raw, fallback))

        result: List[str] = []
        for item in parsed:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)

        return result or list(default)

    except Exception:
        return list(default)


# ─────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────

def _log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        current_app.logger.warning(message, *args, **kwargs)
    except Exception:
        pass


def _log_exception(message: str, exc: Exception | None = None) -> None:
    try:
        if exc is None:
            current_app.logger.exception(message)
        else:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Response / security helpers
# ─────────────────────────────────────────────────────────────

def _is_dev() -> bool:
    try:
        env = str(current_app.config.get("FLASK_ENV", "") or "").lower()
        return env.startswith("dev") or env.startswith("development")
    except Exception:
        return False


def _csp_token(value: str) -> str:
    try:
        text = str(value or "").strip()

        if text in {"self", "'self'"}:
            return "'self'"

        return text

    except Exception:
        return ""


def _csp_join(values: Sequence[str]) -> str:
    try:
        result: List[str] = []

        for item in values:
            token = _csp_token(item)
            if token and token not in result:
                result.append(token)

        return " ".join(result)

    except Exception:
        return ""


def _app_frame_src_values() -> List[str]:
    try:
        configured = _cfg_text_list(
            (
                "VECTOPLAN_APP_ALLOWED_FRAME_SRC",
                "APP_ALLOWED_FRAME_SRC",
                "CSP_FRAME_SRC",
                "SECURITY_CSP_FRAME_SRC",
            ),
            DEFAULT_ALLOWED_FRAME_SRC,
        )

        result: List[str] = []

        for item in configured:
            token = str(item or "").strip()
            if not token:
                continue
            if token not in result:
                result.append(token)

        editor_origin = _cached_origin_from_url(
            _cfg_first(
                (
                    "VECTOPLAN_EDITOR_PUBLIC_URL",
                    "VECTOPLAN_EDITOR_PUBLIC_BASE_URL",
                    "EDITOR_PUBLIC_URL",
                ),
                DEFAULT_EDITOR_PUBLIC_URL,
            )
        )

        openlayer_origin = _cached_origin_from_url(
            _cfg_first(
                (
                    "OPENLAYER_PUBLIC_URL",
                    "OPENLAYER_PUBLIC_BASE_URL",
                    "VECTOPLAN_OPENLAYER_PUBLIC_URL",
                ),
                DEFAULT_OPENLAYER_PUBLIC_URL,
            )
        )

        for origin in (editor_origin, openlayer_origin):
            if origin and origin not in result:
                result.append(origin)

        if "self" not in result and "'self'" not in result:
            result.insert(0, "self")

        return result

    except Exception:
        return list(DEFAULT_ALLOWED_FRAME_SRC)


def _frame_ancestors_values() -> List[str]:
    try:
        return _cfg_text_list(
            (
                "VECTOPLAN_ALLOWED_FRAME_PARENTS",
                "VECTOPLAN_FRAME_ANCESTORS",
                "FRAME_ANCESTORS",
            ),
            DEFAULT_ALLOWED_FRAME_PARENTS,
        )
    except Exception:
        return list(DEFAULT_ALLOWED_FRAME_PARENTS)


def _workspace_csp_header_value() -> str:
    try:
        frame_src = _csp_join(_app_frame_src_values())
        frame_ancestors = _csp_join(["self", *_frame_ancestors_values()])

        directives = [
            f"frame-src {frame_src}",
            f"child-src {frame_src}",
            f"frame-ancestors {frame_ancestors}",
        ]

        return "; ".join(directive for directive in directives if directive.strip())

    except Exception:
        return (
            "frame-src 'self' http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "child-src 'self' http://localhost:5100 http://127.0.0.1:5100 "
            "http://localhost:5190 http://127.0.0.1:5190; "
            "frame-ancestors 'self' http://localhost:5103 http://127.0.0.1:5103"
        )


def _apply_security_headers(resp: Response) -> None:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    except Exception:
        pass


def _apply_cache_headers(resp: Response, *, no_store: bool = False) -> None:
    try:
        if no_store or _is_dev():
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass


def _apply_frame_headers(resp: Response, *, allow_embed: Optional[bool] = None, workspace_shell: bool = False) -> None:
    try:
        if allow_embed is None:
            allow_embed = request.args.get("allow_embed") == "1" or request.args.get("embed") == "1"

        if workspace_shell:
            resp.headers["Content-Security-Policy"] = _workspace_csp_header_value()
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            return

        if allow_embed:
            frame_ancestors = _csp_join(["self", *_frame_ancestors_values()])
            resp.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    except Exception:
        pass


def _finalize_html_response(
    resp: Response,
    *,
    no_store: bool = False,
    allow_embed: Optional[bool] = None,
    workspace_shell: bool = False,
) -> Response:
    try:
        _apply_security_headers(resp)
    except Exception:
        pass

    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass

    try:
        _apply_frame_headers(resp, allow_embed=allow_embed, workspace_shell=workspace_shell)
    except Exception:
        pass

    return resp


def _finalize_json_response(resp: Response, *, no_store: bool = True) -> Response:
    try:
        _apply_security_headers(resp)
    except Exception:
        pass

    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass

    return resp


def _json_error(message: str, status: int = 500, *, code: Optional[str] = None) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "error": message,
    }

    if code:
        payload["code"] = code

    resp = jsonify(payload)
    resp.status_code = status
    _finalize_json_response(resp, no_store=True)
    return resp


# ─────────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────────

def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def _safe_filename(filename: Optional[str], fallback: str = "upload") -> str:
    try:
        cleaned = secure_filename(filename or "")
        return cleaned or fallback
    except Exception:
        return fallback


def _safe_quote(value: Any) -> str:
    try:
        return quote(str(value), safe="")
    except Exception:
        return ""


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


def _safe_bool(value: Any, default: bool = False) -> bool:
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


def _validate_filetype(filename: str, mimetype: Optional[str]) -> Tuple[bool, str]:
    try:
        ext = _ext_of(filename)

        if ext in ALLOWED_EXTS:
            return True, ext

        mime = (mimetype or "").lower().strip()

        if mime in ALLOWED_MIMES:
            if "ifc" in mime:
                return True, ".ifc"
            if "stl" in mime or "sla" in mime:
                return True, ".stl"
            if "obj" in mime or mime == "text/plain":
                return True, ".obj"
            if "gltf+json" in mime:
                return True, ".gltf"
            if "gltf-binary" in mime or "gltf-buffer" in mime:
                return True, ".glb"
            if "dxf" in mime or "vnd.dxf" in mime or "x-dxf" in mime:
                return True, ".dxf"

        return False, ext

    except Exception:
        return False, ""


def _file_urls(file_id: str) -> Dict[str, str]:
    try:
        file_id_safe = quote(str(file_id), safe="")
        return {
            "content_url": f"/v1/files/{file_id_safe}/content",
            "download_url": f"/v1/files/{file_id_safe}/download",
            "meta_url": f"/v1/files/{file_id_safe}",
        }
    except Exception:
        return {}


def _url_for_safe(endpoint: str, fallback: str, **values: Any) -> str:
    try:
        return str(url_for(endpoint, **values))
    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────
# Project-shell compatibility helpers
# ─────────────────────────────────────────────────────────────

def _project_root_url() -> str:
    try:
        return _url_for_safe("ui_projects.project_root", "/", _external=False)
    except Exception:
        return "/"


def _project_public_url(project_identifier: Any = None) -> str:
    try:
        value = _safe_str(project_identifier, "", 160)

        if not value or value == "new":
            return "/project=new"

        return f"/project={_safe_quote(value)}"

    except Exception:
        return "/project=new"


def _project_identifier_from_request() -> str:
    try:
        candidates = (
            request.args.get("project"),
            request.args.get("project_id"),
            request.args.get("projectPublicId"),
            request.args.get("project_public_id"),
            request.args.get("p"),
        )

        for candidate in candidates:
            value = _safe_str(candidate, "", 160)
            if value:
                return value

        return ""

    except Exception:
        return ""


def _legacy_shell_enabled() -> bool:
    """
    Diagnostic escape hatch.

    Default behavior of /ui/chat-3d is redirect to the new project shell.
    If needed during local debugging, set:
    - ?legacy_shell=1
    or config:
    - VECTOPLAN_ALLOW_LEGACY_CHAT_3D_SHELL=true
    """
    try:
        if request.args.get("legacy_shell") == "1" or request.args.get("legacy") == "1":
            return True

        return _cfg_bool("VECTOPLAN_ALLOW_LEGACY_CHAT_3D_SHELL", False)

    except Exception:
        return False


def _get_current_user_id_safe() -> int:
    try:
        from services.current_user import get_current_user_id

        return int(get_current_user_id())
    except Exception:
        return 1


def _current_user_context_safe() -> Dict[str, Any]:
    try:
        from services.current_user import get_current_user_context

        ctx = get_current_user_context(ensure=True)
        if hasattr(ctx, "to_dict"):
            return ctx.to_dict()

        return dict(ctx)
    except Exception:
        return {
            "id": 1,
            "user_id": 1,
            "public_id": "u_demo_1",
            "display_name": "Demo User",
            "is_placeholder": True,
            "source": "fallback",
        }


def _project_for_conversation(chat_id: str) -> Any:
    try:
        from services.project_service import get_project_by_conversation_id

        project = get_project_by_conversation_id(chat_id)
        if project is not None:
            return project
    except Exception:
        pass

    try:
        from models import Project

        return Project.query.filter_by(conversation_id=chat_id).one_or_none()
    except Exception:
        return None


def _project_public_id_for_project(project: Any) -> str:
    try:
        if project is None:
            return ""

        return (
            _safe_str(getattr(project, "public_id", None), "", 160)
            or _safe_str(getattr(project, "id", None), "", 160)
        )
    except Exception:
        return ""


def _project_redirect_target_for_chat(chat_id: Optional[str]) -> str:
    try:
        explicit_project = _project_identifier_from_request()
        if explicit_project:
            return _project_public_url(explicit_project)

        if chat_id:
            project = _project_for_conversation(chat_id)
            project_public_id = _project_public_id_for_project(project)

            if project_public_id:
                return _project_public_url(project_public_id)

        return _project_root_url()

    except Exception:
        return "/"


def _project_payload_for_chat(chat_id: str) -> Dict[str, Any]:
    try:
        project = _project_for_conversation(chat_id)
        user_id = _get_current_user_id_safe()

        if project is not None:
            try:
                from services.project_service import serialize_project

                return serialize_project(
                    project,
                    user_id=user_id,
                    include_permissions=True,
                    include_service_links=True,
                    include_versions=False,
                    include_embed_policy=False,
                )
            except Exception:
                pass

            public_id = _project_public_id_for_project(project)

            return {
                "id": getattr(project, "id", None),
                "project_id": getattr(project, "id", None),
                "public_id": public_id,
                "name": getattr(project, "name", None) or "Projekt",
                "description": getattr(project, "description", None) or "",
                "address_text": getattr(project, "address_text", None) or "",
                "conversation_id": getattr(project, "conversation_id", chat_id),
                "setup_status": getattr(project, "setup_status", "draft"),
                "is_configured": bool(getattr(project, "is_configured", False)),
                "url": _project_public_url(public_id),
                "href": _project_public_url(public_id),
                "paths": {
                    "projectPagePath": f"/ui/project/{_safe_quote(public_id)}/project" if public_id else "/ui/project/new",
                    "projectUrl": f"/ui/project/{_safe_quote(public_id)}/project" if public_id else "/ui/project/new",
                    "projectPublicUrl": _project_public_url(public_id),
                },
            }

        return {
            "isNew": True,
            "is_new": True,
            "id": None,
            "project_id": None,
            "public_id": "new",
            "name": "",
            "display_name": "Neues Projekt",
            "description": "",
            "conversation_id": chat_id,
            "setup_status": "draft",
            "is_configured": False,
            "url": "/project=new",
            "href": "/project=new",
            "paths": {
                "projectPagePath": "/ui/project/new",
                "projectUrl": "/ui/project/new",
                "projectPublicUrl": "/project=new",
            },
            "access": {
                "role": "owner",
                "permissions": {
                    "view": True,
                    "edit": True,
                    "manage": True,
                    "delete": True,
                    "transfer": True,
                    "embed": True,
                },
                "can_view": True,
                "can_edit": True,
                "can_manage": True,
                "can_delete": True,
                "can_transfer": True,
                "can_embed": True,
                "is_owner": True,
                "source": "legacy_chat_shell",
            },
        }

    except Exception:
        return {
            "isNew": True,
            "is_new": True,
            "public_id": "new",
            "name": "",
            "display_name": "Neues Projekt",
            "conversation_id": chat_id,
            "setup_status": "draft",
            "is_configured": False,
            "url": "/project=new",
            "href": "/project=new",
            "paths": {
                "projectPagePath": "/ui/project/new",
                "projectUrl": "/ui/project/new",
                "projectPublicUrl": "/project=new",
            },
        }


def _project_sidebar_context_for_chat(chat_id: str, project_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        payload = project_payload if isinstance(project_payload, dict) else {}
        user_id = _get_current_user_id_safe()
        items: List[Dict[str, Any]] = []

        try:
            from services.project_service import list_project_sidebar_items

            items = list_project_sidebar_items(user_id=user_id, include_public=True, limit=200)
        except Exception:
            items = []

        project_public_id = _safe_str(
            payload.get("public_id") or payload.get("publicId") or payload.get("project_public_id"),
            "new",
            160,
        )

        project_name = _safe_str(
            payload.get("name") or payload.get("display_name") or payload.get("displayName"),
            "Neues Projekt",
            200,
        )

        project_subtitle = _safe_str(
            payload.get("address_text") or payload.get("setup_status"),
            "Projekt definieren",
            240,
        )

        return {
            "enabled": True,
            "currentChatId": chat_id,
            "currentProjectId": project_public_id,
            "current_project_id": project_public_id,
            "currentTitle": project_name,
            "currentSubtitle": project_subtitle,
            "defaultCollapsed": False,
            "defaultWidth": 280,
            "minWidth": 220,
            "maxWidth": 420,
            "collapsedWidth": 64,
            "storageKey": "vectoplan.projectSidebar.v1",
            "routeBase": "/",
            "apiPath": "/v1/projects/sidebar",
            "createProjectUrl": "/project=new",
            "items": items,
        }

    except Exception:
        return {
            "enabled": True,
            "currentChatId": chat_id,
            "currentProjectId": "new",
            "currentTitle": "Neues Projekt",
            "currentSubtitle": "Projekt definieren",
            "storageKey": "vectoplan.projectSidebar.v1",
            "routeBase": "/",
            "apiPath": "/v1/projects/sidebar",
            "createProjectUrl": "/project=new",
            "items": [],
        }


# ─────────────────────────────────────────────────────────────
# Workspace URL helpers
# ─────────────────────────────────────────────────────────────

def _editor_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/editor"
    return _url_for_safe("ui_editor.editor_iframe", fallback, chat_id=chat_id)


def _map_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/map"
    return _url_for_safe("ui_map.map_page", fallback, chat_id=chat_id)


def _viewer_json_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/viewer.json"
    return _url_for_safe("ui_chat.viewer_json", fallback, chat_id=chat_id)


def _versions_json_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/versions.json"
    return _url_for_safe("ui_chat.versions_json", fallback, chat_id=chat_id)


def _upload_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/upload"
    return _url_for_safe("ui_chat.ui_upload", fallback, chat_id=chat_id)


def _cad2d_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/cad2d"
    return _url_for_safe("ui_2dviewer.cad2d_page", fallback, chat_id=chat_id)


def _plan2d_json_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/plan2d.json"
    return _url_for_safe("ui_2dviewer.plan2d_json", fallback, chat_id=chat_id)


def _cad_embed_json_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/ui/chat/{chat_id_safe}/cad-embed.json"
    return _url_for_safe("ui_2dviewer.cad_embed_json", fallback, chat_id=chat_id)


def _state_selection_url_for_chat(chat_id: str) -> str:
    chat_id_safe = _safe_quote(chat_id)
    fallback = f"/v1/chats/{chat_id_safe}/viewer/selection"
    return _url_for_safe("viewer_selection.viewer_selection", fallback, chat_id=chat_id)


def _workspace_context_for_chat(chat_id: str) -> Dict[str, Any]:
    try:
        editor_url = _editor_url_for_chat(chat_id)
        map_url = _map_url_for_chat(chat_id)
        project_payload = _project_payload_for_chat(chat_id)

        project_public_id = _safe_str(project_payload.get("public_id"), "new", 160)
        project_page_path = "/ui/project/new" if project_public_id == "new" else f"/ui/project/{_safe_quote(project_public_id)}/project"
        project_public_url = _project_public_url(project_public_id)

        return {
            "chat_id": str(chat_id),
            "project_public_id": project_public_id,
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": project_page_path,
            "project_public_url": project_public_url,
            "project_configured": _safe_bool(project_payload.get("is_configured"), False),
            "editor_url": editor_url,
            "viewer_url": editor_url,
            "map_url": map_url,
            "paths": {
                "projectPagePath": project_page_path,
                "projectUrl": project_page_path,
                "projectPublicUrl": project_public_url,
                "editorPagePath": editor_url,
                "initialEditorUrl": editor_url,
                "viewerJsonPath": _viewer_json_url_for_chat(chat_id),
                "versionsPath": _versions_json_url_for_chat(chat_id),
                "mapPagePath": map_url,
                "plan2dJsonPath": _plan2d_json_url_for_chat(chat_id),
                "cad2dPagePath": _cad2d_url_for_chat(chat_id),
                "cadEmbedJsonPath": _cad_embed_json_url_for_chat(chat_id),
                "adminPagePath": _url_for_safe(
                    "ui_chat.admin_page",
                    f"/ui/chat/{_safe_quote(chat_id)}/admin",
                    chat_id=chat_id,
                ),
                "lvPagePath": _url_for_safe(
                    "ui_chat.lv_page",
                    f"/ui/chat/{_safe_quote(chat_id)}/lv",
                    chat_id=chat_id,
                ),
                "uploadPath": _upload_url_for_chat(chat_id),
                "stateGetPath": _state_selection_url_for_chat(chat_id),
                "statePutPath": _state_selection_url_for_chat(chat_id),
            },
        }

    except Exception:
        chat_id_safe = _safe_quote(chat_id)
        editor_url = f"/ui/chat/{chat_id_safe}/editor"
        map_url = f"/ui/chat/{chat_id_safe}/map"

        return {
            "chat_id": str(chat_id),
            "project_public_id": "new",
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": "/ui/project/new",
            "project_public_url": "/project=new",
            "project_configured": False,
            "editor_url": editor_url,
            "viewer_url": editor_url,
            "map_url": map_url,
            "paths": {
                "projectPagePath": "/ui/project/new",
                "projectUrl": "/ui/project/new",
                "projectPublicUrl": "/project=new",
                "editorPagePath": editor_url,
                "initialEditorUrl": editor_url,
                "viewerJsonPath": f"/ui/chat/{chat_id_safe}/viewer.json",
                "versionsPath": f"/ui/chat/{chat_id_safe}/versions.json",
                "mapPagePath": map_url,
                "plan2dJsonPath": f"/ui/chat/{chat_id_safe}/plan2d.json",
                "cad2dPagePath": f"/ui/chat/{chat_id_safe}/cad2d",
                "cadEmbedJsonPath": f"/ui/chat/{chat_id_safe}/cad-embed.json",
                "adminPagePath": f"/ui/chat/{chat_id_safe}/admin",
                "lvPagePath": f"/ui/chat/{chat_id_safe}/lv",
                "uploadPath": f"/ui/chat/{chat_id_safe}/upload",
                "stateGetPath": f"/v1/chats/{chat_id_safe}/viewer/selection",
                "statePutPath": f"/v1/chats/{chat_id_safe}/viewer/selection",
            },
        }


# ─────────────────────────────────────────────────────────────
# Version / upload helpers
# ─────────────────────────────────────────────────────────────

def _record_file_version_safe(
    *,
    conv: Conversation,
    kind: str,
    label: str,
    blob: Optional[Blob],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from versioning import prune, record_version

        clean_meta: Dict[str, Any] = {
            "source": "vectoplan-app",
            "source_service": "vectoplan-app",
            "source_kind": "ui_upload",
            "storage": "blob",
            "stored_only": True,
            "legacy_speckle": False,
            "legacy_3d_backend": False,
        }

        if meta:
            clean_meta.update(meta)

        if blob:
            clean_meta.update(
                {
                    "filename": blob.filename,
                    "mime": blob.mime,
                    "size": blob.size,
                    "sha256": blob.sha256,
                    "blob_id": blob.id,
                }
            )

        try:
            record_version(
                conversation_id=conv.id,
                kind=kind,
                label=label,
                source_message_id=None,
                input_blob_id=blob.id if blob else None,
                blob_id=blob.id if blob else None,
                artifact_ref={
                    "type": "blob",
                    "blob_id": blob.id if blob else None,
                }
                if blob
                else None,
                status="stored",
                meta=clean_meta,
            )

        except TypeError:
            try:
                record_version(
                    conversation_id=conv.id,
                    kind=kind,
                    label=label,
                    source_message_id=None,
                    input_blob_id=blob.id if blob else None,
                    status="stored",
                    meta=clean_meta,
                )
            except TypeError:
                record_version(
                    conversation_id=conv.id,
                    kind=kind,
                    label=label,
                    source_message_id=None,
                    input_blob_id=blob.id if blob else None,
                    speckle_project_id=None,
                    speckle_model_id=None,
                    speckle_version_id=None,
                    status="stored",
                    meta=clean_meta,
                )

        keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", 10)) or 10

        try:
            prune(conversation_id=conv.id, kind=kind, keep=keep)
        except Exception:
            _log_warning("version prune failed", exc_info=True)

    except Exception:
        _log_warning("versioning not available or failed", exc_info=True)


def _has_start_card(conv: Conversation) -> bool:
    try:
        for item in list(conv.transcript or []):
            if not isinstance(item, dict):
                continue

            meta = item.get("meta") or {}

            if meta.get("type") == "card" and str(meta.get("template") or "") == "project_welcome":
                return True

        return False

    except Exception:
        return False


def _post_start_card_if_missing(conv: Conversation) -> None:
    try:
        if _has_start_card(conv):
            return

        payload = {
            "wfs_url": current_app.config.get("PROJECT_WELCOME_WFS_URL", ""),
            "layer": current_app.config.get("PROJECT_WELCOME_LAYER", ""),
            "hint": current_app.config.get(
                "PROJECT_WELCOME_HINT",
                (
                    "Der AI-Chat dient zur Vereinfachung der Bedienung unserer "
                    "tausenden Möglichkeiten, Daten auszuwerten oder Dinge zu "
                    "erzeugen."
                ),
            ),
        }

        msg.post_card_message(
            conversation=conv,
            template_key="project_welcome",
            payload=payload,
            role="service",
            trace=["system"],
            validate=False,
        )

        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()

    except Exception:
        _log_warning("post_start_card failed", exc_info=True)


def _uploads_disabled() -> bool:
    try:
        return bool(current_app.config.get("VIEW_ONLY_MODE")) or bool(
            current_app.config.get("DISABLE_UI_UPLOADS")
        )
    except Exception:
        return True


def _kind_for_ext(ext: str) -> str:
    if ext == ".dxf":
        return "BPA_DXF"
    if ext == ".ifc":
        return "BGA_IFC"
    if ext in {".obj", ".stl", ".gltf", ".glb"}:
        return "BGA_MESH"
    return "FILE"


def _collect_uploaded_files():
    try:
        if "files" in request.files:
            return request.files.getlist("files")
        if "file" in request.files:
            return [request.files["file"]]
        return []
    except Exception:
        return []


def _create_blob_from_upload(file_storage) -> Blob:
    filename = _safe_filename(getattr(file_storage, "filename", None), "upload")
    mimetype = getattr(file_storage, "mimetype", None) or "application/octet-stream"

    data = file_storage.read()

    if not data:
        raise ValueError("empty file")

    sha256 = ""

    try:
        sha256 = hashlib.sha256(data).hexdigest()
    except Exception:
        sha256 = ""

    blob = Blob(
        filename=filename,
        mime=mimetype,
        size=len(data),
        sha256=sha256,
        data=data,
    )

    db.session.add(blob)
    db.session.flush()

    return blob


def _get_or_create_conversation(chat_id: Optional[str]) -> Optional[Conversation]:
    try:
        conv = Conversation.query.get(chat_id) if chat_id else None

        if conv is not None:
            return conv

        conv = Conversation()
        db.session.add(conv)
        db.session.commit()

        return conv

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        _log_exception("create conversation failed")
        return None


# ─────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────

@bp.get("/ui")
def ui_root():
    return redirect(_project_root_url(), code=302)


@bp.get("/ui/chat")
def chat_page():
    chat_id = request.args.get("chat_id")
    conv = Conversation.query.get(chat_id) if chat_id else None

    if conv is None:
        conv = _get_or_create_conversation(chat_id)

        if conv is None:
            return redirect(url_for("ui_chat.chat_page"), code=302)

        _post_start_card_if_missing(conv)
        return redirect(url_for("ui_chat.chat_page", chat_id=conv.id), code=302)

    try:
        resp = make_response(render_template("chat.html", chat_id=conv.id))
        return _finalize_html_response(resp, no_store=False)
    except Exception as ex:
        _log_exception("render chat.html failed", ex)
        return _json_error(f"chat page render failed: {ex}", 500, code="render_failed")


@bp.get("/ui/chat-3d")
def chat_viewer_page():
    """
    Legacy entry for the old chat/workspace shell.

    New behavior:
    - default redirects to the project-first shell:
        /project=<project_public_id> if chat_id is linked to a project
        / otherwise
    - diagnostic legacy rendering is still available with ?legacy_shell=1.
    """
    chat_id = request.args.get("chat_id")

    if not _legacy_shell_enabled():
        return redirect(_project_redirect_target_for_chat(chat_id), code=302)

    conv = Conversation.query.get(chat_id) if chat_id else None

    if conv is None:
        conv = _get_or_create_conversation(chat_id)

        if conv is None:
            return redirect(_project_root_url(), code=302)

        _post_start_card_if_missing(conv)
        return redirect(url_for("ui_chat.chat_viewer_page", chat_id=conv.id, legacy_shell=1), code=302)

    try:
        workspace = _workspace_context_for_chat(conv.id)
        project_payload = _project_payload_for_chat(conv.id)
        project_sidebar = _project_sidebar_context_for_chat(conv.id, project_payload)

        editor_url = workspace["editor_url"]
        map_url = workspace["map_url"]
        project_url = workspace.get("project_url") or workspace.get("paths", {}).get("projectPagePath") or "/ui/project/new"

        resp = make_response(
            render_template(
                "chat_viewer.html",
                chat_id=conv.id,
                viewer_url=project_url,
                editor_url=editor_url,
                initial_editor_url=editor_url,
                map_url=map_url,
                initial_map_url=map_url,
                default_mode="project",
                workspace_mode="project",
                workspace=workspace,
                workspace_paths=workspace.get("paths", {}),
                project=project_payload,
                current_project=project_payload,
                current_user=_current_user_context_safe(),
                project_sidebar=project_sidebar,
            )
        )

        return _finalize_html_response(resp, no_store=False, workspace_shell=True)

    except Exception as ex:
        _log_exception("render chat_viewer.html failed", ex)
        return _json_error(f"chat viewer render failed: {ex}", 500, code="render_failed")


@bp.get("/ui/chat/<chat_id>/lv")
def lv_page(chat_id: str):
    try:
        conv = Conversation.query.get(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="not_found")

        resp = make_response(render_template("viewer/lv.html", chat_id=conv.id))
        return _finalize_html_response(resp, no_store=False)

    except Exception as ex:
        _log_exception("render lv.html failed", ex)
        return _json_error(f"lv render failed: {ex}", 500, code="render_failed")


@bp.get("/ui/chat/<chat_id>/admin")
def admin_page(chat_id: str):
    try:
        conv = Conversation.query.get(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="not_found")

        resp = make_response(render_template("viewer/admin.html", chat_id=conv.id))
        return _finalize_html_response(resp, no_store=False)

    except Exception as ex:
        _log_exception("render admin.html failed", ex)
        return _json_error(f"admin render failed: {ex}", 500, code="render_failed")


# ─────────────────────────────────────────────────────────────
# UI JSON helpers
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/chat/<chat_id>/viewer.json")
def viewer_json(chat_id: str):
    conv = Conversation.query.get(chat_id)

    if not conv:
        return _json_error("not found", 404, code="not_found")

    try:
        workspace = _workspace_context_for_chat(conv.id)
        project_payload = _project_payload_for_chat(conv.id)

        editor_url = workspace["editor_url"]
        map_url = workspace["map_url"]
        project_url = workspace.get("project_url") or workspace.get("paths", {}).get("projectPagePath") or "/ui/project/new"

        payload = {
            "ok": True,
            "chat_id": conv.id,
            "project": project_payload,
            "mode": "project",
            "workspace_mode": "project",
            "default_mode": "project",
            "project_url": project_url,
            "project_public_url": workspace.get("project_public_url") or "/project=new",
            "viewer_url": project_url,
            "raw_viewer_url": project_url,
            "editor_url": editor_url,
            "map_url": map_url,
            "initial_editor_url": editor_url,
            "initial_map_url": map_url,
            "paths": workspace.get("paths", {}),
            "viewer_selection": {
                "mode": "project",
                "workspace_mode": "project",
                "legacy_3d_backend": False,
            },
            "services": {
                "editor": {
                    "iframe_path": editor_url,
                    "public_url": current_app.config.get("VECTOPLAN_EDITOR_PUBLIC_URL", ""),
                    "route": current_app.config.get("VECTOPLAN_EDITOR_ROUTE", "/editor"),
                    "embed_enabled": bool(current_app.config.get("VECTOPLAN_EDITOR_EMBED_ENABLED", True)),
                },
                "openlayer": {
                    "iframe_path": map_url,
                    "public_url": current_app.config.get("OPENLAYER_PUBLIC_URL", ""),
                    "route": current_app.config.get("OPENLAYER_ROUTE", "/map"),
                    "embed_enabled": bool(current_app.config.get("OPENLAYER_EMBED_ENABLED", True)),
                },
            },
            "legacy_speckle": False,
            "legacy_3d_backend": False,
            "project_first": True,
        }

        resp = jsonify(payload)
        _finalize_json_response(resp, no_store=True)
        return resp, 200

    except Exception as ex:
        _log_exception("viewer_json failed", ex)
        return _json_error(str(ex), 500, code="viewer_json_failed")


@bp.get("/ui/chat/<chat_id>/versions.json")
def versions_json(chat_id: str):
    conv = Conversation.query.get(chat_id)

    if not conv:
        return _json_error("not found", 404, code="not_found")

    try:
        from versioning import list_versions_by_conversation
    except Exception:
        resp = jsonify({"items": [], "total": 0, "legacy_speckle": False, "legacy_3d_backend": False})
        _finalize_json_response(resp, no_store=True)
        return resp, 200

    try:
        kind = request.args.get("kind") or None
        items = list_versions_by_conversation(conversation_id=conv.id, kind=kind) or []

        resp = jsonify(
            {
                "items": items,
                "total": len(items),
                "legacy_speckle": False,
                "legacy_3d_backend": False,
            }
        )
        _finalize_json_response(resp, no_store=True)
        return resp, 200

    except Exception as ex:
        _log_exception("versions_json failed", ex)
        return _json_error(str(ex), 500, code="versions_json_failed")


@bp.get("/ui/templates.json")
def templates_json():
    try:
        items = msg.list_templates() or []
        slim = []

        for item in items:
            try:
                key = item.get("key")

                if str(key or "") == "speckle_viewer":
                    continue

                renderer = item.get("renderer") or "InfoCard"

                if str(renderer or "") == "SpeckleViewerCard":
                    continue

                slim.append(
                    {
                        "key": key,
                        "renderer": renderer,
                        "title": item.get("title") or key,
                        "version": int(item.get("version") or 1),
                    }
                )
            except Exception:
                continue

        resp = jsonify({"items": slim, "total": len(slim), "legacy_speckle": False, "legacy_3d_backend": False})
        _finalize_json_response(resp, no_store=True)
        return resp, 200

    except Exception as ex:
        _log_exception("templates_json failed", ex)
        return _json_error(str(ex), 500, code="templates_json_failed")


# ─────────────────────────────────────────────────────────────
# UI upload
# ─────────────────────────────────────────────────────────────

@bp.post("/ui/chat/<chat_id>/upload")
def ui_upload(chat_id: str):
    if _uploads_disabled():
        payload = {
            "ok": False,
            "error": "uploads disabled",
            "code": "uploads_disabled",
            "view_only": bool(current_app.config.get("VIEW_ONLY_MODE")),
            "legacy_speckle": False,
            "legacy_3d_backend": False,
        }
        resp = jsonify(payload)
        _finalize_json_response(resp, no_store=True)
        return resp, 403

    conv = Conversation.query.get(chat_id)

    if not conv:
        return _json_error("not found", 404, code="not_found")

    files = _collect_uploaded_files()

    if not files:
        return _json_error("no files", 400, code="no_files")

    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for file_storage in files:
        original_filename = getattr(file_storage, "filename", "") or ""

        try:
            filename = _safe_filename(original_filename, "upload")
            ok, ext = _validate_filetype(filename, getattr(file_storage, "mimetype", None))

            if not ok:
                errors.append(
                    {
                        "filename": original_filename,
                        "error": f"unsupported file type: {ext or getattr(file_storage, 'mimetype', '')}",
                    }
                )
                continue

            blob = _create_blob_from_upload(file_storage)
            kind = _kind_for_ext(ext)
            label = os.path.basename(blob.filename or filename or "upload")

            version_meta: Dict[str, Any] = {
                "ext": ext,
                "kind": kind,
                "stored_only": True,
                "legacy_speckle": False,
                "legacy_3d_backend": False,
            }

            _record_file_version_safe(
                conv=conv,
                kind=kind,
                label=label,
                blob=blob,
                meta=version_meta,
            )

            item: Dict[str, Any] = {
                "ok": True,
                "file_id": blob.id,
                "blob_id": blob.id,
                "filename": blob.filename,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
                "ext": ext,
                "kind": kind,
                "stored_only": True,
                "legacy_speckle": False,
                "legacy_3d_backend": False,
                **_file_urls(blob.id),
            }

            if ext == ".dxf":
                try:
                    item["dxf_url"] = url_for(
                        "ui_2dviewer.dxf_blob",
                        chat_id=conv.id,
                        blob_id=blob.id,
                    )
                except Exception:
                    item["dxf_url"] = ""
            else:
                item["editor_url"] = _editor_url_for_chat(conv.id)

            items.append(item)

        except ValueError as ex:
            errors.append(
                {
                    "filename": original_filename,
                    "error": str(ex),
                }
            )

        except Exception as ex:
            _log_exception(f"ui_upload failed for {original_filename}", ex)
            errors.append(
                {
                    "filename": original_filename,
                    "error": str(ex),
                }
            )

    try:
        db.session.add(conv)
        db.session.commit()
    except Exception:
        db.session.rollback()
        _log_warning("DB commit after ui_upload failed", exc_info=True)

    status = 201 if items and not errors else (207 if items and errors else 422)

    body = {
        "ok": bool(items),
        "status": "ok" if items else "error",
        "chat_id": conv.id,
        "items": items,
        "results": items,
        "errors": errors,
        "total": len(items),
        "legacy_speckle": False,
        "legacy_3d_backend": False,
    }

    resp = jsonify(body)
    _finalize_json_response(resp, no_store=True)
    return resp, status


__all__ = ["bp"]