# services/vectoplan-app/routes/ui/editor.py
"""
Legacy UI integration route for embedding the VECTOPLAN editor.

Current responsibility:
- Keep old editor routes alive without letting them become the primary 3D flow.
- Redirect project-scoped /ui/project/<project_id>/editor to /ui/project/<project_id>/editor3d by default.
- Build browser-facing editor URLs only for generic/chat compatibility paths.
- Keep browser/public URLs and Docker/internal URLs separated.
- Pass app-project and chunk-project context defensively when a direct editor redirect is still used.
- Normalize chunk readiness defensively: if chunk_project_id and chunk_world_id exist and status is not error/disabled,
  the editor receives chunk_ready=1 and chunk_status=ready.
- Avoid leaking Docker-internal URLs into browser redirects.

Primary project iframe route after the app refactor:
    /ui/project/<project_public_id>/editor3d

Legacy routes kept here:
    /ui/project/<project_public_id>/editor    -> redirects to /ui/project/<project_public_id>/editor3d
    /ui/chat/<chat_id>/editor                 -> direct editor compatibility redirect
    /ui/editor                                -> generic compatibility redirect, or project redirect when project refs exist
"""

from __future__ import annotations

import copy
import inspect
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, redirect, request


ui_editor_bp = Blueprint("ui_editor", __name__)
bp = ui_editor_bp

logger = logging.getLogger(__name__)


DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"
DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"
DEFAULT_EDITOR3D_GATEWAY_TEMPLATE = "/ui/project/{project_id}/editor3d"

DEFAULT_CHUNK_BROWSER_BASE_URL = "/editor/api/chunk"
DEFAULT_CHUNK_WORLD_ID = "world_spawn"

DEFAULT_CONTEXT_CACHE_TTL_SECONDS = 2.0
DEFAULT_CONFIG_CACHE_TTL_SECONDS = 5.0
DEFAULT_LEGACY_PROJECT_ROUTE_MODE = "editor3d_redirect"
DEFAULT_LEGACY_REDIRECT_CODE = 302

MAX_ID_LENGTH = 180
MAX_QUERY_VALUE_LENGTH = 512
MAX_URL_QUERY_VALUE_LENGTH = 2048
MAX_CACHE_ITEMS = 512

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")
SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,511}$")

DOCKER_INTERNAL_HOSTS = {
    "chunk",
    "editor",
    "openlayer",
    "server-chunk",
    "server-editor",
    "server-openlayer",
    "vectoplan-chunk",
    "vectoplan-editor",
    "vectoplan-openlayer",
    "vectoplan_chunk",
    "vectoplan_editor",
    "vectoplan_openlayer",
}

ALLOWED_LEGACY_PROJECT_ROUTE_MODES = {
    "editor3d_redirect",
    "redirect",
    "direct",
    "disabled",
}

BOOLEAN_QUERY_KEYS = {
    "debug",
    "readonly",
    "chunk_ready",
}

URL_QUERY_KEYS = {
    "context_url",
    "return_url",
    "app_public_url",
    "parent_origin",
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

    # Editor/chunk compatibility.
    "project_id",
    "world_id",
    "snapshot_id",
    "version_id",
    "artifact_id",

    # Explicit app references.
    "app_project_id",
    "app_project_db_id",
    "app_project_public_id",
    "project_public_id",
    "conversation_id",

    # Explicit chunk references.
    "chunk_project_id",
    "chunk_universe_id",
    "chunk_world_id",
    "chunk_ready",
    "chunk_status",

    # Browser-safe context references.
    "context_url",
    "return_url",
    "app_public_url",
    "parent_origin",

    # Later service references.
    "plan2d_id",
    "lv_id",
}

PROJECT_GATEWAY_PASSTHROUGH_KEYS = {
    "debug",
    "mode",
    "tool",
    "view",
    "readonly",
    "workspace",
}


_CONFIG_CACHE: dict[str, tuple[float, Any]] = {}
_PROJECT_CONTEXT_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}


# =============================================================================
# Routes
# =============================================================================


