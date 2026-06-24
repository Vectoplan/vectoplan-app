# services/vectoplan-app/models/__init__.py
from __future__ import annotations

"""
VECTOPLAN app model package.

Zweck:
- Ersetzt die alte flache Datei services/vectoplan-app/models.py.
- Hält bestehende Imports kompatibel:
    from models import Blob, Conversation
    from models import Project, ProjectVersion
- Registriert alle SQLAlchemy-Models beim Import dieses Packages.
- Nutzt die modularen Model-Dateien:
    base.py
    users.py
    legacy.py
    projects.py
    project_access.py
    project_embed.py
    project_links.py
    project_versions.py
    project_audit.py
- Lässt core.py vorerst als Kompatibilitätsschicht bestehen.
- Verhindert doppelte SQLAlchemy-Tabellenregistrierung, indem core.py im
  Normalfall nicht mehr importiert wird.

Wichtige Regel:
- Alle produktiven Models müssen hier importiert werden, bevor db.create_all()
  oder Migrationen laufen.
- vectoplan-app speichert nur Service-Referenzen zu vectoplan-chunk.
- vectoplan-app speichert keine Chunk-Zellen, keine Chunk-Snapshots und keine
  Chunk-Events.
"""

import importlib.util
from typing import Any, Dict, List, Tuple


# ─────────────────────────────────────────────────────────────
# Required modular model modules
# ─────────────────────────────────────────────────────────────

_REQUIRED_MODEL_MODULES: Tuple[str, ...] = (
    "base",
    "users",
    "legacy",
    "projects",
    "project_access",
    "project_embed",
    "project_links",
    "project_versions",
    "project_audit",
)

_OPTIONAL_MODEL_MODULES: Tuple[str, ...] = (
    # Future extension points.
    "project_comments",
    "project_tasks",
    "project_files",
    "project_notifications",
)

_MODEL_IMPORT_ERRORS: Dict[str, Dict[str, str]] = {}
_MODEL_SOURCE = "unknown"


def _module_spec_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(f"{__name__}.{module_name}") is not None
    except Exception:
        return False


def _missing_required_modules() -> List[str]:
    missing: List[str] = []

    for module_name in _REQUIRED_MODEL_MODULES:
        try:
            if not _module_spec_exists(module_name):
                missing.append(module_name)
        except Exception:
            missing.append(module_name)

    return missing


def _store_import_error(module_name: str, exc: BaseException) -> None:
    try:
        _MODEL_IMPORT_ERRORS[str(module_name)] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Primary modular imports
# ─────────────────────────────────────────────────────────────

_missing = _missing_required_modules()

