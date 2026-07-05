# services/vectoplan-app/services/current_user.py
from __future__ import annotations

"""
VECTOPLAN current user service.

Zweck:
- Zentraler User-/Auth-Kontext für vectoplan-app.
- vectoplan-auth ist die kanonische Wahrheit für Loginstatus, Guest, Blocked/Banned,
  User-ID, Rollen, Account, Plan und Entitlements.
- vectoplan-app hält nur lokale AppUser-Links für eigene Foreign Keys:
  Project.owner_user_id, ProjectMembership.user_id, Audit usw.
- Nicht eingeloggte Guests erhalten nur Demo-Kontext ohne dauerhafte Persistenz.
- Auth-Service-unavailable fällt niemals auf Demo oder lokalen User zurück.
- Auth-Service-unavailable ist kein echter User-Ban, sondern ein eigener
  fail-closed Betriebszustand.

Wichtige Regeln:
- Keine lokale Default-User-ID.
- Kein lokaler Dev-Placeholder-User.
- Kein Fallback auf User id=1.
- Guest/ClientIdentity ist niemals ein User.
- Persistente Projektaktionen brauchen:
    authenticated=true aus vectoplan-auth
    user_blocked=false
    auth_unavailable=false
    auth user.id vorhanden
    lokaler AppUser-Link vorhanden
"""

import datetime as _dt
import json
import logging
import os
from dataclasses import dataclass, field, replace
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


try:
    from services.auth_context import (  # type: ignore
        AuthContext as PlatformAuthContext,
        context_auth_unavailable,
        context_denial_status_code,
        context_user_blocked,
        get_auth_context_status as get_platform_auth_context_status,
        get_current_auth_context as _load_platform_auth_context,
    )
except Exception:  # pragma: no cover
    try:
        from .auth_context import (  # type: ignore
            AuthContext as PlatformAuthContext,
            context_auth_unavailable,
            context_denial_status_code,
            context_user_blocked,
            get_auth_context_status as get_platform_auth_context_status,
            get_current_auth_context as _load_platform_auth_context,
        )
    except Exception:
        PlatformAuthContext = None  # type: ignore
        context_auth_unavailable = None  # type: ignore
        context_denial_status_code = None  # type: ignore
        context_user_blocked = None  # type: ignore
        get_platform_auth_context_status = None  # type: ignore
        _load_platform_auth_context = None  # type: ignore


try:
    from services.auth_dependency_service import get_auth_dependency_status  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .auth_dependency_service import get_auth_dependency_status  # type: ignore
    except Exception:
        get_auth_dependency_status = None  # type: ignore


try:
    from services.app_user_link_service import (  # type: ignore
        app_user_model_support_report,
        ensure_app_user_for_auth_context,
    )
except Exception:  # pragma: no cover
    try:
        from .app_user_link_service import (  # type: ignore
            app_user_model_support_report,
            ensure_app_user_for_auth_context,
        )
    except Exception:
        app_user_model_support_report = None  # type: ignore
        ensure_app_user_for_auth_context = None  # type: ignore


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

LOGGER_NAME = "vectoplan.current_user"

AUTH_MODE_EXTERNAL = "external"
AUTH_MODE_DEMO = "demo"

VALID_AUTH_MODES = {
    AUTH_MODE_EXTERNAL,
    AUTH_MODE_DEMO,
}

SOURCE_AUTH_SERVICE = "vectoplan_auth"
SOURCE_AUTH_SERVICE_BLOCKED = "vectoplan_auth_user_blocked"
SOURCE_AUTH_SERVICE_UNAVAILABLE = "vectoplan_auth_unavailable"
SOURCE_AUTH_SERVICE_ACCESS_BLOCKED = "vectoplan_auth_access_blocked"
SOURCE_AUTH_HEADERS = "auth_headers"
SOURCE_APP_USER_LINK = "app_user_link"
SOURCE_AUTH_UNLINKED = "auth_unlinked"
SOURCE_DEMO = "demo"
SOURCE_ANONYMOUS = "anonymous"
SOURCE_ERROR_UNAVAILABLE = "error_auth_unavailable"

CURRENT_CONTEXT_G_KEY = "vectoplan_current_user_context"
CURRENT_USER_ID_G_KEY = "vectoplan_user_id"
PLATFORM_AUTH_CONTEXT_G_KEY = "vectoplan_platform_auth_context"

ANONYMOUS_PUBLIC_ID = "anonymous"
ANONYMOUS_HANDLE = "anonymous"
ANONYMOUS_DISPLAY_NAME = "Nicht angemeldet"

DEMO_PUBLIC_ID = "demo_guest"
DEMO_HANDLE = "demo"
DEMO_DISPLAY_NAME = "Demo-Modus"
DEMO_ROLE = "guest"
DEMO_ACCOUNT_PLAN = "guest_demo"
DEMO_ACCOUNT_STATUS = "temporary"
DEMO_TTL_SECONDS = 3600

AUTH_UNAVAILABLE_PUBLIC_ID = "auth_unavailable"
AUTH_UNAVAILABLE_HANDLE = "auth_unavailable"
AUTH_UNAVAILABLE_DISPLAY_NAME = "Auth-Service nicht erreichbar"

BLOCKED_PUBLIC_ID = "blocked"
BLOCKED_HANDLE = "blocked"
BLOCKED_DISPLAY_NAME = "Gesperrter Benutzer"

DEFAULT_LOCALE = "de-DE"
DEFAULT_TIMEZONE = "Europe/Berlin"


# ─────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────

class CurrentUserAccessError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "current_user_access_denied",
        status_code: int = 403,
        context: Optional["CurrentUserContext"] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.context = context

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "status_code": self.status_code,
            "message": str(self),
            "auth": self.context.to_dict() if self.context is not None else None,
        }


