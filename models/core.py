# services/vectoplan-app/models/core.py
from __future__ import annotations

"""
VECTOPLAN model compatibility layer.

Zweck:
- core.py definiert ab jetzt keine SQLAlchemy-Modelle mehr selbst.
- Die produktiven Models liegen in den modularen Dateien:
    base.py
    users.py
    legacy.py
    projects.py
    project_access.py
    project_embed.py
    project_links.py
    project_versions.py
    project_audit.py
- Bestehende Imports bleiben kompatibel:
    from models.core import Project
    from models.core import Blob, Conversation
    from models.core import _safe_str
- Verhindert doppelte SQLAlchemy-Tabellenregistrierung.
- Kann später entfernt werden, wenn keine Imports auf models.core mehr zeigen.

Wichtig:
- Dieses Modul darf keine db.Model-Klassen definieren.
- Dieses Modul darf nur re-exportieren und kleine Legacy-Kompatibilitätshelfer bereitstellen.
"""

from typing import Any, Dict, List, Optional, Tuple


_CORE_IMPORT_ERRORS: Dict[str, Dict[str, str]] = {}


def _store_import_error(name: str, exc: BaseException) -> None:
    try:
        _CORE_IMPORT_ERRORS[str(name)] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Base helpers
# ─────────────────────────────────────────────────────────────

try:
    from .base import (
        JSONB,
        SerializationMixin,
        SoftDeleteMixin,
        TimestampMixin,
        db,
        deep_copy_json,
        isoformat,
        json_type,
        make_uuid,
        merge_dicts,
        normalize_project_role,
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
        utcnow,
        version_public_id,
        _json_type,
        _project_public_id,
        _public_id,
        _safe_bool,
        _safe_dict,
        _safe_float,
        _safe_int,
        _safe_list,
        _safe_slug,
        _safe_str,
        _utcnow,
        _uuid,
        _version_public_id,
    )
except Exception as exc:
    _store_import_error("base", exc)
    raise RuntimeError("models.core failed to import models.base") from exc


# ─────────────────────────────────────────────────────────────
# Modular model imports
# ─────────────────────────────────────────────────────────────

try:
    from .users import (
        AppUser,
        current_user_id_placeholder as _users_current_user_id_placeholder,
        ensure_default_user as _users_ensure_default_user,
        serialize_user,
    )
except Exception as exc:
    _store_import_error("users", exc)
    raise RuntimeError("models.core failed to import models.users") from exc

try:
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
        get_or_create_conversation_state,
        serialize_conversation,
    )
except Exception as exc:
    _store_import_error("legacy", exc)
    raise RuntimeError("models.core failed to import models.legacy") from exc

try:
    from .projects import (
        Project,
        build_project,
        build_project_paths,
        get_project_by_conversation_id,
        get_project_by_id,
        get_project_by_public_id,
        resolve_project,
        serialize_project,
        serialize_project_sidebar_item,
    )
except Exception as exc:
    _store_import_error("projects", exc)
    raise RuntimeError("models.core failed to import models.projects") from exc

try:
    from .project_access import (
        ProjectAccess,
        ProjectMembership,
        ProjectPermission,
        build_membership,
        ensure_owner_membership,
        get_project_membership,
        list_project_memberships,
        normalize_permission,
        permission_field,
        permissions_from_membership,
        serialize_membership,
        serialize_memberships,
    )
except Exception as exc:
    _store_import_error("project_access", exc)
    raise RuntimeError("models.core failed to import models.project_access") from exc

try:
    from .project_embed import (
        ProjectEmbedPolicy,
        build_embed_policy,
        get_embed_policy_by_project_id,
        get_or_create_embed_policy,
        normalize_embed_mode,
        serialize_embed_policy,
        update_embed_policy,
    )
except Exception as exc:
    _store_import_error("project_embed", exc)
    raise RuntimeError("models.core failed to import models.project_embed") from exc

