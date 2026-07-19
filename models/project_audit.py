# services/vectoplan-app/models/project_audit.py
from __future__ import annotations

import datetime as _dt
import hashlib
import inspect
import ipaddress
import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from sqlalchemy import event as _sa_event
except Exception:  # pragma: no cover - SQLAlchemy exists in app runtime.
    _sa_event = None  # type: ignore[assignment]

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    public_id,
    safe_bool,
    safe_dict,
    safe_int,
    safe_list,
    safe_slug,
    safe_str,
    utcnow,
)


# ─────────────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────────────

AUDIT_CATEGORY_PROJECT = "project"
AUDIT_CATEGORY_ACCESS = "access"
AUDIT_CATEGORY_EMBED = "embed"
AUDIT_CATEGORY_SERVICE_LINK = "service_link"
AUDIT_CATEGORY_VERSION = "version"
AUDIT_CATEGORY_FILE = "file"
AUDIT_CATEGORY_WORKSPACE = "workspace"
AUDIT_CATEGORY_SYSTEM = "system"
AUDIT_CATEGORY_PROVISIONING = "provisioning"
AUDIT_CATEGORY_ACCESS_SYNC = "access_sync"
AUDIT_CATEGORY_RECONCILIATION = "reconciliation"
AUDIT_CATEGORY_IDENTITY = "identity"
AUDIT_CATEGORY_INVITATION = "invitation"

AUDIT_CATEGORIES = {
    AUDIT_CATEGORY_PROJECT,
    AUDIT_CATEGORY_ACCESS,
    AUDIT_CATEGORY_EMBED,
    AUDIT_CATEGORY_SERVICE_LINK,
    AUDIT_CATEGORY_VERSION,
    AUDIT_CATEGORY_FILE,
    AUDIT_CATEGORY_WORKSPACE,
    AUDIT_CATEGORY_SYSTEM,
    AUDIT_CATEGORY_PROVISIONING,
    AUDIT_CATEGORY_ACCESS_SYNC,
    AUDIT_CATEGORY_RECONCILIATION,
    AUDIT_CATEGORY_IDENTITY,
    AUDIT_CATEGORY_INVITATION,
}


# ─────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────

AUDIT_ACTION_CREATED = "created"
AUDIT_ACTION_UPDATED = "updated"
AUDIT_ACTION_DELETED = "deleted"
AUDIT_ACTION_RESTORED = "restored"
AUDIT_ACTION_ARCHIVED = "archived"
AUDIT_ACTION_TRANSFERRED = "transferred"
AUDIT_ACTION_VIEWED = "viewed"
AUDIT_ACTION_OPENED = "opened"
AUDIT_ACTION_EXPORTED = "exported"
AUDIT_ACTION_IMPORTED = "imported"
AUDIT_ACTION_LINKED = "linked"
AUDIT_ACTION_UNLINKED = "unlinked"
AUDIT_ACTION_PERMISSION_CHANGED = "permission_changed"
AUDIT_ACTION_EMBED_CHANGED = "embed_changed"
AUDIT_ACTION_VERSION_CREATED = "version_created"
AUDIT_ACTION_ERROR = "error"

AUDIT_ACTION_MEMBER_REMOVED = "member_removed"

AUDIT_ACTION_INVITATION_CREATED = "invitation_created"
AUDIT_ACTION_INVITATION_DISPATCHED = "invitation_dispatched"
AUDIT_ACTION_INVITATION_FAILED = "invitation_failed"
AUDIT_ACTION_INVITATION_REVOKED = "invitation_revoked"
AUDIT_ACTION_INVITATION_REJECTED = "invitation_rejected"
AUDIT_ACTION_INVITATION_ACCEPTED = "invitation_accepted"
AUDIT_ACTION_INVITATION_EXPIRED = "invitation_expired"

AUDIT_ACTION_CHUNK_PROVISIONING_PENDING = "chunk_provisioning_pending"
AUDIT_ACTION_CHUNK_PROVISIONING_STARTED = "chunk_provisioning_started"
AUDIT_ACTION_CHUNK_PROVISIONING_SUCCEEDED = "chunk_provisioning_succeeded"
AUDIT_ACTION_CHUNK_PROVISIONING_FALLBACK = "chunk_provisioning_fallback"
AUDIT_ACTION_CHUNK_PROVISIONING_FAILED = "chunk_provisioning_failed"
AUDIT_ACTION_CHUNK_PROVISIONING_REPAIR_REQUIRED = "chunk_provisioning_repair_required"
AUDIT_ACTION_CHUNK_PROVISIONING_DISABLED = "chunk_provisioning_disabled"
AUDIT_ACTION_CHUNK_PROJECT_LINKED = "chunk_project_linked"

AUDIT_ACTION_CHUNK_ACCESS_SYNC_PENDING = "chunk_access_sync_pending"
AUDIT_ACTION_CHUNK_ACCESS_SYNC_STARTED = "chunk_access_sync_started"
AUDIT_ACTION_CHUNK_ACCESS_SYNC_SUCCEEDED = "chunk_access_sync_succeeded"
AUDIT_ACTION_CHUNK_ACCESS_SYNC_FAILED = "chunk_access_sync_failed"
AUDIT_ACTION_CHUNK_ACCESS_SYNC_REPAIR_REQUIRED = "chunk_access_sync_repair_required"
AUDIT_ACTION_CHUNK_ACCESS_INITIALIZED = "chunk_access_initialized"
AUDIT_ACTION_CHUNK_ASSIGNMENT_UPSERTED = "chunk_assignment_upserted"
AUDIT_ACTION_CHUNK_ASSIGNMENT_REVOKED = "chunk_assignment_revoked"
AUDIT_ACTION_CHUNK_OWNER_TRANSFERRED = "chunk_owner_transferred"

AUDIT_ACTION_RECONCILIATION_STARTED = "reconciliation_started"
AUDIT_ACTION_RECONCILIATION_COMPLETED = "reconciliation_completed"
AUDIT_ACTION_RECONCILIATION_FAILED = "reconciliation_failed"
AUDIT_ACTION_RECONCILIATION_SKIPPED = "reconciliation_skipped"

AUDIT_ACTIONS = {
    AUDIT_ACTION_CREATED,
    AUDIT_ACTION_UPDATED,
    AUDIT_ACTION_DELETED,
    AUDIT_ACTION_RESTORED,
    AUDIT_ACTION_ARCHIVED,
    AUDIT_ACTION_TRANSFERRED,
    AUDIT_ACTION_VIEWED,
    AUDIT_ACTION_OPENED,
    AUDIT_ACTION_EXPORTED,
    AUDIT_ACTION_IMPORTED,
    AUDIT_ACTION_LINKED,
    AUDIT_ACTION_UNLINKED,
    AUDIT_ACTION_PERMISSION_CHANGED,
    AUDIT_ACTION_EMBED_CHANGED,
    AUDIT_ACTION_VERSION_CREATED,
    AUDIT_ACTION_ERROR,
    AUDIT_ACTION_MEMBER_REMOVED,
    AUDIT_ACTION_INVITATION_CREATED,
    AUDIT_ACTION_INVITATION_DISPATCHED,
    AUDIT_ACTION_INVITATION_FAILED,
    AUDIT_ACTION_INVITATION_REVOKED,
    AUDIT_ACTION_INVITATION_REJECTED,
    AUDIT_ACTION_INVITATION_ACCEPTED,
    AUDIT_ACTION_INVITATION_EXPIRED,
    AUDIT_ACTION_CHUNK_PROVISIONING_PENDING,
    AUDIT_ACTION_CHUNK_PROVISIONING_STARTED,
    AUDIT_ACTION_CHUNK_PROVISIONING_SUCCEEDED,
    AUDIT_ACTION_CHUNK_PROVISIONING_FALLBACK,
    AUDIT_ACTION_CHUNK_PROVISIONING_FAILED,
    AUDIT_ACTION_CHUNK_PROVISIONING_REPAIR_REQUIRED,
    AUDIT_ACTION_CHUNK_PROVISIONING_DISABLED,
    AUDIT_ACTION_CHUNK_PROJECT_LINKED,
    AUDIT_ACTION_CHUNK_ACCESS_SYNC_PENDING,
    AUDIT_ACTION_CHUNK_ACCESS_SYNC_STARTED,
    AUDIT_ACTION_CHUNK_ACCESS_SYNC_SUCCEEDED,
    AUDIT_ACTION_CHUNK_ACCESS_SYNC_FAILED,
    AUDIT_ACTION_CHUNK_ACCESS_SYNC_REPAIR_REQUIRED,
    AUDIT_ACTION_CHUNK_ACCESS_INITIALIZED,
    AUDIT_ACTION_CHUNK_ASSIGNMENT_UPSERTED,
    AUDIT_ACTION_CHUNK_ASSIGNMENT_REVOKED,
    AUDIT_ACTION_CHUNK_OWNER_TRANSFERRED,
    AUDIT_ACTION_RECONCILIATION_STARTED,
    AUDIT_ACTION_RECONCILIATION_COMPLETED,
    AUDIT_ACTION_RECONCILIATION_FAILED,
    AUDIT_ACTION_RECONCILIATION_SKIPPED,
}

AUDIT_ACTION_PREFIXES = (
    "project_",
    "member_",
    "membership_",
    "permission_",
    "invitation_",
    "publication_",
    "embed_",
    "workspace_",
    "service_",
    "chunk_",
    "reconciliation_",
    "identity_",
    "version_",
    "file_",
    "audit_",
)


# ─────────────────────────────────────────────────────────────
# Severity and status
# ─────────────────────────────────────────────────────────────

AUDIT_SEVERITY_INFO = "info"
AUDIT_SEVERITY_WARNING = "warning"
AUDIT_SEVERITY_ERROR = "error"
AUDIT_SEVERITY_CRITICAL = "critical"

AUDIT_SEVERITIES = {
    AUDIT_SEVERITY_INFO,
    AUDIT_SEVERITY_WARNING,
    AUDIT_SEVERITY_ERROR,
    AUDIT_SEVERITY_CRITICAL,
}

AUDIT_STATUS_RECORDED = "recorded"
AUDIT_STATUS_PENDING = "pending"
AUDIT_STATUS_SUCCEEDED = "succeeded"
AUDIT_STATUS_FAILED = "failed"
AUDIT_STATUS_SKIPPED = "skipped"
AUDIT_STATUS_REPAIR_REQUIRED = "repair_required"
AUDIT_STATUS_RECORD_FAILED = "record_failed"