@ui_editor_bp.get("/ui/chat/<chat_id>/editor")
def editor_iframe(chat_id: str):
    """
    Legacy chat workspace iframe route.

    This route still redirects directly to vectoplan-editor because the chat-id
    legacy path has no guaranteed project-shell URL. If the chat/conversation is
    linked to an app Project, chunk context is attached to the editor URL.
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
        _apply_legacy_headers(
            response,
            route_kind="chat_editor_legacy_direct",
            target=editor_url,
        )
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
@ui_editor_bp.get("/ui/project/<project_id>/editor/")
def project_editor_iframe(project_id: str):
    """
    Legacy project editor route.

    The project-first shell now uses /ui/project/<project_id>/editor3d.
    This route therefore redirects to that gateway by default.

    A direct editor redirect remains available behind configuration:
        VECTOPLAN_UI_EDITOR_LEGACY_PROJECT_ROUTE_MODE=direct

    Supported modes:
        editor3d_redirect | redirect | direct | disabled
    """
    try:
        safe_project_id = _sanitize_id(project_id, label="project_id")
        mode = _legacy_project_route_mode()

        if mode == "disabled":
            return _json_error(
                status=410,
                code="legacy_project_editor_route_disabled",
                message="Die alte Editor-Route ist deaktiviert. Verwende /ui/project/<project_id>/editor3d.",
            )

        if mode == "direct":
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
            _apply_legacy_headers(
                response,
                route_kind="project_editor_legacy_direct",
                target=editor_url,
            )
            return response

        target_url = _build_editor3d_gateway_url(safe_project_id)
        response = redirect(target_url, code=_legacy_redirect_code())
        _apply_no_cache_headers(response)
        _apply_legacy_headers(
            response,
            route_kind="project_editor_legacy_redirect",
            target=target_url,
        )
        return response

    except ValueError as exc:
        logger.warning("Invalid project editor iframe request: %s", exc)
        return _json_error(
            status=400,
            code="invalid_project_editor_request",
            message=str(exc),
        )

    except Exception:
        logger.exception("Failed to handle legacy project editor route for project_id=%r", project_id)
        return _json_error(
            status=500,
            code="editor_gateway_failed",
            message="Editor-Gateway konnte nicht erzeugt werden.",
        )


@ui_editor_bp.get("/ui/editor")
@ui_editor_bp.get("/ui/editor/")
def editor_iframe_without_chat():
    """
    Generic compatibility route.

    If a project reference is present and project redirect is enabled, this route
    redirects into the project editor3d gateway. Without project reference, it
    remains a direct editor compatibility redirect.
    """
    try:
        project_identifier = _project_identifier_from_request()

        if project_identifier and _generic_project_redirect_enabled():
            safe_project_id = _sanitize_id(project_identifier, label="project_identifier")
            target_url = _build_editor3d_gateway_url(safe_project_id)
            response = redirect(target_url, code=_legacy_redirect_code())
            _apply_no_cache_headers(response)
            _apply_legacy_headers(
                response,
                route_kind="generic_editor_project_redirect",
                target=target_url,
            )
            return response

        editor_url = _build_editor_url(
            chat_id=None,
            project_identifier=project_identifier,
        )

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        _apply_legacy_headers(
            response,
            route_kind="generic_editor_direct",
            target=editor_url,
        )
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


@ui_editor_bp.get("/ui/editor/_status")
@ui_editor_bp.get("/ui/project/editor/_status")
def editor_ui_route_status():
    try:
        payload = {
            "ok": True,
            "service": "vectoplan-app",
            "route": "routes.ui.editor",
            "module": "services/vectoplan-app/routes/ui/editor.py",
            "legacy_project_route_mode": _legacy_project_route_mode(),
            "generic_project_redirect_enabled": _generic_project_redirect_enabled(),
            "editor_public_url": _editor_public_url(),
            "editor_route": _editor_route(),
            "editor3d_gateway_template": _editor3d_gateway_template(),
            "chunk_default_world_id": DEFAULT_CHUNK_WORLD_ID,
            "cache": {
                "config_items": len(_CONFIG_CACHE),
                "project_context_items": len(_PROJECT_CONTEXT_CACHE),
                "context_cache_ttl_seconds": _project_context_cache_ttl_seconds(),
                "config_cache_ttl_seconds": _config_cache_ttl_seconds(),
            },
            "notes": [
                "Project-scoped legacy /editor routes redirect to /editor3d by default.",
                "Direct editor redirects use public editor URLs only.",
                "Chunk IDs are passed separately from app project IDs.",
                "If chunk_project_id and chunk_world_id exist, chunk_status is normalized to ready unless error/disabled.",
            ],
        }
        response = jsonify(payload)
        _apply_no_cache_headers(response)
        return response
    except Exception:
        logger.exception("Failed to build editor UI route status")
        return _json_error(
            status=500,
            code="editor_ui_status_failed",
            message="Editor-UI-Status konnte nicht erzeugt werden.",
        )


# =============================================================================
# Public URL / Gateway builders
# =============================================================================


def _build_editor3d_gateway_url(project_id: str) -> str:
    safe_project_id = _sanitize_id(project_id, label="project_id")
    template = _editor3d_gateway_template()

    try:
        path = template.format(project_id=quote(safe_project_id, safe=""))
    except Exception:
        path = DEFAULT_EDITOR3D_GATEWAY_TEMPLATE.format(project_id=quote(safe_project_id, safe=""))

    if not path.startswith("/"):
        path = "/" + path

    passthrough = _gateway_passthrough_query()

    return _append_query(path, passthrough) if passthrough else path


def _build_editor_url(
    *,
    chat_id: Optional[str],
    project_identifier: Optional[str] = None,
    preloaded_project_context: Optional[Dict[str, Any]] = None,
) -> str:
    public_url = _editor_public_url()
    editor_route = _editor_route()

    _validate_public_editor_url(public_url)

    base_url = _join_url(public_url, editor_route)
    project_context = (
        copy.deepcopy(preloaded_project_context)
        if isinstance(preloaded_project_context, dict)
        else _resolve_project_context(chat_id=chat_id, project_identifier=project_identifier)
    )
    query = _build_query(chat_id=chat_id, project_context=project_context)

    return _append_query(base_url, query)


def _build_query(*, chat_id: Optional[str], project_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    query: Dict[str, str] = {
        "embed": "1",
        "source": "vectoplan-app",
        "workspace": "editor3d",
    }

    if chat_id:
        query["chat_id"] = _sanitize_id(chat_id, label="chat_id")

    # User-provided safe editor options. App/chunk context overrides these below.
    for key in sorted(ALLOWED_QUERY_KEYS):
        if key in {
            "chat_id",
            "embed",
            "source",
            "workspace",
            "project_id",
            "world_id",
            "app_project_id",
            "app_project_db_id",
            "app_project_public_id",
            "project_public_id",
            "conversation_id",
            "chunk_project_id",
            "chunk_universe_id",
            "chunk_world_id",
            "chunk_ready",
            "chunk_status",
            "context_url",
            "return_url",
            "app_public_url",
        }:
            continue

        value = request.args.get(key)
        if value is None or value == "":
            continue

        cleaned = _sanitize_query_value(key, value)
        if cleaned:
            query[key] = cleaned

    ctx = project_context if isinstance(project_context, dict) else {}
    normalized_chunk = _normalize_chunk_context(ctx)

    app_project_db_id = _safe_optional_id(ctx.get("app_project_db_id") or ctx.get("app_project_id_db"))
    app_project_public_id = _safe_optional_id(
        ctx.get("app_project_public_id")
        or ctx.get("project_public_id")
        or ctx.get("public_id")
    )
    conversation_id = _safe_optional_id(ctx.get("conversation_id"))

    chunk_project_id = _safe_optional_id(normalized_chunk.get("chunk_project_id"))
    chunk_universe_id = _safe_optional_id(normalized_chunk.get("chunk_universe_id"))
    chunk_world_id = _safe_optional_id(normalized_chunk.get("chunk_world_id")) or DEFAULT_CHUNK_WORLD_ID
    chunk_status = _safe_optional_status(normalized_chunk.get("chunk_status"))
    chunk_ready = _as_bool(normalized_chunk.get("chunk_ready"), bool(chunk_project_id and chunk_world_id))

    plan2d_id = _safe_optional_id(ctx.get("plan2d_id"))
    lv_id = _safe_optional_id(ctx.get("lv_id"))

    if app_project_db_id:
        query["app_project_db_id"] = app_project_db_id

    if app_project_public_id:
        query["app_project_id"] = app_project_public_id
        query["app_project_public_id"] = app_project_public_id
        query["project_public_id"] = app_project_public_id

        context_url = _build_app_project_context_url(app_project_public_id)
        return_url = _build_app_project_return_url(app_project_public_id)
        app_public_url = _app_public_url()

        if context_url:
            query["context_url"] = context_url
        if return_url:
            query["return_url"] = return_url
        if app_public_url:
            query["app_public_url"] = app_public_url

    if conversation_id:
        query["conversation_id"] = conversation_id
        query.setdefault("chat_id", conversation_id)

    if chunk_project_id:
        query["chunk_project_id"] = chunk_project_id

        # Critical compatibility: old editor bootstrap/runtime paths still read project_id.
        query["project_id"] = chunk_project_id

    if chunk_universe_id:
        query["chunk_universe_id"] = chunk_universe_id

    if chunk_world_id:
        query["chunk_world_id"] = chunk_world_id

        # Critical compatibility: old editor bootstrap/runtime paths still read world_id.
        query["world_id"] = chunk_world_id

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

    return _sanitize_query_mapping(query)


def _gateway_passthrough_query() -> Dict[str, str]:
    query: Dict[str, str] = {}

    try:
        for key in sorted(PROJECT_GATEWAY_PASSTHROUGH_KEYS):
            value = request.args.get(key)
            if value is None or value == "":
                continue

            cleaned = _sanitize_query_value(key, value)
            if cleaned:
                query[key] = cleaned

        query["legacy_editor_redirect"] = "1"
        return query

    except Exception:
        return {}


# =============================================================================
# Project / chunk context resolution
# =============================================================================


def _resolve_project_context(
    *,
    chat_id: Optional[str],
    project_identifier: Optional[str],
) -> Dict[str, Any]:
    cache_key = _project_context_cache_key(chat_id=chat_id, project_identifier=project_identifier)

    cached = _cache_get(_PROJECT_CONTEXT_CACHE, cache_key, ttl_seconds=_project_context_cache_ttl_seconds())
    if isinstance(cached, dict):
        return copy.deepcopy(cached)

    context = _resolve_project_context_uncached(
        chat_id=chat_id,
        project_identifier=project_identifier,
    )

    if isinstance(context, dict):
        _cache_set(_PROJECT_CONTEXT_CACHE, cache_key, context)
        return copy.deepcopy(context)

    return {}


def _resolve_project_context_uncached(
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
                    if project is None and str(identifier).isdigit():
                        project = Project.query.filter_by(id=int(identifier)).one_or_none()
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
            _safe_refresh_model(project)

        app_project_db_id = _safe_optional_id(getattr(project, "id", None))
        app_project_public_id = _safe_optional_id(getattr(project, "public_id", None)) or app_project_db_id
        conversation_id = _safe_optional_id(getattr(project, "conversation_id", None)) or _safe_optional_id(chat_id)

        chunk_context = _extract_project_chunk_context(project)
        chunk_context = _normalize_chunk_context(chunk_context)

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
    Extract chunk context from direct model fields, metadata, service refs and to_dict().

    Defensive rule:
    If chunk_project_id and chunk_world_id exist, the context is usable. A stale
    stored "pending" status must not block the editor from loading chunks.
    """
    result: Dict[str, Any] = {
        "chunk_project_id": "",
        "chunk_universe_id": "",
        "chunk_world_id": "",
        "chunk_status": "pending",
        "chunk_ready": False,
        "chunk_route_hints": {},
    }

    payload: Dict[str, Any] = {}

    try:
        if hasattr(project, "to_dict"):
            try:
                payload = project.to_dict(include_private=True, include_refs=True)
            except TypeError:
                payload = project.to_dict()
            except Exception:
                payload = {}

        if not isinstance(payload, dict):
            payload = {}

    except Exception:
        payload = {}

    candidates: list[Mapping[str, Any]] = []

    try:
        direct_candidate = {
            "chunk_project_id": getattr(project, "chunk_project_id", None),
            "chunk_universe_id": getattr(project, "chunk_universe_id", None),
            "chunk_world_id": getattr(project, "chunk_world_id", None),
            "chunk_status": getattr(project, "chunk_status", None),
            "chunk_ready": getattr(project, "chunk_ready", None),
            "chunk_route_hints": getattr(project, "chunk_route_hints", None),
        }
        candidates.append(direct_candidate)
    except Exception:
        pass

    try:
        candidates.extend(_chunk_context_candidates_from_payload(payload))
    except Exception:
        pass

    for candidate in candidates:
        try:
            _merge_chunk_candidate(result, candidate)
        except Exception:
            continue

    return _normalize_chunk_context(result)


