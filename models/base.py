# services/vectoplan-app/models/base.py
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy.dialects.postgresql import JSONB

from extensions import db


# ─────────────────────────────────────────────────────────────
# SQLAlchemy / JSON helpers
# ─────────────────────────────────────────────────────────────

def json_type() -> Any:
    """
    Return a portable JSON column type.

    PostgreSQL gets JSONB. Other engines fall back to db.JSON.
    This helper is intentionally callable so model modules do not have to know
    which database engine is active.
    """
    try:
        bind = db.get_engine()
        dialect_name = str(getattr(bind, "dialect", None).name or "").lower()

        if dialect_name == "postgresql":
            return JSONB

        return db.JSON

    except Exception:
        try:
            return db.JSON
        except Exception:
            return JSONB


def _json_type() -> Any:
    """
    Backwards-compatible alias used by the previous monolithic core.py.
    """
    return json_type()


# ─────────────────────────────────────────────────────────────
# Time / ID helpers
# ─────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    """
    Return timezone-naive UTC datetime for compatibility with existing columns.

    SQLAlchemy DateTime columns in this app have historically stored naive UTC.
    Keep that behavior stable.
    """
    try:
        return datetime.utcnow()
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _utcnow() -> datetime:
    return utcnow()


def make_uuid() -> str:
    try:
        return str(uuid.uuid4())
    except Exception:
        return uuid.uuid4().hex


def _uuid() -> str:
    return make_uuid()


def public_id(prefix: str = "id") -> str:
    """
    Build a compact public identifier.

    Examples:
      prj_...
      usr_...
      ver_...
    """
    try:
        clean_prefix = safe_slug(prefix or "id", default="id", max_len=24)
        token = uuid.uuid4().hex[:24]
        return f"{clean_prefix}_{token}"
    except Exception:
        return f"id_{uuid.uuid4().hex[:24]}"


def _public_id(prefix: str = "id") -> str:
    return public_id(prefix)


def project_public_id() -> str:
    return public_id("prj")


def _project_public_id() -> str:
    return project_public_id()


def version_public_id() -> str:
    return public_id("ver")


def _version_public_id() -> str:
    return version_public_id()


# ─────────────────────────────────────────────────────────────
# Safe conversion helpers
# ─────────────────────────────────────────────────────────────

def safe_str(value: Any, default: str = "", max_len: int = 1000) -> str:
    try:
        if value is None:
            text = str(default or "")
        elif isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)

        text = text.strip()

        if not text:
            text = str(default or "").strip()

        if max_len and max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        try:
            return str(default or "")[:max_len] if max_len and max_len > 0 else str(default or "")
        except Exception:
            return ""


def _safe_str(value: Any, default: str = "", max_len: int = 1000) -> str:
    return safe_str(value, default=default, max_len=max_len)


def safe_slug(value: Any, default: str = "item", max_len: int = 80) -> str:
    try:
        import re

        text = safe_str(value, default=default, max_len=max_len * 2).lower()
        text = re.sub(r"[^a-z0-9_-]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_-")

        if not text:
            text = safe_str(default, "item", max_len=max_len)

        if max_len and max_len > 0:
            text = text[:max_len].strip("_-")

        return text or "item"

    except Exception:
        return safe_str(default, "item", max_len=max_len) or "item"


def _safe_slug(value: Any, default: str = "item", max_len: int = 80) -> str:
    return safe_slug(value, default=default, max_len=max_len)


def safe_int(value: Any, default: int = 0, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        if isinstance(value, bool):
            result = default
        elif value is None or value == "":
            result = default
        else:
            result = int(value)

        if minimum is not None and result < minimum:
            return int(minimum)

        if maximum is not None and result > maximum:
            return int(maximum)

        return int(result)

    except Exception:
        return int(default)


def _safe_int(value: Any, default: int = 0, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    return safe_int(value, default=default, minimum=minimum, maximum=maximum)


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return default

        if value is None or value == "":
            return default

        result = float(value)

        if result != result:
            return default

        if result in {float("inf"), float("-inf")}:
            return default

        return result

    except Exception:
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    return safe_float(value, default=default)


def safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            if value == 1:
                return True
            if value == 0:
                return False

        text = safe_str(value, "", max_len=32).lower()

        if text in {"1", "true", "t", "yes", "y", "on", "ja"}:
            return True

        if text in {"0", "false", "f", "no", "n", "off", "nein"}:
            return False

        return bool(default)

    except Exception:
        return bool(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    return safe_bool(value, default=default)


def safe_dict(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)

        if isinstance(value, str) and value.strip():
            import json

            parsed = json.loads(value)
            if isinstance(parsed, Mapping):
                return dict(parsed)

        return dict(default or {})

    except Exception:
        return dict(default or {})


def _safe_dict(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return safe_dict(value, default=default)


def safe_list(value: Any, default: Optional[Sequence[Any]] = None) -> List[Any]:
    try:
        if value is None:
            return list(default or [])

        if isinstance(value, list):
            return list(value)

        if isinstance(value, tuple):
            return list(value)

        if isinstance(value, set):
            return list(value)

        if isinstance(value, str) and value.strip():
            import json

            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return list(parsed)
            except Exception:
                return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]

        return list(default or [])

    except Exception:
        return list(default or [])


def _safe_list(value: Any, default: Optional[Sequence[Any]] = None) -> List[Any]:
    return safe_list(value, default=default)


def isoformat(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.isoformat()

        if hasattr(value, "isoformat"):
            return value.isoformat()

        return safe_str(value, "", max_len=80) or None

    except Exception:
        return None


def _iso(value: Any) -> Optional[str]:
    return isoformat(value)


def deep_copy_json(value: Any, fallback: Optional[Any] = None) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        try:
            import json

            return json.loads(json.dumps(value))
        except Exception:
            return copy.deepcopy(fallback) if fallback is not None else None


def merge_dicts(base: Optional[Mapping[str, Any]], patch: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Recursive merge for JSON-like dictionaries.
    """
    try:
        result = safe_dict(base)

        if not isinstance(patch, Mapping):
            return result

        for key, value in patch.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = merge_dicts(result.get(key), value)
            else:
                result[key] = deep_copy_json(value)

        return result

    except Exception:
        merged = safe_dict(base)
        try:
            merged.update(safe_dict(patch))
        except Exception:
            pass
        return merged


