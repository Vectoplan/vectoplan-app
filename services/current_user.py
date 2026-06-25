# services/vectoplan-app/services/current_user.py
from __future__ import annotations

"""
VECTOPLAN current user service.

Zweck:
- Zentraler User-/Auth-Kontext für vectoplan-app.
- Hält den aktuellen Entwicklungsmodus mit lokalem Dev-User id=1 kompatibel.
- Bereitet den späteren Login-/Registrierungsdienst vor.
- Unterscheidet sauber zwischen:
    1. dev_placeholder_user  -> aktueller Entwicklungsuser id=1
    2. authenticated_user    -> späterer eingeloggter User aus Auth-Service
    3. demo_guest            -> nicht eingeloggter Demo-Modus ohne Persistenz
- Erzeugt KEINE echten Benutzeraccounts.
- Kann vorhandene AppUser-Datensätze als lokale Auth-Verknüpfung verwenden.
- Zeigt im Context klar an, ob Daten persistent sind oder Demo-Daten.

Wichtige Architekturregel:
- Registrierung, Login, Account-Typ, Abo-Status und Bigdata-Zugriff liegen
  später im Auth-/Registrierungsdienst.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Einladungen,
  Veröffentlichungen und Projektfrontend.
- AppUser in vectoplan-app ist nur lokaler Link/Shadow/Placeholder, nicht die
  echte Benutzerregistrierung.
"""

import datetime as _dt
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple


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


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

LOGGER_NAME = "vectoplan.current_user"

DEFAULT_USER_ID = 1
DEFAULT_USER_PUBLIC_ID = "u_demo_1"
DEFAULT_USER_HANDLE = "demo"
DEFAULT_USER_DISPLAY_NAME = "Demo User"
DEFAULT_USER_ROLE = "admin"
DEFAULT_USER_LOCALE = "de-DE"
DEFAULT_USER_TIMEZONE = "Europe/Berlin"

DEMO_PUBLIC_ID = "demo_guest"
DEMO_HANDLE = "demo"
DEMO_DISPLAY_NAME = "Demo-Modus"
DEMO_ROLE = "demo"
DEMO_ACCOUNT_PLAN = "demo"
DEMO_ACCOUNT_STATUS = "temporary"
DEMO_TTL_SECONDS = 1800

AUTH_MODE_DEV = "dev"
AUTH_MODE_EXTERNAL = "external"
AUTH_MODE_DEMO = "demo"

VALID_AUTH_MODES = {
    AUTH_MODE_DEV,
    AUTH_MODE_EXTERNAL,
    AUTH_MODE_DEMO,
}

SOURCE_DB = "db"
SOURCE_FALLBACK = "fallback"
SOURCE_DEV_PLACEHOLDER = "dev_placeholder"
SOURCE_AUTH_HEADERS = "auth_headers"
SOURCE_DEMO = "demo"
SOURCE_ERROR_FALLBACK = "error_fallback"

CURRENT_CONTEXT_G_KEY = "vectoplan_current_user_context"
CURRENT_USER_ID_G_KEY = "vectoplan_user_id"


# ─────────────────────────────────────────────────────────────
# Context object
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CurrentUserContext:
    """
    Einheitlicher User-/Auth-Kontext.

    Backward-kompatibel:
    - user_id und id bleiben vorhanden.
    - Im aktuellen Dev-Modus sind beide 1.
    - Im Demo-Modus können beide None sein. Für alte Aufrufer gibt es weiterhin
      get_current_user_id(), das aus Kompatibilitätsgründen einen Fallback liefern kann.

    Neue Felder:
    - authenticated: echter/dev-authentifizierter Kontext
    - demo_mode: nicht eingeloggter Demo-Kontext
    - persistent: ob Änderungen dauerhaft gespeichert werden dürfen
    - auth_user_id: externe Auth-ID aus späterem Login-/Registrierungsdienst
    - account_plan/account_status/can_use_bigdata: spätere Account-/Abo-Information
    """

    user_id: Optional[int]
    id: Optional[int]

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

    authenticated: bool = True
    demo_mode: bool = False
    persistent: bool = True

    auth_mode: str = AUTH_MODE_DEV
    auth_user_id: Optional[str] = None
    auth_email: Optional[str] = None

    account_plan: Optional[str] = None
    account_status: Optional[str] = None
    can_use_bigdata: bool = False

    source: str = SOURCE_DEV_PLACEHOLDER

    ttl_seconds: Optional[int] = None
    expires_at: Optional[str] = None

    warning: Optional[str] = None
    capabilities: Dict[str, bool] = field(default_factory=dict)
    raw_auth: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            # legacy/current keys
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

            # explicit auth/demo keys
            "authenticated": self.authenticated,
            "is_authenticated": self.authenticated,
            "demo_mode": self.demo_mode,
            "is_demo": self.demo_mode,
            "persistent": self.persistent,
            "auth_mode": self.auth_mode,
            "auth_user_id": self.auth_user_id,
            "auth_email": self.auth_email,
            "account_plan": self.account_plan,
            "account_status": self.account_status,
            "can_use_bigdata": self.can_use_bigdata,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "warning": self.warning,
            "capabilities": dict(self.capabilities or {}),
            "raw_auth": dict(self.raw_auth or {}),

            # frontend-friendly aliases
            "userId": self.user_id,
            "publicId": self.public_id,
            "displayName": self.display_name,
            "isActive": self.is_active,
            "isPlaceholder": self.is_placeholder,
            "isSystem": self.is_system,
            "isAuthenticated": self.authenticated,
            "demoMode": self.demo_mode,
            "authMode": self.auth_mode,
            "authUserId": self.auth_user_id,
            "authEmail": self.auth_email,
            "accountPlan": self.account_plan,
            "accountStatus": self.account_status,
            "canUseBigdata": self.can_use_bigdata,
            "ttlSeconds": self.ttl_seconds,
            "expiresAt": self.expires_at,
        }

        return data


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _utcnow() -> _dt.datetime:
    try:
        return _dt.datetime.now(_dt.timezone.utc)
    except Exception:  # pragma: no cover
        return _dt.datetime.utcnow()


