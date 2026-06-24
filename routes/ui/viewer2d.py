# services/vectoplan-app/routes/ui/viewer2d.py
from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from models import Blob, Conversation


bp = Blueprint("ui_2dviewer", __name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

_DXF_KINDS = {"BPA_DXF", "BPA_PLAN_DXF", "BPA_PLAN", "DXF_UPLOAD"}

_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on", "ja"})
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", "nein"})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")
_SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,511}$")
_SPLIT_RE = re.compile(r"[\s,;]+")

MAX_ID_LENGTH = 180
MAX_QUERY_VALUE_LENGTH = 512

DEFAULT_CAD_INTERNAL_URL = "http://cad:8050"
DEFAULT_CAD_PUBLIC_URL = "http://localhost:8050"
DEFAULT_CAD_EMBED_ROUTE = "/embed"

DOCKER_INTERNAL_CAD_HOSTS = {
    "cad",
    "cadviewer",
    "cad-viewer",
    "vectoplan-cad",
    "vectoplan-2d",
    "server-cad",
}

DEFAULT_ALLOWED_FRAME_PARENTS = [
    "http://localhost:5103",
    "http://127.0.0.1:5103",
]

PROJECT_REF_QUERY_KEYS = {
    "app_project_id",
    "app_project_db_id",
    "app_project_public_id",
    "project_public_id",
    "conversation_id",
    "chat_id",
    "chunk_project_id",
    "chunk_world_id",
    "world_id",
    "plan2d_id",
    "lv_id",
    "readonly",
}


# ─────────────────────────────────────────────────────────────
# Generic helpers
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


def _safe_str(value: Any, default: str = "", max_len: int = 500) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in _TRUE_VALUES:
            return True

        if text in _FALSE_VALUES:
            return False

        return default

    except Exception:
        return default


def _safe_float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None

        parsed = float(value)

        if not (-10_000_000.0 <= parsed <= 10_000_000.0):
            return None

        return parsed

    except Exception:
        return None


def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def _is_dxf_name(name: str | None) -> bool:
    try:
        return _ext_of(name or "") == ".dxf"
    except Exception:
        return False


def _is_local_path(value: str | None) -> bool:
    """
    Same-origin path only:
    - starts with /
    - not //
    - no http/https scheme
    """
    try:
        url = str(value or "").strip()

        if not url:
            return False

        if url.startswith("//"):
            return False

        if url.startswith("http://") or url.startswith("https://"):
            return False

        return url.startswith("/")

    except Exception:
        return False


def _sanitize_id(value: Any, *, label: str = "id") -> str:
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"{label} fehlt.")

    if len(text) > MAX_ID_LENGTH:
        raise ValueError(f"{label} ist zu lang.")

    if not _SAFE_ID_RE.match(text):
        raise ValueError(f"{label} enthält ungültige Zeichen.")

    return text


def _safe_optional_id(value: Any) -> str:
    try:
        text = str(value if value is not None else "").strip()

        if not text:
            return ""

        return _sanitize_id(text, label="id")

    except Exception:
        return ""


def _sanitize_query_value(key: str, value: Any) -> str:
    try:
        text = str(value or "").strip()

        if not text:
            return ""

        if len(text) > MAX_QUERY_VALUE_LENGTH:
            text = text[:MAX_QUERY_VALUE_LENGTH]

        if key in {"readonly", "debug", "embed"}:
            return "1" if text.lower() in _TRUE_VALUES else "0"

        if key in PROJECT_REF_QUERY_KEYS:
            if not _SAFE_QUERY_RE.match(text):
                return ""
            return text

        return text

    except Exception:
        return ""


def _split_text_list(raw: str, fallback: str = "") -> List[str]:
    try:
        text = str(raw or "").strip()

        if not text:
            text = str(fallback or "").strip()

        if not text:
            return []

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                result: List[str] = []
                for item in parsed:
                    item_text = str(item or "").strip()
                    if item_text and item_text not in result:
                        result.append(item_text)
                return result
        except Exception:
            pass

        result = []
        for part in _SPLIT_RE.split(text):
            item_text = str(part or "").strip()
            if item_text and item_text not in result:
                result.append(item_text)

        return result

    except Exception:
        return []


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
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in _TRUE_VALUES:
            return True

        if text in _FALSE_VALUES:
            return False

        return default

    except Exception:
        return default


def _cfg_cad_internal_base() -> str:
    """
    Server-to-server CAD URL.

    This may use Docker-internal hostnames.
    It is never sent to the browser as iframe src.
    """
    try:
        value = _cfg_first(
            [
                "CADVIEWER_BASE_URL",
                "CADVIEWER_INTERNAL_URL",
                "CAD_INTERNAL_URL",
                "VECTOPLAN_CAD_INTERNAL_URL",
            ],
            DEFAULT_CAD_INTERNAL_URL,
        )
        return str(value or DEFAULT_CAD_INTERNAL_URL).strip().rstrip("/")
    except Exception:
        return DEFAULT_CAD_INTERNAL_URL