AUDIT_STATUSES = {
    AUDIT_STATUS_RECORDED,
    AUDIT_STATUS_PENDING,
    AUDIT_STATUS_SUCCEEDED,
    AUDIT_STATUS_FAILED,
    AUDIT_STATUS_SKIPPED,
    AUDIT_STATUS_REPAIR_REQUIRED,
    AUDIT_STATUS_RECORD_FAILED,
}

AUDIT_SCHEMA_VERSION = 2
AUDIT_SOURCE_SERVICE = "vectoplan-app"


# ─────────────────────────────────────────────────────────────
# Data minimization and redaction
# ─────────────────────────────────────────────────────────────

AUDIT_SECRET_KEYS = {
    "authorization",
    "proxy_authorization",
    "cookie",
    "cookies",
    "set_cookie",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "token",
    "plain_token",
    "invitation_token",
    "token_hash",
    "jwt",
    "session",
    "session_id",
    "sessionid",
    "sid",
    "csrf",
    "csrf_token",
    "private_key",
    "credential",
    "credentials",
}

AUDIT_IDENTITY_KEYS = {
    "email",
    "auth_email",
    "auth_user_id",
    "authuserid",
    "canonical_user_id",
    "canonicaluserid",
    "external_user_id",
    "externaluserid",
    "user_id",
    "userid",
    "local_user_id",
    "localuserid",
    "actor_user_id",
    "actoruserid",
    "actor_auth_user_id",
    "actorauthuserid",
    "target_user_id",
    "targetuserid",
    "target_auth_user_id",
    "targetauthuserid",
    "account_id",
    "accountid",
}

AUDIT_BULK_KEYS = {
    "raw",
    "raw_auth",
    "binary",
    "blob",
    "content",
    "file_content",
    "geometry",
    "geometries",
    "features",
    "blocks",
    "chunks",
    "chunk_data",
    "vertices",
    "mesh",
    "meshes",
    "world_state",
    "snapshot_data",
}

AUDIT_MAX_DEPTH = 8
AUDIT_MAX_MAPPING_ITEMS = 200
AUDIT_MAX_SEQUENCE_ITEMS = 200
AUDIT_MAX_STRING_LENGTH = 8000

_RE_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_RE_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|access_token|refresh_token|api[_-]?key|secret|password|authorization|cookie|session(?:_id)?)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_RE_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,190}\.[A-Za-z]{2,63}")


# ─────────────────────────────────────────────────────────────
# Transitional model helpers
# ─────────────────────────────────────────────────────────────

def _metadata_has_table(table_name: str) -> bool:
    try:
        return str(table_name) in db.metadata.tables
    except Exception:
        return False


def _table_args(extend_existing: bool, *constraints: Any) -> Any:
    try:
        options = {"extend_existing": True} if extend_existing else {}

        if constraints:
            if options:
                return (*constraints, options)
            return constraints

        return options

    except Exception:
        return {"extend_existing": True} if extend_existing else {}


