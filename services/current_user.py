# services/vectoplan-app/services/current_user.py
from __future__ import annotations

"""
VECTOPLAN current user service.

Zweck:
- Zentraler Platzhalter für "eingeloggter User".
- In der ersten Projektverwaltungsphase wird immer User id=1 verwendet.
- Nutzt das neue modulare AppUser-Model direkt.
- Keine Kompatibilität mit alter DB-Struktur.

Aktuelles Verhalten:
- get_current_user_id() gibt standardmäßig 1 zurück.
- ensure_default_user() legt AppUser(id=1) an, falls noch nicht vorhanden.
- get_current_user() liefert den AppUser-Datensatz.
- Kein Login-System, keine Session-Pflicht, keine externe Auth.

Wichtig:
- Diese Datei behebt keine alte Postgres-Struktur.
- Nach Model-Änderungen muss die Entwicklungsdatenbank neu erzeugt werden.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from flask import current_app, g, has_app_context, has_request_context, request
except Exception:  # pragma: no cover
    current_app = None  # type: ignore
    g = None  # type: ignore
    request = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False

    def has_request_context() -> bool:  # type: ignore
        return False

try:
    from extensions import db
except Exception:  # pragma: no cover
    db = None  # type: ignore

try:
    from models import AppUser
except Exception:  # pragma: no cover
    AppUser = None  # type: ignore


DEFAULT_USER_ID = 1
DEFAULT_USER_PUBLIC_ID = "u_demo_1"
DEFAULT_USER_HANDLE = "demo"
DEFAULT_USER_DISPLAY_NAME = "Demo User"
DEFAULT_USER_ROLE = "admin"
DEFAULT_USER_LOCALE = "de-DE"
DEFAULT_USER_TIMEZONE = "Europe/Berlin"


@dataclass(frozen=True)
class CurrentUserContext:
    user_id: int
    id: int
    public_id: str = DEFAULT_USER_PUBLIC_ID
    handle: str = DEFAULT_USER_HANDLE
    display_name: str = DEFAULT_USER_DISPLAY_NAME
    email: Optional[str] = None
    role: str = DEFAULT_USER_ROLE
    locale: str = DEFAULT_USER_LOCALE
    timezone: str = DEFAULT_USER_TIMEZONE
    is_active: bool = True
    is_placeholder: bool = True
    is_system: bool = True
    source: str = "placeholder"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "id": self.id,
            "public_id": self.public_id,
            "handle": self.handle,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "locale": self.locale,
            "timezone": self.timezone,
            "is_active": self.is_active,
            "is_placeholder": self.is_placeholder,
            "is_system": self.is_system,
            "source": self.source,
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_int(value: Any, default: int = DEFAULT_USER_ID) -> int:
    try:
        if isinstance(value, bool):
            return default

        if value is None:
            return default

        text = str(value).strip()

        if not text:
            return default

        parsed = int(text)

        if parsed <= 0:
            return default

        return parsed

    except Exception:
        return default


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein"}:
            return False

        return default

    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _config_value(key: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(key)
            if value is not None and value != "":
                return value

        return default

    except Exception:
        return default


def _log_warning(message: str, *args: Any) -> None:
    try:
        if has_app_context() and current_app is not None:
            current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        if has_app_context() and current_app is not None:
            if exc is not None:
                current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
            else:
                current_app.logger.exception(message)
    except Exception:
        pass


def _db_get_user(user_id: int) -> Any:
    if AppUser is None or db is None:
        return None

    try:
        if hasattr(db.session, "get"):
            return db.session.get(AppUser, user_id)

        return AppUser.query.get(user_id)

    except Exception:
        raise


# ─────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────

def get_default_user_id() -> int:
    try:
        return _safe_int(
            _config_value("VECTOPLAN_DEFAULT_USER_ID", DEFAULT_USER_ID),
            DEFAULT_USER_ID,
        )
    except Exception:
        return DEFAULT_USER_ID


def get_default_user_public_id() -> str:
    try:
        configured = _safe_str(
            _config_value("VECTOPLAN_DEFAULT_USER_PUBLIC_ID", DEFAULT_USER_PUBLIC_ID),
            DEFAULT_USER_PUBLIC_ID,
            80,
        )
        return configured or DEFAULT_USER_PUBLIC_ID
    except Exception:
        return DEFAULT_USER_PUBLIC_ID


def get_default_user_handle() -> str:
    try:
        configured = _safe_str(
            _config_value("VECTOPLAN_DEFAULT_USER_HANDLE", DEFAULT_USER_HANDLE),
            DEFAULT_USER_HANDLE,
            80,
        )
        return configured or DEFAULT_USER_HANDLE
    except Exception:
        return DEFAULT_USER_HANDLE


def get_default_user_display_name() -> str:
    try:
        configured = _safe_str(
            _config_value("VECTOPLAN_DEFAULT_USER_DISPLAY_NAME", DEFAULT_USER_DISPLAY_NAME),
            DEFAULT_USER_DISPLAY_NAME,
            160,
        )
        return configured or DEFAULT_USER_DISPLAY_NAME
    except Exception:
        return DEFAULT_USER_DISPLAY_NAME


def get_default_user_role() -> str:
    try:
        configured = _safe_str(
            _config_value("VECTOPLAN_DEFAULT_USER_ROLE", DEFAULT_USER_ROLE),
            DEFAULT_USER_ROLE,
            40,
        ).lower()

        if configured in {"owner", "admin", "editor", "viewer", "user", "system"}:
            return configured

        return DEFAULT_USER_ROLE

    except Exception:
        return DEFAULT_USER_ROLE


# ─────────────────────────────────────────────────────────────
# Current user ID resolution
# ─────────────────────────────────────────────────────────────

def _request_user_id_for_diagnostics_only() -> Optional[int]:
    try:
        if not has_request_context() or request is None:
            return None

        allow_header_override = _safe_bool(
            _config_value("VECTOPLAN_ALLOW_USER_HEADER_OVERRIDE", False),
            False,
        )

        if not allow_header_override:
            return None

        candidates = (
            request.headers.get("X-VECTOPLAN-USER-ID"),
            request.headers.get("X-User-ID"),
            request.args.get("user_id"),
        )

        for candidate in candidates:
            parsed = _safe_int(candidate, 0)
            if parsed > 0:
                return parsed

        return None

    except Exception:
        return None


def get_current_user_id(*, allow_request_override: bool = False) -> int:
    try:
        default_id = get_default_user_id()

        if allow_request_override:
            request_user_id = _request_user_id_for_diagnostics_only()
            if request_user_id:
                return request_user_id

        return default_id

    except Exception:
        return DEFAULT_USER_ID


def set_current_user_id_on_g(user_id: Optional[int] = None) -> int:
    resolved = _safe_int(user_id, get_current_user_id())

    try:
        if has_request_context() and g is not None:
            g.vectoplan_user_id = resolved
    except Exception:
        pass

    return resolved


def get_current_user_id_from_g_or_default() -> int:
    try:
        if has_request_context() and g is not None:
            value = getattr(g, "vectoplan_user_id", None)
            if value:
                return _safe_int(value, get_current_user_id())
    except Exception:
        pass

    return get_current_user_id()


# ─────────────────────────────────────────────────────────────
# User creation / loading
# ─────────────────────────────────────────────────────────────

def _default_user_metadata() -> Dict[str, Any]:
    return {
        "source": "vectoplan-app",
        "placeholder": True,
        "created_by": "services.current_user.ensure_default_user",
    }


def _apply_default_user_values(user: Any, *, user_id: int) -> bool:
    changed = False

    try:
        if not getattr(user, "id", None):
            user.id = user_id
            changed = True

        if not getattr(user, "public_id", None):
            user.public_id = get_default_user_public_id() if user_id == DEFAULT_USER_ID else f"u_demo_{user_id}"
            changed = True

        if not getattr(user, "handle", None):
            user.handle = get_default_user_handle() if user_id == DEFAULT_USER_ID else f"demo_{user_id}"
            changed = True

        if not getattr(user, "display_name", None):
            user.display_name = get_default_user_display_name()
            changed = True

        if not getattr(user, "role", None):
            user.role = get_default_user_role()
            changed = True

        if not getattr(user, "locale", None):
            user.locale = DEFAULT_USER_LOCALE
            changed = True

        if not getattr(user, "timezone", None):
            user.timezone = DEFAULT_USER_TIMEZONE
            changed = True

        if getattr(user, "is_active", None) is not True:
            user.is_active = True
            changed = True

        if getattr(user, "is_placeholder", None) is not True:
            user.is_placeholder = True
            changed = True

        if getattr(user, "is_system", None) is not True:
            user.is_system = True
            changed = True

        settings = _safe_dict(getattr(user, "settings", None))
        if not settings.get("phase"):
            settings["phase"] = "placeholder-user"
            user.settings = settings
            changed = True

        profile = _safe_dict(getattr(user, "profile", None))
        if not profile.get("display_name"):
            profile["display_name"] = getattr(user, "display_name", get_default_user_display_name())
            user.profile = profile
            changed = True

        metadata = _safe_dict(getattr(user, "metadata_json", None))
        defaults = _default_user_metadata()

        for key, value in defaults.items():
            if key not in metadata:
                metadata[key] = value
                changed = True

        user.metadata_json = metadata

        if hasattr(user, "normalize"):
            user.normalize()

        return changed

    except Exception:
        return changed


def _build_default_user(user_id: int) -> Any:
    if AppUser is None:
        return None

    try:
        if hasattr(AppUser, "build_default") and user_id == DEFAULT_USER_ID:
            user = AppUser.build_default()
            _apply_default_user_values(user, user_id=user_id)
            return user

    except Exception:
        pass

    user = AppUser(
        id=user_id,
        public_id=get_default_user_public_id() if user_id == DEFAULT_USER_ID else f"u_demo_{user_id}",
        email=None,
        handle=get_default_user_handle() if user_id == DEFAULT_USER_ID else f"demo_{user_id}",
        display_name=get_default_user_display_name(),
        role=get_default_user_role(),
        locale=DEFAULT_USER_LOCALE,
        timezone=DEFAULT_USER_TIMEZONE,
        avatar_url=None,
        is_active=True,
        is_placeholder=True,
        is_system=True,
        settings={"phase": "placeholder-user"},
        profile={"display_name": get_default_user_display_name()},
        metadata_json=_default_user_metadata(),
    )

    try:
        if hasattr(user, "normalize"):
            user.normalize()
    except Exception:
        pass

    return user


def ensure_default_user(*, commit: bool = True) -> Any:
    if AppUser is None:
        _log_warning("AppUser model unavailable; cannot ensure default user")
        return None

    if db is None:
        _log_warning("db unavailable; cannot ensure default user")
        return None

    try:
        if not has_app_context():
            return None

        user_id = get_default_user_id()
        user = _db_get_user(user_id)

        if user is None:
            user = _build_default_user(user_id)
            db.session.add(user)

            if commit:
                db.session.commit()
            else:
                db.session.flush()

            return user

        changed = _apply_default_user_values(user, user_id=user_id)

        if changed:
            db.session.add(user)

            if commit:
                db.session.commit()
            else:
                db.session.flush()

        return user

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass

        _log_exception("ensure_default_user failed", exc)
        return None


def get_current_user(*, ensure: bool = True) -> Any:
    if AppUser is None or db is None:
        return None

    try:
        if not has_app_context():
            return None

        user_id = get_current_user_id_from_g_or_default()
        user = _db_get_user(user_id)

        if user is None and ensure:
            user = ensure_default_user()

        return user

    except Exception as exc:
        _log_exception("get_current_user failed", exc)
        return None


def require_current_user() -> Any:
    user = get_current_user(ensure=True)

    if user is None:
        raise RuntimeError("current user unavailable")

    return user


def get_current_user_context(*, ensure: bool = True) -> CurrentUserContext:
    try:
        user = get_current_user(ensure=ensure)

        if user is not None:
            user_id = _safe_int(getattr(user, "id", None), get_default_user_id())

            return CurrentUserContext(
                user_id=user_id,
                id=user_id,
                public_id=_safe_str(getattr(user, "public_id", None), get_default_user_public_id(), 80),
                handle=_safe_str(getattr(user, "handle", None), get_default_user_handle(), 80),
                display_name=_safe_str(getattr(user, "display_name", None), get_default_user_display_name(), 160),
                email=getattr(user, "email", None),
                role=_safe_str(getattr(user, "role", None), get_default_user_role(), 40),
                locale=_safe_str(getattr(user, "locale", None), DEFAULT_USER_LOCALE, 40),
                timezone=_safe_str(getattr(user, "timezone", None), DEFAULT_USER_TIMEZONE, 80),
                is_active=_safe_bool(getattr(user, "is_active", True), True),
                is_placeholder=_safe_bool(getattr(user, "is_placeholder", True), True),
                is_system=_safe_bool(getattr(user, "is_system", True), True),
                source="db",
            )

        fallback_id = get_current_user_id()

        return CurrentUserContext(
            user_id=fallback_id,
            id=fallback_id,
            public_id=get_default_user_public_id(),
            handle=get_default_user_handle(),
            display_name=get_default_user_display_name(),
            email=None,
            role=get_default_user_role(),
            locale=DEFAULT_USER_LOCALE,
            timezone=DEFAULT_USER_TIMEZONE,
            is_active=True,
            is_placeholder=True,
            is_system=True,
            source="fallback",
        )

    except Exception:
        return CurrentUserContext(
            user_id=DEFAULT_USER_ID,
            id=DEFAULT_USER_ID,
            public_id=DEFAULT_USER_PUBLIC_ID,
            handle=DEFAULT_USER_HANDLE,
            display_name=DEFAULT_USER_DISPLAY_NAME,
            email=None,
            role=DEFAULT_USER_ROLE,
            locale=DEFAULT_USER_LOCALE,
            timezone=DEFAULT_USER_TIMEZONE,
            is_active=True,
            is_placeholder=True,
            is_system=True,
            source="error_fallback",
        )


def serialize_current_user(*, ensure: bool = True) -> Dict[str, Any]:
    try:
        user = get_current_user(ensure=ensure)

        if user is not None and hasattr(user, "to_public_dict"):
            try:
                return user.to_public_dict()
            except Exception:
                pass

        if user is not None and hasattr(user, "to_dict"):
            try:
                return user.to_dict(include_private=False)
            except TypeError:
                return user.to_dict()
            except Exception:
                pass

        return get_current_user_context(ensure=ensure).to_dict()

    except Exception:
        return CurrentUserContext(
            user_id=DEFAULT_USER_ID,
            id=DEFAULT_USER_ID,
        ).to_dict()


# ─────────────────────────────────────────────────────────────
# Permission convenience
# ─────────────────────────────────────────────────────────────

def is_current_user(user_id: Any) -> bool:
    try:
        return _safe_int(user_id, 0) == get_current_user_id_from_g_or_default()
    except Exception:
        return False


def is_current_user_admin_placeholder() -> bool:
    try:
        user_id = get_current_user_id_from_g_or_default()
        return user_id == get_default_user_id() == DEFAULT_USER_ID
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_current_user_status() -> Dict[str, Any]:
    try:
        context = get_current_user_context(ensure=False)

        db_user_exists = False

        try:
            if has_app_context() and AppUser is not None and db is not None:
                db_user_exists = _db_get_user(context.user_id) is not None
        except Exception:
            db_user_exists = False

        return {
            "ok": True,
            "phase": "placeholder-user",
            "default_user_id": get_default_user_id(),
            "current_user": context.to_dict(),
            "db_user_exists": db_user_exists,
            "has_app_context": bool(has_app_context()),
            "has_request_context": bool(has_request_context()),
            "header_override_enabled": _safe_bool(
                _config_value("VECTOPLAN_ALLOW_USER_HEADER_OVERRIDE", False),
                False,
            ),
            "model_available": AppUser is not None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "phase": "placeholder-user",
            "default_user_id": DEFAULT_USER_ID,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
            "model_available": AppUser is not None,
        }


__all__ = [
    "DEFAULT_USER_ID",
    "DEFAULT_USER_PUBLIC_ID",
    "DEFAULT_USER_HANDLE",
    "DEFAULT_USER_DISPLAY_NAME",
    "DEFAULT_USER_ROLE",
    "DEFAULT_USER_LOCALE",
    "DEFAULT_USER_TIMEZONE",
    "CurrentUserContext",
    "get_default_user_id",
    "get_default_user_public_id",
    "get_default_user_handle",
    "get_default_user_display_name",
    "get_default_user_role",
    "get_current_user_id",
    "set_current_user_id_on_g",
    "get_current_user_id_from_g_or_default",
    "ensure_default_user",
    "get_current_user",
    "require_current_user",
    "get_current_user_context",
    "serialize_current_user",
    "is_current_user",
    "is_current_user_admin_placeholder",
    "get_current_user_status",
]