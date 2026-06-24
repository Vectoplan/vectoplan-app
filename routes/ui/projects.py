# services/vectoplan-app/routes/ui/projects.py
from __future__ import annotations

"""
VECTOPLAN project UI routes.

Zweck:
- Projektgeführte UI-Einstiege für vectoplan-app.
- Root-URL http://localhost:5103/ rendert die App-Shell.
- Projekt-URL http://localhost:5103/project=<project_public_id> rendert dieselbe Shell
  mit ausgewähltem Projekt.
- http://localhost:5103/project=new öffnet die Projekterstellung.
- Workspace startet im Modus "Projekt".
- Map/3D/2D/LV werden erst nach Projekt-Konfiguration freigeschaltet.
- Chunk-Kontext wird aus dem App-Projekt in Shell, Workspace und context.json
  durchgereicht.

Wichtig:
- vectoplan-app besitzt hier nur Projekt-Metadaten und UI-Kontext.
- Chunk-, Editor-, 2D-, Map- und LV-Daten bleiben in ihren Microservices.
- Chunk-Provisioning selbst läuft über services.project_service und
  routes/projects_api.py, nicht in Templates.
"""

from typing import Any, Dict, List, Optional
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

from extensions import db
from models import Conversation

from services.current_user import (
    ensure_default_user,
    get_current_user_context,
    get_current_user_id,
)

from services.project_permissions import (
    PERMISSION_VIEW,
    PermissionDenied,
    require_project_permission,
    serialize_project_permissions,
)

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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled"}:
            return False

        return default

    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_quote(value: Any) -> str:
    try:
        return quote(str(value), safe="")
    except Exception:
        return ""


def _first_non_empty(*values: Any) -> str:
    try:
        for value in values:
            if isinstance(value, bool):
                return "1" if value else "0"

            text = str(value if value is not None else "").strip()
            if text:
                return text
    except Exception:
        pass

    return ""


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


def _safe_status(value: Any, default: str = "pending") -> str:
    try:
        text = _safe_str(value, "", 40).lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "ok": "ready",
            "active": "ready",
            "linked": "ready",
            "created": "ready",
            "provisioned": "ready",
            "failed": "error",
            "failure": "error",
            "unavailable": "error",
            "waiting": "pending",
            "queued": "pending",
            "off": "disabled",
        }

        text = aliases.get(text, text)

        if text in {"ready", "pending", "error", "disabled"}:
            return text

        return default

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


def _url_for_safe(endpoint: str, fallback: str, **values: Any) -> str:
    try:
        return str(url_for(endpoint, **values))
    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────
# Chunk context helpers
# ─────────────────────────────────────────────────────────────