if _missing:
    _MODEL_SOURCE = "core_fallback"
    _MODEL_IMPORT_ERRORS["missing_modular_modules"] = {
        "type": "MissingModelModules",
        "message": ", ".join(_missing),
    }

    try:
        from .core import (
            AppUser,
            Blob,
            Client,
            Conversation,
            ConversationState,
            IdempotencyKey,
            Job,
            MessageTemplate,
            Project,
            ProjectAuditEvent,
            ProjectEmbedPolicy,
            ProjectMembership,
            ProjectServiceLink,
            ProjectVersion,
            CORE_MODEL_CLASSES,
            _deep_merge_state,
            _is_legacy_viewer_key,
            _iso,
            _json_type,
            _legacy_backend_prefix,
            _normalize_project_role,
            _normalize_status,
            _normalize_visibility,
            _project_public_id,
            _public_id,
            _role,
            _role_permission_defaults,
            _safe_bool,
            _safe_dict,
            _safe_float,
            _safe_int,
            _safe_list,
            _safe_slug,
            _safe_str,
            _sanitize_viewer_selection,
            _utcnow,
            _uuid,
            _version_public_id,
        )
    except Exception as exc:
        raise RuntimeError(
            "Failed to initialize models package. "
            "Required modular model files are missing and core.py fallback also failed."
        ) from exc

    # Minimal fallback constants/helpers so imports do not fail if modular files
    # are missing during transitional deployments.
    PROJECT_SETUP_DRAFT = "draft"
    PROJECT_SETUP_DEFINED = "defined"
    PROJECT_SETUP_CONFIGURED = "configured"
    PROJECT_STATUS_ACTIVE = "active"
    PROJECT_STATUS_ARCHIVED = "archived"
    PROJECT_STATUS_DELETED = "deleted"
    PROJECT_VISIBILITY_PRIVATE = "private"
    PROJECT_VISIBILITY_SHARED = "shared"
    PROJECT_VISIBILITY_PUBLIC = "public"

    CHUNK_STATUS_DISABLED = "disabled"
    CHUNK_STATUS_PENDING = "pending"
    CHUNK_STATUS_READY = "ready"
    CHUNK_STATUS_ERROR = "error"
    VALID_CHUNK_STATUSES = {
        CHUNK_STATUS_DISABLED,
        CHUNK_STATUS_PENDING,
        CHUNK_STATUS_READY,
        CHUNK_STATUS_ERROR,
    }

    SERVICE_APP = "app"
    SERVICE_CHAT = "chat"
    SERVICE_CHUNK = "chunk"
    SERVICE_EDITOR3D = "editor3d"
    SERVICE_OPENLAYER = "openlayer"
    SERVICE_2D = "cad2d"
    SERVICE_LV = "lv"
    SERVICE_GEOSERVER = "geoserver"
    SERVICE_FILES = "files"
    SERVICE_VERSIONING = "versioning"
    SERVICE_EXTERNAL = "external"

    RESOURCE_PROJECT = "project"
    RESOURCE_CONVERSATION = "conversation"
    RESOURCE_WORLD = "world"
    RESOURCE_CHUNK_PROJECT = "chunk_project"
    RESOURCE_CHUNK_UNIVERSE = "universe"
    RESOURCE_PLAN2D = "plan2d"
    RESOURCE_LV = "lv"
    RESOURCE_BLOB = "blob"
    RESOURCE_VERSION = "version"
    RESOURCE_ARTIFACT = "artifact"
    RESOURCE_DATASET = "dataset"
    RESOURCE_LAYER = "layer"
    RESOURCE_URL = "url"

    KNOWN_SERVICES = {
        SERVICE_APP,
        SERVICE_CHAT,
        SERVICE_CHUNK,
        SERVICE_EDITOR3D,
        SERVICE_OPENLAYER,
        SERVICE_2D,
        SERVICE_LV,
        SERVICE_GEOSERVER,
        SERVICE_FILES,
        SERVICE_VERSIONING,
        SERVICE_EXTERNAL,
    }

    KNOWN_RESOURCE_TYPES = {
        RESOURCE_PROJECT,
        RESOURCE_CONVERSATION,
        RESOURCE_WORLD,
        RESOURCE_CHUNK_PROJECT,
        RESOURCE_CHUNK_UNIVERSE,
        RESOURCE_PLAN2D,
        RESOURCE_LV,
        RESOURCE_BLOB,
        RESOURCE_VERSION,
        RESOURCE_ARTIFACT,
        RESOURCE_DATASET,
        RESOURCE_LAYER,
        RESOURCE_URL,
    }

    CHUNK_RESOURCE_TYPES = {
        RESOURCE_CHUNK_PROJECT,
        RESOURCE_CHUNK_UNIVERSE,
        RESOURCE_WORLD,
    }

    LINK_STATUS_ACTIVE = "active"
    LINK_STATUS_PENDING = "pending"
    LINK_STATUS_DISABLED = "disabled"
    LINK_STATUS_ERROR = "error"
    LINK_STATUS_DELETED = "deleted"
    LINK_STATUSES = {
        LINK_STATUS_ACTIVE,
        LINK_STATUS_PENDING,
        LINK_STATUS_DISABLED,
        LINK_STATUS_ERROR,
        LINK_STATUS_DELETED,
    }

    def normalize_service(value: Any, default: str = SERVICE_EXTERNAL) -> str:
        try:
            text = _safe_str(value, default, 80).strip().lower().replace("-", "_")
            aliases = {
                "chunks": SERVICE_CHUNK,
                "chunk_service": SERVICE_CHUNK,
                "editor": SERVICE_EDITOR3D,
                "editor_3d": SERVICE_EDITOR3D,
                "map": SERVICE_OPENLAYER,
                "cad": SERVICE_2D,
                "plan2d": SERVICE_2D,
            }
            return aliases.get(text, text or default)
        except Exception:
            return default

    def normalize_resource_type(value: Any, default: str = RESOURCE_ARTIFACT) -> str:
        try:
            text = _safe_str(value, default, 80).strip().lower().replace("-", "_")
            aliases = {
                "chunk": RESOURCE_CHUNK_PROJECT,
                "chunks": RESOURCE_CHUNK_PROJECT,
                "chunk_project_id": RESOURCE_CHUNK_PROJECT,
                "chunk_universe": RESOURCE_CHUNK_UNIVERSE,
                "chunk_universe_id": RESOURCE_CHUNK_UNIVERSE,
                "universe": RESOURCE_CHUNK_UNIVERSE,
                "world_id": RESOURCE_WORLD,
                "chunk_world": RESOURCE_WORLD,
                "chunk_world_id": RESOURCE_WORLD,
            }
            return aliases.get(text, text or default)
        except Exception:
            return default

    def normalize_link_status(value: Any, default: str = LINK_STATUS_ACTIVE) -> str:
        try:
            text = _safe_str(value, default, 40).strip().lower().replace("-", "_")
            aliases = {
                "ok": LINK_STATUS_ACTIVE,
                "ready": LINK_STATUS_ACTIVE,
                "failed": LINK_STATUS_ERROR,
                "waiting": LINK_STATUS_PENDING,
                "removed": LINK_STATUS_DELETED,
            }
            result = aliases.get(text, text or default)
            return result if result in LINK_STATUSES else default
        except Exception:
            return default

    def normalize_capabilities(value: Any) -> Dict[str, bool]:
        try:
            return {str(k): bool(v) for k, v in _safe_dict(value).items()}
        except Exception:
            return {}

    def default_resource_type_for_service(service: Any) -> str:
        try:
            return {
                SERVICE_APP: RESOURCE_PROJECT,
                SERVICE_CHAT: RESOURCE_CONVERSATION,
                SERVICE_CHUNK: RESOURCE_CHUNK_PROJECT,
                SERVICE_EDITOR3D: RESOURCE_WORLD,
                SERVICE_OPENLAYER: RESOURCE_DATASET,
                SERVICE_2D: RESOURCE_PLAN2D,
                SERVICE_LV: RESOURCE_LV,
            }.get(normalize_service(service), RESOURCE_ARTIFACT)
        except Exception:
            return RESOURCE_ARTIFACT

    def normalize_project_setup_status(value: Any, default: str = PROJECT_SETUP_DRAFT) -> str:
        try:
            return _safe_str(value, default, 40).strip().lower() or default
        except Exception:
            return default

    def normalize_project_status(value: Any, default: str = PROJECT_STATUS_ACTIVE) -> str:
        try:
            return _safe_str(value, default, 40).strip().lower() or default
        except Exception:
            return default

    def normalize_chunk_status(value: Any, default: str = CHUNK_STATUS_PENDING, *, has_refs: bool = False) -> str:
        try:
            text = _safe_str(value, "", 40).strip().lower()
            if not text and has_refs:
                return CHUNK_STATUS_READY
            if text in {"ok", "active", "ready", "linked"}:
                return CHUNK_STATUS_READY
            if text in {"failed", "failure", "unavailable"}:
                return CHUNK_STATUS_ERROR
            if text in VALID_CHUNK_STATUSES:
                return text
            return default
        except Exception:
            return default

    def build_chunk_refs(**kwargs: Any) -> Dict[str, Any]:
        chunk_project_id = kwargs.get("chunk_project_id") or kwargs.get("chunkProjectId")
        chunk_universe_id = kwargs.get("chunk_universe_id") or kwargs.get("chunkUniverseId")
        chunk_world_id = kwargs.get("chunk_world_id") or kwargs.get("chunkWorldId")
        status = normalize_chunk_status(kwargs.get("status"), has_refs=bool(chunk_project_id and chunk_world_id))
        return {
            "status": status,
            "ready": bool(chunk_project_id and chunk_world_id and status == CHUNK_STATUS_READY),
            "chunk_project_id": chunk_project_id,
            "chunkProjectId": chunk_project_id,
            "chunk_universe_id": chunk_universe_id,
            "chunkUniverseId": chunk_universe_id,
            "chunk_world_id": chunk_world_id,
            "chunkWorldId": chunk_world_id,
            "route_hints": kwargs.get("route_hints") or kwargs.get("routeHints") or {},
            "routeHints": kwargs.get("route_hints") or kwargs.get("routeHints") or {},
            "error": kwargs.get("error") or {},
        }

    def build_chunk_reference(**kwargs: Any) -> Dict[str, Any]:
        return build_chunk_refs(**kwargs)

    def chunk_resource_id_for_type(
        *,
        resource_type: Any,
        chunk_project_id: Any = "",
        chunk_universe_id: Any = "",
        chunk_world_id: Any = "",
        fallback: Any = "",
    ) -> str:
        clean_type = normalize_resource_type(resource_type)
        if clean_type == RESOURCE_WORLD:
            return _safe_str(chunk_world_id or fallback, "", 255)
        if clean_type == RESOURCE_CHUNK_UNIVERSE:
            return _safe_str(chunk_universe_id or fallback, "", 255)
        if clean_type == RESOURCE_CHUNK_PROJECT:
            return _safe_str(chunk_project_id or fallback, "", 255)
        return _safe_str(fallback, "", 255)

    def build_service_link(*args: Any, **kwargs: Any) -> Any:
        link = ProjectServiceLink()
        for key, value in kwargs.items():
            try:
                if hasattr(link, key):
                    setattr(link, key, value)
            except Exception:
                pass
        return link

    def build_chunk_service_link(*args: Any, **kwargs: Any) -> Any:
        kwargs["service"] = SERVICE_CHUNK
        return build_service_link(*args, **kwargs)

    def get_service_link_by_id(*args: Any, **kwargs: Any) -> Any:
        return None

    def find_project_service_link(*args: Any, **kwargs: Any) -> Any:
        return None

    def find_chunk_service_link(*args: Any, **kwargs: Any) -> Any:
        return None

    def list_project_service_links(*args: Any, **kwargs: Any) -> List[Any]:
        return []

    def list_project_chunk_links(*args: Any, **kwargs: Any) -> List[Any]:
        return []

    def serialize_service_link(link: Any, *, include_private: bool = False) -> Dict[str, Any]:
        try:
            if hasattr(link, "to_dict"):
                return link.to_dict(include_private=include_private)
        except Exception:
            pass
        return {}

    def serialize_service_links(links: Any, *, include_private: bool = False) -> List[Dict[str, Any]]:
        return [serialize_service_link(link, include_private=include_private) for link in list(links or [])]

    def upsert_service_link(*args: Any, **kwargs: Any) -> Any:
        return build_service_link(**kwargs)

    def upsert_chunk_service_link(*args: Any, **kwargs: Any) -> Any:
        kwargs["service"] = SERVICE_CHUNK
        return build_service_link(**kwargs)

    def upsert_chunk_service_links(*args: Any, **kwargs: Any) -> List[Any]:
        return [upsert_chunk_service_link(*args, **kwargs)]

