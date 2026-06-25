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
    - Team-/Rollenverwaltung
    - Einladungen per registrierter E-Mail
    - Veröffentlichte Workspace-Reiter
    - Embed-/Publication-Policy
    - Chunk-Referenzen
    - Demo-/Auth-Kontext

Wichtige Architekturregeln:
- vectoplan-app erzeugt KEINE echten Benutzeraccounts.
- Login, Registrierung, Account-Typ, Abo-Status und Bigdata-Zugriff liegen
  später im Auth-/Registrierungsdienst.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Veröffentlichungen und
  Projektfrontend.
- Diese API speichert keine Chunk-Daten.
- Diese API speichert keine 3D-Welt-Wahrheit.
- Diese API speichert keine 2D-Geometrie.
- Diese API speichert keine LV-Fachdaten.
- Systemreferenzen sind API-/Admin-Daten und nicht normales Projektformular.
"""

from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, request
from werkzeug.wrappers import Response

from services.current_user import (
    ensure_default_user,
    get_current_user_context,
    get_current_user_id,
    get_current_user_id_optional,
    get_current_user_status,
)

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
    list_project_sidebar_items,
    list_project_versions,
    list_projects_result,
    resolve_project,
    revoke_project_member,
    serialize_project,
    serialize_project_sidebar_item,
    set_project_member_role,
    transfer_project_owner,
    update_project_embed_policy,
    update_project_result,
    upsert_project_service_link,
)

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

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable"}:
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


def _current_user_context_dict(*, ensure: bool = False) -> Dict[str, Any]:
    try:
        context = get_current_user_context(ensure=ensure)
        if hasattr(context, "to_dict"):
            return _safe_dict(context.to_dict())
        return _safe_dict(context)
    except Exception:
        return {
            "user_id": get_current_user_id(),
            "authenticated": True,
            "demo_mode": False,
            "persistent": True,
            "source": "route_fallback",
        }


def _current_user_id_optional() -> Optional[int]:
    try:
        value = get_current_user_id_optional()
        parsed = _safe_int(value, 0)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _current_user_id_for_legacy() -> int:
    try:
        optional = _current_user_id_optional()
        if optional:
            return optional
        return get_current_user_id()
    except Exception:
        return 1


def _current_auth_user_id() -> Optional[str]:
    try:
        context = _current_user_context_dict(ensure=False)
        value = _safe_str(
            context.get("auth_user_id")
            or context.get("authUserId")
            or context.get("sub")
            or context.get("subject"),
            "",
            160,
        )
        return value or None
    except Exception:
        return None


def _current_email() -> Optional[str]:
    try:
        context = _current_user_context_dict(ensure=False)
        value = _safe_str(
            context.get("email")
            or context.get("auth_email")
            or context.get("authEmail"),
            "",
            320,
        ).lower()
        return value or None
    except Exception:
        return None


def _is_demo_mode() -> bool:
    try:
        context = _current_user_context_dict(ensure=False)
        return _safe_bool(context.get("demo_mode") or context.get("is_demo"), False)
    except Exception:
        return False


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

        chunk = _extract_chunk_from_project_payload(project_payload)

        result = {
            "ok": True,
            "project_id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "appProjectPublicId": getattr(project, "public_id", None),
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
            "project": project_payload,
        }

        if include_private:
            result["service_links"] = list_project_service_links(project)
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


# ─────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────

def _finalize_json_response(resp: Response, *, no_store: bool = True) -> Response:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
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
        resp = jsonify(payload)
        resp.status_code = int(status)
        _finalize_json_response(resp, no_store=no_store)
        return resp, status

    except Exception:
        fallback = jsonify(
            {
                "ok": False,
                "error": "failed to serialize response",
                "code": "response_serialization_failed",
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
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status, no_store=True)


def _result_response(result: Any):
    try:
        payload = result.to_dict() if hasattr(result, "to_dict") else {}
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
        return _json_response(exc.to_dict(), exc.status_code, no_store=True)
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


# ─────────────────────────────────────────────────────────────
# Request lifecycle
# ─────────────────────────────────────────────────────────────

@bp.before_request
def _projects_api_before_request():
    try:
        context = _current_user_context_dict(ensure=False)

        # Demo- und nicht persistente externe Auth-Kontexte sollen keinen
        # Dev-Placeholder-User erzwingen.
        if _safe_bool(context.get("demo_mode") or context.get("is_demo"), False):
            return

        if not _safe_bool(context.get("persistent"), bool(context.get("user_id"))):
            return

        ensure_default_user()

    except Exception as exc:
        _log_warning("ensure_default_user before projects API failed: %s", exc.__class__.__name__)


# ─────────────────────────────────────────────────────────────
# Status / diagnostics
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/_status")
def projects_status():
    try:
        invitation_status = {}
        publication_status = {}

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

        payload = {
            "ok": True,
            "service": "projects_api",
            "blueprint": "projects_api",
            "phase": "project-management-auth-demo-invitations-publication",
            "current_user": get_current_user_status(),
            "project_service": get_project_service_status(),
            "permissions": get_permission_service_status(),
            "invitations": invitation_status,
            "publication": publication_status,
            "project_form": {
                "address_input_mode": "single_box",
                "visibility_mode": "cards_private_unlisted_public",
                "system_refs_in_normal_form": False,
            },
            "chunk": {
                "provisioningEnabled": _config_bool("VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True),
                "provisioningRequired": _config_bool("VECTOPLAN_CHUNK_PROVISION_REQUIRED", False),
                "internalUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_INTERNAL_URL", "")),
                "publicUrlConfigured": bool(_config_str("VECTOPLAN_CHUNK_PUBLIC_URL", "")),
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
        user_id = _current_user_id_optional()
        limit = _request_int("limit", 100)
        include_public = _request_bool("include_public", True)

        items = list_project_sidebar_items(
            user_id=user_id,
            include_public=include_public,
            limit=limit,
        )

        context = _current_user_context_dict(ensure=False)

        return _json_response(
            {
                "ok": True,
                "user_id": user_id,
                "auth": context,
                "demo_mode": _safe_bool(context.get("demo_mode"), False),
                "items": items,
                "sidebar_items": items,
                "total": len(items),
            },
            200,
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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        include_private = _request_bool("include_private", False) and bool(
            user_id and can_manage_project(project, user_id)
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


# ─────────────────────────────────────────────────────────────
# Project members / permissions
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/access")
def project_access_get(project_id: str):
    try:
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()

        return _json_response(
            {
                "ok": True,
                "project_id": getattr(project, "id", None),
                "public_id": getattr(project, "public_id", None),
                "auth": _current_user_context_dict(ensure=False),
                "access": serialize_project_permissions(project, user_id=user_id),
            },
            200,
            no_store=True,
        )

    except Exception as exc:
        return _exception_response("project_access_get failed", exc, code="project_access_failed")


@bp.get("/v1/projects/<project_id>/members")
def project_members_list(project_id: str):
    try:
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        require_project_permission(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        include_inactive = _request_bool("include_inactive", False)
        members = list_project_memberships(project, include_inactive=include_inactive)

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        data = _request_json({})

        role = data.get("role") or request.args.get("role") or "viewer"
        permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}

        membership = set_project_member_role(
            project,
            target_user_id=target_user_id,
            role=normalize_role(role),
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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

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
        if not callable(list_project_invitations):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
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
        if not callable(invite_registered_email_to_project):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        data = _request_json({})
        user_id = _current_user_id_optional()

        email = (
            data.get("email")
            or data.get("user_email")
            or data.get("userEmail")
            or data.get("invitee")
            or data.get("recipient")
            or request.args.get("email")
        )
        role = data.get("role") or request.args.get("role") or "viewer"
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
        if not callable(revoke_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        data = _request_json({})
        user_id = _current_user_id_optional()
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
        if not callable(accept_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        data = _request_json({})
        user_id = _current_user_id_optional()

        result = accept_project_invitation(
            invitation_id=invitation_id,
            auth_user_id=data.get("auth_user_id") or data.get("authUserId") or _current_auth_user_id(),
            email=data.get("email") or _current_email(),
            local_user_id=data.get("local_user_id") or data.get("localUserId") or user_id,
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
        if not callable(reject_project_invitation):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        data = _request_json({})

        result = reject_project_invitation(
            invitation_id=invitation_id,
            auth_user_id=data.get("auth_user_id") or data.get("authUserId") or _current_auth_user_id(),
            email=data.get("email") or _current_email(),
            reason=data.get("reason") or request.args.get("reason"),
            commit=True,
        )

        return _service_dict_response(result, default_status=200)

    except Exception as exc:
        return _exception_response("project_invitation_reject failed", exc, code="invitation_reject_failed")


@bp.post("/v1/projects/invitations/expire")
@bp.post("/v1/project-invitations/expire")
def project_invitations_expire():
    try:
        if not callable(expire_project_invitations):
            return _json_error("project invitation service unavailable", 503, code="invitation_service_unavailable")

        project_id = _request_str("project_id", "", 120) or None
        project = resolve_project(project_id) if project_id else None

        if project is not None:
            user_id = _current_user_id_optional()
            require_project_permission(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

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
        if not callable(get_project_publication):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        include_private_requested = _request_bool("include_private", False)

        include_private = False
        if include_private_requested and user_id and can_manage_project(project, user_id):
            include_private = True

        result = get_project_publication(
            project,
            actor_user_id=user_id,
            include_private=include_private,
            for_public=not bool(user_id),
            use_cache=False,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_publication_get failed", exc, code="publication_get_failed")


@bp.put("/v1/projects/<project_id>/publication")
@bp.patch("/v1/projects/<project_id>/publication")
def project_publication_update(project_id: str):
    try:
        if not callable(update_project_publication):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        data = _request_json({})

        result = update_project_publication(
            project,
            data,
            actor_user_id=user_id,
            commit=True,
        )

        return _service_dict_response(result, default_status=200)

    except PermissionDenied as exc:
        return _permission_error_response(exc)

    except Exception as exc:
        return _exception_response("project_publication_update failed", exc, code="publication_update_failed")


@bp.get("/v1/projects/<project_id>/publication/workspaces/<workspace>")
@bp.get("/v1/projects/<project_id>/workspace-access/<workspace>")
def project_workspace_access_get(project_id: str, workspace: str):
    try:
        if not callable(can_access_project_workspace):
            return _json_error("project publication service unavailable", 503, code="publication_service_unavailable")

        user_id = _current_user_id_optional()
        public_request = _request_bool("public", False) or user_id is None
        workspace_key = normalize_workspace_key(workspace) if callable(normalize_workspace_key) else workspace

        result = can_access_project_workspace(
            project_id,
            workspace_key,
            actor_user_id=user_id,
            public_request=public_request,
        )

        return _service_dict_response(result, default_status=200)

    except Exception as exc:
        return _exception_response("project_workspace_access_get failed", exc, code="workspace_access_failed")


# ─────────────────────────────────────────────────────────────
# Project versions
# ─────────────────────────────────────────────────────────────

@bp.get("/v1/projects/<project_id>/versions")
def project_versions_list(project_id: str):
    try:
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

        kind = _request_str("kind", "", 80) or None
        service_name = _request_str("service_name", "", 80) or _request_str("service", "", 80) or None
        limit = _request_int("limit", 100)

        items = list_project_versions(
            project,
            kind=kind,
            service_name=service_name,
            limit=limit,
        )

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()

        # Systemreferenzen sind Admin-/Settings-Daten.
        require_project_permission(project, PERMISSION_MANAGE, user_id, allow_public_view=False)

        items = list_project_service_links(project)

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()

        # Embed-Policy ist Einstellung. Nicht an viewer/public ausliefern.
        require_project_permission(project, PERMISSION_EMBED, user_id, allow_public_view=False)

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

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
        project = resolve_project(project_id)

        if project is None:
            return _json_error("project not found", 404, code="project_not_found")

        user_id = _current_user_id_optional()
        require_project_permission(project, PERMISSION_VIEW, user_id, allow_public_view=True)

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