def _new_chunk_payload() -> Dict[str, Any]:
    return {
        "status": "pending",
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


def _extract_chunk_context_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = _safe_dict(payload)
        chunk = _safe_dict(data.get("chunk"))
        service_refs = _safe_dict(data.get("service_refs") or data.get("serviceRefs"))
        service_chunk = _safe_dict(service_refs.get("chunk"))
        metadata = _safe_dict(data.get("metadata"))
        metadata_chunk = _safe_dict(metadata.get("chunk"))

        chunk_project_id = _first_non_empty(
            data.get("chunk_project_id"),
            data.get("chunkProjectId"),
            chunk.get("chunk_project_id"),
            chunk.get("chunkProjectId"),
            service_chunk.get("chunk_project_id"),
            service_chunk.get("chunkProjectId"),
            metadata_chunk.get("chunk_project_id"),
            metadata_chunk.get("chunkProjectId"),
        ) or None

        chunk_universe_id = _first_non_empty(
            data.get("chunk_universe_id"),
            data.get("chunkUniverseId"),
            chunk.get("chunk_universe_id"),
            chunk.get("chunkUniverseId"),
            service_chunk.get("chunk_universe_id"),
            service_chunk.get("chunkUniverseId"),
            metadata_chunk.get("chunk_universe_id"),
            metadata_chunk.get("chunkUniverseId"),
        ) or None

        chunk_world_id = _first_non_empty(
            data.get("chunk_world_id"),
            data.get("chunkWorldId"),
            chunk.get("chunk_world_id"),
            chunk.get("chunkWorldId"),
            service_chunk.get("chunk_world_id"),
            service_chunk.get("chunkWorldId"),
            metadata_chunk.get("chunk_world_id"),
            metadata_chunk.get("chunkWorldId"),
        ) or None

        status = _safe_status(
            _first_non_empty(
                data.get("chunk_status"),
                data.get("chunkStatus"),
                chunk.get("status"),
                chunk.get("chunk_status"),
                chunk.get("chunkStatus"),
                service_chunk.get("status"),
                metadata_chunk.get("status"),
            ),
            "ready" if chunk_project_id and chunk_world_id else "pending",
        )

        route_hints = (
            _safe_dict(data.get("chunk_route_hints"))
            or _safe_dict(data.get("chunkRouteHints"))
            or _safe_dict(chunk.get("route_hints"))
            or _safe_dict(chunk.get("routeHints"))
            or _safe_dict(service_chunk.get("route_hints"))
            or _safe_dict(service_chunk.get("routeHints"))
            or _safe_dict(metadata_chunk.get("route_hints"))
            or _safe_dict(metadata_chunk.get("routeHints"))
        )

        explicit_ready = _first_non_empty(
            data.get("chunk_ready"),
            data.get("chunkReady"),
            chunk.get("ready"),
            chunk.get("chunk_ready"),
            chunk.get("chunkReady"),
            service_chunk.get("ready"),
            metadata_chunk.get("ready"),
        )

        ready = _safe_bool(
            explicit_ready,
            bool(chunk_project_id and chunk_world_id and status == "ready"),
        )

        if not chunk_project_id or not chunk_world_id:
            ready = False

        if ready and status not in {"error", "disabled"}:
            status = "ready"

        error = (
            _safe_dict(data.get("chunk_last_error"))
            or _safe_dict(data.get("chunkLastError"))
            or _safe_dict(chunk.get("error"))
            or _safe_dict(service_chunk.get("error"))
            or _safe_dict(metadata_chunk.get("error"))
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
            "error": error,
        }

    except Exception:
        return _new_chunk_payload()


def _project_chunk_context(project: Optional[Any]) -> Dict[str, Any]:
    if project is None:
        return _new_chunk_payload()

    try:
        direct_payload = {
            "chunk_project_id": getattr(project, "chunk_project_id", None),
            "chunk_universe_id": getattr(project, "chunk_universe_id", None),
            "chunk_world_id": getattr(project, "chunk_world_id", None),
            "chunk_status": getattr(project, "chunk_status", None),
            "chunk_ready": getattr(project, "chunk_ready", None),
            "chunk_route_hints": getattr(project, "chunk_route_hints", None),
            "chunk_last_error": getattr(project, "chunk_last_error", None),
            "service_refs": getattr(project, "service_refs", None),
            "metadata": getattr(project, "metadata_json", None),
        }

        context = _extract_chunk_context_from_payload(direct_payload)

        if context.get("chunk_project_id") and context.get("chunk_world_id"):
            return context

    except Exception:
        pass

    try:
        if hasattr(project, "to_dict"):
            payload = project.to_dict(include_private=True, include_refs=True)
            return _extract_chunk_context_from_payload(payload)
    except Exception:
        pass

    return _new_chunk_payload()


def _should_try_chunk_retry(project: Optional[Any]) -> bool:
    try:
        if project is None:
            return False

        if bool(getattr(project, "is_deleted", False)):
            return False

        if not _config_bool("VECTOPLAN_CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN", True):
            return False

        if request.args.get("skip_chunk_retry"):
            return False

        if not (
            request.args.get("ensure_chunk")
            or request.args.get("retry_chunk")
            or request.args.get("ensureChunk")
            or request.args.get("retryChunk")
        ):
            return False

        context = _project_chunk_context(project)
        return not bool(context.get("chunk_project_id") and context.get("chunk_world_id"))

    except Exception:
        return False


def _try_ensure_chunk_for_project(project: Optional[Any]) -> None:
    """
    Best-effort manual chunk provisioning retry.

    This only runs when the request explicitly asks for ensure_chunk/retry_chunk.
    Normal shell rendering should not block on a remote service.
    """
    if not _should_try_chunk_retry(project):
        return

    try:
        from services.project_service import ensure_project_chunk_link

        ensure_project_chunk_link(
            project,
            user_id=get_current_user_id(),
            force=False,
            commit=True,
        )
    except Exception as exc:
        _log_warning("chunk ensure from project UI failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Response / security helpers
# ─────────────────────────────────────────────────────────────

def _is_dev() -> bool:
    try:
        env = str(current_app.config.get("FLASK_ENV", "") or "").lower()
        debug = bool(current_app.config.get("DEBUG", False))
        return debug or env.startswith("dev") or env.startswith("development")
    except Exception:
        return False


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


def _json_response(payload: Dict[str, Any], status: int = 200):
    try:
        resp = jsonify(payload)
        resp.status_code = int(status)
        return _finalize_json_response(resp, no_store=True), status

    except Exception:
        fallback = jsonify(
            {
                "ok": False,
                "error": "failed to serialize response",
                "code": "response_serialization_failed",
            }
        )
        fallback.status_code = 500
        return _finalize_json_response(fallback, no_store=True), 500


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
        "code": code,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status)


def _exception_response(message: str, exc: Exception, *, code: str = "internal_error"):
    _log_exception(message, exc)
    return _json_error(str(exc), 500, code=code)


def _permission_error_response(exc: PermissionDenied):
    try:
        return _json_response(exc.to_dict(), exc.status_code)
    except Exception:
        return _json_error("permission denied", 403, code="permission_denied")


# ─────────────────────────────────────────────────────────────
# Conversation / project helpers
# ─────────────────────────────────────────────────────────────

def _new_conversation(
    *,
    title: str = "Projekt-Shell",
    project: Optional[Any] = None,
    project_id: Optional[str] = None,
    commit: bool = True,
) -> Conversation:
    conv = Conversation()

    try:
        conv.title = _safe_str(title, "Projekt-Shell", 255)

        resolved_project_id = project_id

        if resolved_project_id is None and project is not None:
            resolved_project_id = str(getattr(project, "id", "") or "")

        if resolved_project_id:
            conv.project_id = _safe_str(resolved_project_id, "", 120) or None

        if project is not None and hasattr(conv, "owner_user_id"):
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
            db.session.rollback()
        raise


def _get_or_create_shell_conversation() -> Conversation:
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
            commit=True,
        )