def _iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


def _safe_int(value: Any, default: Optional[int] = DEFAULT_USER_ID) -> Optional[int]:
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

        if value is None:
            return default

        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()

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
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return dict(value.to_dict())
        return {}
    except Exception:
        return {}


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _config_value(key: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(key)
            if value is not None and value != "":
                return value
    except Exception:
        pass

    try:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value
    except Exception:
        pass

    return default


def _log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        logger = current_app.logger if has_app_context() and current_app is not None else logging.getLogger(LOGGER_NAME)
        if kwargs:
            logger.warning("%s %s", message, _compact_json(kwargs))
        else:
            logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None, **kwargs: Any) -> None:
    try:
        logger = current_app.logger if has_app_context() and current_app is not None else logging.getLogger(LOGGER_NAME)
        suffix = _compact_json(kwargs) if kwargs else ""
        if exc is not None:
            logger.exception("%s %s: %s", message, suffix, exc.__class__.__name__)
        else:
            logger.exception("%s %s", message, suffix)
    except Exception:
        pass


def _request_header(name: str, default: Any = None) -> Any:
    try:
        if not has_request_context() or request is None:
            return default
        return request.headers.get(name, default)
    except Exception:
        return default


def _request_arg(name: str, default: Any = None) -> Any:
    try:
        if not has_request_context() or request is None:
            return default
        return request.args.get(name, default)
    except Exception:
        return default


def _db_get_user(user_id: Optional[int]) -> Any:
    if AppUser is None or db is None or not user_id:
        return None

    try:
        if hasattr(db.session, "get"):
            return db.session.get(AppUser, user_id)

        return AppUser.query.get(user_id)

    except Exception:
        raise


def _db_find_user_by_field(field_name: str, value: Any) -> Any:
    if AppUser is None or db is None:
        return None

    safe_value = _safe_str(value, "", 320)
    if not safe_value:
        return None

    try:
        if not hasattr(AppUser, field_name):
            return None

        field = getattr(AppUser, field_name)
        return AppUser.query.filter(field == safe_value).first()

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Auth/Demo configuration
# ─────────────────────────────────────────────────────────────

def get_auth_mode() -> str:
    """
    Liefert den Betriebsmodus.

    Default bleibt "dev", damit der aktuelle Entwicklungsstand mit User id=1
    nicht bricht.

    Empfohlene spätere Werte:
    - VECTOPLAN_AUTH_MODE=external
      Auth-Service ist vorgeschaltet; ohne Auth-Kontext wird Demo angezeigt.

    - VECTOPLAN_AUTH_MODE=demo
      Erzwingt Demo-Modus.
    """
    try:
        forced_demo = _safe_bool(_config_value("VECTOPLAN_FORCE_DEMO_MODE", False), False)
        if forced_demo:
            return AUTH_MODE_DEMO

        configured = _safe_str(
            _config_value("VECTOPLAN_AUTH_MODE", AUTH_MODE_DEV),
            AUTH_MODE_DEV,
            40,
        ).lower()

        if configured in {"placeholder", "local", "development"}:
            return AUTH_MODE_DEV

        if configured in {"auth", "login", "sso", "oidc", "external"}:
            return AUTH_MODE_EXTERNAL

        if configured in {"demo", "guest", "anonymous"}:
            return AUTH_MODE_DEMO

        if configured in VALID_AUTH_MODES:
            return configured

        return AUTH_MODE_DEV

    except Exception:
        return AUTH_MODE_DEV


def is_demo_query_enabled() -> bool:
    return _safe_bool(_config_value("VECTOPLAN_ALLOW_DEMO_QUERY_PARAM", True), True)


def get_demo_ttl_seconds() -> int:
    try:
        configured = _safe_int(_config_value("VECTOPLAN_DEMO_TTL_SECONDS", DEMO_TTL_SECONDS), DEMO_TTL_SECONDS)
        if not configured or configured < 60:
            return DEMO_TTL_SECONDS
        return int(configured)
    except Exception:
        return DEMO_TTL_SECONDS


def get_demo_expires_at() -> str:
    try:
        return (_utcnow() + _dt.timedelta(seconds=get_demo_ttl_seconds())).isoformat()
    except Exception:
        return ""


