# services/vectoplan-app/services/auth_dependency_service.py
from __future__ import annotations

"""
VECTOPLAN auth dependency service.

Zweck:
- Zentrale Bewertung, ob vectoplan-auth für vectoplan-app erreichbar ist.
- Klassifiziert Auth-Ausfälle sauber:
    dns_failed
    connection_refused
    timeout
    http_5xx
    access_denied
    invalid_payload
    service_unavailable
    not_configured
- Ersetzt keine Auth-Wahrheit.
- Erzeugt keine Benutzer.
- Erzeugt keinen Default-User.
- Erlaubt keinen Demo-Fallback bei Auth-Ausfall.
- Liefert Statusdaten für /ready, /v1/auth/status und spätere Routenlogik.

Wichtige Regel:
- vectoplan-auth ist die Wahrheit für User, Guest, Demo, Blocked/Banned,
  Account, Plan, Rollen und Entitlements.
- Diese Datei prüft nur die technische Betriebsfähigkeit dieser Dependency.
"""

import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

SERVICE_NAME = "auth_dependency_service"

DEFAULT_AUTH_INTERNAL_URL = "http://vectoplan-auth:5000"
DEFAULT_AUTH_PUBLIC_URL = "http://localhost:5000"

DEFAULT_CONTEXT_MINIMAL_PATH = "/auth/context/minimal"
DEFAULT_CONTEXT_PATH = "/auth/context"
DEFAULT_ME_PATH = "/auth/me"
DEFAULT_READY_PATH = "/health"

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_CACHE_TTL_SECONDS = 5
DEFAULT_NEGATIVE_CACHE_TTL_SECONDS = 3

AUTH_STATE_AVAILABLE = "available"
AUTH_STATE_NOT_CONFIGURED = "not_configured"
AUTH_STATE_SERVICE_UNAVAILABLE = "service_unavailable"
AUTH_STATE_DNS_FAILED = "dns_failed"
AUTH_STATE_CONNECTION_REFUSED = "connection_refused"
AUTH_STATE_TIMEOUT = "timeout"
AUTH_STATE_HTTP_5XX = "http_5xx"
AUTH_STATE_HTTP_ERROR = "http_error"
AUTH_STATE_ACCESS_DENIED = "access_denied"
AUTH_STATE_INVALID_PAYLOAD = "invalid_payload"
AUTH_STATE_REQUEST_FAILED = "request_failed"
AUTH_STATE_REQUESTS_UNAVAILABLE = "requests_unavailable"

SERVICE_UNAVAILABLE_CODES = {
    "service_unavailable",
    "auth_service_unavailable",
    "auth_unavailable",
    "auth_context_unavailable",
    "dependency_unavailable",
    "upstream_unavailable",
    "dns_failed",
    "connection_refused",
    "timeout",
}

