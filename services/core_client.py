"""Idempotent internal client for ``vectoplan-app -> vectoplan-core``.

The App project is always committed before this client performs a network call.
Core provisioning is therefore retryable and does not pretend to be part of the
App database transaction.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any


class CoreClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _config(name: str, default: Any = None) -> Any:
    try:
        from flask import current_app, has_app_context

        if has_app_context() and name in current_app.config:
            return current_app.config[name]
    except Exception:
        pass
    return os.getenv(name, default)


def _bool(name: str, default: bool) -> bool:
    value = _config(name, default)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _service_refs(project: Any) -> dict[str, Any]:
    value = getattr(project, "service_refs", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _chunk_refs(project: Any) -> dict[str, Any]:
    refs = _service_refs(project)
    chunk = refs.get("chunk") if isinstance(refs.get("chunk"), Mapping) else {}
    return {
        "chunkProjectId": _text(getattr(project, "chunk_project_id", None)) or _text(chunk.get("chunk_project_id")) or _text(chunk.get("chunkProjectId")),
        "chunkUniverseId": _text(getattr(project, "chunk_universe_id", None)) or _text(chunk.get("chunk_universe_id")) or _text(chunk.get("chunkUniverseId")),
        "chunkWorldId": _text(getattr(project, "chunk_world_id", None)) or _text(chunk.get("chunk_world_id")) or _text(chunk.get("chunkWorldId")),
    }


def core_project_id_from_project(project: Any) -> str | None:
    """Return the App-owned Core reference without contacting another service."""
    refs = _service_refs(project)
    core = refs.get("core") if isinstance(refs.get("core"), Mapping) else {}
    metadata = getattr(project, "metadata_json", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    provisioning = metadata.get("coreProvisioning")
    provisioning = dict(provisioning) if isinstance(provisioning, Mapping) else {}
    return (
        _text(core.get("core_project_id"))
        or _text(core.get("coreProjectId"))
        or _text(core.get("project_id"))
        or _text(core.get("projectId"))
        or _text(provisioning.get("coreProjectId"))
        or _text(provisioning.get("core_project_id"))
    )


def is_core_provisioning_enabled() -> bool:
    try:
        from flask import current_app, has_app_context

        if (
            has_app_context()
            and current_app.testing
            and os.getenv("VECTOPLAN_APP_CORE_PROVISION_ON_PROJECT_CREATE") is None
        ):
            return False
    except Exception:
        pass
    return _bool("VECTOPLAN_APP_CORE_PROVISION_ON_PROJECT_CREATE", True)


def is_core_provisioning_required() -> bool:
    return _bool("VECTOPLAN_APP_CORE_PROVISIONING_REQUIRED", False)


def ensure_core_project_for_app_project(project: Any) -> dict[str, Any]:
    if not is_core_provisioning_enabled():
        return {"ok": True, "status": "disabled", "created": False}

    app_project_id = _text(getattr(project, "public_id", None))
    if not app_project_id:
        raise CoreClientError("app_project_public_id_missing", "Project.public_id is required", retryable=False)

    base_url = _text(_config("VECTOPLAN_CORE_INTERNAL_URL", "http://vectoplan-core:5000"))
    if not base_url:
        raise CoreClientError("core_client_not_configured", "VECTOPLAN_CORE_INTERNAL_URL is not configured")

    chunk_refs = _chunk_refs(project)
    body = {
        "name": _text(getattr(project, "name", None)),
        "ownerUserId": _text(getattr(project, "auth_owner_user_id", None)) or _text(getattr(project, "owner_user_id", None)),
        **chunk_refs,
        "metadata": {
            "source": "vectoplan-app",
            "appProjectStatus": _text(getattr(project, "status", None)),
            "provisioningContract": "app-core-project/0.1",
        },
    }
    encoded_id = urllib.parse.quote(app_project_id, safe="")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1/projects/by-app/{encoded_id}",
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "vectoplan-app/core-client",
            "X-Service-ID": "vectoplan-app",
        },
    )
    api_key = _text(_config("VECTOPLAN_APP_CORE_SERVICE_API_KEY", ""))
    if api_key:
        request.add_header("X-Service-API-Key", api_key)
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        timeout = float(_config("VECTOPLAN_APP_CORE_TIMEOUT_SECONDS", 10) or 10)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CoreClientError("core_http_error", f"Core provisioning returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CoreClientError("core_unreachable", f"Core provisioning failed: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise CoreClientError("core_provisioning_rejected", "Core rejected project provisioning")

    project_payload = payload.get("project") if isinstance(payload.get("project"), Mapping) else {}
    core_project_id = _text(payload.get("coreProjectId")) or _text(project_payload.get("projectId"))
    if not core_project_id:
        raise CoreClientError("core_response_incomplete", "Core response has no coreProjectId")

    refs = _service_refs(project)
    refs["core"] = {
        "status": "ready",
        "core_project_id": core_project_id,
        "app_project_id": app_project_id,
        **{key: value for key, value in chunk_refs.items() if value},
        "contract": "app-core-project/0.1",
    }
    project.service_refs = refs
    metadata = getattr(project, "metadata_json", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    metadata["coreProvisioning"] = {
        "status": "ready",
        "coreProjectId": core_project_id,
        "created": bool(payload.get("created")),
        "changed": bool(payload.get("changed")),
    }
    project.metadata_json = metadata
    return {
        "ok": True,
        "status": "ready",
        "coreProjectId": core_project_id,
        "created": bool(payload.get("created")),
        "changed": bool(payload.get("changed")),
        "project": project_payload,
    }


def mark_core_provisioning_failed(project: Any, error: BaseException) -> None:
    refs = _service_refs(project)
    existing = refs.get("core") if isinstance(refs.get("core"), Mapping) else {}
    refs["core"] = {
        **dict(existing),
        "status": "error",
        "error": {
            "code": _text(getattr(error, "code", None)) or "core_provisioning_failed",
            "message": str(error),
            "retryable": bool(getattr(error, "retryable", True)),
        },
    }
    project.service_refs = refs


__all__ = [
    "CoreClientError",
    "core_project_id_from_project",
    "ensure_core_project_for_app_project",
    "is_core_provisioning_enabled",
    "is_core_provisioning_required",
    "mark_core_provisioning_failed",
]