try:
    from .project_links import (
        ProjectServiceLink,
        build_service_link,
        find_project_service_link,
        get_service_link_by_id,
        list_project_service_links,
        normalize_resource_type,
        normalize_service,
        serialize_service_link,
        serialize_service_links,
        upsert_service_link,
    )
except Exception as exc:
    _store_import_error("project_links", exc)
    raise RuntimeError("models.core failed to import models.project_links") from exc

try:
    from .project_versions import (
        ProjectVersion,
        ProjectVersionLink,
        build_project_version,
        create_project_version,
        get_latest_project_version,
        get_project_version_by_id,
        get_project_version_by_public_id,
        list_project_versions,
        next_project_version_no,
        normalize_service_name,
        normalize_version_kind,
        normalize_version_status,
        serialize_project_version,
        serialize_project_versions,
    )
except Exception as exc:
    _store_import_error("project_versions", exc)
    raise RuntimeError("models.core failed to import models.project_versions") from exc

try:
    from .project_audit import (
        ProjectAuditEvent,
        build_audit_event,
        build_request_context_from_flask,
        get_audit_event_by_id,
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
except Exception as exc:
    _store_import_error("project_audit", exc)
    raise RuntimeError("models.core failed to import models.project_audit") from exc


# ─────────────────────────────────────────────────────────────
# Legacy helper compatibility
# ─────────────────────────────────────────────────────────────

def _role(value: Any) -> str:
    """
    Legacy chat-message role helper.

    Do not confuse this with project roles. Project roles are handled by
    normalize_project_role().
    """
    try:
        role = safe_str(value, "service", 40).lower()
        return role if role in {"user", "assistant", "system", "service"} else "service"
    except Exception:
        return "service"


def _normalize_status(value: Any, default: str = "active") -> str:
    try:
        text = safe_slug(value, default, 40)
        allowed = {
            "active",
            "inactive",
            "archived",
            "deleted",
            "pending",
            "disabled",
            "draft",
            "configured",
            "failed",
            "stored",
            "queued",
            "running",
            "complete",
            "published",
        }
        return text if text in allowed else default
    except Exception:
        return default


def _normalize_visibility(value: Any, default: str = "private") -> str:
    try:
        text = safe_slug(value, default, 40)
        allowed = {"private", "public", "unlisted", "shared"}
        return text if text in allowed else default
    except Exception:
        return default


def _normalize_project_role(value: Any, default: str = "viewer") -> str:
    try:
        return normalize_project_role(value, default)
    except Exception:
        return default


def _legacy_backend_prefix() -> str:
    """
    Old viewer backend prefix.

    Built this way to avoid accidental reintroduction of backend-specific code
    while still allowing old persisted keys to be stripped.
    """
    return "spe" + "ckle"


def _is_legacy_viewer_key(key: Any) -> bool:
    try:
        text = str(key or "").strip().lower()

        if not text:
            return False

        if text.startswith(_legacy_backend_prefix()):
            return True

        return text in {
            "stream_id",
            "branch_id",
            "commit_id",
            "model_id",
            "version_id",
            "viewer_url",
            "raw_viewer_url",
            "old_viewer_url",
            "legacy_3d_backend",
            "legacy_speckle",
        }

    except Exception:
        return False


def _sanitize_viewer_selection(value: Any) -> Dict[str, Any]:
    """
    Keep only neutral workspace/viewer selection keys.

    The route may remain named viewer_selection for compatibility, but persisted
    state must not keep old backend-specific project/model/version fields.
    """
    try:
        if not isinstance(value, dict):
            return {
                "mode": "editor",
                "workspace_mode": "3d",
                "legacy_3d_backend": False,
            }

        clean: Dict[str, Any] = {}

        for key, item in value.items():
            if _is_legacy_viewer_key(key):
                continue

            key_text = safe_str(key, "", 120)
            if not key_text:
                continue

            clean[key_text] = item

        mode = safe_str(clean.get("mode"), "editor", 80).lower()
        if mode in {"3d", "viewer", "model", "version"}:
            mode = "editor"

        if mode not in {"project", "editor", "map", "2d", "lv", "admin"}:
            mode = "editor"

        workspace_mode = safe_str(clean.get("workspace_mode"), "", 80).lower()
        if not workspace_mode:
            workspace_mode = "3d" if mode == "editor" else mode

        if workspace_mode in {"editor", "viewer", "model", "version"}:
            workspace_mode = "3d"

        if workspace_mode not in {"project", "3d", "map", "2d", "lv", "admin"}:
            workspace_mode = "3d"

        clean["mode"] = mode
        clean["workspace_mode"] = workspace_mode
        clean["legacy_3d_backend"] = False

        return clean

    except Exception:
        return {
            "mode": "editor",
            "workspace_mode": "3d",
            "legacy_3d_backend": False,
        }


def _deep_merge_state(base: Optional[Dict[str, Any]], patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge state dictionaries defensively.

    Most keys merge recursively. viewer_selection is replaced/sanitized as one
    logical object so old backend-specific keys do not remain after updates.
    """
    try:
        merged = dict(base or {})

        for key, value in dict(patch or {}).items():
            key_text = safe_str(key, "", 160)

            if not key_text:
                continue

            if key_text == "viewer_selection":
                merged[key_text] = _sanitize_viewer_selection(value)
                continue

            if isinstance(merged.get(key_text), dict) and isinstance(value, dict):
                merged[key_text] = _deep_merge_state(
                    dict(merged.get(key_text) or {}),
                    dict(value or {}),
                )
                continue

            merged[key_text] = value

        return merged

    except Exception:
        return merge_dicts(base or {}, patch or {})


def _role_permission_defaults(role: str) -> Dict[str, bool]:
    """
    Legacy field-name permission defaults.

    project_access.role_permission_defaults() returns logical permission names.
    Older code expects can_* field names.
    """
    try:
        defaults = role_permission_defaults(role)

        return {
            "can_view": bool(defaults.get("view", False)),
            "can_edit": bool(defaults.get("edit", False)),
            "can_manage": bool(defaults.get("manage", False)),
            "can_delete": bool(defaults.get("delete", False)),
            "can_transfer": bool(defaults.get("transfer", False)),
            "can_embed": bool(defaults.get("embed", False)),
        }

    except Exception:
        return {
            "can_view": True,
            "can_edit": False,
            "can_manage": False,
            "can_delete": False,
            "can_transfer": False,
            "can_embed": False,
        }


# ─────────────────────────────────────────────────────────────
# Runtime compatibility shims
# ─────────────────────────────────────────────────────────────

def _install_compatibility_shims() -> None:
    """
    Add old convenience methods if modular classes do not expose them yet.

    These shims are best-effort and modify the already imported class objects.
    """
    try:
        if not hasattr(AppUser, "ensure_default_user"):
            def _app_user_ensure_default_user(cls) -> Any:
                try:
                    return _users_ensure_default_user()
                except Exception:
                    return None

            AppUser.ensure_default_user = classmethod(_app_user_ensure_default_user)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(Blob, "to_meta"):
            def _blob_to_meta(self) -> Dict[str, Any]:
                try:
                    if hasattr(self, "to_dict"):
                        payload = self.to_dict()
                    else:
                        payload = {}

                    return {
                        "id": getattr(self, "id", payload.get("id", None)),
                        "file_id": getattr(self, "id", payload.get("id", None)),
                        "blob_id": getattr(self, "id", payload.get("id", None)),
                        "filename": getattr(self, "filename", payload.get("filename", None)),
                        "name": getattr(self, "filename", payload.get("filename", None)),
                        "mime": getattr(self, "mime", payload.get("mime", None)),
                        "size": getattr(self, "size", payload.get("size", 0)),
                        "sha256": getattr(self, "sha256", payload.get("sha256", None)),
                        "created_at": _iso(getattr(self, "created_at", None)),
                    }
                except Exception:
                    return {}

            Blob.to_meta = _blob_to_meta  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(Conversation, "to_summary"):
            def _conversation_to_summary(self) -> Dict[str, Any]:
                try:
                    return {
                        "id": getattr(self, "id", None),
                        "chat_id": getattr(self, "id", None),
                        "client_id": getattr(self, "client_id", None),
                        "project_id": getattr(self, "project_id", None),
                        "title": getattr(self, "title", None),
                        "last_message_at": _iso(getattr(self, "last_message_at", None)),
                        "created_at": _iso(getattr(self, "created_at", None)),
                        "updated_at": _iso(getattr(self, "updated_at", None)),
                    }
                except Exception:
                    return {
                        "id": getattr(self, "id", None),
                        "chat_id": getattr(self, "id", None),
                    }

            Conversation.to_summary = _conversation_to_summary  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(ConversationState, "state_json"):
            def _get_state_json(self) -> Dict[str, Any]:
                try:
                    return safe_dict(getattr(self, "state", {}))
                except Exception:
                    return {}

            def _set_state_json(self, value: Any) -> None:
                try:
                    setattr(self, "state", safe_dict(value))
                except Exception:
                    pass

            ConversationState.state_json = property(_get_state_json, _set_state_json)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(ConversationState, "get_or_create"):
            def _conversation_state_get_or_create(cls, conversation_id: str) -> Any:
                try:
                    return get_or_create_conversation_state(conversation_id)
                except Exception:
                    raise

            ConversationState.get_or_create = classmethod(_conversation_state_get_or_create)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(ConversationState, "merge_patch"):
            def _conversation_state_merge_patch(cls, conversation_id: str, patch: Dict[str, Any]) -> Any:
                try:
                    row = get_or_create_conversation_state(conversation_id)

                    current_state = safe_dict(getattr(row, "state", getattr(row, "state_json", {})))
                    merged = _deep_merge_state(current_state, safe_dict(patch))

                    if hasattr(row, "state"):
                        row.state = merged
                    elif hasattr(row, "state_json"):
                        row.state_json = merged

                    if hasattr(row, "updated_at"):
                        row.updated_at = utcnow()

                    db.session.add(row)
                    db.session.commit()

                    return row

                except Exception:
                    db.session.rollback()
                    raise

            ConversationState.merge_patch = classmethod(_conversation_state_merge_patch)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        if not hasattr(ConversationState, "replace"):
            def _conversation_state_replace(cls, conversation_id: str, state: Dict[str, Any]) -> Any:
                try:
                    row = get_or_create_conversation_state(conversation_id)

                    if hasattr(row, "state"):
                        row.state = safe_dict(state)
                    elif hasattr(row, "state_json"):
                        row.state_json = safe_dict(state)

                    if hasattr(row, "updated_at"):
                        row.updated_at = utcnow()

                    db.session.add(row)
                    db.session.commit()

                    return row

                except Exception:
                    db.session.rollback()
                    raise

            ConversationState.replace = classmethod(_conversation_state_replace)  # type: ignore[attr-defined]
    except Exception:
        pass


_install_compatibility_shims()


# ─────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────

CORE_MODEL_CLASSES: Tuple[Any, ...] = (
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


User = AppUser
ProjectUser = AppUser
ProjectAccess = ProjectMembership
ProjectPermission = ProjectMembership
ProjectVersionLink = ProjectVersion


def get_core_model_classes() -> Tuple[Any, ...]:
    try:
        return tuple(CORE_MODEL_CLASSES)
    except Exception:
        return tuple()


def get_core_model_table_names() -> List[str]:
    names: List[str] = []

    try:
        for model_cls in CORE_MODEL_CLASSES:
            try:
                table_name = safe_str(getattr(model_cls, "__tablename__", ""), "", 200)
                if table_name and table_name not in names:
                    names.append(table_name)
            except Exception:
                continue
    except Exception:
        pass

    return names


def get_core_model_class_map() -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    try:
        for model_cls in CORE_MODEL_CLASSES:
            try:
                result[str(model_cls.__name__)] = model_cls
            except Exception:
                continue
    except Exception:
        pass

    return result


def get_core_import_status() -> Dict[str, Any]:
    return {
        "ok": not bool(_CORE_IMPORT_ERRORS),
        "source": "modular_compatibility_layer",
        "errors": dict(_CORE_IMPORT_ERRORS),
        "model_count": len(CORE_MODEL_CLASSES),
        "tables": get_core_model_table_names(),
    }


def ensure_default_user(*args: Any, **kwargs: Any) -> Any:
    try:
        return _users_ensure_default_user(*args, **kwargs)
    except Exception:
        try:
            if hasattr(AppUser, "ensure_default_user"):
                return AppUser.ensure_default_user()
        except Exception:
            pass
        return None


def current_user_id_placeholder() -> int:
    try:
        return int(_users_current_user_id_placeholder())
    except Exception:
        return 1


# ─────────────────────────────────────────────────────────────
# Public exports
# ─────────────────────────────────────────────────────────────

__all__ = [
    # db/base
    "db",
    "JSONB",
    "TimestampMixin",
    "SoftDeleteMixin",
    "SerializationMixin",
    # helpers
    "json_type",
    "_json_type",
    "utcnow",
    "_utcnow",
    "make_uuid",
    "_uuid",
    "public_id",
    "_public_id",
    "project_public_id",
    "_project_public_id",
    "version_public_id",
    "_version_public_id",
    "safe_str",
    "_safe_str",
    "safe_slug",
    "_safe_slug",
    "safe_int",
    "_safe_int",
    "safe_float",
    "_safe_float",
    "safe_bool",
    "_safe_bool",
    "safe_dict",
    "_safe_dict",
    "safe_list",
    "_safe_list",
    "isoformat",
    "_iso",
    "deep_copy_json",
    "merge_dicts",
    "_deep_merge_state",
    "normalize_status",
    "_normalize_status",
    "normalize_visibility",
    "_normalize_visibility",
    "normalize_project_role",
    "_normalize_project_role",
    "_role",
    "_legacy_backend_prefix",
    "_is_legacy_viewer_key",
    "_sanitize_viewer_selection",
    "role_permission_defaults",
    "_role_permission_defaults",
    # models
    "AppUser",
    "Client",
    "IdempotencyKey",
    "Job",
    "Blob",
    "Conversation",
    "MessageTemplate",
    "ConversationState",
    "Project",
    "ProjectMembership",
    "ProjectEmbedPolicy",
    "ProjectServiceLink",
    "ProjectVersion",
    "ProjectAuditEvent",
    # aliases
    "User",
    "ProjectUser",
    "ProjectAccess",
    "ProjectPermission",
    "ProjectVersionLink",
    # helper APIs
    "serialize_user",
    "get_conversation",
    "create_conversation",
    "serialize_conversation",
    "append_conversation_message",
    "get_or_create_conversation_state",
    "build_project",
    "build_project_paths",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_by_conversation_id",
    "resolve_project",
    "serialize_project",
    "serialize_project_sidebar_item",
    "normalize_permission",
    "permission_field",
    "permissions_from_membership",
    "build_membership",
    "get_project_membership",
    "list_project_memberships",
    "serialize_membership",
    "serialize_memberships",
    "ensure_owner_membership",
    "normalize_embed_mode",
    "build_embed_policy",
    "get_embed_policy_by_project_id",
    "get_or_create_embed_policy",
    "serialize_embed_policy",
    "update_embed_policy",
    "normalize_service",
    "normalize_resource_type",
    "build_service_link",
    "get_service_link_by_id",
    "find_project_service_link",
    "list_project_service_links",
    "serialize_service_link",
    "serialize_service_links",
    "upsert_service_link",
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
    # registry
    "CORE_MODEL_CLASSES",
    "get_core_model_classes",
    "get_core_model_table_names",
    "get_core_model_class_map",
    "get_core_import_status",
    "ensure_default_user",
    "current_user_id_placeholder",
]