def _core_model_if_registered(model_name: str, table_name: str) -> Any:
    """
    Return the legacy core model while models/core.py still owns the table.

    Once core.py becomes an aggregator, this module defines the model itself.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None:
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
# Generic safe helpers
# ─────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    try:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return safe_bool(raw, default)
    except Exception:
        return default


def _sha256_text(value: Any) -> str:
    try:
        return hashlib.sha256(str(value if value is not None else "").encode("utf-8", errors="replace")).hexdigest()
    except Exception:
        return ""


def _is_sha256(value: Any) -> bool:
    try:
        text = safe_str(value, "", 80).lower()
        return bool(len(text) == 64 and all(char in "0123456789abcdef" for char in text))
    except Exception:
        return False


def _normalize_key(value: Any) -> str:
    try:
        return safe_slug(value, "", max_len=160).replace("-", "_").lower()
    except Exception:
        return ""


def _redact_url_query(text: str) -> str:
    try:
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return text

        clean_pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            normalized = _normalize_key(key)
            clean_pairs.append((key, "<redacted>") if normalized in AUDIT_SECRET_KEYS else (key, value))

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(clean_pairs, doseq=True),
                "",
            )
        )
    except Exception:
        return text


def redact_audit_text(
    value: Any,
    *,
    redact_identities: bool = False,
    max_len: int = AUDIT_MAX_STRING_LENGTH,
) -> str:
    try:
        text = safe_str(value, "", max_len * 2 if max_len else 0)
        if not text:
            return ""

        text = _redact_url_query(text)
        text = _RE_BEARER.sub(r"\1 <redacted>", text)
        text = _RE_SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)

        if redact_identities:
            text = _RE_EMAIL.sub("<redacted:email>", text)

        if max_len > 0 and len(text) > max_len:
            text = text[:max_len] + "…"

        return text
    except Exception:
        return ""


def _bulk_summary(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return {"redacted": True, "kind": "mapping", "items": len(value)}
        if isinstance(value, (list, tuple, set, frozenset)):
            return {"redacted": True, "kind": "sequence", "items": len(value)}
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"redacted": True, "kind": "binary", "bytes": len(value)}
        text = safe_str(value, "", 1000)
        return {"redacted": True, "kind": type(value).__name__, "length": len(text)}
    except Exception:
        return {"redacted": True, "kind": "unknown"}


def sanitize_audit_value(
    value: Any,
    *,
    redact_identities: bool = False,
    _depth: int = 0,
) -> Any:
    """
    Convert arbitrary values to bounded JSON-safe audit data.

    Secrets and bulk service-owned data are removed before persistence. Identity
    fields can optionally be redacted for non-private serialization.
    """
    try:
        if value is None or isinstance(value, (bool, int, float)):
            return value

        if isinstance(value, str):
            return redact_audit_text(value, redact_identities=redact_identities)

        if isinstance(value, (bytes, bytearray, memoryview)):
            return _bulk_summary(value)

        if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
            try:
                return value.isoformat()
            except Exception:
                return safe_str(value, "", 200)

        if _depth >= AUDIT_MAX_DEPTH:
            return {"truncated": True, "reason": "max_depth", "type": type(value).__name__}

        if isinstance(value, Mapping):
            result: Dict[str, Any] = {}
            items = list(value.items())
            for raw_key, item in items[:AUDIT_MAX_MAPPING_ITEMS]:
                key = safe_str(raw_key, "", 160)
                if not key:
                    continue

                normalized_key = _normalize_key(key)

                if normalized_key in AUDIT_SECRET_KEYS:
                    result[key] = "<redacted:secret>"
                    continue

                if normalized_key in AUDIT_BULK_KEYS:
                    result[key] = _bulk_summary(item)
                    continue

                if redact_identities and normalized_key in AUDIT_IDENTITY_KEYS:
                    result[key] = "<redacted:identity>"
                    continue

                result[key] = sanitize_audit_value(
                    item,
                    redact_identities=redact_identities,
                    _depth=_depth + 1,
                )

            if len(items) > AUDIT_MAX_MAPPING_ITEMS:
                result["_truncated_items"] = len(items) - AUDIT_MAX_MAPPING_ITEMS

            return result

        if isinstance(value, (list, tuple, set, frozenset)):
            sequence = list(value)
            result = [
                sanitize_audit_value(
                    item,
                    redact_identities=redact_identities,
                    _depth=_depth + 1,
                )
                for item in sequence[:AUDIT_MAX_SEQUENCE_ITEMS]
            ]
            if len(sequence) > AUDIT_MAX_SEQUENCE_ITEMS:
                result.append({"truncated_items": len(sequence) - AUDIT_MAX_SEQUENCE_ITEMS})
            return result

        if hasattr(value, "to_dict") and callable(value.to_dict):
            try:
                return sanitize_audit_value(
                    value.to_dict(),
                    redact_identities=redact_identities,
                    _depth=_depth + 1,
                )
            except Exception:
                pass

        if hasattr(value, "__dict__"):
            try:
                public_attrs = {
                    key: item
                    for key, item in vars(value).items()
                    if not str(key).startswith("_")
                }
                if public_attrs:
                    return sanitize_audit_value(
                        public_attrs,
                        redact_identities=redact_identities,
                        _depth=_depth + 1,
                    )
            except Exception:
                pass

        return redact_audit_text(value, redact_identities=redact_identities)

    except Exception:
        return "<unserializable>"


def sanitize_audit_mapping(
    value: Any,
    *,
    redact_identities: bool = False,
) -> Dict[str, Any]:
    try:
        sanitized = sanitize_audit_value(value, redact_identities=redact_identities)
        return dict(sanitized) if isinstance(sanitized, Mapping) else {}
    except Exception:
        return {}


def sanitize_audit_list(
    value: Any,
    *,
    redact_identities: bool = False,
) -> List[Any]:
    try:
        sanitized = sanitize_audit_value(value, redact_identities=redact_identities)
        return list(sanitized) if isinstance(sanitized, list) else []
    except Exception:
        return []


def _normalize_tags(value: Any) -> List[str]:
    try:
        result: List[str] = []
        for item in safe_list(value)[:64]:
            clean = safe_slug(item, "", max_len=80).replace("-", "_")
            if clean and clean not in result:
                result.append(clean)
        return result
    except Exception:
        return []


def _set_if_present(obj: Any, name: str, value: Any) -> bool:
    try:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return True
    except Exception:
        return False
    return False


def _getattr_any(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    try:
        for name in names:
            if isinstance(obj, Mapping) and name in obj:
                value = obj.get(name)
                if value not in (None, ""):
                    return value
            try:
                value = getattr(obj, name)
                if value not in (None, ""):
                    return value
            except Exception:
                continue
    except Exception:
        pass
    return default


# ─────────────────────────────────────────────────────────────
# Normalizers
# ─────────────────────────────────────────────────────────────

def normalize_audit_category(value: Any, default: str = AUDIT_CATEGORY_PROJECT) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "membership": AUDIT_CATEGORY_ACCESS,
            "member": AUDIT_CATEGORY_ACCESS,
            "permission": AUDIT_CATEGORY_ACCESS,
            "permissions": AUDIT_CATEGORY_ACCESS,
            "project_access": AUDIT_CATEGORY_ACCESS,
            "policy": AUDIT_CATEGORY_EMBED,
            "iframe": AUDIT_CATEGORY_EMBED,
            "service": AUDIT_CATEGORY_SERVICE_LINK,
            "link": AUDIT_CATEGORY_SERVICE_LINK,
            "service_ref": AUDIT_CATEGORY_SERVICE_LINK,
            "service_reference": AUDIT_CATEGORY_SERVICE_LINK,
            "revision": AUDIT_CATEGORY_VERSION,
            "snapshot": AUDIT_CATEGORY_VERSION,
            "blob": AUDIT_CATEGORY_FILE,
            "upload": AUDIT_CATEGORY_FILE,
            "editor": AUDIT_CATEGORY_WORKSPACE,
            "map": AUDIT_CATEGORY_WORKSPACE,
            "2d": AUDIT_CATEGORY_WORKSPACE,
            "cad2d": AUDIT_CATEGORY_WORKSPACE,
            "lv": AUDIT_CATEGORY_WORKSPACE,
            "chunk": AUDIT_CATEGORY_PROVISIONING,
            "chunk_provisioning": AUDIT_CATEGORY_PROVISIONING,
            "provision": AUDIT_CATEGORY_PROVISIONING,
            "sync": AUDIT_CATEGORY_ACCESS_SYNC,
            "chunk_access": AUDIT_CATEGORY_ACCESS_SYNC,
            "chunk_access_sync": AUDIT_CATEGORY_ACCESS_SYNC,
            "reconcile": AUDIT_CATEGORY_RECONCILIATION,
            "auth": AUDIT_CATEGORY_IDENTITY,
            "user_identity": AUDIT_CATEGORY_IDENTITY,
            "invite": AUDIT_CATEGORY_INVITATION,
            "invitations": AUDIT_CATEGORY_INVITATION,
        }

        normalized = aliases.get(text, text)
        return normalized if normalized in AUDIT_CATEGORIES else default

    except Exception:
        return default


def normalize_audit_action(
    value: Any,
    default: str = AUDIT_ACTION_UPDATED,
    *,
    allow_custom: Optional[bool] = None,
) -> str:
    try:
        text = safe_slug(value, default=default, max_len=120).replace("-", "_")

        aliases = {
            "create": AUDIT_ACTION_CREATED,
            "add": AUDIT_ACTION_CREATED,
            "new": AUDIT_ACTION_CREATED,
            "update": AUDIT_ACTION_UPDATED,
            "edit": AUDIT_ACTION_UPDATED,
            "change": AUDIT_ACTION_UPDATED,
            "patch": AUDIT_ACTION_UPDATED,
            "remove": AUDIT_ACTION_DELETED,
            "delete": AUDIT_ACTION_DELETED,
            "soft_delete": AUDIT_ACTION_DELETED,
            "restore": AUDIT_ACTION_RESTORED,
            "unarchive": AUDIT_ACTION_RESTORED,
            "archive": AUDIT_ACTION_ARCHIVED,
            "transfer": AUDIT_ACTION_TRANSFERRED,
            "ownership_transfer": AUDIT_ACTION_TRANSFERRED,
            "view": AUDIT_ACTION_VIEWED,
            "open": AUDIT_ACTION_OPENED,
            "download": AUDIT_ACTION_EXPORTED,
            "export": AUDIT_ACTION_EXPORTED,
            "upload": AUDIT_ACTION_IMPORTED,
            "import": AUDIT_ACTION_IMPORTED,
            "connect": AUDIT_ACTION_LINKED,
            "linked": AUDIT_ACTION_LINKED,
            "disconnect": AUDIT_ACTION_UNLINKED,
            "unlinked": AUDIT_ACTION_UNLINKED,
            "permission": AUDIT_ACTION_PERMISSION_CHANGED,
            "permissions": AUDIT_ACTION_PERMISSION_CHANGED,
            "role_changed": AUDIT_ACTION_PERMISSION_CHANGED,
            "embed": AUDIT_ACTION_EMBED_CHANGED,
            "embed_policy": AUDIT_ACTION_EMBED_CHANGED,
            "version": AUDIT_ACTION_VERSION_CREATED,
            "version_create": AUDIT_ACTION_VERSION_CREATED,
            "fail": AUDIT_ACTION_ERROR,
            "failed": AUDIT_ACTION_ERROR,
            "exception": AUDIT_ACTION_ERROR,
            "chunk_provision_failed": AUDIT_ACTION_CHUNK_PROVISIONING_FAILED,
            "chunk_project_linked": AUDIT_ACTION_CHUNK_PROJECT_LINKED,
        }

        normalized = aliases.get(text, text)
        if normalized in AUDIT_ACTIONS:
            return normalized

        if allow_custom is None:
            allow_custom = _env_bool("VECTOPLAN_AUDIT_ALLOW_CUSTOM_ACTIONS", True)

        if allow_custom and normalized and (
            normalized.startswith(AUDIT_ACTION_PREFIXES)
            or re.fullmatch(r"[a-z][a-z0-9_]{1,119}", normalized) is not None
        ):
            return normalized

        return default

    except Exception:
        return default


def infer_audit_category(action: Any, category: Any = None) -> str:
    try:
        explicit = normalize_audit_category(category, "") if category not in (None, "") else ""
        normalized_action = normalize_audit_action(action)

        inferred = AUDIT_CATEGORY_PROJECT
        if normalized_action.startswith("chunk_provision") or normalized_action == AUDIT_ACTION_CHUNK_PROJECT_LINKED:
            inferred = AUDIT_CATEGORY_PROVISIONING
        elif normalized_action.startswith("chunk_access") or normalized_action.startswith("chunk_assignment") or normalized_action == AUDIT_ACTION_CHUNK_OWNER_TRANSFERRED:
            inferred = AUDIT_CATEGORY_ACCESS_SYNC
        elif normalized_action.startswith("reconciliation_"):
            inferred = AUDIT_CATEGORY_RECONCILIATION
        elif normalized_action.startswith("invitation_"):
            inferred = AUDIT_CATEGORY_INVITATION
        elif normalized_action.startswith("identity_"):
            inferred = AUDIT_CATEGORY_IDENTITY
        elif normalized_action.startswith(("member_", "membership_", "permission_")):
            inferred = AUDIT_CATEGORY_ACCESS
        elif normalized_action.startswith("workspace_"):
            inferred = AUDIT_CATEGORY_WORKSPACE

        # The historical model defaulted category to "project". Treat that
        # default as unspecified when the action has a more precise category.
        if explicit and not (explicit == AUDIT_CATEGORY_PROJECT and inferred != AUDIT_CATEGORY_PROJECT):
            return explicit

        return inferred

    except Exception:
        return AUDIT_CATEGORY_PROJECT


def normalize_audit_severity(value: Any, default: str = AUDIT_SEVERITY_INFO) -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "warn": AUDIT_SEVERITY_WARNING,
            "warning": AUDIT_SEVERITY_WARNING,
            "err": AUDIT_SEVERITY_ERROR,
            "failed": AUDIT_SEVERITY_ERROR,
            "failure": AUDIT_SEVERITY_ERROR,
            "fatal": AUDIT_SEVERITY_CRITICAL,
        }

        normalized = aliases.get(text, text)
        return normalized if normalized in AUDIT_SEVERITIES else default

    except Exception:
        return default


def infer_audit_severity(
    action: Any,
    *,
    severity: Any = None,
    error: Any = None,
    repair_required: bool = False,
) -> str:
    try:
        explicit = normalize_audit_severity(severity, "") if severity not in (None, "") else ""
        normalized_action = normalize_audit_action(action)

        # Warning/error/critical are explicit operator decisions. Historical
        # default "info" must not suppress failure inference.
        if explicit in {AUDIT_SEVERITY_WARNING, AUDIT_SEVERITY_ERROR, AUDIT_SEVERITY_CRITICAL}:
            return explicit

        if error or normalized_action == AUDIT_ACTION_ERROR:
            return AUDIT_SEVERITY_ERROR
        if normalized_action.endswith("_failed"):
            return AUDIT_SEVERITY_ERROR
        if repair_required or normalized_action.endswith("_repair_required"):
            return AUDIT_SEVERITY_WARNING
        if normalized_action.endswith("_fallback"):
            return AUDIT_SEVERITY_WARNING

        return explicit or AUDIT_SEVERITY_INFO

    except Exception:
        return AUDIT_SEVERITY_INFO


def normalize_audit_status(value: Any, default: str = AUDIT_STATUS_RECORDED) -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")
        aliases = {
            "ok": AUDIT_STATUS_SUCCEEDED,
            "ready": AUDIT_STATUS_SUCCEEDED,
            "success": AUDIT_STATUS_SUCCEEDED,
            "complete": AUDIT_STATUS_SUCCEEDED,
            "completed": AUDIT_STATUS_SUCCEEDED,
            "error": AUDIT_STATUS_FAILED,
            "failure": AUDIT_STATUS_FAILED,
            "repair": AUDIT_STATUS_REPAIR_REQUIRED,
            "repairable": AUDIT_STATUS_REPAIR_REQUIRED,
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in AUDIT_STATUSES else default
    except Exception:
        return default


def infer_audit_status(
    action: Any,
    *,
    status: Any = None,
    error: Any = None,
    repair_required: bool = False,
) -> str:
    try:
        explicit = normalize_audit_status(status, "") if status not in (None, "") else ""
        normalized_action = normalize_audit_action(action)

        # A non-default status is authoritative. Historical default "recorded"
        # remains inferable from the action.
        if explicit and explicit != AUDIT_STATUS_RECORDED:
            return explicit

        if repair_required or normalized_action.endswith("_repair_required"):
            return AUDIT_STATUS_REPAIR_REQUIRED
        if error or normalized_action == AUDIT_ACTION_ERROR or normalized_action.endswith("_failed"):
            return AUDIT_STATUS_FAILED
        if normalized_action.endswith(("_pending", "_started")):
            return AUDIT_STATUS_PENDING
        if normalized_action.endswith("_skipped"):
            return AUDIT_STATUS_SKIPPED
        if normalized_action.endswith(("_succeeded", "_completed")):
            return AUDIT_STATUS_SUCCEEDED

        return explicit or AUDIT_STATUS_RECORDED

    except Exception:
        return AUDIT_STATUS_RECORDED


def normalize_actor_type(value: Any, default: str = "user") -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "human": "user",
            "person": "user",
            "system_user": "system",
            "service": "service",
            "api": "api",
            "automation": "automation",
            "job": "job",
            "script": "automation",
            "reconciliation": "automation",
        }

        normalized = aliases.get(text, text or default)
        return normalized if normalized in {"user", "system", "service", "api", "automation", "job"} else default

    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Request context
# ─────────────────────────────────────────────────────────────

def _origin_only(value: Any) -> str:
    try:
        text = safe_str(value, "", 2000)
        if not text:
            return ""
        parsed = urlsplit(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        return ""
    except Exception:
        return ""


def _path_without_query(value: Any) -> str:
    try:
        text = safe_str(value, "", 2000)
        if not text:
            return ""
        parsed = urlsplit(text)
        return parsed.path or text.split("?", 1)[0]
    except Exception:
        return ""


def _network_fingerprint(value: Any) -> str:
    try:
        text = safe_str(value, "", 300)
        if not text:
            return ""

        first = text.split(",", 1)[0].strip()
        try:
            first = str(ipaddress.ip_address(first))
        except Exception:
            pass

        return "sha256:" + _sha256_text(first)[:32]
    except Exception:
        return ""


def _session_fingerprint(value: Any) -> str:
    try:
        text = safe_str(value, "", 2000)
        return "sha256:" + _sha256_text(text)[:32] if text else ""
    except Exception:
        return ""


def normalize_request_context(value: Any) -> Dict[str, Any]:
    """
    Keep correlation and diagnostic metadata without raw cookies, sessions or IPs.
    """
    try:
        data = safe_dict(value)
        result: Dict[str, Any] = {}

        request_id = safe_str(
            data.get("request_id")
            or data.get("requestId")
            or data.get("x_request_id"),
            "",
            160,
        )
        correlation_id = safe_str(
            data.get("correlation_id")
            or data.get("correlationId")
            or data.get("x_correlation_id")
            or request_id,
            "",
            160,
        )
        trace_id = safe_str(
            data.get("trace_id")
            or data.get("traceId")
            or data.get("x_trace_id"),
            "",
            160,
        )

        if request_id:
            result["request_id"] = request_id
        if correlation_id:
            result["correlation_id"] = correlation_id
        if trace_id:
            result["trace_id"] = trace_id

        method = safe_str(data.get("method"), "", 20).upper()
        path = _path_without_query(data.get("path") or data.get("url") or data.get("full_path"))
        endpoint = safe_str(data.get("endpoint"), "", 240)

        if method:
            result["method"] = method
        if path:
            result["path"] = path
        if endpoint:
            result["endpoint"] = endpoint

        origin = _origin_only(data.get("origin"))
        referer_origin = _origin_only(data.get("referer") or data.get("referrer"))
        if origin:
            result["origin"] = origin
        if referer_origin:
            result["referer_origin"] = referer_origin

        user_agent = redact_audit_text(data.get("user_agent") or data.get("userAgent"), max_len=1000)
        if user_agent:
            result["user_agent"] = user_agent

        ip_fingerprint = safe_str(data.get("ip_fingerprint"), "", 80)
        if not ip_fingerprint:
            ip_fingerprint = _network_fingerprint(
                data.get("ip")
                or data.get("remote_addr")
                or data.get("remoteAddr")
                or data.get("x_forwarded_for")
            )
        if ip_fingerprint:
            result["ip_fingerprint"] = ip_fingerprint

        session_fingerprint = safe_str(data.get("session_fingerprint"), "", 80)
        if not session_fingerprint:
            session_value = (
                data.get("session_id")
                or data.get("sessionId")
                or data.get("session")
            )
            session_fingerprint = _session_fingerprint(session_value)
        if session_fingerprint:
            result["session_fingerprint"] = session_fingerprint

        allowed_extra = {
            "route",
            "blueprint",
            "request_source",
            "client_version",
            "service",
            "operation",
            "phase",
        }
        for key in allowed_extra:
            if key in data and data.get(key) not in (None, ""):
                result[key] = sanitize_audit_value(data.get(key))

        return sanitize_audit_mapping(result)

    except Exception:
        return {}


def build_request_context_from_flask() -> Dict[str, Any]:
    try:
        from flask import g, request

        request_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-VECTOPLAN-Request-ID")
            or getattr(g, "request_id", None)
        )
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-VECTOPLAN-Correlation-ID")
            or request_id
        )

        return normalize_request_context(
            {
                "request_id": request_id,
                "correlation_id": correlation_id,
                "trace_id": request.headers.get("X-Trace-ID"),
                "ip": request.headers.get("X-Forwarded-For") or request.remote_addr,
                "user_agent": request.headers.get("User-Agent"),
                "method": request.method,
                "path": request.path,
                "endpoint": request.endpoint,
                "origin": request.headers.get("Origin"),
                "referer": request.headers.get("Referer"),
                "blueprint": request.blueprint,
            }
        )

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# Fingerprinting
# ─────────────────────────────────────────────────────────────

def build_audit_event_fingerprint(event_or_payload: Any) -> str:
    try:
        if isinstance(event_or_payload, Mapping):
            source = dict(event_or_payload)
        else:
            source = {
                "event_id": getattr(event_or_payload, "event_id", None),
                "project_id": getattr(event_or_payload, "project_id", None),
                "project_public_id": getattr(event_or_payload, "project_public_id", None),
                "category": getattr(event_or_payload, "category", None),
                "action": getattr(event_or_payload, "action", None),
                "service": getattr(event_or_payload, "service", None),
                "resource_type": getattr(event_or_payload, "resource_type", None),
                "resource_id": getattr(event_or_payload, "resource_id", None),
                "operation": getattr(event_or_payload, "operation", None),
                "phase": getattr(event_or_payload, "phase", None),
                "request_id": getattr(event_or_payload, "request_id", None),
                "correlation_id": getattr(event_or_payload, "correlation_id", None),
                "idempotency_key_hash": getattr(event_or_payload, "idempotency_key_hash", None),
                "attempt": getattr(event_or_payload, "attempt", None),
            }

        stable = sanitize_audit_mapping(source, redact_identities=True)
        text = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
        return _sha256_text(text)
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# ProjectAuditEvent model
# ─────────────────────────────────────────────────────────────

def _define_project_audit_event_model(*, extend_existing: bool = False):
    class ProjectAuditEvent(TimestampMixin, SerializationMixin, db.Model):
        """
        Append-only audit event for app-owned project operations.

        The model records facts and references only. It deliberately bounds and
        redacts payloads so geometry, world state, chunks, secrets and raw auth
        material cannot become an accidental audit-data store.
        """

        __tablename__ = "project_audit_events"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        event_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("aud"),
        )
        schema_version = db.Column(db.Integer, nullable=False, default=AUDIT_SCHEMA_VERSION, index=True)
        event_fingerprint = db.Column(db.String(64), nullable=True, index=True)
        parent_event_id = db.Column(db.String(120), nullable=True, index=True)

        project_id = db.Column(db.Integer, nullable=True, index=True)
        project_public_id = db.Column(db.String(120), nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)

        category = db.Column(db.String(80), nullable=False, default=AUDIT_CATEGORY_PROJECT, index=True)
        action = db.Column(db.String(120), nullable=False, default=AUDIT_ACTION_UPDATED, index=True)
        severity = db.Column(db.String(40), nullable=False, default=AUDIT_SEVERITY_INFO, index=True)
        status = db.Column(db.String(40), nullable=False, default=AUDIT_STATUS_RECORDED, index=True)

        source_service = db.Column(db.String(120), nullable=False, default=AUDIT_SOURCE_SERVICE, index=True)
        service = db.Column(db.String(120), nullable=True, index=True)
        resource_type = db.Column(db.String(120), nullable=True, index=True)
        resource_id = db.Column(db.String(255), nullable=True, index=True)

        operation = db.Column(db.String(120), nullable=True, index=True)
        phase = db.Column(db.String(80), nullable=True, index=True)
        attempt = db.Column(db.Integer, nullable=False, default=0)

        actor_user_id = db.Column(db.Integer, nullable=True, index=True)
        actor_auth_user_id = db.Column(db.String(160), nullable=True, index=True)
        actor_account_id = db.Column(db.String(160), nullable=True, index=True)
        actor_type = db.Column(db.String(40), nullable=False, default="user", index=True)
        actor_label = db.Column(db.String(255), nullable=True)

        target_type = db.Column(db.String(120), nullable=True, index=True)
        target_id = db.Column(db.String(255), nullable=True, index=True)
        target_auth_user_id = db.Column(db.String(160), nullable=True, index=True)
        target_label = db.Column(db.String(255), nullable=True)

        message = db.Column(db.Text, nullable=True)

        before = db.Column(json_type(), nullable=True)
        after = db.Column(json_type(), nullable=True)
        changes = db.Column(json_type(), nullable=False, default=dict)
        payload = db.Column(json_type(), nullable=False, default=dict)
        result = db.Column(json_type(), nullable=True)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        request_context = db.Column(json_type(), nullable=False, default=dict)
        request_id = db.Column(db.String(160), nullable=True, index=True)
        correlation_id = db.Column(db.String(160), nullable=True, index=True)
        trace_id = db.Column(db.String(160), nullable=True, index=True)

        # Legacy column names retained. Values are fingerprints, never raw data.
        session_id = db.Column(db.String(255), nullable=True, index=True)
        ip_address = db.Column(db.String(160), nullable=True, index=True)
        user_agent = db.Column(db.Text, nullable=True)

        idempotency_key_hash = db.Column(db.String(64), nullable=True, index=True)
        remote_status_code = db.Column(db.Integer, nullable=True, index=True)
        retryable = db.Column(db.Boolean, nullable=False, default=False)
        repair_required = db.Column(db.Boolean, nullable=False, default=False)

        tags = db.Column(json_type(), nullable=False, default=list)
        error = db.Column(db.Text, nullable=True)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectAuditEvent event_id={self.event_id!r} "
                    f"project_id={self.project_id!r} action={self.action!r} "
                    f"status={self.status!r}>"
                )
            except Exception:
                return "<ProjectAuditEvent>"

        @property
        def is_error(self) -> bool:
            try:
                return bool(
                    self.severity in {AUDIT_SEVERITY_ERROR, AUDIT_SEVERITY_CRITICAL}
                    or self.action == AUDIT_ACTION_ERROR
                    or self.status in {AUDIT_STATUS_FAILED, AUDIT_STATUS_RECORD_FAILED}
                    or self.error
                )
            except Exception:
                return False

        @property
        def actor(self) -> Dict[str, Any]:
            try:
                return {
                    "user_id": self.actor_user_id,
                    "type": self.actor_type,
                    "label": self.actor_label,
                }
            except Exception:
                return {}

        @property
        def target(self) -> Dict[str, Any]:
            try:
                return {
                    "type": self.target_type,
                    "id": self.target_id,
                    "label": self.target_label,
                }
            except Exception:
                return {}

        @property
        def resource(self) -> Dict[str, Any]:
            try:
                return {
                    "service": self.service,
                    "resource_type": self.resource_type,
                    "resource_id": self.resource_id,
                }
            except Exception:
                return {}

        def normalize(self) -> "ProjectAuditEvent":
            try:
                if not self.event_id:
                    self.event_id = public_id("aud")

                self.schema_version = safe_int(self.schema_version, AUDIT_SCHEMA_VERSION, minimum=1, maximum=100)
                self.parent_event_id = safe_str(self.parent_event_id, "", 120) or None

                self.project_id = safe_int(self.project_id, 0) or None
                self.project_public_id = safe_str(self.project_public_id, "", 120) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None

                self.action = normalize_audit_action(self.action, AUDIT_ACTION_UPDATED)
                self.category = infer_audit_category(self.action, self.category)

                self.repair_required = safe_bool(self.repair_required, False)
                self.error = redact_audit_text(self.error, max_len=8000) or None
                self.severity = infer_audit_severity(
                    self.action,
                    severity=self.severity,
                    error=self.error,
                    repair_required=self.repair_required,
                )
                self.status = infer_audit_status(
                    self.action,
                    status=self.status,
                    error=self.error,
                    repair_required=self.repair_required,
                )

                self.source_service = safe_slug(self.source_service, AUDIT_SOURCE_SERVICE, 120) or AUDIT_SOURCE_SERVICE
                self.service = safe_slug(self.service, "", 120) or None
                self.resource_type = safe_slug(self.resource_type, "", 120) or None
                self.resource_id = safe_str(self.resource_id, "", 255) or None

                self.operation = safe_slug(self.operation, "", 120) or None
                self.phase = safe_slug(self.phase, "", 80) or None
                self.attempt = safe_int(self.attempt, 0, minimum=0, maximum=1_000_000)

                self.actor_user_id = safe_int(self.actor_user_id, 0) or None
                self.actor_auth_user_id = safe_str(self.actor_auth_user_id, "", 160) or None
                self.actor_account_id = safe_str(self.actor_account_id, "", 160) or None
                self.actor_type = normalize_actor_type(self.actor_type, "user")
                self.actor_label = redact_audit_text(self.actor_label, redact_identities=True, max_len=255) or None

                self.target_type = safe_str(self.target_type, "", 120) or None
                self.target_id = safe_str(self.target_id, "", 255) or None
                self.target_auth_user_id = safe_str(self.target_auth_user_id, "", 160) or None
                self.target_label = redact_audit_text(self.target_label, redact_identities=True, max_len=255) or None

                self.message = redact_audit_text(self.message, redact_identities=True, max_len=8000) or None

                self.before = sanitize_audit_mapping(self.before, redact_identities=True) if self.before is not None else None
                self.after = sanitize_audit_mapping(self.after, redact_identities=True) if self.after is not None else None
                self.changes = sanitize_audit_mapping(self.changes, redact_identities=True)
                self.payload = sanitize_audit_mapping(self.payload, redact_identities=True)
                self.result = sanitize_audit_mapping(self.result, redact_identities=True) if self.result is not None else None
                self.metadata_json = sanitize_audit_mapping(self.metadata_json, redact_identities=True)

                self.request_context = normalize_request_context(self.request_context)
                self.request_id = safe_str(
                    self.request_id or self.request_context.get("request_id"),
                    "",
                    160,
                ) or None
                self.correlation_id = safe_str(
                    self.correlation_id
                    or self.request_context.get("correlation_id")
                    or self.request_id,
                    "",
                    160,
                ) or None
                self.trace_id = safe_str(
                    self.trace_id or self.request_context.get("trace_id"),
                    "",
                    160,
                ) or None

                raw_session = self.session_id or self.request_context.get("session_fingerprint")
                self.session_id = (
                    safe_str(raw_session, "", 255)
                    if safe_str(raw_session, "", 255).startswith("sha256:")
                    else _session_fingerprint(raw_session)
                ) or None

                raw_ip = self.ip_address or self.request_context.get("ip_fingerprint")
                self.ip_address = (
                    safe_str(raw_ip, "", 160)
                    if safe_str(raw_ip, "", 160).startswith("sha256:")
                    else _network_fingerprint(raw_ip)
                ) or None

                self.user_agent = redact_audit_text(
                    self.user_agent or self.request_context.get("user_agent"),
                    max_len=2000,
                ) or None

                idempotency_value = safe_str(self.idempotency_key_hash, "", 1000)
                if idempotency_value:
                    self.idempotency_key_hash = (
                        idempotency_value.lower()
                        if _is_sha256(idempotency_value)
                        else _sha256_text(idempotency_value)
                    )
                else:
                    self.idempotency_key_hash = None

                remote_status = safe_int(self.remote_status_code, 0, minimum=0, maximum=999)
                self.remote_status_code = remote_status or None
                self.retryable = safe_bool(self.retryable, False)

                self.tags = _normalize_tags(self.tags)

                self.event_fingerprint = build_audit_event_fingerprint(self) or None
                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> "ProjectAuditEvent":
            try:
                data = safe_dict(payload)

                field_map = {
                    "event_id": "event_id",
                    "eventId": "event_id",
                    "schema_version": "schema_version",
                    "schemaVersion": "schema_version",
                    "parent_event_id": "parent_event_id",
                    "parentEventId": "parent_event_id",
                    "project_id": "project_id",
                    "projectId": "project_id",
                    "project_public_id": "project_public_id",
                    "projectPublicId": "project_public_id",
                    "conversation_id": "conversation_id",
                    "conversationId": "conversation_id",
                    "chat_id": "conversation_id",
                    "chatId": "conversation_id",
                    "category": "category",
                    "action": "action",
                    "severity": "severity",
                    "status": "status",
                    "source_service": "source_service",
                    "sourceService": "source_service",
                    "service": "service",
                    "resource_type": "resource_type",
                    "resourceType": "resource_type",
                    "resource_id": "resource_id",
                    "resourceId": "resource_id",
                    "operation": "operation",
                    "phase": "phase",
                    "attempt": "attempt",
                    "actor_user_id": "actor_user_id",
                    "actorUserId": "actor_user_id",
                    "user_id": "actor_user_id",
                    "userId": "actor_user_id",
                    "actor_auth_user_id": "actor_auth_user_id",
                    "actorAuthUserId": "actor_auth_user_id",
                    "actor_account_id": "actor_account_id",
                    "actorAccountId": "actor_account_id",
                    "actor_type": "actor_type",
                    "actorType": "actor_type",
                    "actor_label": "actor_label",
                    "actorLabel": "actor_label",
                    "target_type": "target_type",
                    "targetType": "target_type",
                    "target_id": "target_id",
                    "targetId": "target_id",
                    "target_auth_user_id": "target_auth_user_id",
                    "targetAuthUserId": "target_auth_user_id",
                    "target_label": "target_label",
                    "targetLabel": "target_label",
                    "message": "message",
                    "request_id": "request_id",
                    "requestId": "request_id",
                    "correlation_id": "correlation_id",
                    "correlationId": "correlation_id",
                    "trace_id": "trace_id",
                    "traceId": "trace_id",
                    "session_id": "session_id",
                    "sessionId": "session_id",
                    "ip_address": "ip_address",
                    "ipAddress": "ip_address",
                    "user_agent": "user_agent",
                    "userAgent": "user_agent",
                    "idempotency_key": "idempotency_key_hash",
                    "idempotencyKey": "idempotency_key_hash",
                    "idempotency_key_hash": "idempotency_key_hash",
                    "idempotencyKeyHash": "idempotency_key_hash",
                    "remote_status_code": "remote_status_code",
                    "remoteStatusCode": "remote_status_code",
                    "retryable": "retryable",
                    "repair_required": "repair_required",
                    "repairRequired": "repair_required",
                    "error": "error",
                }

                for source_key, target_key in field_map.items():
                    if source_key in data:
                        setattr(self, target_key, data.get(source_key))

                if "before" in data:
                    self.before = sanitize_audit_mapping(data.get("before"), redact_identities=True)

                if "after" in data:
                    self.after = sanitize_audit_mapping(data.get("after"), redact_identities=True)

                if "changes" in data or "diff" in data:
                    self.changes = sanitize_audit_mapping(data.get("changes") or data.get("diff"), redact_identities=True)

                if "payload" in data:
                    self.payload = sanitize_audit_mapping(data.get("payload"), redact_identities=True)

                if "result" in data:
                    self.result = sanitize_audit_mapping(data.get("result"), redact_identities=True)

                if "request_context" in data or "requestContext" in data:
                    self.request_context = normalize_request_context(
                        data.get("request_context") or data.get("requestContext")
                    )

                if "tags" in data:
                    self.tags = _normalize_tags(data.get("tags"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = sanitize_audit_mapping(data.get("metadata") or data.get("meta"), redact_identities=True)

                self.normalize()
                self.touch()
                return self

            except Exception:
                return self

        def mark_error(
            self,
            error: Any,
            *,
            severity: str = AUDIT_SEVERITY_ERROR,
            repair_required: bool = False,
            retryable: bool = False,
        ) -> None:
            try:
                self.error = redact_audit_text(error, redact_identities=True, max_len=8000) or "unknown error"
                self.action = AUDIT_ACTION_ERROR
                self.severity = normalize_audit_severity(severity, AUDIT_SEVERITY_ERROR)
                self.status = AUDIT_STATUS_REPAIR_REQUIRED if repair_required else AUDIT_STATUS_FAILED
                self.repair_required = bool(repair_required)
                self.retryable = bool(retryable)
                self.normalize()
                self.touch()
            except Exception:
                pass

        def add_tag(self, tag: Any) -> None:
            try:
                clean = safe_slug(tag, "", max_len=80).replace("-", "_")
                if not clean:
                    return

                tags = _normalize_tags(self.tags)
                if clean not in tags:
                    tags.append(clean)
                    self.tags = tags[:64]
                    self.touch()
            except Exception:
                pass

        def to_dict(
            self,
            *,
            include_private: bool = False,
            include_payload: bool = True,
            include_diff: bool = True,
            include_request: bool = False,
        ) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "event_id": self.event_id,
                    "eventId": self.event_id,
                    "schema_version": getattr(self, "schema_version", AUDIT_SCHEMA_VERSION),
                    "schemaVersion": getattr(self, "schema_version", AUDIT_SCHEMA_VERSION),
                    "event_fingerprint": getattr(self, "event_fingerprint", None),
                    "eventFingerprint": getattr(self, "event_fingerprint", None),
                    "parent_event_id": getattr(self, "parent_event_id", None),
                    "parentEventId": getattr(self, "parent_event_id", None),
                    "project_id": self.project_id,
                    "projectId": self.project_id,
                    "project_public_id": self.project_public_id,
                    "projectPublicId": self.project_public_id,
                    "conversation_id": self.conversation_id,
                    "conversationId": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "category": self.category,
                    "action": self.action,
                    "severity": self.severity,
                    "status": self.status,
                    "source_service": getattr(self, "source_service", AUDIT_SOURCE_SERVICE),
                    "sourceService": getattr(self, "source_service", AUDIT_SOURCE_SERVICE),
                    "operation": getattr(self, "operation", None),
                    "phase": getattr(self, "phase", None),
                    "attempt": getattr(self, "attempt", 0),
                    "actor": self.actor,
                    "actor_user_id": self.actor_user_id,
                    "actorUserId": self.actor_user_id,
                    "actor_type": self.actor_type,
                    "actorType": self.actor_type,
                    "actor_label": self.actor_label,
                    "actorLabel": self.actor_label,
                    "target": self.target,
                    "target_type": self.target_type,
                    "targetType": self.target_type,
                    "target_id": self.target_id,
                    "targetId": self.target_id,
                    "target_label": self.target_label,
                    "targetLabel": self.target_label,
                    "resource": self.resource,
                    "service": self.service,
                    "resource_type": self.resource_type,
                    "resourceType": self.resource_type,
                    "resource_id": self.resource_id,
                    "resourceId": self.resource_id,
                    "message": self.message or "",
                    "remote_status_code": getattr(self, "remote_status_code", None),
                    "remoteStatusCode": getattr(self, "remote_status_code", None),
                    "retryable": bool(getattr(self, "retryable", False)),
                    "repair_required": bool(getattr(self, "repair_required", False)),
                    "repairRequired": bool(getattr(self, "repair_required", False)),
                    "is_error": self.is_error,
                    "isError": self.is_error,
                    "error": redact_audit_text(self.error, redact_identities=not include_private, max_len=8000) or None,
                    "tags": _normalize_tags(self.tags),
                    "created_at": isoformat(self.created_at),
                    "createdAt": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "updatedAt": isoformat(self.updated_at),
                }

                if include_diff:
                    payload["before"] = sanitize_audit_mapping(self.before, redact_identities=not include_private)
                    payload["after"] = sanitize_audit_mapping(self.after, redact_identities=not include_private)
                    payload["changes"] = sanitize_audit_mapping(self.changes, redact_identities=not include_private)

                if include_payload:
                    payload["payload"] = sanitize_audit_mapping(self.payload, redact_identities=not include_private)
                    payload["result"] = sanitize_audit_mapping(self.result, redact_identities=not include_private)

                if include_request or include_private:
                    request_context = normalize_request_context(self.request_context)
                    if not include_private:
                        request_context.pop("ip_fingerprint", None)
                        request_context.pop("session_fingerprint", None)
                        request_context.pop("user_agent", None)
                        request_context.pop("origin", None)
                        request_context.pop("referer_origin", None)

                    payload["request_context"] = request_context
                    payload["requestContext"] = request_context
                    payload["request_id"] = self.request_id
                    payload["requestId"] = self.request_id
                    payload["correlation_id"] = getattr(self, "correlation_id", None)
                    payload["correlationId"] = getattr(self, "correlation_id", None)
                    payload["trace_id"] = self.trace_id
                    payload["traceId"] = self.trace_id

                if include_private:
                    payload["actor_auth_user_id"] = getattr(self, "actor_auth_user_id", None)
                    payload["actorAuthUserId"] = getattr(self, "actor_auth_user_id", None)
                    payload["actor_account_id"] = getattr(self, "actor_account_id", None)
                    payload["actorAccountId"] = getattr(self, "actor_account_id", None)
                    payload["target_auth_user_id"] = getattr(self, "target_auth_user_id", None)
                    payload["targetAuthUserId"] = getattr(self, "target_auth_user_id", None)
                    payload["idempotency_key_hash"] = getattr(self, "idempotency_key_hash", None)
                    payload["idempotencyKeyHash"] = getattr(self, "idempotency_key_hash", None)
                    payload["session_fingerprint"] = getattr(self, "session_id", None)
                    payload["sessionFingerprint"] = getattr(self, "session_id", None)
                    payload["ip_fingerprint"] = getattr(self, "ip_address", None)
                    payload["ipFingerprint"] = getattr(self, "ip_address", None)
                    payload["user_agent"] = self.user_agent
                    payload["userAgent"] = self.user_agent
                    payload["metadata"] = sanitize_audit_mapping(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "event_id": getattr(self, "event_id", None),
                    "project_id": getattr(self, "project_id", None),
                    "action": getattr(self, "action", None),
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any = None,
            project_public_id: Any = "",
            conversation_id: Any = "",
            category: Any = None,
            action: Any = AUDIT_ACTION_UPDATED,
            severity: Any = None,
            status: Any = None,
            source_service: Any = AUDIT_SOURCE_SERVICE,
            service: Any = "",
            resource_type: Any = "",
            resource_id: Any = "",
            operation: Any = "",
            phase: Any = "",
            attempt: Any = 0,
            actor_user_id: Optional[int] = None,
            actor_auth_user_id: Any = "",
            actor_account_id: Any = "",
            actor_type: str = "user",
            actor_label: str = "",
            target_type: str = "",
            target_id: Any = "",
            target_auth_user_id: Any = "",
            target_label: str = "",
            message: str = "",
            before: Optional[Mapping[str, Any]] = None,
            after: Optional[Mapping[str, Any]] = None,
            changes: Optional[Mapping[str, Any]] = None,
            payload: Optional[Mapping[str, Any]] = None,
            result: Optional[Mapping[str, Any]] = None,
            request_context: Optional[Mapping[str, Any]] = None,
            request_id: Any = "",
            correlation_id: Any = "",
            trace_id: Any = "",
            idempotency_key: Any = "",
            parent_event_id: Any = "",
            remote_status_code: Any = None,
            retryable: Any = False,
            repair_required: Any = False,
            tags: Optional[List[Any]] = None,
            metadata: Optional[Mapping[str, Any]] = None,
            error: Any = None,
        ) -> "ProjectAuditEvent":
            event = cls()
            event.project_id = safe_int(project_id, 0) or None
            event.project_public_id = safe_str(project_public_id, "", 120) or None
            event.conversation_id = safe_str(conversation_id, "", 80) or None

            event.action = normalize_audit_action(action)
            event.category = infer_audit_category(event.action, category)
            event.severity = infer_audit_severity(
                event.action,
                severity=severity,
                error=error,
                repair_required=safe_bool(repair_required, False),
            )
            event.status = infer_audit_status(
                event.action,
                status=status,
                error=error,
                repair_required=safe_bool(repair_required, False),
            )

            event.source_service = safe_slug(source_service, AUDIT_SOURCE_SERVICE, 120)
            event.service = safe_slug(service, "", 120) or None
            event.resource_type = safe_slug(resource_type, "", 120) or None
            event.resource_id = safe_str(resource_id, "", 255) or None
            event.operation = safe_slug(operation, "", 120) or None
            event.phase = safe_slug(phase, "", 80) or None
            event.attempt = safe_int(attempt, 0, minimum=0, maximum=1_000_000)

            event.actor_user_id = safe_int(actor_user_id, 0) or None
            event.actor_auth_user_id = safe_str(actor_auth_user_id, "", 160) or None
            event.actor_account_id = safe_str(actor_account_id, "", 160) or None
            event.actor_type = normalize_actor_type(actor_type, "user")
            event.actor_label = redact_audit_text(actor_label, redact_identities=True, max_len=255) or None

            event.target_type = safe_str(target_type, "", 120) or None
            event.target_id = safe_str(target_id, "", 255) or None
            event.target_auth_user_id = safe_str(target_auth_user_id, "", 160) or None
            event.target_label = redact_audit_text(target_label, redact_identities=True, max_len=255) or None

            event.message = redact_audit_text(message, redact_identities=True, max_len=8000) or None
            event.error = redact_audit_text(error, redact_identities=True, max_len=8000) or None

            event.before = sanitize_audit_mapping(before, redact_identities=True) if before is not None else None
            event.after = sanitize_audit_mapping(after, redact_identities=True) if after is not None else None
            event.changes = sanitize_audit_mapping(changes, redact_identities=True)
            event.payload = sanitize_audit_mapping(payload, redact_identities=True)
            event.result = sanitize_audit_mapping(result, redact_identities=True) if result is not None else None
            event.metadata_json = sanitize_audit_mapping(metadata, redact_identities=True)

            event.request_context = normalize_request_context(request_context or build_request_context_from_flask())
            event.request_id = safe_str(request_id, "", 160) or event.request_context.get("request_id") or None
            event.correlation_id = (
                safe_str(correlation_id, "", 160)
                or event.request_context.get("correlation_id")
                or event.request_id
                or None
            )
            event.trace_id = safe_str(trace_id, "", 160) or event.request_context.get("trace_id") or None

            event.idempotency_key_hash = _sha256_text(idempotency_key) if idempotency_key else None
            event.parent_event_id = safe_str(parent_event_id, "", 120) or None
            event.remote_status_code = safe_int(remote_status_code, 0, minimum=0, maximum=999) or None
            event.retryable = safe_bool(retryable, False)
            event.repair_required = safe_bool(repair_required, False)
            event.tags = _normalize_tags(tags)
            event.schema_version = AUDIT_SCHEMA_VERSION

            event.normalize()
            return event

    return ProjectAuditEvent


ProjectAuditEvent = _resolve_model(
    "ProjectAuditEvent",
    "project_audit_events",
    _define_project_audit_event_model,
)


def _normalize_audit_model_before_write(mapper: Any, connection: Any, target: Any) -> None:
    try:
        if hasattr(target, "normalize") and callable(target.normalize):
            target.normalize()
    except Exception:
        # Audit normalization must never abort the caller's business operation.
        pass


def _register_audit_model_listeners(model: Any) -> bool:
    try:
        if _sa_event is None or model is None:
            return False

        if bool(getattr(model, "_vectoplan_audit_listeners_registered", False)):
            return True

        _sa_event.listen(model, "before_insert", _normalize_audit_model_before_write, propagate=True)
        _sa_event.listen(model, "before_update", _normalize_audit_model_before_write, propagate=True)
        setattr(model, "_vectoplan_audit_listeners_registered", True)
        return True
    except Exception:
        return False


_register_audit_model_listeners(ProjectAuditEvent)


# ─────────────────────────────────────────────────────────────
# Compatibility-aware builders
# ─────────────────────────────────────────────────────────────

def _filter_supported_kwargs(callable_obj: Any, values: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        signature = inspect.signature(callable_obj)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return dict(values)
        return {
            key: value
            for key, value in values.items()
            if key in signature.parameters
        }
    except Exception:
        return dict(values)


def _apply_event_values(event: Any, values: Mapping[str, Any]) -> Any:
    try:
        data = dict(values)
        aliases = {
            "metadata": "metadata_json",
            "idempotency_key": "idempotency_key_hash",
        }

        mapping_fields = {"before", "after", "changes", "payload", "result", "metadata_json"}
        identity_text_fields = {"actor_label", "target_label", "message", "error"}

        for key, value in data.items():
            target = aliases.get(key, key)

            if target == "idempotency_key_hash" and value:
                value = _sha256_text(value) if not _is_sha256(value) else safe_str(value, "", 64)
            elif target in mapping_fields:
                if target in {"before", "after", "result"} and value is None:
                    value = None
                else:
                    value = sanitize_audit_mapping(value, redact_identities=True)
            elif target == "request_context":
                value = normalize_request_context(value)
            elif target == "tags":
                value = _normalize_tags(value)
            elif target in identity_text_fields:
                value = redact_audit_text(value, redact_identities=True)

            _set_if_present(event, target, value)

        if hasattr(event, "normalize") and callable(event.normalize):
            event.normalize()
        return event
    except Exception:
        return event


def build_audit_event(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    category: Any = None,
    action: Any = AUDIT_ACTION_UPDATED,
    severity: Any = None,
    status: Any = None,
    source_service: Any = AUDIT_SOURCE_SERVICE,
    service: Any = "",
    resource_type: Any = "",
    resource_id: Any = "",
    operation: Any = "",
    phase: Any = "",
    attempt: Any = 0,
    actor_user_id: Optional[int] = None,
    actor_auth_user_id: Any = "",
    actor_account_id: Any = "",
    actor_type: str = "user",
    actor_label: str = "",
    target_type: str = "",
    target_id: Any = "",
    target_auth_user_id: Any = "",
    target_label: str = "",
    message: str = "",
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    changes: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    request_context: Optional[Mapping[str, Any]] = None,
    request_id: Any = "",
    correlation_id: Any = "",
    trace_id: Any = "",
    idempotency_key: Any = "",
    parent_event_id: Any = "",
    remote_status_code: Any = None,
    retryable: Any = False,
    repair_required: Any = False,
    tags: Optional[List[Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    error: Any = None,
) -> ProjectAuditEvent:
    values = {
        "project_id": project_id,
        "project_public_id": project_public_id,
        "conversation_id": conversation_id,
        "category": category,
        "action": action,
        "severity": severity,
        "status": status,
        "source_service": source_service,
        "service": service,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "operation": operation,
        "phase": phase,
        "attempt": attempt,
        "actor_user_id": actor_user_id,
        "actor_auth_user_id": actor_auth_user_id,
        "actor_account_id": actor_account_id,
        "actor_type": actor_type,
        "actor_label": actor_label,
        "target_type": target_type,
        "target_id": target_id,
        "target_auth_user_id": target_auth_user_id,
        "target_label": target_label,
        "message": message,
        "before": before,
        "after": after,
        "changes": changes,
        "payload": payload,
        "result": result,
        "request_context": request_context,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
        "idempotency_key": idempotency_key,
        "parent_event_id": parent_event_id,
        "remote_status_code": remote_status_code,
        "retryable": retryable,
        "repair_required": repair_required,
        "tags": tags,
        "metadata": metadata,
        "error": error,
    }

    try:
        if hasattr(ProjectAuditEvent, "build") and callable(ProjectAuditEvent.build):
            supported = _filter_supported_kwargs(ProjectAuditEvent.build, values)
            event = ProjectAuditEvent.build(**supported)
            return _apply_event_values(event, values)

        event = ProjectAuditEvent()
        return _apply_event_values(event, values)

    except Exception:
        event = ProjectAuditEvent()
        try:
            _set_if_present(event, "project_id", safe_int(project_id, 0) or None)
            _set_if_present(event, "project_public_id", safe_str(project_public_id, "", 120) or None)
            _set_if_present(event, "action", normalize_audit_action(action))
            _set_if_present(event, "category", infer_audit_category(action, category))
            _set_if_present(event, "message", redact_audit_text(message, redact_identities=True))
        except Exception:
            pass
        return event


# ─────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────

def record_audit_event(
    *,
    commit: bool = True,
    raise_on_error: bool = False,
    rollback_on_error: Optional[bool] = None,
    **kwargs: Any,
) -> ProjectAuditEvent:
    """
    Persist an audit event without making audit availability a business-operation
    dependency by default.

    With commit=False a nested transaction/savepoint is used when available, so
    an audit failure does not roll back the caller's entire unit of work.
    """
    event = build_audit_event(**kwargs)
    nested = None

    try:
        if not commit and hasattr(db.session, "begin_nested"):
            try:
                nested = db.session.begin_nested()
            except Exception:
                nested = None

        db.session.add(event)

        # Without savepoint support, defer the flush to the caller's transaction.
        # This prevents a non-critical audit flush from poisoning or rolling back
        # the caller's complete business operation.
        if not commit and nested is None:
            return event

        db.session.flush()

        if nested is not None:
            try:
                nested.commit()
            except Exception:
                pass

        if commit:
            db.session.commit()

        return event

    except Exception as exc:
        try:
            if nested is not None:
                nested.rollback()
            elif rollback_on_error is True or (rollback_on_error is None and commit):
                db.session.rollback()
        except Exception:
            pass

        try:
            _set_if_present(event, "status", AUDIT_STATUS_RECORD_FAILED)
            _set_if_present(event, "error", redact_audit_text(exc, redact_identities=True, max_len=8000))
            if hasattr(event, "normalize") and callable(event.normalize):
                event.normalize()
        except Exception:
            pass

        if raise_on_error:
            raise

        return event


def record_project_audit_event(
    project: Any,
    *,
    action: Any,
    category: Any = None,
    actor_user_id: Optional[int] = None,
    actor_auth_user_id: Any = "",
    actor_account_id: Any = "",
    message: str = "",
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    changes: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    try:
        return record_audit_event(
            project_id=getattr(project, "id", None),
            project_public_id=getattr(project, "public_id", ""),
            conversation_id=getattr(project, "conversation_id", ""),
            category=category,
            action=action,
            actor_user_id=actor_user_id,
            actor_auth_user_id=actor_auth_user_id,
            actor_account_id=actor_account_id,
            message=message,
            before=before,
            after=after,
            changes=changes,
            payload=payload,
            commit=commit,
            **kwargs,
        )

    except Exception:
        return record_audit_event(
            category=category,
            action=action,
            actor_user_id=actor_user_id,
            actor_auth_user_id=actor_auth_user_id,
            actor_account_id=actor_account_id,
            message=message,
            payload=payload,
            commit=commit,
            **kwargs,
        )


def record_chunk_provisioning_audit_event(
    project: Any,
    *,
    action: Any,
    actor_user_id: Optional[int] = None,
    actor_auth_user_id: Any = "",
    request_id: Any = "",
    idempotency_key: Any = "",
    result: Optional[Mapping[str, Any]] = None,
    error: Any = None,
    retryable: bool = False,
    repair_required: bool = False,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    return record_project_audit_event(
        project,
        action=action,
        category=AUDIT_CATEGORY_PROVISIONING,
        actor_user_id=actor_user_id,
        actor_auth_user_id=actor_auth_user_id,
        service="chunk",
        operation="project_provisioning",
        request_id=request_id,
        idempotency_key=idempotency_key,
        result=result,
        error=error,
        retryable=retryable,
        repair_required=repair_required,
        commit=commit,
        **kwargs,
    )


def record_chunk_access_sync_audit_event(
    project: Any,
    *,
    action: Any,
    actor_user_id: Optional[int] = None,
    actor_auth_user_id: Any = "",
    target_auth_user_id: Any = "",
    request_id: Any = "",
    idempotency_key: Any = "",
    result: Optional[Mapping[str, Any]] = None,
    error: Any = None,
    retryable: bool = False,
    repair_required: bool = False,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    return record_project_audit_event(
        project,
        action=action,
        category=AUDIT_CATEGORY_ACCESS_SYNC,
        actor_user_id=actor_user_id,
        actor_auth_user_id=actor_auth_user_id,
        target_auth_user_id=target_auth_user_id,
        service="chunk",
        operation="project_access_sync",
        request_id=request_id,
        idempotency_key=idempotency_key,
        result=result,
        error=error,
        retryable=retryable,
        repair_required=repair_required,
        commit=commit,
        **kwargs,
    )


def record_reconciliation_audit_event(
    project: Any,
    *,
    action: Any,
    request_id: Any = "",
    result: Optional[Mapping[str, Any]] = None,
    error: Any = None,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    return record_project_audit_event(
        project,
        action=action,
        category=AUDIT_CATEGORY_RECONCILIATION,
        actor_type="automation",
        source_service=AUDIT_SOURCE_SERVICE,
        service="chunk",
        operation="chunk_reconciliation",
        request_id=request_id,
        result=result,
        error=error,
        commit=commit,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────

def get_audit_event_by_id(event_ref: Any) -> Optional[ProjectAuditEvent]:
    try:
        value = safe_str(event_ref, "", 180)
        if not value:
            return None

        numeric_id = safe_int(value, 0)
        if numeric_id:
            item = ProjectAuditEvent.query.get(numeric_id)
            if item is not None:
                return item

        return ProjectAuditEvent.query.filter_by(event_id=value).one_or_none()

    except Exception:
        return None


def _parse_datetime(value: Any) -> Optional[_dt.datetime]:
    try:
        if isinstance(value, _dt.datetime):
            return value
        text = safe_str(value, "", 100)
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(normalized)
    except Exception:
        return None


def list_project_audit_events(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    category: Any = "",
    action: Any = "",
    severity: Any = "",
    status: Any = "",
    service: Any = "",
    actor_user_id: Any = None,
    actor_auth_user_id: Any = "",
    request_id: Any = "",
    correlation_id: Any = "",
    repair_required: Optional[bool] = None,
    include_errors_only: bool = False,
    created_after: Any = None,
    created_before: Any = None,
    limit: int = 200,
) -> List[ProjectAuditEvent]:
    try:
        query = ProjectAuditEvent.query

        resolved_project_id = safe_int(project_id, 0)
        resolved_project_public_id = safe_str(project_public_id, "", 120)
        resolved_conversation_id = safe_str(conversation_id, "", 80)

        if resolved_project_id:
            query = query.filter_by(project_id=resolved_project_id)
        if resolved_project_public_id:
            query = query.filter_by(project_public_id=resolved_project_public_id)
        if resolved_conversation_id:
            query = query.filter_by(conversation_id=resolved_conversation_id)
        if category:
            query = query.filter_by(category=normalize_audit_category(category))
        if action:
            query = query.filter_by(action=normalize_audit_action(action))
        if severity:
            query = query.filter_by(severity=normalize_audit_severity(severity))
        if status:
            query = query.filter_by(status=normalize_audit_status(status))
        if service:
            query = query.filter_by(service=safe_slug(service, "", 120))

        resolved_actor_user_id = safe_int(actor_user_id, 0)
        if resolved_actor_user_id:
            query = query.filter_by(actor_user_id=resolved_actor_user_id)

        resolved_actor_auth_user_id = safe_str(actor_auth_user_id, "", 160)
        if resolved_actor_auth_user_id and hasattr(ProjectAuditEvent, "actor_auth_user_id"):
            query = query.filter_by(actor_auth_user_id=resolved_actor_auth_user_id)

        resolved_request_id = safe_str(request_id, "", 160)
        if resolved_request_id:
            query = query.filter_by(request_id=resolved_request_id)

        resolved_correlation_id = safe_str(correlation_id, "", 160)
        if resolved_correlation_id and hasattr(ProjectAuditEvent, "correlation_id"):
            query = query.filter_by(correlation_id=resolved_correlation_id)

        if repair_required is not None and hasattr(ProjectAuditEvent, "repair_required"):
            query = query.filter_by(repair_required=bool(repair_required))

        if include_errors_only:
            query = query.filter(
                ProjectAuditEvent.severity.in_(
                    [AUDIT_SEVERITY_ERROR, AUDIT_SEVERITY_CRITICAL]
                )
            )

        after_dt = _parse_datetime(created_after)
        before_dt = _parse_datetime(created_before)
        if after_dt is not None:
            query = query.filter(ProjectAuditEvent.created_at >= after_dt)
        if before_dt is not None:
            query = query.filter(ProjectAuditEvent.created_at < before_dt)

        return list(
            query.order_by(ProjectAuditEvent.created_at.desc(), ProjectAuditEvent.id.desc())
            .limit(safe_int(limit, 200, minimum=1, maximum=2000))
            .all()
        )

    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Serialization helpers
# ─────────────────────────────────────────────────────────────

def serialize_audit_event(
    event: Any,
    *,
    include_private: bool = False,
    include_payload: bool = True,
    include_diff: bool = True,
    include_request: bool = False,
) -> Dict[str, Any]:
    try:
        if event is None:
            return {}

        if hasattr(event, "to_dict"):
            try:
                return event.to_dict(
                    include_private=include_private,
                    include_payload=include_payload,
                    include_diff=include_diff,
                    include_request=include_request,
                )
            except TypeError:
                raw = event.to_dict()
                return sanitize_audit_mapping(raw, redact_identities=not include_private)

        return {
            "id": getattr(event, "id", None),
            "event_id": getattr(event, "event_id", None),
            "project_id": getattr(event, "project_id", None),
            "project_public_id": getattr(event, "project_public_id", None),
            "category": getattr(event, "category", None),
            "action": getattr(event, "action", None),
            "severity": getattr(event, "severity", None),
            "status": getattr(event, "status", None),
            "message": redact_audit_text(
                getattr(event, "message", None),
                redact_identities=not include_private,
            ),
            "created_at": isoformat(getattr(event, "created_at", None)),
        }

    except Exception:
        return {}


def serialize_audit_events(events: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_audit_event(item, **kwargs)
            for item in list(events or [])
            if item is not None
        ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_project_audit_model_classes() -> List[Any]:
    return [ProjectAuditEvent]


def get_project_audit_model_status() -> Dict[str, Any]:
    try:
        count = -1
        try:
            count = int(ProjectAuditEvent.query.count())
        except Exception:
            count = -1

        return {
            "ok": True,
            "models": ["ProjectAuditEvent"],
            "tables": [getattr(ProjectAuditEvent, "__tablename__", "project_audit_events")],
            "count": count,
            "schema_version": AUDIT_SCHEMA_VERSION,
            "categories": sorted(AUDIT_CATEGORIES),
            "actions": sorted(AUDIT_ACTIONS),
            "custom_actions_allowed": _env_bool("VECTOPLAN_AUDIT_ALLOW_CUSTOM_ACTIONS", True),
            "severities": sorted(AUDIT_SEVERITIES),
            "statuses": sorted(AUDIT_STATUSES),
            "rules": {
                "append_only": True,
                "secrets_removed_before_persistence": True,
                "bulk_service_data_bounded": True,
                "raw_cookie_stored": False,
                "raw_session_id_stored": False,
                "raw_ip_stored": False,
                "session_fingerprint_only": True,
                "ip_fingerprint_only": True,
                "actor_auth_user_id_private": True,
                "idempotency_key_hashed": True,
                "audit_failure_blocks_business_operation": False,
                "commit_false_uses_savepoint_when_available": True,
                "sqlalchemy_normalize_listener_registered": bool(
                    getattr(ProjectAuditEvent, "_vectoplan_audit_listeners_registered", False)
                ),
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectAuditEvent"],
            "tables": ["project_audit_events"],
            "schema_version": AUDIT_SCHEMA_VERSION,
            "error": redact_audit_text(exc, redact_identities=True),
        }


__all__ = [
    "AUDIT_CATEGORY_PROJECT",
    "AUDIT_CATEGORY_ACCESS",
    "AUDIT_CATEGORY_EMBED",
    "AUDIT_CATEGORY_SERVICE_LINK",
    "AUDIT_CATEGORY_VERSION",
    "AUDIT_CATEGORY_FILE",
    "AUDIT_CATEGORY_WORKSPACE",
    "AUDIT_CATEGORY_SYSTEM",
    "AUDIT_CATEGORY_PROVISIONING",
    "AUDIT_CATEGORY_ACCESS_SYNC",
    "AUDIT_CATEGORY_RECONCILIATION",
    "AUDIT_CATEGORY_IDENTITY",
    "AUDIT_CATEGORY_INVITATION",
    "AUDIT_CATEGORIES",
    "AUDIT_ACTION_CREATED",
    "AUDIT_ACTION_UPDATED",
    "AUDIT_ACTION_DELETED",
    "AUDIT_ACTION_RESTORED",
    "AUDIT_ACTION_ARCHIVED",
    "AUDIT_ACTION_TRANSFERRED",
    "AUDIT_ACTION_VIEWED",
    "AUDIT_ACTION_OPENED",
    "AUDIT_ACTION_EXPORTED",
    "AUDIT_ACTION_IMPORTED",
    "AUDIT_ACTION_LINKED",
    "AUDIT_ACTION_UNLINKED",
    "AUDIT_ACTION_PERMISSION_CHANGED",
    "AUDIT_ACTION_EMBED_CHANGED",
    "AUDIT_ACTION_VERSION_CREATED",
    "AUDIT_ACTION_ERROR",
    "AUDIT_ACTION_MEMBER_REMOVED",
    "AUDIT_ACTION_INVITATION_CREATED",
    "AUDIT_ACTION_INVITATION_DISPATCHED",
    "AUDIT_ACTION_INVITATION_FAILED",
    "AUDIT_ACTION_INVITATION_REVOKED",
    "AUDIT_ACTION_INVITATION_REJECTED",
    "AUDIT_ACTION_INVITATION_ACCEPTED",
    "AUDIT_ACTION_INVITATION_EXPIRED",
    "AUDIT_ACTION_CHUNK_PROVISIONING_PENDING",
    "AUDIT_ACTION_CHUNK_PROVISIONING_STARTED",
    "AUDIT_ACTION_CHUNK_PROVISIONING_SUCCEEDED",
    "AUDIT_ACTION_CHUNK_PROVISIONING_FALLBACK",
    "AUDIT_ACTION_CHUNK_PROVISIONING_FAILED",
    "AUDIT_ACTION_CHUNK_PROVISIONING_REPAIR_REQUIRED",
    "AUDIT_ACTION_CHUNK_PROVISIONING_DISABLED",
    "AUDIT_ACTION_CHUNK_PROJECT_LINKED",
    "AUDIT_ACTION_CHUNK_ACCESS_SYNC_PENDING",
    "AUDIT_ACTION_CHUNK_ACCESS_SYNC_STARTED",
    "AUDIT_ACTION_CHUNK_ACCESS_SYNC_SUCCEEDED",
    "AUDIT_ACTION_CHUNK_ACCESS_SYNC_FAILED",
    "AUDIT_ACTION_CHUNK_ACCESS_SYNC_REPAIR_REQUIRED",
    "AUDIT_ACTION_CHUNK_ACCESS_INITIALIZED",
    "AUDIT_ACTION_CHUNK_ASSIGNMENT_UPSERTED",
    "AUDIT_ACTION_CHUNK_ASSIGNMENT_REVOKED",
    "AUDIT_ACTION_CHUNK_OWNER_TRANSFERRED",
    "AUDIT_ACTION_RECONCILIATION_STARTED",
    "AUDIT_ACTION_RECONCILIATION_COMPLETED",
    "AUDIT_ACTION_RECONCILIATION_FAILED",
    "AUDIT_ACTION_RECONCILIATION_SKIPPED",
    "AUDIT_ACTIONS",
    "AUDIT_SEVERITY_INFO",
    "AUDIT_SEVERITY_WARNING",
    "AUDIT_SEVERITY_ERROR",
    "AUDIT_SEVERITY_CRITICAL",
    "AUDIT_SEVERITIES",
    "AUDIT_STATUS_RECORDED",
    "AUDIT_STATUS_PENDING",
    "AUDIT_STATUS_SUCCEEDED",
    "AUDIT_STATUS_FAILED",
    "AUDIT_STATUS_SKIPPED",
    "AUDIT_STATUS_REPAIR_REQUIRED",
    "AUDIT_STATUS_RECORD_FAILED",
    "AUDIT_STATUSES",
    "AUDIT_SCHEMA_VERSION",
    "ProjectAuditEvent",
    "normalize_audit_category",
    "normalize_audit_action",
    "normalize_audit_severity",
    "normalize_audit_status",
    "normalize_actor_type",
    "infer_audit_category",
    "infer_audit_severity",
    "infer_audit_status",
    "normalize_request_context",
    "build_request_context_from_flask",
    "redact_audit_text",
    "sanitize_audit_value",
    "sanitize_audit_mapping",
    "sanitize_audit_list",
    "build_audit_event_fingerprint",
    "build_audit_event",
    "record_audit_event",
    "record_project_audit_event",
    "record_chunk_provisioning_audit_event",
    "record_chunk_access_sync_audit_event",
    "record_reconciliation_audit_event",
    "get_audit_event_by_id",
    "list_project_audit_events",
    "serialize_audit_event",
    "serialize_audit_events",
    "get_project_audit_model_classes",
    "get_project_audit_model_status",
]