def _ensure_project_conversation(project: Optional[Any]) -> Conversation:
    try:
        if project is None:
            return _get_or_create_shell_conversation()

        conv = get_or_create_project_conversation(project, commit=True)

        if conv is not None:
            return conv

        return _get_or_create_shell_conversation()

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return _get_or_create_shell_conversation()


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


def _is_new_project_identifier(value: Any) -> bool:
    try:
        text = _safe_str(value, "", 80).lower()
        return text in {"", "new", "create", "neu", "projekt-neu", "project-new"}
    except Exception:
        return False


def _load_selected_project(project_identifier: Optional[str]) -> Optional[Any]:
    try:
        if _is_new_project_identifier(project_identifier):
            return None

        project = resolve_project(project_identifier)

        if project is not None:
            _try_ensure_chunk_for_project(project)

        return project

    except Exception:
        return None


def _new_project_payload() -> Dict[str, Any]:
    chunk = _new_chunk_payload()

    return {
        "isNew": True,
        "is_new": True,
        "id": None,
        "project_id": None,
        "public_id": "new",
        "projectPublicId": "new",
        "appProjectPublicId": "new",
        "name": "",
        "display_name": "Neues Projekt",
        "displayName": "Neues Projekt",
        "description": "",
        "address_text": "",
        "address": {
            "text": "",
            "street": "",
            "house_number": "",
            "postal_code": "",
            "city": "",
            "region": "",
            "country": "DE",
            "latitude": None,
            "longitude": None,
            "coordinate_srid": "EPSG:4326",
        },
        "street": "",
        "house_number": "",
        "postal_code": "",
        "city": "",
        "region": "",
        "country": "DE",
        "latitude": None,
        "longitude": None,
        "coordinate_srid": "EPSG:4326",
        "coordinates": {
            "lat": None,
            "lng": None,
            "latitude": None,
            "longitude": None,
            "srid": "EPSG:4326",
        },
        "chunk": chunk,
        "chunk_ready": False,
        "chunkReady": False,
        "chunk_status": "pending",
        "chunkStatus": "pending",
        "chunk_project_id": None,
        "chunkProjectId": None,
        "chunk_universe_id": None,
        "chunkUniverseId": None,
        "chunk_world_id": None,
        "chunkWorldId": None,
        "chunk_route_hints": {},
        "chunkRouteHints": {},
        "plan2d_id": None,
        "plan2dId": None,
        "lv_id": None,
        "lvId": None,
        "visibility": "private",
        "is_public": False,
        "setup_status": "draft",
        "setupStatus": "draft",
        "is_configured": False,
        "isConfigured": False,
        "status": "draft",
        "url": "/project=new",
        "href": "/project=new",
        "paths": {
            "projectPagePath": "/ui/project/new",
            "projectUrl": "/ui/project/new",
            "projectPublicUrl": "/project=new",
            "chunkContextPath": "",
            "chunkEnsurePath": "",
            "chunkRetryPath": "",
        },
        "access": {
            "role": "owner",
            "source": "new_project",
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
        },
    }


