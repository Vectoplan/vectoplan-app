# services/vectoplan-app/models/projects.py
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy.orm import validates

from .base import (
    SerializationMixin,
    SoftDeleteMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    merge_dicts,
    normalize_status,
    project_public_id,
    safe_bool,
    safe_dict,
    safe_float,
    safe_int,
    safe_str,
    utcnow,
)


PROJECT_SETUP_DRAFT = "draft"
PROJECT_SETUP_DEFINED = "defined"
PROJECT_SETUP_CONFIGURED = "configured"

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUS_DELETED = "deleted"
PROJECT_STATUS_EXPIRED = "expired"

PROJECT_VISIBILITY_PRIVATE = "private"
PROJECT_VISIBILITY_UNLISTED = "unlisted"
PROJECT_VISIBILITY_PUBLIC = "public"

# Legacy alias. Keep exported so older imports do not break.
PROJECT_VISIBILITY_SHARED = PROJECT_VISIBILITY_UNLISTED

PROJECT_SCOPE_PERSONAL = "personal"
PROJECT_SCOPE_ACCOUNT = "account"
PROJECT_SCOPE_DEMO = "demo"

CHUNK_STATUS_DISABLED = "disabled"
CHUNK_STATUS_PENDING = "pending"
CHUNK_STATUS_READY = "ready"
CHUNK_STATUS_ERROR = "error"

CHUNK_PROVISIONING_PENDING = "pending"
CHUNK_PROVISIONING_PROVISIONING = "provisioning"
CHUNK_PROVISIONING_READY = "ready"
CHUNK_PROVISIONING_FALLBACK_READY = "fallback_ready"
CHUNK_PROVISIONING_FAILED = "failed"
CHUNK_PROVISIONING_REPAIR_REQUIRED = "repair_required"
CHUNK_PROVISIONING_DISABLED = "disabled"

CHUNK_WORLD_TEMPLATE_EARTH = "earth"
CHUNK_WORLD_TEMPLATE_FLAT = "flat"

CHUNK_ACCESS_SYNC_PENDING = "pending"
CHUNK_ACCESS_SYNC_SYNCING = "syncing"
CHUNK_ACCESS_SYNC_READY = "ready"
CHUNK_ACCESS_SYNC_FAILED = "failed"
CHUNK_ACCESS_SYNC_REPAIR_REQUIRED = "repair_required"
CHUNK_ACCESS_SYNC_DISABLED = "disabled"

VALID_PROJECT_SCOPES = frozenset(
    {
        PROJECT_SCOPE_PERSONAL,
        PROJECT_SCOPE_ACCOUNT,
        PROJECT_SCOPE_DEMO,
    }
)

VALID_PROJECT_VISIBILITIES = frozenset(
    {
        PROJECT_VISIBILITY_PRIVATE,
        PROJECT_VISIBILITY_UNLISTED,
        PROJECT_VISIBILITY_PUBLIC,
    }
)

VALID_CHUNK_STATUSES = frozenset(
    {
        CHUNK_STATUS_DISABLED,
        CHUNK_STATUS_PENDING,
        CHUNK_STATUS_READY,
        CHUNK_STATUS_ERROR,
    }
)

VALID_CHUNK_PROVISIONING_STATUSES = frozenset(
    {
        CHUNK_PROVISIONING_PENDING,
        CHUNK_PROVISIONING_PROVISIONING,
        CHUNK_PROVISIONING_READY,
        CHUNK_PROVISIONING_FALLBACK_READY,
        CHUNK_PROVISIONING_FAILED,
        CHUNK_PROVISIONING_REPAIR_REQUIRED,
        CHUNK_PROVISIONING_DISABLED,
    }
)

VALID_CHUNK_WORLD_TEMPLATES = frozenset(
    {
        CHUNK_WORLD_TEMPLATE_EARTH,
        CHUNK_WORLD_TEMPLATE_FLAT,
    }
)

VALID_CHUNK_ACCESS_SYNC_STATUSES = frozenset(
    {
        CHUNK_ACCESS_SYNC_PENDING,
        CHUNK_ACCESS_SYNC_SYNCING,
        CHUNK_ACCESS_SYNC_READY,
        CHUNK_ACCESS_SYNC_FAILED,
        CHUNK_ACCESS_SYNC_REPAIR_REQUIRED,
        CHUNK_ACCESS_SYNC_DISABLED,
    }
)


# ─────────────────────────────────────────────────────────────
# Transitional import helpers
# ─────────────────────────────────────────────────────────────

def _metadata_has_table(table_name: str) -> bool:
    try:
        return str(table_name) in db.metadata.tables
    except Exception:
        return False


def _table_args(extend_existing: bool) -> Dict[str, Any]:
    try:
        return {"extend_existing": True} if extend_existing else {}
    except Exception:
        return {"extend_existing": True}


def _model_has_columns(model: Any, required_columns: List[str]) -> bool:
    try:
        table = getattr(model, "__table__", None)
        columns = getattr(table, "columns", None)

        if columns is None:
            return False

        available = {str(column.name) for column in columns}
        return all(column in available for column in required_columns)

    except Exception:
        return False