def is_requesting_demo_mode() -> bool:
    try:
        if get_auth_mode() == AUTH_MODE_DEMO:
            return True

        header_demo = (
            _request_header("X-VECTOPLAN-DEMO-MODE")
            or _request_header("X-Demo-Mode")
            or _request_header("X-VECTOPLAN-GUEST")
        )
        if header_demo is not None and _safe_bool(header_demo, False):
            return True

        if is_demo_query_enabled():
            arg_demo = _request_arg("demo")
            if arg_demo is not None and _safe_bool(arg_demo, False):
                return True

        return False
    except Exception:
        return False


def auth_headers_trusted() -> bool:
    """
    Serverseitige Header-Vertrauensentscheidung.

    In Produktion darf diese Option nur gesetzt werden, wenn ein vertrauenswürdiger
    Reverse Proxy / Auth-Gateway die Header schreibt.
    """
    try:
        if get_auth_mode() == AUTH_MODE_EXTERNAL:
            return _safe_bool(_config_value("VECTOPLAN_TRUST_AUTH_HEADERS", True), True)

        return _safe_bool(_config_value("VECTOPLAN_TRUST_AUTH_HEADERS", False), False)

    except Exception:
        return False


def allow_user_header_override() -> bool:
    try:
        return _safe_bool(_config_value("VECTOPLAN_ALLOW_USER_HEADER_OVERRIDE", False), False)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────

def get_default_user_id() -> int:
    try:
        parsed = _safe_int(
            _config_value("VECTOPLAN_DEFAULT_USER_ID", DEFAULT_USER_ID),
            DEFAULT_USER_ID,
        )
        return int(parsed or DEFAULT_USER_ID)
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
# Request auth extraction
# ─────────────────────────────────────────────────────────────

def _request_user_id_for_diagnostics_only() -> Optional[int]:
    try:
        if not has_request_context() or request is None:
            return None

        if not allow_user_header_override():
            return None

        candidates = (
            request.headers.get("X-VECTOPLAN-USER-ID"),
            request.headers.get("X-User-ID"),
            request.headers.get("X-App-User-ID"),
            request.args.get("user_id"),
        )

        for candidate in candidates:
            parsed = _safe_int(candidate, None)
            if parsed and parsed > 0:
                return parsed

        return None

    except Exception:
        return None


def _request_auth_header_context() -> Dict[str, Any]:
    """
    Liest spätere Auth-Gateway-Header.

    Diese Header werden nur verwendet, wenn auth_headers_trusted() True ist.
    """
    if not has_request_context() or request is None:
        return {}

    if not auth_headers_trusted():
        return {}

    try:
        authenticated = _safe_bool(
            request.headers.get("X-VECTOPLAN-AUTHENTICATED")
            or request.headers.get("X-Authenticated")
            or request.headers.get("X-User-Authenticated"),
            default=False,
        )

        auth_user_id = _safe_str(
            request.headers.get("X-VECTOPLAN-AUTH-USER-ID")
            or request.headers.get("X-Auth-User-ID")
            or request.headers.get("X-User-Sub")
            or request.headers.get("X-Forwarded-User")
            or request.headers.get("X-Remote-User"),
            "",
            160,
        )

        email = _safe_str(
            request.headers.get("X-VECTOPLAN-USER-EMAIL")
            or request.headers.get("X-User-Email")
            or request.headers.get("X-Auth-Email"),
            "",
            320,
        ).lower()

        display_name = _safe_str(
            request.headers.get("X-VECTOPLAN-USER-NAME")
            or request.headers.get("X-User-Name")
            or request.headers.get("X-Auth-Name"),
            "",
            160,
        )

        account_plan = _safe_str(
            request.headers.get("X-VECTOPLAN-ACCOUNT-PLAN")
            or request.headers.get("X-Account-Plan")
            or request.headers.get("X-Subscription-Plan"),
            "",
            80,
        ).lower()

        account_status = _safe_str(
            request.headers.get("X-VECTOPLAN-ACCOUNT-STATUS")
            or request.headers.get("X-Account-Status"),
            "",
            80,
        ).lower()

        can_use_bigdata = _safe_bool(
            request.headers.get("X-VECTOPLAN-CAN-USE-BIGDATA")
            or request.headers.get("X-Can-Use-Bigdata")
            or request.headers.get("X-Bigdata-Access"),
            default=False,
        )

        local_user_id = _safe_int(
            request.headers.get("X-VECTOPLAN-APP-USER-ID")
            or request.headers.get("X-App-User-ID"),
            None,
        )

        # Wenn ein Auth-Gateway eine Auth-ID oder E-Mail liefert, werten wir das
        # als authentifizierten Kontext, auch wenn kein explizites Bool-Header
        # vorhanden ist.
        if auth_user_id or email:
            authenticated = True

        return {
            "authenticated": authenticated,
            "auth_user_id": auth_user_id or None,
            "email": email or None,
            "display_name": display_name or None,
            "account_plan": account_plan or None,
            "account_status": account_status or None,
            "can_use_bigdata": can_use_bigdata,
            "local_user_id": local_user_id,
            "source": SOURCE_AUTH_HEADERS,
        }

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# User creation / loading
# ─────────────────────────────────────────────────────────────