# ─────────────────────────────────────────────────────────────
# Context object
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CurrentUserContext:
    """
    Einheitlicher User-/Auth-Kontext für vectoplan-app.

    user_id / id:
        Lokale AppUser.id, nur vorhanden, wenn ein echter Auth-User erfolgreich
        lokal verknüpft wurde.

    auth_user_id:
        Kanonische User-ID aus vectoplan-auth.

    persistent:
        True nur, wenn lokale App-Persistenz erlaubt ist.

    demo_mode:
        True nur für nicht eingeloggte Guests mit demo_project_access.

    auth_unavailable:
        True, wenn vectoplan-auth technisch nicht erreichbar/nutzbar ist.
        Kein User-Ban, aber fail-closed.

    user_blocked:
        True, wenn vectoplan-auth den User/Account wirklich als gesperrt meldet.

    access_blocked / blocked:
        Effektive Zugriffssperre für Guards/Legacy-Code.
    """

    user_id: Optional[int] = None
    id: Optional[int] = None

    public_id: Optional[str] = None
    handle: Optional[str] = None
    display_name: str = ANONYMOUS_DISPLAY_NAME
    email: Optional[str] = None
    role: str = "guest"
    locale: str = DEFAULT_LOCALE
    timezone: str = DEFAULT_TIMEZONE

    is_active: bool = False
    is_placeholder: bool = False
    is_system: bool = False

    authenticated: bool = False
    demo_mode: bool = False
    persistent: bool = False

    auth_mode: str = AUTH_MODE_EXTERNAL
    auth_state: str = "anonymous"
    auth_available: bool = False
    auth_unavailable: bool = False
    auth_user_id: Optional[str] = None
    auth_email: Optional[str] = None

    account_id: Optional[str] = None
    account_role: Optional[str] = None
    account_plan: Optional[str] = None
    account_status: Optional[str] = None

    roles: Tuple[str, ...] = field(default_factory=tuple)
    entitlements: Tuple[str, ...] = field(default_factory=tuple)

    blocked: bool = False
    blocked_reason: Optional[str] = None
    blocked_kind: Optional[str] = None
    user_blocked: bool = False
    access_blocked: bool = False
    denial_status_code: int = 403

    can_use_bigdata: bool = False
    can_use_cloud: bool = False
    can_demo: bool = False
    can_manage_account: bool = False
    can_manage_members: bool = False
    can_manage_api_keys: bool = False
    can_project_sharing: bool = False
    dashboard_allowed: bool = False

    source: str = SOURCE_AUTH_SERVICE

    ttl_seconds: Optional[int] = None
    expires_at: Optional[str] = None

    login_url: Optional[str] = None
    register_url: Optional[str] = None
    logout_url: Optional[str] = None
    account_dashboard_url: Optional[str] = None
    admin_dashboard_url: Optional[str] = None

    warning: Optional[str] = None
    capabilities: Dict[str, bool] = field(default_factory=dict)
    raw_auth: Dict[str, Any] = field(default_factory=dict)

    @property
    def effective_blocked(self) -> bool:
        return bool(self.blocked or self.access_blocked or self.user_blocked or self.auth_unavailable)

    def with_app_user_id(self, app_user_id: Optional[int]) -> "CurrentUserContext":
        parsed = _safe_int(app_user_id, None)
        return replace(
            self,
            user_id=parsed,
            id=parsed,
            persistent=bool(parsed and self.authenticated and not self.effective_blocked),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = {
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
            "authenticated": self.authenticated,
            "is_authenticated": self.authenticated,
            "demo_mode": self.demo_mode,
            "is_demo": self.demo_mode,
            "persistent": self.persistent,
            "auth_mode": self.auth_mode,
            "auth_state": self.auth_state,
            "auth_available": self.auth_available,
            "auth_unavailable": self.auth_unavailable,
            "auth_user_id": self.auth_user_id,
            "auth_email": self.auth_email,
            "account_id": self.account_id,
            "account_role": self.account_role,
            "account_plan": self.account_plan,
            "account_status": self.account_status,
            "roles": list(self.roles),
            "entitlements": list(self.entitlements),
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "blocked_kind": self.blocked_kind,
            "user_blocked": self.user_blocked,
            "access_blocked": self.access_blocked,
            "denial_status_code": self.denial_status_code,
            "can_use_bigdata": self.can_use_bigdata,
            "can_use_cloud": self.can_use_cloud,
            "can_demo": self.can_demo,
            "can_manage_account": self.can_manage_account,
            "can_manage_members": self.can_manage_members,
            "can_manage_api_keys": self.can_manage_api_keys,
            "can_project_sharing": self.can_project_sharing,
            "dashboard_allowed": self.dashboard_allowed,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "login_url": self.login_url,
            "register_url": self.register_url,
            "logout_url": self.logout_url,
            "account_dashboard_url": self.account_dashboard_url,
            "admin_dashboard_url": self.admin_dashboard_url,
            "warning": self.warning,
            "capabilities": dict(self.capabilities or {}),
            "raw_auth": dict(self.raw_auth or {}),
        }

        payload.update(
            {
                "userId": self.user_id,
                "publicId": self.public_id,
                "displayName": self.display_name,
                "isActive": self.is_active,
                "isPlaceholder": self.is_placeholder,
                "isSystem": self.is_system,
                "isAuthenticated": self.authenticated,
                "demoMode": self.demo_mode,
                "authMode": self.auth_mode,
                "authState": self.auth_state,
                "authAvailable": self.auth_available,
                "authUnavailable": self.auth_unavailable,
                "authUserId": self.auth_user_id,
                "authEmail": self.auth_email,
                "accountId": self.account_id,
                "accountRole": self.account_role,
                "accountPlan": self.account_plan,
                "accountStatus": self.account_status,
                "blockedReason": self.blocked_reason,
                "blockedKind": self.blocked_kind,
                "userBlocked": self.user_blocked,
                "accessBlocked": self.access_blocked,
                "denialStatusCode": self.denial_status_code,
                "canUseBigdata": self.can_use_bigdata,
                "canUseCloud": self.can_use_cloud,
                "canDemo": self.can_demo,
                "canManageAccount": self.can_manage_account,
                "canManageMembers": self.can_manage_members,
                "canManageApiKeys": self.can_manage_api_keys,
                "canProjectSharing": self.can_project_sharing,
                "dashboardAllowed": self.dashboard_allowed,
                "ttlSeconds": self.ttl_seconds,
                "expiresAt": self.expires_at,
                "loginUrl": self.login_url,
                "registerUrl": self.register_url,
                "logoutUrl": self.logout_url,
                "accountDashboardUrl": self.account_dashboard_url,
                "adminDashboardUrl": self.admin_dashboard_url,
            }
        )

        return payload

    def to_app_config(self) -> Dict[str, Any]:
        return {
            "userId": self.user_id,
            "publicId": self.public_id,
            "displayName": self.display_name,
            "email": self.email,
            "role": self.role,
            "authenticated": self.authenticated,
            "demo": self.demo_mode,
            "persistent": self.persistent,
            "authMode": self.auth_mode,
            "authState": self.auth_state,
            "authAvailable": self.auth_available,
            "authUnavailable": self.auth_unavailable,
            "authUserId": self.auth_user_id,
            "accountId": self.account_id,
            "accountRole": self.account_role,
            "plan": self.account_plan,
            "roles": list(self.roles),
            "entitlements": list(self.entitlements),
            "blocked": self.blocked,
            "blockedReason": self.blocked_reason,
            "blockedKind": self.blocked_kind,
            "userBlocked": self.user_blocked,
            "accessBlocked": self.access_blocked,
            "denialStatusCode": self.denial_status_code,
            "canUseCloud": self.can_use_cloud,
            "canDemo": self.can_demo,
            "canPersist": self.persistent,
            "canManageAccount": self.can_manage_account,
            "canManageMembers": self.can_manage_members,
            "canManageApiKeys": self.can_manage_api_keys,
            "canProjectSharing": self.can_project_sharing,
            "dashboardAllowed": self.dashboard_allowed,
            "links": {
                "login": self.login_url,
                "register": self.register_url,
                "logout": self.logout_url,
                "accountDashboard": self.account_dashboard_url,
                "adminDashboard": self.admin_dashboard_url,
            },
            "ttlSeconds": self.ttl_seconds,
            "expiresAt": self.expires_at,
            "warning": self.warning,
        }


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _utcnow() -> _dt.datetime:
    try:
        return _dt.datetime.now(_dt.timezone.utc)
    except Exception:
        return _dt.datetime.utcnow()


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
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
        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable", "active", "ok"}:
            return True
        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable", "inactive", "error", "failed"}:
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
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
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


def _tuple_lower(values: Any) -> Tuple[str, ...]:
    try:
        if values is None:
            return tuple()
        if isinstance(values, str):
            raw = [item.strip() for item in values.split(",") if item.strip()]
        else:
            raw = list(values)
        return tuple(dict.fromkeys(_safe_str(value, "", 120).lower() for value in raw if _safe_str(value, "", 120)))
    except Exception:
        return tuple()


def _tuple_text(values: Any) -> Tuple[str, ...]:
    try:
        if values is None:
            return tuple()
        if isinstance(values, str):
            raw = [item.strip() for item in values.split(",") if item.strip()]
        else:
            raw = list(values)
        return tuple(dict.fromkeys(_safe_str(value, "", 160) for value in raw if _safe_str(value, "", 160)))
    except Exception:
        return tuple()


def _db_get_user(user_id: Optional[int]) -> Any:
    if AppUser is None or db is None or not user_id:
        return None

    try:
        if hasattr(db.session, "get"):
            return db.session.get(AppUser, user_id)
        return AppUser.query.get(user_id)
    except Exception:
        raise


def _context_attr(context: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(context, name, default)
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Auth/Demo configuration
# ─────────────────────────────────────────────────────────────

def get_auth_mode() -> str:
    """
    Betriebsmodus.

    Default:
        external

    Kein Dev-/Default-User-Modus.
    """
    try:
        configured = _safe_str(
            _config_value("VECTOPLAN_AUTH_MODE", AUTH_MODE_EXTERNAL),
            AUTH_MODE_EXTERNAL,
            40,
        ).lower()

        if configured in {"demo", "guest", "anonymous"}:
            return AUTH_MODE_DEMO

        return AUTH_MODE_EXTERNAL
    except Exception:
        return AUTH_MODE_EXTERNAL


def is_external_auth_enabled() -> bool:
    return get_auth_mode() == AUTH_MODE_EXTERNAL


def is_demo_query_enabled() -> bool:
    return _safe_bool(_config_value("VECTOPLAN_ALLOW_DEMO_QUERY_PARAM", False), False)


def get_demo_ttl_seconds() -> int:
    try:
        configured = _safe_int(
            _config_value(
                "VECTOPLAN_DEMO_PROJECT_TTL_SECONDS",
                _config_value("VECTOPLAN_DEMO_TTL_SECONDS", DEMO_TTL_SECONDS),
            ),
            DEMO_TTL_SECONDS,
        )
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
    """
    Nur UI-/Diagnose-Signal.

    Im normalen external-Modus entscheidet ausschließlich vectoplan-auth,
    ob demo_project_access vorhanden ist.
    """
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
    try:
        return _safe_bool(
            _config_value(
                "VECTOPLAN_AUTH_TRUSTED_GATEWAY_HEADERS",
                _config_value("VECTOPLAN_TRUST_AUTH_HEADERS", False),
            ),
            False,
        )
    except Exception:
        return False


def allow_user_header_override() -> bool:
    return False


def legacy_default_user_fallback_enabled() -> bool:
    return False


# ─────────────────────────────────────────────────────────────
# Trusted gateway compatibility
# ─────────────────────────────────────────────────────────────

def _request_auth_header_context() -> Dict[str, Any]:
    """
    Liest vertrauenswürdige Auth-Gateway-Header.

    Nur aktiv, wenn VECTOPLAN_AUTH_TRUSTED_GATEWAY_HEADERS=true.
    Public Clients dürfen diese Header nicht setzen.
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
            request.headers.get("X-VECTOPLAN-USER-ID")
            or request.headers.get("X-VECTOPLAN-AUTH-USER-ID")
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

        account_id = _safe_str(
            request.headers.get("X-VECTOPLAN-ACCOUNT-ID")
            or request.headers.get("X-Account-ID"),
            "",
            160,
        )

        account_plan = _safe_str(
            request.headers.get("X-VECTOPLAN-PLAN")
            or request.headers.get("X-VECTOPLAN-ACCOUNT-PLAN")
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

        roles = _tuple_lower(
            request.headers.get("X-VECTOPLAN-ROLES")
            or request.headers.get("X-Roles")
        )

        entitlements = _tuple_text(
            request.headers.get("X-VECTOPLAN-ENTITLEMENTS")
            or request.headers.get("X-Entitlements")
        )

        blocked = _safe_bool(
            request.headers.get("X-VECTOPLAN-BLOCKED")
            or request.headers.get("X-Blocked"),
            default=False,
        )

        if auth_user_id or email:
            authenticated = True

        return {
            "authenticated": authenticated,
            "blocked": blocked,
            "user_blocked": blocked,
            "auth_unavailable": False,
            "access_blocked": blocked,
            "auth_user_id": auth_user_id or None,
            "email": email or None,
            "display_name": display_name or None,
            "account_id": account_id or None,
            "account_plan": account_plan or None,
            "account_status": account_status or None,
            "roles": roles,
            "entitlements": entitlements,
            "source": SOURCE_AUTH_HEADERS,
        }

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# Context conversion
# ─────────────────────────────────────────────────────────────

def _auth_data_from_platform_context(context: Any) -> Dict[str, Any]:
    if context is None:
        return {}

    try:
        public = context.to_public_dict(include_raw=False)
    except Exception:
        public = {}

    try:
        app_config = context.to_app_config()
    except Exception:
        app_config = {}

    links = _safe_dict(public.get("links")) or _safe_dict(app_config.get("links"))

    account_obj = getattr(context, "account", None)
    access_obj = getattr(context, "access", None)

    auth_unavailable = _safe_bool(getattr(context, "auth_unavailable", False), False)
    user_blocked = _safe_bool(getattr(context, "user_blocked", False), False)
    access_blocked = _safe_bool(getattr(context, "access_blocked", False), False) or auth_unavailable or user_blocked

    return {
        "authenticated": bool(getattr(context, "authenticated", False)),
        "auth_available": bool(getattr(context, "auth_available", False)),
        "auth_unavailable": auth_unavailable,
        "auth_state": _safe_str(getattr(context, "auth_state", "anonymous"), "anonymous", 80),
        "auth_user_id": _safe_str(getattr(context, "user_id", None), "", 160) or None,
        "email": _safe_str(getattr(context, "email", None), "", 320) or None,
        "display_name": _safe_str(getattr(context, "display_name", None), "", 160) or None,
        "account_id": _safe_str(getattr(context, "account_id", None), "", 160) or None,
        "account_role": _safe_str(getattr(account_obj, "member_role", None), "", 80) or None,
        "account_plan": _safe_str(getattr(context, "plan", None), "", 80) or None,
        "account_status": _safe_str(getattr(access_obj, "plan_status", None), "", 80) or None,
        "roles": tuple(getattr(context, "roles", tuple()) or tuple()),
        "entitlements": tuple(getattr(access_obj, "entitlements", tuple()) or tuple()),
        "blocked": bool(getattr(context, "blocked", False)),
        "blocked_reason": _safe_str(getattr(context, "blocked_reason", None), "", 160) or None,
        "blocked_kind": _safe_str(getattr(context, "blocked_kind", None), "", 80) or None,
        "user_blocked": user_blocked,
        "access_blocked": access_blocked,
        "denial_status_code": _safe_int(getattr(context, "denial_status_code", None), 503 if auth_unavailable else 403),
        "can_use_cloud": bool(getattr(context, "can_use_cloud", False)),
        "can_use_bigdata": bool(getattr(context, "can_use_bigdata", False)) if hasattr(context, "can_use_bigdata") else False,
        "can_demo": bool(getattr(context, "can_demo", False)),
        "can_manage_account": bool(getattr(context, "can_manage_account", False)),
        "can_manage_members": bool(getattr(context, "can_manage_members", False)),
        "can_manage_api_keys": bool(getattr(context, "can_manage_api_keys", False)),
        "can_project_sharing": bool(getattr(context, "can_project_sharing", False)),
        "dashboard_allowed": bool(getattr(context, "dashboard_allowed", False)),
        "login_url": _safe_str(links.get("login_url") or links.get("login"), "", 500) or getattr(context, "login_url", None),
        "register_url": _safe_str(links.get("register_url") or links.get("register"), "", 500) or getattr(context, "register_url", None),
        "logout_url": _safe_str(links.get("logout_url") or links.get("logout"), "", 500) or getattr(context, "logout_url", None),
        "account_dashboard_url": _safe_str(
            links.get("account_dashboard_url") or links.get("accountDashboard"),
            "",
            500,
        ) or getattr(context, "account_dashboard_url", None),
        "admin_dashboard_url": _safe_str(
            links.get("admin_dashboard_url") or links.get("adminDashboard"),
            "",
            500,
        ) or getattr(context, "admin_dashboard_url", None),
        "raw": public,
    }


def _context_from_app_user(
    app_user: Any,
    platform_context: Any,
    *,
    source: str = SOURCE_APP_USER_LINK,
) -> CurrentUserContext:
    auth = _auth_data_from_platform_context(platform_context)

    local_user_id = _safe_int(getattr(app_user, "id", None), None)
    auth_unavailable = _safe_bool(auth.get("auth_unavailable"), False)
    user_blocked = _safe_bool(auth.get("user_blocked"), False)
    access_blocked = _safe_bool(auth.get("access_blocked"), False) or auth_unavailable or user_blocked

    roles = _tuple_lower(auth.get("roles"))
    entitlements = _tuple_text(auth.get("entitlements"))

    capabilities = {
        "can_use_bigdata": _safe_bool(auth.get("can_use_bigdata"), False),
        "can_use_cloud": _safe_bool(auth.get("can_use_cloud"), False),
        "can_persist_projects": bool(local_user_id and not access_blocked),
        "can_manage_projects": bool(local_user_id and not access_blocked),
        "can_manage_account": _safe_bool(auth.get("can_manage_account"), False) and not access_blocked,
        "can_manage_members": _safe_bool(auth.get("can_manage_members"), False) and not access_blocked,
        "can_manage_api_keys": _safe_bool(auth.get("can_manage_api_keys"), False) and not access_blocked,
        "can_project_sharing": _safe_bool(auth.get("can_project_sharing"), False) and not access_blocked,
        "demo": False,
    }

    display_name = (
        _safe_str(auth.get("display_name"), "", 160)
        or _safe_str(getattr(app_user, "display_name", None), "", 160)
        or _safe_str(auth.get("email"), "", 160)
        or "User"
    )

    return CurrentUserContext(
        user_id=local_user_id if not access_blocked else None,
        id=local_user_id if not access_blocked else None,
        public_id=_safe_str(getattr(app_user, "public_id", None), "", 120) or None,
        handle=_safe_str(getattr(app_user, "handle", None), "", 120) or _safe_str(getattr(app_user, "username", None), "", 120) or None,
        display_name=display_name,
        email=_safe_str(auth.get("email") or getattr(app_user, "email", None), "", 320) or None,
        role=_safe_str(getattr(app_user, "role", None), "user", 40),
        locale=_safe_str(getattr(app_user, "locale", None), DEFAULT_LOCALE, 40),
        timezone=_safe_str(getattr(app_user, "timezone", None), DEFAULT_TIMEZONE, 80),
        is_active=_safe_bool(getattr(app_user, "is_active", True), True) and not access_blocked,
        is_placeholder=False,
        is_system=_safe_bool(getattr(app_user, "is_system", False), False),
        authenticated=bool(local_user_id and not access_blocked),
        demo_mode=False,
        persistent=bool(local_user_id and not access_blocked),
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=_safe_str(auth.get("auth_state"), "authenticated", 80),
        auth_available=_safe_bool(auth.get("auth_available"), not auth_unavailable),
        auth_unavailable=auth_unavailable,
        auth_user_id=_safe_str(auth.get("auth_user_id"), "", 160) or None,
        auth_email=_safe_str(auth.get("email"), "", 320) or None,
        account_id=_safe_str(auth.get("account_id"), "", 160) or None,
        account_role=_safe_str(auth.get("account_role"), "", 80) or None,
        account_plan=_safe_str(auth.get("account_plan"), "", 80) or None,
        account_status=_safe_str(auth.get("account_status"), "", 80) or None,
        roles=roles,
        entitlements=entitlements,
        blocked=access_blocked,
        blocked_reason=_safe_str(auth.get("blocked_reason"), "", 160) or None,
        blocked_kind=_safe_str(auth.get("blocked_kind"), "", 80) or ("auth_unavailable" if auth_unavailable else "user_blocked" if user_blocked else None),
        user_blocked=user_blocked,
        access_blocked=access_blocked,
        denial_status_code=503 if auth_unavailable else 403 if access_blocked else 200,
        can_use_bigdata=capabilities["can_use_bigdata"],
        can_use_cloud=capabilities["can_use_cloud"],
        can_demo=False,
        can_manage_account=capabilities["can_manage_account"],
        can_manage_members=capabilities["can_manage_members"],
        can_manage_api_keys=capabilities["can_manage_api_keys"],
        can_project_sharing=capabilities["can_project_sharing"],
        dashboard_allowed=_safe_bool(auth.get("dashboard_allowed"), False) and not access_blocked,
        source=source,
        login_url=_safe_str(auth.get("login_url"), "", 500) or None,
        register_url=_safe_str(auth.get("register_url"), "", 500) or None,
        logout_url=_safe_str(auth.get("logout_url"), "", 500) or None,
        account_dashboard_url=_safe_str(auth.get("account_dashboard_url"), "", 500) or None,
        admin_dashboard_url=_safe_str(auth.get("admin_dashboard_url"), "", 500) or None,
        capabilities=capabilities,
        raw_auth=auth,
    )


def _demo_context(source: str = SOURCE_DEMO, platform_context: Optional[Any] = None) -> CurrentUserContext:
    ttl = get_demo_ttl_seconds()

    raw_auth = {}
    login_url = None
    register_url = None
    account_dashboard_url = None
    admin_dashboard_url = None
    logout_url = None
    auth_state = "guest"
    entitlements = ("demo_project_access",)

    if platform_context is not None:
        raw_auth = _auth_data_from_platform_context(platform_context)

        if _safe_bool(raw_auth.get("auth_unavailable"), False) or _safe_bool(raw_auth.get("access_blocked"), False):
            return _auth_unavailable_context(platform_context)

        login_url = raw_auth.get("login_url")
        register_url = raw_auth.get("register_url")
        logout_url = raw_auth.get("logout_url")
        account_dashboard_url = raw_auth.get("account_dashboard_url")
        admin_dashboard_url = raw_auth.get("admin_dashboard_url")
        auth_state = _safe_str(getattr(platform_context, "auth_state", "guest"), "guest", 80)
        entitlements = tuple(getattr(getattr(platform_context, "access", None), "entitlements", entitlements) or entitlements)

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=DEMO_PUBLIC_ID,
        handle=DEMO_HANDLE,
        display_name=DEMO_DISPLAY_NAME,
        email=None,
        role=DEMO_ROLE,
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=True,
        is_placeholder=True,
        is_system=False,
        authenticated=False,
        demo_mode=True,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=auth_state,
        auth_available=True,
        auth_unavailable=False,
        auth_user_id=None,
        auth_email=None,
        account_id=None,
        account_role=None,
        account_plan=DEMO_ACCOUNT_PLAN,
        account_status=DEMO_ACCOUNT_STATUS,
        roles=("guest",),
        entitlements=entitlements,
        blocked=False,
        blocked_reason=None,
        blocked_kind=None,
        user_blocked=False,
        access_blocked=False,
        denial_status_code=200,
        can_use_bigdata=False,
        can_use_cloud=False,
        can_demo=True,
        can_manage_account=False,
        can_manage_members=False,
        can_manage_api_keys=False,
        can_project_sharing=False,
        dashboard_allowed=False,
        source=source,
        ttl_seconds=ttl,
        expires_at=get_demo_expires_at(),
        login_url=login_url,
        register_url=register_url,
        logout_url=logout_url,
        account_dashboard_url=account_dashboard_url,
        admin_dashboard_url=admin_dashboard_url,
        warning=(
            "Du bist nicht eingeloggt und befindest dich im Demo-Modus. "
            f"Änderungen werden temporär gespeichert und nach ca. {max(1, int(ttl / 60))} Minuten gelöscht."
        ),
        capabilities={
            "can_use_bigdata": False,
            "can_use_cloud": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "can_manage_account": False,
            "can_manage_members": False,
            "can_manage_api_keys": False,
            "can_project_sharing": False,
            "demo": True,
        },
        raw_auth=raw_auth,
    )


def _auth_unavailable_context(platform_context: Optional[Any] = None, *, source: str = SOURCE_AUTH_SERVICE_UNAVAILABLE) -> CurrentUserContext:
    raw_auth = _auth_data_from_platform_context(platform_context)
    auth_state = _safe_str(raw_auth.get("auth_state"), "service_unavailable", 80)
    blocked_reason = _safe_str(raw_auth.get("blocked_reason"), "", 160) or auth_state or "service_unavailable"

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=AUTH_UNAVAILABLE_PUBLIC_ID,
        handle=AUTH_UNAVAILABLE_HANDLE,
        display_name=AUTH_UNAVAILABLE_DISPLAY_NAME,
        email=None,
        role="unavailable",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=False,
        is_placeholder=False,
        is_system=False,
        authenticated=False,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=auth_state,
        auth_available=False,
        auth_unavailable=True,
        auth_user_id=None,
        auth_email=None,
        account_id=None,
        account_role=None,
        account_plan=None,
        account_status=None,
        roles=("guest",),
        entitlements=tuple(),
        blocked=True,
        blocked_reason=blocked_reason,
        blocked_kind="auth_unavailable",
        user_blocked=False,
        access_blocked=True,
        denial_status_code=503,
        can_use_bigdata=False,
        can_use_cloud=False,
        can_demo=False,
        can_manage_account=False,
        can_manage_members=False,
        can_manage_api_keys=False,
        can_project_sharing=False,
        dashboard_allowed=False,
        source=source,
        login_url=raw_auth.get("login_url"),
        register_url=raw_auth.get("register_url"),
        logout_url=raw_auth.get("logout_url"),
        account_dashboard_url=raw_auth.get("account_dashboard_url"),
        admin_dashboard_url=raw_auth.get("admin_dashboard_url"),
        warning="vectoplan-auth ist nicht erreichbar. Zugriff wird aus Sicherheitsgründen vorübergehend gesperrt.",
        capabilities={
            "can_use_bigdata": False,
            "can_use_cloud": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "can_manage_account": False,
            "can_manage_members": False,
            "can_manage_api_keys": False,
            "can_project_sharing": False,
            "demo": False,
            "auth_unavailable": True,
        },
        raw_auth=raw_auth,
    )


def _blocked_context(platform_context: Optional[Any] = None, *, source: str = SOURCE_AUTH_SERVICE_BLOCKED) -> CurrentUserContext:
    raw_auth = _auth_data_from_platform_context(platform_context)

    if _safe_bool(raw_auth.get("auth_unavailable"), False):
        return _auth_unavailable_context(platform_context, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

    auth_state = _safe_str(raw_auth.get("auth_state"), "blocked", 80)
    blocked_reason = _safe_str(raw_auth.get("blocked_reason"), "", 160) or auth_state or "blocked"

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=BLOCKED_PUBLIC_ID,
        handle=BLOCKED_HANDLE,
        display_name=BLOCKED_DISPLAY_NAME,
        email=raw_auth.get("email"),
        role="blocked",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=False,
        is_placeholder=False,
        is_system=False,
        authenticated=False,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=auth_state,
        auth_available=True,
        auth_unavailable=False,
        auth_user_id=raw_auth.get("auth_user_id"),
        auth_email=raw_auth.get("email"),
        account_id=raw_auth.get("account_id"),
        account_role=raw_auth.get("account_role"),
        account_plan=raw_auth.get("account_plan"),
        account_status=raw_auth.get("account_status"),
        roles=_tuple_lower(raw_auth.get("roles")),
        entitlements=_tuple_text(raw_auth.get("entitlements")),
        blocked=True,
        blocked_reason=blocked_reason,
        blocked_kind="user_blocked",
        user_blocked=True,
        access_blocked=True,
        denial_status_code=403,
        can_use_bigdata=False,
        can_use_cloud=False,
        can_demo=False,
        can_manage_account=False,
        can_manage_members=False,
        can_manage_api_keys=False,
        can_project_sharing=False,
        dashboard_allowed=False,
        source=source,
        login_url=raw_auth.get("login_url"),
        register_url=raw_auth.get("register_url"),
        logout_url=raw_auth.get("logout_url"),
        account_dashboard_url=raw_auth.get("account_dashboard_url"),
        admin_dashboard_url=raw_auth.get("admin_dashboard_url"),
        warning="Dieser Zugang ist gesperrt.",
        capabilities={
            "can_use_bigdata": False,
            "can_use_cloud": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "can_manage_account": False,
            "can_manage_members": False,
            "can_manage_api_keys": False,
            "can_project_sharing": False,
            "demo": False,
            "user_blocked": True,
        },
        raw_auth=raw_auth,
    )


def _access_blocked_context(platform_context: Optional[Any] = None, *, source: str = SOURCE_AUTH_SERVICE_ACCESS_BLOCKED) -> CurrentUserContext:
    raw_auth = _auth_data_from_platform_context(platform_context)

    if _safe_bool(raw_auth.get("auth_unavailable"), False):
        return _auth_unavailable_context(platform_context, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

    if _safe_bool(raw_auth.get("user_blocked"), False):
        return _blocked_context(platform_context, source=SOURCE_AUTH_SERVICE_BLOCKED)

    auth_state = _safe_str(raw_auth.get("auth_state"), "access_blocked", 80)
    blocked_reason = _safe_str(raw_auth.get("blocked_reason"), "", 160) or auth_state or "access_blocked"

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id="access_blocked",
        handle="access_blocked",
        display_name="Zugriff gesperrt",
        email=raw_auth.get("email"),
        role="blocked",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=False,
        is_placeholder=False,
        is_system=False,
        authenticated=False,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=auth_state,
        auth_available=True,
        auth_unavailable=False,
        auth_user_id=raw_auth.get("auth_user_id"),
        auth_email=raw_auth.get("email"),
        account_id=raw_auth.get("account_id"),
        account_role=raw_auth.get("account_role"),
        account_plan=raw_auth.get("account_plan"),
        account_status=raw_auth.get("account_status"),
        roles=_tuple_lower(raw_auth.get("roles")),
        entitlements=_tuple_text(raw_auth.get("entitlements")),
        blocked=True,
        blocked_reason=blocked_reason,
        blocked_kind="access_blocked",
        user_blocked=False,
        access_blocked=True,
        denial_status_code=403,
        can_use_bigdata=False,
        can_use_cloud=False,
        can_demo=False,
        can_manage_account=False,
        can_manage_members=False,
        can_manage_api_keys=False,
        can_project_sharing=False,
        dashboard_allowed=False,
        source=source,
        login_url=raw_auth.get("login_url"),
        register_url=raw_auth.get("register_url"),
        logout_url=raw_auth.get("logout_url"),
        account_dashboard_url=raw_auth.get("account_dashboard_url"),
        admin_dashboard_url=raw_auth.get("admin_dashboard_url"),
        warning="Der Zugriff ist gesperrt.",
        capabilities={
            "can_use_bigdata": False,
            "can_use_cloud": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "can_manage_account": False,
            "can_manage_members": False,
            "can_manage_api_keys": False,
            "can_project_sharing": False,
            "demo": False,
            "access_blocked": True,
        },
        raw_auth=raw_auth,
    )


def _anonymous_context(platform_context: Optional[Any] = None, *, source: str = SOURCE_ANONYMOUS) -> CurrentUserContext:
    raw_auth = _auth_data_from_platform_context(platform_context)

    if _safe_bool(raw_auth.get("auth_unavailable"), False):
        return _auth_unavailable_context(platform_context, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=ANONYMOUS_PUBLIC_ID,
        handle=ANONYMOUS_HANDLE,
        display_name=ANONYMOUS_DISPLAY_NAME,
        email=None,
        role="guest",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=True,
        is_placeholder=True,
        is_system=False,
        authenticated=False,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state=_safe_str(raw_auth.get("auth_state"), "anonymous", 80),
        auth_available=True,
        auth_unavailable=False,
        account_plan=raw_auth.get("account_plan"),
        roles=("guest",),
        entitlements=_tuple_text(raw_auth.get("entitlements")),
        blocked=False,
        blocked_reason=None,
        blocked_kind=None,
        user_blocked=False,
        access_blocked=False,
        denial_status_code=401,
        can_demo=False,
        source=source,
        login_url=raw_auth.get("login_url"),
        register_url=raw_auth.get("register_url"),
        logout_url=raw_auth.get("logout_url"),
        account_dashboard_url=raw_auth.get("account_dashboard_url"),
        admin_dashboard_url=raw_auth.get("admin_dashboard_url"),
        warning="Nicht angemeldet.",
        capabilities={
            "can_use_bigdata": False,
            "can_use_cloud": False,
            "can_persist_projects": False,
            "can_manage_projects": False,
            "demo": False,
        },
        raw_auth=raw_auth,
    )


# ─────────────────────────────────────────────────────────────
# Context resolvers
# ─────────────────────────────────────────────────────────────

def _platform_auth_context(*, force_refresh: bool = False) -> Any:
    if _load_platform_auth_context is None:
        return None

    try:
        if has_request_context() and g is not None and not force_refresh:
            existing = getattr(g, PLATFORM_AUTH_CONTEXT_G_KEY, None)
            if existing is not None:
                return existing

        context = _load_platform_auth_context(
            minimal=True,
            use_cache=True,
            force_refresh=force_refresh,
        )

        try:
            if has_request_context() and g is not None:
                setattr(g, PLATFORM_AUTH_CONTEXT_G_KEY, context)
        except Exception:
            pass

        return context

    except Exception as exc:
        _log_exception("loading vectoplan-auth context failed", exc)
        return None


def _external_context_from_headers() -> Optional[CurrentUserContext]:
    if not auth_headers_trusted():
        return None

    auth_data = _request_auth_header_context()
    if not auth_data:
        return None

    if _safe_bool(auth_data.get("blocked"), False):
        return _blocked_context(None, source=SOURCE_AUTH_HEADERS)

    if not _safe_bool(auth_data.get("authenticated"), False):
        return None

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=SOURCE_AUTH_HEADERS,
        handle=_safe_str(auth_data.get("email") or auth_data.get("auth_user_id"), "auth_headers", 120),
        display_name=_safe_str(auth_data.get("display_name"), "Angemeldeter User", 160),
        email=_safe_str(auth_data.get("email"), "", 320) or None,
        role="user",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=True,
        is_placeholder=False,
        is_system=False,
        authenticated=True,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state="authenticated_gateway_unlinked",
        auth_available=True,
        auth_unavailable=False,
        auth_user_id=_safe_str(auth_data.get("auth_user_id"), "", 160) or None,
        auth_email=_safe_str(auth_data.get("email"), "", 320) or None,
        account_id=_safe_str(auth_data.get("account_id"), "", 160) or None,
        account_plan=_safe_str(auth_data.get("account_plan"), "", 80) or None,
        account_status=_safe_str(auth_data.get("account_status"), "", 80) or None,
        roles=_tuple_lower(auth_data.get("roles") or ("user",)),
        entitlements=_tuple_text(auth_data.get("entitlements")),
        user_blocked=False,
        access_blocked=False,
        blocked=False,
        denial_status_code=403,
        can_use_cloud="cloud_access" in _tuple_text(auth_data.get("entitlements")),
        source=SOURCE_AUTH_HEADERS,
        warning="Trusted gateway headers authenticated the user, but no local AppUser link was created here.",
        raw_auth=auth_data,
    )


def _external_context_from_auth_service() -> CurrentUserContext:
    header_context = _external_context_from_headers()
    if header_context is not None:
        return header_context

    platform_context = _platform_auth_context()

    if platform_context is None:
        return _auth_unavailable_context(None, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

    raw_auth = _auth_data_from_platform_context(platform_context)

    if _safe_bool(raw_auth.get("auth_unavailable"), False):
        return _auth_unavailable_context(platform_context, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

    if _safe_bool(raw_auth.get("user_blocked"), False):
        return _blocked_context(platform_context, source=SOURCE_AUTH_SERVICE_BLOCKED)

    if _safe_bool(raw_auth.get("access_blocked"), False):
        return _access_blocked_context(platform_context, source=SOURCE_AUTH_SERVICE_ACCESS_BLOCKED)

    if getattr(platform_context, "authenticated", False):
        if ensure_app_user_for_auth_context is None:
            return _authenticated_unlinked_context(
                platform_context,
                warning="AppUser-Link-Service ist nicht verfügbar. Persistente Projektaktionen sind gesperrt.",
            )

        try:
            link_result = ensure_app_user_for_auth_context(
                context=platform_context,
                create=True,
                update=True,
                commit=True,
                use_cache=True,
            )
        except Exception as exc:
            raw_auth = _auth_data_from_platform_context(platform_context)
            raw_auth["app_user_link_error"] = f"{exc.__class__.__name__}: {exc}"
            return _authenticated_unlinked_context(
                platform_context,
                raw_patch=raw_auth,
                warning=(
                    "Der User ist laut vectoplan-auth authentifiziert, aber der lokale AppUser-Link "
                    "konnte wegen eines Fehlers nicht erstellt werden. Persistente Projektaktionen sind gesperrt."
                ),
            )

        if getattr(link_result, "ok", False) and getattr(link_result, "app_user", None) is not None:
            return _context_from_app_user(
                link_result.app_user,
                platform_context,
                source=SOURCE_APP_USER_LINK,
            )

        raw_patch = _auth_data_from_platform_context(platform_context)
        try:
            raw_patch["app_user_link"] = link_result.to_dict(include_context=False) if hasattr(link_result, "to_dict") else {}
        except Exception:
            raw_patch["app_user_link"] = {}

        return _authenticated_unlinked_context(
            platform_context,
            raw_patch=raw_patch,
            warning=(
                "Der User ist laut vectoplan-auth authentifiziert, aber der lokale AppUser-Link "
                "konnte nicht erstellt werden. Persistente Projektaktionen sind gesperrt."
            ),
        )

    if getattr(platform_context, "can_demo", False):
        return _demo_context(source=SOURCE_DEMO, platform_context=platform_context)

    return _anonymous_context(platform_context, source=SOURCE_AUTH_SERVICE)


def _authenticated_unlinked_context(
    platform_context: Any,
    *,
    raw_patch: Optional[Mapping[str, Any]] = None,
    warning: str = "Authentifiziert, aber lokaler AppUser-Link fehlt.",
) -> CurrentUserContext:
    raw_auth = _auth_data_from_platform_context(platform_context)
    raw_auth.update(_safe_dict(raw_patch))

    return CurrentUserContext(
        user_id=None,
        id=None,
        public_id=SOURCE_AUTH_UNLINKED,
        handle=_safe_str(raw_auth.get("email") or raw_auth.get("auth_user_id"), "auth_unlinked", 160),
        display_name=_safe_str(raw_auth.get("display_name"), "Angemeldeter User", 160),
        email=_safe_str(raw_auth.get("email"), "", 320) or None,
        role="user",
        locale=DEFAULT_LOCALE,
        timezone=DEFAULT_TIMEZONE,
        is_active=True,
        is_placeholder=False,
        is_system=False,
        authenticated=True,
        demo_mode=False,
        persistent=False,
        auth_mode=AUTH_MODE_EXTERNAL,
        auth_state="authenticated_unlinked",
        auth_available=True,
        auth_unavailable=False,
        auth_user_id=_safe_str(raw_auth.get("auth_user_id"), "", 160) or None,
        auth_email=_safe_str(raw_auth.get("email"), "", 320) or None,
        account_id=_safe_str(raw_auth.get("account_id"), "", 160) or None,
        account_role=_safe_str(raw_auth.get("account_role"), "", 80) or None,
        account_plan=_safe_str(raw_auth.get("account_plan"), "", 80) or None,
        account_status=_safe_str(raw_auth.get("account_status"), "", 80) or None,
        roles=_tuple_lower(raw_auth.get("roles") or ("user",)),
        entitlements=_tuple_text(raw_auth.get("entitlements")),
        blocked=False,
        user_blocked=False,
        access_blocked=False,
        denial_status_code=403,
        source=SOURCE_AUTH_UNLINKED,
        login_url=raw_auth.get("login_url"),
        register_url=raw_auth.get("register_url"),
        logout_url=raw_auth.get("logout_url"),
        account_dashboard_url=raw_auth.get("account_dashboard_url"),
        admin_dashboard_url=raw_auth.get("admin_dashboard_url"),
        warning=warning,
        capabilities={
            "can_persist_projects": False,
            "can_manage_projects": False,
            "demo": False,
        },
        raw_auth=raw_auth,
    )


def _resolve_current_user_context() -> CurrentUserContext:
    try:
        if get_auth_mode() == AUTH_MODE_DEMO:
            platform_context = _platform_auth_context()
            if platform_context is None:
                return _auth_unavailable_context(None, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)

            raw_auth = _auth_data_from_platform_context(platform_context)
            if _safe_bool(raw_auth.get("auth_unavailable"), False):
                return _auth_unavailable_context(platform_context, source=SOURCE_AUTH_SERVICE_UNAVAILABLE)
            if _safe_bool(raw_auth.get("user_blocked"), False):
                return _blocked_context(platform_context, source=SOURCE_AUTH_SERVICE_BLOCKED)
            if getattr(platform_context, "can_demo", False):
                return _demo_context(source="forced_demo", platform_context=platform_context)
            return _anonymous_context(platform_context, source=SOURCE_AUTH_SERVICE)

        return _external_context_from_auth_service()

    except Exception as exc:
        _log_exception("resolve current user context failed", exc)
        return _auth_unavailable_context(None, source=SOURCE_ERROR_UNAVAILABLE)


# ─────────────────────────────────────────────────────────────
# Current user ID resolution
# ─────────────────────────────────────────────────────────────

def get_current_user_id(
    *,
    allow_request_override: bool = False,
    fallback_to_default: bool = False,
) -> int:
    """
    Legacy-compatible integer resolver.

    No default user fallback.
    Returns 0 if no persistent local AppUser exists.
    """
    try:
        context = get_current_user_context()
        if context.user_id and context.persistent and not context.effective_blocked:
            return int(context.user_id)
        return 0
    except Exception:
        return 0


def get_current_user_id_optional(*, allow_request_override: bool = False) -> Optional[int]:
    try:
        context = get_current_user_context()
        return context.user_id if context.persistent and not context.effective_blocked else None
    except Exception:
        return None


def set_current_user_id_on_g(user_id: Optional[int] = None) -> int:
    resolved = _safe_int(user_id, get_current_user_id()) or 0

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
    """
    Name kept for compatibility.

    No default user exists anymore. Returns 0 when no local AppUser is linked.
    """
    try:
        if has_request_context() and g is not None:
            value = getattr(g, CURRENT_USER_ID_G_KEY, None)
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
    Returns local AppUser if linked.

    Demo/Guest/Blocked/Auth-unavailable:
    - returns None.
    """
    if AppUser is None or db is None:
        return None

    try:
        if not has_app_context():
            return None

        context = get_current_user_context()

        if context.demo_mode or context.effective_blocked:
            return None

        if not context.persistent or not context.user_id:
            return None

        return _db_get_user(context.user_id)

    except Exception as exc:
        _log_exception("get_current_user failed", exc)
        return None


def require_current_user() -> Any:
    context = get_current_user_context()

    if context.auth_unavailable:
        raise CurrentUserAccessError(
            "current user unavailable: vectoplan-auth is not reachable",
            code=context.blocked_reason or "auth_service_unavailable",
            status_code=503,
            context=context,
        )

    if context.user_blocked or context.access_blocked:
        raise CurrentUserAccessError(
            f"current user blocked: {context.blocked_reason or context.auth_state}",
            code=context.blocked_reason or context.auth_state or "access_blocked",
            status_code=403,
            context=context,
        )

    if context.demo_mode:
        raise CurrentUserAccessError(
            "current user unavailable: demo mode",
            code="demo_mode",
            status_code=403,
            context=context,
        )

    if not context.persistent:
        raise CurrentUserAccessError(
            "current user unavailable: non-persistent auth context",
            code="non_persistent_auth_context",
            status_code=403,
            context=context,
        )

    user = get_current_user()

    if user is None:
        raise CurrentUserAccessError(
            "current user unavailable",
            code="current_user_unavailable",
            status_code=403,
            context=context,
        )

    return user


def get_current_user_context(*, ensure: bool = True, force_refresh: bool = False) -> CurrentUserContext:
    try:
        if has_request_context() and g is not None and not force_refresh:
            existing = getattr(g, CURRENT_CONTEXT_G_KEY, None)
            if isinstance(existing, CurrentUserContext):
                return existing

        context = _resolve_current_user_context()

        try:
            if has_request_context() and g is not None:
                setattr(g, CURRENT_CONTEXT_G_KEY, context)
                if context.user_id:
                    setattr(g, CURRENT_USER_ID_G_KEY, context.user_id)
        except Exception:
            pass

        return context

    except Exception:
        return _auth_unavailable_context(None, source=SOURCE_ERROR_UNAVAILABLE)


def get_current_auth_context(*, ensure: bool = True) -> CurrentUserContext:
    """
    Existing alias.

    Returns CurrentUserContext, not the raw platform AuthContext.
    """
    return get_current_user_context(ensure=ensure)


def get_platform_auth_context(*, force_refresh: bool = False) -> Any:
    """
    Returns normalized AuthContext from services/auth_context.py.
    """
    return _platform_auth_context(force_refresh=force_refresh)


def serialize_current_user(*, ensure: bool = True) -> Dict[str, Any]:
    try:
        context = get_current_user_context()

        user = None
        if context.user_id and context.persistent:
            user = get_current_user()

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
                        "blocked": context.blocked,
                        "auth_unavailable": context.auth_unavailable,
                        "user_blocked": context.user_blocked,
                        "access_blocked": context.access_blocked,
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
                        "blocked": context.blocked,
                        "auth_unavailable": context.auth_unavailable,
                        "user_blocked": context.user_blocked,
                        "access_blocked": context.access_blocked,
                    }
                )
                return user_payload

        return context.to_dict()

    except Exception:
        return _auth_unavailable_context(None, source=SOURCE_ERROR_UNAVAILABLE).to_dict()


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
    return False