else:
    _MODEL_SOURCE = "modular"

    try:
        from .base import (
            JSONB,
            SoftDeleteMixin,
            TimestampMixin,
            SerializationMixin,
            db,
            deep_copy_json,
            json_type,
            legacy_backend_prefix,
            merge_dicts,
            normalize_project_role,
            normalize_role,
            normalize_status,
            normalize_visibility,
            project_public_id,
            public_id,
            role_permission_defaults,
            safe_bool,
            safe_dict,
            safe_float,
            safe_int,
            safe_list,
            safe_slug,
            safe_str,
            sanitize_viewer_selection,
            utcnow,
            version_public_id,
            _deep_merge_state,
            _is_legacy_viewer_key,
            _iso,
            _json_type,
            _legacy_backend_prefix,
            _normalize_project_role,
            _normalize_status,
            _normalize_visibility,
            _project_public_id,
            _public_id,
            _role,
            _role_permission_defaults,
            _safe_bool,
            _safe_dict,
            _safe_float,
            _safe_int,
            _safe_list,
            _safe_slug,
            _safe_str,
            _sanitize_viewer_selection,
            _utcnow,
            _uuid,
            _version_public_id,
        )

        from .users import (
            AppUser,
            DEFAULT_USER_ID,
            current_user_id_placeholder as _users_current_user_id_placeholder,
            ensure_default_user as _users_ensure_default_user,
            get_default_user_id,
            get_user_model_status,
            serialize_user,
        )

        from .legacy import (
            Blob,
            Client,
            Conversation,
            ConversationState,
            IdempotencyKey,
            Job,
            MessageTemplate,
            append_conversation_message,
            create_conversation,
            get_conversation,
            get_legacy_model_classes,
            get_legacy_model_status,
            get_or_create_conversation_state,
            serialize_conversation,
        )

        from .projects import (
            CHUNK_STATUS_DISABLED,
            CHUNK_STATUS_ERROR,
            CHUNK_STATUS_PENDING,
            CHUNK_STATUS_READY,
            PROJECT_SETUP_CONFIGURED,
            PROJECT_SETUP_DEFINED,
            PROJECT_SETUP_DRAFT,
            PROJECT_STATUS_ACTIVE,
            PROJECT_STATUS_ARCHIVED,
            PROJECT_STATUS_DELETED,
            PROJECT_VISIBILITY_PRIVATE,
            PROJECT_VISIBILITY_PUBLIC,
            PROJECT_VISIBILITY_SHARED,
            VALID_CHUNK_STATUSES,
            Project,
            build_chunk_refs,
            build_project,
            build_project_paths,
            get_project_by_conversation_id,
            get_project_by_id,
            get_project_by_public_id,
            get_project_model_classes,
            get_project_model_status,
            is_configured_status,
            normalize_chunk_status,
            normalize_project_setup_status,
            normalize_project_status,
            resolve_project,
            serialize_project,
            serialize_project_sidebar_item,
        )

        from .project_access import (
            ProjectAccess,
            ProjectMembership,
            ProjectPermission,
            build_membership,
            ensure_owner_membership,
            get_project_access_model_classes,
            get_project_access_model_status,
            get_project_membership,
            list_project_memberships,
            normalize_permission,
            permission_field,
            permissions_from_membership,
            serialize_membership,
            serialize_memberships,
        )

        from .project_embed import (
            ProjectEmbedPolicy,
            build_embed_policy,
            get_embed_policy_by_project_id,
            get_or_create_embed_policy,
            get_project_embed_model_classes,
            get_project_embed_model_status,
            normalize_embed_mode,
            serialize_embed_policy,
            update_embed_policy,
        )

        from .project_links import (
            CHUNK_RESOURCE_TYPES,
            KNOWN_RESOURCE_TYPES,
            KNOWN_SERVICES,
            LINK_STATUS_ACTIVE,
            LINK_STATUS_DELETED,
            LINK_STATUS_DISABLED,
            LINK_STATUS_ERROR,
            LINK_STATUS_PENDING,
            LINK_STATUSES,
            RESOURCE_ARTIFACT,
            RESOURCE_BLOB,
            RESOURCE_CHUNK_PROJECT,
            RESOURCE_CHUNK_UNIVERSE,
            RESOURCE_CONVERSATION,
            RESOURCE_DATASET,
            RESOURCE_LAYER,
            RESOURCE_LV,
            RESOURCE_PLAN2D,
            RESOURCE_PROJECT,
            RESOURCE_URL,
            RESOURCE_VERSION,
            RESOURCE_WORLD,
            SERVICE_2D,
            SERVICE_APP,
            SERVICE_CHAT,
            SERVICE_CHUNK,
            SERVICE_EDITOR3D,
            SERVICE_EXTERNAL,
            SERVICE_FILES,
            SERVICE_GEOSERVER,
            SERVICE_LV,
            SERVICE_OPENLAYER,
            SERVICE_VERSIONING,
            ProjectServiceLink,
            build_chunk_reference,
            build_chunk_service_link,
            build_service_link,
            chunk_resource_id_for_type,
            default_resource_type_for_service,
            find_chunk_service_link,
            find_project_service_link,
            get_project_links_model_classes,
            get_project_links_model_status,
            get_service_link_by_id,
            list_project_chunk_links,
            list_project_service_links,
            normalize_capabilities,
            normalize_link_status,
            normalize_resource_type,
            normalize_service,
            serialize_service_link,
            serialize_service_links,
            upsert_chunk_service_link,
            upsert_chunk_service_links,
            upsert_service_link,
        )

        from .project_versions import (
            ProjectVersion,
            ProjectVersionLink,
            build_project_version,
            create_project_version,
            get_latest_project_version,
            get_project_version_by_id,
            get_project_version_by_public_id,
            get_project_versions_model_classes,
            get_project_versions_model_status,
            list_project_versions,
            next_project_version_no,
            normalize_service_name,
            normalize_version_kind,
            normalize_version_status,
            serialize_project_version,
            serialize_project_versions,
        )

        from .project_audit import (
            ProjectAuditEvent,
            build_audit_event,
            build_request_context_from_flask,
            get_audit_event_by_id,
            get_project_audit_model_classes,
            get_project_audit_model_status,
            list_project_audit_events,
            normalize_actor_type,
            normalize_audit_action,
            normalize_audit_category,
            normalize_audit_severity,
            normalize_request_context,
            record_audit_event,
            record_project_audit_event,
            serialize_audit_event,
            serialize_audit_events,
        )

        CORE_MODEL_CLASSES = (
            AppUser,
            Client,
            IdempotencyKey,
            Job,
            Blob,
            Conversation,
            MessageTemplate,
            ConversationState,
            Project,
            ProjectMembership,
            ProjectEmbedPolicy,
            ProjectServiceLink,
            ProjectVersion,
            ProjectAuditEvent,
        )

    except Exception as exc:
        _store_import_error("modular", exc)
        raise RuntimeError(
            "Failed to import modular VECTOPLAN model package. "
            "Check models/base.py, users.py, legacy.py, projects.py, "
            "project_access.py, project_embed.py, project_links.py, "
            "project_versions.py and project_audit.py."
        ) from exc