def _project_payload_for_template(project: Optional[Any], *, user_id: int, is_new: bool = False) -> Dict[str, Any]:
    try:
        if project is None:
            return _new_project_payload()

        payload = serialize_project(
            project,
            user_id=user_id,
            include_permissions=True,
            include_members=False,
            include_service_links=True,
            include_versions=False,
            include_embed_policy=False,
        )

        chunk = _project_chunk_context(project)

        payload["isNew"] = False
        payload["is_new"] = False
        payload["chunk"] = chunk
        payload["chunk_ready"] = chunk.get("ready")
        payload["chunkReady"] = chunk.get("ready")
        payload["chunk_status"] = chunk.get("status")
        payload["chunkStatus"] = chunk.get("status")
        payload["chunk_project_id"] = chunk.get("chunk_project_id")
        payload["chunkProjectId"] = chunk.get("chunk_project_id")
        payload["chunk_universe_id"] = chunk.get("chunk_universe_id")
        payload["chunkUniverseId"] = chunk.get("chunk_universe_id")
        payload["chunk_world_id"] = chunk.get("chunk_world_id")
        payload["chunkWorldId"] = chunk.get("chunk_world_id")
        payload["chunk_route_hints"] = chunk.get("route_hints") or {}
        payload["chunkRouteHints"] = chunk.get("route_hints") or {}

        paths = _safe_dict(payload.get("paths"))
        public_id = _safe_str(getattr(project, "public_id", None), "", 120)

        if public_id:
            paths.setdefault("chunkContextPath", f"/v1/projects/{_safe_quote(public_id)}/chunk")
            paths.setdefault("chunkEnsurePath", f"/v1/projects/{_safe_quote(public_id)}/chunk/ensure")
            paths.setdefault("chunkRetryPath", f"/v1/projects/{_safe_quote(public_id)}/chunk/retry")

        payload["paths"] = paths

        return payload

    except Exception:
        if is_new:
            return _new_project_payload()

        return {
            "isNew": False,
            "is_new": False,
            "public_id": "",
            "name": "",
            "setup_status": "draft",
            "is_configured": False,
            "chunk": _new_chunk_payload(),
            "chunk_ready": False,
            "chunkReady": False,
            "url": "/project=new",
            "href": "/project=new",
        }


