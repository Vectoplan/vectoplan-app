# services/vectoplan-app/services/auth_requirements.py
from __future__ import annotations

"""
Zentrale Auth-/Access-Requirement-Schicht für vectoplan-app.

Diese Datei baut auf services/auth_context.py auf und stellt einheitliche Guards
für Routes, APIs und Services bereit.

Ziele:
- keine verstreuten Auth-Entscheidungen in Routes
- kein Vertrauen in LocalStorage, ClientIdentity oder Legacy-Session-Felder
- blockierte/gebannte User immer zentral sperren
- Auth-Service-Ausfall getrennt von echter User-Sperre behandeln
- Guest-Demo sauber von persistentem User-Zugriff trennen
- Login-Redirects, JSON-401/403/503 und HTML-Fehler zentral erzeugen
- spätere Entitlement-/Account-/Plan-Regeln ohne Umbau vieler Dateien erweiterbar machen

Wichtige Regeln:
- vectoplan-auth ist die Wahrheit.
- authenticated=true + user.id + blocked=false ist Pflicht für echte User-Funktionen.
- Guest darf nur Demo-Funktionen, wenn demo_project_access vorhanden ist.
- auth_unavailable/service_unavailable darf nicht still in Demo-Fallback übergehen.
- auth_unavailable ist 503 und kein echter User-Ban.
- user_blocked/banned ist 403.
- Team/Admin/Publication/Invitation brauchen persistente authentifizierte User.
- Kein Default-User.
- Kein lokaler Dev-User.
"""

import functools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, TypeVar, Union

try:
    from flask import (
        Blueprint,
        Response,
        abort,
        current_app,
        has_app_context,
        has_request_context,
        jsonify,
        make_response,
        redirect,
        render_template_string,
        request,
    )
except Exception:  # pragma: no cover
    Blueprint = Any  # type: ignore
    Response = Any  # type: ignore
    current_app = None  # type: ignore
    request = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False

    def has_request_context() -> bool:  # type: ignore
        return False

    def jsonify(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore
        if args and isinstance(args[0], dict):
            return args[0]
        return dict(kwargs)

    def make_response(value: Any, status: Optional[int] = None) -> Any:  # type: ignore
        return value

    def redirect(location: str, code: int = 302) -> Dict[str, Any]:  # type: ignore
        return {"redirect": location, "status_code": code}

    def abort(status_code: int) -> None:  # type: ignore
        raise RuntimeError(f"abort({status_code})")

    def render_template_string(template: str, **context: Any) -> str:  # type: ignore
        return template


try:
    from services.auth_context import (  # type: ignore
        AuthContext,
        can_persist_context,
        context_access_blocked,
        context_auth_unavailable,
        context_denial_status_code,
        context_is_blocked,
        context_requires_login,
        context_user_blocked,
        get_current_auth_context,
        get_current_auth_context_full,
        get_current_auth_me_context,
        is_authenticated_context,
        is_demo_context,
    )
except Exception:  # pragma: no cover
    try:
        from .auth_context import (  # type: ignore
            AuthContext,
            can_persist_context,
            context_access_blocked,
            context_auth_unavailable,
            context_denial_status_code,
            context_is_blocked,
            context_requires_login,
            context_user_blocked,
            get_current_auth_context,
            get_current_auth_context_full,
            get_current_auth_me_context,
            is_authenticated_context,
            is_demo_context,
        )
    except Exception:  # pragma: no cover
        AuthContext = Any  # type: ignore

        def can_persist_context(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "can_persist", False))

        def context_access_blocked(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "access_blocked", getattr(context, "blocked", False)))

        def context_auth_unavailable(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "auth_unavailable", False))

        def context_denial_status_code(context: Any) -> int:  # type: ignore
            if context_auth_unavailable(context):
                return 503
            if context_is_blocked(context):
                return 403
            return 401

        def context_is_blocked(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "blocked", False))

        def context_requires_login(context: Any) -> bool:  # type: ignore
            return bool(context is None or getattr(context, "requires_login", True))

        def context_user_blocked(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "user_blocked", False))

        def get_current_auth_context(*args: Any, **kwargs: Any) -> Any:  # type: ignore
            return None

        def get_current_auth_context_full(*args: Any, **kwargs: Any) -> Any:  # type: ignore
            return None

        def get_current_auth_me_context(*args: Any, **kwargs: Any) -> Any:  # type: ignore
            return None

        def is_authenticated_context(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "authenticated", False) and getattr(context, "user_id", None) and not context_is_blocked(context))

        def is_demo_context(context: Any) -> bool:  # type: ignore
            return bool(context and getattr(context, "can_demo", False) and not getattr(context, "authenticated", False) and not context_is_blocked(context))


LOGGER = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

AUTH_DECISION_ALLOW = "allow"
AUTH_DECISION_DENY = "deny"
AUTH_DECISION_REDIRECT = "redirect"

DEFAULT_LOGIN_STATUS = 401
DEFAULT_BLOCKED_STATUS = 403
DEFAULT_FORBIDDEN_STATUS = 403
DEFAULT_SERVICE_UNAVAILABLE_STATUS = 503

ADMIN_REQUIRED_ROLES = {
    "admin",
    "staff",
    "system_admin",
    "system",
}

MANAGE_ACCOUNT_ROLES = {
    "owner",
    "admin",
    "manager",
}

ACCOUNT_MEMBER_ROLES = {
    "owner",
    "admin",
    "manager",
    "member",
    "billing",
    "viewer",
    "support",
}

WRITE_METHODS = {
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
}

SAFE_METHODS = {
    "GET",
    "HEAD",
    "OPTIONS",
}

AUTH_UNAVAILABLE_REASONS = {
    "auth_unavailable",
    "auth_service_unavailable",
    "service_unavailable",
    "dependency_unavailable",
    "upstream_unavailable",
    "storage_unavailable",
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
}

