# services/vectoplan-app/models/users.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

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
    safe_slug,
    safe_str,
    utcnow,
)


DEFAULT_USER_ID = 1
DEFAULT_USER_PUBLIC_ID = "u_demo_1"
DEFAULT_USER_DISPLAY_NAME = "Demo User"
DEFAULT_USER_ROLE = "user"


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        from flask import current_app

        current_app.logger.warning(message, *args, **kwargs)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        from flask import current_app

        if exc is None:
            current_app.logger.exception(message)
        else:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


def _config_value(key: str, default: Any = None) -> Any:
    try:
        from flask import current_app

        value = current_app.config.get(key)
        if value is not None and value != "":
            return value
    except Exception:
        pass

    try:
        value = os.environ.get(key)
        if value is not None and str(value).strip() != "":
            return value
    except Exception:
        pass

    return default


def get_default_user_id(default: int = DEFAULT_USER_ID) -> int:
    try:
        configured = _config_value("VECTOPLAN_DEFAULT_USER_ID", default)
        return safe_int(configured, default, minimum=1)
    except Exception:
        return default


def get_default_user_public_id(user_id: Optional[int] = None) -> str:
    try:
        configured = safe_str(_config_value("VECTOPLAN_DEFAULT_USER_PUBLIC_ID", ""), "", 120)
        if configured:
            return configured

        resolved_user_id = safe_int(user_id, DEFAULT_USER_ID, minimum=1)
        if resolved_user_id == DEFAULT_USER_ID:
            return DEFAULT_USER_PUBLIC_ID

        return f"u_demo_{resolved_user_id}"

    except Exception:
        return DEFAULT_USER_PUBLIC_ID


def get_default_user_display_name() -> str:
    try:
        return safe_str(
            _config_value("VECTOPLAN_DEFAULT_USER_DISPLAY_NAME", DEFAULT_USER_DISPLAY_NAME),
            DEFAULT_USER_DISPLAY_NAME,
            240,
        )
    except Exception:
        return DEFAULT_USER_DISPLAY_NAME


def normalize_user_role(value: Any, default: str = DEFAULT_USER_ROLE) -> str:
    try:
        role = safe_slug(value, default=default, max_len=40)

        aliases = {
            "administrator": "admin",
            "owner": "admin",
            "manager": "admin",
            "default": "user",
            "member": "user",
            "reader": "viewer",
            "readonly": "viewer",
            "read_only": "viewer",
        }

        return aliases.get(role, role or default)

    except Exception:
        return default


def _set_if_available(instance: Any, field: str, value: Any) -> None:
    try:
        if hasattr(instance, field):
            setattr(instance, field, value)
    except Exception:
        pass


def _get_attr(instance: Any, field: str, default: Any = None) -> Any:
    try:
        if instance is None:
            return default
        return getattr(instance, field, default)
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Transitional model definition
# ─────────────────────────────────────────────────────────────