# ─────────────────────────────────────────────────────────────
# Optional future model modules
# ─────────────────────────────────────────────────────────────

def _try_import_optional_models() -> None:
    """
    Import optional future model modules best-effort.

    Missing optional modules are ignored. Existing optional modules that crash
    during import are reported through get_model_import_status().
    """
    package_name = __name__

    for module_name in _OPTIONAL_MODEL_MODULES:
        try:
            if not _module_spec_exists(module_name):
                continue

            __import__(f"{package_name}.{module_name}", fromlist=["*"])

        except Exception as exc:
            _store_import_error(module_name, exc)


_try_import_optional_models()


# ─────────────────────────────────────────────────────────────
# Backward-compatible aliases
# ─────────────────────────────────────────────────────────────

User = AppUser
ProjectUser = AppUser

try:
    ProjectAccess
except NameError:
    ProjectAccess = ProjectMembership

try:
    ProjectPermission
except NameError:
    ProjectPermission = ProjectMembership

try:
    ProjectVersionLink
except NameError:
    ProjectVersionLink = ProjectVersion


# ─────────────────────────────────────────────────────────────
# Model registry helpers
# ─────────────────────────────────────────────────────────────

def get_core_model_classes() -> Tuple[Any, ...]:
    """
    Return all model classes that must be registered for the current app.

    The name is kept for backward compatibility. The returned classes now come
    from modular model files in normal operation.
    """
    try:
        return tuple(CORE_MODEL_CLASSES)
    except Exception:
        return (
            AppUser,
            Client,
            IdempotencyKey,
            Job,
            Blob,
            Conversation,
            MessageTemplate,
            ConversationState,
            Project,
            ProjectMembership,
            ProjectEmbedPolicy,
            ProjectServiceLink,
            ProjectVersion,
            ProjectAuditEvent,
        )