USER_BLOCKED_REASONS = {
    "blocked",
    "banned",
    "user_blocked",
    "user_banned",
    "account_blocked",
    "account_banned",
    "subscription_blocked",
    "plan_blocked",
    "security_blocked",
    "disabled",
    "inactive",
    "suspended",
    "deleted",
    "locked",
}


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

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
        if text in {"1", "true", "yes", "y", "on", "enabled", "enable", "active", "ok", "ready"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "disable", "inactive", "error", "failed"}:
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


def _config_bool(name: str, default: bool = False) -> bool:
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return _safe_bool(current_app.config.get(name), default=default)
    except Exception:
        pass
    return default


def _config_str(name: str, default: str = "") -> str:
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return _safe_str(current_app.config.get(name), default=default)
    except Exception:
        pass
    return default


def _current_method(default: str = "GET") -> str:
    try:
        if has_request_context() and request is not None:
            return _safe_str(request.method, default=default).upper()
    except Exception:
        pass
    return default


def _current_path(default: str = "/") -> str:
    try:
        if has_request_context() and request is not None:
            full_path = request.full_path or request.path or default
            if full_path.endswith("?"):
                full_path = full_path[:-1]
            return full_path or default
    except Exception:
        pass
    return default


def _request_wants_json(default: bool = False) -> bool:
    try:
        if not has_request_context() or request is None:
            return default

        if request.path.startswith("/v1/") or request.path.endswith(".json"):
            return True

        if request.headers.get("X-Requested-With", "").lower() in {"xmlhttprequest", "fetch", "service"}:
            return True

        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return True

        if request.is_json:
            return True

        return default
    except Exception:
        return default


def _as_tuple(values: Optional[Union[str, Iterable[str]]]) -> Tuple[str, ...]:
    if values is None:
        return tuple()
    if isinstance(values, str):
        if "," in values:
            return tuple(item.strip() for item in values.split(",") if item.strip())
        text = values.strip()
        return (text,) if text else tuple()
    return tuple(_safe_str(value) for value in values if _safe_str(value))


def _normalize_role_values(values: Optional[Union[str, Iterable[str]]]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_lower(value) for value in _as_tuple(values) if _safe_str(value)))


def _normalize_entitlement_values(values: Optional[Union[str, Iterable[str]]]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_safe_str(value) for value in _as_tuple(values) if _safe_str(value)))


def _context_attr(context: Optional[AuthContext], name: str, default: Any = None) -> Any:
    try:
        if context is None:
            return default
        return getattr(context, name, default)
    except Exception:
        return default


def _context_public_dict(context: Optional[AuthContext]) -> Dict[str, Any]:
    try:
        if context is not None and hasattr(context, "to_public_dict"):
            return _safe_dict(context.to_public_dict(include_raw=False))
    except Exception:
        pass
    return {}


def _context_plan(context: Optional[AuthContext]) -> Optional[str]:
    try:
        return _safe_str(getattr(context, "plan", None), "", 120) or None
    except Exception:
        return None


def _context_auth_is_unavailable(context: Optional[AuthContext]) -> bool:
    try:
        if context_auth_unavailable is not None:
            return bool(context_auth_unavailable(context))
    except Exception:
        pass

    auth_state = _lower(_context_attr(context, "auth_state", ""))
    reason_code = _lower(_context_attr(context, "reason_code", ""))
    blocked_kind = _lower(_context_attr(context, "blocked_kind", ""))
    return bool(
        _safe_bool(_context_attr(context, "auth_unavailable", False), False)
        or auth_state in AUTH_UNAVAILABLE_REASONS
        or reason_code in AUTH_UNAVAILABLE_REASONS
        or blocked_kind == "auth_unavailable"
    )


def _context_user_is_blocked(context: Optional[AuthContext]) -> bool:
    try:
        if context_user_blocked is not None:
            return bool(context_user_blocked(context))
    except Exception:
        pass

    reason = _lower(_context_attr(context, "blocked_reason", ""))
    auth_state = _lower(_context_attr(context, "auth_state", ""))
    blocked_kind = _lower(_context_attr(context, "blocked_kind", ""))
    return bool(
        _safe_bool(_context_attr(context, "user_blocked", False), False)
        or blocked_kind == "user_blocked"
        or reason in USER_BLOCKED_REASONS
        or auth_state in USER_BLOCKED_REASONS
    )


def _context_access_is_blocked(context: Optional[AuthContext]) -> bool:
    try:
        if context_access_blocked is not None:
            return bool(context_access_blocked(context))
    except Exception:
        pass

    return bool(
        _safe_bool(_context_attr(context, "access_blocked", False), False)
        or _safe_bool(_context_attr(context, "blocked", False), False)
    )


def _context_effectively_blocked(context: Optional[AuthContext]) -> bool:
    try:
        if context_is_blocked is not None and context_is_blocked(context):
            return True
    except Exception:
        pass

    return bool(
        _context_auth_is_unavailable(context)
        or _context_user_is_blocked(context)
        or _context_access_is_blocked(context)
    )


def _context_denial_code(context: Optional[AuthContext]) -> int:
    try:
        if context_denial_status_code is not None:
            status = int(context_denial_status_code(context))
            if status > 0:
                return status
    except Exception:
        pass

    if _context_auth_is_unavailable(context):
        return DEFAULT_SERVICE_UNAVAILABLE_STATUS
    if _context_user_is_blocked(context) or _context_access_is_blocked(context):
        return DEFAULT_BLOCKED_STATUS
    if context is None or context_requires_login(context):
        return DEFAULT_LOGIN_STATUS
    return DEFAULT_FORBIDDEN_STATUS


def _context_login_url(context: Optional[AuthContext]) -> Optional[str]:
    try:
        if context is not None:
            value = _safe_str(getattr(context, "login_url", None), "", 800)
            if value:
                return value
    except Exception:
        pass

    public = _context_public_dict(context)
    links = _safe_dict(public.get("links"))
    return _safe_str(links.get("login_url") or links.get("login"), "", 800) or None