def _core_model_if_registered(model_name: str, table_name: str) -> Any:
    """
    Transitional guard.

    If models/core.py already registered Project, reuse it only when it has
    the required auth/demo/chunk columns. Otherwise define the extended model
    with extend_existing=True.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None and _model_has_columns(
                model,
                [
                    "owner_user_id",
                    "auth_owner_user_id",
                    "auth_account_id",
                    "project_scope",
                    "is_demo",
                    "demo_client_identity_id",
                    "demo_session_id",
                    "demo_expires_at",
                    "chunk_project_id",
                    "chunk_universe_id",
                    "chunk_world_id",
                    "chunk_status",
                    "chunk_ready",
                    "chunk_provisioning_status",
                    "chunk_world_template_requested",
                    "chunk_world_template_fallback",
                    "chunk_world_template_effective",
                    "earth_reference_fingerprint",
                    "chunk_access_sync_status",
                ],
            ):
                return model
        except Exception:
            return None

    except Exception:
        return None

    return None


def _resolve_model(model_name: str, table_name: str, factory: Any) -> Any:
    try:
        existing = _core_model_if_registered(model_name, table_name)
        if existing is not None:
            return existing

        return factory(extend_existing=_metadata_has_table(table_name))

    except Exception:
        return factory(extend_existing=True)


# ─────────────────────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────────────────────

def normalize_project_setup_status(value: Any, default: str = PROJECT_SETUP_DRAFT) -> str:
    try:
        text = safe_str(value, default, 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "new": PROJECT_SETUP_DRAFT,
            "empty": PROJECT_SETUP_DRAFT,
            "draft": PROJECT_SETUP_DRAFT,
            "created": PROJECT_SETUP_DRAFT,
            "definition": PROJECT_SETUP_DEFINED,
            "defined": PROJECT_SETUP_DEFINED,
            "metadata": PROJECT_SETUP_DEFINED,
            "ready": PROJECT_SETUP_CONFIGURED,
            "complete": PROJECT_SETUP_CONFIGURED,
            "completed": PROJECT_SETUP_CONFIGURED,
            "configured": PROJECT_SETUP_CONFIGURED,
            "active": PROJECT_SETUP_CONFIGURED,
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def is_configured_status(value: Any) -> bool:
    try:
        return normalize_project_setup_status(value) in {
            PROJECT_SETUP_CONFIGURED,
            "ready",
            "complete",
            "completed",
            "active",
        }
    except Exception:
        return False


def normalize_project_status(value: Any, default: str = PROJECT_STATUS_ACTIVE) -> str:
    try:
        text = normalize_status(value, default)

        if text in {"archive", "archived"}:
            return PROJECT_STATUS_ARCHIVED

        if text in {"delete", "deleted", "removed"}:
            return PROJECT_STATUS_DELETED

        if text in {"expire", "expired", "ttl_expired"}:
            return PROJECT_STATUS_EXPIRED

        if text in {"active", "enabled", "live"}:
            return PROJECT_STATUS_ACTIVE

        return text or default

    except Exception:
        return default


def normalize_project_visibility(value: Any, default: str = PROJECT_VISIBILITY_PRIVATE) -> str:
    try:
        text = safe_str(value, default, 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "private": PROJECT_VISIBILITY_PRIVATE,
            "hidden": PROJECT_VISIBILITY_PRIVATE,
            "owner": PROJECT_VISIBILITY_PRIVATE,
            "personal": PROJECT_VISIBILITY_PRIVATE,
            "shared": PROJECT_VISIBILITY_UNLISTED,
            "link": PROJECT_VISIBILITY_UNLISTED,
            "link_only": PROJECT_VISIBILITY_UNLISTED,
            "unlisted": PROJECT_VISIBILITY_UNLISTED,
            "public_link": PROJECT_VISIBILITY_UNLISTED,
            "public": PROJECT_VISIBILITY_PUBLIC,
            "published": PROJECT_VISIBILITY_PUBLIC,
            "discoverable": PROJECT_VISIBILITY_PUBLIC,
        }

        normalized = aliases.get(text, text or default)

        if normalized not in VALID_PROJECT_VISIBILITIES:
            return default

        return normalized

    except Exception:
        return default


def normalize_project_scope(value: Any, *, is_demo: bool = False, auth_account_id: Any = None) -> str:
    try:
        if is_demo:
            return PROJECT_SCOPE_DEMO

        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": PROJECT_SCOPE_ACCOUNT if safe_str(auth_account_id, "", 160) else PROJECT_SCOPE_PERSONAL,
            "user": PROJECT_SCOPE_PERSONAL,
            "owner": PROJECT_SCOPE_PERSONAL,
            "personal": PROJECT_SCOPE_PERSONAL,
            "private": PROJECT_SCOPE_PERSONAL,
            "account": PROJECT_SCOPE_ACCOUNT,
            "org": PROJECT_SCOPE_ACCOUNT,
            "organization": PROJECT_SCOPE_ACCOUNT,
            "team": PROJECT_SCOPE_ACCOUNT,
            "tenant": PROJECT_SCOPE_ACCOUNT,
            "demo": PROJECT_SCOPE_DEMO,
            "guest": PROJECT_SCOPE_DEMO,
            "temporary": PROJECT_SCOPE_DEMO,
        }

        normalized = aliases.get(text, text or PROJECT_SCOPE_PERSONAL)

        if normalized not in VALID_PROJECT_SCOPES:
            return PROJECT_SCOPE_PERSONAL

        return normalized

    except Exception:
        return PROJECT_SCOPE_DEMO if is_demo else PROJECT_SCOPE_PERSONAL


def normalize_chunk_status(
    value: Any,
    default: str = CHUNK_STATUS_PENDING,
    *,
    has_refs: bool = False,
) -> str:
    try:
        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": CHUNK_STATUS_READY if has_refs else default,
            "ok": CHUNK_STATUS_READY,
            "active": CHUNK_STATUS_READY,
            "linked": CHUNK_STATUS_READY,
            "provisioned": CHUNK_STATUS_READY,
            "created": CHUNK_STATUS_READY,
            "ready": CHUNK_STATUS_READY,
            "pending": CHUNK_STATUS_PENDING,
            "waiting": CHUNK_STATUS_PENDING,
            "queued": CHUNK_STATUS_PENDING,
            "disabled": CHUNK_STATUS_DISABLED,
            "off": CHUNK_STATUS_DISABLED,
            "error": CHUNK_STATUS_ERROR,
            "failed": CHUNK_STATUS_ERROR,
            "failure": CHUNK_STATUS_ERROR,
            "unavailable": CHUNK_STATUS_ERROR,
        }

        status = aliases.get(text, text or default)

        if status not in VALID_CHUNK_STATUSES:
            return CHUNK_STATUS_READY if has_refs else default

        return status

    except Exception:
        return CHUNK_STATUS_READY if has_refs else default



def normalize_chunk_world_template(
    value: Any,
    default: Optional[str] = CHUNK_WORLD_TEMPLATE_EARTH,
    *,
    allow_none: bool = False,
) -> Optional[str]:
    try:
        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": None if allow_none else default,
            "earth": CHUNK_WORLD_TEMPLATE_EARTH,
            "global": CHUNK_WORLD_TEMPLATE_EARTH,
            "globe": CHUNK_WORLD_TEMPLATE_EARTH,
            "geographic": CHUNK_WORLD_TEMPLATE_EARTH,
            "wgs84": CHUNK_WORLD_TEMPLATE_EARTH,
            "flat": CHUNK_WORLD_TEMPLATE_FLAT,
            "local": CHUNK_WORLD_TEMPLATE_FLAT,
            "planar": CHUNK_WORLD_TEMPLATE_FLAT,
            "default": CHUNK_WORLD_TEMPLATE_FLAT,
        }

        normalized = aliases.get(text, text or (None if allow_none else default))

        if normalized is None and allow_none:
            return None

        if normalized not in VALID_CHUNK_WORLD_TEMPLATES:
            return None if allow_none else (default or CHUNK_WORLD_TEMPLATE_EARTH)

        return normalized

    except Exception:
        return None if allow_none else (default or CHUNK_WORLD_TEMPLATE_EARTH)


def normalize_chunk_provisioning_status(
    value: Any,
    default: str = CHUNK_PROVISIONING_PENDING,
    *,
    has_refs: bool = False,
    requested_template: Any = None,
    effective_template: Any = None,
) -> str:
    try:
        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        requested = normalize_chunk_world_template(requested_template, allow_none=True)
        effective = normalize_chunk_world_template(effective_template, allow_none=True)

        inferred_ready = (
            CHUNK_PROVISIONING_FALLBACK_READY
            if has_refs and requested and effective and requested != effective
            else CHUNK_PROVISIONING_READY
        )

        aliases = {
            "": inferred_ready if has_refs else default,
            "pending": CHUNK_PROVISIONING_PENDING,
            "waiting": CHUNK_PROVISIONING_PENDING,
            "queued": CHUNK_PROVISIONING_PENDING,
            "provisioning": CHUNK_PROVISIONING_PROVISIONING,
            "running": CHUNK_PROVISIONING_PROVISIONING,
            "in_progress": CHUNK_PROVISIONING_PROVISIONING,
            "ready": inferred_ready if has_refs else CHUNK_PROVISIONING_READY,
            "ok": inferred_ready if has_refs else CHUNK_PROVISIONING_READY,
            "provisioned": inferred_ready if has_refs else CHUNK_PROVISIONING_READY,
            "fallback": CHUNK_PROVISIONING_FALLBACK_READY,
            "fallback_ready": CHUNK_PROVISIONING_FALLBACK_READY,
            "failed": CHUNK_PROVISIONING_FAILED,
            "failure": CHUNK_PROVISIONING_FAILED,
            "error": CHUNK_PROVISIONING_FAILED,
            "repair": CHUNK_PROVISIONING_REPAIR_REQUIRED,
            "repair_required": CHUNK_PROVISIONING_REPAIR_REQUIRED,
            "inconsistent": CHUNK_PROVISIONING_REPAIR_REQUIRED,
            "disabled": CHUNK_PROVISIONING_DISABLED,
            "off": CHUNK_PROVISIONING_DISABLED,
        }

        normalized = aliases.get(text, text or default)

        if normalized not in VALID_CHUNK_PROVISIONING_STATUSES:
            return inferred_ready if has_refs else default

        if normalized == CHUNK_PROVISIONING_READY and has_refs and requested and effective and requested != effective:
            return CHUNK_PROVISIONING_FALLBACK_READY

        return normalized

    except Exception:
        return CHUNK_PROVISIONING_READY if has_refs else default


def normalize_chunk_access_sync_status(
    value: Any,
    default: str = CHUNK_ACCESS_SYNC_PENDING,
) -> str:
    try:
        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": default,
            "pending": CHUNK_ACCESS_SYNC_PENDING,
            "waiting": CHUNK_ACCESS_SYNC_PENDING,
            "queued": CHUNK_ACCESS_SYNC_PENDING,
            "syncing": CHUNK_ACCESS_SYNC_SYNCING,
            "running": CHUNK_ACCESS_SYNC_SYNCING,
            "in_progress": CHUNK_ACCESS_SYNC_SYNCING,
            "ready": CHUNK_ACCESS_SYNC_READY,
            "ok": CHUNK_ACCESS_SYNC_READY,
            "synced": CHUNK_ACCESS_SYNC_READY,
            "failed": CHUNK_ACCESS_SYNC_FAILED,
            "failure": CHUNK_ACCESS_SYNC_FAILED,
            "error": CHUNK_ACCESS_SYNC_FAILED,
            "repair": CHUNK_ACCESS_SYNC_REPAIR_REQUIRED,
            "repair_required": CHUNK_ACCESS_SYNC_REPAIR_REQUIRED,
            "inconsistent": CHUNK_ACCESS_SYNC_REPAIR_REQUIRED,
            "disabled": CHUNK_ACCESS_SYNC_DISABLED,
            "off": CHUNK_ACCESS_SYNC_DISABLED,
        }

        normalized = aliases.get(text, text or default)

        if normalized not in VALID_CHUNK_ACCESS_SYNC_STATUSES:
            return default

        return normalized

    except Exception:
        return default

def _clean_ref_id(value: Any, max_len: int = 160) -> Optional[str]:
    try:
        text = safe_str(value, "", max_len).strip()
        return text or None
    except Exception:
        return None


def _clean_user_id(value: Any) -> Optional[int]:
    try:
        parsed = safe_int(value, 0, minimum=1)
        return int(parsed) if parsed else None
    except Exception:
        return None


def _safe_route_hints(value: Any) -> Dict[str, Any]:
    try:
        return safe_dict(value)
    except Exception:
        return {}


def _safe_datetime_iso(value: Any) -> Optional[str]:
    try:
        return isoformat(value)
    except Exception:
        return None


def _chunk_payload_from_mapping(value: Any) -> Dict[str, Any]:
    try:
        data = safe_dict(value)
        chunk = safe_dict(data.get("chunk"))

        if not chunk and any(
            key in data
            for key in (
                "chunk_project_id",
                "chunkProjectId",
                "chunk_world_id",
                "chunkWorldId",
                "chunk_universe_id",
                "chunkUniverseId",
            )
        ):
            chunk = data

        ids = safe_dict(chunk.get("ids"))

        return {
            "status": (
                chunk.get("status")
                or chunk.get("chunkStatus")
                or data.get("chunk_status")
                or data.get("chunkStatus")
            ),
            "chunk_project_id": (
                chunk.get("chunk_project_id")
                or chunk.get("chunkProjectId")
                or ids.get("chunkProjectId")
                or ids.get("chunk_project_id")
                or data.get("chunk_project_id")
                or data.get("chunkProjectId")
            ),
            "chunk_universe_id": (
                chunk.get("chunk_universe_id")
                or chunk.get("chunkUniverseId")
                or ids.get("chunkUniverseId")
                or ids.get("chunk_universe_id")
                or data.get("chunk_universe_id")
                or data.get("chunkUniverseId")
            ),
            "chunk_world_id": (
                chunk.get("chunk_world_id")
                or chunk.get("chunkWorldId")
                or ids.get("chunkWorldId")
                or ids.get("chunk_world_id")
                or data.get("chunk_world_id")
                or data.get("chunkWorldId")
            ),
            "route_hints": (
                chunk.get("route_hints")
                or chunk.get("routeHints")
                or data.get("route_hints")
                or data.get("routeHints")
                or {}
            ),
            "error": (
                chunk.get("error")
                or data.get("chunk_error")
                or data.get("chunkError")
                or {}
            ),
            "provisioned_at": (
                chunk.get("provisioned_at")
                or chunk.get("provisionedAt")
                or data.get("chunk_provisioned_at")
                or data.get("chunkProvisionedAt")
            ),
        }

    except Exception:
        return {}


def build_chunk_refs(
    *,
    chunk_project_id: Any = None,
    chunk_universe_id: Any = None,
    chunk_world_id: Any = None,
    status: Any = None,
    route_hints: Any = None,
    error: Any = None,
    provisioned_at: Any = None,
) -> Dict[str, Any]:
    try:
        clean_chunk_project_id = _clean_ref_id(chunk_project_id)
        clean_chunk_universe_id = _clean_ref_id(chunk_universe_id)
        clean_chunk_world_id = _clean_ref_id(chunk_world_id)
        clean_route_hints = _safe_route_hints(route_hints)
        clean_error = safe_dict(error)

        has_refs = bool(clean_chunk_project_id and clean_chunk_world_id)
        clean_status = normalize_chunk_status(status, has_refs=has_refs)

        ready = bool(has_refs and clean_status == CHUNK_STATUS_READY)

        return {
            "status": clean_status,
            "ready": ready,
            "chunk_project_id": clean_chunk_project_id,
            "chunkProjectId": clean_chunk_project_id,
            "chunk_universe_id": clean_chunk_universe_id,
            "chunkUniverseId": clean_chunk_universe_id,
            "chunk_world_id": clean_chunk_world_id,
            "chunkWorldId": clean_chunk_world_id,
            "route_hints": clean_route_hints,
            "routeHints": clean_route_hints,
            "error": clean_error,
            "provisioned_at": isoformat(provisioned_at) if provisioned_at else None,
            "provisionedAt": isoformat(provisioned_at) if provisioned_at else None,
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
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
            "provisioned_at": None,
            "provisionedAt": None,
        }


def _chunk_refs_from_sources(
    *,
    direct_project_id: Any = None,
    direct_universe_id: Any = None,
    direct_world_id: Any = None,
    direct_status: Any = None,
    direct_ready: Any = None,
    direct_route_hints: Any = None,
    direct_error: Any = None,
    direct_provisioned_at: Any = None,
    service_refs: Any = None,
    metadata_json: Any = None,
) -> Dict[str, Any]:
    try:
        refs = safe_dict(service_refs)
        metadata = safe_dict(metadata_json)

        chunk_from_refs = _chunk_payload_from_mapping(refs)
        chunk_from_meta = _chunk_payload_from_mapping(metadata)

        chunk_project_id = (
            _clean_ref_id(direct_project_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_project_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_project_id"))
        )

        chunk_universe_id = (
            _clean_ref_id(direct_universe_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_universe_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_universe_id"))
        )

        chunk_world_id = (
            _clean_ref_id(direct_world_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_world_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_world_id"))
        )

        route_hints = (
            _safe_route_hints(direct_route_hints)
            or _safe_route_hints(chunk_from_refs.get("route_hints"))
            or _safe_route_hints(chunk_from_meta.get("route_hints"))
        )

        error = (
            safe_dict(direct_error)
            or safe_dict(chunk_from_refs.get("error"))
            or safe_dict(chunk_from_meta.get("error"))
        )

        status = (
            direct_status
            or chunk_from_refs.get("status")
            or chunk_from_meta.get("status")
        )

        provisioned_at = (
            direct_provisioned_at
            or chunk_from_refs.get("provisioned_at")
            or chunk_from_meta.get("provisioned_at")
        )

        built = build_chunk_refs(
            chunk_project_id=chunk_project_id,
            chunk_universe_id=chunk_universe_id,
            chunk_world_id=chunk_world_id,
            status=status,
            route_hints=route_hints,
            error=error,
            provisioned_at=provisioned_at,
        )

        if direct_ready is not None:
            explicit_ready = safe_bool(direct_ready, built["ready"])
            if not explicit_ready:
                built["ready"] = False
            elif built["chunk_project_id"] and built["chunk_world_id"] and built["status"] != CHUNK_STATUS_ERROR:
                built["ready"] = True
                built["status"] = CHUNK_STATUS_READY

        return built

    except Exception:
        return build_chunk_refs(status=CHUNK_STATUS_ERROR)


def _chunk_refs_to_service_ref(chunk_refs: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        refs = safe_dict(chunk_refs)

        return {
            "status": refs.get("status") or CHUNK_STATUS_PENDING,
            "ready": safe_bool(refs.get("ready"), False),
            "chunk_project_id": refs.get("chunk_project_id") or refs.get("chunkProjectId"),
            "chunkProjectId": refs.get("chunk_project_id") or refs.get("chunkProjectId"),
            "chunk_universe_id": refs.get("chunk_universe_id") or refs.get("chunkUniverseId"),
            "chunkUniverseId": refs.get("chunk_universe_id") or refs.get("chunkUniverseId"),
            "chunk_world_id": refs.get("chunk_world_id") or refs.get("chunkWorldId"),
            "chunkWorldId": refs.get("chunk_world_id") or refs.get("chunkWorldId"),
            "route_hints": safe_dict(refs.get("route_hints") or refs.get("routeHints")),
            "routeHints": safe_dict(refs.get("route_hints") or refs.get("routeHints")),
            "error": safe_dict(refs.get("error")),
            "provisioned_at": refs.get("provisioned_at") or refs.get("provisionedAt"),
            "provisionedAt": refs.get("provisioned_at") or refs.get("provisionedAt"),
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
            "ready": False,
        }


# ─────────────────────────────────────────────────────────────
# URL/path helpers
# ─────────────────────────────────────────────────────────────

def _public_project_url(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/project=new"
        return f"/project={value}"
    except Exception:
        return "/project=new"


def _project_workspace_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/project"
    except Exception:
        return "/ui/project/new"


def _project_editor_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/editor3d"
    except Exception:
        return "/ui/project/new"


def _project_map_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/map"
    except Exception:
        return "/ui/project/new"


def _project_cad2d_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/cad2d"
    except Exception:
        return "/ui/project/new"


def _project_plan2d_json_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return ""
        return f"/ui/project/{value}/plan2d.json"
    except Exception:
        return ""


def _project_cad_embed_json_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return ""
        return f"/ui/project/{value}/cad-embed.json"
    except Exception:
        return ""


def build_project_paths(public_id: Any) -> Dict[str, str]:
    try:
        value = safe_str(public_id, "new", 180) or "new"

        return {
            "projectPublicUrl": _public_project_url(value),
            "projectPagePath": _project_workspace_path(value),
            "projectUrl": _project_workspace_path(value),
            "editorPagePath": _project_editor_path(value),
            "editor3dPagePath": _project_editor_path(value),
            "initialEditorUrl": _project_editor_path(value),
            "mapPagePath": _project_map_path(value),
            "cad2dPagePath": _project_cad2d_path(value),
            "plan2dJsonPath": _project_plan2d_json_path(value),
            "cadEmbedJsonPath": _project_cad_embed_json_path(value),
            "adminPagePath": f"/ui/project/{value}/admin" if value != "new" else "",
            "lvPagePath": f"/ui/project/{value}/lv" if value != "new" else "",
            "versionsPagePath": f"/ui/project/{value}/versions" if value != "new" else "",
        }

    except Exception:
        return {
            "projectPublicUrl": "/project=new",
            "projectPagePath": "/ui/project/new",
            "projectUrl": "/ui/project/new",
        }


# ─────────────────────────────────────────────────────────────
# Project model
# ─────────────────────────────────────────────────────────────

def _define_project_model(*, extend_existing: bool = False):
    class Project(TimestampMixin, SoftDeleteMixin, SerializationMixin, db.Model):
        """
        App-owned project shell model.

        Stores:
        - local owner FK for vectoplan-app only
        - canonical auth owner references from vectoplan-auth
        - auth account reference from vectoplan-auth
        - demo/temporary project state
        - address/project metadata
        - service references into Chunk, 3D, Map, 2D/CAD, LV

        Does not store:
        - auth truth
        - passwords/sessions/tokens
        - chunk contents
        - 3D world truth
        - OpenLayer feature data
        - CAD geometry truth
        - LV contents
        """

        __tablename__ = "projects"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        public_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=project_public_id,
        )

        # Local FK into app_users. Nullable because Guest/Demo has no real local user.
        # Persistent project creation must set this explicitly from the local AppUser link.
        owner_user_id = db.Column(db.Integer, nullable=True, index=True)

        # Canonical auth references. These are not foreign keys in vectoplan-app.
        auth_owner_user_id = db.Column(db.String(160), nullable=True, index=True)
        auth_account_id = db.Column(db.String(160), nullable=True, index=True)
        owner_subject_type = db.Column(db.String(40), nullable=True, default="user", index=True)

        # Project scope separates personal, account and demo data.
        project_scope = db.Column(db.String(40), nullable=False, default=PROJECT_SCOPE_PERSONAL, index=True)

        # Demo project lifecycle. Demo projects must never become normal persistent projects.
        is_demo = db.Column(db.Boolean, nullable=False, default=False, index=True)
        demo_client_identity_id = db.Column(db.String(180), nullable=True, index=True)
        demo_session_id = db.Column(db.String(180), nullable=True, index=True)
        demo_expires_at = db.Column(db.DateTime, nullable=True, index=True)

        client_id = db.Column(db.String(80), nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)

        name = db.Column(db.String(255), nullable=False, default="Neues Projekt", index=True)
        description = db.Column(db.Text, nullable=True)

        address_text = db.Column(db.Text, nullable=True)
        street = db.Column(db.String(255), nullable=True)
        house_number = db.Column(db.String(80), nullable=True)
        postal_code = db.Column(db.String(40), nullable=True)
        city = db.Column(db.String(160), nullable=True)
        region = db.Column(db.String(160), nullable=True)
        country = db.Column(db.String(160), nullable=True)

        latitude = db.Column(db.Float, nullable=True, index=True)
        longitude = db.Column(db.Float, nullable=True, index=True)
        coordinate_srid = db.Column(db.String(40), nullable=True, default="EPSG:4326")
        geocode_source = db.Column(db.String(120), nullable=True)
        geocode_quality = db.Column(db.String(120), nullable=True)
        geocode_raw = db.Column(json_type(), nullable=True)

        # References into vectoplan-chunk.
        # These are service references, not foreign keys.
        chunk_project_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_universe_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_world_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_status = db.Column(db.String(40), nullable=False, default=CHUNK_STATUS_PENDING, index=True)
        chunk_ready = db.Column(db.Boolean, nullable=False, default=False, index=True)
        chunk_provisioned_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_last_error = db.Column(json_type(), nullable=True)
        chunk_route_hints = db.Column(json_type(), nullable=False, default=dict)

        # App-side orchestration state for the idempotent App -> Chunk provisioning flow.
        # The default requested template is Earth; Flat is the controlled fallback.
        chunk_provisioning_status = db.Column(
            db.String(40),
            nullable=False,
            default=CHUNK_PROVISIONING_PENDING,
            index=True,
        )
        chunk_world_template_requested = db.Column(
            db.String(40),
            nullable=False,
            default=CHUNK_WORLD_TEMPLATE_EARTH,
            index=True,
        )
        chunk_world_template_fallback = db.Column(
            db.String(40),
            nullable=False,
            default=CHUNK_WORLD_TEMPLATE_FLAT,
            index=True,
        )
        chunk_world_template_effective = db.Column(db.String(40), nullable=True, index=True)
        chunk_world_fallback_reason = db.Column(db.String(160), nullable=True, index=True)
        earth_reference_fingerprint = db.Column(db.String(128), nullable=True, index=True)

        chunk_provisioning_error_code = db.Column(db.String(160), nullable=True, index=True)
        chunk_provisioning_error_message = db.Column(db.Text, nullable=True)
        chunk_provisioning_attempt_count = db.Column(db.Integer, nullable=False, default=0)
        chunk_provisioning_started_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_provisioning_finished_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_provisioning_request_id = db.Column(db.String(180), nullable=True, index=True)
        chunk_provisioning_idempotency_key = db.Column(db.String(180), nullable=True, index=True)

        # Mirrored App-membership -> Chunk Project Access synchronization state.
        chunk_access_sync_status = db.Column(
            db.String(40),
            nullable=False,
            default=CHUNK_ACCESS_SYNC_PENDING,
            index=True,
        )
        chunk_access_sync_error_code = db.Column(db.String(160), nullable=True, index=True)
        chunk_access_sync_error_message = db.Column(db.Text, nullable=True)
        chunk_access_sync_attempt_count = db.Column(db.Integer, nullable=False, default=0)
        chunk_access_sync_started_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_access_synced_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_access_sync_request_id = db.Column(db.String(180), nullable=True, index=True)

        # References into other microservices.
        plan2d_id = db.Column(db.String(160), nullable=True, index=True)
        lv_id = db.Column(db.String(160), nullable=True, index=True)

        service_refs = db.Column(json_type(), nullable=False, default=dict)
        artifact_refs = db.Column(json_type(), nullable=False, default=dict)

        visibility = db.Column(db.String(40), nullable=False, default=PROJECT_VISIBILITY_PRIVATE, index=True)
        is_public = db.Column(db.Boolean, nullable=False, default=False, index=True)

        setup_status = db.Column(db.String(40), nullable=False, default=PROJECT_SETUP_DRAFT, index=True)
        setup_completed_at = db.Column(db.DateTime, nullable=True, index=True)

        status = db.Column(db.String(40), nullable=False, default=PROJECT_STATUS_ACTIVE, index=True)

        archived_at = db.Column(db.DateTime, nullable=True, index=True)
        archived_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        archive_reason = db.Column(db.Text, nullable=True)

        transferred_at = db.Column(db.DateTime, nullable=True)
        transferred_from_user_id = db.Column(db.Integer, nullable=True, index=True)

        last_opened_at = db.Column(db.DateTime, nullable=True, index=True)
        last_activity_at = db.Column(db.DateTime, nullable=True, index=True)

        sort_index = db.Column(db.Integer, nullable=False, default=0, index=True)

        settings = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        __serialize_exclude__ = ()

        def __repr__(self) -> str:
            try:
                return (
                    f"<Project id={self.id!r} public_id={self.public_id!r} "
                    f"scope={self.project_scope!r} demo={self.is_demo!r} name={self.name!r}>"
                )
            except Exception:
                return "<Project>"

        @validates("chunk_provisioning_status")
        def _validate_chunk_provisioning_status(self, _: str, value: Any) -> str:
            """
            Keep the legacy chunk readiness columns coherent when the new
            provisioning service writes its explicit status fields directly.
            """
            try:
                normalized = normalize_chunk_provisioning_status(
                    value,
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    requested_template=self.chunk_world_template_requested,
                    effective_template=self.chunk_world_template_effective,
                )

                if normalized in {
                    CHUNK_PROVISIONING_READY,
                    CHUNK_PROVISIONING_FALLBACK_READY,
                }:
                    self.chunk_status = CHUNK_STATUS_READY
                    self.chunk_ready = bool(self.chunk_project_id and self.chunk_world_id)
                    self.chunk_last_error = None

                elif normalized in {
                    CHUNK_PROVISIONING_FAILED,
                    CHUNK_PROVISIONING_REPAIR_REQUIRED,
                }:
                    self.chunk_status = CHUNK_STATUS_ERROR
                    self.chunk_ready = False

                elif normalized == CHUNK_PROVISIONING_DISABLED:
                    self.chunk_status = CHUNK_STATUS_DISABLED
                    self.chunk_ready = False

                elif not (self.chunk_project_id and self.chunk_world_id):
                    self.chunk_status = CHUNK_STATUS_PENDING
                    self.chunk_ready = False

                return normalized

            except Exception:
                return CHUNK_PROVISIONING_PENDING

        @validates(
            "chunk_world_template_requested",
            "chunk_world_template_fallback",
            "chunk_world_template_effective",
        )
        def _validate_chunk_world_template(self, key: str, value: Any) -> Optional[str]:
            try:
                if key == "chunk_world_template_effective":
                    return normalize_chunk_world_template(value, allow_none=True)

                default = (
                    CHUNK_WORLD_TEMPLATE_FLAT
                    if key == "chunk_world_template_fallback"
                    else CHUNK_WORLD_TEMPLATE_EARTH
                )
                return normalize_chunk_world_template(value, default)

            except Exception:
                return (
                    None
                    if key == "chunk_world_template_effective"
                    else (
                        CHUNK_WORLD_TEMPLATE_FLAT
                        if key == "chunk_world_template_fallback"
                        else CHUNK_WORLD_TEMPLATE_EARTH
                    )
                )

        @validates("chunk_access_sync_status")
        def _validate_chunk_access_sync_status(self, _: str, value: Any) -> str:
            return normalize_chunk_access_sync_status(value)

        @property
        def project_id(self) -> str:
            try:
                return self.public_id or str(self.id or "")
            except Exception:
                return ""

        @property
        def app_project_public_id(self) -> str:
            return self.project_id

        @property
        def display_name(self) -> str:
            try:
                return safe_str(self.name, "Unbenanntes Projekt", 255) or "Unbenanntes Projekt"
            except Exception:
                return "Unbenanntes Projekt"

        @property
        def title(self) -> str:
            return self.display_name

        @property
        def is_archived(self) -> bool:
            try:
                return self.archived_at is not None or self.status == PROJECT_STATUS_ARCHIVED
            except Exception:
                return False

        @property
        def is_expired(self) -> bool:
            try:
                if self.status == PROJECT_STATUS_EXPIRED:
                    return True

                if self.is_demo and self.demo_expires_at is not None:
                    return self.demo_expires_at <= utcnow()

                return False
            except Exception:
                return False

        @property
        def is_active(self) -> bool:
            try:
                return (
                    self.status == PROJECT_STATUS_ACTIVE
                    and self.deleted_at is None
                    and self.archived_at is None
                    and not self.is_expired
                )
            except Exception:
                return False

        @property
        def is_configured(self) -> bool:
            try:
                return bool(
                    is_configured_status(self.setup_status)
                    or self.setup_completed_at is not None
                )
            except Exception:
                return False

        @property
        def is_unlisted(self) -> bool:
            try:
                return self.visibility == PROJECT_VISIBILITY_UNLISTED
            except Exception:
                return False

        @property
        def is_private(self) -> bool:
            try:
                return self.visibility == PROJECT_VISIBILITY_PRIVATE
            except Exception:
                return True

        @property
        def has_coordinates(self) -> bool:
            try:
                return self.latitude is not None and self.longitude is not None
            except Exception:
                return False

        @property
        def coordinates(self) -> Optional[Dict[str, Any]]:
            try:
                if not self.has_coordinates:
                    return None

                return {
                    "lat": self.latitude,
                    "lng": self.longitude,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "srid": self.coordinate_srid or "EPSG:4326",
                }

            except Exception:
                return None

        @property
        def address(self) -> Dict[str, Any]:
            try:
                return {
                    "text": self.address_text,
                    "street": self.street,
                    "house_number": self.house_number,
                    "postal_code": self.postal_code,
                    "city": self.city,
                    "region": self.region,
                    "country": self.country,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "coordinate_srid": self.coordinate_srid,
                }
            except Exception:
                return {}

        @property
        def public_url(self) -> str:
            return _public_project_url(self.public_id)

        @property
        def workspace_url(self) -> str:
            return _project_workspace_path(self.public_id)

        @property
        def has_chunk_project(self) -> bool:
            try:
                return bool(self.chunk_project_id)
            except Exception:
                return False

        @property
        def has_chunk_world(self) -> bool:
            try:
                return bool(self.chunk_project_id and self.chunk_world_id)
            except Exception:
                return False

        @property
        def is_chunk_ready(self) -> bool:
            try:
                explicit_ready = self.chunk_provisioning_status in {
                    CHUNK_PROVISIONING_READY,
                    CHUNK_PROVISIONING_FALLBACK_READY,
                }
                return bool(
                    self.has_chunk_world
                    and (
                        explicit_ready
                        or (
                            self.chunk_ready
                            and self.chunk_status == CHUNK_STATUS_READY
                        )
                    )
                )
            except Exception:
                return False

        @property
        def uses_earth_world(self) -> bool:
            try:
                return self.chunk_world_template_effective == CHUNK_WORLD_TEMPLATE_EARTH
            except Exception:
                return False

        @property
        def uses_flat_world(self) -> bool:
            try:
                return self.chunk_world_template_effective == CHUNK_WORLD_TEMPLATE_FLAT
            except Exception:
                return False

        @property
        def chunk_fallback_used(self) -> bool:
            try:
                return bool(
                    self.chunk_world_template_requested
                    and self.chunk_world_template_effective
                    and self.chunk_world_template_requested != self.chunk_world_template_effective
                )
            except Exception:
                return False

        @property
        def chunk_provisioning(self) -> Dict[str, Any]:
            try:
                metadata = safe_dict(self.metadata_json)
                metadata_state = safe_dict(metadata.get("chunkProvisioning"))

                status = normalize_chunk_provisioning_status(
                    self.chunk_provisioning_status or metadata_state.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    requested_template=(
                        self.chunk_world_template_requested
                        or metadata_state.get("requestedWorldTemplate")
                    ),
                    effective_template=(
                        self.chunk_world_template_effective
                        or metadata_state.get("effectiveWorldTemplate")
                    ),
                )

                requested = normalize_chunk_world_template(
                    self.chunk_world_template_requested
                    or metadata_state.get("requestedWorldTemplate"),
                    CHUNK_WORLD_TEMPLATE_EARTH,
                )
                fallback = normalize_chunk_world_template(
                    self.chunk_world_template_fallback
                    or metadata_state.get("fallbackWorldTemplate"),
                    CHUNK_WORLD_TEMPLATE_FLAT,
                )
                effective = normalize_chunk_world_template(
                    self.chunk_world_template_effective
                    or metadata_state.get("effectiveWorldTemplate"),
                    allow_none=True,
                )

                return {
                    "status": status,
                    "requested_world_template": requested,
                    "requestedWorldTemplate": requested,
                    "fallback_world_template": fallback,
                    "fallbackWorldTemplate": fallback,
                    "effective_world_template": effective,
                    "effectiveWorldTemplate": effective,
                    "fallback_used": bool(requested and effective and requested != effective),
                    "fallbackUsed": bool(requested and effective and requested != effective),
                    "fallback_reason": (
                        self.chunk_world_fallback_reason
                        or metadata_state.get("fallbackReason")
                    ),
                    "fallbackReason": (
                        self.chunk_world_fallback_reason
                        or metadata_state.get("fallbackReason")
                    ),
                    "earth_reference_fingerprint": (
                        self.earth_reference_fingerprint
                        or metadata_state.get("earthReferenceFingerprint")
                    ),
                    "earthReferenceFingerprint": (
                        self.earth_reference_fingerprint
                        or metadata_state.get("earthReferenceFingerprint")
                    ),
                    "attempt_count": safe_int(
                        self.chunk_provisioning_attempt_count
                        or metadata_state.get("attemptCount"),
                        0,
                        minimum=0,
                    ),
                    "attemptCount": safe_int(
                        self.chunk_provisioning_attempt_count
                        or metadata_state.get("attemptCount"),
                        0,
                        minimum=0,
                    ),
                    "started_at": (
                        isoformat(self.chunk_provisioning_started_at)
                        or metadata_state.get("lastStartedAt")
                    ),
                    "startedAt": (
                        isoformat(self.chunk_provisioning_started_at)
                        or metadata_state.get("lastStartedAt")
                    ),
                    "finished_at": (
                        isoformat(self.chunk_provisioning_finished_at)
                        or metadata_state.get("lastFinishedAt")
                    ),
                    "finishedAt": (
                        isoformat(self.chunk_provisioning_finished_at)
                        or metadata_state.get("lastFinishedAt")
                    ),
                    "request_id": (
                        self.chunk_provisioning_request_id
                        or metadata_state.get("lastRequestId")
                    ),
                    "requestId": (
                        self.chunk_provisioning_request_id
                        or metadata_state.get("lastRequestId")
                    ),
                    "error_code": (
                        self.chunk_provisioning_error_code
                        or metadata_state.get("lastErrorCode")
                    ),
                    "errorCode": (
                        self.chunk_provisioning_error_code
                        or metadata_state.get("lastErrorCode")
                    ),
                    "error_message": (
                        self.chunk_provisioning_error_message
                        or metadata_state.get("lastErrorMessage")
                    ),
                    "errorMessage": (
                        self.chunk_provisioning_error_message
                        or metadata_state.get("lastErrorMessage")
                    ),
                    "ready": bool(
                        self.chunk_project_id
                        and self.chunk_world_id
                        and status in {
                            CHUNK_PROVISIONING_READY,
                            CHUNK_PROVISIONING_FALLBACK_READY,
                        }
                    ),
                }

            except Exception:
                return {
                    "status": CHUNK_PROVISIONING_PENDING,
                    "requestedWorldTemplate": CHUNK_WORLD_TEMPLATE_EARTH,
                    "fallbackWorldTemplate": CHUNK_WORLD_TEMPLATE_FLAT,
                    "effectiveWorldTemplate": None,
                    "ready": False,
                }

        @property
        def chunk_access_sync(self) -> Dict[str, Any]:
            try:
                return {
                    "status": normalize_chunk_access_sync_status(
                        self.chunk_access_sync_status
                    ),
                    "attempt_count": safe_int(
                        self.chunk_access_sync_attempt_count,
                        0,
                        minimum=0,
                    ),
                    "attemptCount": safe_int(
                        self.chunk_access_sync_attempt_count,
                        0,
                        minimum=0,
                    ),
                    "started_at": isoformat(self.chunk_access_sync_started_at),
                    "startedAt": isoformat(self.chunk_access_sync_started_at),
                    "synced_at": isoformat(self.chunk_access_synced_at),
                    "syncedAt": isoformat(self.chunk_access_synced_at),
                    "request_id": self.chunk_access_sync_request_id,
                    "requestId": self.chunk_access_sync_request_id,
                    "error_code": self.chunk_access_sync_error_code,
                    "errorCode": self.chunk_access_sync_error_code,
                    "error_message": self.chunk_access_sync_error_message,
                    "errorMessage": self.chunk_access_sync_error_message,
                }
            except Exception:
                return {
                    "status": CHUNK_ACCESS_SYNC_PENDING,
                    "attemptCount": 0,
                }

        @property
        def demo_remaining_seconds(self) -> Optional[int]:
            try:
                if not self.is_demo or self.demo_expires_at is None:
                    return None

                remaining = int((self.demo_expires_at - utcnow()).total_seconds())
                return max(0, remaining)

            except Exception:
                return None

        @property
        def chunk_refs(self) -> Dict[str, Any]:
            return _chunk_refs_from_sources(
                direct_project_id=self.chunk_project_id,
                direct_universe_id=self.chunk_universe_id,
                direct_world_id=self.chunk_world_id,
                direct_status=self.chunk_status,
                direct_ready=self.chunk_ready,
                direct_route_hints=self.chunk_route_hints,
                direct_error=self.chunk_last_error,
                direct_provisioned_at=self.chunk_provisioned_at,
                service_refs=self.service_refs,
                metadata_json=self.metadata_json,
            )

        def sync_chunk_refs(self) -> None:
            """
            Synchronize direct chunk columns with service_refs["chunk"] and metadata["chunk"].
            """
            try:
                refs = self.chunk_refs

                self.chunk_project_id = _clean_ref_id(refs.get("chunk_project_id"))
                self.chunk_universe_id = _clean_ref_id(refs.get("chunk_universe_id"))
                self.chunk_world_id = _clean_ref_id(refs.get("chunk_world_id"))
                self.chunk_status = normalize_chunk_status(
                    refs.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                )
                self.chunk_ready = bool(
                    self.chunk_project_id
                    and self.chunk_world_id
                    and self.chunk_status == CHUNK_STATUS_READY
                )
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                service_refs = safe_dict(self.service_refs)
                service_refs["chunk"] = _chunk_refs_to_service_ref(
                    build_chunk_refs(
                        chunk_project_id=self.chunk_project_id,
                        chunk_universe_id=self.chunk_universe_id,
                        chunk_world_id=self.chunk_world_id,
                        status=self.chunk_status,
                        route_hints=self.chunk_route_hints,
                        error=self.chunk_last_error,
                        provisioned_at=self.chunk_provisioned_at,
                    )
                )
                service_refs["chunk"]["provisioning"] = self.chunk_provisioning
                service_refs["chunk"]["accessSync"] = self.chunk_access_sync
                self.service_refs = service_refs

                metadata = safe_dict(self.metadata_json)
                metadata["chunk"] = _chunk_refs_to_service_ref(service_refs["chunk"])
                metadata["chunk"]["provisioning"] = self.chunk_provisioning
                metadata["chunk"]["accessSync"] = self.chunk_access_sync
                self.metadata_json = metadata

            except Exception:
                pass

        def set_chunk_refs(
            self,
            *,
            chunk_project_id: Any = None,
            chunk_universe_id: Any = None,
            chunk_world_id: Any = None,
            route_hints: Optional[Mapping[str, Any]] = None,
            status: Any = CHUNK_STATUS_READY,
            error: Optional[Mapping[str, Any]] = None,
            provisioned_at: Any = None,
            requested_world_template: Any = None,
            fallback_world_template: Any = None,
            effective_world_template: Any = None,
            fallback_reason: Any = None,
            earth_reference_fingerprint: Any = None,
        ) -> None:
            try:
                refs = build_chunk_refs(
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    status=status,
                    route_hints=route_hints,
                    error=error,
                    provisioned_at=provisioned_at or self.chunk_provisioned_at or utcnow(),
                )

                self.chunk_project_id = refs.get("chunk_project_id")
                self.chunk_universe_id = refs.get("chunk_universe_id")
                self.chunk_world_id = refs.get("chunk_world_id")
                self.chunk_status = refs.get("status") or CHUNK_STATUS_PENDING
                self.chunk_ready = bool(refs.get("ready"))
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                if requested_world_template is not None:
                    self.chunk_world_template_requested = normalize_chunk_world_template(
                        requested_world_template,
                        CHUNK_WORLD_TEMPLATE_EARTH,
                    )

                if fallback_world_template is not None:
                    self.chunk_world_template_fallback = normalize_chunk_world_template(
                        fallback_world_template,
                        CHUNK_WORLD_TEMPLATE_FLAT,
                    )

                if effective_world_template is not None:
                    self.chunk_world_template_effective = normalize_chunk_world_template(
                        effective_world_template,
                        allow_none=True,
                    )

                if fallback_reason is not None:
                    self.chunk_world_fallback_reason = safe_str(
                        fallback_reason,
                        "",
                        160,
                    ) or None

                if earth_reference_fingerprint is not None:
                    self.earth_reference_fingerprint = safe_str(
                        earth_reference_fingerprint,
                        "",
                        128,
                    ) or None

                if self.chunk_ready:
                    self.chunk_provisioning_status = normalize_chunk_provisioning_status(
                        CHUNK_PROVISIONING_READY,
                        has_refs=True,
                        requested_template=self.chunk_world_template_requested,
                        effective_template=self.chunk_world_template_effective,
                    )
                    self.chunk_provisioning_error_code = None
                    self.chunk_provisioning_error_message = None

                if self.chunk_ready and not self.chunk_provisioned_at:
                    self.chunk_provisioned_at = utcnow()

                self.sync_chunk_refs()
                self.touch()

            except Exception:
                pass

        def mark_chunk_pending(self) -> None:
            try:
                self.chunk_status = CHUNK_STATUS_PENDING
                self.chunk_ready = False
                self.chunk_provisioning_status = CHUNK_PROVISIONING_PENDING
                self.chunk_provisioning_error_code = None
                self.chunk_provisioning_error_message = None
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_ready(
            self,
            *,
            chunk_project_id: Any = None,
            chunk_universe_id: Any = None,
            chunk_world_id: Any = None,
            route_hints: Optional[Mapping[str, Any]] = None,
        ) -> None:
            self.set_chunk_refs(
                chunk_project_id=chunk_project_id or self.chunk_project_id,
                chunk_universe_id=chunk_universe_id or self.chunk_universe_id,
                chunk_world_id=chunk_world_id or self.chunk_world_id,
                route_hints=route_hints or self.chunk_route_hints,
                status=CHUNK_STATUS_READY,
                error=None,
                provisioned_at=self.chunk_provisioned_at or utcnow(),
                requested_world_template=self.chunk_world_template_requested,
                fallback_world_template=self.chunk_world_template_fallback,
                effective_world_template=(
                    self.chunk_world_template_effective
                    or self.chunk_world_template_requested
                ),
                fallback_reason=self.chunk_world_fallback_reason,
                earth_reference_fingerprint=self.earth_reference_fingerprint,
            )

        def mark_chunk_error(self, error: Optional[Mapping[str, Any]] = None) -> None:
            try:
                clean_error = safe_dict(error)
                self.chunk_status = CHUNK_STATUS_ERROR
                self.chunk_ready = False
                self.chunk_last_error = clean_error
                self.chunk_provisioning_status = CHUNK_PROVISIONING_FAILED
                self.chunk_provisioning_error_code = safe_str(
                    clean_error.get("code"),
                    "chunk_provisioning_failed",
                    160,
                ) or "chunk_provisioning_failed"
                self.chunk_provisioning_error_message = safe_str(
                    clean_error.get("message"),
                    "Chunk provisioning failed.",
                    2000,
                ) or "Chunk provisioning failed."
                self.chunk_provisioning_finished_at = utcnow()
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_disabled(self) -> None:
            try:
                self.chunk_status = CHUNK_STATUS_DISABLED
                self.chunk_ready = False
                self.chunk_provisioning_status = CHUNK_PROVISIONING_DISABLED
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_provisioning_started(
            self,
            *,
            request_id: Any = None,
            idempotency_key: Any = None,
            requested_world_template: Any = CHUNK_WORLD_TEMPLATE_EARTH,
            fallback_world_template: Any = CHUNK_WORLD_TEMPLATE_FLAT,
        ) -> None:
            try:
                self.chunk_provisioning_status = CHUNK_PROVISIONING_PROVISIONING
                self.chunk_world_template_requested = normalize_chunk_world_template(
                    requested_world_template,
                    CHUNK_WORLD_TEMPLATE_EARTH,
                )
                self.chunk_world_template_fallback = normalize_chunk_world_template(
                    fallback_world_template,
                    CHUNK_WORLD_TEMPLATE_FLAT,
                )
                self.chunk_provisioning_error_code = None
                self.chunk_provisioning_error_message = None
                self.chunk_provisioning_attempt_count = safe_int(
                    self.chunk_provisioning_attempt_count,
                    0,
                    minimum=0,
                ) + 1
                self.chunk_provisioning_started_at = utcnow()
                self.chunk_provisioning_finished_at = None
                self.chunk_provisioning_request_id = safe_str(
                    request_id,
                    "",
                    180,
                ) or None
                self.chunk_provisioning_idempotency_key = safe_str(
                    idempotency_key,
                    "",
                    180,
                ) or None
                self.touch()
            except Exception:
                pass

        def mark_chunk_provisioning_ready(
            self,
            *,
            effective_world_template: Any,
            fallback_reason: Any = None,
            earth_reference_fingerprint: Any = None,
        ) -> None:
            try:
                self.chunk_world_template_effective = normalize_chunk_world_template(
                    effective_world_template,
                    allow_none=True,
                )
                self.chunk_world_fallback_reason = safe_str(
                    fallback_reason,
                    "",
                    160,
                ) or None
                self.earth_reference_fingerprint = safe_str(
                    earth_reference_fingerprint,
                    "",
                    128,
                ) or None
                self.chunk_provisioning_status = normalize_chunk_provisioning_status(
                    CHUNK_PROVISIONING_READY,
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    requested_template=self.chunk_world_template_requested,
                    effective_template=self.chunk_world_template_effective,
                )
                self.chunk_status = CHUNK_STATUS_READY
                self.chunk_ready = bool(self.chunk_project_id and self.chunk_world_id)
                self.chunk_last_error = None
                self.chunk_provisioning_error_code = None
                self.chunk_provisioning_error_message = None
                self.chunk_provisioned_at = self.chunk_provisioned_at or utcnow()
                self.chunk_provisioning_finished_at = utcnow()
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_provisioning_failed(
            self,
            *,
            code: Any = "chunk_provisioning_failed",
            message: Any = "Chunk provisioning failed.",
            repair_required: bool = False,
        ) -> None:
            try:
                self.chunk_provisioning_status = (
                    CHUNK_PROVISIONING_REPAIR_REQUIRED
                    if repair_required
                    else CHUNK_PROVISIONING_FAILED
                )
                self.chunk_provisioning_error_code = safe_str(
                    code,
                    "chunk_provisioning_failed",
                    160,
                ) or "chunk_provisioning_failed"
                self.chunk_provisioning_error_message = safe_str(
                    message,
                    "Chunk provisioning failed.",
                    2000,
                ) or "Chunk provisioning failed."
                self.chunk_status = CHUNK_STATUS_ERROR
                self.chunk_ready = False
                self.chunk_provisioning_finished_at = utcnow()
                self.chunk_last_error = {
                    "code": self.chunk_provisioning_error_code,
                    "message": self.chunk_provisioning_error_message,
                }
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_access_sync_started(self, *, request_id: Any = None) -> None:
            try:
                self.chunk_access_sync_status = CHUNK_ACCESS_SYNC_SYNCING
                self.chunk_access_sync_error_code = None
                self.chunk_access_sync_error_message = None
                self.chunk_access_sync_attempt_count = safe_int(
                    self.chunk_access_sync_attempt_count,
                    0,
                    minimum=0,
                ) + 1
                self.chunk_access_sync_started_at = utcnow()
                self.chunk_access_sync_request_id = safe_str(
                    request_id,
                    "",
                    180,
                ) or None
                self.touch()
            except Exception:
                pass

        def mark_chunk_access_synced(self) -> None:
            try:
                self.chunk_access_sync_status = CHUNK_ACCESS_SYNC_READY
                self.chunk_access_sync_error_code = None
                self.chunk_access_sync_error_message = None
                self.chunk_access_synced_at = utcnow()
                self.touch()
            except Exception:
                pass

        def mark_chunk_access_sync_failed(
            self,
            *,
            code: Any = "chunk_access_sync_failed",
            message: Any = "Chunk access synchronization failed.",
            repair_required: bool = False,
        ) -> None:
            try:
                self.chunk_access_sync_status = (
                    CHUNK_ACCESS_SYNC_REPAIR_REQUIRED
                    if repair_required
                    else CHUNK_ACCESS_SYNC_FAILED
                )
                self.chunk_access_sync_error_code = safe_str(
                    code,
                    "chunk_access_sync_failed",
                    160,
                ) or "chunk_access_sync_failed"
                self.chunk_access_sync_error_message = safe_str(
                    message,
                    "Chunk access synchronization failed.",
                    2000,
                ) or "Chunk access synchronization failed."
                self.touch()
            except Exception:
                pass

        def mark_demo(
            self,
            *,
            demo_client_identity_id: Any = None,
            demo_session_id: Any = None,
            demo_expires_at: Any = None,
            reason: str = "",
        ) -> None:
            try:
                self.is_demo = True
                self.project_scope = PROJECT_SCOPE_DEMO
                self.owner_user_id = None
                self.auth_owner_user_id = None
                self.auth_account_id = None
                self.owner_subject_type = "guest"

                self.demo_client_identity_id = _clean_ref_id(demo_client_identity_id, 180)
                self.demo_session_id = _clean_ref_id(demo_session_id, 180)

                if demo_expires_at is not None:
                    self.demo_expires_at = demo_expires_at

                self.visibility = PROJECT_VISIBILITY_PRIVATE
                self.is_public = False

                metadata = safe_dict(self.metadata_json)
                demo_meta = safe_dict(metadata.get("vectoplan_demo"))
                demo_meta.update(
                    {
                        "enabled": True,
                        "reason": safe_str(reason, "", 500) or None,
                        "marked_at": isoformat(utcnow()),
                        "expires_at": isoformat(self.demo_expires_at),
                    }
                )
                metadata["vectoplan_demo"] = demo_meta
                self.metadata_json = metadata

                self.touch()
                self.normalize_lifecycle()

            except Exception:
                pass

        def expire_demo(self, reason: str = "expired") -> None:
            try:
                if not self.is_demo:
                    return

                self.status = PROJECT_STATUS_EXPIRED
                self.demo_expires_at = self.demo_expires_at or utcnow()

                metadata = safe_dict(self.metadata_json)
                demo_meta = safe_dict(metadata.get("vectoplan_demo"))
                demo_meta.update(
                    {
                        "expired": True,
                        "expired_at": isoformat(utcnow()),
                        "expired_reason": safe_str(reason, "", 500) or "expired",
                    }
                )
                metadata["vectoplan_demo"] = demo_meta
                self.metadata_json = metadata

                self.touch()

            except Exception:
                pass

        def set_auth_owner(
            self,
            *,
            owner_user_id: Any = None,
            auth_owner_user_id: Any = None,
            auth_account_id: Any = None,
            account_scoped: bool = False,
        ) -> None:
            try:
                self.owner_user_id = _clean_user_id(owner_user_id)
                self.auth_owner_user_id = _clean_ref_id(auth_owner_user_id)
                self.auth_account_id = _clean_ref_id(auth_account_id)
                self.owner_subject_type = "user" if self.auth_owner_user_id else None

                self.is_demo = False
                self.demo_client_identity_id = None
                self.demo_session_id = None
                self.demo_expires_at = None

                self.project_scope = normalize_project_scope(
                    PROJECT_SCOPE_ACCOUNT if account_scoped or self.auth_account_id else PROJECT_SCOPE_PERSONAL,
                    is_demo=False,
                    auth_account_id=self.auth_account_id,
                )

                self.touch()
                self.normalize_lifecycle()

            except Exception:
                pass

        def normalize_lifecycle(self) -> "Project":
            try:
                if not self.public_id:
                    self.public_id = project_public_id()

                self.name = safe_str(self.name, "Neues Projekt", 255) or "Neues Projekt"
                self.description = safe_str(self.description, "", 10000) or None

                self.is_demo = safe_bool(self.is_demo, False)

                self.owner_user_id = _clean_user_id(self.owner_user_id)
                self.auth_owner_user_id = _clean_ref_id(self.auth_owner_user_id)
                self.auth_account_id = _clean_ref_id(self.auth_account_id)
                self.owner_subject_type = safe_str(self.owner_subject_type, "", 40) or ("guest" if self.is_demo else "user")

                self.demo_client_identity_id = _clean_ref_id(self.demo_client_identity_id, 180)
                self.demo_session_id = _clean_ref_id(self.demo_session_id, 180)

                self.project_scope = normalize_project_scope(
                    self.project_scope,
                    is_demo=self.is_demo,
                    auth_account_id=self.auth_account_id,
                )

                if self.project_scope == PROJECT_SCOPE_DEMO:
                    self.is_demo = True

                if self.is_demo:
                    self.project_scope = PROJECT_SCOPE_DEMO
                    self.owner_user_id = None
                    self.auth_owner_user_id = None
                    self.auth_account_id = None
                    self.owner_subject_type = "guest"
                    self.visibility = PROJECT_VISIBILITY_PRIVATE
                    self.is_public = False

                self.client_id = safe_str(self.client_id, "", 80) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None

                self.address_text = safe_str(self.address_text, "", 2000) or None
                self.street = safe_str(self.street, "", 255) or None
                self.house_number = safe_str(self.house_number, "", 80) or None
                self.postal_code = safe_str(self.postal_code, "", 40) or None
                self.city = safe_str(self.city, "", 160) or None
                self.region = safe_str(self.region, "", 160) or None
                self.country = safe_str(self.country, "", 160) or None

                self.latitude = safe_float(self.latitude, None)
                self.longitude = safe_float(self.longitude, None)
                self.coordinate_srid = safe_str(self.coordinate_srid, "EPSG:4326", 40) or "EPSG:4326"
                self.geocode_source = safe_str(self.geocode_source, "", 120) or None
                self.geocode_quality = safe_str(self.geocode_quality, "", 120) or None
                self.geocode_raw = safe_dict(self.geocode_raw) if self.geocode_raw is not None else None

                self.service_refs = safe_dict(self.service_refs)
                self.artifact_refs = safe_dict(self.artifact_refs)
                self.settings = safe_dict(self.settings)
                self.metadata_json = safe_dict(self.metadata_json)

                refs = _chunk_refs_from_sources(
                    direct_project_id=self.chunk_project_id,
                    direct_universe_id=self.chunk_universe_id,
                    direct_world_id=self.chunk_world_id,
                    direct_status=self.chunk_status,
                    direct_ready=self.chunk_ready,
                    direct_route_hints=self.chunk_route_hints,
                    direct_error=self.chunk_last_error,
                    direct_provisioned_at=self.chunk_provisioned_at,
                    service_refs=self.service_refs,
                    metadata_json=self.metadata_json,
                )

                self.chunk_project_id = _clean_ref_id(refs.get("chunk_project_id"))
                self.chunk_universe_id = _clean_ref_id(refs.get("chunk_universe_id"))
                self.chunk_world_id = _clean_ref_id(refs.get("chunk_world_id"))
                self.chunk_status = normalize_chunk_status(
                    refs.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                )
                self.chunk_ready = bool(
                    self.chunk_project_id
                    and self.chunk_world_id
                    and self.chunk_status == CHUNK_STATUS_READY
                )
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                provisioning_meta = safe_dict(
                    safe_dict(self.metadata_json).get("chunkProvisioning")
                )

                self.chunk_world_template_requested = normalize_chunk_world_template(
                    self.chunk_world_template_requested
                    or provisioning_meta.get("requestedWorldTemplate"),
                    CHUNK_WORLD_TEMPLATE_EARTH,
                )
                self.chunk_world_template_fallback = normalize_chunk_world_template(
                    self.chunk_world_template_fallback
                    or provisioning_meta.get("fallbackWorldTemplate"),
                    CHUNK_WORLD_TEMPLATE_FLAT,
                )
                self.chunk_world_template_effective = normalize_chunk_world_template(
                    self.chunk_world_template_effective
                    or provisioning_meta.get("effectiveWorldTemplate"),
                    allow_none=True,
                )
                self.chunk_world_fallback_reason = safe_str(
                    self.chunk_world_fallback_reason
                    or provisioning_meta.get("fallbackReason"),
                    "",
                    160,
                ) or None
                self.earth_reference_fingerprint = safe_str(
                    self.earth_reference_fingerprint
                    or provisioning_meta.get("earthReferenceFingerprint"),
                    "",
                    128,
                ) or None

                self.chunk_provisioning_attempt_count = safe_int(
                    self.chunk_provisioning_attempt_count
                    or provisioning_meta.get("attemptCount"),
                    0,
                    minimum=0,
                )
                self.chunk_provisioning_request_id = safe_str(
                    self.chunk_provisioning_request_id
                    or provisioning_meta.get("lastRequestId"),
                    "",
                    180,
                ) or None
                self.chunk_provisioning_idempotency_key = safe_str(
                    self.chunk_provisioning_idempotency_key
                    or provisioning_meta.get("lastIdempotencyKey"),
                    "",
                    180,
                ) or None
                self.chunk_provisioning_error_code = safe_str(
                    self.chunk_provisioning_error_code
                    or provisioning_meta.get("lastErrorCode"),
                    "",
                    160,
                ) or None
                self.chunk_provisioning_error_message = safe_str(
                    self.chunk_provisioning_error_message
                    or provisioning_meta.get("lastErrorMessage"),
                    "",
                    2000,
                ) or None

                self.chunk_provisioning_status = normalize_chunk_provisioning_status(
                    self.chunk_provisioning_status
                    or provisioning_meta.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    requested_template=self.chunk_world_template_requested,
                    effective_template=self.chunk_world_template_effective,
                )

                if (
                    self.chunk_project_id
                    and self.chunk_world_id
                    and self.chunk_provisioning_status
                    in {
                        CHUNK_PROVISIONING_READY,
                        CHUNK_PROVISIONING_FALLBACK_READY,
                    }
                ):
                    self.chunk_status = CHUNK_STATUS_READY
                    self.chunk_ready = True
                    self.chunk_last_error = None

                elif self.chunk_provisioning_status in {
                    CHUNK_PROVISIONING_FAILED,
                    CHUNK_PROVISIONING_REPAIR_REQUIRED,
                }:
                    self.chunk_status = CHUNK_STATUS_ERROR
                    self.chunk_ready = False

                elif self.chunk_provisioning_status == CHUNK_PROVISIONING_DISABLED:
                    self.chunk_status = CHUNK_STATUS_DISABLED
                    self.chunk_ready = False

                self.chunk_access_sync_status = normalize_chunk_access_sync_status(
                    self.chunk_access_sync_status
                )
                self.chunk_access_sync_error_code = safe_str(
                    self.chunk_access_sync_error_code,
                    "",
                    160,
                ) or None
                self.chunk_access_sync_error_message = safe_str(
                    self.chunk_access_sync_error_message,
                    "",
                    2000,
                ) or None
                self.chunk_access_sync_attempt_count = safe_int(
                    self.chunk_access_sync_attempt_count,
                    0,
                    minimum=0,
                )
                self.chunk_access_sync_request_id = safe_str(
                    self.chunk_access_sync_request_id,
                    "",
                    180,
                ) or None

                self.sync_chunk_refs()

                self.plan2d_id = safe_str(self.plan2d_id, "", 160) or None
                self.lv_id = safe_str(self.lv_id, "", 160) or None

                self.visibility = normalize_project_visibility(self.visibility, PROJECT_VISIBILITY_PRIVATE)
                self.is_public = self.visibility == PROJECT_VISIBILITY_PUBLIC or safe_bool(self.is_public, False)

                if self.is_public:
                    self.visibility = PROJECT_VISIBILITY_PUBLIC

                if self.is_demo:
                    self.visibility = PROJECT_VISIBILITY_PRIVATE
                    self.is_public = False

                self.setup_status = normalize_project_setup_status(self.setup_status, PROJECT_SETUP_DRAFT)

                if self.setup_completed_at and self.setup_status == PROJECT_SETUP_DRAFT:
                    self.setup_status = PROJECT_SETUP_CONFIGURED

                self.status = normalize_project_status(self.status, PROJECT_STATUS_ACTIVE)

                if self.is_expired and self.status != PROJECT_STATUS_DELETED:
                    self.status = PROJECT_STATUS_EXPIRED

                if self.deleted_at is not None:
                    self.status = PROJECT_STATUS_DELETED

                if self.archived_at is not None and self.status != PROJECT_STATUS_DELETED:
                    self.status = PROJECT_STATUS_ARCHIVED

                self.archived_by_user_id = _clean_user_id(self.archived_by_user_id)
                self.archive_reason = safe_str(self.archive_reason, "", 2000) or None
                self.transferred_from_user_id = _clean_user_id(self.transferred_from_user_id)
                self.sort_index = safe_int(self.sort_index, 0)

                return self

            except Exception:
                return self

        def normalize(self) -> "Project":
            return self.normalize_lifecycle()

        def update_metadata(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.metadata_json = data
                else:
                    self.metadata_json = merge_dicts(self.metadata_json, data)

                if "chunk" in data:
                    self.sync_chunk_refs()

                if "vectoplan_demo" in data:
                    demo_meta = safe_dict(data.get("vectoplan_demo"))
                    if safe_bool(demo_meta.get("enabled"), False):
                        self.is_demo = True
                        self.project_scope = PROJECT_SCOPE_DEMO

                self.touch()
                self.normalize_lifecycle()

            except Exception:
                pass

        def update_settings(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.settings = data
                else:
                    self.settings = merge_dicts(self.settings, data)

                self.touch()

            except Exception:
                pass

        def update_address(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                if "address_text" in data or "address" in data:
                    address_value = data.get("address_text")
                    if address_value is None and not isinstance(data.get("address"), Mapping):
                        address_value = data.get("address")
                    self.address_text = safe_str(address_value, "", 2000) or None

                address_obj = safe_dict(data.get("address")) if isinstance(data.get("address"), Mapping) else data

                for field in ("street", "house_number", "postal_code", "city", "region", "country"):
                    if field in address_obj:
                        setattr(self, field, safe_str(address_obj.get(field), "", 255) or None)

                self.touch()

            except Exception:
                pass

        def update_coordinates(
            self,
            *,
            latitude: Any = None,
            longitude: Any = None,
            srid: Any = None,
            geocode_source: Any = None,
            geocode_quality: Any = None,
            geocode_raw: Any = None,
        ) -> None:
            try:
                lat = safe_float(latitude, None)
                lon = safe_float(longitude, None)

                if lat is not None:
                    self.latitude = lat

                if lon is not None:
                    self.longitude = lon

                if srid is not None:
                    self.coordinate_srid = safe_str(srid, "EPSG:4326", 40) or "EPSG:4326"

                if geocode_source is not None:
                    self.geocode_source = safe_str(geocode_source, "", 120) or None

                if geocode_quality is not None:
                    self.geocode_quality = safe_str(geocode_quality, "", 120) or None

                if geocode_raw is not None:
                    self.geocode_raw = safe_dict(geocode_raw)

                self.touch()

            except Exception:
                pass

        def update_service_refs(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.service_refs = data
                else:
                    self.service_refs = merge_dicts(self.service_refs, data)

                direct_or_nested = _chunk_payload_from_mapping(data)

                if data.get("chunk_project_id") or data.get("chunkProjectId") or direct_or_nested.get("chunk_project_id"):
                    self.chunk_project_id = (
                        _clean_ref_id(data.get("chunk_project_id"))
                        or _clean_ref_id(data.get("chunkProjectId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_project_id"))
                    )

                if data.get("chunk_universe_id") or data.get("chunkUniverseId") or direct_or_nested.get("chunk_universe_id"):
                    self.chunk_universe_id = (
                        _clean_ref_id(data.get("chunk_universe_id"))
                        or _clean_ref_id(data.get("chunkUniverseId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_universe_id"))
                    )

                if data.get("chunk_world_id") or data.get("chunkWorldId") or direct_or_nested.get("chunk_world_id"):
                    self.chunk_world_id = (
                        _clean_ref_id(data.get("chunk_world_id"))
                        or _clean_ref_id(data.get("chunkWorldId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_world_id"))
                    )

                if direct_or_nested.get("route_hints"):
                    self.chunk_route_hints = safe_dict(direct_or_nested.get("route_hints"))

                if direct_or_nested.get("error"):
                    self.chunk_last_error = safe_dict(direct_or_nested.get("error"))

                if direct_or_nested.get("status"):
                    self.chunk_status = normalize_chunk_status(
                        direct_or_nested.get("status"),
                        has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    )

                if data.get("plan2d_id"):
                    self.plan2d_id = safe_str(data.get("plan2d_id"), "", 160) or None

                if data.get("lv_id"):
                    self.lv_id = safe_str(data.get("lv_id"), "", 160) or None

                self.sync_chunk_refs()
                self.touch()

            except Exception:
                pass

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                for field in (
                    "name",
                    "description",
                    "address_text",
                    "street",
                    "house_number",
                    "postal_code",
                    "city",
                    "region",
                    "country",
                    "client_id",
                    "conversation_id",
                    "chunk_project_id",
                    "chunk_universe_id",
                    "chunk_world_id",
                    "chunk_status",
                    "chunk_provisioning_status",
                    "chunk_world_template_requested",
                    "chunk_world_template_fallback",
                    "chunk_world_template_effective",
                    "chunk_world_fallback_reason",
                    "earth_reference_fingerprint",
                    "chunk_provisioning_error_code",
                    "chunk_provisioning_error_message",
                    "chunk_provisioning_request_id",
                    "chunk_provisioning_idempotency_key",
                    "chunk_access_sync_status",
                    "chunk_access_sync_error_code",
                    "chunk_access_sync_error_message",
                    "chunk_access_sync_request_id",
                    "plan2d_id",
                    "lv_id",
                    "setup_status",
                    "status",
                    "visibility",
                    "auth_owner_user_id",
                    "auth_account_id",
                    "owner_subject_type",
                    "project_scope",
                    "demo_client_identity_id",
                    "demo_session_id",
                ):
                    if field in data:
                        setattr(self, field, data.get(field))

                if "owner_user_id" in data:
                    self.owner_user_id = _clean_user_id(data.get("owner_user_id"))

                if "is_demo" in data or "demo" in data:
                    self.is_demo = safe_bool(data.get("is_demo", data.get("demo")), False)

                if "demo_expires_at" in data:
                    self.demo_expires_at = data.get("demo_expires_at")

                if "chunkProjectId" in data:
                    self.chunk_project_id = _clean_ref_id(data.get("chunkProjectId"))

                if "chunkUniverseId" in data:
                    self.chunk_universe_id = _clean_ref_id(data.get("chunkUniverseId"))

                if "chunkWorldId" in data:
                    self.chunk_world_id = _clean_ref_id(data.get("chunkWorldId"))

                if "chunkStatus" in data:
                    self.chunk_status = normalize_chunk_status(data.get("chunkStatus"))

                if "chunkReady" in data or "chunk_ready" in data:
                    self.chunk_ready = safe_bool(data.get("chunkReady", data.get("chunk_ready")), False)

                if "chunkRouteHints" in data or "chunk_route_hints" in data:
                    self.chunk_route_hints = safe_dict(data.get("chunkRouteHints") or data.get("chunk_route_hints"))

                if "chunkError" in data or "chunk_last_error" in data:
                    self.chunk_last_error = safe_dict(data.get("chunkError") or data.get("chunk_last_error"))

                camel_fields = {
                    "chunkProvisioningStatus": "chunk_provisioning_status",
                    "requestedWorldTemplate": "chunk_world_template_requested",
                    "fallbackWorldTemplate": "chunk_world_template_fallback",
                    "effectiveWorldTemplate": "chunk_world_template_effective",
                    "fallbackReason": "chunk_world_fallback_reason",
                    "earthReferenceFingerprint": "earth_reference_fingerprint",
                    "chunkProvisioningErrorCode": "chunk_provisioning_error_code",
                    "chunkProvisioningErrorMessage": "chunk_provisioning_error_message",
                    "chunkAccessSyncStatus": "chunk_access_sync_status",
                }
                for source_field, target_field in camel_fields.items():
                    if source_field in data:
                        setattr(self, target_field, data.get(source_field))

                if "address" in data:
                    self.update_address(data)

                if "latitude" in data or "longitude" in data or "lat" in data or "lng" in data:
                    self.update_coordinates(
                        latitude=data.get("latitude", data.get("lat")),
                        longitude=data.get("longitude", data.get("lng")),
                        srid=data.get("coordinate_srid", data.get("srid")),
                    )

                if "is_public" in data or "public" in data:
                    self.is_public = safe_bool(data.get("is_public", data.get("public")), False)
                    self.visibility = PROJECT_VISIBILITY_PUBLIC if self.is_public else PROJECT_VISIBILITY_PRIVATE

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                if "service_refs" in data or "serviceRefs" in data:
                    self.update_service_refs(data.get("service_refs") or data.get("serviceRefs"))

                self.normalize_lifecycle()
                self.touch()

            except Exception:
                pass

        def mark_defined(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_DEFINED
                self.touch()
            except Exception:
                pass

        def mark_configured(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_CONFIGURED
                self.setup_completed_at = self.setup_completed_at or utcnow()
                self.touch()
            except Exception:
                pass

        def mark_draft(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_DRAFT
                self.setup_completed_at = None
                self.touch()
            except Exception:
                pass

        def mark_opened(self) -> None:
            try:
                self.last_opened_at = utcnow()
                self.last_activity_at = self.last_opened_at
                self.touch()
            except Exception:
                pass

        def archive(self, *, user_id: Optional[int] = None, reason: str = "") -> None:
            try:
                self.status = PROJECT_STATUS_ARCHIVED
                self.archived_at = utcnow()
                self.archived_by_user_id = _clean_user_id(user_id)
                self.archive_reason = safe_str(reason, "", 2000) or None
                self.touch()
            except Exception:
                pass

        def restore_archive(self) -> None:
            try:
                if self.is_demo and self.is_expired:
                    return

                self.status = PROJECT_STATUS_ACTIVE
                self.archived_at = None
                self.archived_by_user_id = None
                self.archive_reason = None
                self.touch()
            except Exception:
                pass

        def transfer_ownership(
            self,
            *,
            new_owner_user_id: Any = None,
            new_auth_owner_user_id: Any = None,
            new_auth_account_id: Any = None,
        ) -> None:
            try:
                if self.is_demo:
                    return

                old_owner = _clean_user_id(self.owner_user_id)
                new_owner = _clean_user_id(new_owner_user_id)

                self.transferred_from_user_id = old_owner
                self.owner_user_id = new_owner
                self.auth_owner_user_id = _clean_ref_id(new_auth_owner_user_id) or self.auth_owner_user_id
                self.auth_account_id = _clean_ref_id(new_auth_account_id) or self.auth_account_id
                self.transferred_at = utcnow()
                self.touch()
                self.normalize_lifecycle()

            except Exception:
                pass

        def set_visibility(self, visibility: Any = None, *, is_public: Optional[bool] = None) -> None:
            try:
                if self.is_demo:
                    self.visibility = PROJECT_VISIBILITY_PRIVATE
                    self.is_public = False
                    self.touch()
                    return

                if is_public is not None:
                    self.is_public = bool(is_public)
                    self.visibility = PROJECT_VISIBILITY_PUBLIC if self.is_public else PROJECT_VISIBILITY_PRIVATE
                else:
                    self.visibility = normalize_project_visibility(visibility, PROJECT_VISIBILITY_PRIVATE)
                    self.is_public = self.visibility == PROJECT_VISIBILITY_PUBLIC

                self.touch()

            except Exception:
                pass

        def build_paths(self) -> Dict[str, str]:
            return build_project_paths(self.public_id)

        def to_dict(
            self,
            *,
            include_private: bool = False,
            include_paths: bool = True,
            include_refs: bool = True,
            include_address: bool = True,
            include_permissions: bool = False,
            include_service_links: bool = False,
            include_versions: bool = False,
            include_embed_policy: bool = False,
            access: Optional[Mapping[str, Any]] = None,
            **_: Any,
        ) -> Dict[str, Any]:
            try:
                self.sync_chunk_refs()

                public_id = self.public_id or str(self.id or "")
                chunk_refs = self.chunk_refs
                chunk_provisioning = self.chunk_provisioning
                chunk_access_sync = self.chunk_access_sync

                payload: Dict[str, Any] = {
                    "id": self.id,
                    "project_id": self.id,
                    "public_id": public_id,
                    "projectPublicId": public_id,
                    "appProjectPublicId": public_id,
                    "name": self.name,
                    "display_name": self.display_name,
                    "displayName": self.display_name,
                    "description": self.description or "",
                    "owner_user_id": self.owner_user_id,
                    "auth_owner_user_id": self.auth_owner_user_id,
                    "auth_account_id": self.auth_account_id,
                    "owner_subject_type": self.owner_subject_type,
                    "project_scope": self.project_scope,
                    "projectScope": self.project_scope,
                    "is_demo": bool(self.is_demo),
                    "isDemo": bool(self.is_demo),
                    "demo_expires_at": isoformat(self.demo_expires_at),
                    "demoExpiresAt": isoformat(self.demo_expires_at),
                    "demo_remaining_seconds": self.demo_remaining_seconds,
                    "demoRemainingSeconds": self.demo_remaining_seconds,
                    "client_id": self.client_id,
                    "conversation_id": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "visibility": self.visibility,
                    "is_public": bool(self.is_public),
                    "is_unlisted": self.is_unlisted,
                    "setup_status": self.setup_status,
                    "setupStatus": self.setup_status,
                    "setup_completed_at": isoformat(self.setup_completed_at),
                    "is_configured": self.is_configured,
                    "isConfigured": self.is_configured,
                    "status": self.status,
                    "is_active": self.is_active,
                    "is_archived": self.is_archived,
                    "is_expired": self.is_expired,
                    "is_deleted": self.is_deleted,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "archived_at": isoformat(self.archived_at),
                    "deleted_at": isoformat(self.deleted_at),
                    "last_opened_at": isoformat(self.last_opened_at),
                    "last_activity_at": isoformat(self.last_activity_at),
                    "url": _public_project_url(public_id),
                    "href": _public_project_url(public_id),
                    "chunk": chunk_refs,
                    "chunk_ready": chunk_refs.get("ready"),
                    "chunkReady": chunk_refs.get("ready"),
                    "chunk_status": chunk_refs.get("status"),
                    "chunkStatus": chunk_refs.get("status"),
                    "chunk_project_id": chunk_refs.get("chunk_project_id"),
                    "chunkProjectId": chunk_refs.get("chunk_project_id"),
                    "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                    "chunkUniverseId": chunk_refs.get("chunk_universe_id"),
                    "chunk_world_id": chunk_refs.get("chunk_world_id"),
                    "chunkWorldId": chunk_refs.get("chunk_world_id"),
                    "chunk_route_hints": chunk_refs.get("route_hints"),
                    "chunkRouteHints": chunk_refs.get("route_hints"),
                    "chunk_provisioning": chunk_provisioning,
                    "chunkProvisioning": chunk_provisioning,
                    "chunk_provisioning_status": chunk_provisioning.get("status"),
                    "chunkProvisioningStatus": chunk_provisioning.get("status"),
                    "requested_world_template": chunk_provisioning.get("requestedWorldTemplate"),
                    "requestedWorldTemplate": chunk_provisioning.get("requestedWorldTemplate"),
                    "effective_world_template": chunk_provisioning.get("effectiveWorldTemplate"),
                    "effectiveWorldTemplate": chunk_provisioning.get("effectiveWorldTemplate"),
                    "fallback_world_template": chunk_provisioning.get("fallbackWorldTemplate"),
                    "fallbackWorldTemplate": chunk_provisioning.get("fallbackWorldTemplate"),
                    "fallback_used": chunk_provisioning.get("fallbackUsed"),
                    "fallbackUsed": chunk_provisioning.get("fallbackUsed"),
                    "fallback_reason": chunk_provisioning.get("fallbackReason"),
                    "fallbackReason": chunk_provisioning.get("fallbackReason"),
                    "earth_reference_fingerprint": chunk_provisioning.get("earthReferenceFingerprint"),
                    "earthReferenceFingerprint": chunk_provisioning.get("earthReferenceFingerprint"),
                    "chunk_access_sync_status": chunk_access_sync.get("status"),
                    "chunkAccessSyncStatus": chunk_access_sync.get("status"),
                }

                if include_address:
                    payload.update(
                        {
                            "address_text": self.address_text or "",
                            "address": self.address,
                            "street": self.street,
                            "house_number": self.house_number,
                            "postal_code": self.postal_code,
                            "city": self.city,
                            "region": self.region,
                            "country": self.country,
                            "latitude": self.latitude,
                            "longitude": self.longitude,
                            "coordinate_srid": self.coordinate_srid,
                            "coordinates": self.coordinates,
                            "geocode_source": self.geocode_source,
                            "geocode_quality": self.geocode_quality,
                        }
                    )

                if include_refs:
                    payload.update(
                        {
                            "plan2d_id": self.plan2d_id,
                            "plan2dId": self.plan2d_id,
                            "lv_id": self.lv_id,
                            "lvId": self.lv_id,
                            "service_refs": safe_dict(self.service_refs),
                            "serviceRefs": safe_dict(self.service_refs),
                            "artifact_refs": safe_dict(self.artifact_refs),
                            "artifactRefs": safe_dict(self.artifact_refs),
                        }
                    )

                if include_paths:
                    payload["paths"] = self.build_paths()
                    payload.update(
                        {
                            "projectPublicUrl": payload["paths"].get("projectPublicUrl"),
                            "projectPagePath": payload["paths"].get("projectPagePath"),
                            "projectUrl": payload["paths"].get("projectUrl"),
                            "editorPagePath": payload["paths"].get("editorPagePath"),
                            "editor3dPagePath": payload["paths"].get("editor3dPagePath"),
                            "mapPagePath": payload["paths"].get("mapPagePath"),
                        }
                    )

                if include_permissions and access is not None:
                    payload["access"] = safe_dict(access)

                if include_service_links:
                    payload.setdefault("service_links", [])

                if include_versions:
                    payload.setdefault("versions", [])

                if include_embed_policy:
                    payload.setdefault("embed_policy", None)

                if include_private:
                    payload.update(
                        {
                            "settings": safe_dict(self.settings),
                            "metadata": safe_dict(self.metadata_json),
                            "geocode_raw": safe_dict(self.geocode_raw),
                            "sort_index": self.sort_index,
                            "archived_by_user_id": self.archived_by_user_id,
                            "archive_reason": self.archive_reason,
                            "deleted_by_user_id": self.deleted_by_user_id,
                            "delete_reason": self.delete_reason,
                            "transferred_at": isoformat(self.transferred_at),
                            "transferred_from_user_id": self.transferred_from_user_id,
                            "demo_client_identity_id": self.demo_client_identity_id,
                            "demo_session_id": self.demo_session_id,
                            "chunk_last_error": safe_dict(self.chunk_last_error),
                            "chunkLastError": safe_dict(self.chunk_last_error),
                            "chunk_provisioned_at": isoformat(self.chunk_provisioned_at),
                            "chunkProvisionedAt": isoformat(self.chunk_provisioned_at),
                            "chunk_provisioning_details": chunk_provisioning,
                            "chunkProvisioningDetails": chunk_provisioning,
                            "chunk_access_sync": chunk_access_sync,
                            "chunkAccessSync": chunk_access_sync,
                            "chunk_provisioning_idempotency_key": self.chunk_provisioning_idempotency_key,
                            "chunkProvisioningIdempotencyKey": self.chunk_provisioning_idempotency_key,
                        }
                    )

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "public_id": getattr(self, "public_id", None),
                    "name": getattr(self, "name", "Projekt"),
                    "is_configured": False,
                    "is_demo": bool(getattr(self, "is_demo", False)),
                    "chunk_ready": False,
                    "chunkReady": False,
                }

        def to_sidebar_item(
            self,
            *,
            current_project_id: Optional[str] = None,
            current_chat_id: Optional[str] = None,
            include_meta: bool = False,
            **_: Any,
        ) -> Dict[str, Any]:
            try:
                self.sync_chunk_refs()

                public_id = self.public_id or str(self.id or "")
                subtitle = (
                    "Demo-Projekt"
                    if self.is_demo
                    else (
                        self.address_text
                        or self.city
                        or ("Projekt aktiv" if self.is_configured else "Projekt definieren")
                    )
                )

                is_active = False

                if current_project_id and str(current_project_id) == str(public_id):
                    is_active = True

                if current_chat_id and self.conversation_id and str(current_chat_id) == str(self.conversation_id):
                    is_active = True

                chunk_refs = self.chunk_refs

                item: Dict[str, Any] = {
                    "id": public_id,
                    "projectId": public_id,
                    "project_id": public_id,
                    "public_id": public_id,
                    "title": self.display_name,
                    "name": self.name,
                    "subtitle": subtitle,
                    "description": self.description or "",
                    "href": _public_project_url(public_id),
                    "url": _public_project_url(public_id),
                    "chatId": self.conversation_id or "",
                    "chat_id": self.conversation_id or "",
                    "conversationId": self.conversation_id or "",
                    "conversation_id": self.conversation_id or "",
                    "isActive": is_active,
                    "is_active": is_active,
                    "isConfigured": self.is_configured,
                    "is_configured": self.is_configured,
                    "setupStatus": self.setup_status,
                    "setup_status": self.setup_status,
                    "visibility": self.visibility,
                    "is_public": bool(self.is_public),
                    "projectScope": self.project_scope,
                    "project_scope": self.project_scope,
                    "isDemo": bool(self.is_demo),
                    "is_demo": bool(self.is_demo),
                    "demoExpiresAt": isoformat(self.demo_expires_at),
                    "demoRemainingSeconds": self.demo_remaining_seconds,
                    "chunkReady": chunk_refs.get("ready"),
                    "chunk_ready": chunk_refs.get("ready"),
                    "chunkStatus": chunk_refs.get("status"),
                    "chunk_status": chunk_refs.get("status"),
                    "chunkProjectId": chunk_refs.get("chunk_project_id"),
                    "chunk_project_id": chunk_refs.get("chunk_project_id"),
                    "chunkUniverseId": chunk_refs.get("chunk_universe_id"),
                    "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                    "chunkWorldId": chunk_refs.get("chunk_world_id"),
                    "chunk_world_id": chunk_refs.get("chunk_world_id"),
                    "chunkProvisioningStatus": self.chunk_provisioning.get("status"),
                    "chunk_provisioning_status": self.chunk_provisioning.get("status"),
                    "requestedWorldTemplate": self.chunk_provisioning.get("requestedWorldTemplate"),
                    "effectiveWorldTemplate": self.chunk_provisioning.get("effectiveWorldTemplate"),
                    "fallbackUsed": self.chunk_provisioning.get("fallbackUsed"),
                    "source": "api",
                    "initial": (self.display_name[:1] or "P").upper(),
                    "updatedAt": isoformat(self.updated_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_meta:
                    item["meta"] = {
                        "owner_user_id": self.owner_user_id,
                        "auth_owner_user_id": self.auth_owner_user_id,
                        "auth_account_id": self.auth_account_id,
                        "project_scope": self.project_scope,
                        "is_demo": bool(self.is_demo),
                        "demo_client_identity_id": self.demo_client_identity_id,
                        "demo_session_id": self.demo_session_id,
                        "demo_expires_at": isoformat(self.demo_expires_at),
                        "chunk": chunk_refs,
                        "chunk_project_id": chunk_refs.get("chunk_project_id"),
                        "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                        "chunk_world_id": chunk_refs.get("chunk_world_id"),
                        "chunk_provisioning": self.chunk_provisioning,
                        "chunk_access_sync": self.chunk_access_sync,
                        "plan2d_id": self.plan2d_id,
                        "lv_id": self.lv_id,
                        "status": self.status,
                    }

                return item

            except Exception:
                return {
                    "id": getattr(self, "public_id", None) or getattr(self, "id", None),
                    "title": getattr(self, "name", "Projekt"),
                    "href": "/",
                    "source": "fallback",
                    "isDemo": bool(getattr(self, "is_demo", False)),
                    "chunkReady": False,
                    "chunk_ready": False,
                }

    return Project


Project = _resolve_model("Project", "projects", _define_project_model)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def get_project_by_id(project_id: Any) -> Optional[Project]:
    try:
        resolved_id = safe_int(project_id, 0)
        if not resolved_id:
            return None

        return Project.query.get(resolved_id)

    except Exception:
        return None


def get_project_by_public_id(public_id_value: Any) -> Optional[Project]:
    try:
        value = safe_str(public_id_value, "", 180)
        if not value:
            return None

        return Project.query.filter_by(public_id=value).one_or_none()

    except Exception:
        return None


def get_project_by_conversation_id(conversation_id: Any) -> Optional[Project]:
    try:
        value = safe_str(conversation_id, "", 120)
        if not value:
            return None

        return Project.query.filter_by(conversation_id=value).one_or_none()

    except Exception:
        return None


def get_projects_by_owner_user_id(owner_user_id: Any, *, include_demo: bool = False) -> List[Project]:
    try:
        resolved_owner_user_id = _clean_user_id(owner_user_id)
        if not resolved_owner_user_id:
            return []

        query = Project.query.filter(Project.owner_user_id == resolved_owner_user_id)

        if not include_demo and hasattr(Project, "is_demo"):
            query = query.filter(Project.is_demo.is_(False))

        return list(query.order_by(Project.updated_at.desc()).all())

    except Exception:
        return []


def get_projects_by_auth_owner_user_id(auth_owner_user_id: Any, *, include_demo: bool = False) -> List[Project]:
    try:
        value = safe_str(auth_owner_user_id, "", 160)
        if not value:
            return []

        query = Project.query.filter(Project.auth_owner_user_id == value)

        if not include_demo and hasattr(Project, "is_demo"):
            query = query.filter(Project.is_demo.is_(False))

        return list(query.order_by(Project.updated_at.desc()).all())

    except Exception:
        return []


def get_projects_by_auth_account_id(auth_account_id: Any, *, include_demo: bool = False) -> List[Project]:
    try:
        value = safe_str(auth_account_id, "", 160)
        if not value:
            return []

        query = Project.query.filter(Project.auth_account_id == value)

        if not include_demo and hasattr(Project, "is_demo"):
            query = query.filter(Project.is_demo.is_(False))

        return list(query.order_by(Project.updated_at.desc()).all())

    except Exception:
        return []


def get_demo_project_by_session_id(demo_session_id: Any) -> Optional[Project]:
    try:
        value = safe_str(demo_session_id, "", 180)
        if not value:
            return None

        return Project.query.filter_by(
            demo_session_id=value,
            is_demo=True,
        ).one_or_none()

    except Exception:
        return None


def resolve_project(project_ref: Any) -> Optional[Project]:
    try:
        value = safe_str(project_ref, "", 180)
        if not value:
            return None

        project = get_project_by_public_id(value)
        if project is not None:
            return project

        numeric_id = safe_int(value, 0)
        if numeric_id:
            project = get_project_by_id(numeric_id)
            if project is not None:
                return project

        project = get_project_by_conversation_id(value)
        if project is not None:
            return project

        return None

    except Exception:
        return None


def serialize_project(project: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        if hasattr(project, "to_dict"):
            return project.to_dict(**kwargs)

        return {
            "id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "name": getattr(project, "name", "Projekt"),
            "description": getattr(project, "description", ""),
            "owner_user_id": getattr(project, "owner_user_id", None),
            "auth_owner_user_id": getattr(project, "auth_owner_user_id", None),
            "auth_account_id": getattr(project, "auth_account_id", None),
            "project_scope": getattr(project, "project_scope", None),
            "is_demo": bool(getattr(project, "is_demo", False)),
            "conversation_id": getattr(project, "conversation_id", None),
            "is_configured": bool(getattr(project, "is_configured", False)),
            "chunk_ready": bool(getattr(project, "chunk_ready", False)),
            "chunk_project_id": getattr(project, "chunk_project_id", None),
            "chunk_universe_id": getattr(project, "chunk_universe_id", None),
            "chunk_world_id": getattr(project, "chunk_world_id", None),
            "chunk_provisioning_status": getattr(
                project,
                "chunk_provisioning_status",
                CHUNK_PROVISIONING_PENDING,
            ),
            "requested_world_template": getattr(
                project,
                "chunk_world_template_requested",
                CHUNK_WORLD_TEMPLATE_EARTH,
            ),
            "effective_world_template": getattr(
                project,
                "chunk_world_template_effective",
                None,
            ),
        }

    except Exception:
        return {}


def serialize_project_sidebar_item(project: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        if hasattr(project, "to_sidebar_item"):
            return project.to_sidebar_item(**kwargs)

        payload = serialize_project(project)
        public_id = payload.get("public_id") or payload.get("id") or ""

        return {
            "id": public_id,
            "projectId": public_id,
            "public_id": public_id,
            "title": payload.get("name") or "Projekt",
            "subtitle": "Demo-Projekt" if payload.get("is_demo") else payload.get("address_text") or payload.get("setup_status") or "Projekt",
            "href": _public_project_url(public_id),
            "isDemo": bool(payload.get("is_demo")),
            "chunkReady": bool(payload.get("chunk_ready")),
            "chunkProjectId": payload.get("chunk_project_id"),
            "chunkWorldId": payload.get("chunk_world_id"),
            "source": "fallback",
        }

    except Exception:
        return {}


def build_project(
    *,
    owner_user_id: Optional[int] = None,
    auth_owner_user_id: Any = None,
    auth_account_id: Any = None,
    project_scope: str = PROJECT_SCOPE_PERSONAL,
    is_demo: bool = False,
    demo_client_identity_id: Any = None,
    demo_session_id: Any = None,
    demo_expires_at: Any = None,
    name: str = "",
    description: str = "",
    address_text: str = "",
    visibility: str = PROJECT_VISIBILITY_PRIVATE,
    conversation_id: str = "",
    client_id: str = "",
    **extra: Any,
) -> Project:
    project = Project()

    try:
        project.owner_user_id = _clean_user_id(owner_user_id)
        project.auth_owner_user_id = _clean_ref_id(auth_owner_user_id)
        project.auth_account_id = _clean_ref_id(auth_account_id)
        project.owner_subject_type = "guest" if is_demo else ("user" if project.auth_owner_user_id else None)

        project.is_demo = safe_bool(is_demo, False)
        project.project_scope = normalize_project_scope(
            project_scope,
            is_demo=project.is_demo,
            auth_account_id=project.auth_account_id,
        )

        project.demo_client_identity_id = _clean_ref_id(demo_client_identity_id, 180)
        project.demo_session_id = _clean_ref_id(demo_session_id, 180)
        project.demo_expires_at = demo_expires_at

        project.name = safe_str(name, "Neues Projekt", 255) or "Neues Projekt"
        project.description = safe_str(description, "", 10000) or None
        project.address_text = safe_str(address_text, "", 2000) or None

        project.visibility = normalize_project_visibility(visibility, PROJECT_VISIBILITY_PRIVATE)
        project.is_public = project.visibility == PROJECT_VISIBILITY_PUBLIC

        project.conversation_id = safe_str(conversation_id, "", 80) or None
        project.client_id = safe_str(client_id, "", 80) or None

        if extra:
            project.update_from_payload(extra)

        project.normalize_lifecycle()

        return project

    except Exception:
        return project


def get_project_model_classes() -> List[Any]:
    return [Project]


def get_project_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(Project.query.count())
        except Exception:
            count = -1

        table = getattr(Project, "__table__", None)
        columns = []

        try:
            columns = [str(column.name) for column in table.columns]
        except Exception:
            columns = []

        required_chunk_columns = [
            "chunk_project_id",
            "chunk_universe_id",
            "chunk_world_id",
            "chunk_status",
            "chunk_ready",
            "chunk_route_hints",
            "chunk_last_error",
            "chunk_provisioned_at",
        ]

        required_chunk_provisioning_columns = [
            "chunk_provisioning_status",
            "chunk_world_template_requested",
            "chunk_world_template_fallback",
            "chunk_world_template_effective",
            "chunk_world_fallback_reason",
            "earth_reference_fingerprint",
            "chunk_provisioning_error_code",
            "chunk_provisioning_error_message",
            "chunk_provisioning_attempt_count",
            "chunk_provisioning_started_at",
            "chunk_provisioning_finished_at",
            "chunk_provisioning_request_id",
            "chunk_provisioning_idempotency_key",
        ]

        required_chunk_access_sync_columns = [
            "chunk_access_sync_status",
            "chunk_access_sync_error_code",
            "chunk_access_sync_error_message",
            "chunk_access_sync_attempt_count",
            "chunk_access_sync_started_at",
            "chunk_access_synced_at",
            "chunk_access_sync_request_id",
        ]

        required_auth_demo_columns = [
            "owner_user_id",
            "auth_owner_user_id",
            "auth_account_id",
            "owner_subject_type",
            "project_scope",
            "is_demo",
            "demo_client_identity_id",
            "demo_session_id",
            "demo_expires_at",
        ]

        return {
            "ok": True,
            "models": ["Project"],
            "tables": [getattr(Project, "__tablename__", "projects")],
            "count": count,
            "columns": columns,
            "chunkIntegrationReady": all(column in columns for column in required_chunk_columns),
            "chunkProvisioningIntegrationReady": all(
                column in columns
                for column in required_chunk_provisioning_columns
            ),
            "chunkAccessSyncIntegrationReady": all(
                column in columns
                for column in required_chunk_access_sync_columns
            ),
            "authDemoIntegrationReady": all(column in columns for column in required_auth_demo_columns),
            "defaultWorldTemplate": CHUNK_WORLD_TEMPLATE_EARTH,
            "fallbackWorldTemplate": CHUNK_WORLD_TEMPLATE_FLAT,
            "defaultOwnerRemoved": True,
            "ownerUserIdNullable": True,
            "missingChunkColumns": [
                column
                for column in required_chunk_columns
                if column not in columns
            ],
            "missingChunkProvisioningColumns": [
                column
                for column in required_chunk_provisioning_columns
                if column not in columns
            ],
            "missingChunkAccessSyncColumns": [
                column
                for column in required_chunk_access_sync_columns
                if column not in columns
            ],
            "missingAuthDemoColumns": [
                column
                for column in required_auth_demo_columns
                if column not in columns
            ],
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["Project"],
            "tables": ["projects"],
            "error": str(exc),
            "defaultOwnerRemoved": True,
        }


__all__ = [
    "PROJECT_SETUP_DRAFT",
    "PROJECT_SETUP_DEFINED",
    "PROJECT_SETUP_CONFIGURED",
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_ARCHIVED",
    "PROJECT_STATUS_DELETED",
    "PROJECT_STATUS_EXPIRED",
    "PROJECT_VISIBILITY_PRIVATE",
    "PROJECT_VISIBILITY_UNLISTED",
    "PROJECT_VISIBILITY_SHARED",
    "PROJECT_VISIBILITY_PUBLIC",
    "PROJECT_SCOPE_PERSONAL",
    "PROJECT_SCOPE_ACCOUNT",
    "PROJECT_SCOPE_DEMO",
    "VALID_PROJECT_SCOPES",
    "VALID_PROJECT_VISIBILITIES",
    "CHUNK_STATUS_DISABLED",
    "CHUNK_STATUS_PENDING",
    "CHUNK_STATUS_READY",
    "CHUNK_STATUS_ERROR",
    "VALID_CHUNK_STATUSES",
    "CHUNK_PROVISIONING_PENDING",
    "CHUNK_PROVISIONING_PROVISIONING",
    "CHUNK_PROVISIONING_READY",
    "CHUNK_PROVISIONING_FALLBACK_READY",
    "CHUNK_PROVISIONING_FAILED",
    "CHUNK_PROVISIONING_REPAIR_REQUIRED",
    "CHUNK_PROVISIONING_DISABLED",
    "VALID_CHUNK_PROVISIONING_STATUSES",
    "CHUNK_WORLD_TEMPLATE_EARTH",
    "CHUNK_WORLD_TEMPLATE_FLAT",
    "VALID_CHUNK_WORLD_TEMPLATES",
    "CHUNK_ACCESS_SYNC_PENDING",
    "CHUNK_ACCESS_SYNC_SYNCING",
    "CHUNK_ACCESS_SYNC_READY",
    "CHUNK_ACCESS_SYNC_FAILED",
    "CHUNK_ACCESS_SYNC_REPAIR_REQUIRED",
    "CHUNK_ACCESS_SYNC_DISABLED",
    "VALID_CHUNK_ACCESS_SYNC_STATUSES",
    "Project",
    "normalize_project_setup_status",
    "normalize_project_status",
    "normalize_project_visibility",
    "normalize_project_scope",
    "normalize_chunk_status",
    "normalize_chunk_world_template",
    "normalize_chunk_provisioning_status",
    "normalize_chunk_access_sync_status",
    "is_configured_status",
    "build_chunk_refs",
    "build_project_paths",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_by_conversation_id",
    "get_projects_by_owner_user_id",
    "get_projects_by_auth_owner_user_id",
    "get_projects_by_auth_account_id",
    "get_demo_project_by_session_id",
    "resolve_project",
    "serialize_project",
    "serialize_project_sidebar_item",
    "build_project",
    "get_project_model_classes",
    "get_project_model_status",
]