def register_all_models() -> Tuple[Any, ...]:
    """
    Force-import all currently known models.

    SQLAlchemy registers declarative models at class definition/import time.
    Returning the classes is enough to ensure the import path was executed.
    """
    return get_core_model_classes()


def get_model_class_map() -> Dict[str, Any]:
    """
    Return a stable name -> class mapping for diagnostics.
    """
    result: Dict[str, Any] = {}

    try:
        for model_cls in get_core_model_classes():
            try:
                result[str(model_cls.__name__)] = model_cls
            except Exception:
                continue
    except Exception:
        pass

    return result


def get_model_table_names() -> List[str]:
    """
    Return known SQLAlchemy table names.
    """
    names: List[str] = []

    try:
        for model_cls in get_core_model_classes():
            try:
                table_name = str(getattr(model_cls, "__tablename__", "") or "").strip()
                if table_name and table_name not in names:
                    names.append(table_name)
            except Exception:
                continue
    except Exception:
        pass

    return names


def get_model_class_names() -> List[str]:
    """
    Return known model class names.
    """
    names: List[str] = []

    try:
        for model_cls in get_core_model_classes():
            try:
                name = str(getattr(model_cls, "__name__", "") or "").strip()
                if name and name not in names:
                    names.append(name)
            except Exception:
                continue
    except Exception:
        pass

    return names