def _deep_merge_state(base: Optional[Mapping[str, Any]], patch: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return merge_dicts(base, patch)


# ─────────────────────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────────────────────

def normalize_status(value: Any, default: str = "active") -> str:
    try:
        text = safe_slug(value, default=default, max_len=40)

        aliases = {
            "enabled": "active",
            "ok": "active",
            "live": "active",
            "inactive": "disabled",
            "off": "disabled",
            "removed": "deleted",
            "soft_deleted": "deleted",
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def _normalize_status(value: Any, default: str = "active") -> str:
    return normalize_status(value, default=default)


def normalize_visibility(value: Any, default: str = "private") -> str:
    try:
        text = safe_slug(value, default=default, max_len=40)

        if text in {"public", "published", "open"}:
            return "public"

        if text in {"team", "workspace", "shared"}:
            return "shared"

        return "private"

    except Exception:
        return default


def _normalize_visibility(value: Any, default: str = "private") -> str:
    return normalize_visibility(value, default=default)


def normalize_project_role(value: Any, default: str = "viewer") -> str:
    try:
        text = safe_slug(value, default=default, max_len=40)

        aliases = {
            "owner": "owner",
            "admin": "admin",
            "administrator": "admin",
            "manager": "admin",
            "manage": "admin",
            "editor": "editor",
            "edit": "editor",
            "writer": "editor",
            "member": "editor",
            "viewer": "viewer",
            "view": "viewer",
            "reader": "viewer",
            "readonly": "viewer",
            "read_only": "viewer",
        }

        return aliases.get(text, default)

    except Exception:
        return default


def _normalize_project_role(value: Any, default: str = "viewer") -> str:
    return normalize_project_role(value, default=default)


def role_permission_defaults(role: Any) -> Dict[str, bool]:
    normalized = normalize_project_role(role, "viewer")

    defaults = {
        "owner": {
            "view": True,
            "edit": True,
            "manage": True,
            "delete": True,
            "transfer": True,
            "embed": True,
        },
        "admin": {
            "view": True,
            "edit": True,
            "manage": True,
            "delete": False,
            "transfer": False,
            "embed": True,
        },
        "editor": {
            "view": True,
            "edit": True,
            "manage": False,
            "delete": False,
            "transfer": False,
            "embed": True,
        },
        "viewer": {
            "view": True,
            "edit": False,
            "manage": False,
            "delete": False,
            "transfer": False,
            "embed": False,
        },
    }

    return dict(defaults.get(normalized, defaults["viewer"]))


def _role_permission_defaults(role: Any) -> Dict[str, bool]:
    return role_permission_defaults(role)


def normalize_role(value: Any, default: str = "viewer") -> str:
    return normalize_project_role(value, default=default)


def _role(value: Any, default: str = "viewer") -> str:
    return normalize_project_role(value, default=default)


# ─────────────────────────────────────────────────────────────
# Legacy viewer-selection helpers
# ─────────────────────────────────────────────────────────────

def legacy_backend_prefix() -> str:
    return "legacy_"


def _legacy_backend_prefix() -> str:
    return legacy_backend_prefix()


def is_legacy_viewer_key(key: Any) -> bool:
    try:
        text = safe_str(key, "", max_len=120).lower()

        return (
            text.startswith("speckle")
            or text.startswith("legacy_speckle")
            or text.startswith("legacy_3d_backend")
            or text in {"project_id", "model_id", "version_id", "viewer_url"}
        )

    except Exception:
        return False


def _is_legacy_viewer_key(key: Any) -> bool:
    return is_legacy_viewer_key(key)


def sanitize_viewer_selection(value: Any) -> Dict[str, Any]:
    """
    Remove legacy viewer backend identifiers from old selection blobs.

    App project/chunk references are allowed elsewhere, but this helper is for
    old viewer-selection payloads and intentionally strips Speckle-like fields.
    """
    try:
        source = safe_dict(value)
        result: Dict[str, Any] = {}

        for key, item in source.items():
            if is_legacy_viewer_key(key):
                continue
            result[str(key)] = deep_copy_json(item)

        result["legacy_speckle"] = False
        result["legacy_3d_backend"] = False

        return result

    except Exception:
        return {
            "legacy_speckle": False,
            "legacy_3d_backend": False,
        }


def _sanitize_viewer_selection(value: Any) -> Dict[str, Any]:
    return sanitize_viewer_selection(value)


# ─────────────────────────────────────────────────────────────
# SQLAlchemy mixins
# ─────────────────────────────────────────────────────────────

class TimestampMixin:
    """
    Shared created/updated timestamps.
    """

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True)

    def touch(self) -> None:
        try:
            self.updated_at = utcnow()
        except Exception:
            pass


class SoftDeleteMixin:
    """
    Shared soft-delete fields.
    """

    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by_user_id = db.Column(db.Integer, nullable=True, index=True)
    delete_reason = db.Column(db.Text, nullable=True)

    @property
    def is_deleted(self) -> bool:
        try:
            return self.deleted_at is not None
        except Exception:
            return False

    def mark_deleted(self, *, user_id: Optional[int] = None, reason: str = "") -> None:
        try:
            self.deleted_at = utcnow()
            self.deleted_by_user_id = safe_int(user_id, 0) or None
            self.delete_reason = safe_str(reason, "", max_len=2000) or None

            if hasattr(self, "status"):
                try:
                    self.status = "deleted"
                except Exception:
                    pass

            if hasattr(self, "touch"):
                try:
                    self.touch()
                except Exception:
                    pass

        except Exception:
            pass

    def restore_deleted(self) -> None:
        try:
            self.deleted_at = None
            self.deleted_by_user_id = None
            self.delete_reason = None

            if hasattr(self, "status"):
                try:
                    self.status = "active"
                except Exception:
                    pass

            if hasattr(self, "touch"):
                try:
                    self.touch()
                except Exception:
                    pass

        except Exception:
            pass


class SerializationMixin:
    """
    Lightweight serialization helper for model classes.

    Concrete models may override to_dict where needed.
    """

    __serialize_exclude__: Sequence[str] = ()

    def to_dict(self, *, include_private: bool = False) -> Dict[str, Any]:
        try:
            result: Dict[str, Any] = {}
            exclude = set(getattr(self, "__serialize_exclude__", ()) or ())

            for column in getattr(self, "__table__", []).columns:
                try:
                    key = str(column.name)

                    if key in exclude:
                        continue

                    if not include_private and key.startswith("_"):
                        continue

                    value = getattr(self, key, None)

                    if isinstance(value, datetime):
                        result[key] = isoformat(value)
                    else:
                        result[key] = deep_copy_json(value, fallback=value)

                except Exception:
                    continue

            return result

        except Exception:
            return {}


# ─────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────

__all__ = [
    "db",
    "JSONB",
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
    "normalize_role",
    "_role",
    "role_permission_defaults",
    "_role_permission_defaults",
    "legacy_backend_prefix",
    "_legacy_backend_prefix",
    "is_legacy_viewer_key",
    "_is_legacy_viewer_key",
    "sanitize_viewer_selection",
    "_sanitize_viewer_selection",
    "TimestampMixin",
    "SoftDeleteMixin",
    "SerializationMixin",
]