def _project_sidebar_context(
    *,
    selected_project: Optional[Any],
    conversation: Conversation,
    user_id: int,
) -> Dict[str, Any]:
    try:
        items = list_project_sidebar_items(
            user_id=user_id,
            include_public=True,
            limit=200,
        )

        selected_public_id = _safe_str(getattr(selected_project, "public_id", None), "", 120)
        selected_conversation_id = _safe_str(getattr(selected_project, "conversation_id", None), "", 80)
        selected_chunk = _project_chunk_context(selected_project)

        enriched: List[Dict[str, Any]] = []

        for item in items:
            try:
                current_item = dict(item or {})
                public_id = (
                    _safe_str(current_item.get("public_id"), "", 120)
                    or _safe_str(current_item.get("projectId"), "", 120)
                    or _safe_str(current_item.get("id"), "", 120)
                )

                if public_id and public_id == selected_public_id:
                    current_item["isActive"] = True
                    current_item["is_active"] = True

                if not current_item.get("href") and public_id:
                    current_item["href"] = f"/project={public_id}"

                if not current_item.get("chatId") and current_item.get("conversation_id"):
                    current_item["chatId"] = current_item.get("conversation_id")

                enriched.append(current_item)

            except Exception:
                continue

        return {
            "enabled": True,
            "currentChatId": selected_conversation_id or getattr(conversation, "id", ""),
            "currentProjectId": selected_public_id,
            "current_project_id": selected_public_id,
            "currentChunk": selected_chunk,
            "current_chunk": selected_chunk,
            "currentTitle": getattr(selected_project, "name", None) or "Neues Projekt",
            "currentSubtitle": getattr(selected_project, "address_text", None) or "Projekt definieren",
            "defaultCollapsed": False,
            "defaultWidth": 280,
            "minWidth": 220,
            "maxWidth": 420,
            "collapsedWidth": 64,
            "storageKey": "vectoplan.projectSidebar.v1",
            "routeBase": "/",
            "apiPath": "/v1/projects/sidebar",
            "items": enriched,
        }

    except Exception:
        return {
            "enabled": True,
            "currentChatId": getattr(conversation, "id", ""),
            "currentProjectId": "",
            "currentTitle": "Projekt",
            "currentSubtitle": "Projekt definieren",
            "items": [],
        }