def _model_columns(model_cls: Any) -> List[str]:
    try:
        table = getattr(model_cls, "__table__", None)
        columns = getattr(table, "columns", None)
        if columns is None:
            return []
        return [str(column.name) for column in columns]
    except Exception:
        return []


def get_model_column_map() -> Dict[str, List[str]]:
    """Return model class name -> column names for diagnostics."""
    result: Dict[str, List[str]] = {}

    try:
        for name, model_cls in get_model_class_map().items():
            result[name] = _model_columns(model_cls)
    except Exception:
        pass

    return result


def is_app_chunk_model_shape_ready() -> bool:
    """Return whether app models expose required Chunk integration shape."""
    try:
        column_map = get_model_column_map()

        project_columns = set(column_map.get("Project", []))
        project_required = {
            "chunk_project_id",
            "chunk_universe_id",
            "chunk_world_id",
            "chunk_status",
            "chunk_ready",
            "chunk_route_hints",
            "chunk_last_error",
            "chunk_provisioned_at",
            "service_refs",
        }

        link_columns = set(column_map.get("ProjectServiceLink", []))
        link_required = {
            "project_id",
            "service",
            "resource_type",
            "resource_id",
            "reference",
            "service_payload",
            "metadata",
        }

        return project_required.issubset(project_columns) and link_required.issubset(link_columns)

    except Exception:
        return False


def get_model_import_status() -> Dict[str, Any]:
    """
    Return import diagnostics for this package.
    """
    return {
        "ok": not bool(_MODEL_IMPORT_ERRORS),
        "source": _MODEL_SOURCE,
        "core_fallback": _MODEL_SOURCE == "core_fallback",
        "modular_loaded": _MODEL_SOURCE == "modular",
        "required_modules": list(_REQUIRED_MODEL_MODULES),
        "optional_modules": list(_OPTIONAL_MODEL_MODULES),
        "errors": dict(_MODEL_IMPORT_ERRORS),
        "model_count": len(get_core_model_classes()),
        "models": get_model_class_names(),
        "tables": get_model_table_names(),
        "columnMap": get_model_column_map(),
        "appChunkModelShapeReady": is_app_chunk_model_shape_ready(),
    }