BLOCKED_BUT_DEPENDENCY_OK_CODES = {
    "user_blocked",
    "user_banned",
    "account_blocked",
    "account_banned",
    "plan_blocked",
    "entitlement_blocked",
    "permission_denied",
    "project_permission_denied",
}


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

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable", "ok", "ready"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable", "failed", "error"}:
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
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


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _read_config(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(name)  # type: ignore[union-attr]
            if value is not None:
                return value
    except Exception:
        pass

    try:
        value = os.environ.get(name)
        if value is not None:
            return value
    except Exception:
        pass

    return default


def _read_first_config(names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        try:
            value = _read_config(name, None)
            if value is not None and _safe_str(value):
                return value
        except Exception:
            continue
    return default


def _now_monotonic() -> float:
    try:
        return time.monotonic()
    except Exception:
        return time.time()


def _utc_timestamp() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _normalize_base_url(value: Any) -> str:
    text = _safe_str(value, "", 4000).rstrip("/")
    if not text:
        return ""

    try:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return text
    except Exception:
        return ""


def _normalize_path(value: Any, default: str = "/") -> str:
    text = _safe_str(value, default, 1000)
    if not text:
        text = default
    if not text.startswith("/"):
        text = "/" + text
    while "//" in text:
        text = text.replace("//", "/")
    return text


def _join_url(base_url: str, path: str) -> str:
    base = _normalize_base_url(base_url)
    clean_path = _normalize_path(path, "/")

    if not base:
        return ""

    return base.rstrip("/") + "/" + clean_path.lstrip("/")


def _redact_url(value: Any) -> str:
    text = _safe_str(value, "", 4000)
    if not text:
        return ""

    try:
        split = urlsplit(text)
        if not split.scheme or not split.netloc:
            return text

        host = split.hostname or ""
        port = f":{split.port}" if split.port else ""
        netloc = host + port

        return urlunsplit((split.scheme, netloc, split.path, "", ""))
    except Exception:
        return text.split("?", 1)[0]


def _hostname_from_url(value: Any) -> str:
    try:
        split = urlsplit(_safe_str(value, "", 4000))
        return _safe_str(split.hostname, "", 255).lower()
    except Exception:
        return ""


def _port_from_url(value: Any) -> Optional[int]:
    try:
        split = urlsplit(_safe_str(value, "", 4000))
        return split.port
    except Exception:
        return None


def _looks_like_dns_error(message: str) -> bool:
    text = _safe_str(message, "", 4000).lower()
    return any(
        marker in text
        for marker in (
            "nameresolutionerror",
            "failed to resolve",
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname provided",
            "gaierror",
            "getaddrinfo failed",
        )
    )


def _looks_like_connection_refused(message: str) -> bool:
    text = _safe_str(message, "", 4000).lower()
    return any(
        marker in text
        for marker in (
            "connection refused",
            "failed to establish a new connection",
            "errno 111",
            "errno 61",
            "actively refused",
        )
    )


def _looks_like_timeout(message: str) -> bool:
    text = _safe_str(message, "", 4000).lower()
    return any(
        marker in text
        for marker in (
            "read timed out",
            "connect timeout",
            "connection timed out",
            "timeout",
            "timed out",
        )
    )


def _classify_exception(exc: BaseException) -> Tuple[str, str]:
    message = str(exc)

    try:
        if requests is not None:
            if isinstance(exc, requests.exceptions.Timeout):  # type: ignore[attr-defined]
                return AUTH_STATE_TIMEOUT, "vectoplan-auth request timed out."
            if isinstance(exc, requests.exceptions.ConnectionError):  # type: ignore[attr-defined]
                if _looks_like_dns_error(message):
                    return AUTH_STATE_DNS_FAILED, "vectoplan-auth DNS name could not be resolved."
                if _looks_like_connection_refused(message):
                    return AUTH_STATE_CONNECTION_REFUSED, "vectoplan-auth refused the connection."
                if _looks_like_timeout(message):
                    return AUTH_STATE_TIMEOUT, "vectoplan-auth connection timed out."
                return AUTH_STATE_REQUEST_FAILED, "vectoplan-auth connection failed."
            if isinstance(exc, requests.exceptions.RequestException):  # type: ignore[attr-defined]
                return AUTH_STATE_REQUEST_FAILED, "vectoplan-auth request failed."
    except Exception:
        pass

    if _looks_like_dns_error(message):
        return AUTH_STATE_DNS_FAILED, "vectoplan-auth DNS name could not be resolved."

    if _looks_like_connection_refused(message):
        return AUTH_STATE_CONNECTION_REFUSED, "vectoplan-auth refused the connection."

    if _looks_like_timeout(message):
        return AUTH_STATE_TIMEOUT, "vectoplan-auth request timed out."

    return AUTH_STATE_REQUEST_FAILED, "vectoplan-auth request failed."


def _dependency_status_code(auth_state: str) -> int:
    if auth_state == AUTH_STATE_AVAILABLE:
        return 200
    return 503


def _is_infra_unavailable_code(value: Any) -> bool:
    code = _safe_str(value, "", 160).lower()
    return code in SERVICE_UNAVAILABLE_CODES


# ─────────────────────────────────────────────────────────────
# TTL cache
# ─────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    value: Any
    expires_at: float

    def expired(self) -> bool:
        try:
            return _now_monotonic() >= self.expires_at
        except Exception:
            return True


class _TTLCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Tuple[bool, Any]:
        safe_key = _safe_str(key, "", 1000)
        if not safe_key:
            return False, None

        try:
            with self._lock:
                entry = self._items.get(safe_key)
                if entry is None:
                    return False, None

                if entry.expired():
                    self._items.pop(safe_key, None)
                    return False, None

                return True, entry.value
        except Exception:
            return False, None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        safe_key = _safe_str(key, "", 1000)
        ttl = max(0, _safe_int(ttl_seconds, 0))

        if not safe_key or ttl <= 0:
            return

        try:
            with self._lock:
                self._items[safe_key] = _CacheEntry(
                    value=value,
                    expires_at=_now_monotonic() + ttl,
                )
        except Exception:
            pass

    def clear(self) -> None:
        try:
            with self._lock:
                self._items.clear()
        except Exception:
            pass


_DEPENDENCY_CACHE = _TTLCache()
_SERVICE_LOCK = threading.RLock()
_SERVICE_SINGLETON: Optional["AuthDependencyService"] = None


# ─────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuthDependencyConfig:
    internal_url: str = ""
    public_url: str = ""
    context_minimal_path: str = DEFAULT_CONTEXT_MINIMAL_PATH
    context_path: str = DEFAULT_CONTEXT_PATH
    me_path: str = DEFAULT_ME_PATH
    ready_path: str = DEFAULT_READY_PATH
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    verify_ssl: bool = True
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    negative_cache_ttl_seconds: int = DEFAULT_NEGATIVE_CACHE_TTL_SECONDS
    api_token: str = ""

    @classmethod
    def from_runtime(cls) -> "AuthDependencyConfig":
        internal_url = _normalize_base_url(
            _read_first_config(
                (
                    "VECTOPLAN_AUTH_INTERNAL_URL",
                    "AUTH_SERVICE_INTERNAL_URL",
                    "AUTH_IDENTITY_INTERNAL_URL",
                    "REGISTRATION_INTERNAL_URL",
                ),
                DEFAULT_AUTH_INTERNAL_URL,
            )
        )

        public_url = _normalize_base_url(
            _read_first_config(
                (
                    "VECTOPLAN_AUTH_PUBLIC_URL",
                    "AUTH_SERVICE_PUBLIC_URL",
                    "AUTH_PUBLIC_URL",
                    "REGISTRATION_PUBLIC_URL",
                ),
                DEFAULT_AUTH_PUBLIC_URL,
            )
        )

        timeout_seconds = _safe_float(
            _read_config("VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            DEFAULT_TIMEOUT_SECONDS,
        )
        timeout_seconds = max(0.2, min(30.0, timeout_seconds))

        cache_ttl = _safe_int(
            _read_config("VECTOPLAN_AUTH_STATUS_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
            DEFAULT_CACHE_TTL_SECONDS,
        )

        negative_cache_ttl = _safe_int(
            _read_config("VECTOPLAN_AUTH_NEGATIVE_CACHE_TTL_SECONDS", DEFAULT_NEGATIVE_CACHE_TTL_SECONDS),
            DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
        )

        return cls(
            internal_url=internal_url,
            public_url=public_url,
            context_minimal_path=_normalize_path(
                _read_config("VECTOPLAN_AUTH_CONTEXT_MINIMAL_PATH", DEFAULT_CONTEXT_MINIMAL_PATH),
                DEFAULT_CONTEXT_MINIMAL_PATH,
            ),
            context_path=_normalize_path(
                _read_config("VECTOPLAN_AUTH_CONTEXT_PATH", DEFAULT_CONTEXT_PATH),
                DEFAULT_CONTEXT_PATH,
            ),
            me_path=_normalize_path(
                _read_config("VECTOPLAN_AUTH_ME_PATH", DEFAULT_ME_PATH),
                DEFAULT_ME_PATH,
            ),
            ready_path=_normalize_path(
                _read_config("VECTOPLAN_AUTH_READY_PATH", DEFAULT_READY_PATH),
                DEFAULT_READY_PATH,
            ),
            timeout_seconds=timeout_seconds,
            verify_ssl=_safe_bool(
                _read_config("VECTOPLAN_AUTH_VERIFY_SSL", True),
                True,
            ),
            cache_ttl_seconds=max(0, cache_ttl),
            negative_cache_ttl_seconds=max(0, negative_cache_ttl),
            api_token=_safe_str(
                _read_first_config(
                    (
                        "VECTOPLAN_AUTH_SERVICE_TOKEN",
                        "VECTOPLAN_AUTH_API_TOKEN",
                        "AUTH_IDENTITY_API_TOKEN",
                    ),
                    "",
                ),
                "",
                4000,
            ),
        )

    def fingerprint(self) -> str:
        return "|".join(
            (
                self.internal_url,
                self.context_minimal_path,
                self.ready_path,
                str(self.timeout_seconds),
                str(self.verify_ssl),
            )
        )

    def safe_dict(self) -> Dict[str, Any]:
        return {
            "internal_url_configured": bool(self.internal_url),
            "internal_url": _redact_url(self.internal_url),
            "public_url_configured": bool(self.public_url),
            "public_url": _redact_url(self.public_url),
            "context_minimal_path": self.context_minimal_path,
            "context_path": self.context_path,
            "me_path": self.me_path,
            "ready_path": self.ready_path,
            "timeout_seconds": self.timeout_seconds,
            "verify_ssl": bool(self.verify_ssl),
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "negative_cache_ttl_seconds": self.negative_cache_ttl_seconds,
            "api_token_configured": bool(self.api_token),
        }


@dataclass
class AuthDependencyResult:
    ok: bool
    available: bool
    code: str
    auth_state: str
    message: str
    status_code: int = 503
    http_status: Optional[int] = None
    endpoint: str = ""
    method: str = "GET"
    url: str = ""
    internal_url_configured: bool = False
    public_url_configured: bool = False
    elapsed_ms: int = 0
    checked_at: float = 0.0
    cached: bool = False
    cache_ttl_seconds: int = 0
    reason_code: str = ""
    reason: str = ""
    host: str = ""
    port: Optional[int] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    auth_summary: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_private: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(self.ok),
            "available": bool(self.available),
            "code": self.code,
            "auth_state": self.auth_state,
            "message": self.message,
            "status_code": self.status_code,
            "http_status": self.http_status,
            "endpoint": self.endpoint,
            "method": self.method,
            "internal_url_configured": bool(self.internal_url_configured),
            "public_url_configured": bool(self.public_url_configured),
            "elapsed_ms": int(self.elapsed_ms),
            "checked_at": self.checked_at,
            "cached": bool(self.cached),
            "cache_ttl_seconds": int(self.cache_ttl_seconds),
            "reason_code": self.reason_code,
            "reason": self.reason,
            "host": self.host,
            "port": self.port,
            "error": self.error,
            "error_type": self.error_type,
            "auth_summary": dict(self.auth_summary or {}),
            "details": dict(self.details or {}),
            "fail_closed": True,
            "default_user": False,
            "demo_fallback_on_unavailable": False,
        }

        if include_private:
            payload["url"] = _redact_url(self.url)

        return payload


# ─────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────

class AuthDependencyService:
    def __init__(self, config: Optional[AuthDependencyConfig] = None) -> None:
        self.config = config or AuthDependencyConfig.from_runtime()

    def status(
        self,
        *,
        force_refresh: bool = False,
        endpoint: str = "context_minimal",
        include_private: bool = False,
    ) -> Dict[str, Any]:
        result = self.check(
            force_refresh=force_refresh,
            endpoint=endpoint,
        )
        return result.to_dict(include_private=include_private)

    def check(
        self,
        *,
        force_refresh: bool = False,
        endpoint: str = "context_minimal",
    ) -> AuthDependencyResult:
        endpoint_key = _safe_str(endpoint, "context_minimal", 80).lower()
        path = self._path_for_endpoint(endpoint_key)
        cache_key = "auth_dependency:" + self.config.fingerprint() + ":" + endpoint_key

        if not force_refresh:
            hit, cached_value = _DEPENDENCY_CACHE.get(cache_key)
            if hit and isinstance(cached_value, AuthDependencyResult):
                cached_copy = self._copy_result(cached_value)
                cached_copy.cached = True
                return cached_copy

        result = self._check_uncached(endpoint_key=endpoint_key, path=path)
        ttl = self.config.cache_ttl_seconds if result.ok else self.config.negative_cache_ttl_seconds
        result.cache_ttl_seconds = ttl

        if ttl > 0:
            _DEPENDENCY_CACHE.set(cache_key, self._copy_result(result), ttl)

        return result

    def is_available(self, *, force_refresh: bool = False) -> bool:
        try:
            return bool(self.check(force_refresh=force_refresh).ok)
        except Exception:
            return False

    def _copy_result(self, result: AuthDependencyResult) -> AuthDependencyResult:
        try:
            return AuthDependencyResult(
                ok=result.ok,
                available=result.available,
                code=result.code,
                auth_state=result.auth_state,
                message=result.message,
                status_code=result.status_code,
                http_status=result.http_status,
                endpoint=result.endpoint,
                method=result.method,
                url=result.url,
                internal_url_configured=result.internal_url_configured,
                public_url_configured=result.public_url_configured,
                elapsed_ms=result.elapsed_ms,
                checked_at=result.checked_at,
                cached=result.cached,
                cache_ttl_seconds=result.cache_ttl_seconds,
                reason_code=result.reason_code,
                reason=result.reason,
                host=result.host,
                port=result.port,
                error=result.error,
                error_type=result.error_type,
                auth_summary=dict(result.auth_summary or {}),
                details=dict(result.details or {}),
            )
        except Exception:
            return result

    def _path_for_endpoint(self, endpoint_key: str) -> str:
        if endpoint_key in {"context", "auth_context"}:
            return self.config.context_path
        if endpoint_key in {"me", "auth_me"}:
            return self.config.me_path
        if endpoint_key in {"ready", "health", "status"}:
            return self.config.ready_path
        return self.config.context_minimal_path

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Vectoplan-Service": "vectoplan-app",
            "X-Vectoplan-Dependency-Check": "1",
        }

        token = _safe_str(self.config.api_token, "", 4000)
        if token:
            headers["Authorization"] = "Bearer " + token

        return headers

    def _base_result(
        self,
        *,
        code: str,
        auth_state: str,
        message: str,
        endpoint: str,
        url: str,
        ok: bool = False,
        available: bool = False,
        status_code: Optional[int] = None,
    ) -> AuthDependencyResult:
        return AuthDependencyResult(
            ok=bool(ok),
            available=bool(available),
            code=code,
            auth_state=auth_state,
            message=message,
            status_code=status_code if status_code is not None else _dependency_status_code(auth_state),
            endpoint=endpoint,
            url=_redact_url(url),
            internal_url_configured=bool(self.config.internal_url),
            public_url_configured=bool(self.config.public_url),
            checked_at=_utc_timestamp(),
            reason_code=code,
            reason=auth_state,
            host=_hostname_from_url(url or self.config.internal_url),
            port=_port_from_url(url or self.config.internal_url),
            details=self.config.safe_dict(),
        )

    def _check_uncached(self, *, endpoint_key: str, path: str) -> AuthDependencyResult:
        url = _join_url(self.config.internal_url, path)
        started = _now_monotonic()

        if not self.config.internal_url:
            return self._base_result(
                code="auth_not_configured",
                auth_state=AUTH_STATE_NOT_CONFIGURED,
                message="VECTOPLAN_AUTH_INTERNAL_URL is not configured.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )

        if not url:
            return self._base_result(
                code="auth_dependency_url_invalid",
                auth_state=AUTH_STATE_NOT_CONFIGURED,
                message="vectoplan-auth dependency URL is invalid.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )

        if requests is None:
            return self._base_result(
                code="requests_unavailable",
                auth_state=AUTH_STATE_REQUESTS_UNAVAILABLE,
                message="Python requests is unavailable.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )

        try:
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
            )
        except Exception as exc:
            auth_state, message = _classify_exception(exc)
            elapsed_ms = int((_now_monotonic() - started) * 1000)
            result = self._base_result(
                code=auth_state,
                auth_state=auth_state,
                message=message,
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )
            result.elapsed_ms = elapsed_ms
            result.error = str(exc)
            result.error_type = exc.__class__.__name__
            return result

        elapsed_ms = int((_now_monotonic() - started) * 1000)
        http_status = _safe_int(getattr(response, "status_code", None), 0)

        payload: Dict[str, Any] = {}
        text_payload = ""

        try:
            payload = _safe_dict(response.json())
        except Exception:
            try:
                text_payload = _safe_str(getattr(response, "text", ""), "", 1000)
            except Exception:
                text_payload = ""

        if http_status >= 500:
            result = self._base_result(
                code="auth_http_5xx",
                auth_state=AUTH_STATE_HTTP_5XX,
                message="vectoplan-auth returned a server error.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )
            result.http_status = http_status
            result.elapsed_ms = elapsed_ms
            result.error = text_payload or _safe_json(payload)
            return result

        if http_status in {401, 403}:
            result = self._base_result(
                code="auth_dependency_access_denied",
                auth_state=AUTH_STATE_ACCESS_DENIED,
                message="vectoplan-auth rejected the server-to-server dependency check.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=True,
                status_code=503,
            )
            result.http_status = http_status
            result.elapsed_ms = elapsed_ms
            result.error = text_payload or _safe_json(payload)
            result.auth_summary = self._auth_summary(payload)
            return result

        if http_status >= 400:
            result = self._base_result(
                code="auth_http_error",
                auth_state=AUTH_STATE_HTTP_ERROR,
                message="vectoplan-auth returned an HTTP error.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=True,
                status_code=503,
            )
            result.http_status = http_status
            result.elapsed_ms = elapsed_ms
            result.error = text_payload or _safe_json(payload)
            result.auth_summary = self._auth_summary(payload)
            return result

        if not payload:
            result = self._base_result(
                code="auth_invalid_payload",
                auth_state=AUTH_STATE_INVALID_PAYLOAD,
                message="vectoplan-auth returned no valid JSON payload.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=True,
                status_code=503,
            )
            result.http_status = http_status
            result.elapsed_ms = elapsed_ms
            result.error = text_payload or "empty_json_payload"
            return result

        payload_code = self._payload_code(payload)
        payload_auth_state = self._payload_auth_state(payload)

        if _is_infra_unavailable_code(payload_code) or _is_infra_unavailable_code(payload_auth_state):
            result = self._base_result(
                code=payload_code or payload_auth_state or "auth_service_unavailable",
                auth_state=AUTH_STATE_SERVICE_UNAVAILABLE,
                message="vectoplan-auth reported service_unavailable.",
                endpoint=endpoint_key,
                url=url,
                ok=False,
                available=False,
                status_code=503,
            )
            result.http_status = http_status
            result.elapsed_ms = elapsed_ms
            result.auth_summary = self._auth_summary(payload)
            return result

        result = self._base_result(
            code="auth_dependency_available",
            auth_state=AUTH_STATE_AVAILABLE,
            message="vectoplan-auth is reachable.",
            endpoint=endpoint_key,
            url=url,
            ok=True,
            available=True,
            status_code=200,
        )
        result.http_status = http_status
        result.elapsed_ms = elapsed_ms
        result.auth_summary = self._auth_summary(payload)
        return result

    def _payload_code(self, payload: Mapping[str, Any]) -> str:
        data = _safe_dict(payload)
        raw = _safe_dict(data.get("raw"))

        return _safe_str(
            data.get("code")
            or data.get("reason_code")
            or data.get("reason")
            or data.get("auth_state")
            or data.get("blocked_reason")
            or raw.get("code")
            or raw.get("reason_code")
            or raw.get("reason")
            or raw.get("auth_state")
            or raw.get("blocked_reason"),
            "",
            160,
        ).lower()

    def _payload_auth_state(self, payload: Mapping[str, Any]) -> str:
        data = _safe_dict(payload)
        raw = _safe_dict(data.get("raw"))

        return _safe_str(
            data.get("auth_state")
            or data.get("authState")
            or data.get("state")
            or raw.get("auth_state")
            or raw.get("authState")
            or raw.get("state"),
            "",
            160,
        ).lower()

    def _auth_summary(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        data = _safe_dict(payload)
        raw = _safe_dict(data.get("raw"))

        user = _safe_dict(data.get("user") or raw.get("user"))
        access = _safe_dict(data.get("access") or raw.get("access"))
        account = _safe_dict(data.get("account") or raw.get("account"))

        blocked_reason = (
            data.get("blocked_reason")
            or data.get("blockedReason")
            or raw.get("blocked_reason")
            or raw.get("blockedReason")
            or access.get("blocked_reason")
            or access.get("blockedReason")
        )

        auth_state = (
            data.get("auth_state")
            or data.get("authState")
            or raw.get("auth_state")
            or raw.get("authState")
        )

        return {
            "payload_ok": _safe_bool(data.get("ok"), default=True),
            "auth_state": _safe_str(auth_state, "", 160) or None,
            "authenticated": _safe_bool(
                data.get("authenticated")
                or data.get("is_authenticated")
                or raw.get("authenticated")
                or raw.get("is_authenticated"),
                False,
            ),
            "blocked": _safe_bool(
                data.get("blocked")
                or data.get("banned")
                or raw.get("blocked")
                or raw.get("banned")
                or access.get("blocked"),
                False,
            ),
            "blocked_reason": _safe_str(blocked_reason, "", 160) or None,
            "can_demo": _safe_bool(
                data.get("can_demo")
                or data.get("canDemo")
                or raw.get("can_demo")
                or raw.get("canDemo")
                or access.get("demo_project_access"),
                False,
            ),
            "session_valid": _safe_bool(data.get("session_valid") or raw.get("session_valid"), False),
            "user_present": bool(user),
            "account_available": _safe_bool(account.get("available"), False),
            "status_code": _safe_int(data.get("status_code") or raw.get("status_code"), 0) or None,
        }


# ─────────────────────────────────────────────────────────────
# Module-level API
# ─────────────────────────────────────────────────────────────

def get_auth_dependency_service(refresh: bool = False) -> AuthDependencyService:
    global _SERVICE_SINGLETON

    try:
        with _SERVICE_LOCK:
            if refresh or _SERVICE_SINGLETON is None:
                _SERVICE_SINGLETON = AuthDependencyService()
            return _SERVICE_SINGLETON
    except Exception:
        return AuthDependencyService()


def get_auth_dependency_status(
    *,
    force_refresh: bool = False,
    endpoint: str = "context_minimal",
    include_private: bool = False,
) -> Dict[str, Any]:
    try:
        return get_auth_dependency_service().status(
            force_refresh=force_refresh,
            endpoint=endpoint,
            include_private=include_private,
        )
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "code": "auth_dependency_status_failed",
            "auth_state": AUTH_STATE_REQUEST_FAILED,
            "message": "Auth dependency status check failed.",
            "status_code": 503,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "fail_closed": True,
            "default_user": False,
            "demo_fallback_on_unavailable": False,
        }


def check_auth_dependency(
    *,
    force_refresh: bool = False,
    endpoint: str = "context_minimal",
) -> AuthDependencyResult:
    try:
        return get_auth_dependency_service().check(
            force_refresh=force_refresh,
            endpoint=endpoint,
        )
    except Exception as exc:
        return AuthDependencyResult(
            ok=False,
            available=False,
            code="auth_dependency_check_failed",
            auth_state=AUTH_STATE_REQUEST_FAILED,
            message="Auth dependency check failed.",
            status_code=503,
            checked_at=_utc_timestamp(),
            error=str(exc),
            error_type=exc.__class__.__name__,
            reason_code="auth_dependency_check_failed",
            reason=AUTH_STATE_REQUEST_FAILED,
        )


def is_auth_service_available(force_refresh: bool = False) -> bool:
    try:
        return bool(get_auth_dependency_service().is_available(force_refresh=force_refresh))
    except Exception:
        return False


def clear_auth_dependency_cache() -> None:
    try:
        _DEPENDENCY_CACHE.clear()
    except Exception:
        pass


def classify_auth_dependency_error(exc: BaseException) -> Dict[str, Any]:
    try:
        auth_state, message = _classify_exception(exc)
        return {
            "ok": False,
            "code": auth_state,
            "auth_state": auth_state,
            "message": message,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "status_code": 503,
        }
    except Exception:
        return {
            "ok": False,
            "code": AUTH_STATE_REQUEST_FAILED,
            "auth_state": AUTH_STATE_REQUEST_FAILED,
            "message": "Auth dependency error classification failed.",
            "status_code": 503,
        }


__all__ = [
    "AUTH_STATE_ACCESS_DENIED",
    "AUTH_STATE_AVAILABLE",
    "AUTH_STATE_CONNECTION_REFUSED",
    "AUTH_STATE_DNS_FAILED",
    "AUTH_STATE_HTTP_5XX",
    "AUTH_STATE_HTTP_ERROR",
    "AUTH_STATE_INVALID_PAYLOAD",
    "AUTH_STATE_NOT_CONFIGURED",
    "AUTH_STATE_REQUEST_FAILED",
    "AUTH_STATE_REQUESTS_UNAVAILABLE",
    "AUTH_STATE_SERVICE_UNAVAILABLE",
    "AUTH_STATE_TIMEOUT",
    "AuthDependencyConfig",
    "AuthDependencyResult",
    "AuthDependencyService",
    "check_auth_dependency",
    "classify_auth_dependency_error",
    "clear_auth_dependency_cache",
    "get_auth_dependency_service",
    "get_auth_dependency_status",
    "is_auth_service_available",
]