def _is_docker_internal_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = str(parsed.hostname or "").strip().lower()
        return host in DOCKER_INTERNAL_CAD_HOSTS
    except Exception:
        return False


def _cfg_cad_public_base() -> str:
    """
    Browser-facing CAD URL.

    This must be reachable by the browser. It must not be a Docker-internal host.
    """
    try:
        value = _cfg_first(
            [
                "CADVIEWER_PUBLIC_URL",
                "CAD_PUBLIC_URL",
                "VECTOPLAN_CAD_PUBLIC_URL",
                "VECTOPLAN_2D_PUBLIC_URL",
            ],
            DEFAULT_CAD_PUBLIC_URL,
        )

        public_url = str(value or DEFAULT_CAD_PUBLIC_URL).strip().rstrip("/")
        parsed = urlsplit(public_url)

        if parsed.scheme not in {"http", "https"}:
            _log_warning("Ignoring CAD public URL with invalid scheme: %s", public_url)
            return DEFAULT_CAD_PUBLIC_URL

        if not parsed.netloc:
            _log_warning("Ignoring CAD public URL without host: %s", public_url)
            return DEFAULT_CAD_PUBLIC_URL

        if _is_docker_internal_url(public_url):
            _log_warning("Ignoring Docker-internal CAD public URL: %s", public_url)
            return DEFAULT_CAD_PUBLIC_URL

        return public_url

    except Exception:
        return DEFAULT_CAD_PUBLIC_URL


def _cfg_cad_embed_route() -> str:
    try:
        route = _cfg_first(
            [
                "CADVIEWER_EMBED_ROUTE",
                "CAD_EMBED_ROUTE",
                "VECTOPLAN_CAD_EMBED_ROUTE",
            ],
            DEFAULT_CAD_EMBED_ROUTE,
        )

        route = str(route or DEFAULT_CAD_EMBED_ROUTE).strip()

        if not route.startswith("/"):
            route = "/" + route

        return route

    except Exception:
        return DEFAULT_CAD_EMBED_ROUTE


# ─────────────────────────────────────────────────────────────
# Response / frame helpers
# ─────────────────────────────────────────────────────────────

def _frame_ancestors_value() -> str:
    try:
        raw = _cfg_first(
            [
                "VECTOPLAN_ALLOWED_FRAME_PARENTS",
                "VECTOPLAN_FRAME_ANCESTORS",
                "FRAME_ANCESTORS",
            ],
            " ".join(DEFAULT_ALLOWED_FRAME_PARENTS),
        )

        ancestors = _split_text_list(raw, " ".join(DEFAULT_ALLOWED_FRAME_PARENTS))

        result = ["'self'"]
        for item in ancestors:
            text = str(item or "").strip()
            if not text:
                continue
            normalized = "'self'" if text in {"self", "'self'"} else text
            if normalized not in result:
                result.append(normalized)

        return " ".join(result)

    except Exception:
        return "'self' http://localhost:5103 http://127.0.0.1:5103"


def _cache_headers(resp: Response, strong: bool = False) -> Response:
    try:
        dev = str(current_app.config.get("FLASK_ENV", "") or "").lower().startswith("dev")

        if dev:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
        else:
            if strong:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=86400"

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


def _frame_headers(resp: Response, *, allow_embed: Optional[bool] = None) -> Response:
    """
    Conservative iframe headers.

    Never emits frame-ancestors *.
    """
    try:
        if allow_embed is None:
            allow_embed = request.args.get("allow_embed") == "1" or request.args.get("embed") == "1"

        if allow_embed:
            resp.headers["Content-Security-Policy"] = f"frame-ancestors {_frame_ancestors_value()}"
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


def _json_response(payload: Mapping[str, Any], status: int = 200) -> Response:
    resp = make_response(jsonify(payload), status)
    _cache_headers(resp, strong=False)
    return resp


def _json_error(message: str, status: int = 400, *, code: str = "error", extra: Optional[Dict[str, Any]] = None) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "status": "error",
        "error": message,
        "code": code,
        "legacy_3d_backend": False,
        "project_first": True,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status)