def _define_app_user_model(*, extend_existing: bool = False):
    table_args = {"extend_existing": True} if extend_existing else {}

    class AppUser(TimestampMixin, SerializationMixin, db.Model):
        """
        Application user model.

        Current transition state:
        - Authentication is not implemented yet.
        - The app uses placeholder user id=1.
        - The table is still future-proof for real users, roles and profile data.
        """

        __tablename__ = "app_users"
        __table_args__ = table_args

        id = db.Column(db.Integer, primary_key=True)

        public_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("usr"),
        )

        email = db.Column(db.String(255), unique=True, nullable=True, index=True)
        handle = db.Column(db.String(120), unique=True, nullable=True, index=True)

        display_name = db.Column(db.String(255), nullable=False, default=DEFAULT_USER_DISPLAY_NAME)
        first_name = db.Column(db.String(120), nullable=True)
        last_name = db.Column(db.String(120), nullable=True)

        role = db.Column(db.String(40), nullable=False, default=DEFAULT_USER_ROLE, index=True)

        locale = db.Column(db.String(32), nullable=True)
        timezone = db.Column(db.String(80), nullable=True)
        avatar_url = db.Column(db.Text, nullable=True)

        is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
        is_placeholder = db.Column(db.Boolean, nullable=False, default=False, index=True)
        is_system = db.Column(db.Boolean, nullable=False, default=False, index=True)

        last_seen_at = db.Column(db.DateTime, nullable=True, index=True)
        disabled_at = db.Column(db.DateTime, nullable=True)
        disabled_reason = db.Column(db.Text, nullable=True)

        settings = db.Column(json_type(), nullable=False, default=dict)
        profile = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        __serialize_exclude__ = ()

        def __repr__(self) -> str:
            try:
                return f"<AppUser id={self.id!r} public_id={self.public_id!r} display_name={self.display_name!r}>"
            except Exception:
                return "<AppUser>"

        @property
        def user_id(self) -> int:
            try:
                return int(self.id)
            except Exception:
                return DEFAULT_USER_ID

        @property
        def name(self) -> str:
            try:
                return self.display_name or self.email or self.public_id or f"User {self.id}"
            except Exception:
                return DEFAULT_USER_DISPLAY_NAME

        @property
        def is_admin(self) -> bool:
            try:
                return normalize_user_role(self.role) == "admin"
            except Exception:
                return False

        @property
        def is_enabled(self) -> bool:
            try:
                return bool(self.is_active) and self.disabled_at is None
            except Exception:
                return False

        def normalize(self) -> "AppUser":
            try:
                if not self.public_id:
                    if safe_int(self.id, 0) == DEFAULT_USER_ID:
                        self.public_id = DEFAULT_USER_PUBLIC_ID
                    else:
                        self.public_id = public_id("usr")

                self.display_name = safe_str(
                    self.display_name,
                    DEFAULT_USER_DISPLAY_NAME,
                    255,
                ) or DEFAULT_USER_DISPLAY_NAME

                self.email = safe_str(self.email, "", 255) or None
                self.handle = safe_str(self.handle, "", 120) or None
                self.role = normalize_user_role(self.role, DEFAULT_USER_ROLE)

                self.locale = safe_str(self.locale, "", 32) or None
                self.timezone = safe_str(self.timezone, "", 80) or None
                self.avatar_url = safe_str(self.avatar_url, "", 2000) or None

                self.settings = safe_dict(self.settings)
                self.profile = safe_dict(self.profile)
                self.metadata_json = safe_dict(self.metadata_json)

                self.is_active = safe_bool(self.is_active, True)
                self.is_placeholder = safe_bool(self.is_placeholder, False)
                self.is_system = safe_bool(self.is_system, False)

                return self

            except Exception:
                return self

        def mark_seen(self) -> None:
            try:
                self.last_seen_at = utcnow()
                self.touch()
            except Exception:
                pass

        def activate(self) -> None:
            try:
                self.is_active = True
                self.disabled_at = None
                self.disabled_reason = None
                self.touch()
            except Exception:
                pass

        def deactivate(self, reason: str = "") -> None:
            try:
                self.is_active = False
                self.disabled_at = utcnow()
                self.disabled_reason = safe_str(reason, "", 2000) or None
                self.touch()
            except Exception:
                pass

        def update_profile(self, payload: Optional[Dict[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                if "display_name" in data or "displayName" in data or "name" in data:
                    self.display_name = safe_str(
                        data.get("display_name") or data.get("displayName") or data.get("name"),
                        self.display_name or DEFAULT_USER_DISPLAY_NAME,
                        255,
                    )

                if "email" in data:
                    self.email = safe_str(data.get("email"), "", 255) or None

                if "handle" in data:
                    self.handle = safe_str(data.get("handle"), "", 120) or None

                if "locale" in data:
                    self.locale = safe_str(data.get("locale"), "", 32) or None

                if "timezone" in data:
                    self.timezone = safe_str(data.get("timezone"), "", 80) or None

                if "avatar_url" in data or "avatarUrl" in data:
                    self.avatar_url = safe_str(
                        data.get("avatar_url") or data.get("avatarUrl"),
                        "",
                        2000,
                    ) or None

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "profile" in data:
                    self.profile = safe_dict(data.get("profile"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                self.normalize()
                self.touch()

            except Exception:
                pass

        def to_dict(self, *, include_private: bool = False, include_profile: bool = True) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "user_id": self.id,
                    "public_id": self.public_id,
                    "display_name": self.display_name,
                    "name": self.name,
                    "handle": self.handle,
                    "role": normalize_user_role(self.role, DEFAULT_USER_ROLE),
                    "is_active": bool(self.is_active),
                    "is_enabled": self.is_enabled,
                    "is_placeholder": bool(self.is_placeholder),
                    "is_system": bool(self.is_system),
                    "is_admin": self.is_admin,
                    "locale": self.locale,
                    "timezone": self.timezone,
                    "avatar_url": self.avatar_url,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "last_seen_at": isoformat(self.last_seen_at),
                    "disabled_at": isoformat(self.disabled_at),
                }

                if include_profile:
                    payload["settings"] = safe_dict(self.settings)
                    payload["profile"] = safe_dict(self.profile)

                if include_private:
                    payload["email"] = self.email
                    payload["disabled_reason"] = self.disabled_reason
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "user_id": getattr(self, "id", None),
                    "public_id": getattr(self, "public_id", None),
                    "display_name": getattr(self, "display_name", DEFAULT_USER_DISPLAY_NAME),
                    "is_placeholder": bool(getattr(self, "is_placeholder", False)),
                }

        def to_public_dict(self) -> Dict[str, Any]:
            try:
                return {
                    "id": self.id,
                    "user_id": self.id,
                    "public_id": self.public_id,
                    "display_name": self.display_name,
                    "name": self.name,
                    "handle": self.handle,
                    "avatar_url": self.avatar_url,
                    "is_placeholder": bool(self.is_placeholder),
                }
            except Exception:
                return {}

        @classmethod
        def build_default(cls, user_id: Optional[int] = None) -> "AppUser":
            resolved_user_id = safe_int(user_id, get_default_user_id(), minimum=1)

            user = cls()
            user.id = resolved_user_id
            user.public_id = get_default_user_public_id(resolved_user_id)
            user.display_name = get_default_user_display_name()
            user.role = DEFAULT_USER_ROLE
            user.is_active = True
            user.is_placeholder = True
            user.is_system = False
            user.settings = {}
            user.profile = {
                "source": "placeholder",
            }
            user.metadata_json = {
                "source": "vectoplan-app",
                "placeholder": True,
            }
            user.normalize()

            return user

    return AppUser


try:
    if "app_users" in getattr(db, "metadata").tables:
        try:
            from .core import AppUser as AppUser  # type: ignore
        except Exception:
            AppUser = _define_app_user_model(extend_existing=True)
    else:
        AppUser = _define_app_user_model(extend_existing=False)
except Exception:
    AppUser = _define_app_user_model(extend_existing=True)


# ─────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────

def normalize_user_id(value: Any, default: int = DEFAULT_USER_ID) -> int:
    try:
        return safe_int(value, default, minimum=1)
    except Exception:
        return default


def get_user_by_id(user_id: Any) -> Optional[AppUser]:
    try:
        resolved_user_id = normalize_user_id(user_id)
        return AppUser.query.get(resolved_user_id)
    except Exception:
        return None


def get_user_by_public_id(public_id_value: Any) -> Optional[AppUser]:
    try:
        value = safe_str(public_id_value, "", 160)
        if not value:
            return None

        return AppUser.query.filter_by(public_id=value).one_or_none()

    except Exception:
        return None


def serialize_user(user: Any, *, include_private: bool = False, include_profile: bool = True) -> Dict[str, Any]:
    try:
        if user is None:
            return {}

        if hasattr(user, "to_dict"):
            try:
                return user.to_dict(
                    include_private=include_private,
                    include_profile=include_profile,
                )
            except TypeError:
                return user.to_dict()

        return {
            "id": _get_attr(user, "id"),
            "user_id": _get_attr(user, "id"),
            "public_id": _get_attr(user, "public_id"),
            "display_name": _get_attr(user, "display_name", DEFAULT_USER_DISPLAY_NAME),
            "is_active": bool(_get_attr(user, "is_active", True)),
            "is_placeholder": bool(_get_attr(user, "is_placeholder", False)),
        }

    except Exception:
        return {}


def ensure_default_user(
    *,
    user_id: Optional[int] = None,
    commit: bool = True,
    session: Any = None,
) -> AppUser:
    """
    Ensure placeholder user exists.

    Current app contract:
    - logged-in placeholder user id = 1
    - this function is safe to call during startup
    - it is also safe if the user already exists
    """
    resolved_user_id = normalize_user_id(user_id or get_default_user_id())
    db_session = session or db.session

    try:
        user = AppUser.query.get(resolved_user_id)

        if user is not None:
            try:
                if hasattr(user, "normalize"):
                    user.normalize()

                if not _get_attr(user, "public_id"):
                    _set_if_available(user, "public_id", get_default_user_public_id(resolved_user_id))

                if not _get_attr(user, "display_name"):
                    _set_if_available(user, "display_name", get_default_user_display_name())

                if _get_attr(user, "is_placeholder", None) is None:
                    _set_if_available(user, "is_placeholder", resolved_user_id == DEFAULT_USER_ID)

                if commit:
                    db_session.add(user)
                    db_session.commit()
                else:
                    db_session.add(user)
                    db_session.flush()

            except Exception:
                try:
                    db_session.rollback()
                except Exception:
                    pass

            return user

        user = AppUser()

        _set_if_available(user, "id", resolved_user_id)
        _set_if_available(user, "public_id", get_default_user_public_id(resolved_user_id))
        _set_if_available(user, "display_name", get_default_user_display_name())
        _set_if_available(user, "role", DEFAULT_USER_ROLE)
        _set_if_available(user, "is_active", True)
        _set_if_available(user, "is_placeholder", True)
        _set_if_available(user, "is_system", False)
        _set_if_available(user, "settings", {})
        _set_if_available(user, "profile", {"source": "placeholder"})
        _set_if_available(user, "metadata_json", {"source": "vectoplan-app", "placeholder": True})

        if hasattr(user, "normalize"):
            try:
                user.normalize()
            except Exception:
                pass

        db_session.add(user)

        if commit:
            db_session.commit()
        else:
            db_session.flush()

        return user

    except Exception as exc:
        try:
            db_session.rollback()
        except Exception:
            pass

        _log_warning("ensure_default_user failed: %s", exc.__class__.__name__)

        try:
            existing = AppUser.query.get(resolved_user_id)
            if existing is not None:
                return existing
        except Exception:
            pass

        fallback = AppUser()
        _set_if_available(fallback, "id", resolved_user_id)
        _set_if_available(fallback, "public_id", get_default_user_public_id(resolved_user_id))
        _set_if_available(fallback, "display_name", get_default_user_display_name())
        _set_if_available(fallback, "role", DEFAULT_USER_ROLE)
        _set_if_available(fallback, "is_active", True)
        _set_if_available(fallback, "is_placeholder", True)
        return fallback


def current_user_id_placeholder() -> int:
    return get_default_user_id(DEFAULT_USER_ID)


def get_user_model_status() -> Dict[str, Any]:
    try:
        count = 0
        default_exists = False

        try:
            count = int(AppUser.query.count())
        except Exception:
            count = 0

        try:
            default_exists = AppUser.query.get(get_default_user_id()) is not None
        except Exception:
            default_exists = False

        return {
            "ok": True,
            "model": "AppUser",
            "table": getattr(AppUser, "__tablename__", "app_users"),
            "count": count,
            "default_user_id": get_default_user_id(),
            "default_user_exists": default_exists,
            "placeholder_enabled": True,
        }

    except Exception as exc:
        return {
            "ok": False,
            "model": "AppUser",
            "table": "app_users",
            "error": str(exc),
            "default_user_id": DEFAULT_USER_ID,
            "placeholder_enabled": True,
        }


__all__ = [
    "DEFAULT_USER_ID",
    "DEFAULT_USER_PUBLIC_ID",
    "DEFAULT_USER_DISPLAY_NAME",
    "DEFAULT_USER_ROLE",
    "AppUser",
    "normalize_user_id",
    "normalize_user_role",
    "get_default_user_id",
    "get_default_user_public_id",
    "get_default_user_display_name",
    "get_user_by_id",
    "get_user_by_public_id",
    "serialize_user",
    "ensure_default_user",
    "current_user_id_placeholder",
    "get_user_model_status",
]