def _is_auth_unavailable_reason(value: Any) -> bool:
    return _lower(value) in AUTH_UNAVAILABLE_REASONS


# ─────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────

@dataclass
class AuthRequirementResult:
    """
    Ergebnis einer Auth-/Access-Prüfung.
    """

    allowed: bool
    status_code: int = 200
    action: str = AUTH_DECISION_ALLOW
    reason: str = "allowed"
    message: str = "Allowed."
    context: Optional[AuthContext] = None
    required_entitlements: Tuple[str, ...] = field(default_factory=tuple)
    required_roles: Tuple[str, ...] = field(default_factory=tuple)
    required_account_roles: Tuple[str, ...] = field(default_factory=tuple)
    required_plan: Optional[str] = None
    redirect_url: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def denied(self) -> bool:
        return not self.allowed

    @property
    def should_redirect(self) -> bool:
        return self.action == AUTH_DECISION_REDIRECT and bool(self.redirect_url)

    @property
    def auth_unavailable(self) -> bool:
        return bool(_context_auth_is_unavailable(self.context) or self.status_code == DEFAULT_SERVICE_UNAVAILABLE_STATUS)

    def to_dict(self, include_context: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "ok": bool(self.allowed),
            "allowed": bool(self.allowed),
            "status_code": self.status_code,
            "action": self.action,
            "reason": self.reason,
            "message": self.message,
            "required_entitlements": list(self.required_entitlements),
            "required_roles": list(self.required_roles),
            "required_account_roles": list(self.required_account_roles),
            "required_plan": self.required_plan,
            "redirect_url": self.redirect_url,
            "details": self.details,
        }

        if self.context is not None:
            data["auth"] = {
                "authenticated": _safe_bool(_context_attr(self.context, "authenticated", False), False),
                "auth_available": _safe_bool(_context_attr(self.context, "auth_available", False), False),
                "auth_unavailable": _context_auth_is_unavailable(self.context),
                "auth_state": _safe_str(_context_attr(self.context, "auth_state", ""), "", 120),
                "blocked": _safe_bool(_context_attr(self.context, "blocked", False), False),
                "blocked_reason": _safe_str(_context_attr(self.context, "blocked_reason", ""), "", 160),
                "blocked_kind": _safe_str(_context_attr(self.context, "blocked_kind", ""), "", 120),
                "user_blocked": _context_user_is_blocked(self.context),
                "access_blocked": _context_access_is_blocked(self.context),
                "is_guest": _safe_bool(_context_attr(self.context, "is_guest", False), False),
                "can_demo": _safe_bool(_context_attr(self.context, "can_demo", False), False),
                "can_persist": _safe_bool(_context_attr(self.context, "can_persist", False), False),
                "user_id": _context_attr(self.context, "user_id", None),
                "app_user_id": _context_attr(self.context, "app_user_id", None),
                "account_id": _context_attr(self.context, "account_id", None),
                "plan": _context_plan(self.context),
            }

        if include_context and self.context is not None:
            data["auth_context"] = _context_public_dict(self.context)

        return data


class AuthRequirementError(PermissionError):
    """
    Exception-Variante für Services, die lieber Exceptions als Response-Objekte nutzen.
    """

    def __init__(self, result: AuthRequirementResult) -> None:
        self.result = result
        self.status_code = result.status_code
        self.code = result.reason
        super().__init__(result.message)

    def to_dict(self) -> Dict[str, Any]:
        return self.result.to_dict(include_context=True)


# ─────────────────────────────────────────────────────────────
# Result builders
# ─────────────────────────────────────────────────────────────

def allow_result(context: Optional[AuthContext] = None, reason: str = "allowed") -> AuthRequirementResult:
    return AuthRequirementResult(
        allowed=True,
        status_code=200,
        action=AUTH_DECISION_ALLOW,
        reason=reason,
        message="Allowed.",
        context=context,
    )