def _chunk_context_candidates_from_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []

    try:
        candidates.append(payload)

        chunk = _safe_dict(payload.get("chunk"))
        if chunk:
            candidates.append(chunk)

        metadata = _safe_dict(payload.get("metadata") or payload.get("metadata_json") or payload.get("metadataJson"))
        metadata_chunk = _safe_dict(metadata.get("chunk"))
        if metadata_chunk:
            candidates.append(metadata_chunk)

        service_refs = _safe_dict(payload.get("service_refs") or payload.get("serviceRefs"))
        service_chunk = _safe_dict(service_refs.get("chunk"))
        if service_chunk:
            candidates.append(service_chunk)

        service_links = payload.get("service_links") or payload.get("serviceLinks") or []
        if isinstance(service_links, list):
            for link in service_links:
                if not isinstance(link, dict):
                    continue

                service_name = str(link.get("service") or link.get("service_name") or link.get("serviceName") or "").lower()
                if service_name and service_name != "chunk":
                    continue

                link_type = str(link.get("resource_type") or link.get("resourceType") or "").lower()
                link_metadata = _safe_dict(link.get("metadata"))
                link_ref = _safe_dict(link.get("resource_ref") or link.get("resourceRef") or link.get("reference"))
                link_chunk = _safe_dict(link.get("chunk") or link_metadata.get("chunk") or link_ref.get("chunk"))

                if link_type == "chunk_project":
                    candidates.append(
                        {
                            "chunk_project_id": link.get("resource_id")
                            or link.get("resourceId")
                            or link.get("external_id")
                            or link.get("externalId")
                            or link.get("chunk_project_id")
                            or link.get("chunkProjectId"),
                            "chunk_universe_id": link_metadata.get("chunk_universe_id")
                            or link_metadata.get("chunkUniverseId")
                            or link.get("chunk_universe_id")
                            or link.get("chunkUniverseId"),
                            "chunk_world_id": link_metadata.get("chunk_world_id")
                            or link_metadata.get("chunkWorldId")
                            or link.get("chunk_world_id")
                            or link.get("chunkWorldId"),
                            "chunk_status": link.get("status") or link_metadata.get("status"),
                            "chunk_ready": link.get("chunk_ready")
                            or link.get("chunkReady")
                            or link_metadata.get("ready"),
                            "chunk_route_hints": link_metadata.get("route_hints")
                            or link_metadata.get("routeHints")
                            or link_ref.get("routeHints"),
                        }
                    )

                elif link_type in {"world", "chunk_world"}:
                    candidates.append(
                        {
                            "chunk_project_id": link_metadata.get("chunk_project_id")
                            or link_metadata.get("chunkProjectId")
                            or link_ref.get("external_project_id")
                            or link_ref.get("externalProjectId")
                            or link.get("external_project_id")
                            or link.get("externalProjectId"),
                            "chunk_universe_id": link_metadata.get("chunk_universe_id")
                            or link_metadata.get("chunkUniverseId"),
                            "chunk_world_id": link.get("resource_id")
                            or link.get("resourceId")
                            or link.get("external_id")
                            or link.get("externalId")
                            or link_metadata.get("chunk_world_id")
                            or link_metadata.get("chunkWorldId"),
                            "chunk_status": link.get("status") or link_metadata.get("status"),
                            "chunk_ready": link.get("chunk_ready")
                            or link.get("chunkReady")
                            or link_metadata.get("ready"),
                            "chunk_route_hints": link_metadata.get("route_hints")
                            or link_metadata.get("routeHints")
                            or link_ref.get("routeHints"),
                        }
                    )

                if link_chunk:
                    candidates.append(link_chunk)

    except Exception:
        pass

    return candidates