def _workspace_context_for_project(
    *,
    project: Optional[Any],
    conversation: Conversation,
    is_new: bool,
) -> Dict[str, Any]:
    try:
        chat_id = _safe_str(getattr(conversation, "id", ""), "", 120)
        chat_id_q = _safe_quote(chat_id)
        chunk = _project_chunk_context(project)

        if project is not None:
            public_id = _safe_str(getattr(project, "public_id", ""), "", 120)
            public_id_q = _safe_quote(public_id)
            project_paths = build_project_paths(project)
            project_page_path = project_paths.get("projectPagePath") or project_workspace_path(project)
            project_public = project_paths.get("projectPublicUrl") or project_public_url(project)
            configured = bool(getattr(project, "is_configured", False))

            editor_url = project_paths.get("editorPagePath") or f"/ui/project/{public_id_q}/editor"
            map_url = project_paths.get("mapPagePath") or f"/ui/project/{public_id_q}/map"
            cad2d_url = project_paths.get("cad2dPagePath") or f"/ui/project/{public_id_q}/cad2d"
            lv_url = project_paths.get("lvPagePath") or f"/ui/project/{public_id_q}/lv"
            admin_url = project_paths.get("adminPagePath") or f"/ui/project/{public_id_q}/admin"
            plan2d_json = project_paths.get("plan2dJsonPath") or f"/ui/project/{public_id_q}/plan2d.json"
            cad_embed_json = project_paths.get("cadEmbedJsonPath") or f"/ui/project/{public_id_q}/cad-embed.json"
            chunk_context_path = f"/v1/projects/{public_id_q}/chunk"
            chunk_ensure_path = f"/v1/projects/{public_id_q}/chunk/ensure"
            chunk_retry_path = f"/v1/projects/{public_id_q}/chunk/retry"
        else:
            public_id = "new"
            project_paths = {}
            project_page_path = "/ui/project/new"
            project_public = "/project=new"
            configured = False

            editor_url = ""
            map_url = ""
            cad2d_url = ""
            lv_url = ""
            admin_url = ""
            plan2d_json = ""
            cad_embed_json = ""
            chunk_context_path = ""
            chunk_ensure_path = ""
            chunk_retry_path = ""

        paths = {
            "projectPagePath": project_page_path,
            "projectUrl": project_page_path,
            "projectPublicUrl": project_public,
            "editorPagePath": editor_url,
            "initialEditorUrl": editor_url,
            "viewerJsonPath": f"/ui/chat/{chat_id_q}/viewer.json" if chat_id else "",
            "versionsPath": f"/ui/chat/{chat_id_q}/versions.json" if chat_id else "",
            "mapPagePath": map_url,
            "plan2dJsonPath": plan2d_json,
            "cad2dPagePath": cad2d_url,
            "cadEmbedJsonPath": cad_embed_json,
            "adminPagePath": admin_url,
            "lvPagePath": lv_url,
            "chunkContextPath": chunk_context_path,
            "chunkEnsurePath": chunk_ensure_path,
            "chunkRetryPath": chunk_retry_path,
            "uploadPath": f"/ui/chat/{chat_id_q}/upload" if chat_id else "",
            "stateGetPath": f"/v1/chats/{chat_id_q}/viewer/selection" if chat_id else "",
            "statePutPath": f"/v1/chats/{chat_id_q}/viewer/selection" if chat_id else "",
        }

        for key, value in dict(project_paths or {}).items():
            if key not in paths or not paths.get(key):
                paths[key] = value

        return {
            "chat_id": chat_id,
            "project_id": getattr(project, "id", None),
            "project_public_id": public_id,
            "app_project_public_id": public_id,
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": project_page_path,
            "project_public_url": project_public,
            "project_configured": configured,
            "is_new": bool(is_new),
            "editor_url": editor_url,
            "viewer_url": editor_url,
            "map_url": map_url,
            "cad2d_url": cad2d_url,
            "lv_url": lv_url,
            "admin_url": admin_url,
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
            "chunk_route_hints": chunk.get("route_hints") or {},
            "chunkRouteHints": chunk.get("route_hints") or {},
            "paths": paths,
        }

    except Exception:
        chat_id = _safe_str(getattr(conversation, "id", ""), "", 120)
        chat_id_q = _safe_quote(chat_id)

        return {
            "chat_id": chat_id,
            "project_public_id": "new",
            "app_project_public_id": "new",
            "default_mode": "project",
            "workspace_mode": "project",
            "project_url": "/ui/project/new",
            "project_public_url": "/project=new",
            "project_configured": False,
            "editor_url": "",
            "viewer_url": "",
            "map_url": "",
            "chunk": _new_chunk_payload(),
            "chunk_ready": False,
            "chunkReady": False,
            "paths": {
                "projectPagePath": "/ui/project/new",
                "projectUrl": "/ui/project/new",
                "projectPublicUrl": "/project=new",
                "viewerJsonPath": f"/ui/chat/{chat_id_q}/viewer.json" if chat_id else "",
                "stateGetPath": f"/v1/chats/{chat_id_q}/viewer/selection" if chat_id else "",
                "statePutPath": f"/v1/chats/{chat_id_q}/viewer/selection" if chat_id else "",
            },
        }


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
        ensure_default_user()
    except Exception:
        pass

    try:
        user_id = get_current_user_id()
        conversation = _ensure_project_conversation(selected_project)

        project_payload = _project_payload_for_template(
            selected_project,
            user_id=user_id,
            is_new=is_new,
        )

        workspace = _workspace_context_for_project(
            project=selected_project,
            conversation=conversation,
            is_new=is_new,
        )

        workspace_paths = dict(workspace.get("paths") or {})

        project_sidebar = _project_sidebar_context(
            selected_project=selected_project,
            conversation=conversation,
            user_id=user_id,
        )

        resp = make_response(
            render_template(
                "chat_viewer.html",
                chat_id=conversation.id,
                viewer_url=workspace.get("viewer_url") or "",
                editor_url=workspace.get("editor_url") or "",
                initial_editor_url=workspace.get("editor_url") or "",
                map_url=workspace.get("map_url") or "",
                initial_map_url=workspace.get("map_url") or "",
                default_mode="project",
                workspace_mode="project",
                workspace=workspace,
                workspace_paths=workspace_paths,
                project=project_payload,
                current_project=project_payload,
                project_sidebar=project_sidebar,
                current_user=get_current_user_context(ensure=True).to_dict(),
            ),
            status_code,
        )

        return _finalize_html_response(resp, no_store=False, workspace_shell=True)

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


