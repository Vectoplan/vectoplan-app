# services/vectoplan-app/services/auth_context.py
from __future__ import annotations

"""
Normalisierte Auth-Kontext-Schicht für vectoplan-app.

Diese Datei nimmt rohe Antworten von vectoplan-auth entgegen und erzeugt daraus
einen stabilen, app-internen AuthContext.

Ziele:
- keine verstreute Interpretation von /auth/me oder /auth/context in Routes/Services
- keine Verwechslung von Guest/ClientIdentity mit echtem User
- blockierte/gebannte User zentral erkennen
- Auth-Service-Ausfall getrennt von echter User-Sperre erkennen
- Demo-Zugriff zentral erkennen
- persistente Projektfähigkeit zentral ableiten
- robuste Defaults bei Auth-Service-Fehlern liefern

Wichtige Sicherheitsregeln:
- authenticated=true reicht nur zusammen mit echter user.id.
- subject.id darf nur als User-ID verwendet werden, wenn subject.type == "user".
- Guest/ClientIdentity ist kein User.
- user_blocked=true kommt nur aus vectoplan-auth.
- auth_unavailable=true bedeutet fail-closed, aber keinen echten User-Ban.
- access_blocked=true sperrt effektiv Zugriff für alte Guards.
- ok=true bedeutet nicht automatisch Zugriff erlaubt.
- Kein Default-User.
- Kein lokaler Auth-Fallback.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

try:
    from flask import g, has_request_context, request
except Exception:  # pragma: no cover
    g = None  # type: ignore
    request = None  # type: ignore

    def has_request_context() -> bool:  # type: ignore
        return False


try:
    from services.auth_context_client import (  # type: ignore
        AUTH_STATE_ACCESS_DENIED,
        AUTH_STATE_CONNECTION_REFUSED,
        AUTH_STATE_DNS_FAILED,
        AUTH_STATE_HTTP_5XX,
        AUTH_STATE_HTTP_ERROR,
        AUTH_STATE_INVALID_PAYLOAD,
        AUTH_STATE_NOT_CONFIGURED,
        AUTH_STATE_REQUEST_FAILED,
        AUTH_STATE_REQUESTS_UNAVAILABLE,
        AUTH_STATE_TIMEOUT,
        AUTH_STATE_UNAVAILABLE,
        VectoplanAuthClient,
        get_auth_context_client,
        make_auth_unavailable_payload,
    )
except Exception:  # pragma: no cover
    try:
        from .auth_context_client import (  # type: ignore
            AUTH_STATE_ACCESS_DENIED,
            AUTH_STATE_CONNECTION_REFUSED,
            AUTH_STATE_DNS_FAILED,
            AUTH_STATE_HTTP_5XX,
            AUTH_STATE_HTTP_ERROR,
            AUTH_STATE_INVALID_PAYLOAD,
            AUTH_STATE_NOT_CONFIGURED,
            AUTH_STATE_REQUEST_FAILED,
            AUTH_STATE_REQUESTS_UNAVAILABLE,
            AUTH_STATE_TIMEOUT,
            AUTH_STATE_UNAVAILABLE,
            VectoplanAuthClient,
            get_auth_context_client,
            make_auth_unavailable_payload,
        )
    except Exception:  # pragma: no cover
        AUTH_STATE_ACCESS_DENIED = "access_denied"
        AUTH_STATE_CONNECTION_REFUSED = "connection_refused"
        AUTH_STATE_DNS_FAILED = "dns_failed"
        AUTH_STATE_HTTP_5XX = "http_5xx"
        AUTH_STATE_HTTP_ERROR = "http_error"
        AUTH_STATE_INVALID_PAYLOAD = "invalid_payload"
        AUTH_STATE_NOT_CONFIGURED = "not_configured"
        AUTH_STATE_REQUEST_FAILED = "request_failed"
        AUTH_STATE_REQUESTS_UNAVAILABLE = "requests_unavailable"
        AUTH_STATE_TIMEOUT = "timeout"
        AUTH_STATE_UNAVAILABLE = "service_unavailable"
        VectoplanAuthClient = Any  # type: ignore

        def get_auth_context_client() -> Any:  # type: ignore
            return None

        def make_auth_unavailable_payload(endpoint: str = "/auth/context/minimal", **kwargs: Any) -> Dict[str, Any]:  # type: ignore
            return {
                "ok": False,
                "authenticated": False,
                "auth_available": False,
                "auth_state": "service_unavailable",
                "reason": "service_unavailable",
                "reason_code": "service_unavailable",
                "code": "service_unavailable",
                "status_code": 503,
                "endpoint": endpoint,
                "user": None,
                "access": {
                    "blocked": False,
                    "user_blocked": False,
                    "access_blocked": True,
                    "blocked_reason": "service_unavailable",
                    "demo_project_access": False,
                },
                "errors": ["service_unavailable"],
            }


try:
    from services.auth_dependency_service import get_auth_dependency_status  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .auth_dependency_service import get_auth_dependency_status  # type: ignore
    except Exception:
        get_auth_dependency_status = None  # type: ignore


LOGGER = logging.getLogger(__name__)

REQUEST_CONTEXT_CACHE_ATTR = "_vectoplan_normalized_auth_context_cache"


# ─────────────────────────────────────────────────────────────
# Auth state groups
# ─────────────────────────────────────────────────────────────

GUEST_AUTH_STATES = {
    "guest",
    "anonymous",
    "unauthenticated",
    "logged_out",
    "logout",
    "session_missing",
    "auth_session_missing",
    "client_identity",
}

STALE_AUTH_STATES = {
    "stale_session",
    "auth_session_expired",
    "auth_session_revoked",
    "auth_session_user_mismatch",
    "user_missing",
}

AUTH_UNAVAILABLE_STATES = {
    "service_unavailable",
    "auth_service_unavailable",
    "storage_unavailable",
    "dependency_unavailable",
    "upstream_unavailable",
    "dns_failed",
    "connection_refused",
    "timeout",
    "http_5xx",
    "http_error",
    "access_denied",
    "invalid_payload",
    "not_configured",
    "request_failed",
    "requests_unavailable",
    AUTH_STATE_UNAVAILABLE,
    AUTH_STATE_DNS_FAILED,
    AUTH_STATE_CONNECTION_REFUSED,
    AUTH_STATE_TIMEOUT,
    AUTH_STATE_HTTP_5XX,
    AUTH_STATE_HTTP_ERROR,
    AUTH_STATE_ACCESS_DENIED,
    AUTH_STATE_INVALID_PAYLOAD,
    AUTH_STATE_NOT_CONFIGURED,
    AUTH_STATE_REQUEST_FAILED,
    AUTH_STATE_REQUESTS_UNAVAILABLE,
}

USER_BLOCKING_AUTH_STATES = {
    "blocked",
    "banned",
    "user_blocked",
    "user_banned",
    "subscription_blocked",
    "security_blocked",
    "account_blocked",
    "account_banned",
    "plan_blocked",
    "disabled",
    "inactive",
    "suspended",
    "deleted",
    "locked",
    "temporarily_locked",
}

# Backward-compatible export name.
BLOCKING_AUTH_STATES = USER_BLOCKING_AUTH_STATES | AUTH_UNAVAILABLE_STATES

BLOCKING_USER_STATUSES = {
    "banned",
    "blocked",
    "gesperrt",
    "disabled",
    "inactive",
    "suspended",
    "deleted",
    "locked",
    "temporarily_locked",
}

ACTIVE_USER_STATUSES = {
    "active",
    "enabled",
    "ok",
    "verified",
}

BLOCKING_PLAN_STATUSES = {
    "blocked",
    "banned",
    "ban",
    "gesperrt",
    "suspended",
    "disabled",
    "inactive",
    "locked",
}

ADMIN_ROLES = {
    "admin",
    "staff",
    "system_admin",
    "system",
    "owner",
}

ACCOUNT_MANAGEMENT_ENTITLEMENTS = {
    "account_management",
    "advanced_account_management",
    "member_management",
    "account_members",
}

PROJECT_SHARING_ENTITLEMENTS = {
    "project_sharing",
    "project_permissions",
    "member_management",
    "account_members",
}

API_KEY_ENTITLEMENTS = {
    "api_key_access",
    "api_key_management",
}

CLOUD_ENTITLEMENTS = {
    "cloud_access",
}

DEMO_ENTITLEMENTS = {
    "demo_project_access",
}


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _now() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    if value is None:
        return default
    try:
        text = str(value).strip()
        if not text:
            return default
        if max_len > 0 and len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return default


def _lower(value: Any, default: str = "") -> str:
    return _safe_str(value, default=default).lower()


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)

        text = _lower(value)
        if text in {"1", "true", "yes", "y", "on", "enabled", "active", "ok", "ready"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "inactive", "failed", "error"}:
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


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, Mapping):
        try:
            return dict(value)
        except Exception:
            return {}
    return {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        if not value.strip():
            return []
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value.strip()]
    return [value]


def _clean_string_list(value: Any) -> Tuple[str, ...]:
    items = []
    for item in _as_list(value):
        text = _safe_str(item)
        if text:
            items.append(text)
    return tuple(dict.fromkeys(items))


def _clean_lower_set(value: Any) -> Set[str]:
    return {_lower(item) for item in _as_list(value) if _safe_str(item)}


def _deep_get(source: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return default
        if part not in current:
            return default
        current = current.get(part)
    return current


def _value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, set, dict)) and len(value) == 0:
        return False
    return True


def _first_value(candidates: Sequence[Mapping[str, Any]], *paths: str, default: Any = None) -> Any:
    for path in paths:
        for candidate in candidates:
            value = _deep_get(candidate, path, default=None)
            if _value_is_present(value) or isinstance(value, bool):
                return value
    return default


def _first_dict(candidates: Sequence[Mapping[str, Any]], *paths: str) -> Dict[str, Any]:
    value = _first_value(candidates, *paths, default={})
    return _as_dict(value)


def _first_list(candidates: Sequence[Mapping[str, Any]], *paths: str) -> Tuple[str, ...]:
    value = _first_value(candidates, *paths, default=[])
    return _clean_string_list(value)


def _unique_dicts(items: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    result: List[Mapping[str, Any]] = []
    seen: Set[int] = set()

    for item in items:
        if not isinstance(item, Mapping) or not item:
            continue
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)

    return result


def _context_candidates(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    context = _as_dict(root.get("context"))
    data_context = _as_dict(data.get("context"))
    raw = _as_dict(root.get("raw"))
    raw_context = _as_dict(raw.get("context"))
    raw_data = _as_dict(raw.get("data"))

    return _unique_dicts(
        [
            context,
            data_context,
            raw_context,
            raw_data,
            data,
            raw,
            root,
        ]
    )


def _request_path(default: str = "/") -> str:
    try:
        if has_request_context() and request is not None:
            full_path = request.full_path or request.path or default
            if full_path.endswith("?"):
                full_path = full_path[:-1]
            return full_path or default
    except Exception:
        pass
    return default


def _safe_url_from_payload(
    candidates: Sequence[Mapping[str, Any]],
    client: Optional[VectoplanAuthClient],
    kind: str,
    next_url: Optional[str],
) -> str:
    ui = _first_dict(candidates, "ui")
    redirect = _first_dict(candidates, "redirect")
    links = _first_dict(candidates, "links")

    possible_keys = {
        "login": ("login_url", "auth_url", "sign_in_url"),
        "register": ("register_url", "registration_url", "sign_up_url"),
        "logout": ("logout_url",),
        "account_dashboard": ("account_dashboard_url", "dashboard_url"),
        "admin_dashboard": ("admin_dashboard_url", "admin_url"),
    }.get(kind, ())

    for key in possible_keys:
        value = _safe_str(ui.get(key) or redirect.get(key) or links.get(key))
        if value:
            return value

    try:
        auth_client = client or get_auth_context_client()
        if kind == "login":
            return auth_client.login_url(next_url=next_url)
        if kind == "register":
            return auth_client.register_url(next_url=next_url)
        if kind == "logout":
            return auth_client.logout_url()
        if kind == "account_dashboard":
            return auth_client.account_dashboard_url()
        if kind == "admin_dashboard":
            if hasattr(auth_client, "admin_dashboard_url"):
                return auth_client.admin_dashboard_url()
            return auth_client.public_auth_url("/auth/admin/dashboard")
    except Exception:
        pass

    fallback = {
        "login": "/auth?mode=login",
        "register": "/auth?mode=register",
        "logout": "/auth/logout",
        "account_dashboard": "/auth/account/dashboard",
        "admin_dashboard": "/auth/admin/dashboard",
    }.get(kind, "/auth")

    return fallback


def _auth_cache_key(minimal: bool, incoming_headers: Optional[Mapping[str, Any]], extra: Optional[str] = None) -> str:
    mode = "minimal" if minimal else "full"

    if incoming_headers:
        cookie = _safe_str(incoming_headers.get("Cookie") or incoming_headers.get("cookie"))
        authorization = _safe_str(incoming_headers.get("Authorization") or incoming_headers.get("authorization"))
        user_agent = _safe_str(incoming_headers.get("User-Agent") or incoming_headers.get("user-agent"))
        fingerprint = str(abs(hash((cookie, authorization, user_agent))))
    else:
        fingerprint = "request"

    if extra:
        return f"{mode}:{fingerprint}:{extra}"
    return f"{mode}:{fingerprint}"


def _get_request_cache() -> Optional[MutableMapping[str, "AuthContext"]]:
    try:
        if has_request_context() and g is not None:
            cache = getattr(g, REQUEST_CONTEXT_CACHE_ATTR, None)
            if cache is None:
                cache = {}
                setattr(g, REQUEST_CONTEXT_CACHE_ATTR, cache)
            return cache
    except Exception:
        return None
    return None


def _is_auth_unavailable_state(value: Any) -> bool:
    return _lower(value) in AUTH_UNAVAILABLE_STATES


def _is_user_blocking_state(value: Any) -> bool:
    return _lower(value) in USER_BLOCKING_AUTH_STATES


def _dedupe(values: Iterable[Any]) -> Tuple[str, ...]:
    result: List[str] = []
    for value in values:
        text = _safe_str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


# ─────────────────────────────────────────────────────────────
# Snapshots
# ─────────────────────────────────────────────────────────────

@dataclass
class AuthUserSnapshot:
    id: Optional[str] = None
    email: Optional[str] = None
    username: Optional[str] = None
    display_name: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: bool = False
    is_system: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "status": self.status,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "is_system": self.is_system,
        }


@dataclass
class AuthAccountSnapshot:
    available: bool = False
    account_id: Optional[str] = None
    account_type: Optional[str] = None
    account_name: Optional[str] = None
    member_role: Optional[str] = None
    can_manage_account: bool = False
    can_manage_members: bool = False
    can_invite_members: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "account_id": self.account_id,
            "account_type": self.account_type,
            "account_name": self.account_name,
            "member_role": self.member_role,
            "can_manage_account": self.can_manage_account,
            "can_manage_members": self.can_manage_members,
            "can_invite_members": self.can_invite_members,
        }


@dataclass
class AuthAccessSnapshot:
    plan: Optional[str] = None
    plan_key: Optional[str] = None
    plan_status: Optional[str] = None
    entitlements: Tuple[str, ...] = field(default_factory=tuple)
    blocked: bool = False
    blocked_reason: Optional[str] = None
    user_blocked: bool = False
    access_blocked: bool = False
    auth_unavailable: bool = False
    cloud_access: bool = False
    demo_project_access: bool = False
    api_key_access: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def has_entitlement(self, entitlement: str) -> bool:
        return _safe_str(entitlement) in set(self.entitlements)

    def has_any_entitlement(self, entitlements: Iterable[str]) -> bool:
        current = set(self.entitlements)
        return any(item in current for item in entitlements)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan": self.plan,
            "plan_key": self.plan_key,
            "plan_status": self.plan_status,
            "entitlements": list(self.entitlements),
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "user_blocked": self.user_blocked,
            "access_blocked": self.access_blocked,
            "auth_unavailable": self.auth_unavailable,
            "cloud_access": self.cloud_access,
            "demo_project_access": self.demo_project_access,
            "api_key_access": self.api_key_access,
        }


# ─────────────────────────────────────────────────────────────
# AuthContext
# ─────────────────────────────────────────────────────────────

@dataclass
class AuthContext:
    """
    Normalisierter Auth-Zustand für vectoplan-app.

    Kernfelder:
    - authenticated: echter eingeloggter User mit user_id und nicht blockiert
    - is_guest: anonymer/guest Kontext, nicht als User verwenden
    - can_demo: guest darf temporäres Demo-Projekt nutzen
    - can_persist: echte Projektpersistenz erlaubt
    - auth_unavailable: vectoplan-auth ist technisch nicht erreichbar/nutzbar
    - user_blocked: echter User/Account ist gesperrt
    - access_blocked: effektive Sperre für App-Zugriff
    - blocked: Legacy-Alias für effektive Zugriffssperre
    """

    ok: bool = False
    status_code: int = 0
    source: str = "unknown"
    fetched_at: float = field(default_factory=_now)
    request_id: str = field(default_factory=lambda: f"authctx_{uuid.uuid4().hex}")

    auth_available: bool = False
    auth_unavailable: bool = False
    auth_dependency: Dict[str, Any] = field(default_factory=dict)

    authenticated: bool = False
    raw_authenticated: bool = False
    auth_state: str = "anonymous"
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    session_valid: bool = False

    subject_type: Optional[str] = None
    subject_id: Optional[str] = None

    user_id: Optional[str] = None
    app_user_id: Optional[int] = None
    user: AuthUserSnapshot = field(default_factory=AuthUserSnapshot)

    account: AuthAccountSnapshot = field(default_factory=AuthAccountSnapshot)
    access: AuthAccessSnapshot = field(default_factory=AuthAccessSnapshot)

    roles: Tuple[str, ...] = field(default_factory=tuple)
    primary_role: str = "guest"
    is_admin: bool = False
    is_staff: bool = False
    is_system: bool = False

    blocked: bool = False
    blocked_reason: Optional[str] = None
    blocked_kind: Optional[str] = None
    user_blocked: bool = False
    access_blocked: bool = False

    is_guest: bool = True
    can_demo: bool = False
    can_persist: bool = False
    can_use_cloud: bool = False
    can_manage_account: bool = False
    can_manage_members: bool = False
    can_manage_api_keys: bool = False
    can_project_sharing: bool = False
    dashboard_allowed: bool = False
    requires_login: bool = True

    language: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    client_identity: Dict[str, Any] = field(default_factory=dict)
    api_key: Dict[str, Any] = field(default_factory=dict)
    ui: Dict[str, Any] = field(default_factory=dict)
    redirect: Dict[str, Any] = field(default_factory=dict)
    cleanup: Dict[str, Any] = field(default_factory=dict)
    security: Dict[str, Any] = field(default_factory=dict)

    login_url: str = "/auth?mode=login"
    register_url: str = "/auth?mode=register"
    logout_url: str = "/auth/logout"
    account_dashboard_url: str = "/auth/account/dashboard"
    admin_dashboard_url: str = "/auth/admin/dashboard"

    raw_payload: Dict[str, Any] = field(default_factory=dict)
    errors: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_payload(
        cls,
        payload: Optional[Mapping[str, Any]],
        *,
        source: str = "auth_payload",
        client: Optional[VectoplanAuthClient] = None,
        next_url: Optional[str] = None,
    ) -> "AuthContext":
        try:
            return normalize_auth_payload(
                payload=payload,
                source=source,
                client=client,
                next_url=next_url,
            )
        except Exception as exc:
            LOGGER.exception("Failed to normalize auth payload: %s", exc)
            unavailable = make_auth_unavailable_payload(endpoint=source)
            return normalize_auth_payload(
                payload=unavailable,
                source="normalization_error",
                client=client,
                next_url=next_url,
                errors=[f"normalization_error: {exc}"],
            )

    @property
    def plan(self) -> Optional[str]:
        return self.access.plan or self.access.plan_key

    @property
    def account_id(self) -> Optional[str]:
        return self.account.account_id

    @property
    def email(self) -> Optional[str]:
        return self.user.email

    @property
    def display_name(self) -> Optional[str]:
        return self.user.display_name

    @property
    def effective_blocked(self) -> bool:
        return bool(self.blocked or self.access_blocked or self.auth_unavailable or self.user_blocked)

    def has_role(self, role: str) -> bool:
        needle = _lower(role)
        return needle in {_lower(item) for item in self.roles}

    def has_any_role(self, roles: Iterable[str]) -> bool:
        current = {_lower(item) for item in self.roles}
        return any(_lower(role) in current for role in roles)

    def has_entitlement(self, entitlement: str) -> bool:
        return self.access.has_entitlement(entitlement)

    def has_any_entitlement(self, entitlements: Iterable[str]) -> bool:
        return self.access.has_any_entitlement(entitlements)

    def with_app_user_id(self, app_user_id: Optional[int]) -> "AuthContext":
        self.app_user_id = app_user_id
        return self

    def deny_reason(self) -> str:
        if self.auth_unavailable:
            return self.reason_code or self.auth_state or "auth_service_unavailable"
        if self.user_blocked:
            return self.blocked_reason or self.reason_code or "user_blocked"
        if self.access_blocked:
            return self.blocked_reason or self.reason_code or "access_blocked"
        if not self.authenticated and not self.can_demo:
            return self.reason_code or self.reason or "authentication_required"
        if not self.can_persist and self.is_guest:
            return "demo_only"
        return "not_allowed"

    def deny_status_code(self) -> int:
        if self.auth_unavailable:
            return 503
        if self.user_blocked or self.access_blocked:
            return 403
        if not self.authenticated and not self.can_demo:
            return 401
        return 403

    def to_public_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        ui = {
            **self.ui,
            "show_login": bool(not self.auth_unavailable and not self.authenticated and not self.user_blocked),
            "show_logout": bool(self.authenticated),
            "show_account_dashboard": bool(self.dashboard_allowed),
            "dashboard_allowed": bool(self.dashboard_allowed),
            "blocked": bool(self.user_blocked),
            "user_blocked": bool(self.user_blocked),
            "access_blocked": bool(self.access_blocked),
            "auth_unavailable": bool(self.auth_unavailable),
            "auth_service_unavailable": bool(self.auth_unavailable),
        }

        data = {
            "ok": self.ok,
            "status_code": self.status_code,
            "source": self.source,
            "auth_available": self.auth_available,
            "auth_unavailable": self.auth_unavailable,
            "auth_dependency": self.auth_dependency,
            "authenticated": self.authenticated,
            "auth_state": self.auth_state,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "session_valid": self.session_valid,
            "subject": {
                "type": self.subject_type,
                "id": self.subject_id,
            },
            "user": self.user.to_dict() if self.authenticated else None,
            "app_user_id": self.app_user_id,
            "account": self.account.to_dict(),
            "access": self.access.to_dict(),
            "roles": {
                "primary": self.primary_role,
                "primary_role": self.primary_role,
                "names": list(self.roles),
                "roles": list(self.roles),
                "is_admin": self.is_admin,
                "is_staff": self.is_staff,
                "is_system": self.is_system,
            },
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "blocked_kind": self.blocked_kind,
            "user_blocked": self.user_blocked,
            "access_blocked": self.access_blocked,
            "is_guest": self.is_guest,
            "can_demo": self.can_demo,
            "can_persist": self.can_persist,
            "can_use_cloud": self.can_use_cloud,
            "can_manage_account": self.can_manage_account,
            "can_manage_members": self.can_manage_members,
            "can_manage_api_keys": self.can_manage_api_keys,
            "can_project_sharing": self.can_project_sharing,
            "dashboard_allowed": self.dashboard_allowed,
            "requires_login": self.requires_login,
            "language": self.language,
            "ui": ui,
            "cleanup": self.cleanup,
            "links": {
                "login_url": self.login_url,
                "register_url": self.register_url,
                "logout_url": self.logout_url,
                "account_dashboard_url": self.account_dashboard_url,
                "admin_dashboard_url": self.admin_dashboard_url,
            },
            "errors": list(self.errors),
        }

        if include_raw:
            data["raw_payload"] = self.raw_payload

        return data

    def to_template_context(self) -> Dict[str, Any]:
        return {
            "auth_context": self,
            "auth": self.to_public_dict(include_raw=False),
            "current_auth": self.to_public_dict(include_raw=False),
            "current_user": self.user.to_dict() if self.authenticated else None,
            "current_account": self.account.to_dict(),
            "auth_links": {
                "login_url": self.login_url,
                "register_url": self.register_url,
                "logout_url": self.logout_url,
                "account_dashboard_url": self.account_dashboard_url,
                "admin_dashboard_url": self.admin_dashboard_url,
            },
            "is_authenticated": self.authenticated,
            "is_guest": self.is_guest,
            "is_demo": self.can_demo and not self.authenticated,
            "is_blocked": self.user_blocked,
            "is_access_blocked": self.access_blocked,
            "auth_unavailable": self.auth_unavailable,
        }

    def to_app_config(self) -> Dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "authAvailable": self.auth_available,
            "authUnavailable": self.auth_unavailable,
            "authState": self.auth_state,
            "reason": self.reason,
            "reasonCode": self.reason_code,
            "blocked": self.blocked,
            "blockedReason": self.blocked_reason,
            "blockedKind": self.blocked_kind,
            "userBlocked": self.user_blocked,
            "accessBlocked": self.access_blocked,
            "guest": self.is_guest,
            "demo": self.can_demo and not self.authenticated,
            "canPersist": self.can_persist,
            "canDemo": self.can_demo,
            "user": self.user.to_dict() if self.authenticated else None,
            "appUserId": self.app_user_id,
            "account": self.account.to_dict(),
            "access": self.access.to_dict(),
            "roles": list(self.roles),
            "primaryRole": self.primary_role,
            "links": {
                "login": self.login_url,
                "register": self.register_url,
                "logout": self.logout_url,
                "accountDashboard": self.account_dashboard_url,
                "adminDashboard": self.admin_dashboard_url,
            },
        }

    def to_internal_headers(self) -> Dict[str, str]:
        headers = {
            "X-VECTOPLAN-Auth-State": self.auth_state,
            "X-VECTOPLAN-Auth-Available": "true" if self.auth_available else "false",
            "X-VECTOPLAN-Auth-Unavailable": "true" if self.auth_unavailable else "false",
            "X-VECTOPLAN-Authenticated": "true" if self.authenticated else "false",
            "X-VECTOPLAN-Blocked": "true" if self.blocked else "false",
            "X-VECTOPLAN-User-Blocked": "true" if self.user_blocked else "false",
            "X-VECTOPLAN-Access-Blocked": "true" if self.access_blocked else "false",
            "X-VECTOPLAN-Plan": self.plan or "",
            "X-VECTOPLAN-Roles": ",".join(self.roles),
            "X-VECTOPLAN-Entitlements": ",".join(self.access.entitlements),
        }

        if self.user_id:
            headers["X-VECTOPLAN-User-ID"] = self.user_id
        if self.account_id:
            headers["X-VECTOPLAN-Account-ID"] = self.account_id
        if self.app_user_id is not None:
            headers["X-VECTOPLAN-App-User-ID"] = str(self.app_user_id)

        return headers

    def for_log(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "ok": self.ok,
            "status_code": self.status_code,
            "auth_available": self.auth_available,
            "auth_unavailable": self.auth_unavailable,
            "authenticated": self.authenticated,
            "auth_state": self.auth_state,
            "blocked": self.blocked,
            "blocked_kind": self.blocked_kind,
            "user_blocked": self.user_blocked,
            "access_blocked": self.access_blocked,
            "blocked_reason": self.blocked_reason,
            "is_guest": self.is_guest,
            "can_demo": self.can_demo,
            "can_persist": self.can_persist,
            "user_id": self.user_id,
            "account_id": self.account_id,
            "plan": self.plan,
            "roles": list(self.roles),
            "entitlements": list(self.access.entitlements),
        }


# ─────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────

def normalize_auth_payload(
    payload: Optional[Mapping[str, Any]],
    *,
    source: str = "auth_payload",
    client: Optional[VectoplanAuthClient] = None,
    next_url: Optional[str] = None,
    errors: Optional[Sequence[str]] = None,
) -> AuthContext:
    raw_payload = _as_dict(payload)
    if not raw_payload:
        raw_payload = make_auth_unavailable_payload(endpoint=source)

    candidates = _context_candidates(raw_payload)

    status_code = _safe_int(_first_value(candidates, "status_code", default=0), default=0)
    if status_code <= 0:
        status_code = 200 if _safe_bool(_first_value(candidates, "ok", default=False), False) else 0

    ok = _safe_bool(_first_value(candidates, "ok", default=(200 <= status_code < 300)), default=False)

    reason = _safe_str(_first_value(candidates, "reason", "message", default="")) or None
    reason_code = _safe_str(_first_value(candidates, "reason_code", "code", default="")) or None

    auth_state = _lower(_first_value(candidates, "auth_state", "authState", default=""))
    status_value = _lower(_first_value(candidates, "status", default=""))

    if not auth_state and reason_code:
        auth_state = _lower(reason_code)

    subject = _first_dict(candidates, "subject")
    subject_type = _lower(subject.get("type")) or None
    subject_id = _safe_str(subject.get("id")) or None

    user_dict = _first_dict(candidates, "user", "session.user")
    session_dict = _first_dict(candidates, "session")
    access_dict = _first_dict(candidates, "access")
    account_dict = _first_dict(candidates, "account")
    roles_dict = _first_dict(candidates, "roles")
    client_identity_dict = _first_dict(candidates, "client_identity", "clientIdentity")
    api_key_dict = _first_dict(candidates, "api_key", "apiKey")
    language_dict = _first_dict(candidates, "language")
    ui_dict = _first_dict(candidates, "ui")
    redirect_dict = _first_dict(candidates, "redirect")
    cleanup_dict = _first_dict(candidates, "cleanup")
    security_dict = _first_dict(candidates, "security")
    dependency_dict = _first_dict(candidates, "dependency", "auth_dependency", "authDependency")

    raw_authenticated = _safe_bool(
        _first_value(candidates, "authenticated", "is_authenticated", "session_valid", default=False),
        default=False,
    )

    session_authenticated = _safe_bool(session_dict.get("authenticated"), default=False)
    session_valid = _safe_bool(
        _first_value(candidates, "session_valid", "persistent_session_valid", default=False),
        default=False,
    )
    session_status = _lower(session_dict.get("status"))

    if session_status == "active":
        session_valid = True

    if auth_state == "authenticated" or status_value == "authenticated":
        raw_authenticated = True

    if session_authenticated:
        raw_authenticated = True

    user_status = _lower(
        user_dict.get("status")
        or user_dict.get("state")
        or user_dict.get("account_status")
    )

    user_is_active_value = user_dict.get("is_active")
    user_is_active: Optional[bool]
    if user_is_active_value is None:
        user_is_active = None
    else:
        user_is_active = _safe_bool(user_is_active_value, default=False)

    user_id = _safe_str(
        user_dict.get("id")
        or user_dict.get("user_id")
        or user_dict.get("auth_user_id")
        or user_dict.get("public_id")
    ) or None

    if not user_id and subject_type == "user":
        user_id = subject_id

    if not user_id and raw_authenticated and subject_type not in {"guest", "client_identity"}:
        session_user_id = _safe_str(session_dict.get("user_id") or session_dict.get("user"))
        if session_user_id:
            user_id = session_user_id

    if subject_type in {"guest", "client_identity"}:
        if user_id and user_id == subject_id:
            user_id = None

    user_snapshot = AuthUserSnapshot(
        id=user_id,
        email=_safe_str(user_dict.get("email")) or None,
        username=_safe_str(user_dict.get("username") or user_dict.get("handle")) or None,
        display_name=_safe_str(
            user_dict.get("display_name")
            or user_dict.get("name")
            or user_dict.get("email")
        ) or None,
        status=user_status or None,
        is_active=user_is_active,
        is_admin=_safe_bool(user_dict.get("is_admin"), default=False),
        is_system=_safe_bool(user_dict.get("is_system"), default=False),
        raw=user_dict,
    )

    role_values: List[str] = []
    role_values.extend(_as_list(roles_dict.get("names")))
    role_values.extend(_as_list(roles_dict.get("roles")))
    role_values.extend(_as_list(roles_dict.get("role_names")))
    role_values.extend(_as_list(_first_value(candidates, "roles_list", "role_names", default=[])))

    primary_role = _safe_str(
        roles_dict.get("primary")
        or roles_dict.get("primary_role")
        or roles_dict.get("primaryRole")
    )

    if primary_role:
        role_values.insert(0, primary_role)

    if not role_values:
        if raw_authenticated:
            role_values = ["user"]
        else:
            role_values = ["guest"]

    roles = tuple(dict.fromkeys(_lower(role) for role in role_values if _safe_str(role)))
    primary_role = _lower(primary_role or (roles[0] if roles else "guest")) or "guest"

    is_staff = (
        _safe_bool(roles_dict.get("is_staff"), default=False)
        or "staff" in roles
    )
    is_system = (
        _safe_bool(roles_dict.get("is_system"), default=False)
        or _safe_bool(roles_dict.get("is_system_admin"), default=False)
        or "system_admin" in roles
        or user_snapshot.is_system
    )
    is_admin = (
        _safe_bool(roles_dict.get("is_admin"), default=False)
        or user_snapshot.is_admin
        or is_staff
        or is_system
        or any(role in ADMIN_ROLES for role in roles)
    )

    entitlements = list(_clean_string_list(access_dict.get("entitlements")))
    entitlements.extend(_clean_string_list(access_dict.get("addons")))
    entitlements.extend(_clean_string_list(access_dict.get("permissions")))
    entitlements_tuple = tuple(dict.fromkeys(item for item in entitlements if item))
    entitlement_set = set(entitlements_tuple)

    plan = _safe_str(access_dict.get("plan") or access_dict.get("plan_key")) or None
    plan_key = _safe_str(access_dict.get("plan_key") or access_dict.get("plan")) or None
    plan_status = _lower(
        access_dict.get("plan_status")
        or access_dict.get("subscription_status")
        or access_dict.get("status")
    ) or None

    payload_auth_available_raw = _first_value(candidates, "auth_available", "authAvailable", default=None)
    payload_auth_unavailable_raw = _first_value(candidates, "auth_unavailable", "authUnavailable", default=None)

    auth_unavailable_reasons: List[str] = []
    user_blocking_reasons: List[str] = []

    if _is_auth_unavailable_state(auth_state):
        auth_unavailable_reasons.append(auth_state)

    if _is_auth_unavailable_state(status_value):
        auth_unavailable_reasons.append(status_value)

    if reason_code and _is_auth_unavailable_state(reason_code):
        auth_unavailable_reasons.append(reason_code)

    if status_code == 503:
        auth_unavailable_reasons.append(auth_state or reason_code or "service_unavailable")

    if payload_auth_available_raw is False:
        auth_unavailable_reasons.append("auth_unavailable")

    if _safe_bool(payload_auth_unavailable_raw, default=False):
        auth_unavailable_reasons.append(auth_state or "auth_unavailable")

    if _is_user_blocking_state(auth_state):
        user_blocking_reasons.append(auth_state)

    if _is_user_blocking_state(status_value):
        user_blocking_reasons.append(status_value)

    if user_status in BLOCKING_USER_STATUSES:
        user_blocking_reasons.append(f"user_{user_status}")

    if raw_authenticated and user_is_active is False:
        user_blocking_reasons.append("user_inactive")

    if plan_status in BLOCKING_PLAN_STATUSES:
        user_blocking_reasons.append(f"subscription_{plan_status}")

    access_blocked_flag = _safe_bool(
        access_dict.get("access_blocked")
        or access_dict.get("accessBlocked"),
        default=False,
    )
    user_blocked_flag = _safe_bool(
        access_dict.get("user_blocked")
        or access_dict.get("userBlocked")
        or access_dict.get("blocked")
        or security_dict.get("blocked"),
        default=False,
    )
    security_blocked = _safe_bool(security_dict.get("blocked"), default=False)
    ui_blocked = _safe_bool(ui_dict.get("blocked"), default=False)

    access_block_reason = _safe_str(
        access_dict.get("access_blocked_reason")
        or access_dict.get("accessBlockedReason")
        or access_dict.get("blocked_reason")
        or access_dict.get("blockedReason")
    )

    security_block_reason = _safe_str(
        security_dict.get("blocked_reason")
        or security_dict.get("blockedReason")
    )

    payload_blocked_reason = _safe_str(_first_value(candidates, "blocked_reason", "blockedReason", default=""))

    if user_blocked_flag:
        reason_candidate = access_block_reason or security_block_reason or payload_blocked_reason or "user_blocked"
        if not _is_auth_unavailable_state(reason_candidate):
            user_blocking_reasons.append(reason_candidate)

    if security_blocked:
        reason_candidate = security_block_reason or "security_blocked"
        if not _is_auth_unavailable_state(reason_candidate):
            user_blocking_reasons.append(reason_candidate)

    if ui_blocked:
        reason_candidate = payload_blocked_reason or "ui_blocked"
        if not _is_auth_unavailable_state(reason_candidate):
            user_blocking_reasons.append(reason_candidate)

    if status_code == 403 and not auth_unavailable_reasons:
        user_blocking_reasons.append(reason_code or "forbidden")

    auth_unavailable = bool(auth_unavailable_reasons)
    user_blocked = bool(user_blocking_reasons)
    access_blocked = bool(auth_unavailable or user_blocked or access_blocked_flag)

    if not auth_state:
        if auth_unavailable:
            auth_state = "service_unavailable"
        elif raw_authenticated:
            auth_state = "authenticated"
        elif subject_type in {"guest", "client_identity"}:
            auth_state = "guest"
        elif status_value in GUEST_AUTH_STATES:
            auth_state = status_value
        elif status_value:
            auth_state = status_value
        else:
            auth_state = "anonymous"

    if not reason_code:
        if auth_unavailable:
            reason_code = auth_unavailable_reasons[0] if auth_unavailable_reasons else "auth_service_unavailable"
        elif user_blocked:
            reason_code = user_blocking_reasons[0] if user_blocking_reasons else "user_blocked"

    if not reason:
        if auth_unavailable:
            reason = "vectoplan-auth is unavailable."
        elif user_blocked:
            reason = "User or account access is blocked."

    blocked_kind = None
    if auth_unavailable:
        blocked_kind = "auth_unavailable"
    elif user_blocked:
        blocked_kind = "user_blocked"
    elif access_blocked:
        blocked_kind = "access_blocked"

    blocked_reason = (
        access_block_reason
        or security_block_reason
        or payload_blocked_reason
        or (auth_unavailable_reasons[0] if auth_unavailable_reasons else "")
        or (user_blocking_reasons[0] if user_blocking_reasons else "")
        or None
    )

    authenticated = bool(raw_authenticated and user_id and not access_blocked)

    if raw_authenticated and not user_id and not access_blocked:
        reason_code = reason_code or "user_missing"
        reason = reason or "Authenticated flag was present but no canonical user.id was available."

    is_guest = False
    if not authenticated:
        is_guest = (
            auth_state in GUEST_AUTH_STATES
            or subject_type in {"guest", "client_identity"}
            or primary_role == "guest"
            or "guest" in roles
            or plan == "guest_demo"
            or plan_key == "guest_demo"
        )

    if auth_unavailable:
        is_guest = False

    auth_available = bool(not auth_unavailable and status_code < 500)

    access_snapshot = AuthAccessSnapshot(
        plan=plan,
        plan_key=plan_key,
        plan_status=plan_status,
        entitlements=entitlements_tuple,
        blocked=access_blocked,
        blocked_reason=blocked_reason,
        user_blocked=user_blocked,
        access_blocked=access_blocked,
        auth_unavailable=auth_unavailable,
        cloud_access=(
            _safe_bool(access_dict.get("cloud_access"), default=False)
            or _safe_bool(access_dict.get("can_use_cloud"), default=False)
            or bool(entitlement_set.intersection(CLOUD_ENTITLEMENTS))
        ),
        demo_project_access=(
            _safe_bool(access_dict.get("demo_project_access"), default=False)
            or bool(entitlement_set.intersection(DEMO_ENTITLEMENTS))
            or plan == "guest_demo"
            or plan_key == "guest_demo"
        ),
        api_key_access=(
            _safe_bool(access_dict.get("api_key_access"), default=False)
            or bool(entitlement_set.intersection(API_KEY_ENTITLEMENTS))
        ),
        raw=access_dict,
    )

    account_id = _safe_str(
        account_dict.get("account_id")
        or account_dict.get("id")
        or account_dict.get("account")
    ) or None

    account_role = _lower(
        account_dict.get("member_role")
        or account_dict.get("role")
        or account_dict.get("account_role")
    ) or None

    account_snapshot = AuthAccountSnapshot(
        available=_safe_bool(account_dict.get("available"), default=bool(account_id)),
        account_id=account_id,
        account_type=_safe_str(account_dict.get("account_type") or account_dict.get("type")) or None,
        account_name=_safe_str(account_dict.get("account_name") or account_dict.get("name")) or None,
        member_role=account_role,
        can_manage_account=(
            _safe_bool(account_dict.get("can_manage_account"), default=False)
            or account_role in {"owner", "admin", "manager"}
        ),
        can_manage_members=(
            _safe_bool(account_dict.get("can_manage_members"), default=False)
            or account_role in {"owner", "admin", "manager"}
        ),
        can_invite_members=(
            _safe_bool(account_dict.get("can_invite_members"), default=False)
            or account_role in {"owner", "admin", "manager"}
        ),
        raw=account_dict,
    )

    can_demo = bool(
        auth_available
        and not auth_unavailable
        and not authenticated
        and not access_blocked
        and access_snapshot.demo_project_access
    )

    can_persist = bool(
        auth_available
        and authenticated
        and user_id
        and not access_blocked
    )

    can_use_cloud = bool(
        auth_available
        and authenticated
        and not access_blocked
        and access_snapshot.cloud_access
    )

    can_manage_account = bool(
        auth_available
        and authenticated
        and not access_blocked
        and (
            account_snapshot.can_manage_account
            or bool(entitlement_set.intersection(ACCOUNT_MANAGEMENT_ENTITLEMENTS))
            or is_admin
        )
    )

    can_manage_members = bool(
        auth_available
        and authenticated
        and not access_blocked
        and (
            account_snapshot.can_manage_members
            or account_snapshot.can_invite_members
            or bool(entitlement_set.intersection(ACCOUNT_MANAGEMENT_ENTITLEMENTS))
            or is_admin
        )
    )

    can_manage_api_keys = bool(
        auth_available
        and authenticated
        and not access_blocked
        and (
            access_snapshot.api_key_access
            or bool(entitlement_set.intersection(API_KEY_ENTITLEMENTS))
            or is_admin
        )
    )

    can_project_sharing = bool(
        auth_available
        and authenticated
        and not access_blocked
        and (
            bool(entitlement_set.intersection(PROJECT_SHARING_ENTITLEMENTS))
            or account_snapshot.can_invite_members
            or is_admin
        )
    )

    dashboard_allowed = bool(
        auth_available
        and authenticated
        and not access_blocked
        and (
            _safe_bool(ui_dict.get("dashboard_allowed"), default=False)
            or _safe_bool(ui_dict.get("show_account_dashboard"), default=False)
            or account_snapshot.available
        )
    )

    requires_login = bool(not auth_unavailable and not authenticated and not can_demo and not access_blocked)

    next_target = next_url or _request_path("/")

    login_url = _safe_url_from_payload(candidates, client, "login", next_target)
    register_url = _safe_url_from_payload(candidates, client, "register", next_target)
    logout_url = _safe_url_from_payload(candidates, client, "logout", next_target)
    account_dashboard_url = _safe_url_from_payload(candidates, client, "account_dashboard", next_target)
    admin_dashboard_url = _safe_url_from_payload(candidates, client, "admin_dashboard", next_target)

    if not cleanup_dict:
        cleanup_dict = {
            "clear_session": False,
            "clear_frontend_storage": False,
            "preserve_client_identity": True,
        }

    if auth_state in STALE_AUTH_STATES:
        cleanup_dict.setdefault("clear_session", True)
        cleanup_dict.setdefault("clear_frontend_storage", True)
        cleanup_dict.setdefault("preserve_client_identity", True)

    if user_blocked:
        cleanup_dict.setdefault("clear_frontend_storage", True)
        cleanup_dict.setdefault("preserve_client_identity", True)

    if auth_unavailable:
        cleanup_dict.setdefault("clear_session", False)
        cleanup_dict.setdefault("clear_frontend_storage", False)
        cleanup_dict.setdefault("preserve_client_identity", True)

    all_errors = list(errors or [])
    payload_error = _safe_str(raw_payload.get("error"))
    if payload_error:
        all_errors.append(payload_error)

    if auth_unavailable and not all_errors:
        all_errors.append(reason_code or auth_state or "auth_service_unavailable")

    if not dependency_dict and auth_unavailable:
        dependency_dict = {
            "name": "vectoplan-auth",
            "available": False,
            "state": auth_state,
            "code": reason_code or auth_state,
            "status_code": status_code,
        }

    return AuthContext(
        ok=ok,
        status_code=status_code,
        source=source,
        auth_available=auth_available,
        auth_unavailable=auth_unavailable,
        auth_dependency=dependency_dict,
        authenticated=authenticated,
        raw_authenticated=raw_authenticated,
        auth_state=auth_state,
        reason=reason,
        reason_code=reason_code,
        session_valid=session_valid,
        subject_type=subject_type,
        subject_id=subject_id,
        user_id=user_id,
        user=user_snapshot,
        account=account_snapshot,
        access=access_snapshot,
        roles=roles,
        primary_role=primary_role,
        is_admin=is_admin,
        is_staff=is_staff,
        is_system=is_system,
        blocked=access_blocked,
        blocked_reason=blocked_reason,
        blocked_kind=blocked_kind,
        user_blocked=user_blocked,
        access_blocked=access_blocked,
        is_guest=is_guest,
        can_demo=can_demo,
        can_persist=can_persist,
        can_use_cloud=can_use_cloud,
        can_manage_account=can_manage_account,
        can_manage_members=can_manage_members,
        can_manage_api_keys=can_manage_api_keys,
        can_project_sharing=can_project_sharing,
        dashboard_allowed=dashboard_allowed,
        requires_login=requires_login,
        language=language_dict,
        session=session_dict,
        client_identity=client_identity_dict,
        api_key=api_key_dict,
        ui=ui_dict,
        redirect=redirect_dict,
        cleanup=cleanup_dict,
        security=security_dict,
        login_url=login_url,
        register_url=register_url,
        logout_url=logout_url,
        account_dashboard_url=account_dashboard_url,
        admin_dashboard_url=admin_dashboard_url,
        raw_payload=raw_payload,
        errors=tuple(dict.fromkeys(all_errors)),
    )


# ─────────────────────────────────────────────────────────────
# Load current context
# ─────────────────────────────────────────────────────────────

def get_current_auth_context(
    *,
    minimal: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
    incoming_headers: Optional[Mapping[str, Any]] = None,
    client: Optional[VectoplanAuthClient] = None,
    next_url: Optional[str] = None,
) -> AuthContext:
    auth_client = client or get_auth_context_client()
    cache_key = _auth_cache_key(minimal=minimal, incoming_headers=incoming_headers)

    if use_cache and not force_refresh:
        cache = _get_request_cache()
        if cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

    try:
        payload = auth_client.context(
            incoming_headers=incoming_headers,
            minimal=minimal,
            use_cache=use_cache and not force_refresh,
        )
        context = normalize_auth_payload(
            payload=payload,
            source="/auth/context/minimal" if minimal else "/auth/context",
            client=auth_client,
            next_url=next_url,
        )
    except Exception as exc:
        LOGGER.warning("Failed to load current auth context: %s", exc)
        payload = make_auth_unavailable_payload(endpoint="/auth/context/minimal" if minimal else "/auth/context")
        context = normalize_auth_payload(
            payload=payload,
            source="auth_context_error",
            client=auth_client,
            next_url=next_url,
            errors=[f"auth_context_error: {exc}"],
        )

    if use_cache:
        cache = _get_request_cache()
        if cache is not None:
            cache[cache_key] = context

    return context


def get_current_auth_context_full(
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    incoming_headers: Optional[Mapping[str, Any]] = None,
    client: Optional[VectoplanAuthClient] = None,
    next_url: Optional[str] = None,
) -> AuthContext:
    return get_current_auth_context(
        minimal=False,
        use_cache=use_cache,
        force_refresh=force_refresh,
        incoming_headers=incoming_headers,
        client=client,
        next_url=next_url,
    )


def get_current_auth_me_context(
    *,
    use_cache: bool = True,
    force_refresh: bool = False,
    incoming_headers: Optional[Mapping[str, Any]] = None,
    client: Optional[VectoplanAuthClient] = None,
    next_url: Optional[str] = None,
) -> AuthContext:
    auth_client = client or get_auth_context_client()
    cache_key = _auth_cache_key(
        minimal=True,
        incoming_headers=incoming_headers,
        extra="me",
    )

    if use_cache and not force_refresh:
        cache = _get_request_cache()
        if cache is not None:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

    try:
        payload = auth_client.me(
            incoming_headers=incoming_headers,
            use_cache=use_cache and not force_refresh,
        )
        context = normalize_auth_payload(
            payload=payload,
            source="/auth/me",
            client=auth_client,
            next_url=next_url,
        )
    except Exception as exc:
        LOGGER.warning("Failed to load /auth/me context: %s", exc)
        payload = make_auth_unavailable_payload(endpoint="/auth/me")
        context = normalize_auth_payload(
            payload=payload,
            source="auth_me_error",
            client=auth_client,
            next_url=next_url,
            errors=[f"auth_me_error: {exc}"],
        )

    if use_cache:
        cache = _get_request_cache()
        if cache is not None:
            cache[cache_key] = context

    return context


def clear_current_auth_context_cache() -> None:
    try:
        if has_request_context() and g is not None:
            setattr(g, REQUEST_CONTEXT_CACHE_ATTR, {})
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Predicate helpers
# ─────────────────────────────────────────────────────────────

def is_authenticated_context(context: Optional[AuthContext]) -> bool:
    return bool(context and context.authenticated and context.user_id and not context.access_blocked)


def is_demo_context(context: Optional[AuthContext]) -> bool:
    return bool(context and context.can_demo and not context.authenticated and not context.access_blocked and not context.auth_unavailable)


def can_persist_context(context: Optional[AuthContext]) -> bool:
    return bool(context and context.can_persist and context.authenticated and context.user_id and not context.access_blocked)


def context_requires_login(context: Optional[AuthContext]) -> bool:
    return bool(context is None or context.requires_login)


def context_is_blocked(context: Optional[AuthContext]) -> bool:
    return bool(context and context.effective_blocked)


def context_access_blocked(context: Optional[AuthContext]) -> bool:
    return bool(context and context.access_blocked)


def context_user_blocked(context: Optional[AuthContext]) -> bool:
    return bool(context and context.user_blocked)


def context_auth_unavailable(context: Optional[AuthContext]) -> bool:
    return bool(context and context.auth_unavailable)


def context_denial_status_code(context: Optional[AuthContext]) -> int:
    if context is None:
        return 401
    return context.deny_status_code()


def get_auth_template_context(
    *,
    minimal: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    context = get_current_auth_context(
        minimal=minimal,
        use_cache=use_cache,
        force_refresh=force_refresh,
    )
    return context.to_template_context()


def get_auth_app_config(
    *,
    minimal: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    context = get_current_auth_context(
        minimal=minimal,
        use_cache=use_cache,
        force_refresh=force_refresh,
    )
    return context.to_app_config()


def get_auth_context_status(force_refresh: bool = False) -> Dict[str, Any]:
    dependency = {}
    if get_auth_dependency_status is not None:
        try:
            dependency = get_auth_dependency_status(force_refresh=force_refresh, include_private=False)
        except Exception as exc:
            dependency = {
                "ok": False,
                "error": str(exc),
                "code": "auth_dependency_status_failed",
            }

    return {
        "ok": True,
        "service": "auth_context",
        "auth_dependency": dependency,
        "rules": {
            "authenticated_requires_user_id": True,
            "guest_is_not_user": True,
            "auth_unavailable_is_not_user_ban": True,
            "auth_unavailable_fail_closed": True,
            "default_user": False,
            "demo_requires_auth_context": True,
        },
    }


__all__ = [
    "ACCOUNT_MANAGEMENT_ENTITLEMENTS",
    "ADMIN_ROLES",
    "API_KEY_ENTITLEMENTS",
    "AUTH_UNAVAILABLE_STATES",
    "AuthAccessSnapshot",
    "AuthAccountSnapshot",
    "AuthContext",
    "AuthUserSnapshot",
    "BLOCKING_AUTH_STATES",
    "BLOCKING_PLAN_STATUSES",
    "BLOCKING_USER_STATUSES",
    "CLOUD_ENTITLEMENTS",
    "DEMO_ENTITLEMENTS",
    "GUEST_AUTH_STATES",
    "PROJECT_SHARING_ENTITLEMENTS",
    "STALE_AUTH_STATES",
    "USER_BLOCKING_AUTH_STATES",
    "can_persist_context",
    "clear_current_auth_context_cache",
    "context_access_blocked",
    "context_auth_unavailable",
    "context_denial_status_code",
    "context_is_blocked",
    "context_requires_login",
    "context_user_blocked",
    "get_auth_app_config",
    "get_auth_context_status",
    "get_auth_template_context",
    "get_current_auth_context",
    "get_current_auth_context_full",
    "get_current_auth_me_context",
    "is_authenticated_context",
    "is_demo_context",
    "normalize_auth_payload",
]