def get_model_status() -> Dict[str, Any]:
    """
    Aggregate lightweight model diagnostics.
    """
    status: Dict[str, Any] = {
        "ok": True,
        "import": get_model_import_status(),
        "tables": get_model_table_names(),
        "appChunkModelShapeReady": is_app_chunk_model_shape_ready(),
    }

    try:
        if _MODEL_SOURCE == "modular":
            status["users"] = get_user_model_status()
            status["legacy"] = get_legacy_model_status()
            status["projects"] = get_project_model_status()
            status["project_access"] = get_project_access_model_status()
            status["project_embed"] = get_project_embed_model_status()
            status["project_links"] = get_project_links_model_status()
            status["project_versions"] = get_project_versions_model_status()
            status["project_audit"] = get_project_audit_model_status()
        else:
            status["core_fallback"] = True

    except Exception as exc:
        status["ok"] = False
        status["error"] = str(exc)

    return status


# ─────────────────────────────────────────────────────────────
# User compatibility helpers
# ─────────────────────────────────────────────────────────────

def ensure_default_user(*args: Any, **kwargs: Any) -> Any:
    """
    Ensure placeholder user id=1 exists.

    In modular mode this delegates to models/users.py.
    In core fallback mode this delegates to AppUser.ensure_default_user().
    """
    try:
        if _MODEL_SOURCE == "modular":
            return _users_ensure_default_user(*args, **kwargs)

        if hasattr(AppUser, "ensure_default_user"):
            return AppUser.ensure_default_user(*args, **kwargs)

        user = AppUser()
        try:
            user.id = 1
            user.public_id = "u_demo_1"
            user.display_name = "Demo User"
            user.is_placeholder = True
        except Exception:
            pass
        return user

    except Exception:
        try:
            if hasattr(AppUser, "ensure_default_user"):
                return AppUser.ensure_default_user()
        except Exception:
            pass

        return None


def current_user_id_placeholder() -> int:
    """
    Temporary user resolver for the first project-management phase.
    """
    try:
        if _MODEL_SOURCE == "modular":
            return int(_users_current_user_id_placeholder())

        return 1

    except Exception:
        return 1


# ─────────────────────────────────────────────────────────────
# Public exports
# ─────────────────────────────────────────────────────────────