def _default_user_metadata() -> Dict[str, Any]:
    return {
        "source": "vectoplan-app",
        "placeholder": True,
        "auth_mode": AUTH_MODE_DEV,
        "created_by": "services.current_user.ensure_default_user",
        "note": "Dev placeholder user. Not a real registered account.",
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
            changed = True
        if not settings.get("auth_mode"):
            settings["auth_mode"] = AUTH_MODE_DEV
            changed = True
        if hasattr(user, "settings"):
            user.settings = settings

        profile = _safe_dict(getattr(user, "profile", None))
        if not profile.get("display_name"):
            profile["display_name"] = getattr(user, "display_name", get_default_user_display_name())
            changed = True
        if hasattr(user, "profile"):
            user.profile = profile

        metadata = _safe_dict(getattr(user, "metadata_json", None))
        defaults = _default_user_metadata()

        for key, value in defaults.items():
            if key not in metadata:
                metadata[key] = value
                changed = True

        if hasattr(user, "metadata_json"):
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

    kwargs = {
        "id": user_id,
        "public_id": get_default_user_public_id() if user_id == DEFAULT_USER_ID else f"u_demo_{user_id}",
        "email": None,
        "handle": get_default_user_handle() if user_id == DEFAULT_USER_ID else f"demo_{user_id}",
        "display_name": get_default_user_display_name(),
        "role": get_default_user_role(),
        "locale": DEFAULT_USER_LOCALE,
        "timezone": DEFAULT_USER_TIMEZONE,
        "avatar_url": None,
        "is_active": True,
        "is_placeholder": True,
        "is_system": True,
        "settings": {"phase": "placeholder-user", "auth_mode": AUTH_MODE_DEV},
        "profile": {"display_name": get_default_user_display_name()},
        "metadata_json": _default_user_metadata(),
    }

    try:
        user = AppUser(**kwargs)
    except Exception:
        user = AppUser()
        for key, value in kwargs.items():
            try:
                if hasattr(user, key):
                    setattr(user, key, value)
            except Exception:
                pass

    try:
        if hasattr(user, "normalize"):
            user.normalize()
    except Exception:
        pass

    return user


def ensure_default_user(*, commit: bool = True) -> Any:
    """
    Stellt den Dev-Placeholder-User id=1 sicher.

    Nicht für echte Registrierung verwenden.
    Nicht im Demo-Modus verwenden.
    """
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


# ─────────────────────────────────────────────────────────────
# Auth-local user linking
# ─────────────────────────────────────────────────────────────

def find_local_user_for_auth_identity(
    *,
    auth_user_id: Any = None,
    email: Any = None,
    local_user_id: Any = None,
) -> Any:
    """
    Sucht eine bereits vorhandene lokale AppUser-Verknüpfung.

    Wichtig:
    - Diese Funktion erzeugt keinen AppUser.
    - Sie dient später dem Login-Sync.
    """
    try:
        parsed_local_user_id = _safe_int(local_user_id, None)
        if parsed_local_user_id:
            found = _db_get_user(parsed_local_user_id)
            if found is not None:
                return found
    except Exception:
        pass

    try:
        safe_auth_user_id = _safe_str(auth_user_id, "", 160)
        if safe_auth_user_id:
            found = _db_find_user_by_field("auth_user_id", safe_auth_user_id)
            if found is not None:
                return found
    except Exception:
        pass

    try:
        safe_email = _safe_str(email, "", 320).lower()
        if safe_email:
            found = _db_find_user_by_field("email", safe_email)
            if found is not None:
                return found
    except Exception:
        pass

    return None


def _context_from_user(
    user: Any,
    *,
    auth_mode: str,
    source: str,
    authenticated: bool = True,
    auth_data: Optional[Mapping[str, Any]] = None,
) -> CurrentUserContext:
    auth = _safe_dict(auth_data)

    user_id = _safe_int(getattr(user, "id", None), None)

    auth_user_id = _safe_str(
        auth.get("auth_user_id")
        or getattr(user, "auth_user_id", None),
        "",
        160,
    ) or None

    email = _safe_str(
        auth.get("email")
        or getattr(user, "email", None),
        "",
        320,
    ) or None

    display_name = _safe_str(
        auth.get("display_name")
        or getattr(user, "display_name", None),
        get_default_user_display_name(),
        160,
    )

    role = _safe_str(getattr(user, "role", None), get_default_user_role(), 40)

    is_placeholder = _safe_bool(getattr(user, "is_placeholder", False), False)
    is_system = _safe_bool(getattr(user, "is_system", False), False)

    account_plan = _safe_str(
        auth.get("account_plan")
        or getattr(user, "account_plan", None),
        "",
        80,
    ) or None

    account_status = _safe_str(
        auth.get("account_status")
        or getattr(user, "account_status", None),
        "",
        80,
    ) or None

    can_use_bigdata = _safe_bool(
        auth.get("can_use_bigdata")
        or getattr(user, "can_use_bigdata", None),
        False,
    )

    capabilities = {
        "can_use_bigdata": bool(can_use_bigdata),
        "can_persist_projects": True,
        "can_manage_projects": True,
        "demo": False,
    }

    return CurrentUserContext(
        user_id=user_id,
        id=user_id,
        public_id=_safe_str(getattr(user, "public_id", None), get_default_user_public_id(), 80),
        handle=_safe_str(getattr(user, "handle", None), get_default_user_handle(), 80),
        display_name=display_name,
        email=email,
        role=role,
        locale=_safe_str(getattr(user, "locale", None), DEFAULT_USER_LOCALE, 40),
        timezone=_safe_str(getattr(user, "timezone", None), DEFAULT_USER_TIMEZONE, 80),
        is_active=_safe_bool(getattr(user, "is_active", True), True),
        is_placeholder=is_placeholder,
        is_system=is_system,
        authenticated=authenticated,
        demo_mode=False,
        persistent=True,
        auth_mode=auth_mode,
        auth_user_id=auth_user_id,
        auth_email=email,
        account_plan=account_plan,
        account_status=account_status,
        can_use_bigdata=can_use_bigdata,
        source=source,
        capabilities=capabilities,
        raw_auth=auth,
    )


def _demo_context(source: str = SOURCE_DEMO) -> CurrentUserContext:
    ttl = get_demo_ttl_seconds()

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=DEMO_PUBLIC_ID,
        handle=DEMO_HANDLE,
        display_name=DEMO_DISPLAY_NAME,
        email=None,
        role=DEMO_ROLE,
        locale=DEFAULT_USER_LOCALE,
        timezone=DEFAULT_USER_TIMEZONE,
        is_active=True,
        is_placeholder=True,
        is_system=False,
        authenticated=False,
        demo_mode=True,
        persistent=False,
        auth_mode=AUTH_MODE_DEMO,
        auth_user_id=None,
        auth_email=None,
        account_plan=DEMO_ACCOUNT_PLAN,
        account_status=DEMO_ACCOUNT_STATUS,
        can_use_bigdata=False,
        source=source,
        ttl_seconds=ttl,
        expires_at=get_demo_expires_at(),
        warning=(
            "Du bist nicht eingeloggt und befindest dich im Demo-Modus. "
            "Änderungen werden nicht dauerhaft gespeichert und können nach "
            "Aktualisierung oder spätestens nach ca. 30 Minuten verloren gehen."
        ),
        capabilities={
            "can_use_bigdata": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "demo": True,
        },
        raw_auth={},
    )