def deny_result(
    *,
    context: Optional[AuthContext],
    status_code: int,
    reason: str,
    message: str,
    action: str = AUTH_DECISION_DENY,
    redirect_url: Optional[str] = None,
    required_entitlements: Optional[Union[str, Iterable[str]]] = None,
    required_roles: Optional[Union[str, Iterable[str]]] = None,
    required_account_roles: Optional[Union[str, Iterable[str]]] = None,
    required_plan: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> AuthRequirementResult:
    return AuthRequirementResult(
        allowed=False,
        status_code=status_code,
        action=action,
        reason=reason,
        message=message,
        context=context,
        required_entitlements=_normalize_entitlement_values(required_entitlements),
        required_roles=_normalize_role_values(required_roles),
        required_account_roles=_normalize_role_values(required_account_roles),
        required_plan=required_plan,
        redirect_url=redirect_url,
        details=dict(details or {}),
    )


# ─────────────────────────────────────────────────────────────
# Context loading
# ─────────────────────────────────────────────────────────────

def load_auth_context(
    *,
    minimal: bool = True,
    full: bool = False,
    me_only: bool = False,
    use_cache: bool = True,
    force_refresh: bool = False,
    next_url: Optional[str] = None,
) -> AuthContext:
    """
    Lädt den aktuellen AuthContext.

    Standard:
    - /auth/context/minimal
    - request-lokal gecacht
    """
    if me_only:
        return get_current_auth_me_context(
            use_cache=use_cache,
            force_refresh=force_refresh,
            next_url=next_url,
        )

    if full:
        return get_current_auth_context_full(
            use_cache=use_cache,
            force_refresh=force_refresh,
            next_url=next_url,
        )

    return get_current_auth_context(
        minimal=minimal,
        use_cache=use_cache,
        force_refresh=force_refresh,
        next_url=next_url,
    )


# ─────────────────────────────────────────────────────────────
# Evaluation functions
# ─────────────────────────────────────────────────────────────

def evaluate_not_blocked(context: Optional[AuthContext]) -> AuthRequirementResult:
    if context is None:
        return deny_result(
            context=None,
            status_code=DEFAULT_SERVICE_UNAVAILABLE_STATUS,
            reason="auth_context_missing",
            message="Auth context is unavailable.",
        )

    if _context_auth_is_unavailable(context):
        reason = (
            _safe_str(_context_attr(context, "reason_code", ""), "", 160)
            or _safe_str(_context_attr(context, "blocked_reason", ""), "", 160)
            or _safe_str(_context_attr(context, "auth_state", ""), "", 160)
            or "auth_service_unavailable"
        )

        return deny_result(
            context=context,
            status_code=DEFAULT_SERVICE_UNAVAILABLE_STATUS,
            reason=reason,
            message="vectoplan-auth is unavailable.",
            details={
                "auth_state": _safe_str(_context_attr(context, "auth_state", ""), "", 120),
                "auth_unavailable": True,
                "blocked_kind": _safe_str(_context_attr(context, "blocked_kind", ""), "", 120),
            },
        )

    if _context_user_is_blocked(context):
        reason = (
            _safe_str(_context_attr(context, "blocked_reason", ""), "", 160)
            or _safe_str(_context_attr(context, "reason_code", ""), "", 160)
            or _safe_str(_context_attr(context, "auth_state", ""), "", 160)
            or "user_blocked"
        )

        return deny_result(
            context=context,
            status_code=DEFAULT_BLOCKED_STATUS,
            reason=reason,
            message="User access is blocked.",
            details={
                "auth_state": _safe_str(_context_attr(context, "auth_state", ""), "", 120),
                "user_blocked": True,
                "blocked_reason": reason,
                "blocked_kind": _safe_str(_context_attr(context, "blocked_kind", "user_blocked"), "", 120),
            },
        )

    if _context_access_is_blocked(context):
        status = _context_denial_code(context)
        if status == DEFAULT_SERVICE_UNAVAILABLE_STATUS:
            status = DEFAULT_SERVICE_UNAVAILABLE_STATUS
        elif status <= 0:
            status = DEFAULT_BLOCKED_STATUS

        reason = (
            _safe_str(_context_attr(context, "blocked_reason", ""), "", 160)
            or _safe_str(_context_attr(context, "reason_code", ""), "", 160)
            or _safe_str(_context_attr(context, "auth_state", ""), "", 160)
            or "access_blocked"
        )

        return deny_result(
            context=context,
            status_code=status,
            reason=reason,
            message="Access is blocked.",
            details={
                "auth_state": _safe_str(_context_attr(context, "auth_state", ""), "", 120),
                "access_blocked": True,
                "blocked_reason": reason,
                "blocked_kind": _safe_str(_context_attr(context, "blocked_kind", "access_blocked"), "", 120),
            },
        )

    return allow_result(context=context)


def evaluate_authenticated(context: Optional[AuthContext]) -> AuthRequirementResult:
    blocked_check = evaluate_not_blocked(context)
    if not blocked_check.allowed:
        return blocked_check

    assert context is not None

    if is_authenticated_context(context):
        return allow_result(context=context)

    return deny_result(
        context=context,
        status_code=DEFAULT_LOGIN_STATUS,
        action=AUTH_DECISION_REDIRECT if not _request_wants_json(default=False) else AUTH_DECISION_DENY,
        reason=_safe_str(_context_attr(context, "reason_code", ""), "", 160)
        or _safe_str(_context_attr(context, "reason", ""), "", 160)
        or "authentication_required",
        message="Login required.",
        redirect_url=_context_login_url(context),
        details={
            "auth_state": _safe_str(_context_attr(context, "auth_state", ""), "", 120),
            "is_guest": _safe_bool(_context_attr(context, "is_guest", False), False),
            "can_demo": _safe_bool(_context_attr(context, "can_demo", False), False),
        },
    )


def evaluate_persistent_user(context: Optional[AuthContext]) -> AuthRequirementResult:
    authenticated_check = evaluate_authenticated(context)
    if not authenticated_check.allowed:
        return authenticated_check

    assert context is not None

    if can_persist_context(context):
        return allow_result(context=context)

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="persistent_user_required",
        message="A persistent authenticated user is required.",
        details={
            "can_persist": _safe_bool(_context_attr(context, "can_persist", False), False),
            "user_id": _context_attr(context, "user_id", None),
            "app_user_id": _context_attr(context, "app_user_id", None),
        },
    )


def evaluate_demo_or_authenticated(context: Optional[AuthContext]) -> AuthRequirementResult:
    blocked_check = evaluate_not_blocked(context)
    if not blocked_check.allowed:
        return blocked_check

    assert context is not None

    if is_authenticated_context(context):
        return allow_result(context=context, reason="authenticated")

    if is_demo_context(context):
        return allow_result(context=context, reason="demo_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_LOGIN_STATUS,
        action=AUTH_DECISION_REDIRECT if not _request_wants_json(default=False) else AUTH_DECISION_DENY,
        reason="login_or_demo_required",
        message="Login or demo access is required.",
        redirect_url=_context_login_url(context),
        details={
            "auth_state": _safe_str(_context_attr(context, "auth_state", ""), "", 120),
            "can_demo": _safe_bool(_context_attr(context, "can_demo", False), False),
            "entitlements": list(getattr(getattr(context, "access", None), "entitlements", tuple()) or tuple()),
        },
    )


def evaluate_entitlement(
    context: Optional[AuthContext],
    entitlement: str,
    *,
    allow_admin: bool = True,
    allow_demo: bool = False,
) -> AuthRequirementResult:
    return evaluate_any_entitlement(
        context,
        [entitlement],
        allow_admin=allow_admin,
        allow_demo=allow_demo,
        require_all=False,
    )