__all__ = [
    # database/base helpers
    "db",
    "JSONB",
    "TimestampMixin",
    "SoftDeleteMixin",
    "SerializationMixin",
    # old/core-compatible models
    "Client",
    "IdempotencyKey",
    "Job",
    "Blob",
    "Conversation",
    "MessageTemplate",
    "ConversationState",
    "Project",
    "ProjectVersion",
    # new project-management models
    "AppUser",
    "ProjectMembership",
    "ProjectEmbedPolicy",
    "ProjectServiceLink",
    "ProjectAuditEvent",
    # aliases
    "User",
    "ProjectUser",
    "ProjectAccess",
    "ProjectPermission",
    "ProjectVersionLink",
    # app project constants
    "PROJECT_SETUP_DRAFT",
    "PROJECT_SETUP_DEFINED",
    "PROJECT_SETUP_CONFIGURED",
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_ARCHIVED",
    "PROJECT_STATUS_DELETED",
    "PROJECT_VISIBILITY_PRIVATE",
    "PROJECT_VISIBILITY_SHARED",
    "PROJECT_VISIBILITY_PUBLIC",
    # chunk project constants
    "CHUNK_STATUS_DISABLED",
    "CHUNK_STATUS_PENDING",
    "CHUNK_STATUS_READY",
    "CHUNK_STATUS_ERROR",
    "VALID_CHUNK_STATUSES",
    # service link constants
    "SERVICE_APP",
    "SERVICE_CHAT",
    "SERVICE_CHUNK",
    "SERVICE_EDITOR3D",
    "SERVICE_OPENLAYER",
    "SERVICE_2D",
    "SERVICE_LV",
    "SERVICE_GEOSERVER",
    "SERVICE_FILES",
    "SERVICE_VERSIONING",
    "SERVICE_EXTERNAL",
    "KNOWN_SERVICES",
    "RESOURCE_PROJECT",
    "RESOURCE_CONVERSATION",
    "RESOURCE_WORLD",
    "RESOURCE_CHUNK_PROJECT",
    "RESOURCE_CHUNK_UNIVERSE",
    "RESOURCE_PLAN2D",
    "RESOURCE_LV",
    "RESOURCE_BLOB",
    "RESOURCE_VERSION",
    "RESOURCE_ARTIFACT",
    "RESOURCE_DATASET",
    "RESOURCE_LAYER",
    "RESOURCE_URL",
    "KNOWN_RESOURCE_TYPES",
    "CHUNK_RESOURCE_TYPES",
    "LINK_STATUS_ACTIVE",
    "LINK_STATUS_PENDING",
    "LINK_STATUS_DISABLED",
    "LINK_STATUS_ERROR",
    "LINK_STATUS_DELETED",
    "LINK_STATUSES",
    # registry/status
    "CORE_MODEL_CLASSES",
    "register_all_models",
    "get_core_model_classes",
    "get_model_class_map",
    "get_model_class_names",
    "get_model_table_names",
    "get_model_column_map",
    "get_model_import_status",
    "get_model_status",
    "is_app_chunk_model_shape_ready",
    "ensure_default_user",
    "current_user_id_placeholder",
    # user helpers
    "serialize_user",
    # legacy helpers
    "get_conversation",
    "create_conversation",
    "serialize_conversation",
    "append_conversation_message",
    "get_or_create_conversation_state",
    # project helpers
    "normalize_project_setup_status",
    "normalize_project_status",
    "normalize_chunk_status",
    "build_chunk_refs",
    "build_project",
    "build_project_paths",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_by_conversation_id",
    "resolve_project",
    "serialize_project",
    "serialize_project_sidebar_item",
    # project access helpers
    "normalize_permission",
    "permission_field",
    "permissions_from_membership",
    "build_membership",
    "get_project_membership",
    "list_project_memberships",
    "serialize_membership",
    "serialize_memberships",
    "ensure_owner_membership",
    # embed helpers
    "normalize_embed_mode",
    "build_embed_policy",
    "get_embed_policy_by_project_id",
    "get_or_create_embed_policy",
    "serialize_embed_policy",
    "update_embed_policy",
    # service-link helpers
    "normalize_service",
    "normalize_resource_type",
    "normalize_link_status",
    "normalize_capabilities",
    "default_resource_type_for_service",
    "build_chunk_reference",
    "chunk_resource_id_for_type",
    "build_service_link",
    "build_chunk_service_link",
    "get_service_link_by_id",
    "find_project_service_link",
    "find_chunk_service_link",
    "list_project_service_links",
    "list_project_chunk_links",
    "serialize_service_link",
    "serialize_service_links",
    "upsert_service_link",
    "upsert_chunk_service_link",
    "upsert_chunk_service_links",
    # version helpers
    "normalize_version_kind",
    "normalize_version_status",
    "normalize_service_name",
    "build_project_version",
    "create_project_version",
    "get_project_version_by_id",
    "get_project_version_by_public_id",
    "next_project_version_no",
    "list_project_versions",
    "get_latest_project_version",
    "serialize_project_version",
    "serialize_project_versions",
    # audit helpers
    "normalize_audit_category",
    "normalize_audit_action",
    "normalize_audit_severity",
    "normalize_actor_type",
    "normalize_request_context",
    "build_request_context_from_flask",
    "build_audit_event",
    "record_audit_event",
    "record_project_audit_event",
    "get_audit_event_by_id",
    "list_project_audit_events",
    "serialize_audit_event",
    "serialize_audit_events",
    # low-level helpers used by existing routes/services
    "json_type",
    "utcnow",
    "public_id",
    "project_public_id",
    "version_public_id",
    "safe_str",
    "safe_slug",
    "safe_int",
    "safe_float",
    "safe_bool",
    "safe_dict",
    "safe_list",
    "merge_dicts",
    "deep_copy_json",
    "normalize_status",
    "normalize_visibility",
    "normalize_project_role",
    "normalize_role",
    "role_permission_defaults",
    "sanitize_viewer_selection",
    "legacy_backend_prefix",
    # old underscored compatibility helpers
    "_json_type",
    "_utcnow",
    "_uuid",
    "_public_id",
    "_project_public_id",
    "_version_public_id",
    "_safe_str",
    "_safe_slug",
    "_safe_int",
    "_safe_float",
    "_safe_bool",
    "_safe_dict",
    "_safe_list",
    "_iso",
    "_role",
    "_normalize_status",
    "_normalize_visibility",
    "_normalize_project_role",
    "_legacy_backend_prefix",
    "_is_legacy_viewer_key",
    "_sanitize_viewer_selection",
    "_deep_merge_state",
    "_role_permission_defaults",
]