def _external_context_from_headers(ensure: bool = True) -> Optional[CurrentUserContext]:
    auth_data = _request_auth_header_context()

    if not auth_data or not _safe_bool(auth_data.get("authenticated"), False):
        return None

    local_user = find_local_user_for_auth_identity(
        auth_user_id=auth_data.get("auth_user_id"),
        email=auth_data.get("email"),
        local_user_id=auth_data.get("local_user_id"),
    )

    if local_user is not None:
        return _context_from_user(
            local_user,
            auth_mode=AUTH_MODE_EXTERNAL,
            source=SOURCE_AUTH_HEADERS,
            authenticated=True,
            auth_data=auth_data,
        )

    # Authentifiziert, aber noch nicht lokal verknüpft.
    # Nicht automatisch AppUser erzeugen. Das übernimmt später ein expliziter
    # Login-Sync aus dem Auth-/Registrierungsdienst.
    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=_safe_str(auth_data.get("auth_user_id"), "auth_unlinked", 120),
        handle=_safe_str(auth_data.get("email") or auth_data.get("auth_user_id"), "auth_unlinked", 120),
        display_name=_safe_str(auth_data.get("display_name"), "Angemeldeter User", 160),
        email=_safe_str(auth_data.get("email"), "", 320) or None,
        role="user",
        locale=DEFAULT_USER_LOCALE,
        timezone=DEFAULT_USER_TIMEZONE,
        is_active=True,
        is_placeholder=False,
        is_system=False,
        authenticated=True,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_user_id=_safe_str(auth_data.get("auth_user_id"), "", 160) or None,
        auth_email=_safe_str(auth_data.get("email"), "", 320) or None,
        account_plan=_safe_str(auth_data.get("account_plan"), "", 80) or None,
        account_status=_safe_str(auth_data.get("account_status"), "", 80) or None,
        can_use_bigdata=_safe_bool(auth_data.get("can_use_bigdata"), False),
        source=SOURCE_AUTH_HEADERS,
        warning=(
            "Der User ist authentifiziert, aber noch nicht mit einem lokalen "
            "vectoplan-app AppUser verknüpft. Persistente Projektaktionen sind "
            "erst nach Login-Sync möglich."
        ),
        capabilities={
            "can_use_bigdata": _safe_bool(auth_data.get("can_use_bigdata"), False),
            "can_persist_projects": False,
            "can_manage_projects": False,
            "demo": False,
        },
        raw_auth=auth_data,
    )


