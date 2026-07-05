# services/vectoplan-app/models/__init__.py
from __future__ import annotations

"""
VECTOPLAN app model package.

Zweck:
- Ersetzt die alte flache Datei services/vectoplan-app/models.py.
- Hält bestehende Imports kompatibel:
    from models import Blob, Conversation
    from models import Project, ProjectVersion
    from models import ProjectInvitation
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
    project_invitations.py
- Verhindert harte Importabbrüche durch robuste Best-Effort-Imports und
  Diagnosefunktionen.
- Importiert keine Default-User-Konstanten.
- Erzeugt keinen Default-User.
- Kein Fallback auf AppUser id=1.

Wichtige Regel:
- Alle produktiven Models sollen hier importiert werden, bevor db.create_all()
  oder Migrationen laufen.
- vectoplan-app speichert Projekt-, Rollen-, Sichtbarkeits-, Einladungs-,
  Service-Link-, Version- und Auditdaten.
- vectoplan-app erzeugt keine echten Benutzeraccounts.
- vectoplan-auth ist die Wahrheit für Login, Registrierung, Account, Plan,
  Entitlements, Blocked/Banned und User-Identität.
- vectoplan-app speichert nur lokale AppUser-Links, keine Auth-Wahrheit.
- vectoplan-app speichert nur Service-Referenzen zu vectoplan-chunk.
- vectoplan-app speichert keine Chunk-Zellen, keine Chunk-Snapshots und keine
  Chunk-Events.
"""

import importlib
import importlib.util
import threading
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# ─────────────────────────────────────────────────────────────
# Module registry
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
    "project_invitations",
)

_OPTIONAL_MODEL_MODULES: Tuple[str, ...] = (
    "project_comments",
    "project_tasks",
    "project_files",
    "project_notifications",
)

_MODEL_IMPORT_LOCK = threading.RLock()
_MODEL_IMPORT_CACHE: Dict[str, Optional[ModuleType]] = {}
_MODEL_IMPORT_ERRORS: Dict[str, Dict[str, str]] = {}
_MODEL_SOURCE = "modular_best_effort"


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
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
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = _safe_str(value, "", 80).lower()
        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable"}:
            return True
        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable"}:
            return False
        return default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return dict(value.to_dict())
        return {}
    except Exception:
        return {}


def _safe_list(value: Any) -> List[Any]:
    try:
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        return []
    except Exception:
        return []


def _missing_callable(*args: Any, **kwargs: Any) -> Any:
    return None