def _merge_chunk_candidate(result: Dict[str, Any], candidate: Mapping[str, Any]) -> None:
    if not isinstance(candidate, Mapping):
        return

    chunk_project_id = _first_non_empty(
        result.get("chunk_project_id"),
        candidate.get("chunk_project_id"),
        candidate.get("chunkProjectId"),
        candidate.get("project_id"),
        candidate.get("projectId"),
    )
    chunk_universe_id = _first_non_empty(
        result.get("chunk_universe_id"),
        candidate.get("chunk_universe_id"),
        candidate.get("chunkUniverseId"),
        candidate.get("universe_id"),
        candidate.get("universeId"),
    )
    chunk_world_id = _first_non_empty(
        result.get("chunk_world_id"),
        candidate.get("chunk_world_id"),
        candidate.get("chunkWorldId"),
        candidate.get("world_id"),
        candidate.get("worldId"),
    )
    chunk_status = _safe_optional_status(
        _first_non_empty(
            result.get("chunk_status"),
            candidate.get("chunk_status"),
            candidate.get("chunkStatus"),
            candidate.get("status"),
        )
    )
    chunk_ready_raw = _first_non_empty(
        result.get("chunk_ready"),
        candidate.get("chunk_ready"),
        candidate.get("chunkReady"),
        candidate.get("ready"),
    )
    route_hints = (
        _safe_dict(result.get("chunk_route_hints"))
        or _safe_dict(candidate.get("chunk_route_hints"))
        or _safe_dict(candidate.get("chunkRouteHints"))
        or _safe_dict(candidate.get("route_hints"))
        or _safe_dict(candidate.get("routeHints"))
    )

    result.update(
        {
            "chunk_project_id": _safe_optional_id(chunk_project_id),
            "chunk_universe_id": _safe_optional_id(chunk_universe_id),
            "chunk_world_id": _safe_optional_id(chunk_world_id),
            "chunk_status": chunk_status or result.get("chunk_status") or "pending",
            "chunk_ready": _as_bool(chunk_ready_raw, bool(result.get("chunk_ready"))),
            "chunk_route_hints": route_hints,
        }
    )