def evaluate_any_entitlement(
    context: Optional[AuthContext],
    entitlements: Union[str, Iterable[str]],
    *,
    allow_admin: bool = True,
    allow_demo: bool = False,
    require_all: bool = False,
) -> AuthRequirementResult:
    entitlements_tuple = _normalize_entitlement_values(entitlements)

    if not entitlements_tuple:
        return evaluate_authenticated(context)

    if allow_demo:
        base_check = evaluate_demo_or_authenticated(context)
    else:
        base_check = evaluate_authenticated(context)

    if not base_check.allowed:
        return base_check

    assert context is not None

    if allow_admin and _safe_bool(_context_attr(context, "is_admin", False), False):
        return allow_result(context=context, reason="admin_override")

    current = set(getattr(getattr(context, "access", None), "entitlements", tuple()) or tuple())
    required = set(entitlements_tuple)

    if require_all:
        has_required = required.issubset(current)
    else:
        has_required = bool(required.intersection(current))

    if has_required:
        return allow_result(context=context, reason="entitlement_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="entitlement_required",
        message="Required entitlement is missing.",
        required_entitlements=entitlements_tuple,
        details={
            "current_entitlements": list(current),
            "require_all": require_all,
        },
    )


def evaluate_role(
    context: Optional[AuthContext],
    roles: Union[str, Iterable[str]],
    *,
    allow_admin: bool = True,
) -> AuthRequirementResult:
    roles_tuple = _normalize_role_values(roles)
    base_check = evaluate_authenticated(context)
    if not base_check.allowed:
        return base_check

    assert context is not None

    if allow_admin and _safe_bool(_context_attr(context, "is_admin", False), False):
        return allow_result(context=context, reason="admin_override")

    try:
        if context.has_any_role(roles_tuple):
            return allow_result(context=context, reason="role_allowed")
    except Exception:
        current_roles = _normalize_role_values(getattr(context, "roles", tuple()))
        if set(current_roles).intersection(set(roles_tuple)):
            return allow_result(context=context, reason="role_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="role_required",
        message="Required role is missing.",
        required_roles=roles_tuple,
        details={
            "current_roles": list(getattr(context, "roles", tuple()) or tuple()),
        },
    )


def evaluate_admin(context: Optional[AuthContext]) -> AuthRequirementResult:
    base_check = evaluate_authenticated(context)
    if not base_check.allowed:
        return base_check

    assert context is not None

    try:
        has_admin_role = context.has_any_role(ADMIN_REQUIRED_ROLES)
    except Exception:
        has_admin_role = bool(set(_normalize_role_values(getattr(context, "roles", tuple()))).intersection(ADMIN_REQUIRED_ROLES))

    if (
        _safe_bool(_context_attr(context, "is_admin", False), False)
        or _safe_bool(_context_attr(context, "is_staff", False), False)
        or _safe_bool(_context_attr(context, "is_system", False), False)
        or has_admin_role
    ):
        return allow_result(context=context, reason="admin_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="admin_required",
        message="Admin access is required.",
        required_roles=ADMIN_REQUIRED_ROLES,
        details={
            "current_roles": list(getattr(context, "roles", tuple()) or tuple()),
        },
    )


def evaluate_account(
    context: Optional[AuthContext],
    *,
    require_account_id: bool = True,
) -> AuthRequirementResult:
    base_check = evaluate_authenticated(context)
    if not base_check.allowed:
        return base_check

    assert context is not None

    account = getattr(context, "account", None)
    account_available = _safe_bool(getattr(account, "available", False), False)
    account_id = _safe_str(getattr(account, "account_id", None), "", 160)

    if account_available and (account_id or not require_account_id):
        return allow_result(context=context, reason="account_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="account_required",
        message="Account context is required.",
        details={
            "account": account.to_dict() if hasattr(account, "to_dict") else {},
        },
    )


def evaluate_account_role(
    context: Optional[AuthContext],
    roles: Union[str, Iterable[str]],
    *,
    allow_admin: bool = True,
) -> AuthRequirementResult:
    roles_tuple = _normalize_role_values(roles)
    account_check = evaluate_account(context)
    if not account_check.allowed:
        return account_check

    assert context is not None

    if allow_admin and _safe_bool(_context_attr(context, "is_admin", False), False):
        return allow_result(context=context, reason="admin_override")

    account = getattr(context, "account", None)
    current_role = _lower(getattr(account, "member_role", None))
    if current_role and current_role in set(roles_tuple):
        return allow_result(context=context, reason="account_role_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="account_role_required",
        message="Required account role is missing.",
        required_account_roles=roles_tuple,
        details={
            "current_account_role": getattr(account, "member_role", None),
        },
    )


def evaluate_account_management(context: Optional[AuthContext]) -> AuthRequirementResult:
    account_check = evaluate_account(context)
    if not account_check.allowed:
        return account_check

    assert context is not None

    if _safe_bool(_context_attr(context, "can_manage_account", False), False):
        return allow_result(context=context, reason="account_management_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="account_management_required",
        message="Account management permission is required.",
        required_account_roles=MANAGE_ACCOUNT_ROLES,
    )


def evaluate_member_management(context: Optional[AuthContext]) -> AuthRequirementResult:
    account_check = evaluate_account(context)
    if not account_check.allowed:
        return account_check

    assert context is not None

    if _safe_bool(_context_attr(context, "can_manage_members", False), False):
        return allow_result(context=context, reason="member_management_allowed")

    return deny_result(
        context=context,
        status_code=DEFAULT_FORBIDDEN_STATUS,
        reason="member_management_required",
        message="Member management permission is required.",
        required_account_roles=MANAGE_ACCOUNT_ROLES,
    )


def evaluate_write_allowed(context: Optional[AuthContext]) -> AuthRequirementResult:
    method = _current_method()
    if method in SAFE_METHODS:
        return evaluate_demo_or_authenticated(context)

    return evaluate_persistent_user(context)


# ─────────────────────────────────────────────────────────────
# Require wrappers
# ─────────────────────────────────────────────────────────────