def _project_locked_html(project_context: Optional[Dict[str, Any]], workspace: str = "2D") -> Response:
    ctx = _project_context_payload(project_context)

    html = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{workspace} · Projekt nicht konfiguriert</title>
  <style>
    html,body{{height:100%;margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#f6f7fb;color:#0b0f1e}}
    main{{min-height:100%;display:grid;place-items:center;padding:24px}}
    section{{max-width:560px;padding:22px;border:1px solid #e1e6ef;border-radius:16px;background:#fff;box-shadow:0 18px 44px rgba(15,23,42,.10)}}
    h1{{margin:0 0 8px;font-size:1.25rem}}
    p{{margin:0;color:#5b647a;line-height:1.5}}
    a{{display:inline-flex;margin-top:16px;color:#2b59ff;text-decoration:none;font-weight:700}}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>{workspace} noch nicht verfügbar</h1>
      <p>Speichere und konfiguriere zuerst das Projekt. Danach werden 2D, Map, 3D und LV freigeschaltet.</p>
      <a href="/ui/project/{quote(str(ctx.get("project_public_id") or "new"), safe="")}/project">Projekt öffnen</a>
    </section>
  </main>
</body>
</html>"""

    resp = make_response(html, 403)
    _cache_headers(resp, strong=False)
    _frame_headers(resp, allow_embed=True)
    return resp


def _project_locked_json(project_context: Optional[Dict[str, Any]], workspace: str = "2d") -> Response:
    return _json_error(
        "Projekt ist noch nicht konfiguriert.",
        403,
        code="project_not_configured",
        extra={
            "workspace": workspace,
            "project": _project_context_payload(project_context),
        },
    )


# ─────────────────────────────────────────────────────────────
# Project context helpers
# ─────────────────────────────────────────────────────────────

def _project_identifier_from_request() -> str:
    try:
        candidates = (
            request.args.get("app_project_id"),
            request.args.get("app_project_public_id"),
            request.args.get("project_public_id"),
            request.args.get("project"),
            request.args.get("project_id"),
            request.args.get("p"),
        )

        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value:
                continue
            return _sanitize_id(value, label="project_identifier")

        return ""

    except Exception:
        return ""


def _resolve_project(project_identifier: Optional[str] = None, chat_id: Optional[str] = None) -> Any:
    try:
        project = None
        identifier = _safe_optional_id(project_identifier)

        if identifier:
            try:
                from services.project_service import resolve_project

                project = resolve_project(identifier)
            except Exception:
                project = None

            if project is None:
                try:
                    from models import Project

                    project = Project.query.filter_by(public_id=identifier).one_or_none()
                    if project is None:
                        project = Project.query.filter_by(id=identifier).one_or_none()
                except Exception:
                    project = None

        if project is None and chat_id:
            try:
                from services.project_service import get_project_by_conversation_id

                project = get_project_by_conversation_id(chat_id)
            except Exception:
                project = None

            if project is None:
                try:
                    from models import Project

                    project = Project.query.filter_by(conversation_id=chat_id).one_or_none()
                except Exception:
                    project = None

        return project

    except Exception:
        return None


def _resolve_project_context(
    *,
    chat_id: Optional[str],
    project_identifier: Optional[str],
) -> Dict[str, Any]:
    """
    Resolve app Project references best-effort.

    Separates:
    - app project references
    - chunk references
    - plan2d/lv references
    """
    context: Dict[str, Any] = {}

    try:
        project = _resolve_project(project_identifier=project_identifier, chat_id=chat_id)
        identifier = _safe_optional_id(project_identifier)

        if project is None:
            if chat_id:
                context["conversation_id"] = _safe_optional_id(chat_id)
            if identifier:
                context["app_project_id"] = identifier
                context["app_project_public_id"] = identifier
                context["project_public_id"] = identifier
                context["is_configured"] = False
            return context

        app_project_db_id = _safe_optional_id(getattr(project, "id", None))
        app_project_public_id = _safe_optional_id(getattr(project, "public_id", None)) or app_project_db_id
        conversation_id = _safe_optional_id(getattr(project, "conversation_id", None)) or _safe_optional_id(chat_id)

        context.update(
            {
                "app_project_db_id": app_project_db_id,
                "app_project_id": app_project_public_id,
                "app_project_public_id": app_project_public_id,
                "project_public_id": app_project_public_id,
                "public_id": app_project_public_id,
                "conversation_id": conversation_id,
                "chunk_project_id": _safe_optional_id(getattr(project, "chunk_project_id", None)),
                "chunk_world_id": _safe_optional_id(getattr(project, "chunk_world_id", None)),
                "plan2d_id": _safe_optional_id(getattr(project, "plan2d_id", None)),
                "lv_id": _safe_optional_id(getattr(project, "lv_id", None)),
                "name": _safe_str(getattr(project, "name", ""), "", 240),
                "address_text": _safe_str(getattr(project, "address_text", ""), "", 500),
                "setup_status": _safe_str(getattr(project, "setup_status", "draft"), "draft", 80),
                "is_configured": bool(getattr(project, "is_configured", False)),
                "latitude": _safe_float_or_none(getattr(project, "latitude", None)),
                "longitude": _safe_float_or_none(getattr(project, "longitude", None)),
            }
        )

        try:
            from services.current_user import get_current_user_id
            from services.project_permissions import can_edit_project, can_view_project

            user_id = get_current_user_id()
            context["can_view"] = bool(can_view_project(project, user_id=user_id))
            context["readonly"] = not bool(can_edit_project(project, user_id=user_id))
        except Exception:
            context["can_view"] = True
            context["readonly"] = False

        return context

    except Exception as exc:
        _log_warning("Project context resolution failed: %s", exc.__class__.__name__)
        if chat_id:
            context["conversation_id"] = _safe_optional_id(chat_id)
        return context


def _project_context_payload(project_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        ctx = project_context if isinstance(project_context, dict) else {}

        return {
            "app_project_id": ctx.get("app_project_id") or ctx.get("app_project_public_id") or "",
            "app_project_db_id": ctx.get("app_project_db_id") or "",
            "project_public_id": ctx.get("project_public_id") or "",
            "conversation_id": ctx.get("conversation_id") or "",
            "chunk_project_id": ctx.get("chunk_project_id") or "",
            "chunk_world_id": ctx.get("chunk_world_id") or "",
            "plan2d_id": ctx.get("plan2d_id") or "",
            "lv_id": ctx.get("lv_id") or "",
            "name": ctx.get("name") or "",
            "address_text": ctx.get("address_text") or "",
            "setup_status": ctx.get("setup_status") or "draft",
            "latitude": ctx.get("latitude"),
            "longitude": ctx.get("longitude"),
            "is_configured": bool(ctx.get("is_configured")),
            "can_view": bool(ctx.get("can_view", True)),
            "readonly": bool(ctx.get("readonly")),
        }

    except Exception:
        return {}


def _context_has_project(project_context: Optional[Dict[str, Any]]) -> bool:
    try:
        ctx = project_context if isinstance(project_context, dict) else {}
        return bool(
            ctx.get("app_project_id")
            or ctx.get("app_project_public_id")
            or ctx.get("project_public_id")
            or ctx.get("app_project_db_id")
        )
    except Exception:
        return False


def _project_tools_allowed(project_context: Optional[Dict[str, Any]]) -> bool:
    try:
        require_configured = _cfg_bool("VECTOPLAN_REQUIRE_CONFIGURED_PROJECT_FOR_2D", True)

        if not require_configured:
            return True

        if not _context_has_project(project_context):
            return True

        ctx = project_context if isinstance(project_context, dict) else {}
        return bool(ctx.get("is_configured"))

    except Exception:
        return False


def _get_or_create_project_conversation(project_identifier: str) -> Optional[Conversation]:
    try:
        project = _resolve_project(project_identifier=project_identifier, chat_id=None)

        if project is None:
            return None

        try:
            from services.project_service import get_or_create_project_conversation

            conv = get_or_create_project_conversation(project, commit=True)
            if conv is not None:
                return conv
        except Exception:
            pass

        conversation_id = _safe_optional_id(getattr(project, "conversation_id", None))
        if conversation_id:
            return _ensure_conv(conversation_id)

        return None

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Conversation / version helpers
# ─────────────────────────────────────────────────────────────

def _ensure_conv(chat_id: Optional[str]) -> Conversation:
    try:
        if chat_id:
            conv = Conversation.query.get(str(chat_id))
            if conv:
                return conv

        conv = Conversation()

        from extensions import db

        db.session.add(conv)
        db.session.commit()
        return conv

    except Exception:
        try:
            from extensions import db

            db.session.rollback()
        except Exception:
            pass

        _log_exception("create conversation failed")
        abort(404, description="conversation create failed")


def _find_latest_dxf_version(conv_id: str) -> Optional[Dict[str, Any]]:
    try:
        from versioning import list_versions_by_conversation
    except Exception:
        return None

    try:
        items: List[Dict[str, Any]] = list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
    except Exception:
        return None

    def is_candidate(version: Dict[str, Any]) -> bool:
        if str(version.get("kind") or "") in _DXF_KINDS:
            return True

        meta = version.get("meta") or {}

        if _is_dxf_name(meta.get("filename")):
            return True

        if _is_dxf_name(version.get("label")):
            return True

        return False

    candidates = [item for item in items if is_candidate(item)]
    if not candidates:
        return None

    def ts(version: Dict[str, Any]) -> Any:
        return version.get("created_at") or version.get("created") or version.get("ts") or 0

    candidates.sort(key=ts, reverse=True)

    version = candidates[0]
    meta = version.get("meta") or {}

    return {
        "version": version,
        "blob_id": version.get("input_blob_id") or version.get("blob_id"),
        "url": meta.get("dxf_url") or meta.get("url") or "",
        "meta": meta,
        "label": version.get("label") or meta.get("filename") or "plan.dxf",
        "rev": version.get("id") or version.get("version_id") or meta.get("rev") or 0,
        "created_at": ts(version),
    }


def _conversation_has_blob(conv_id: str, blob_id: int) -> bool:
    try:
        from versioning import list_versions_by_conversation

        items = list_versions_by_conversation(conversation_id=conv_id, kind=None) or []

        for version in items:
            if int(version.get("input_blob_id") or 0) == int(blob_id):
                return True
            if int(version.get("blob_id") or 0) == int(blob_id):
                return True

    except Exception:
        pass

    return False


def _fallback_plan2d_url(conv_id: Optional[str], project_context: Optional[Dict[str, Any]]) -> str:
    try:
        tmpl = current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE", "")
        if not tmpl:
            return ""

        ctx = _project_context_payload(project_context)

        return tmpl.format(
            chat_id=conv_id or "",
            conversation_id=conv_id or "",
            project_id=ctx.get("app_project_id") or "",
            project_public_id=ctx.get("project_public_id") or "",
            plan2d_id=ctx.get("plan2d_id") or "",
        )
    except Exception:
        return ""


def _dxf_url_for_blob(conv_id: str, blob_id: Any) -> str:
    try:
        return url_for("ui_2dviewer.dxf_blob", chat_id=conv_id, blob_id=int(blob_id))
    except Exception:
        try:
            return f"/ui/chat/{conv_id}/file/{int(blob_id)}.dxf"
        except Exception:
            return ""


def _build_plan2d_payload(conv_id: Optional[str], project_context: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    ctx_payload = _project_context_payload(project_context)

    src = "unknown"
    label = "plan.dxf"
    dxf_url = ""
    rev = 0
    updated = None
    blob_id = None

    view_only = bool(current_app.config.get("VIEW_ONLY_MODE"))
    allow_cdn = bool(current_app.config.get("ALLOW_CDN"))

    info = _find_latest_dxf_version(conv_id) if conv_id else None

    if view_only:
        fallback = _fallback_plan2d_url(conv_id, project_context)
        if fallback:
            dxf_url = fallback
            src = "config-template"
        elif info:
            label = info.get("label") or label
            rev = int(info.get("rev") or 0)
            updated = info.get("created_at")
            blob_id = info.get("blob_id")

            if info.get("url"):
                dxf_url = info["url"]
                src = "version-meta-url"
            elif blob_id and conv_id:
                dxf_url = _dxf_url_for_blob(conv_id, blob_id)
                src = "version-blob"

    if not view_only and not dxf_url and info:
        label = info.get("label") or label
        rev = int(info.get("rev") or 0)
        updated = info.get("created_at")
        blob_id = info.get("blob_id")

        if info.get("url"):
            dxf_url = info["url"]
            src = "version-meta-url"
        elif blob_id and conv_id:
            dxf_url = _dxf_url_for_blob(conv_id, blob_id)
            src = "version-blob"

    if not dxf_url:
        fallback = _fallback_plan2d_url(conv_id, project_context)
        if fallback:
            dxf_url = fallback
            src = "config-template"

    if dxf_url and not _is_local_path(dxf_url) and not allow_cdn:
        return 403, {
            "ok": False,
            "chat_id": conv_id,
            "conversation_id": conv_id,
            "project": ctx_payload,
            "status": "blocked",
            "message": "external DXF URLs are disabled",
            "source": src,
            "legacy_3d_backend": False,
            "project_first": True,
        }

    if not dxf_url:
        return 404, {
            "ok": False,
            "chat_id": conv_id,
            "conversation_id": conv_id,
            "project": ctx_payload,
            "status": "no_plan",
            "message": "no DXF found for conversation/project",
            "plan2d_id": ctx_payload.get("plan2d_id"),
            "legacy_3d_backend": False,
            "project_first": True,
        }

    return 200, {
        "ok": True,
        "chat_id": conv_id,
        "conversation_id": conv_id,
        "project": ctx_payload,
        "dxf_url": dxf_url,
        "title": label,
        "rev": rev,
        "updated_at": updated,
        "source": src,
        "blob_id": blob_id,
        "plan2d_id": ctx_payload.get("plan2d_id"),
        "lv_id": ctx_payload.get("lv_id"),
        "chunk_project_id": ctx_payload.get("chunk_project_id"),
        "chunk_world_id": ctx_payload.get("chunk_world_id"),
        "legacy_3d_backend": False,
        "project_first": True,
    }


# ─────────────────────────────────────────────────────────────
# CAD service helpers
# ─────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: float = 10.0) -> Tuple[bool, Any]:
    try:
        import requests  # type: ignore

        response = requests.get(url, timeout=timeout)
        if response.ok:
            try:
                return True, response.json()
            except Exception:
                return False, None
        return False, None
    except Exception:
        try:
            from urllib.request import Request, urlopen  # type: ignore

            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
                data = resp.read()
                try:
                    return True, json.loads(data.decode("utf-8"))
                except Exception:
                    return False, None
        except Exception:
            return False, None


def _http_post_file(
    url: str,
    field_name: str,
    filename: str,
    data: bytes,
    content_type: str = "application/dxf",
    timeout: float = 30.0,
) -> Tuple[bool, Any]:
    try:
        import requests  # type: ignore

        files = {field_name: (filename, data, content_type)}
        response = requests.post(url, files=files, timeout=timeout)
        if response.ok:
            try:
                return True, response.json()
            except Exception:
                return True, None
        return False, getattr(response, "text", "")
    except Exception:
        try:
            boundary = "----cadFormBoundary7MA4YWxkTrZu0gW"
            body = []
            body.append(f"--{boundary}\r\n".encode())
            body.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode())
            body.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.append(data)
            body.append(f"\r\n--{boundary}--\r\n".encode())
            payload = b"".join(body)

            from urllib.request import Request, urlopen  # type: ignore

            req = Request(url, data=payload, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Content-Length", str(len(payload)))
            with urlopen(req, timeout=timeout) as resp:
                out = resp.read()
                try:
                    return True, json.loads(out.decode("utf-8"))
                except Exception:
                    return True, None
        except Exception:
            return False, None


def _safe_name(value: str) -> str:
    try:
        base = os.path.basename(value or "plan.dxf")
        if not base.lower().endswith(".dxf"):
            base = base + ".dxf"
        base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base)
        return base or "plan.dxf"
    except Exception:
        return "plan.dxf"


def _build_cad_name(context_id: str, label: str, rev: Any) -> str:
    try:
        suffix = f"_r{rev}" if str(rev) else ""
        prefix = _safe_name(context_id or "project").replace(".dxf", "")
        name = f"{prefix}{suffix}_{_safe_name(label)}"
        name = re.sub(r"\.dxf\.dxf$", ".dxf", name, flags=re.IGNORECASE)
        return name
    except Exception:
        return _safe_name(label or "plan.dxf")


def _load_blob_bytes(blob_id: object) -> Optional[bytes]:
    try:
        if blob_id is None:
            return None

        bid = int(str(blob_id))
    except Exception:
        return None

    try:
        blob = Blob.query.get(bid)
        if not blob or not blob.data:
            return None

        return bytes(blob.data) if isinstance(blob.data, (bytes, bytearray)) else bytes(blob.data)
    except Exception:
        return None


def _fetch_local_path_bytes(path: str) -> Optional[bytes]:
    try:
        base = str(current_app.config.get("WEB_INTERNAL_URL") or "").strip()
        if not base:
            return None

        if not _is_local_path(path):
            return None

        url = base.rstrip("/") + path

        try:
            import requests  # type: ignore

            response = requests.get(url, timeout=15)
            if response.ok:
                return response.content
            return None
        except Exception:
            from urllib.request import urlopen  # type: ignore

            with urlopen(url, timeout=15) as resp:
                return resp.read()
    except Exception:
        return None


def _context_query_params(conv_id: Optional[str], project_context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    ctx = _project_context_payload(project_context)

    raw: Dict[str, Any] = {
        "source": "vectoplan-app",
        "chat_id": conv_id or ctx.get("conversation_id") or "",
        "conversation_id": conv_id or ctx.get("conversation_id") or "",
        "app_project_id": ctx.get("app_project_id") or "",
        "app_project_db_id": ctx.get("app_project_db_id") or "",
        "app_project_public_id": ctx.get("project_public_id") or "",
        "project_public_id": ctx.get("project_public_id") or "",
        "chunk_project_id": ctx.get("chunk_project_id") or "",
        "chunk_world_id": ctx.get("chunk_world_id") or "",
        "world_id": ctx.get("chunk_world_id") or "",
        "plan2d_id": ctx.get("plan2d_id") or "",
        "lv_id": ctx.get("lv_id") or "",
        "readonly": "1" if ctx.get("readonly") else "0",
    }

    result: Dict[str, str] = {}

    for key, value in raw.items():
        cleaned = _sanitize_query_value(key, value)
        if cleaned:
            result[key] = cleaned

    return result


def _append_query(url: str, query: Mapping[str, Any]) -> str:
    try:
        parts = urlsplit(url)
        current = dict(parse_qsl(parts.query, keep_blank_values=False))

        for key, value in query.items():
            if value is None or value == "":
                continue
            current[str(key)] = str(value)

        encoded = urlencode(current, doseq=False)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))
    except Exception:
        return url


def _build_cad_embed_url(name: str, conv_id: Optional[str], project_context: Optional[Dict[str, Any]]) -> str:
    public_base = _cfg_cad_public_base().rstrip("/")
    route = _cfg_cad_embed_route()

    base_url = f"{public_base}{route}"
    query = {"file": name}
    query.update(_context_query_params(conv_id, project_context))

    return _append_query(base_url, query)


# ─────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/chat/<chat_id>/cad2d")
def cad2d_page(chat_id: str) -> Response:
    conv = _ensure_conv(chat_id)
    project_context = _resolve_project_context(
        chat_id=str(conv.id),
        project_identifier=_project_identifier_from_request(),
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_html(project_context, workspace="2D")

    try:
        plan2d_url = url_for("ui_2dviewer.plan2d_json", chat_id=conv.id)
    except Exception:
        plan2d_url = f"/ui/chat/{conv.id}/plan2d.json"

    resp = make_response(
        render_template(
            "viewer/cad2d.html",
            chat_id=conv.id,
            plan2d_url=plan2d_url,
            project=_project_context_payload(project_context),
            current_project=_project_context_payload(project_context),
            project_context=project_context,
            project_configured=bool(project_context.get("is_configured")),
            plan2d_id=project_context.get("plan2d_id"),
            lv_id=project_context.get("lv_id"),
            chunk_project_id=project_context.get("chunk_project_id"),
            chunk_world_id=project_context.get("chunk_world_id"),
        )
    )
    _cache_headers(resp, strong=False)
    _frame_headers(resp, allow_embed=True)
    return resp


@bp.get("/ui/project/<project_id>/cad2d")
def project_cad2d_page(project_id: str) -> Response:
    safe_project_id = _sanitize_id(project_id, label="project_id")
    project_context = _resolve_project_context(
        chat_id=None,
        project_identifier=safe_project_id,
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_html(project_context, workspace="2D")

    conv = _get_or_create_project_conversation(safe_project_id)
    conv_id = str(conv.id) if conv is not None else _safe_optional_id(project_context.get("conversation_id"))

    if conv_id:
        plan2d_url = f"/ui/chat/{quote(conv_id, safe='')}/plan2d.json?project_public_id={quote(safe_project_id, safe='')}"
    else:
        plan2d_url = f"/ui/project/{quote(safe_project_id, safe='')}/plan2d.json"

    resp = make_response(
        render_template(
            "viewer/cad2d.html",
            chat_id=conv_id,
            plan2d_url=plan2d_url,
            project=_project_context_payload(project_context),
            current_project=_project_context_payload(project_context),
            project_context=project_context,
            project_configured=bool(project_context.get("is_configured")),
            plan2d_id=project_context.get("plan2d_id"),
            lv_id=project_context.get("lv_id"),
            chunk_project_id=project_context.get("chunk_project_id"),
            chunk_world_id=project_context.get("chunk_world_id"),
        )
    )
    _cache_headers(resp, strong=False)
    _frame_headers(resp, allow_embed=True)
    return resp


@bp.get("/ui/chat/<chat_id>/plan2d.json")
def plan2d_json(chat_id: str) -> Response:
    conv = Conversation.query.get(chat_id)
    if not conv:
        return _json_error("not found", 404, code="not_found")

    project_context = _resolve_project_context(
        chat_id=str(conv.id),
        project_identifier=_project_identifier_from_request(),
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_json(project_context, workspace="2d")

    status, payload = _build_plan2d_payload(str(conv.id), project_context)
    resp = make_response(jsonify(payload), status)
    _cache_headers(resp, strong=False)
    return resp


@bp.get("/ui/project/<project_id>/plan2d.json")
def project_plan2d_json(project_id: str) -> Response:
    safe_project_id = _sanitize_id(project_id, label="project_id")
    project_context = _resolve_project_context(
        chat_id=None,
        project_identifier=safe_project_id,
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_json(project_context, workspace="2d")

    conv = _get_or_create_project_conversation(safe_project_id)
    conv_id = str(conv.id) if conv is not None else _safe_optional_id(project_context.get("conversation_id"))

    status, payload = _build_plan2d_payload(conv_id or None, project_context)
    resp = make_response(jsonify(payload), status)
    _cache_headers(resp, strong=False)
    return resp


@bp.get("/ui/chat/<chat_id>/file/<int:blob_id>.dxf")
def dxf_blob(chat_id: str, blob_id: int) -> Response:
    conv = Conversation.query.get(chat_id)
    if not conv:
        return _json_error("not found", 404, code="not_found")

    if not _conversation_has_blob(conv.id, blob_id):
        return _json_error("forbidden or blob not linked to conversation", 404, code="blob_not_linked")

    blob = Blob.query.get(blob_id)
    if not blob or not blob.data:
        return _json_error("blob not found", 404, code="blob_not_found")

    if not _is_dxf_name(blob.filename):
        return _json_error("blob is not a DXF", 400, code="not_dxf")

    data = BytesIO(blob.data if isinstance(blob.data, (bytes, bytearray)) else bytes(blob.data))
    payload = data.getvalue()

    resp = make_response(payload)
    resp.headers["Content-Type"] = blob.mime or "application/dxf"
    resp.headers["Content-Length"] = str(len(payload))
    resp.headers["Content-Disposition"] = f'inline; filename="{os.path.basename(blob.filename or "plan.dxf")}"'

    if getattr(blob, "sha256", None):
        resp.headers["ETag"] = blob.sha256
        _cache_headers(resp, strong=True)
    else:
        _cache_headers(resp, strong=False)

    _frame_headers(resp, allow_embed=True)
    return resp


@bp.get("/ui/project/<project_id>/file/<int:blob_id>.dxf")
def project_dxf_blob(project_id: str, blob_id: int) -> Response:
    safe_project_id = _sanitize_id(project_id, label="project_id")
    project_context = _resolve_project_context(
        chat_id=None,
        project_identifier=safe_project_id,
    )

    conv_id = _safe_optional_id(project_context.get("conversation_id"))
    if not conv_id:
        return _json_error("project conversation not found", 404, code="conversation_not_found")

    return dxf_blob(conv_id, blob_id)


# ─────────────────────────────────────────────────────────────
# CAD embed
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/chat/<chat_id>/cad-embed.json")
def cad_embed_json(chat_id: str) -> Response:
    conv = Conversation.query.get(chat_id)
    if not conv:
        return _json_error("not found", 404, code="not_found")

    project_context = _resolve_project_context(
        chat_id=str(conv.id),
        project_identifier=_project_identifier_from_request(),
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_json(project_context, workspace="cad2d")

    return _cad_embed_response(str(conv.id), project_context)


@bp.get("/ui/project/<project_id>/cad-embed.json")
def project_cad_embed_json(project_id: str) -> Response:
    safe_project_id = _sanitize_id(project_id, label="project_id")
    project_context = _resolve_project_context(
        chat_id=None,
        project_identifier=safe_project_id,
    )

    if not _project_tools_allowed(project_context):
        return _project_locked_json(project_context, workspace="cad2d")

    conv = _get_or_create_project_conversation(safe_project_id)
    conv_id = str(conv.id) if conv is not None else _safe_optional_id(project_context.get("conversation_id"))

    return _cad_embed_response(conv_id or None, project_context)


def _cad_embed_response(conv_id: Optional[str], project_context: Optional[Dict[str, Any]]) -> Response:
    ctx_payload = _project_context_payload(project_context)

    info = _find_latest_dxf_version(conv_id) if conv_id else None

    if not info:
        fallback = _fallback_plan2d_url(conv_id, project_context)
        if not fallback:
            return _json_response(
                {
                    "ok": False,
                    "chat_id": conv_id,
                    "conversation_id": conv_id,
                    "project": ctx_payload,
                    "status": "no_plan",
                    "plan2d_id": ctx_payload.get("plan2d_id"),
                    "legacy_3d_backend": False,
                    "project_first": True,
                },
                404,
            )

        dxf_url = fallback
        label = "plan.dxf"
        rev = 0
        blob_id = None
    else:
        dxf_url = info.get("url") or ""
        label = info.get("label") or "plan.dxf"
        rev = info.get("rev") or 0
        blob_id = info.get("blob_id")

        if not dxf_url and blob_id and conv_id:
            dxf_url = _dxf_url_for_blob(conv_id, blob_id)

    cad_base_internal = _cfg_cad_internal_base().rstrip("/")
    context_id = ctx_payload.get("project_public_id") or conv_id or "project"
    name = _build_cad_name(context_id, label, rev)
    embed_url = _build_cad_embed_url(name, conv_id, project_context)

    ok, listing = _http_get_json(f"{cad_base_internal}/api/v1/files")
    if ok and isinstance(listing, list) and name in listing:
        return _json_response(
            {
                "ok": True,
                "chat_id": conv_id,
                "conversation_id": conv_id,
                "project": ctx_payload,
                "status": "ok",
                "embed_url": embed_url,
                "iframe_url": embed_url,
                "viewer_url": embed_url,
                "dxf_url": dxf_url,
                "file": name,
                "rev": rev,
                "cad": {
                    "public_url": _cfg_cad_public_base(),
                    "internal_url_configured": bool(_cfg_cad_internal_base()),
                    "passes_app_project_refs": True,
                    "passes_chunk_refs": True,
                    "passes_plan2d_refs": True,
                },
                "legacy_3d_backend": False,
                "project_first": True,
            },
            200,
        )

    data_bytes: Optional[bytes] = None

    if blob_id is not None:
        data_bytes = _load_blob_bytes(blob_id)

    if data_bytes is None and _is_local_path(dxf_url):
        data_bytes = _fetch_local_path_bytes(dxf_url)

    if data_bytes is None:
        return _json_response(
            {
                "ok": False,
                "chat_id": conv_id,
                "conversation_id": conv_id,
                "project": ctx_payload,
                "status": "error",
                "error": "DXF bytes not available for upload",
                "dxf_url": dxf_url,
                "legacy_3d_backend": False,
                "project_first": True,
            },
            500,
        )

    ok, upload_response = _http_post_file(
        f"{cad_base_internal}/api/v1/files",
        "file",
        name,
        data_bytes,
        "application/dxf",
    )

    if not ok:
        return _json_response(
            {
                "ok": False,
                "chat_id": conv_id,
                "conversation_id": conv_id,
                "project": ctx_payload,
                "status": "error",
                "error": "upload to CAD service failed",
                "cad_response": upload_response,
                "legacy_3d_backend": False,
                "project_first": True,
            },
            502,
        )

    return _json_response(
        {
            "ok": True,
            "chat_id": conv_id,
            "conversation_id": conv_id,
            "project": ctx_payload,
            "status": "ok",
            "embed_url": embed_url,
            "iframe_url": embed_url,
            "viewer_url": embed_url,
            "dxf_url": dxf_url,
            "file": name,
            "rev": rev,
            "uploaded": True,
            "cad": {
                "public_url": _cfg_cad_public_base(),
                "internal_url_configured": bool(_cfg_cad_internal_base()),
                "passes_app_project_refs": True,
                "passes_chunk_refs": True,
                "passes_plan2d_refs": True,
            },
            "legacy_3d_backend": False,
            "project_first": True,
        },
        200,
    )


__all__ = ["bp"]