def is_current_user_authenticated() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.authenticated and not context.effective_blocked)
    except Exception:
        return False


def is_current_user_demo() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.demo_mode and context.can_demo and not context.effective_blocked)
    except Exception:
        return False


def current_user_can_persist() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.persistent and context.authenticated and not context.demo_mode and context.user_id and not context.effective_blocked)
    except Exception:
        return False


def current_user_can_use_bigdata() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.can_use_bigdata and not context.effective_blocked)
    except Exception:
        return False


def current_user_can_use_cloud() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.can_use_cloud and not context.effective_blocked)
    except Exception:
        return False


def current_user_can_demo() -> bool:
    try:
        context = get_current_user_context()
        return bool(context.can_demo and context.demo_mode and not context.effective_blocked)
    except Exception:
        return False


def require_persistent_current_user() -> CurrentUserContext:
    context = get_current_user_context()

    if context.auth_unavailable:
        raise CurrentUserAccessError(
            "persistent user required: vectoplan-auth is unavailable",
            code=context.blocked_reason or "auth_service_unavailable",
            status_code=503,
            context=context,
        )

    if context.user_blocked or context.access_blocked:
        raise CurrentUserAccessError(
            f"persistent user required: access blocked: {context.blocked_reason or context.auth_state}",
            code=context.blocked_reason or context.auth_state or "access_blocked",
            status_code=403,
            context=context,
        )

    if context.demo_mode:
        raise CurrentUserAccessError(
            "persistent user required: demo mode",
            code="demo_mode",
            status_code=403,
            context=context,
        )

    if not context.authenticated:
        raise CurrentUserAccessError(
            "persistent user required: not authenticated",
            code="authentication_required",
            status_code=401,
            context=context,
        )

    if not context.persistent or not context.user_id:
        raise CurrentUserAccessError(
            "persistent user required: local AppUser link missing",
            code="local_app_user_link_missing",
            status_code=403,
            context=context,
        )

    return context