def require_auth_context(
    *,
    minimal: bool = True,
    full: bool = False,
    me_only: bool = False,
    use_cache: bool = True,
    force_refresh: bool = False,
    raise_error: bool = False,
) -> AuthContext:
    context = load_auth_context(
        minimal=minimal,
        full=full,
        me_only=me_only,
        use_cache=use_cache,
        force_refresh=force_refresh,
    )

    blocked_check = evaluate_not_blocked(context)
    if raise_error and not blocked_check.allowed:
        raise AuthRequirementError(blocked_check)

    return context


def require_not_blocked(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context()
    result = evaluate_not_blocked(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_authenticated(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context()
    result = evaluate_authenticated(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_persistent_user(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context()
    result = evaluate_persistent_user(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_demo_or_authenticated(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context()
    result = evaluate_demo_or_authenticated(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_entitlement(
    entitlement: str,
    context: Optional[AuthContext] = None,
    *,
    allow_admin: bool = True,
    allow_demo: bool = False,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_entitlement(
        context,
        entitlement,
        allow_admin=allow_admin,
        allow_demo=allow_demo,
    )
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_any_entitlement(
    entitlements: Union[str, Iterable[str]],
    context: Optional[AuthContext] = None,
    *,
    allow_admin: bool = True,
    allow_demo: bool = False,
    require_all: bool = False,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_any_entitlement(
        context,
        entitlements,
        allow_admin=allow_admin,
        allow_demo=allow_demo,
        require_all=require_all,
    )
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_role(
    roles: Union[str, Iterable[str]],
    context: Optional[AuthContext] = None,
    *,
    allow_admin: bool = True,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_role(
        context,
        roles,
        allow_admin=allow_admin,
    )
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_admin(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_admin(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_account(
    context: Optional[AuthContext] = None,
    *,
    require_account_id: bool = True,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_account(context, require_account_id=require_account_id)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_account_role(
    roles: Union[str, Iterable[str]],
    context: Optional[AuthContext] = None,
    *,
    allow_admin: bool = True,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_account_role(
        context,
        roles,
        allow_admin=allow_admin,
    )
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_account_management(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_account_management(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_member_management(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context(full=True)
    result = evaluate_member_management(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


def require_write_allowed(
    context: Optional[AuthContext] = None,
    *,
    raise_error: bool = False,
) -> AuthRequirementResult:
    context = context or load_auth_context()
    result = evaluate_write_allowed(context)
    if raise_error and not result.allowed:
        raise AuthRequirementError(result)
    return result


# ─────────────────────────────────────────────────────────────
# Response builders
# ─────────────────────────────────────────────────────────────

def build_login_redirect(
    context: Optional[AuthContext] = None,
    *,
    next_url: Optional[str] = None,
    status_code: int = 302,
) -> Any:
    context = context or load_auth_context(next_url=next_url)

    if _context_auth_is_unavailable(context):
        return build_auth_unavailable_response(context=context)

    target = _context_login_url(context) or "/auth?mode=login"
    return redirect(target, code=status_code)


def build_auth_unavailable_response(
    result: Optional[AuthRequirementResult] = None,
    *,
    context: Optional[AuthContext] = None,
    message: str = "vectoplan-auth is unavailable.",
) -> Any:
    context = context or (result.context if result else None) or load_auth_context()

    reason = (
        result.reason if result else ""
    ) or _safe_str(_context_attr(context, "reason_code", ""), "", 160) or _safe_str(
        _context_attr(context, "auth_state", ""),
        "auth_service_unavailable",
        160,
    )

    payload = {
        "ok": False,
        "code": reason or "auth_service_unavailable",
        "error": "auth_service_unavailable",
        "message": message,
        "status_code": DEFAULT_SERVICE_UNAVAILABLE_STATUS,
        "auth": _context_public_dict(context),
        "requirement": result.to_dict(include_context=False) if result else {},
    }

    if _request_wants_json(default=False):
        return _json_response(payload, DEFAULT_SERVICE_UNAVAILABLE_STATUS)

    return _html_error_response(
        title="Auth-Service nicht erreichbar",
        message=(
            "vectoplan-auth ist nicht erreichbar. "
            "Der Zugriff ist aus Sicherheitsgründen vorübergehend gesperrt."
        ),
        status_code=DEFAULT_SERVICE_UNAVAILABLE_STATUS,
        payload=payload,
        show_login=False,
    )


def build_auth_required_response(
    result: Optional[AuthRequirementResult] = None,
    *,
    context: Optional[AuthContext] = None,
    message: str = "Login required.",
) -> Any:
    context = context or (result.context if result else None) or load_auth_context()

    if _context_auth_is_unavailable(context):
        return build_auth_unavailable_response(result=result, context=context)

    login_url = _context_login_url(context)

    if not _request_wants_json(default=False) and login_url:
        return redirect(login_url, code=302)

    payload = {
        "ok": False,
        "code": "authentication_required",
        "error": "authentication_required",
        "message": message,
        "status_code": DEFAULT_LOGIN_STATUS,
        "auth": _context_public_dict(context),
        "redirect_url": login_url,
    }
    return _json_response(payload, DEFAULT_LOGIN_STATUS)


def build_blocked_response(
    result: Optional[AuthRequirementResult] = None,
    *,
    context: Optional[AuthContext] = None,
    message: str = "Access is blocked.",
) -> Any:
    context = context or (result.context if result else None) or load_auth_context()

    if _context_auth_is_unavailable(context):
        return build_auth_unavailable_response(result=result, context=context)

    reason = (
        result.reason if result else ""
    ) or _safe_str(_context_attr(context, "blocked_reason", ""), "", 160) or "blocked"

    status = result.status_code if result else _context_denial_code(context)
    if status == DEFAULT_SERVICE_UNAVAILABLE_STATUS:
        return build_auth_unavailable_response(result=result, context=context)

    if status <= 0:
        status = DEFAULT_BLOCKED_STATUS

    payload = {
        "ok": False,
        "code": reason,
        "error": reason,
        "message": message,
        "status_code": status,
        "auth": _context_public_dict(context),
        "requirement": result.to_dict(include_context=False) if result else {},
    }

    if _request_wants_json(default=False):
        return _json_response(payload, status)

    return _html_error_response(
        title="Zugriff gesperrt",
        message=message,
        status_code=status,
        payload=payload,
        show_login=False,
    )


def build_forbidden_response(
    result: Optional[AuthRequirementResult] = None,
    *,
    context: Optional[AuthContext] = None,
    message: str = "Access denied.",
) -> Any:
    context = context or (result.context if result else None) or load_auth_context()

    if _context_auth_is_unavailable(context) or (result and result.status_code == DEFAULT_SERVICE_UNAVAILABLE_STATUS):
        return build_auth_unavailable_response(result=result, context=context)

    result_payload = result.to_dict(include_context=False) if result else {}
    status = result.status_code if result else DEFAULT_FORBIDDEN_STATUS
    if status <= 0:
        status = DEFAULT_FORBIDDEN_STATUS

    payload = {
        "ok": False,
        "code": result.reason if result else "forbidden",
        "error": result.reason if result else "forbidden",
        "message": result.message if result else message,
        "status_code": status,
        "auth": _context_public_dict(context),
        "requirement": result_payload,
    }

    if _request_wants_json(default=False):
        return _json_response(payload, status)

    return _html_error_response(
        title="Zugriff verweigert",
        message=result.message if result else message,
        status_code=status,
        payload=payload,
        show_login=False,
    )


def build_requirement_response(result: AuthRequirementResult) -> Any:
    if result.allowed:
        return None

    if result.status_code == DEFAULT_SERVICE_UNAVAILABLE_STATUS or result.auth_unavailable or _is_auth_unavailable_reason(result.reason):
        return build_auth_unavailable_response(result=result)

    if result.status_code == DEFAULT_LOGIN_STATUS or result.action == AUTH_DECISION_REDIRECT:
        return build_auth_required_response(result=result)

    if result.context and (_context_user_is_blocked(result.context) or _context_access_is_blocked(result.context)):
        return build_blocked_response(result=result)

    return build_forbidden_response(result=result)


def ensure_requirement_response(result: AuthRequirementResult) -> Optional[Any]:
    if result.allowed:
        return None
    return build_requirement_response(result)


def _json_response(payload: Mapping[str, Any], status_code: int) -> Any:
    try:
        response = jsonify(dict(payload))
        response.status_code = status_code
        return response
    except Exception:
        data = dict(payload)
        data["status_code"] = status_code
        return data


def _html_error_response(
    *,
    title: str,
    message: str,
    status_code: int,
    payload: Mapping[str, Any],
    show_login: bool = True,
) -> Any:
    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f7fb;
      color: #172033;
    }
    * {
      box-sizing: border-box;
    }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background: #f4f7fb;
    }
    main {
      width: min(680px, 100%);
      border: 1px solid #d8e0ec;
      border-top: 4px solid #2563eb;
      border-radius: 10px;
      padding: clamp(22px, 4vw, 34px);
      background: #ffffff;
      box-shadow: 0 18px 48px rgba(31, 48, 77, .10);
    }
    h1 {
      margin: 0 0 12px;
      color: #111827;
      font-size: clamp(22px, 4vw, 28px);
      line-height: 1.2;
      letter-spacing: -.02em;
    }
    p {
      margin: 0 0 16px;
      color: #536176;
      line-height: 1.6;
    }
    code {
      padding: 2px 6px;
      border: 1px solid #d8e0ec;
      border-radius: 4px;
      background: #eef3f9;
      color: #26364d;
    }
    a {
      color: #155eef;
      font-weight: 650;
      text-underline-offset: 3px;
    }
    .meta {
      margin-top: 20px;
      padding-top: 14px;
      border-top: 1px solid #e2e8f0;
      font-size: 13px;
      color: #68758a;
    }
    @media (max-width: 540px) {
      body {
        place-items: start stretch;
        padding: 12px;
      }
      main {
        margin-top: 8vh;
      }
    }
  </style>
</head>
<body>
  <main>
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    {% if login_url %}
      <p><a href="{{ login_url }}">Zur Anmeldung</a></p>
    {% endif %}
    <div class="meta">Status {{ status_code }} · {{ reason }}</div>
  </main>
</body>
</html>
"""
    context = dict(payload.get("auth") or {})
    login_url = None

    if show_login:
        links = context.get("links") if isinstance(context, dict) else None
        if isinstance(links, dict):
            login_url = links.get("login_url") or links.get("login")

    try:
        rendered = render_template_string(
            html,
            title=title,
            message=message,
            status_code=status_code,
            reason=payload.get("code") or payload.get("error") or "denied",
            login_url=login_url,
        )
        return make_response(rendered, status_code)
    except Exception:
        return _json_response(payload, status_code)


# ─────────────────────────────────────────────────────────────
# Decorators
# ─────────────────────────────────────────────────────────────

def auth_required_route(
    func: Optional[F] = None,
    *,
    full: bool = False,
    me_only: bool = False,
) -> Union[F, Callable[[F], F]]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context(full=full, me_only=me_only)
            result = evaluate_authenticated(context)
            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)

    return decorator


def demo_or_auth_required_route(
    func: Optional[F] = None,
) -> Union[F, Callable[[F], F]]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context()
            result = evaluate_demo_or_authenticated(context)
            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)

    return decorator


def persistent_user_required_route(
    func: Optional[F] = None,
) -> Union[F, Callable[[F], F]]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context()
            result = evaluate_persistent_user(context)
            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)

    return decorator


def entitlement_required_route(
    entitlements: Union[str, Iterable[str]],
    *,
    allow_admin: bool = True,
    allow_demo: bool = False,
    require_all: bool = False,
) -> Callable[[F], F]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context(full=True)
            result = evaluate_any_entitlement(
                context,
                entitlements,
                allow_admin=allow_admin,
                allow_demo=allow_demo,
                require_all=require_all,
            )
            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def admin_required_route(
    func: Optional[F] = None,
) -> Union[F, Callable[[F], F]]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context(full=True)
            result = evaluate_admin(context)
            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)

    return decorator


def account_required_route(
    func: Optional[F] = None,
    *,
    roles: Optional[Union[str, Iterable[str]]] = None,
    management: bool = False,
    members: bool = False,
) -> Union[F, Callable[[F], F]]:
    def decorator(route_func: F) -> F:
        @functools.wraps(route_func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = load_auth_context(full=True)

            if management:
                result = evaluate_account_management(context)
            elif members:
                result = evaluate_member_management(context)
            elif roles:
                result = evaluate_account_role(context, roles)
            else:
                result = evaluate_account(context)

            response = ensure_requirement_response(result)
            if response is not None:
                return response
            return route_func(*args, **kwargs)

        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)

    return decorator


# ─────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────

def should_allow_demo_project(context: Optional[AuthContext] = None) -> bool:
    context = context or load_auth_context()
    if _context_auth_is_unavailable(context):
        return False
    return is_demo_context(context)


def should_allow_persistent_projects(context: Optional[AuthContext] = None) -> bool:
    context = context or load_auth_context()
    if _context_auth_is_unavailable(context):
        return False
    return can_persist_context(context)


def should_show_login(context: Optional[AuthContext] = None) -> bool:
    context = context or load_auth_context()
    return bool(
        not _context_auth_is_unavailable(context)
        and not _safe_bool(_context_attr(context, "authenticated", False), False)
        and not _context_user_is_blocked(context)
    )


def should_show_account_dashboard(context: Optional[AuthContext] = None) -> bool:
    context = context or load_auth_context()
    return bool(
        _safe_bool(_context_attr(context, "dashboard_allowed", False), False)
        and _safe_bool(_context_attr(context, "authenticated", False), False)
        and not _context_effectively_blocked(context)
    )


def should_show_admin(context: Optional[AuthContext] = None) -> bool:
    context = context or load_auth_context(full=True)
    return bool(
        _safe_bool(_context_attr(context, "authenticated", False), False)
        and not _context_effectively_blocked(context)
        and _safe_bool(_context_attr(context, "is_admin", False), False)
    )


def auth_context_for_template(
    *,
    full: bool = False,
    use_cache: bool = True,
) -> Dict[str, Any]:
    context = load_auth_context(full=full, use_cache=use_cache)
    return context.to_template_context()


def auth_context_for_app_config(
    *,
    full: bool = False,
    use_cache: bool = True,
) -> Dict[str, Any]:
    context = load_auth_context(full=full, use_cache=use_cache)
    return context.to_app_config()


def log_requirement_denied(result: AuthRequirementResult, *, logger: Optional[logging.Logger] = None) -> None:
    if result.allowed:
        return

    log = logger or LOGGER
    try:
        context_log = result.context.for_log() if result.context and hasattr(result.context, "for_log") else {}
        log.info(
            "Auth requirement denied: reason=%s status=%s action=%s details=%s auth=%s",
            result.reason,
            result.status_code,
            result.action,
            result.details,
            context_log,
        )
    except Exception:
        pass


def get_auth_requirements_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "auth_requirements",
        "phase": "auth-unavailable-separated",
        "default_user": False,
        "rules": {
            "auth_unavailable_status": DEFAULT_SERVICE_UNAVAILABLE_STATUS,
            "user_blocked_status": DEFAULT_BLOCKED_STATUS,
            "login_required_status": DEFAULT_LOGIN_STATUS,
            "forbidden_status": DEFAULT_FORBIDDEN_STATUS,
            "demo_fallback_when_auth_unavailable": False,
            "persistent_user_requires_auth": True,
        },
    }


__all__ = [
    "ACCOUNT_MEMBER_ROLES",
    "ADMIN_REQUIRED_ROLES",
    "AUTH_DECISION_ALLOW",
    "AUTH_DECISION_DENY",
    "AUTH_DECISION_REDIRECT",
    "AUTH_UNAVAILABLE_REASONS",
    "AuthRequirementError",
    "AuthRequirementResult",
    "DEFAULT_BLOCKED_STATUS",
    "DEFAULT_FORBIDDEN_STATUS",
    "DEFAULT_LOGIN_STATUS",
    "DEFAULT_SERVICE_UNAVAILABLE_STATUS",
    "MANAGE_ACCOUNT_ROLES",
    "SAFE_METHODS",
    "USER_BLOCKED_REASONS",
    "WRITE_METHODS",
    "account_required_route",
    "admin_required_route",
    "allow_result",
    "auth_context_for_app_config",
    "auth_context_for_template",
    "auth_required_route",
    "build_auth_required_response",
    "build_auth_unavailable_response",
    "build_blocked_response",
    "build_forbidden_response",
    "build_login_redirect",
    "build_requirement_response",
    "demo_or_auth_required_route",
    "deny_result",
    "ensure_requirement_response",
    "entitlement_required_route",
    "evaluate_account",
    "evaluate_account_management",
    "evaluate_account_role",
    "evaluate_admin",
    "evaluate_any_entitlement",
    "evaluate_authenticated",
    "evaluate_demo_or_authenticated",
    "evaluate_entitlement",
    "evaluate_member_management",
    "evaluate_not_blocked",
    "evaluate_persistent_user",
    "evaluate_role",
    "evaluate_write_allowed",
    "get_auth_requirements_status",
    "load_auth_context",
    "log_requirement_denied",
    "persistent_user_required_route",
    "require_account",
    "require_account_management",
    "require_account_role",
    "require_admin",
    "require_any_entitlement",
    "require_auth_context",
    "require_authenticated",
    "require_demo_or_authenticated",
    "require_entitlement",
    "require_member_management",
    "require_not_blocked",
    "require_persistent_user",
    "require_role",
    "require_write_allowed",
    "should_allow_demo_project",
    "should_allow_persistent_projects",
    "should_show_account_dashboard",
    "should_show_admin",
    "should_show_login",
]