def _normalize_chunk_context(context: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(context) if isinstance(context, Mapping) else {}

    chunk_project_id = _safe_optional_id(
        _first_non_empty(
            result.get("chunk_project_id"),
            result.get("chunkProjectId"),
            result.get("project_id"),
            result.get("projectId"),
        )
    )
    chunk_universe_id = _safe_optional_id(
        _first_non_empty(
            result.get("chunk_universe_id"),
            result.get("chunkUniverseId"),
            result.get("universe_id"),
            result.get("universeId"),
        )
    )
    chunk_world_id = _safe_optional_id(
        _first_non_empty(
            result.get("chunk_world_id"),
            result.get("chunkWorldId"),
            result.get("world_id"),
            result.get("worldId"),
        )
    )

    if chunk_project_id and not chunk_world_id:
        chunk_world_id = DEFAULT_CHUNK_WORLD_ID

    raw_status = _safe_optional_status(
        _first_non_empty(
            result.get("chunk_status"),
            result.get("chunkStatus"),
            result.get("status"),
        )
    )
    explicit_ready = _as_bool(
        _first_non_empty(
            result.get("chunk_ready"),
            result.get("chunkReady"),
            result.get("ready"),
        ),
        False,
    )

    if raw_status in {"error", "disabled"}:
        chunk_status = raw_status
        chunk_ready = False
    elif chunk_project_id and chunk_world_id:
        chunk_status = "ready"
        chunk_ready = True
    else:
        chunk_status = raw_status or "pending"
        chunk_ready = explicit_ready and bool(chunk_project_id and chunk_world_id)

    route_hints = (
        _safe_dict(result.get("chunk_route_hints"))
        or _safe_dict(result.get("chunkRouteHints"))
        or _safe_dict(result.get("route_hints"))
        or _safe_dict(result.get("routeHints"))
    )

    if not route_hints and chunk_project_id and chunk_world_id:
        route_hints = _build_chunk_route_hints(
            chunk_project_id=chunk_project_id,
            chunk_world_id=chunk_world_id,
            chunk_universe_id=chunk_universe_id,
        )

    result.update(
        {
            "chunk_project_id": chunk_project_id,
            "chunkProjectId": chunk_project_id,
            "chunk_universe_id": chunk_universe_id,
            "chunkUniverseId": chunk_universe_id,
            "chunk_world_id": chunk_world_id,
            "chunkWorldId": chunk_world_id,
            "chunk_status": chunk_status,
            "chunkStatus": chunk_status,
            "chunk_ready": chunk_ready,
            "chunkReady": chunk_ready,
            "chunk_route_hints": route_hints,
            "chunkRouteHints": route_hints,
        }
    )

    return result


def _build_chunk_route_hints(
    *,
    chunk_project_id: str,
    chunk_world_id: str,
    chunk_universe_id: str = "",
) -> Dict[str, str]:
    try:
        api_base = _normalize_route_path(
            _config_value(
                primary="VECTOPLAN_EDITOR_CHUNK_API_PREFIX",
                fallback="EDITOR_CHUNK_API_PREFIX",
                default=DEFAULT_CHUNK_BROWSER_BASE_URL,
            ),
            DEFAULT_CHUNK_BROWSER_BASE_URL,
        )

        project_base = _join_route_path(api_base, "projects", chunk_project_id)
        world_base = _join_route_path(project_base, "worlds", chunk_world_id)

        return {
            "apiBaseUrl": api_base,
            "browserBaseUrl": api_base,
            "status": _join_route_path(api_base, "_status"),
            "testConnection": _join_route_path(api_base, "_test", "connection"),
            "placeableBlocks": _join_route_path(api_base, "placeable-blocks"),
            "projects": _join_route_path(api_base, "projects"),
            "project": project_base,
            "projectBootstrap": _join_route_path(project_base, "bootstrap"),
            "universes": _join_route_path(project_base, "universes"),
            "universe": _join_route_path(project_base, "universes", chunk_universe_id) if chunk_universe_id else "",
            "worlds": _join_route_path(project_base, "worlds"),
            "world": world_base,
            "blocks": _join_route_path(world_base, "blocks"),
            "chunk": _join_route_path(world_base, "chunks"),
            "chunks": _join_route_path(world_base, "chunks"),
            "chunksBatch": _join_route_path(world_base, "chunks", "batch"),
            "commands": _join_route_path(world_base, "commands"),
        }
    except Exception:
        return {}


def _should_retry_chunk_on_editor_open(project: Any) -> bool:
    try:
        enabled = _config_bool(
            primary="VECTOPLAN_CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
            fallback="CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
            default=True,
        )
        if not enabled or project is None:
            return False

        if bool(getattr(project, "is_deleted", False)):
            return False

        context = _extract_project_chunk_context(project)
        chunk_project_id = _safe_optional_id(context.get("chunk_project_id"))
        chunk_world_id = _safe_optional_id(context.get("chunk_world_id"))
        status = _safe_optional_status(context.get("chunk_status"))

        if not chunk_project_id or not chunk_world_id:
            return True

        retry_pending = _config_bool(
            primary="VECTOPLAN_CHUNK_PROVISION_RETRY_PENDING_ON_WORKSPACE_OPEN",
            fallback="CHUNK_PROVISION_RETRY_PENDING_ON_WORKSPACE_OPEN",
            default=False,
        )

        return bool(retry_pending and status == "pending")

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

        kwargs = {
            "project": project,
            "user_id": get_current_user_id(),
            "force": False,
            "commit": True,
        }

        _call_with_supported_kwargs(ensure_project_chunk_link, kwargs)

    except Exception:
        logger.warning("Chunk provisioning retry on editor open failed", exc_info=True)


# =============================================================================
# Config / cache helpers
# =============================================================================


def _legacy_project_route_mode() -> str:
    mode = _config_text(
        primary="VECTOPLAN_UI_EDITOR_LEGACY_PROJECT_ROUTE_MODE",
        fallback="UI_EDITOR_LEGACY_PROJECT_ROUTE_MODE",
        default=DEFAULT_LEGACY_PROJECT_ROUTE_MODE,
    ).strip().lower().replace("-", "_").replace(" ", "_")

    if mode == "redirect":
        return "editor3d_redirect"

    if mode in ALLOWED_LEGACY_PROJECT_ROUTE_MODES:
        return mode

    return DEFAULT_LEGACY_PROJECT_ROUTE_MODE


def _legacy_redirect_code() -> int:
    code = _config_int(
        primary="VECTOPLAN_UI_EDITOR_LEGACY_REDIRECT_CODE",
        fallback="UI_EDITOR_LEGACY_REDIRECT_CODE",
        default=DEFAULT_LEGACY_REDIRECT_CODE,
        minimum=301,
        maximum=308,
    )

    return code if code in {301, 302, 303, 307, 308} else DEFAULT_LEGACY_REDIRECT_CODE


def _generic_project_redirect_enabled() -> bool:
    return _config_bool(
        primary="VECTOPLAN_UI_EDITOR_GENERIC_PROJECT_REDIRECT_ENABLED",
        fallback="UI_EDITOR_GENERIC_PROJECT_REDIRECT_ENABLED",
        default=True,
    )


def _editor_public_url() -> str:
    return _config_text(
        primary="VECTOPLAN_EDITOR_PUBLIC_URL",
        fallback="EDITOR_PUBLIC_URL",
        default=DEFAULT_EDITOR_PUBLIC_URL,
    )


def _app_public_url() -> str:
    value = _config_text(
        primary="VECTOPLAN_APP_PUBLIC_URL",
        fallback="APP_PUBLIC_URL",
        default=DEFAULT_APP_PUBLIC_URL,
    )
    return _normalize_external_url(value, DEFAULT_APP_PUBLIC_URL)


def _editor_route() -> str:
    return _normalize_route_path(
        _config_text(
            primary="VECTOPLAN_EDITOR_ROUTE",
            fallback="EDITOR_ROUTE",
            default=DEFAULT_EDITOR_ROUTE,
        ),
        DEFAULT_EDITOR_ROUTE,
    )


def _editor3d_gateway_template() -> str:
    return _config_text(
        primary="VECTOPLAN_UI_EDITOR3D_GATEWAY_TEMPLATE",
        fallback="UI_EDITOR3D_GATEWAY_TEMPLATE",
        default=DEFAULT_EDITOR3D_GATEWAY_TEMPLATE,
    )


def _project_context_cache_ttl_seconds() -> float:
    return _config_float(
        primary="VECTOPLAN_UI_EDITOR_PROJECT_CONTEXT_CACHE_TTL_SECONDS",
        fallback="UI_EDITOR_PROJECT_CONTEXT_CACHE_TTL_SECONDS",
        default=DEFAULT_CONTEXT_CACHE_TTL_SECONDS,
        minimum=0.0,
        maximum=60.0,
    )


def _config_cache_ttl_seconds() -> float:
    return _config_float(
        primary="VECTOPLAN_UI_EDITOR_CONFIG_CACHE_TTL_SECONDS",
        fallback="UI_EDITOR_CONFIG_CACHE_TTL_SECONDS",
        default=DEFAULT_CONFIG_CACHE_TTL_SECONDS,
        minimum=0.0,
        maximum=300.0,
    )


def _config_bool(*, primary: str, fallback: str, default: bool = False) -> bool:
    return _as_bool(_config_value(primary=primary, fallback=fallback, default="1" if default else "0"), default)


def _config_text(*, primary: str, fallback: str, default: str = "") -> str:
    return str(_config_value(primary=primary, fallback=fallback, default=default)).strip()


def _config_int(
    *,
    primary: str,
    fallback: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        raw = _config_value(primary=primary, fallback=fallback, default=str(default))
        value = int(float(str(raw).strip()))

        if minimum is not None:
            value = max(minimum, value)

        if maximum is not None:
            value = min(maximum, value)

        return value
    except Exception:
        return default


def _config_float(
    *,
    primary: str,
    fallback: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        raw = _config_value(primary=primary, fallback=fallback, default=str(default))
        value = float(str(raw).strip())

        if minimum is not None:
            value = max(minimum, value)

        if maximum is not None:
            value = min(maximum, value)

        return value
    except Exception:
        return default


def _config_value(*, primary: str, fallback: str, default: str) -> str:
    cache_key = f"{primary}|{fallback}|{default}"
    cached = _cache_get(_CONFIG_CACHE, cache_key, ttl_seconds=_config_cache_ttl_seconds_raw())
    if isinstance(cached, str):
        return cached

    value = None

    try:
        value = current_app.config.get(primary)
    except RuntimeError:
        value = None
    except Exception:
        value = None

    if value is None or str(value).strip() == "":
        try:
            value = current_app.config.get(fallback)
        except RuntimeError:
            value = None
        except Exception:
            value = None

    if value is None or str(value).strip() == "":
        value = os.environ.get(primary)

    if value is None or str(value).strip() == "":
        value = os.environ.get(fallback)

    if value is None or str(value).strip() == "":
        value = default

    result = str(value).strip()
    _cache_set(_CONFIG_CACHE, cache_key, result)
    return result


def _config_cache_ttl_seconds_raw() -> float:
    try:
        raw = os.environ.get("VECTOPLAN_UI_EDITOR_CONFIG_CACHE_TTL_SECONDS") or os.environ.get("UI_EDITOR_CONFIG_CACHE_TTL_SECONDS")
        if raw is None or str(raw).strip() == "":
            return DEFAULT_CONFIG_CACHE_TTL_SECONDS
        return max(0.0, min(300.0, float(str(raw).strip())))
    except Exception:
        return DEFAULT_CONFIG_CACHE_TTL_SECONDS


def _project_context_cache_key(*, chat_id: Optional[str], project_identifier: Optional[str]) -> str:
    return "|".join(
        (
            "project_context",
            _safe_optional_id(project_identifier),
            _safe_optional_id(chat_id),
        )
    )


def _cache_get(cache: dict[str, tuple[float, Any]], key: str, *, ttl_seconds: float) -> Any:
    try:
        if ttl_seconds <= 0:
            return None

        entry = cache.get(key)
        if not entry:
            return None

        created_at, value = entry
        if time.monotonic() - created_at > ttl_seconds:
            cache.pop(key, None)
            return None

        return copy.deepcopy(value)

    except Exception:
        return None


def _cache_set(cache: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
    try:
        if len(cache) > MAX_CACHE_ITEMS:
            _cache_prune(cache)

        cache[key] = (time.monotonic(), copy.deepcopy(value))
    except Exception:
        pass


def _cache_prune(cache: dict[str, tuple[float, Any]]) -> None:
    try:
        items = sorted(cache.items(), key=lambda item: item[1][0])
        excess = max(0, len(items) - (MAX_CACHE_ITEMS // 2))
        for key, _ in items[:excess]:
            cache.pop(key, None)
    except Exception:
        cache.clear()


def clear_ui_editor_cache() -> None:
    _CONFIG_CACHE.clear()
    _PROJECT_CONTEXT_CACHE.clear()


# =============================================================================
# URL / query helpers
# =============================================================================


def _build_app_project_context_url(app_project_public_id: str) -> str:
    try:
        safe_project_id = _sanitize_id(app_project_public_id, label="app_project_public_id")
        return _join_url(
            _app_public_url(),
            f"/ui/project/{quote(safe_project_id, safe='')}/context.json",
        )
    except Exception:
        return ""


def _build_app_project_return_url(app_project_public_id: str) -> str:
    try:
        safe_project_id = _sanitize_id(app_project_public_id, label="app_project_public_id")
        return _join_url(
            _app_public_url(),
            f"/project={quote(safe_project_id, safe='')}",
        )
    except Exception:
        return ""


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


def _normalize_external_url(value: str, default: str) -> str:
    try:
        clean = str(value or "").strip().rstrip("/")
        if not clean:
            clean = default

        parsed = urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return default.rstrip("/")

        host = str(parsed.hostname or "").lower()
        if host in DOCKER_INTERNAL_HOSTS:
            return default.rstrip("/")

        return clean
    except Exception:
        return default.rstrip("/")


def _join_url(public_url: str, route: str) -> str:
    clean_public_url = str(public_url or "").strip()
    clean_route = str(route or "").strip()

    if not clean_public_url:
        raise ValueError("Basis-URL fehlt.")

    if not clean_route:
        clean_route = "/"

    if not clean_route.startswith("/"):
        clean_route = "/" + clean_route

    if clean_public_url.endswith("/"):
        clean_public_url = clean_public_url[:-1]

    return clean_public_url + clean_route


def _join_route_path(*parts: str) -> str:
    cleaned: list[str] = []

    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        cleaned.append(text.strip("/"))

    return "/" + "/".join(cleaned) if cleaned else "/"


def _normalize_route_path(value: Any, default: str) -> str:
    try:
        text = str(value or default or "").strip()
        if not text:
            text = default

        if not text.startswith("/"):
            text = "/" + text

        while "//" in text:
            text = text.replace("//", "/")

        if len(text) > 1:
            text = text.rstrip("/")

        return text
    except Exception:
        return default


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

        if key in ALLOWED_QUERY_KEYS or key in {"source", "legacy_editor_redirect"}:
            try:
                clean_query[str(key)] = _sanitize_query_value(str(key), str(value))
            except Exception:
                continue

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


def _sanitize_query_mapping(query: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}

    for key, value in query.items():
        try:
            if value is None or value == "":
                continue
            cleaned = _sanitize_query_value(str(key), value)
            if cleaned:
                result[str(key)] = cleaned
        except Exception:
            continue

    return result


def _sanitize_query_value(key: str, value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if key in URL_QUERY_KEYS:
        return _sanitize_url_query_value(key, text)

    if len(text) > MAX_QUERY_VALUE_LENGTH:
        raise ValueError(f"Query-Parameter '{key}' ist zu lang.")

    if key in BOOLEAN_QUERY_KEYS:
        return "1" if text.lower() in {"1", "true", "yes", "on", "y", "ja", "enabled"} else "0"

    if key == "chunk_status":
        status = _safe_optional_status(text)
        if not status:
            raise ValueError(f"Query-Parameter '{key}' enthält ungültige Zeichen.")
        return status

    if key == "legacy_editor_redirect":
        return "1" if text.lower() in {"1", "true", "yes", "on", "y", "ja", "enabled"} else "0"

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
        "chat_id",
        "embed",
    }:
        if not SAFE_QUERY_RE.match(text):
            raise ValueError(f"Query-Parameter '{key}' enthält ungültige Zeichen.")

    return text


def _sanitize_url_query_value(key: str, value: str) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if len(text) > MAX_URL_QUERY_VALUE_LENGTH:
        raise ValueError(f"Query-Parameter '{key}' ist zu lang.")

    try:
        if text.startswith("/"):
            return text

        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""

        host = str(parsed.hostname or "").strip().lower()
        if host in DOCKER_INTERNAL_HOSTS:
            return ""

        return text
    except Exception:
        return ""


# =============================================================================
# Generic helpers
# =============================================================================


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
            "available": "ready",
            "ready": "ready",
            "failed": "error",
            "failure": "error",
            "unavailable": "error",
            "waiting": "pending",
            "queued": "pending",
            "pending": "pending",
            "off": "disabled",
            "disabled": "disabled",
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


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, int) and not isinstance(value, bool):
            return bool(value)

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "ready"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "pending", "error"}:
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


def _safe_refresh_model(model: Any) -> None:
    try:
        from models import db

        db.session.refresh(model)
    except Exception:
        pass


def _call_with_supported_kwargs(callback: Callable[..., Any], kwargs: Mapping[str, Any]) -> Any:
    kwargs_dict = dict(kwargs)

    try:
        signature = inspect.signature(callback)
    except Exception:
        try:
            return callback(**kwargs_dict)
        except TypeError:
            return callback()

    parameters = signature.parameters
    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    if accepts_var_kwargs:
        return callback(**kwargs_dict)

    supported_kwargs: Dict[str, Any] = {}

    for name, parameter in parameters.items():
        if (
            parameter.kind in {
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
            and name in kwargs_dict
        ):
            supported_kwargs[name] = kwargs_dict[name]

    try:
        return callback(**supported_kwargs)
    except TypeError:
        return callback()


# =============================================================================
# Responses / headers
# =============================================================================


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


def _apply_legacy_headers(response, *, route_kind: str, target: str):
    try:
        response.headers["X-VECTOPLAN-App-Editor-Route"] = route_kind
        response.headers["X-VECTOPLAN-App-Editor-Target"] = str(target or "")
        response.headers["X-VECTOPLAN-App-Editor-Legacy"] = "true"
        response.headers["X-VECTOPLAN-App-Editor-Primary-Gateway"] = "editor3d"
    except Exception:
        pass
    return response


__all__ = [
    "ui_editor_bp",
    "bp",
    "clear_ui_editor_cache",
]