def _render_project_workspace(
    *,
    selected_project: Optional[Any],
    is_new: bool = False,
    status_code: int = 200,
) -> Response:
    try:
        user_id = get_current_user_id()
        project_payload = _project_payload_for_template(
            selected_project,
            user_id=user_id,
            is_new=is_new,
        )

        resp = make_response(
            render_template(
                "viewer/project.html",
                project=project_payload,
                current_project=project_payload,
                current_user=get_current_user_context(ensure=True).to_dict(),
                is_new=bool(is_new),
            ),
            status_code,
        )

        return _finalize_html_response(resp, no_store=False, allow_embed=True)

    except Exception as exc:
        _log_exception("render project workspace failed", exc)
        resp = jsonify(
            {
                "ok": False,
                "error": f"project workspace render failed: {exc}",
                "code": "project_workspace_render_failed",
            }
        )
        resp.status_code = 500
        return _finalize_json_response(resp, no_store=True)


def _render_placeholder_workspace(
    *,
    title: str,
    message: str,
    project: Optional[Any] = None,
    status_code: int = 200,
) -> Response:
    try:
        html = render_template(
            "viewer/project.html",
            project=_project_payload_for_template(
                project,
                user_id=get_current_user_id(),
                is_new=project is None,
            ),
            current_project=_project_payload_for_template(
                project,
                user_id=get_current_user_id(),
                is_new=project is None,
            ),
            current_user=get_current_user_context(ensure=True).to_dict(),
            is_new=project is None,
            placeholder={
                "title": title,
                "message": message,
            },
        )
        resp = make_response(html, status_code)
        return _finalize_html_response(resp, no_store=False, allow_embed=True)

    except Exception:
        resp = jsonify(
            {
                "ok": True,
                "title": title,
                "message": message,
            }
        )
        resp.status_code = status_code
        return _finalize_json_response(resp, no_store=True)


# ─────────────────────────────────────────────────────────────
# Root / project shell routes
# ─────────────────────────────────────────────────────────────

@bp.before_request
def _ui_projects_before_request():
    try:
        ensure_default_user()
    except Exception as exc:
        _log_warning("ensure_default_user before project UI failed: %s", exc.__class__.__name__)


@bp.get("/")
def project_root():
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
def project_by_equals(project_id: str):
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

        user_id = get_current_user_id()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        return _render_project_shell(selected_project=project, is_new=False)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_by_equals failed", exc, code="project_route_failed")


@bp.get("/project/<project_id>")
def project_by_path(project_id: str):
    try:
        return redirect(f"/project={_safe_quote(project_id)}", code=302)
    except Exception:
        return redirect("/", code=302)


@bp.get("/projects")
def projects_list_page():
    try:
        return _render_project_shell(selected_project=None, is_new=True)
    except Exception as exc:
        return _exception_response("projects_list_page failed", exc, code="projects_page_failed")


# ─────────────────────────────────────────────────────────────
# Workspace iframe routes
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/project/new")
def project_new_workspace():
    try:
        return _render_project_workspace(selected_project=None, is_new=True)
    except Exception as exc:
        return _exception_response("project_new_workspace failed", exc, code="project_new_workspace_failed")