def _empty_dict_callable(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return {}


def _empty_list_callable(*args: Any, **kwargs: Any) -> List[Any]:
    return []


def _identity_callable(value: Any = None, *args: Any, **kwargs: Any) -> Any:
    return value


def _safe_name(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _store_import_error(module_name: str, exc: BaseException) -> None:
    try:
        _MODEL_IMPORT_ERRORS[str(module_name)] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
    except Exception:
        pass


def _clear_import_error(module_name: str) -> None:
    try:
        _MODEL_IMPORT_ERRORS.pop(str(module_name), None)
    except Exception:
        pass


def _module_spec_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(f"{__name__}.{module_name}") is not None
    except Exception:
        return False


def _import_model_module(module_name: str, *, required: bool = True) -> Optional[ModuleType]:
    safe_module_name = _safe_str(module_name, "", 120)
    if not safe_module_name:
        return None

    with _MODEL_IMPORT_LOCK:
        if safe_module_name in _MODEL_IMPORT_CACHE:
            return _MODEL_IMPORT_CACHE.get(safe_module_name)

        if not _module_spec_exists(safe_module_name):
            if required:
                _MODEL_IMPORT_ERRORS[safe_module_name] = {
                    "type": "ModuleNotFound",
                    "message": f"models.{safe_module_name} not found",
                }
            _MODEL_IMPORT_CACHE[safe_module_name] = None
            return None

        try:
            module = importlib.import_module(f".{safe_module_name}", package=__name__)
            _MODEL_IMPORT_CACHE[safe_module_name] = module
            _clear_import_error(safe_module_name)
            return module
        except Exception as exc:
            _store_import_error(safe_module_name, exc)
            _MODEL_IMPORT_CACHE[safe_module_name] = None
            return None


def _get_attr(module: Optional[ModuleType], name: str, default: Any = None) -> Any:
    try:
        if module is None:
            return default
        return getattr(module, name, default)
    except Exception:
        return default


def _export_attrs(module: Optional[ModuleType], names: Sequence[str], defaults: Optional[Mapping[str, Any]] = None) -> None:
    fallback = _safe_dict(defaults)

    for name in names:
        try:
            globals()[name] = _get_attr(module, name, fallback.get(name))
        except Exception:
            globals()[name] = fallback.get(name)


def _compact_model_classes(values: Iterable[Any]) -> Tuple[Any, ...]:
    result: List[Any] = []

    try:
        for value in values:
            if value is None:
                continue

            try:
                if not hasattr(value, "__tablename__"):
                    continue
            except Exception:
                continue

            if value not in result:
                result.append(value)
    except Exception:
        pass

    return tuple(result)


# ─────────────────────────────────────────────────────────────
# Base bootstrap
# ─────────────────────────────────────────────────────────────

_base_module = _import_model_module("base", required=True)

try:
    from extensions import db as _extensions_db  # type: ignore
except Exception:
    _extensions_db = None  # type: ignore

db = _get_attr(_base_module, "db", _extensions_db)
JSONB = _get_attr(_base_module, "JSONB", getattr(db, "JSON", None) if db is not None else None)
TimestampMixin = _get_attr(_base_module, "TimestampMixin", object)
SoftDeleteMixin = _get_attr(_base_module, "SoftDeleteMixin", object)
SerializationMixin = _get_attr(_base_module, "SerializationMixin", object)

# Public base helpers.
json_type = _get_attr(_base_module, "json_type", lambda: getattr(db, "JSON", None) if db is not None else None)
utcnow = _get_attr(_base_module, "utcnow", _missing_callable)
public_id = _get_attr(_base_module, "public_id", _missing_callable)
project_public_id = _get_attr(_base_module, "project_public_id", _missing_callable)
version_public_id = _get_attr(_base_module, "version_public_id", _missing_callable)
safe_str = _get_attr(_base_module, "safe_str", _safe_str)
safe_slug = _get_attr(_base_module, "safe_slug", _safe_str)
safe_int = _get_attr(_base_module, "safe_int", _safe_int)
safe_float = _get_attr(_base_module, "safe_float", lambda value, default=0.0: default)
safe_bool = _get_attr(_base_module, "safe_bool", _safe_bool)
safe_dict = _get_attr(_base_module, "safe_dict", _safe_dict)
safe_list = _get_attr(_base_module, "safe_list", _safe_list)
merge_dicts = _get_attr(_base_module, "merge_dicts", lambda *values, **kwargs: {})
deep_copy_json = _get_attr(_base_module, "deep_copy_json", lambda value=None: _safe_dict(value))
normalize_status = _get_attr(_base_module, "normalize_status", lambda value, default="active": _safe_str(value, default, 80))
normalize_visibility = _get_attr(_base_module, "normalize_visibility", lambda value, default="private": _safe_str(value, default, 80))
normalize_project_role = _get_attr(_base_module, "normalize_project_role", lambda value, default="viewer": _safe_str(value, default, 80))
normalize_role = _get_attr(_base_module, "normalize_role", normalize_project_role)
role_permission_defaults = _get_attr(_base_module, "role_permission_defaults", lambda role=None: {})
sanitize_viewer_selection = _get_attr(_base_module, "sanitize_viewer_selection", lambda value=None: _safe_dict(value))
legacy_backend_prefix = _get_attr(_base_module, "legacy_backend_prefix", lambda: "")

# Old underscored compatibility helpers.
_json_type = _get_attr(_base_module, "_json_type", json_type)
_utcnow = _get_attr(_base_module, "_utcnow", utcnow)
_uuid = _get_attr(_base_module, "_uuid", _missing_callable)
_public_id = _get_attr(_base_module, "_public_id", public_id)
_project_public_id = _get_attr(_base_module, "_project_public_id", project_public_id)
_version_public_id = _get_attr(_base_module, "_version_public_id", version_public_id)
_safe_str = _get_attr(_base_module, "_safe_str", safe_str)
_safe_slug = _get_attr(_base_module, "_safe_slug", safe_slug)
_safe_int = _get_attr(_base_module, "_safe_int", safe_int)
_safe_float = _get_attr(_base_module, "_safe_float", safe_float)
_safe_bool = _get_attr(_base_module, "_safe_bool", safe_bool)
_safe_dict = _get_attr(_base_module, "_safe_dict", safe_dict)
_safe_list = _get_attr(_base_module, "_safe_list", safe_list)
_iso = _get_attr(_base_module, "_iso", lambda value=None: str(value) if value is not None else None)
_role = _get_attr(_base_module, "_role", normalize_role)
_normalize_status = _get_attr(_base_module, "_normalize_status", normalize_status)
_normalize_visibility = _get_attr(_base_module, "_normalize_visibility", normalize_visibility)
_normalize_project_role = _get_attr(_base_module, "_normalize_project_role", normalize_project_role)
_legacy_backend_prefix = _get_attr(_base_module, "_legacy_backend_prefix", legacy_backend_prefix)
_is_legacy_viewer_key = _get_attr(_base_module, "_is_legacy_viewer_key", lambda value=None: False)
_sanitize_viewer_selection = _get_attr(_base_module, "_sanitize_viewer_selection", sanitize_viewer_selection)
_deep_merge_state = _get_attr(_base_module, "_deep_merge_state", merge_dicts)
_role_permission_defaults = _get_attr(_base_module, "_role_permission_defaults", role_permission_defaults)


# ─────────────────────────────────────────────────────────────
# Modular imports
# ─────────────────────────────────────────────────────────────

_users_module = _import_model_module("users", required=True)
_legacy_module = _import_model_module("legacy", required=True)
_projects_module = _import_model_module("projects", required=True)
_project_access_module = _import_model_module("project_access", required=True)
_project_embed_module = _import_model_module("project_embed", required=True)
_project_links_module = _import_model_module("project_links", required=True)
_project_versions_module = _import_model_module("project_versions", required=True)
_project_audit_module = _import_model_module("project_audit", required=True)
_project_invitations_module = _import_model_module("project_invitations", required=True)


# Users.
AppUser = _get_attr(_users_module, "AppUser")
serialize_user = _get_attr(_users_module, "serialize_user", _empty_dict_callable)
get_user_model_status = _get_attr(_users_module, "get_user_model_status", lambda: {"ok": AppUser is not None, "default_user_removed": True})

# Intentionally not importing/exporting:
# - DEFAULT_USER_ID
# - get_default_user_id
# - current_user_id_placeholder from users.py
# - ensure_default_user from users.py


# Legacy models/helpers.
Client = _get_attr(_legacy_module, "Client")
IdempotencyKey = _get_attr(_legacy_module, "IdempotencyKey")
Job = _get_attr(_legacy_module, "Job")
Blob = _get_attr(_legacy_module, "Blob")
Conversation = _get_attr(_legacy_module, "Conversation")
MessageTemplate = _get_attr(_legacy_module, "MessageTemplate")
ConversationState = _get_attr(_legacy_module, "ConversationState")

append_conversation_message = _get_attr(_legacy_module, "append_conversation_message", _missing_callable)
create_conversation = _get_attr(_legacy_module, "create_conversation", _missing_callable)
get_conversation = _get_attr(_legacy_module, "get_conversation", _missing_callable)
get_legacy_model_classes = _get_attr(_legacy_module, "get_legacy_model_classes", _empty_list_callable)
get_legacy_model_status = _get_attr(_legacy_module, "get_legacy_model_status", lambda: {"ok": _legacy_module is not None})
get_or_create_conversation_state = _get_attr(_legacy_module, "get_or_create_conversation_state", _missing_callable)
serialize_conversation = _get_attr(_legacy_module, "serialize_conversation", _empty_dict_callable)


# Projects.
PROJECT_SETUP_DRAFT = _get_attr(_projects_module, "PROJECT_SETUP_DRAFT", "draft")
PROJECT_SETUP_DEFINED = _get_attr(_projects_module, "PROJECT_SETUP_DEFINED", "defined")
PROJECT_SETUP_CONFIGURED = _get_attr(_projects_module, "PROJECT_SETUP_CONFIGURED", "configured")
PROJECT_STATUS_ACTIVE = _get_attr(_projects_module, "PROJECT_STATUS_ACTIVE", "active")
PROJECT_STATUS_ARCHIVED = _get_attr(_projects_module, "PROJECT_STATUS_ARCHIVED", "archived")
PROJECT_STATUS_DELETED = _get_attr(_projects_module, "PROJECT_STATUS_DELETED", "deleted")
PROJECT_VISIBILITY_PRIVATE = _get_attr(_projects_module, "PROJECT_VISIBILITY_PRIVATE", "private")
PROJECT_VISIBILITY_SHARED = _get_attr(_projects_module, "PROJECT_VISIBILITY_SHARED", "shared")
PROJECT_VISIBILITY_PUBLIC = _get_attr(_projects_module, "PROJECT_VISIBILITY_PUBLIC", "public")
CHUNK_STATUS_DISABLED = _get_attr(_projects_module, "CHUNK_STATUS_DISABLED", "disabled")
CHUNK_STATUS_PENDING = _get_attr(_projects_module, "CHUNK_STATUS_PENDING", "pending")
CHUNK_STATUS_READY = _get_attr(_projects_module, "CHUNK_STATUS_READY", "ready")
CHUNK_STATUS_ERROR = _get_attr(_projects_module, "CHUNK_STATUS_ERROR", "error")
VALID_CHUNK_STATUSES = _get_attr(
    _projects_module,
    "VALID_CHUNK_STATUSES",
    {CHUNK_STATUS_DISABLED, CHUNK_STATUS_PENDING, CHUNK_STATUS_READY, CHUNK_STATUS_ERROR},
)

Project = _get_attr(_projects_module, "Project")
build_chunk_refs = _get_attr(_projects_module, "build_chunk_refs", _empty_dict_callable)
build_project = _get_attr(_projects_module, "build_project", _missing_callable)
build_project_paths = _get_attr(_projects_module, "build_project_paths", _empty_dict_callable)
get_project_by_conversation_id = _get_attr(_projects_module, "get_project_by_conversation_id", _missing_callable)
get_project_by_id = _get_attr(_projects_module, "get_project_by_id", _missing_callable)
get_project_by_public_id = _get_attr(_projects_module, "get_project_by_public_id", _missing_callable)
get_project_model_classes = _get_attr(_projects_module, "get_project_model_classes", _empty_list_callable)
get_project_model_status = _get_attr(_projects_module, "get_project_model_status", lambda: {"ok": Project is not None})
is_configured_status = _get_attr(_projects_module, "is_configured_status", lambda value=None: False)
normalize_chunk_status = _get_attr(_projects_module, "normalize_chunk_status", lambda value=None, default=CHUNK_STATUS_PENDING, **kwargs: default)
normalize_project_setup_status = _get_attr(_projects_module, "normalize_project_setup_status", lambda value=None, default=PROJECT_SETUP_DRAFT: default)
normalize_project_status = _get_attr(_projects_module, "normalize_project_status", lambda value=None, default=PROJECT_STATUS_ACTIVE: default)
resolve_project = _get_attr(_projects_module, "resolve_project", _missing_callable)
serialize_project = _get_attr(_projects_module, "serialize_project", _empty_dict_callable)
serialize_project_sidebar_item = _get_attr(_projects_module, "serialize_project_sidebar_item", _empty_dict_callable)


# Project access.
ProjectAccess = _get_attr(_project_access_module, "ProjectAccess")
ProjectMembership = _get_attr(_project_access_module, "ProjectMembership")
ProjectPermission = _get_attr(_project_access_module, "ProjectPermission")

build_membership = _get_attr(_project_access_module, "build_membership", _missing_callable)
ensure_owner_membership = _get_attr(_project_access_module, "ensure_owner_membership", _missing_callable)
get_project_access_model_classes = _get_attr(_project_access_module, "get_project_access_model_classes", _empty_list_callable)
get_project_access_model_status = _get_attr(_project_access_module, "get_project_access_model_status", lambda: {"ok": ProjectMembership is not None})
get_project_membership = _get_attr(_project_access_module, "get_project_membership", _missing_callable)
list_project_memberships = _get_attr(_project_access_module, "list_project_memberships", _empty_list_callable)
normalize_permission = _get_attr(_project_access_module, "normalize_permission", lambda value=None, default="view": default)
permission_field = _get_attr(_project_access_module, "permission_field", lambda value=None: "")
permissions_from_membership = _get_attr(_project_access_module, "permissions_from_membership", _empty_dict_callable)
serialize_membership = _get_attr(_project_access_module, "serialize_membership", _empty_dict_callable)
serialize_memberships = _get_attr(_project_access_module, "serialize_memberships", _empty_list_callable)


# Project embed.
ProjectEmbedPolicy = _get_attr(_project_embed_module, "ProjectEmbedPolicy")

build_embed_policy = _get_attr(_project_embed_module, "build_embed_policy", _missing_callable)
get_embed_policy_by_project_id = _get_attr(_project_embed_module, "get_embed_policy_by_project_id", _missing_callable)
get_or_create_embed_policy = _get_attr(_project_embed_module, "get_or_create_embed_policy", _missing_callable)
get_project_embed_model_classes = _get_attr(_project_embed_module, "get_project_embed_model_classes", _empty_list_callable)
get_project_embed_model_status = _get_attr(_project_embed_module, "get_project_embed_model_status", lambda: {"ok": ProjectEmbedPolicy is not None})
normalize_embed_mode = _get_attr(_project_embed_module, "normalize_embed_mode", lambda value=None, default="readonly": default)
serialize_embed_policy = _get_attr(_project_embed_module, "serialize_embed_policy", _empty_dict_callable)
update_embed_policy = _get_attr(_project_embed_module, "update_embed_policy", _missing_callable)


# Project links.
SERVICE_APP = _get_attr(_project_links_module, "SERVICE_APP", "app")
SERVICE_CHAT = _get_attr(_project_links_module, "SERVICE_CHAT", "chat")
SERVICE_CHUNK = _get_attr(_project_links_module, "SERVICE_CHUNK", "chunk")
SERVICE_EDITOR3D = _get_attr(_project_links_module, "SERVICE_EDITOR3D", "editor3d")
SERVICE_OPENLAYER = _get_attr(_project_links_module, "SERVICE_OPENLAYER", "openlayer")
SERVICE_2D = _get_attr(_project_links_module, "SERVICE_2D", "cad2d")
SERVICE_LV = _get_attr(_project_links_module, "SERVICE_LV", "lv")
SERVICE_GEOSERVER = _get_attr(_project_links_module, "SERVICE_GEOSERVER", "geoserver")
SERVICE_FILES = _get_attr(_project_links_module, "SERVICE_FILES", "files")
SERVICE_VERSIONING = _get_attr(_project_links_module, "SERVICE_VERSIONING", "versioning")
SERVICE_EXTERNAL = _get_attr(_project_links_module, "SERVICE_EXTERNAL", "external")

RESOURCE_PROJECT = _get_attr(_project_links_module, "RESOURCE_PROJECT", "project")
RESOURCE_CONVERSATION = _get_attr(_project_links_module, "RESOURCE_CONVERSATION", "conversation")
RESOURCE_WORLD = _get_attr(_project_links_module, "RESOURCE_WORLD", "world")
RESOURCE_CHUNK_PROJECT = _get_attr(_project_links_module, "RESOURCE_CHUNK_PROJECT", "chunk_project")
RESOURCE_CHUNK_UNIVERSE = _get_attr(_project_links_module, "RESOURCE_CHUNK_UNIVERSE", "universe")
RESOURCE_PLAN2D = _get_attr(_project_links_module, "RESOURCE_PLAN2D", "plan2d")
RESOURCE_LV = _get_attr(_project_links_module, "RESOURCE_LV", "lv")
RESOURCE_BLOB = _get_attr(_project_links_module, "RESOURCE_BLOB", "blob")
RESOURCE_VERSION = _get_attr(_project_links_module, "RESOURCE_VERSION", "version")
RESOURCE_ARTIFACT = _get_attr(_project_links_module, "RESOURCE_ARTIFACT", "artifact")
RESOURCE_DATASET = _get_attr(_project_links_module, "RESOURCE_DATASET", "dataset")
RESOURCE_LAYER = _get_attr(_project_links_module, "RESOURCE_LAYER", "layer")
RESOURCE_URL = _get_attr(_project_links_module, "RESOURCE_URL", "url")

KNOWN_SERVICES = _get_attr(
    _project_links_module,
    "KNOWN_SERVICES",
    {
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
    },
)
KNOWN_RESOURCE_TYPES = _get_attr(
    _project_links_module,
    "KNOWN_RESOURCE_TYPES",
    {
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
    },
)
CHUNK_RESOURCE_TYPES = _get_attr(
    _project_links_module,
    "CHUNK_RESOURCE_TYPES",
    {RESOURCE_CHUNK_PROJECT, RESOURCE_CHUNK_UNIVERSE, RESOURCE_WORLD},
)

LINK_STATUS_ACTIVE = _get_attr(_project_links_module, "LINK_STATUS_ACTIVE", "active")
LINK_STATUS_PENDING = _get_attr(_project_links_module, "LINK_STATUS_PENDING", "pending")
LINK_STATUS_DISABLED = _get_attr(_project_links_module, "LINK_STATUS_DISABLED", "disabled")
LINK_STATUS_ERROR = _get_attr(_project_links_module, "LINK_STATUS_ERROR", "error")
LINK_STATUS_DELETED = _get_attr(_project_links_module, "LINK_STATUS_DELETED", "deleted")
LINK_STATUSES = _get_attr(
    _project_links_module,
    "LINK_STATUSES",
    {LINK_STATUS_ACTIVE, LINK_STATUS_PENDING, LINK_STATUS_DISABLED, LINK_STATUS_ERROR, LINK_STATUS_DELETED},
)

ProjectServiceLink = _get_attr(_project_links_module, "ProjectServiceLink")

build_chunk_reference = _get_attr(_project_links_module, "build_chunk_reference", _empty_dict_callable)
build_chunk_service_link = _get_attr(_project_links_module, "build_chunk_service_link", _missing_callable)
build_service_link = _get_attr(_project_links_module, "build_service_link", _missing_callable)
chunk_resource_id_for_type = _get_attr(_project_links_module, "chunk_resource_id_for_type", lambda **kwargs: "")
default_resource_type_for_service = _get_attr(_project_links_module, "default_resource_type_for_service", lambda service=None: RESOURCE_ARTIFACT)
find_chunk_service_link = _get_attr(_project_links_module, "find_chunk_service_link", _missing_callable)
find_project_service_link = _get_attr(_project_links_module, "find_project_service_link", _missing_callable)
get_project_links_model_classes = _get_attr(_project_links_module, "get_project_links_model_classes", _empty_list_callable)
get_project_links_model_status = _get_attr(_project_links_module, "get_project_links_model_status", lambda: {"ok": ProjectServiceLink is not None})
get_service_link_by_id = _get_attr(_project_links_module, "get_service_link_by_id", _missing_callable)
list_project_chunk_links = _get_attr(_project_links_module, "list_project_chunk_links", _empty_list_callable)
list_project_service_links = _get_attr(_project_links_module, "list_project_service_links", _empty_list_callable)
normalize_capabilities = _get_attr(_project_links_module, "normalize_capabilities", _empty_dict_callable)
normalize_link_status = _get_attr(_project_links_module, "normalize_link_status", lambda value=None, default=LINK_STATUS_ACTIVE: default)
normalize_resource_type = _get_attr(_project_links_module, "normalize_resource_type", lambda value=None, default=RESOURCE_ARTIFACT: default)
normalize_service = _get_attr(_project_links_module, "normalize_service", lambda value=None, default=SERVICE_EXTERNAL: default)
serialize_service_link = _get_attr(_project_links_module, "serialize_service_link", _empty_dict_callable)
serialize_service_links = _get_attr(_project_links_module, "serialize_service_links", _empty_list_callable)
upsert_chunk_service_link = _get_attr(_project_links_module, "upsert_chunk_service_link", _missing_callable)
upsert_chunk_service_links = _get_attr(_project_links_module, "upsert_chunk_service_links", _empty_list_callable)
upsert_service_link = _get_attr(_project_links_module, "upsert_service_link", _missing_callable)


# Project versions.
ProjectVersion = _get_attr(_project_versions_module, "ProjectVersion")
ProjectVersionLink = _get_attr(_project_versions_module, "ProjectVersionLink")

build_project_version = _get_attr(_project_versions_module, "build_project_version", _missing_callable)
create_project_version = _get_attr(_project_versions_module, "create_project_version", _missing_callable)
get_latest_project_version = _get_attr(_project_versions_module, "get_latest_project_version", _missing_callable)
get_project_version_by_id = _get_attr(_project_versions_module, "get_project_version_by_id", _missing_callable)
get_project_version_by_public_id = _get_attr(_project_versions_module, "get_project_version_by_public_id", _missing_callable)
get_project_versions_model_classes = _get_attr(_project_versions_module, "get_project_versions_model_classes", _empty_list_callable)
get_project_versions_model_status = _get_attr(_project_versions_module, "get_project_versions_model_status", lambda: {"ok": ProjectVersion is not None})
list_project_versions = _get_attr(_project_versions_module, "list_project_versions", _empty_list_callable)
next_project_version_no = _get_attr(_project_versions_module, "next_project_version_no", lambda *args, **kwargs: 1)
normalize_service_name = _get_attr(_project_versions_module, "normalize_service_name", lambda value=None, default="app": default)
normalize_version_kind = _get_attr(_project_versions_module, "normalize_version_kind", lambda value=None, default="snapshot": default)
normalize_version_status = _get_attr(_project_versions_module, "normalize_version_status", lambda value=None, default="stored": default)
serialize_project_version = _get_attr(_project_versions_module, "serialize_project_version", _empty_dict_callable)
serialize_project_versions = _get_attr(_project_versions_module, "serialize_project_versions", _empty_list_callable)


# Project audit.
ProjectAuditEvent = _get_attr(_project_audit_module, "ProjectAuditEvent")

build_audit_event = _get_attr(_project_audit_module, "build_audit_event", _missing_callable)
build_request_context_from_flask = _get_attr(_project_audit_module, "build_request_context_from_flask", _empty_dict_callable)
get_audit_event_by_id = _get_attr(_project_audit_module, "get_audit_event_by_id", _missing_callable)
get_project_audit_model_classes = _get_attr(_project_audit_module, "get_project_audit_model_classes", _empty_list_callable)
get_project_audit_model_status = _get_attr(_project_audit_module, "get_project_audit_model_status", lambda: {"ok": ProjectAuditEvent is not None})
list_project_audit_events = _get_attr(_project_audit_module, "list_project_audit_events", _empty_list_callable)
normalize_actor_type = _get_attr(_project_audit_module, "normalize_actor_type", lambda value=None, default="user": default)
normalize_audit_action = _get_attr(_project_audit_module, "normalize_audit_action", lambda value=None, default="event": default)
normalize_audit_category = _get_attr(_project_audit_module, "normalize_audit_category", lambda value=None, default="project": default)
normalize_audit_severity = _get_attr(_project_audit_module, "normalize_audit_severity", lambda value=None, default="info": default)
normalize_request_context = _get_attr(_project_audit_module, "normalize_request_context", _safe_dict)
record_audit_event = _get_attr(_project_audit_module, "record_audit_event", _missing_callable)
record_project_audit_event = _get_attr(_project_audit_module, "record_project_audit_event", _missing_callable)
serialize_audit_event = _get_attr(_project_audit_module, "serialize_audit_event", _empty_dict_callable)
serialize_audit_events = _get_attr(_project_audit_module, "serialize_audit_events", _empty_list_callable)


# Project invitations.
ROLE_OWNER = _get_attr(_project_invitations_module, "ROLE_OWNER", "owner")
ROLE_ADMIN = _get_attr(_project_invitations_module, "ROLE_ADMIN", "admin")
ROLE_EDITOR = _get_attr(_project_invitations_module, "ROLE_EDITOR", "editor")
ROLE_VIEWER = _get_attr(_project_invitations_module, "ROLE_VIEWER", "viewer")
VALID_PROJECT_ROLES = _get_attr(_project_invitations_module, "VALID_PROJECT_ROLES", {ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER})
INVITABLE_PROJECT_ROLES = _get_attr(_project_invitations_module, "INVITABLE_PROJECT_ROLES", {ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER})
DEFAULT_INVITATION_ROLE = _get_attr(_project_invitations_module, "DEFAULT_INVITATION_ROLE", ROLE_VIEWER)

STATUS_PENDING = _get_attr(_project_invitations_module, "STATUS_PENDING", "pending")
STATUS_ACCEPTED = _get_attr(_project_invitations_module, "STATUS_ACCEPTED", "accepted")
STATUS_REJECTED = _get_attr(_project_invitations_module, "STATUS_REJECTED", "rejected")
STATUS_REVOKED = _get_attr(_project_invitations_module, "STATUS_REVOKED", "revoked")
STATUS_EXPIRED = _get_attr(_project_invitations_module, "STATUS_EXPIRED", "expired")
STATUS_FAILED = _get_attr(_project_invitations_module, "STATUS_FAILED", "failed")
ACTIVE_INVITATION_STATUSES = _get_attr(_project_invitations_module, "ACTIVE_INVITATION_STATUSES", {STATUS_PENDING})
TERMINAL_INVITATION_STATUSES = _get_attr(
    _project_invitations_module,
    "TERMINAL_INVITATION_STATUSES",
    {STATUS_ACCEPTED, STATUS_REJECTED, STATUS_REVOKED, STATUS_EXPIRED, STATUS_FAILED},
)
VALID_INVITATION_STATUSES = _get_attr(
    _project_invitations_module,
    "VALID_INVITATION_STATUSES",
    set(ACTIVE_INVITATION_STATUSES) | set(TERMINAL_INVITATION_STATUSES),
)

DISPATCH_PENDING = _get_attr(_project_invitations_module, "DISPATCH_PENDING", "pending")
DISPATCH_SENT = _get_attr(_project_invitations_module, "DISPATCH_SENT", "sent")
DISPATCH_SKIPPED = _get_attr(_project_invitations_module, "DISPATCH_SKIPPED", "skipped")
DISPATCH_FAILED = _get_attr(_project_invitations_module, "DISPATCH_FAILED", "failed")
DISPATCH_PLACEHOLDER = _get_attr(_project_invitations_module, "DISPATCH_PLACEHOLDER", "placeholder")
DEFAULT_INVITATION_EXPIRY_DAYS = _get_attr(_project_invitations_module, "DEFAULT_INVITATION_EXPIRY_DAYS", 14)

ProjectInvitation = _get_attr(_project_invitations_module, "ProjectInvitation")

default_expires_at = _get_attr(_project_invitations_module, "default_expires_at", _missing_callable)
generate_plain_invitation_token = _get_attr(_project_invitations_module, "generate_plain_invitation_token", lambda: "")
generate_project_invitation_public_id = _get_attr(_project_invitations_module, "generate_project_invitation_public_id", lambda: "pinv_unavailable")
hash_invitation_token = _get_attr(_project_invitations_module, "hash_invitation_token", lambda token=None: "")
invitation_status_counts = _get_attr(_project_invitations_module, "invitation_status_counts", lambda invitations=None: {"total": 0})
is_valid_email = _get_attr(_project_invitations_module, "is_valid_email", lambda value=None: False)
normalize_dispatch_status = _get_attr(_project_invitations_module, "normalize_dispatch_status", lambda value=None: DISPATCH_PENDING)
normalize_email = _get_attr(_project_invitations_module, "normalize_email", lambda value=None: _safe_str(value, "", 320).lower())
normalize_invitation_role = _get_attr(_project_invitations_module, "normalize_invitation_role", lambda value=None, allow_owner=False: DEFAULT_INVITATION_ROLE)
normalize_invitation_status = _get_attr(_project_invitations_module, "normalize_invitation_status", lambda value=None: STATUS_PENDING)
serialize_project_invitation = _get_attr(_project_invitations_module, "serialize_project_invitation", _empty_dict_callable)
serialize_project_invitations = _get_attr(_project_invitations_module, "serialize_project_invitations", _empty_list_callable)


# ─────────────────────────────────────────────────────────────
# Optional future model modules
# ─────────────────────────────────────────────────────────────

def _try_import_optional_models() -> None:
    for module_name in _OPTIONAL_MODEL_MODULES:
        try:
            _import_model_module(module_name, required=False)
        except Exception as exc:
            _store_import_error(module_name, exc)


_try_import_optional_models()


# ─────────────────────────────────────────────────────────────
# Backward-compatible aliases
# ─────────────────────────────────────────────────────────────

User = AppUser
ProjectUser = AppUser

if ProjectAccess is None:
    ProjectAccess = ProjectMembership

if ProjectPermission is None:
    ProjectPermission = ProjectMembership

if ProjectVersionLink is None:
    ProjectVersionLink = ProjectVersion

ProjectInvite = ProjectInvitation
ProjectInvitationLink = ProjectInvitation


# ─────────────────────────────────────────────────────────────
# Removed Default-User compatibility shims
# ─────────────────────────────────────────────────────────────

def ensure_default_user(*args: Any, **kwargs: Any) -> None:
    """
    Removed compatibility shim.

    vectoplan-auth is the user truth.
    This function intentionally creates no user and returns None.
    """
    return None


def current_user_id_placeholder() -> int:
    """
    Removed compatibility shim.

    Returns 0 instead of a synthetic local user id.
    """
    return 0


# ─────────────────────────────────────────────────────────────
# Model registry
# ─────────────────────────────────────────────────────────────

def _build_core_model_classes() -> Tuple[Any, ...]:
    return _compact_model_classes(
        (
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
            ProjectInvitation,
        )
    )


CORE_MODEL_CLASSES = _build_core_model_classes()


def get_core_model_classes() -> Tuple[Any, ...]:
    try:
        return _compact_model_classes(CORE_MODEL_CLASSES)
    except Exception:
        return _build_core_model_classes()


def register_all_models() -> Tuple[Any, ...]:
    """
    Force-import all currently known models.

    SQLAlchemy registers declarative models at class definition/import time.
    Returning the classes is enough to ensure the import path was executed.
    """
    return get_core_model_classes()


def get_model_class_map() -> Dict[str, Any]:
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
    result: Dict[str, List[str]] = {}

    try:
        for name, model_cls in get_model_class_map().items():
            result[name] = _model_columns(model_cls)
    except Exception:
        pass

    return result


def is_app_chunk_model_shape_ready() -> bool:
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


def is_project_invitation_model_shape_ready() -> bool:
    try:
        column_map = get_model_column_map()
        columns = set(column_map.get("ProjectInvitation", []))

        required = {
            "id",
            "public_id",
            "project_id",
            "email",
            "email_normalized",
            "auth_user_id",
            "role",
            "status",
            "dispatch_status",
            "invited_by_user_id",
            "invited_at",
            "expires_at",
            "metadata_json",
        }

        return required.issubset(columns)
    except Exception:
        return False


def is_auth_link_model_shape_ready() -> bool:
    try:
        column_map = get_model_column_map()
        columns = set(column_map.get("AppUser", []))

        if not columns:
            return False

        auth_link_columns = {
            "auth_user_id",
            "email",
        }

        return bool(auth_link_columns.intersection(columns))
    except Exception:
        return False


def is_project_auth_context_shape_ready() -> bool:
    try:
        column_map = get_model_column_map()
        project_columns = set(column_map.get("Project", []))

        expected = {
            "auth_owner_user_id",
            "auth_account_id",
            "owner_subject_type",
            "project_scope",
            "is_demo",
        }

        return expected.issubset(project_columns)
    except Exception:
        return False


def get_project_invitations_model_status() -> Dict[str, Any]:
    try:
        model_cls = globals().get("ProjectInvitation")
        if model_cls is None:
            return {
                "ok": False,
                "available": False,
                "reason": "ProjectInvitation not imported",
            }

        return {
            "ok": True,
            "available": True,
            "model": _safe_name(getattr(model_cls, "__name__", ""), "ProjectInvitation"),
            "table": _safe_name(getattr(model_cls, "__tablename__", ""), "project_invitations"),
            "columns": _model_columns(model_cls),
            "shapeReady": is_project_invitation_model_shape_ready(),
            "validRoles": sorted(list(VALID_PROJECT_ROLES)),
            "invitableRoles": sorted(list(INVITABLE_PROJECT_ROLES)),
            "validStatuses": sorted(list(VALID_INVITATION_STATUSES)),
            "dispatchStatuses": sorted([DISPATCH_PENDING, DISPATCH_SENT, DISPATCH_SKIPPED, DISPATCH_FAILED]),
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "error": str(exc),
        }


def get_model_import_status() -> Dict[str, Any]:
    missing_required: List[str] = []

    try:
        for module_name in _REQUIRED_MODEL_MODULES:
            if _MODEL_IMPORT_CACHE.get(module_name) is None:
                missing_required.append(module_name)
    except Exception:
        pass

    return {
        "ok": not bool(_MODEL_IMPORT_ERRORS) and not missing_required,
        "source": _MODEL_SOURCE,
        "core_fallback": False,
        "modular_loaded": True,
        "required_modules": list(_REQUIRED_MODEL_MODULES),
        "optional_modules": list(_OPTIONAL_MODEL_MODULES),
        "missing_required_modules": missing_required,
        "errors": dict(_MODEL_IMPORT_ERRORS),
        "model_count": len(get_core_model_classes()),
        "models": get_model_class_names(),
        "tables": get_model_table_names(),
        "columnMap": get_model_column_map(),
        "appChunkModelShapeReady": is_app_chunk_model_shape_ready(),
        "projectInvitationModelShapeReady": is_project_invitation_model_shape_ready(),
        "authLinkModelShapeReady": is_auth_link_model_shape_ready(),
        "projectAuthContextShapeReady": is_project_auth_context_shape_ready(),
        "defaultUserRemoved": True,
    }


def _call_status(fn: Callable[[], Dict[str, Any]], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return _safe_dict(fn())
    except Exception as exc:
        result = dict(fallback or {})
        result.update(
            {
                "ok": False,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
        )
        return result


def get_model_status() -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "ok": True,
        "import": get_model_import_status(),
        "tables": get_model_table_names(),
        "appChunkModelShapeReady": is_app_chunk_model_shape_ready(),
        "projectInvitationModelShapeReady": is_project_invitation_model_shape_ready(),
        "authLinkModelShapeReady": is_auth_link_model_shape_ready(),
        "projectAuthContextShapeReady": is_project_auth_context_shape_ready(),
        "defaultUserRemoved": True,
    }

    try:
        status["users"] = _call_status(get_user_model_status, {"service": "users"})
        status["legacy"] = _call_status(get_legacy_model_status, {"service": "legacy"})
        status["projects"] = _call_status(get_project_model_status, {"service": "projects"})
        status["project_access"] = _call_status(get_project_access_model_status, {"service": "project_access"})
        status["project_embed"] = _call_status(get_project_embed_model_status, {"service": "project_embed"})
        status["project_links"] = _call_status(get_project_links_model_status, {"service": "project_links"})
        status["project_versions"] = _call_status(get_project_versions_model_status, {"service": "project_versions"})
        status["project_audit"] = _call_status(get_project_audit_model_status, {"service": "project_audit"})
        status["project_invitations"] = get_project_invitations_model_status()

        import_status = _safe_dict(status.get("import"))
        status["ok"] = bool(import_status.get("ok", False))

    except Exception as exc:
        status["ok"] = False
        status["error"] = str(exc)

    return status


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

    # project-management models
    "AppUser",
    "ProjectMembership",
    "ProjectEmbedPolicy",
    "ProjectServiceLink",
    "ProjectAuditEvent",
    "ProjectInvitation",

    # aliases
    "User",
    "ProjectUser",
    "ProjectAccess",
    "ProjectPermission",
    "ProjectVersionLink",
    "ProjectInvite",
    "ProjectInvitationLink",

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

    # invitation constants
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "ROLE_VIEWER",
    "VALID_PROJECT_ROLES",
    "INVITABLE_PROJECT_ROLES",
    "DEFAULT_INVITATION_ROLE",
    "STATUS_PENDING",
    "STATUS_ACCEPTED",
    "STATUS_REJECTED",
    "STATUS_REVOKED",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "ACTIVE_INVITATION_STATUSES",
    "TERMINAL_INVITATION_STATUSES",
    "VALID_INVITATION_STATUSES",
    "DISPATCH_PENDING",
    "DISPATCH_SENT",
    "DISPATCH_SKIPPED",
    "DISPATCH_FAILED",
    "DISPATCH_PLACEHOLDER",
    "DEFAULT_INVITATION_EXPIRY_DAYS",

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
    "is_project_invitation_model_shape_ready",
    "is_auth_link_model_shape_ready",
    "is_project_auth_context_shape_ready",
    "get_project_invitations_model_status",

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

    # invitation helpers
    "normalize_email",
    "is_valid_email",
    "normalize_invitation_role",
    "normalize_invitation_status",
    "normalize_dispatch_status",
    "generate_project_invitation_public_id",
    "generate_plain_invitation_token",
    "hash_invitation_token",
    "default_expires_at",
    "invitation_status_counts",
    "serialize_project_invitation",
    "serialize_project_invitations",

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