def _dev_context(ensure: bool = True) -> CurrentUserContext:
    user_id = _request_user_id_for_diagnostics_only() or get_default_user_id()

    user = None

    try:
        if has_app_context() and AppUser is not None and db is not None:
            user = _db_get_user(user_id)
            if user is None and ensure:
                if user_id == get_default_user_id():
                    user = ensure_default_user()
                else:
                    # Diagnose-/Header-Override-User nur laden, nicht erzeugen.
                    user = None
    except Exception as exc:
        _log_exception("failed to load dev context user", exc, user_id=user_id)
        user = None

    if user is not None:
        return _context_from_user(
            user,
            auth_mode=AUTH_MODE_DEV,
            source=SOURCE_DB,
            authenticated=True,
            auth_data={
                "dev_placeholder": True,
                "auth_mode": AUTH_MODE_DEV,
            },
        )

    # Fallback ohne DB/AppContext.
    fallback_user_id = user_id or DEFAULT_USER_ID

    return CurrentUserContext(
        user_id=fallback_user_id,
        id=fallback_user_id,
        public_id=get_default_user_public_id() if fallback_user_id == DEFAULT_USER_ID else f"u_demo_{fallback_user_id}",
        handle=get_default_user_handle() if fallback_user_id == DEFAULT_USER_ID else f"demo_{fallback_user_id}",
        display_name=get_default_user_display_name(),
        email=None,
        role=get_default_user_role(),
        locale=DEFAULT_USER_LOCALE,
        timezone=DEFAULT_USER_TIMEZONE,
        is_active=True,
        is_placeholder=True,
        is_system=True,
        authenticated=True,
        demo_mode=False,
        persistent=True,
        auth_mode=AUTH_MODE_DEV,
        auth_user_id=None,
        auth_email=None,
        account_plan="dev",
        account_status="active",
        can_use_bigdata=False,
        source=SOURCE_FALLBACK,
        warning=(
            "Dev-Placeholder-Kontext ohne geladenen AppUser. "
            "In normalem App-Kontext sollte ensure_default_user() den User id=1 anlegen."
        ),
        capabilities={
            "can_use_bigdata": False,
            "can_persist_projects": True,
            "can_manage_projects": True,
            "demo": False,
        },
        raw_auth={"dev_placeholder": True},
    )


def _resolve_current_user_context(ensure: bool = True) -> CurrentUserContext:
    try:
        if is_requesting_demo_mode():
            return _demo_context()

        mode = get_auth_mode()

        if mode == AUTH_MODE_DEMO:
            return _demo_context()

        if mode == AUTH_MODE_EXTERNAL:
            external = _external_context_from_headers(ensure=ensure)
            if external is not None:
                return external

            return _demo_context(source="external_auth_missing")

        # Default/current behavior.
        return _dev_context(ensure=ensure)

    except Exception as exc:
        _log_exception("resolve current user context failed", exc)
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
            authenticated=True,
            demo_mode=False,
            persistent=True,
            auth_mode=AUTH_MODE_DEV,
            source=SOURCE_ERROR_FALLBACK,
            warning="Fallback-Kontext nach Fehler in current_user.py.",
            capabilities={
                "can_use_bigdata": False,
                "can_persist_projects": True,
                "can_manage_projects": True,
                "demo": False,
            },
        )


# ─────────────────────────────────────────────────────────────
# Current user ID resolution
# ─────────────────────────────────────────────────────────────

def get_current_user_id(
    *,
    allow_request_override: bool = False,
    fallback_to_default: bool = True,
) -> int:
    """
    Legacy-kompatibler Resolver.

    Standardverhalten:
    - gibt weiterhin id=1 im aktuellen Dev-Modus zurück.

    Für neue Auth-/Demo-sensible Logik besser verwenden:
    - get_current_user_context()
    - get_current_user_id_optional()
    """
    try:
        if allow_request_override:
            request_user_id = _request_user_id_for_diagnostics_only()
            if request_user_id:
                return int(request_user_id)

        context = get_current_user_context(ensure=False)
        if context.user_id:
            return int(context.user_id)

        if fallback_to_default:
            return get_default_user_id()

        return 0

    except Exception:
        return DEFAULT_USER_ID if fallback_to_default else 0


def get_current_user_id_optional(*, allow_request_override: bool = False) -> Optional[int]:
    """
    Auth-/Demo-sensibler Resolver.

    Gibt None zurück, wenn:
    - Demo-Modus aktiv ist,
    - externer Auth-User noch nicht lokal verknüpft ist,
    - kein persistenter AppUser verfügbar ist.
    """
    try:
        if allow_request_override:
            request_user_id = _request_user_id_for_diagnostics_only()
            if request_user_id:
                return request_user_id

        context = get_current_user_context(ensure=False)
        return context.user_id

    except Exception:
        return None


def set_current_user_id_on_g(user_id: Optional[int] = None) -> int:
    resolved = _safe_int(user_id, get_current_user_id()) or get_default_user_id()

    try:
        if has_request_context() and g is not None:
            setattr(g, CURRENT_USER_ID_G_KEY, resolved)
    except Exception:
        pass

    return int(resolved)


def set_current_user_context_on_g(context: Optional[CurrentUserContext] = None) -> CurrentUserContext:
    resolved = context or get_current_user_context()

    try:
        if has_request_context() and g is not None:
            setattr(g, CURRENT_CONTEXT_G_KEY, resolved)
            if resolved.user_id:
                setattr(g, CURRENT_USER_ID_G_KEY, resolved.user_id)
    except Exception:
        pass

    return resolved