# ─────────────────────────────────────────────────────────────
# Removed default-user compatibility stubs
# ─────────────────────────────────────────────────────────────

def ensure_default_user(*args: Any, **kwargs: Any) -> None:
    """
    Removed compatibility shim.

    There is no default local user anymore.
    This function creates nothing and returns None.
    """
    return None


def get_default_user_id(*args: Any, **kwargs: Any) -> int:
    return 0


def current_user_id_placeholder(*args: Any, **kwargs: Any) -> int:
    return 0


def get_default_user_public_id(*args: Any, **kwargs: Any) -> str:
    return ""


def get_default_user_handle(*args: Any, **kwargs: Any) -> str:
    return ""


def get_default_user_display_name(*args: Any, **kwargs: Any) -> str:
    return ""


def get_default_user_role(*args: Any, **kwargs: Any) -> str:
    return ""


# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────

def get_current_user_status() -> Dict[str, Any]:
    try:
        context = get_current_user_context()

        db_user_exists = False

        try:
            if has_app_context() and AppUser is not None and db is not None and context.user_id:
                db_user_exists = _db_get_user(context.user_id) is not None
        except Exception:
            db_user_exists = False

        app_user_report = {}
        try:
            if app_user_model_support_report is not None:
                app_user_report = app_user_model_support_report()
        except Exception:
            app_user_report = {"ok": False, "error": "support_report_failed"}

        platform_auth = None
        try:
            raw = get_platform_auth_context()
            if raw is not None and hasattr(raw, "to_public_dict"):
                platform_auth = raw.to_public_dict(include_raw=False)
        except Exception:
            platform_auth = None

        auth_dependency = {}
        try:
            if get_auth_dependency_status is not None:
                auth_dependency = get_auth_dependency_status(include_private=False)
        except Exception as exc:
            auth_dependency = {
                "ok": False,
                "code": "auth_dependency_status_failed",
                "error": str(exc),
            }

        platform_status = {}
        try:
            if get_platform_auth_context_status is not None:
                platform_status = get_platform_auth_context_status()
        except Exception as exc:
            platform_status = {
                "ok": False,
                "error": str(exc),
            }

        return {
            "ok": True,
            "phase": "vectoplan-auth-integrated-no-default-user",
            "auth_mode": get_auth_mode(),
            "current_user": context.to_dict(),
            "platform_auth": platform_auth,
            "platform_status": platform_status,
            "auth_dependency": auth_dependency,
            "db_user_exists": db_user_exists,
            "has_app_context": bool(has_app_context()),
            "has_request_context": bool(has_request_context()),
            "auth_headers_trusted": auth_headers_trusted(),
            "demo_query_enabled": is_demo_query_enabled(),
            "demo_ttl_seconds": get_demo_ttl_seconds(),
            "default_user_removed": True,
            "model_available": AppUser is not None,
            "db_available": db is not None,
            "app_user_model": app_user_report,
            "states": {
                "auth_available": context.auth_available,
                "auth_unavailable": context.auth_unavailable,
                "user_blocked": context.user_blocked,
                "access_blocked": context.access_blocked,
                "blocked_kind": context.blocked_kind,
                "denial_status_code": context.denial_status_code,
            },
            "notes": {
                "user_truth": "vectoplan-auth",
                "local_app_user": "Local FK/link only.",
                "default_user": "Removed. No id=1 fallback.",
                "demo_mode": "Guests get Demo only if vectoplan-auth returns demo_project_access.",
                "auth_unavailable": "Fail-closed 503 state, not a real user ban.",
                "blocked": "Blocked/Banned never falls back to Demo.",
                "no_account_creation": "vectoplan-app creates local AppUser links only, not real auth accounts.",
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "phase": "vectoplan-auth-integrated-no-default-user",
            "default_user_removed": True,
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
    "AUTH_MODE_EXTERNAL",
    "CURRENT_CONTEXT_G_KEY",
    "CURRENT_USER_ID_G_KEY",
    "PLATFORM_AUTH_CONTEXT_G_KEY",
    "ANONYMOUS_PUBLIC_ID",
    "ANONYMOUS_HANDLE",
    "ANONYMOUS_DISPLAY_NAME",
    "DEMO_PUBLIC_ID",
    "DEMO_HANDLE",
    "DEMO_DISPLAY_NAME",
    "DEMO_ROLE",
    "DEMO_ACCOUNT_PLAN",
    "DEMO_ACCOUNT_STATUS",
    "DEMO_TTL_SECONDS",
    "CurrentUserAccessError",
    "CurrentUserContext",
    "allow_user_header_override",
    "auth_headers_trusted",
    "current_user_can_demo",
    "current_user_can_persist",
    "current_user_can_use_bigdata",
    "current_user_can_use_cloud",
    "current_user_id_placeholder",
    "ensure_default_user",
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
    "get_platform_auth_context",
    "is_current_user",
    "is_current_user_admin_placeholder",
    "is_current_user_authenticated",
    "is_current_user_demo",
    "is_demo_query_enabled",
    "is_external_auth_enabled",
    "is_requesting_demo_mode",
    "legacy_default_user_fallback_enabled",
    "require_current_user",
    "require_persistent_current_user",
    "serialize_current_user",
    "set_current_user_context_on_g",
    "set_current_user_id_on_g",
]