@bp.get("/ui/project/<project_id>/project")
def project_edit_workspace(project_id: str):
    try:
        if _is_new_project_identifier(project_id):
            return _render_project_workspace(selected_project=None, is_new=True)

        project = _load_selected_project(project_id)

        if project is None:
            return _json_error(
                "project not found",
                404,
                code="project_not_found",
                extra={"project_id": project_id},
            )

        user_id = get_current_user_id()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        return _render_project_workspace(selected_project=project, is_new=False)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_edit_workspace failed", exc, code="project_workspace_failed")


@bp.get("/ui/project/<project_id>/admin")
def project_admin_workspace(project_id: str):
    try:
        project = _load_selected_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = get_current_user_id()
        require_project_permission(project, "manage", user_id, allow_public_view=False)

        return _render_placeholder_workspace(
            title="Projektverwaltung",
            message="Die Projektverwaltung wird in einem späteren Schritt ergänzt.",
            project=project,
            status_code=200,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_admin_workspace failed", exc, code="project_admin_failed")


@bp.get("/ui/project/<project_id>/lv")
def project_lv_workspace(project_id: str):
    try:
        project = _load_selected_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = get_current_user_id()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        if not bool(getattr(project, "is_configured", False)):
            return _json_error(
                "project is not configured",
                409,
                code="project_not_configured",
                extra={"project_id": project_id},
            )

        return _render_placeholder_workspace(
            title="Leistungsverzeichnis",
            message="Das LV-Modul ist noch nicht angebunden.",
            project=project,
            status_code=200,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_lv_workspace failed", exc, code="project_lv_failed")


# ─────────────────────────────────────────────────────────────
# Lightweight JSON helpers for UI
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/project/<project_id>/context.json")
def project_context_json(project_id: str):
    try:
        user_id = get_current_user_id()

        if _is_new_project_identifier(project_id):
            payload = _project_payload_for_template(None, user_id=user_id, is_new=True)
            shell_conv = _get_or_create_shell_conversation()

            return _json_response(
                {
                    "ok": True,
                    "project": payload,
                    "is_new": True,
                    "workspace": _workspace_context_for_project(
                        project=None,
                        conversation=shell_conv,
                        is_new=True,
                    ),
                },
                200,
            )

        project = _load_selected_project(project_id)

        if project is None:
            return _json_error(
                "project not found",
                404,
                code="project_not_found",
                extra={"project_id": project_id},
            )

        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        conversation = _ensure_project_conversation(project)
        workspace = _workspace_context_for_project(
            project=project,
            conversation=conversation,
            is_new=False,
        )

        return _json_response(
            {
                "ok": True,
                "project": _project_payload_for_template(project, user_id=user_id, is_new=False),
                "sidebar_item": serialize_project_sidebar_item(project, user_id=user_id),
                "workspace": workspace,
                "chunk": workspace.get("chunk") or _project_chunk_context(project),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
        )

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_context_json failed", exc, code="project_context_failed")


@bp.get("/ui/projects/sidebar.json")
def ui_projects_sidebar_json():
    try:
        user_id = get_current_user_id()
        items = list_project_sidebar_items(user_id=user_id, include_public=True, limit=200)

        return _json_response(
            {
                "ok": True,
                "user_id": user_id,
                "items": items,
                "sidebar_items": items,
                "total": len(items),
            },
            200,
        )

    except Exception as exc:
        return _exception_response("ui_projects_sidebar_json failed", exc, code="ui_sidebar_failed")


# ─────────────────────────────────────────────────────────────
# Redirect helpers
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/project")
def ui_project_root_redirect():
    try:
        project_id = _current_project_identifier_from_request()

        if project_id:
            return redirect(f"/project={_safe_quote(project_id)}", code=302)

        return redirect("/", code=302)

    except Exception:
        return redirect("/", code=302)


@bp.get("/ui/projects")
def ui_projects_root_redirect():
    try:
        return redirect("/", code=302)
    except Exception:
        return redirect("/", code=302)


__all__ = [
    "bp",
    "ui_projects_bp",
    "projects_ui_bp",
]