def get_current_user_id_from_g_or_default() -> int:
    try:
        if has_request_context() and g is not None:
            value = getattr(g, CURRENT_USER_ID_G_KEY, None)
            if value:
                parsed = _safe_int(value, None)
                if parsed:
                    return int(parsed)
    except Exception:
        pass

    return get_current_user_id()


def get_current_user_id_from_g_or_none() -> Optional[int]:
    try:
        if has_request_context() and g is not None:
            value = getattr(g, CURRENT_USER_ID_G_KEY, None)
            if value:
                parsed = _safe_int(value, None)
                if parsed:
                    return parsed
    except Exception:
        pass

    return get_current_user_id_optional()


# ─────────────────────────────────────────────────────────────
# Current user loading
# ─────────────────────────────────────────────────────────────

def get_current_user(*, ensure: bool = True) -> Any:
    """
    Liefert lokalen AppUser, falls vorhanden.

    Demo-Modus:
    - gibt None zurück.

    Externer Auth-Modus ohne lokalen Link:
    - gibt None zurück.
    - erzeugt keinen AppUser.
    """
    if AppUser is None or db is None:
        return None

    try:
        if not has_app_context():
            return None

        context = get_current_user_context(ensure=ensure)

        if context.demo_mode:
            return None

        if not context.user_id:
            return None

        user = _db_get_user(context.user_id)

        if user is None and ensure and context.auth_mode == AUTH_MODE_DEV and context.user_id == get_default_user_id():
            user = ensure_default_user()

        return user

    except Exception as exc:
        _log_exception("get_current_user failed", exc)
        return None


def require_current_user() -> Any:
    """
    Erzwingt einen persistenten lokalen AppUser.

    Demo-Modus und nicht synchronisierte externe Auth-User werden abgelehnt.
    """
    context = get_current_user_context(ensure=True)

    if context.demo_mode:
        raise RuntimeError("current user unavailable: demo mode")

    if not context.persistent:
        raise RuntimeError("current user unavailable: non-persistent auth context")

    user = get_current_user(ensure=True)

    if user is None:
        raise RuntimeError("current user unavailable")

    return user


def get_current_user_context(*, ensure: bool = True) -> CurrentUserContext:
    try:
        if has_request_context() and g is not None:
            existing = getattr(g, CURRENT_CONTEXT_G_KEY, None)
            if isinstance(existing, CurrentUserContext):
                return existing

        context = _resolve_current_user_context(ensure=ensure)

        try:
            if has_request_context() and g is not None:
                setattr(g, CURRENT_CONTEXT_G_KEY, context)
                if context.user_id:
                    setattr(g, CURRENT_USER_ID_G_KEY, context.user_id)
        except Exception:
            pass

        return context

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
            authenticated=True,
            demo_mode=False,
            persistent=True,
            auth_mode=AUTH_MODE_DEV,
            source=SOURCE_ERROR_FALLBACK,
            warning="Fallback-Kontext nach Fehler.",
        )


def get_current_auth_context(*, ensure: bool = True) -> CurrentUserContext:
    """
    Alias für neue Services, die semantisch AuthContext erwarten.
    """
    return get_current_user_context(ensure=ensure)


def serialize_current_user(*, ensure: bool = True) -> Dict[str, Any]:
    try:
        context = get_current_user_context(ensure=ensure)

        user = None
        if context.user_id and context.persistent:
            user = get_current_user(ensure=ensure)

        if user is not None and hasattr(user, "to_public_dict"):
            try:
                user_payload = _safe_dict(user.to_public_dict())
                user_payload.update(
                    {
                        "auth": context.to_dict(),
                        "authenticated": context.authenticated,
                        "demo_mode": context.demo_mode,
                        "persistent": context.persistent,
                        "auth_mode": context.auth_mode,
                    }
                )
                return user_payload
            except Exception:
                pass

        if user is not None and hasattr(user, "to_dict"):
            try:
                user_payload = _safe_dict(user.to_dict(include_private=False))
            except TypeError:
                try:
                    user_payload = _safe_dict(user.to_dict())
                except Exception:
                    user_payload = {}
            except Exception:
                user_payload = {}

            if user_payload:
                user_payload.update(
                    {
                        "auth": context.to_dict(),
                        "authenticated": context.authenticated,
                        "demo_mode": context.demo_mode,
                        "persistent": context.persistent,
                        "auth_mode": context.auth_mode,
                    }
                )
                return user_payload

        return context.to_dict()

    except Exception:
        return CurrentUserContext(
            user_id=DEFAULT_USER_ID,
            id=DEFAULT_USER_ID,
        ).to_dict()


# ─────────────────────────────────────────────────────────────
# Permission/Auth convenience
# ─────────────────────────────────────────────────────────────

def is_current_user(user_id: Any) -> bool:
    try:
        current_id = get_current_user_id_optional()
        return bool(current_id and _safe_int(user_id, None) == current_id)
    except Exception:
        return False


