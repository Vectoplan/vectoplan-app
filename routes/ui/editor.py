# services/vectoplan-app/routes/ui/editor.py
"""
UI integration route for embedding the local VECTOPLAN editor.

Purpose:
- Build browser-facing editor URLs only.
- Redirect app iframe requests to vectoplan-editor.
- Keep browser/public URLs and Docker/internal URLs separated.
- Add app-project and chunk references when available.
- Optionally retry app -> chunk provisioning before redirect.
- Stay defensive during the project-management refactor.

Primary iframe routes:
    /ui/chat/<chat_id>/editor
    /ui/project/<project_id>/editor
    /ui/editor

Redirect target example:
    http://localhost:5100/editor?embed=1&chat_id=...&app_project_id=...&chunk_project_id=...&chunk_world_id=...

Important:
- This route uses VECTOPLAN_EDITOR_PUBLIC_URL, never the internal Docker URL.
- App project references are separate from chunk project references.
- If a chunk_project_id exists, it is passed as chunk_project_id and, when no
  explicit project_id was requested, also as project_id for editor compatibility.
- If a chunk_world_id exists, it is passed as chunk_world_id and, when no
  explicit world_id was requested, also as world_id for editor compatibility.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, redirect, request


ui_editor_bp = Blueprint("ui_editor", __name__)
bp = ui_editor_bp

logger = logging.getLogger(__name__)


DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"

MAX_ID_LENGTH = 180
MAX_QUERY_VALUE_LENGTH = 512

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")
SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,511}$")

DOCKER_INTERNAL_HOSTS = {
    "editor",
    "vectoplan-editor",
    "server-editor",
    "vectoplan_editor",
}

ALLOWED_QUERY_KEYS = {
    "chat_id",
    "embed",
    "mode",
    "tool",
    "view",
    "debug",
    "readonly",
    "workspace",
    "source",

    # Editor / chunk compatibility
    "project_id",
    "world_id",
    "snapshot_id",
    "version_id",
    "artifact_id",

    # Explicit app references
    "app_project_id",
    "app_project_db_id",
    "app_project_public_id",
    "project_public_id",
    "conversation_id",

    # Explicit chunk references
    "chunk_project_id",
    "chunk_universe_id",
    "chunk_world_id",
    "chunk_ready",
    "chunk_status",

    # Later service references
    "plan2d_id",
    "lv_id",
}


@ui_editor_bp.get("/ui/chat/<chat_id>/editor")
def editor_iframe(chat_id: str):
    """
    Redirect chat workspace iframe to vectoplan-editor.

    Adds project/chunk refs if the chat/conversation is linked to an app Project.
    """
    try:
        safe_chat_id = _sanitize_id(chat_id, label="chat_id")
        project_identifier = _project_identifier_from_request()
        editor_url = _build_editor_url(
            chat_id=safe_chat_id,
            project_identifier=project_identifier,
        )

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        return response

    except ValueError as exc:
        logger.warning("Invalid editor iframe request: %s", exc)
        return _json_error(
            status=400,
            code="invalid_editor_request",
            message=str(exc),
        )

    except Exception:
        logger.exception("Failed to build editor iframe URL for chat_id=%r", chat_id)
        return _json_error(
            status=500,
            code="editor_url_failed",
            message="Editor-URL konnte nicht erzeugt werden.",
        )


@ui_editor_bp.get("/ui/project/<project_id>/editor")
def project_editor_iframe(project_id: str):
    """
    Redirect project workspace iframe to vectoplan-editor.

    This route is useful after project-first navigation:
        /ui/project/<project_public_id>/editor
    """
    try:
        safe_project_id = _sanitize_id(project_id, label="project_id")
        project_context = _resolve_project_context(
            chat_id=None,
            project_identifier=safe_project_id,
        )

        chat_id = _safe_optional_id(project_context.get("conversation_id"))

        editor_url = _build_editor_url(
            chat_id=chat_id,
            project_identifier=safe_project_id,
            preloaded_project_context=project_context,
        )

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        return response

    except ValueError as exc:
        logger.warning("Invalid project editor iframe request: %s", exc)
        return _json_error(
            status=400,
            code="invalid_project_editor_request",
            message=str(exc),
        )

    except Exception:
        logger.exception("Failed to build editor iframe URL for project_id=%r", project_id)
        return _json_error(
            status=500,
            code="editor_url_failed",
            message="Editor-URL konnte nicht erzeugt werden.",
        )


@ui_editor_bp.get("/ui/editor")
def editor_iframe_without_chat():
    """
    Generic editor iframe route.

    Supports optional project refs via query:
        /ui/editor?app_project_id=<public_id>
        /ui/editor?project_public_id=<public_id>
    """
    try:
        project_identifier = _project_identifier_from_request()
        editor_url = _build_editor_url(
            chat_id=None,
            project_identifier=project_identifier,
        )

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        return response

    except ValueError as exc:
        logger.warning("Invalid generic editor iframe request: %s", exc)
        return _json_error(
            status=400,
            code="invalid_editor_request",
            message=str(exc),
        )

    except Exception:
        logger.exception("Failed to build generic editor iframe URL")
        return _json_error(
            status=500,
            code="editor_url_failed",
            message="Editor-URL konnte nicht erzeugt werden.",
        )


def _build_editor_url(
    *,
    chat_id: Optional[str],
    project_identifier: Optional[str] = None,
    preloaded_project_context: Optional[Dict[str, Any]] = None,
) -> str:
    public_url = _config_value(
        primary="VECTOPLAN_EDITOR_PUBLIC_URL",
        fallback="EDITOR_PUBLIC_URL",
        default=DEFAULT_EDITOR_PUBLIC_URL,
    )
    editor_route = _config_value(
        primary="VECTOPLAN_EDITOR_ROUTE",
        fallback="EDITOR_ROUTE",
        default=DEFAULT_EDITOR_ROUTE,
    )

    _validate_public_editor_url(public_url)

    base_url = _join_url(public_url, editor_route)
    project_context = (
        preloaded_project_context
        if isinstance(preloaded_project_context, dict)
        else _resolve_project_context(chat_id=chat_id, project_identifier=project_identifier)
    )
    query = _build_query(chat_id=chat_id, project_context=project_context)

    return _append_query(base_url, query)


def _build_query(*, chat_id: Optional[str], project_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    query: Dict[str, str] = {
        "embed": "1",
        "source": "vectoplan-app",
    }

    if chat_id:
        query["chat_id"] = _sanitize_id(chat_id, label="chat_id")

    # User-provided safe editor options.
    for key in sorted(ALLOWED_QUERY_KEYS):
        if key in {"chat_id", "embed", "source"}:
            continue

        value = request.args.get(key)
        if value is None or value == "":
            continue

        cleaned = _sanitize_query_value(key, value)
        if cleaned:
            query[key] = cleaned

    # App/project context is authoritative for app_* and chunk_* keys.
    ctx = project_context if isinstance(project_context, dict) else {}

    app_project_db_id = _safe_optional_id(ctx.get("app_project_db_id") or ctx.get("app_project_id_db"))
    app_project_public_id = _safe_optional_id(
        ctx.get("app_project_public_id")
        or ctx.get("project_public_id")
        or ctx.get("public_id")
    )
    conversation_id = _safe_optional_id(ctx.get("conversation_id"))

    chunk_project_id = _safe_optional_id(ctx.get("chunk_project_id"))
    chunk_universe_id = _safe_optional_id(ctx.get("chunk_universe_id"))
    chunk_world_id = _safe_optional_id(ctx.get("chunk_world_id") or ctx.get("world_id"))
    chunk_status = _safe_optional_status(ctx.get("chunk_status"))
    chunk_ready = _as_bool(ctx.get("chunk_ready"), bool(chunk_project_id and chunk_world_id and chunk_status == "ready"))

    plan2d_id = _safe_optional_id(ctx.get("plan2d_id"))
    lv_id = _safe_optional_id(ctx.get("lv_id"))

    if app_project_db_id:
        query["app_project_db_id"] = app_project_db_id

    if app_project_public_id:
        query["app_project_id"] = app_project_public_id
        query["app_project_public_id"] = app_project_public_id
        query["project_public_id"] = app_project_public_id

    if conversation_id:
        query["conversation_id"] = conversation_id
        query.setdefault("chat_id", conversation_id)

    if chunk_project_id:
        query["chunk_project_id"] = chunk_project_id
        query.setdefault("project_id", chunk_project_id)

    if chunk_universe_id:
        query["chunk_universe_id"] = chunk_universe_id

    if chunk_world_id:
        query["chunk_world_id"] = chunk_world_id
        query.setdefault("world_id", chunk_world_id)

    if chunk_status:
        query["chunk_status"] = chunk_status

    query["chunk_ready"] = "1" if chunk_ready else "0"

    if plan2d_id:
        query["plan2d_id"] = plan2d_id

    if lv_id:
        query["lv_id"] = lv_id

    readonly = ctx.get("readonly")
    if readonly is not None and "readonly" not in query:
        query["readonly"] = "1" if _as_bool(readonly, False) else "0"

    return query


def _resolve_project_context(
    *,
    chat_id: Optional[str],
    project_identifier: Optional[str],
) -> Dict[str, Any]:
    """
    Resolve app Project references best-effort.

    Returns empty dict when project services/models are not available.
    """
    context: Dict[str, Any] = {}

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

        if project is None:
            if chat_id:
                context["conversation_id"] = chat_id
            return context

        if _should_retry_chunk_on_editor_open(project):
            _try_ensure_project_chunk_link(project)

        app_project_db_id = _safe_optional_id(getattr(project, "id", None))
        app_project_public_id = _safe_optional_id(getattr(project, "public_id", None)) or app_project_db_id
        conversation_id = _safe_optional_id(getattr(project, "conversation_id", None)) or _safe_optional_id(chat_id)

        chunk_context = _extract_project_chunk_context(project)

        context.update(
            {
                "app_project_db_id": app_project_db_id,
                "app_project_public_id": app_project_public_id,
                "project_public_id": app_project_public_id,
                "public_id": app_project_public_id,
                "conversation_id": conversation_id,
                "chunk_project_id": chunk_context.get("chunk_project_id"),
                "chunk_universe_id": chunk_context.get("chunk_universe_id"),
                "chunk_world_id": chunk_context.get("chunk_world_id"),
                "chunk_status": chunk_context.get("chunk_status"),
                "chunk_ready": chunk_context.get("chunk_ready"),
                "chunk_route_hints": chunk_context.get("chunk_route_hints"),
                "plan2d_id": _safe_optional_id(getattr(project, "plan2d_id", None)),
                "lv_id": _safe_optional_id(getattr(project, "lv_id", None)),
                "is_configured": bool(getattr(project, "is_configured", False)),
            }
        )

        try:
            from services.current_user import get_current_user_id
            from services.project_permissions import can_edit_project

            user_id = get_current_user_id()
            context["readonly"] = not bool(can_edit_project(project, user_id=user_id))
        except Exception:
            context["readonly"] = False

        return context

    except Exception:
        logger.warning("Project context resolution failed", exc_info=True)
        if chat_id:
            context["conversation_id"] = chat_id
        return context


def _extract_project_chunk_context(project: Any) -> Dict[str, Any]:
    """
    Extract chunk context from direct model fields, service_refs and to_dict().
    """
    result: Dict[str, Any] = {
        "chunk_project_id": "",
        "chunk_universe_id": "",
        "chunk_world_id": "",
        "chunk_status": "pending",
        "chunk_ready": False,
        "chunk_route_hints": {},
    }

    try:
        direct_project_id = _safe_optional_id(getattr(project, "chunk_project_id", None))
        direct_universe_id = _safe_optional_id(getattr(project, "chunk_universe_id", None))
        direct_world_id = _safe_optional_id(getattr(project, "chunk_world_id", None))
        direct_status = _safe_optional_status(getattr(project, "chunk_status", None))
        direct_ready = _as_bool(getattr(project, "chunk_ready", None), False)

        result.update(
            {
                "chunk_project_id": direct_project_id,
                "chunk_universe_id": direct_universe_id,
                "chunk_world_id": direct_world_id,
                "chunk_status": direct_status or "ready" if direct_project_id and direct_world_id else "pending",
                "chunk_ready": bool(direct_ready and direct_project_id and direct_world_id),
                "chunk_route_hints": _safe_dict(getattr(project, "chunk_route_hints", None)),
            }
        )

        if direct_project_id and direct_world_id:
            if not result["chunk_ready"] and result["chunk_status"] == "ready":
                result["chunk_ready"] = True
            return result

    except Exception:
        pass

    try:
        if hasattr(project, "to_dict"):
            payload = project.to_dict(include_private=True, include_refs=True)
        else:
            payload = {}

        if isinstance(payload, dict):
            chunk = _safe_dict(payload.get("chunk"))
            service_refs = _safe_dict(payload.get("service_refs") or payload.get("serviceRefs"))
            service_chunk = _safe_dict(service_refs.get("chunk"))

            chunk_project_id = _first_non_empty(
                result.get("chunk_project_id"),
                chunk.get("chunk_project_id"),
                chunk.get("chunkProjectId"),
                service_chunk.get("chunk_project_id"),
                service_chunk.get("chunkProjectId"),
                payload.get("chunk_project_id"),
                payload.get("chunkProjectId"),
            )

            chunk_universe_id = _first_non_empty(
                result.get("chunk_universe_id"),
                chunk.get("chunk_universe_id"),
                chunk.get("chunkUniverseId"),
                service_chunk.get("chunk_universe_id"),
                service_chunk.get("chunkUniverseId"),
                payload.get("chunk_universe_id"),
                payload.get("chunkUniverseId"),
            )

            chunk_world_id = _first_non_empty(
                result.get("chunk_world_id"),
                chunk.get("chunk_world_id"),
                chunk.get("chunkWorldId"),
                service_chunk.get("chunk_world_id"),
                service_chunk.get("chunkWorldId"),
                payload.get("chunk_world_id"),
                payload.get("chunkWorldId"),
            )

            chunk_status = _safe_optional_status(
                _first_non_empty(
                    result.get("chunk_status"),
                    chunk.get("status"),
                    chunk.get("chunk_status"),
                    chunk.get("chunkStatus"),
                    service_chunk.get("status"),
                    payload.get("chunk_status"),
                    payload.get("chunkStatus"),
                )
            )

            route_hints = (
                _safe_dict(result.get("chunk_route_hints"))
                or _safe_dict(chunk.get("route_hints"))
                or _safe_dict(chunk.get("routeHints"))
                or _safe_dict(service_chunk.get("route_hints"))
                or _safe_dict(service_chunk.get("routeHints"))
                or _safe_dict(payload.get("chunk_route_hints"))
                or _safe_dict(payload.get("chunkRouteHints"))
            )

            ready = _as_bool(
                _first_non_empty(
                    result.get("chunk_ready"),
                    chunk.get("ready"),
                    chunk.get("chunk_ready"),
                    chunk.get("chunkReady"),
                    service_chunk.get("ready"),
                    payload.get("chunk_ready"),
                    payload.get("chunkReady"),
                ),
                bool(chunk_project_id and chunk_world_id and chunk_status == "ready"),
            )

            result.update(
                {
                    "chunk_project_id": _safe_optional_id(chunk_project_id),
                    "chunk_universe_id": _safe_optional_id(chunk_universe_id),
                    "chunk_world_id": _safe_optional_id(chunk_world_id),
                    "chunk_status": chunk_status or ("ready" if chunk_project_id and chunk_world_id else "pending"),
                    "chunk_ready": bool(ready and chunk_project_id and chunk_world_id),
                    "chunk_route_hints": route_hints,
                }
            )

    except Exception:
        pass

    return result


def _should_retry_chunk_on_editor_open(project: Any) -> bool:
    try:
        enabled = _as_bool(
            _config_value(
                primary="VECTOPLAN_CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
                fallback="CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
                default="true",
            ),
            True,
        )

        if not enabled:
            return False

        if project is None:
            return False

        if bool(getattr(project, "is_deleted", False)):
            return False

        context = _extract_project_chunk_context(project)

        return not bool(context.get("chunk_project_id") and context.get("chunk_world_id"))

    except Exception:
        return False


def _try_ensure_project_chunk_link(project: Any) -> None:
    """
    Best-effort provisioning retry.

    This is intentionally non-fatal. The editor can still open with app refs
    even if the chunk service is temporarily unavailable.
    """
    try:
        from services.current_user import get_current_user_id
        from services.project_service import ensure_project_chunk_link

        ensure_project_chunk_link(
            project,
            user_id=get_current_user_id(),
            force=False,
            commit=True,
        )
    except Exception:
        logger.warning("Chunk provisioning retry on editor open failed", exc_info=True)


def _project_identifier_from_request() -> str:
    try:
        candidates = (
            request.args.get("app_project_id"),
            request.args.get("app_project_public_id"),
            request.args.get("project_public_id"),
            request.args.get("project"),
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


def _safe_optional_id(value: Any) -> str:
    try:
        text = str(value if value is not None else "").strip()

        if not text:
            return ""

        return _sanitize_id(text, label="id")

    except Exception:
        return ""


def _safe_optional_status(value: Any) -> str:
    try:
        text = str(value if value is not None else "").strip().lower()

        if not text:
            return ""

        text = text.replace("-", "_").replace(" ", "_")

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

        if text not in {"ready", "pending", "error", "disabled"}:
            return ""

        return text

    except Exception:
        return ""


def _sanitize_id(value: Any, *, label: str = "id") -> str:
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"{label} fehlt.")

    if len(text) > MAX_ID_LENGTH:
        raise ValueError(f"{label} ist zu lang.")

    if not SAFE_ID_RE.match(text):
        raise ValueError(f"{label} enthält ungültige Zeichen.")

    return text


def _sanitize_query_value(key: str, value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if len(text) > MAX_QUERY_VALUE_LENGTH:
        raise ValueError(f"Query-Parameter '{key}' ist zu lang.")

    if key in {"debug", "readonly", "chunk_ready"}:
        return "1" if text.lower() in {"1", "true", "yes", "on", "y", "ja"} else "0"

    if key == "chunk_status":
        status = _safe_optional_status(text)
        if not status:
            raise ValueError(f"Query-Parameter '{key}' enthält ungültige Zeichen.")
        return status

    if key in {
        "mode",
        "tool",
        "view",
        "workspace",
        "source",
        "project_id",
        "world_id",
        "snapshot_id",
        "version_id",
        "artifact_id",
        "app_project_id",
        "app_project_db_id",
        "app_project_public_id",
        "project_public_id",
        "conversation_id",
        "chunk_project_id",
        "chunk_universe_id",
        "chunk_world_id",
        "plan2d_id",
        "lv_id",
    }:
        if not SAFE_QUERY_RE.match(text):
            raise ValueError(f"Query-Parameter '{key}' enthält ungültige Zeichen.")

    return text


def _as_bool(value: Any, default: bool = False) -> bool:
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


def _config_value(*, primary: str, fallback: str, default: str) -> str:
    value = None

    try:
        value = current_app.config.get(primary)
    except RuntimeError:
        value = None

    if value is None or str(value).strip() == "":
        try:
            value = current_app.config.get(fallback)
        except RuntimeError:
            value = None

    if value is None or str(value).strip() == "":
        value = os.environ.get(primary)

    if value is None or str(value).strip() == "":
        value = os.environ.get(fallback)

    if value is None or str(value).strip() == "":
        value = default

    return str(value).strip()


def _validate_public_editor_url(public_url: str) -> None:
    clean = str(public_url or "").strip()

    if not clean:
        raise ValueError("VECTOPLAN_EDITOR_PUBLIC_URL fehlt.")

    parsed = urlsplit(clean)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("VECTOPLAN_EDITOR_PUBLIC_URL muss eine Browser-URL mit http/https sein.")

    if not parsed.netloc:
        raise ValueError("VECTOPLAN_EDITOR_PUBLIC_URL hat keinen Host.")

    host = str(parsed.hostname or "").strip().lower()

    if host in DOCKER_INTERNAL_HOSTS:
        raise ValueError(
            "VECTOPLAN_EDITOR_PUBLIC_URL darf kein Docker-interner Host sein. "
            "Nutze z. B. http://localhost:5100."
        )


def _join_url(public_url: str, route: str) -> str:
    clean_public_url = str(public_url or "").strip()
    clean_route = str(route or "").strip()

    if not clean_public_url:
        raise ValueError("VECTOPLAN_EDITOR_PUBLIC_URL fehlt.")

    if not clean_route:
        clean_route = DEFAULT_EDITOR_ROUTE

    if not clean_route.startswith("/"):
        clean_route = "/" + clean_route

    if clean_public_url.endswith("/"):
        clean_public_url = clean_public_url[:-1]

    return clean_public_url + clean_route


def _append_query(url: str, query: Dict[str, str]) -> str:
    parts = urlsplit(url)

    existing_query = dict(parse_qsl(parts.query, keep_blank_values=False))

    clean_query: Dict[str, str] = {}

    for key, value in existing_query.items():
        if key in ALLOWED_QUERY_KEYS or key in {"source"}:
            try:
                clean_query[key] = _sanitize_query_value(key, value)
            except Exception:
                continue

    for key, value in query.items():
        if value is None or value == "":
            continue
        clean_query[str(key)] = str(value)

    encoded_query = urlencode(clean_query, doseq=False)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            encoded_query,
            parts.fragment,
        )
    )


def _json_error(*, status: int, code: str, message: str):
    response = jsonify(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
            "status": status,
        }
    )
    response.status_code = status
    _apply_no_cache_headers(response)
    return response


def _apply_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


__all__ = ["ui_editor_bp", "bp"]