def is_current_user_admin_placeholder() -> bool:
    try:
        context = get_current_user_context(ensure=False)
        return (
            context.auth_mode == AUTH_MODE_DEV
            and context.user_id == get_default_user_id() == DEFAULT_USER_ID
            and context.role == DEFAULT_USER_ROLE
        )
    except Exception:
        return True


def is_current_user_authenticated() -> bool:
    try:
        return bool(get_current_user_context(ensure=False).authenticated)
    except Exception:
        return False


def is_current_user_demo() -> bool:
    try:
        return bool(get_current_user_context(ensure=False).demo_mode)
    except Exception:
        return False


def current_user_can_persist() -> bool:
    try:
        context = get_current_user_context(ensure=False)
        return bool(context.persistent and not context.demo_mode and context.user_id)
    except Exception:
        return False


def current_user_can_use_bigdata() -> bool:
    try:
        context = get_current_user_context(ensure=False)
        return bool(context.can_use_bigdata)
    except Exception:
        return False


def require_persistent_current_user() -> CurrentUserContext:
    context = get_current_user_context(ensure=True)

    if context.demo_mode:
        raise RuntimeError("persistent user required: demo mode")

    if not context.authenticated:
        raise RuntimeError("persistent user required: not authenticated")

    if not context.persistent or not context.user_id:
        raise RuntimeError("persistent user required: local AppUser link missing")

    return context


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_current_user_status() -> Dict[str, Any]:
    try:
        context = get_current_user_context(ensure=False)

        db_user_exists = False

        try:
            if has_app_context() and AppUser is not None and db is not None and context.user_id:
                db_user_exists = _db_get_user(context.user_id) is not None
        except Exception:
            db_user_exists = False

        return {
            "ok": True,
            "phase": "auth-demo-prepared",
            "auth_mode": get_auth_mode(),
            "default_user_id": get_default_user_id(),
            "current_user": context.to_dict(),
            "db_user_exists": db_user_exists,
            "has_app_context": bool(has_app_context()),
            "has_request_context": bool(has_request_context()),
            "header_override_enabled": allow_user_header_override(),
            "auth_headers_trusted": auth_headers_trusted(),
            "demo_query_enabled": is_demo_query_enabled(),
            "demo_ttl_seconds": get_demo_ttl_seconds(),
            "model_available": AppUser is not None,
            "db_available": db is not None,
            "notes": {
                "dev_mode_default": "Aktuell bleibt VECTOPLAN_AUTH_MODE=dev kompatibel mit User id=1.",
                "demo_mode": "Nicht eingeloggte User sollen später Demo-Modus ohne Persistenz erhalten.",
                "external_auth": "Echter Login/Auth-Sync wird später über Auth-/Registrierungsdienst angebunden.",
                "no_account_creation": "Diese Datei erzeugt keine echten Accounts.",
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "phase": "auth-demo-prepared",
            "default_user_id": DEFAULT_USER_ID,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
            "model_available": AppUser is not None,
            "db_available": db is not None,
        }


def get_auth_context_status() -> Dict[str, Any]:
    return get_current_user_status()


# ─────────────────────────────────────────────────────────────
# Public exports
# ─────────────────────────────────────────────────────────────

__all__ = [
    "AUTH_MODE_DEMO",
    "AUTH_MODE_DEV",
    "AUTH_MODE_EXTERNAL",
    "CURRENT_CONTEXT_G_KEY",
    "CURRENT_USER_ID_G_KEY",
    "DEFAULT_USER_ID",
    "DEFAULT_USER_PUBLIC_ID",
    "DEFAULT_USER_HANDLE",
    "DEFAULT_USER_DISPLAY_NAME",
    "DEFAULT_USER_ROLE",
    "DEFAULT_USER_LOCALE",
    "DEFAULT_USER_TIMEZONE",
    "DEMO_PUBLIC_ID",
    "DEMO_HANDLE",
    "DEMO_DISPLAY_NAME",
    "DEMO_ROLE",
    "DEMO_ACCOUNT_PLAN",
    "DEMO_ACCOUNT_STATUS",
    "DEMO_TTL_SECONDS",
    "CurrentUserContext",
    "allow_user_header_override",
    "auth_headers_trusted",
    "current_user_can_persist",
    "current_user_can_use_bigdata",
    "ensure_default_user",
    "find_local_user_for_auth_identity",
    "get_auth_context_status",
    "get_auth_mode",
    "get_current_auth_context",
    "get_current_user",
    "get_current_user_context",
    "get_current_user_id",
    "get_current_user_id_from_g_or_default",
    "get_current_user_id_from_g_or_none",
    "get_current_user_id_optional",
    "get_current_user_status",
    "get_default_user_display_name",
    "get_default_user_handle",
    "get_default_user_id",
    "get_default_user_public_id",
    "get_default_user_role",
    "get_demo_expires_at",
    "get_demo_ttl_seconds",
    "is_current_user",
    "is_current_user_admin_placeholder",
    "is_current_user_authenticated",
    "is_current_user_demo",
    "is_demo_query_enabled",
    "is_requesting_demo_mode",
    "require_current_user",
    "require_persistent_current_user",
    "serialize_current_user",
    "set_current_user_context_on_g",
    "set_current_user_id_on_g",
]