# services/vectoplan-app/services/auth_context_client.py
from __future__ import annotations

"""
Robuster HTTP-Client für die Integration von vectoplan-app mit vectoplan-auth.

Zweck:
- vectoplan-app fragt den kanonischen Auth-State bei vectoplan-auth ab.
- Andere App-Schichten sollen nicht direkt Requests/Cookies/Auth-Payloads interpretieren.
- Dieser Client leitet Browser-Cookies serverseitig an vectoplan-auth weiter.
- Standardmäßig wird Auth-State nur request-lokal gecacht, nicht global dauerhaft.
- Auth-Ausfälle werden klassifiziert und als service_unavailable/fail-closed gemeldet.
- Auth-Ausfall ist nicht dasselbe wie ein echter gebannter User.

Wichtige Regeln:
- authenticated=true kommt ausschließlich aus vectoplan-auth.
- Guest/ClientIdentity ist kein User.
- ok=true bedeutet nur: Auth-Service hat geantwortet, nicht automatisch Zugriff.
- user_blocked=true kommt nur aus vectoplan-auth.
- auth_available=false / service_unavailable sperrt App-Zugriff fail-closed, ist aber kein User-Ban.
- Cookies, Authorization Header und API-Keys werden nie geloggt.
- Kein Default-User.
- Kein Dev-Identity-Fallback.
"""

import hashlib
import json
import logging
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, MutableMapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request as UrlLibRequest, urlopen

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

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


LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

DEFAULT_AUTH_INTERNAL_URL = "http://vectoplan-auth:5000"
DEFAULT_AUTH_PUBLIC_URL = "http://localhost:5000"
DEFAULT_SERVICE_NAME = "vectoplan-app"

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_VERIFY_TLS = True
DEFAULT_CONTEXT_CACHE_SECONDS = 0.0
DEFAULT_NEGATIVE_CACHE_SECONDS = 3.0
MAX_PROCESS_CACHE_SECONDS = 60.0

DEFAULT_CONTEXT_MINIMAL_PATH = "/auth/context/minimal"
DEFAULT_CONTEXT_PATH = "/auth/context"
DEFAULT_ME_PATH = "/auth/me"
DEFAULT_API_KEY_VERIFY_PATH = "/auth/api-keys/verify"
DEFAULT_READY_PATH = "/health"
DEFAULT_LIVE_PATH = "/health/live"

REQUEST_CACHE_ATTR = "_vectoplan_auth_context_client_cache"

AUTH_STATE_AVAILABLE = "available"
AUTH_STATE_UNAVAILABLE = "service_unavailable"
AUTH_STATE_DNS_FAILED = "dns_failed"
AUTH_STATE_CONNECTION_REFUSED = "connection_refused"
AUTH_STATE_TIMEOUT = "timeout"
AUTH_STATE_HTTP_5XX = "http_5xx"
AUTH_STATE_HTTP_ERROR = "http_error"
AUTH_STATE_ACCESS_DENIED = "access_denied"
AUTH_STATE_INVALID_PAYLOAD = "invalid_payload"
AUTH_STATE_NOT_CONFIGURED = "not_configured"
AUTH_STATE_REQUEST_FAILED = "request_failed"
AUTH_STATE_REQUESTS_UNAVAILABLE = "requests_unavailable"

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "x-api-key",
    "x-vectoplan-api-key",
    "proxy-authorization",
}

FORWARDED_HEADER_ALLOWLIST = {
    "cookie",
    "user-agent",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-real-ip",
    "x-request-id",
    "x-correlation-id",
    "accept-language",
}

CACHEABLE_GET_ENDPOINTS = {
    DEFAULT_ME_PATH,
    DEFAULT_CONTEXT_PATH,
    DEFAULT_CONTEXT_MINIMAL_PATH,
}

_PROCESS_CACHE: Dict[str, "_CacheEntry"] = {}
_CLIENT_LOCK = threading.RLock()
_CLIENT_SINGLETON: Optional["VectoplanAuthClient"] = None


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()

        if text in {"1", "true", "yes", "y", "on", "enabled", "enable", "ok", "ready"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "disable", "failed", "error"}:
            return False

        return default
    except Exception:
        return default


def _safe_float(value: Any, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    try:
        number = float(value)
    except Exception:
        number = default

    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum

    return number


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


def _read_config(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return current_app.config.get(name)
    except Exception:
        pass

    try:
        return os.getenv(name, default)
    except Exception:
        return default


def _read_first_config(names: Tuple[str, ...], default: Any = None) -> Any:
    for name in names:
        try:
            value = _read_config(name, None)
            if value is not None and _safe_str(value):
                return value
        except Exception:
            continue
    return default


def _normalize_base_url(url: Any, default: str = "") -> str:
    value = _safe_str(url, default=default).rstrip("/")

    if not value:
        value = default.rstrip("/")

    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return default.rstrip("/")
    except Exception:
        return default.rstrip("/")

    return value


def _normalize_path(path: Any, default: str = "/") -> str:
    value = _safe_str(path, default, 1000)

    if not value:
        value = default

    if not value.startswith("/"):
        value = "/" + value

    while "//" in value:
        value = value.replace("//", "/")

    return value


def _join_url(base_url: str, path: str) -> str:
    clean_base = _normalize_base_url(base_url, "").rstrip() + "/"
    clean_path = _normalize_path(path, "/").lstrip("/")
    if not clean_base.strip("/"):
        return ""
    return urljoin(clean_base, clean_path)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _json_dumps_safe(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return "{}"


def _mask_header_name(name: str) -> str:
    return name.lower().strip()


def _redact_headers(headers: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    redacted: Dict[str, str] = {}
    if not headers:
        return redacted

    for key, value in headers.items():
        normalized = _mask_header_name(key)
        if normalized in SENSITIVE_HEADER_NAMES:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = _safe_str(value)
    return redacted


def _redact_url(value: Any) -> str:
    text = _safe_str(value, "", 4000)
    if not text:
        return ""

    try:
        parsed = urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            return text

        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = host + port

        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except Exception:
        return text.split("?", 1)[0]


def _extract_headers_mapping(incoming_headers: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    if incoming_headers is not None:
        return incoming_headers

    try:
        if has_request_context() and request is not None:
            return request.headers
    except Exception:
        pass

    return {}


def _get_request_path_for_next(default: str = "/") -> str:
    try:
        if has_request_context() and request is not None:
            full_path = request.full_path or request.path or default
            if full_path.endswith("?"):
                full_path = full_path[:-1]
            return full_path or default
    except Exception:
        pass
    return default


def _sanitize_next_url(next_url: Optional[str], default: str = "/") -> str:
    candidate = _safe_str(next_url, default=default)
    if not candidate:
        return default

    candidate = candidate.replace("\r", "").replace("\n", "").strip()

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default

    if not parsed.path.startswith("/"):
        candidate = "/" + candidate
        parsed = urlparse(candidate)

    return urlunparse(("", "", parsed.path or "/", "", parsed.query, parsed.fragment))


def _looks_like_dns_error(message: Any) -> bool:
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


def _looks_like_connection_refused(message: Any) -> bool:
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


def _looks_like_timeout(message: Any) -> bool:
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


def _classify_client_exception(exc: BaseException) -> Tuple[str, str]:
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

    if isinstance(exc, TimeoutError):
        return AUTH_STATE_TIMEOUT, "vectoplan-auth request timed out."

    if _looks_like_dns_error(message):
        return AUTH_STATE_DNS_FAILED, "vectoplan-auth DNS name could not be resolved."

    if _looks_like_connection_refused(message):
        return AUTH_STATE_CONNECTION_REFUSED, "vectoplan-auth refused the connection."

    if _looks_like_timeout(message):
        return AUTH_STATE_TIMEOUT, "vectoplan-auth request timed out."

    return AUTH_STATE_REQUEST_FAILED, "vectoplan-auth request failed."


# ─────────────────────────────────────────────────────────────
# Result/cache dataclasses
# ─────────────────────────────────────────────────────────────

@dataclass
class AuthHttpResult:
    ok: bool
    status_code: int
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    reason: Optional[str] = None
    endpoint: Optional[str] = None
    elapsed_ms: Optional[int] = None
    from_cache: bool = False
    auth_state: Optional[str] = None
    code: Optional[str] = None

    def as_payload(self) -> Dict[str, Any]:
        payload = dict(self.payload or {})

        payload.setdefault("ok", bool(self.ok))
        payload.setdefault("status_code", self.status_code)

        if self.error:
            payload.setdefault("error", self.error)
        if self.reason:
            payload.setdefault("reason", self.reason)
        if self.endpoint:
            payload.setdefault("endpoint", self.endpoint)
        if self.elapsed_ms is not None:
            payload.setdefault("elapsed_ms", self.elapsed_ms)
        if self.from_cache:
            payload.setdefault("from_cache", True)
        if self.auth_state:
            payload.setdefault("auth_state", self.auth_state)
        if self.code:
            payload.setdefault("code", self.code)

        return payload


@dataclass
class _CacheEntry:
    expires_at: float
    payload: Dict[str, Any]
    status_code: int
    ok: bool = False


class VectoplanAuthClientError(RuntimeError):
    """Basisklasse für lokale Client-Fehler."""


# ─────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────

class VectoplanAuthClient:
    """
    Server-seitiger HTTP-Client für vectoplan-auth.

    Standard:
    - request-lokaler Cache aktiv
    - process-globaler TTL-Cache nur optional
    - negativer Kurzcache für Auth-Ausfälle
    - kurzer Timeout
    - keine Secrets in Logs
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        public_url: Optional[str] = None,
        service_name: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        verify_tls: Optional[bool] = None,
        context_cache_seconds: Optional[float] = None,
        negative_cache_seconds: Optional[float] = None,
        fail_open_for_public_routes: Optional[bool] = None,
    ) -> None:
        internal_url = _read_first_config(
            (
                "VECTOPLAN_AUTH_INTERNAL_URL",
                "VECTOPLAN_AUTH_BASE_URL",
                "AUTH_SERVICE_INTERNAL_URL",
                "AUTH_IDENTITY_INTERNAL_URL",
                "REGISTRATION_INTERNAL_URL",
            ),
            DEFAULT_AUTH_INTERNAL_URL,
        )

        self.base_url = _normalize_base_url(
            base_url if base_url is not None else internal_url,
            DEFAULT_AUTH_INTERNAL_URL,
        )

        self.public_url = _normalize_base_url(
            public_url if public_url is not None else _read_first_config(
                (
                    "VECTOPLAN_AUTH_PUBLIC_URL",
                    "AUTH_SERVICE_PUBLIC_URL",
                    "AUTH_PUBLIC_URL",
                    "REGISTRATION_PUBLIC_URL",
                ),
                DEFAULT_AUTH_PUBLIC_URL,
            ),
            DEFAULT_AUTH_PUBLIC_URL,
        )

        self.service_name = _safe_str(
            service_name if service_name is not None else _read_config("VECTOPLAN_AUTH_SERVICE_NAME", DEFAULT_SERVICE_NAME),
            DEFAULT_SERVICE_NAME,
        )

        self.timeout_seconds = _safe_float(
            timeout_seconds if timeout_seconds is not None else _read_config(
                "VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
            ),
            DEFAULT_TIMEOUT_SECONDS,
            minimum=0.2,
            maximum=30.0,
        )

        self.verify_tls = _safe_bool(
            verify_tls if verify_tls is not None else _read_config("VECTOPLAN_AUTH_VERIFY_TLS", DEFAULT_VERIFY_TLS),
            DEFAULT_VERIFY_TLS,
        )

        self.context_cache_seconds = _safe_float(
            context_cache_seconds if context_cache_seconds is not None else _read_config(
                "VECTOPLAN_AUTH_CONTEXT_CACHE_SECONDS",
                DEFAULT_CONTEXT_CACHE_SECONDS,
            ),
            DEFAULT_CONTEXT_CACHE_SECONDS,
            minimum=0.0,
            maximum=MAX_PROCESS_CACHE_SECONDS,
        )

        self.negative_cache_seconds = _safe_float(
            negative_cache_seconds if negative_cache_seconds is not None else _read_config(
                "VECTOPLAN_AUTH_NEGATIVE_CACHE_SECONDS",
                DEFAULT_NEGATIVE_CACHE_SECONDS,
            ),
            DEFAULT_NEGATIVE_CACHE_SECONDS,
            minimum=0.0,
            maximum=MAX_PROCESS_CACHE_SECONDS,
        )

        self.fail_open_for_public_routes = _safe_bool(
            fail_open_for_public_routes if fail_open_for_public_routes is not None else _read_config(
                "VECTOPLAN_AUTH_FAIL_OPEN_FOR_PUBLIC_ROUTES",
                False,
            ),
            False,
        )

        self.context_minimal_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_CONTEXT_MINIMAL_PATH", DEFAULT_CONTEXT_MINIMAL_PATH),
            DEFAULT_CONTEXT_MINIMAL_PATH,
        )
        self.context_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_CONTEXT_PATH", DEFAULT_CONTEXT_PATH),
            DEFAULT_CONTEXT_PATH,
        )
        self.me_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_ME_PATH", DEFAULT_ME_PATH),
            DEFAULT_ME_PATH,
        )
        self.api_key_verify_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_API_KEY_VERIFY_PATH", DEFAULT_API_KEY_VERIFY_PATH),
            DEFAULT_API_KEY_VERIFY_PATH,
        )
        self.ready_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_READY_PATH", DEFAULT_READY_PATH),
            DEFAULT_READY_PATH,
        )
        self.live_path = _normalize_path(
            _read_config("VECTOPLAN_AUTH_LIVE_PATH", DEFAULT_LIVE_PATH),
            DEFAULT_LIVE_PATH,
        )

    @classmethod
    def from_config(cls) -> "VectoplanAuthClient":
        return cls()

    def status(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "auth_context_client",
            "auth_service": "vectoplan-auth",
            "base_url_configured": bool(self.base_url),
            "base_url": _redact_url(self.base_url),
            "public_url_configured": bool(self.public_url),
            "public_url": _redact_url(self.public_url),
            "service_name": self.service_name,
            "timeout_seconds": self.timeout_seconds,
            "verify_tls": self.verify_tls,
            "context_cache_seconds": self.context_cache_seconds,
            "negative_cache_seconds": self.negative_cache_seconds,
            "fail_open_for_public_routes": False,
            "default_user": False,
            "dev_identity_fallback": False,
            "paths": {
                "context_minimal": self.context_minimal_path,
                "context": self.context_path,
                "me": self.me_path,
                "api_key_verify": self.api_key_verify_path,
                "ready": self.ready_path,
                "live": self.live_path,
            },
        }

    def ready(self) -> Dict[str, Any]:
        return self.get(self.ready_path, incoming_headers=None, use_cache=False).as_payload()

    def live(self) -> Dict[str, Any]:
        return self.get(self.live_path, incoming_headers=None, use_cache=False).as_payload()

    def me(
        self,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        result = self.get(self.me_path, incoming_headers=incoming_headers, use_cache=use_cache)
        return result.as_payload()

    def context(
        self,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        minimal: bool = True,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        path = self.context_minimal_path if minimal else self.context_path
        result = self.get(path, incoming_headers=incoming_headers, use_cache=use_cache)
        return result.as_payload()

    def context_minimal(
        self,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        return self.context(incoming_headers=incoming_headers, minimal=True, use_cache=use_cache)

    def context_full(
        self,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        return self.context(incoming_headers=incoming_headers, minimal=False, use_cache=use_cache)

    def verify_api_key(
        self,
        authorization: Optional[str] = None,
        raw_key: Optional[str] = None,
        required_scope: Optional[str] = None,
        required_permission: Optional[str] = None,
        incoming_headers: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        headers_extra: Dict[str, str] = {}

        if authorization:
            headers_extra["Authorization"] = authorization
        elif raw_key:
            headers_extra["Authorization"] = f"Bearer {raw_key}"

        body: Dict[str, Any] = {}
        if required_scope:
            body["required_scope"] = required_scope
        if required_permission:
            body["required_permission"] = required_permission

        result = self.post(
            self.api_key_verify_path,
            json_body=body,
            incoming_headers=incoming_headers,
            headers_extra=headers_extra,
            use_cache=False,
        )
        return result.as_payload()

    def login_url(
        self,
        next_url: Optional[str] = None,
        mode: str = "login",
        overlay: bool = False,
        parent_origin: Optional[str] = None,
    ) -> str:
        safe_next = _sanitize_next_url(next_url or _get_request_path_for_next("/"), default="/")
        params: Dict[str, str] = {
            "mode": _safe_str(mode, default="login") or "login",
            "next": safe_next,
        }

        if overlay:
            params["ui"] = "overlay"

        if parent_origin:
            parsed_parent = urlparse(parent_origin)
            if parsed_parent.scheme in {"http", "https"} and parsed_parent.netloc:
                params["parent_origin"] = parent_origin

        return self.public_auth_url("/auth", params=params)

    def register_url(
        self,
        next_url: Optional[str] = None,
        overlay: bool = False,
        parent_origin: Optional[str] = None,
    ) -> str:
        return self.login_url(next_url=next_url, mode="register", overlay=overlay, parent_origin=parent_origin)

    def logout_url(self) -> str:
        return self.public_auth_url("/auth/logout")

    def account_dashboard_url(self) -> str:
        return self.public_auth_url("/auth/account/dashboard")

    def admin_dashboard_url(self) -> str:
        return self.public_auth_url("/auth/admin/dashboard")

    def public_auth_url(self, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
        url = _join_url(self.public_url, path)
        if params:
            query = urlencode({key: value for key, value in params.items() if value is not None})
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"
        return url

    def get(
        self,
        path: str,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> AuthHttpResult:
        return self._request(
            method="GET",
            path=path,
            incoming_headers=incoming_headers,
            json_body=None,
            headers_extra=kwargs.get("headers_extra"),
            use_cache=use_cache,
        )

    def post(
        self,
        path: str,
        json_body: Optional[Mapping[str, Any]] = None,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        headers_extra: Optional[Mapping[str, str]] = None,
        use_cache: bool = False,
        **kwargs: Any,
    ) -> AuthHttpResult:
        if json_body is None:
            for key in ("json", "body", "payload", "data"):
                candidate = kwargs.get(key)
                if isinstance(candidate, Mapping):
                    json_body = candidate
                    break

        return self._request(
            method="POST",
            path=path,
            incoming_headers=incoming_headers,
            json_body=json_body,
            headers_extra=headers_extra,
            use_cache=use_cache,
        )

    def _request(
        self,
        method: str,
        path: str,
        incoming_headers: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        headers_extra: Optional[Mapping[str, str]] = None,
        use_cache: bool = True,
    ) -> AuthHttpResult:
        method = method.upper().strip()
        endpoint = _normalize_path(path, "/")
        url = _join_url(self.base_url, endpoint)
        incoming = _extract_headers_mapping(incoming_headers)
        headers = self._build_headers(incoming, headers_extra=headers_extra)

        if not self.base_url or not url:
            return AuthHttpResult(
                ok=False,
                status_code=503,
                payload=self.unavailable_payload(
                    endpoint=endpoint,
                    code=AUTH_STATE_NOT_CONFIGURED,
                    auth_state=AUTH_STATE_NOT_CONFIGURED,
                    message="vectoplan-auth internal URL is not configured.",
                    error="VECTOPLAN_AUTH_INTERNAL_URL missing or invalid.",
                ),
                error="auth_internal_url_missing",
                reason=AUTH_STATE_NOT_CONFIGURED,
                endpoint=endpoint,
                auth_state=AUTH_STATE_NOT_CONFIGURED,
                code=AUTH_STATE_NOT_CONFIGURED,
            )

        cache_key = self._cache_key(method, endpoint, incoming, json_body)
        cache_allowed = use_cache and method == "GET" and endpoint in CACHEABLE_GET_ENDPOINTS

        if cache_allowed:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return AuthHttpResult(
                    ok=bool(cached.ok),
                    status_code=cached.status_code,
                    payload=dict(cached.payload),
                    endpoint=endpoint,
                    from_cache=True,
                    auth_state=cached.payload.get("auth_state"),
                    code=cached.payload.get("code"),
                )

        started_at = _now()

        try:
            if requests is not None:
                result = self._request_with_requests(method, url, headers, json_body)
            else:
                result = self._request_with_urllib(method, url, headers, json_body)

            result.endpoint = endpoint
            result.elapsed_ms = int((_now() - started_at) * 1000)

            if not result.ok:
                result = self._normalize_failed_http_result(result, endpoint=endpoint, elapsed_ms=result.elapsed_ms)

            if cache_allowed and result.payload:
                self._cache_set(cache_key, result.payload, result.status_code, ok=result.ok)

            return result

        except Exception as exc:
            elapsed_ms = int((_now() - started_at) * 1000)
            auth_state, message = _classify_client_exception(exc)

            self._log_request_error(
                method=method,
                endpoint=endpoint,
                error=exc,
                elapsed_ms=elapsed_ms,
                auth_state=auth_state,
            )

            payload = self.unavailable_payload(
                endpoint=endpoint,
                code=auth_state,
                auth_state=auth_state,
                message=message,
                error=str(exc),
                elapsed_ms=elapsed_ms,
            )

            result = AuthHttpResult(
                ok=False,
                status_code=503,
                payload=payload,
                error=str(exc),
                reason=auth_state,
                endpoint=endpoint,
                elapsed_ms=elapsed_ms,
                auth_state=auth_state,
                code=auth_state,
            )

            if cache_allowed and self.negative_cache_seconds > 0:
                self._cache_set(cache_key, result.payload, result.status_code, ok=False)

            return result

    def _request_with_requests(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Optional[Mapping[str, Any]],
    ) -> AuthHttpResult:
        assert requests is not None

        response = requests.request(
            method=method,
            url=url,
            headers=dict(headers),
            json=dict(json_body) if json_body is not None else None,
            timeout=self.timeout_seconds,
            verify=self.verify_tls,
        )

        payload = self._parse_response_payload(response.status_code, response.text)
        return AuthHttpResult(
            ok=200 <= response.status_code < 300 and _safe_bool(payload.get("ok"), default=True),
            status_code=response.status_code,
            payload=payload,
            reason=payload.get("reason") or payload.get("status") or payload.get("auth_state"),
            auth_state=payload.get("auth_state"),
            code=payload.get("code"),
        )

    def _request_with_urllib(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Optional[Mapping[str, Any]],
    ) -> AuthHttpResult:
        body_bytes: Optional[bytes] = None

        request_headers = dict(headers)
        if json_body is not None:
            body_bytes = _json_dumps_safe(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        req = UrlLibRequest(
            url=url,
            data=body_bytes,
            headers=request_headers,
            method=method,
        )

        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:  # nosec - internal service URL
                status_code = int(getattr(response, "status", 200))
                text = response.read().decode("utf-8", errors="replace")
                payload = self._parse_response_payload(status_code, text)
                return AuthHttpResult(
                    ok=200 <= status_code < 300 and _safe_bool(payload.get("ok"), default=True),
                    status_code=status_code,
                    payload=payload,
                    reason=payload.get("reason") or payload.get("status") or payload.get("auth_state"),
                    auth_state=payload.get("auth_state"),
                    code=payload.get("code"),
                )

        except HTTPError as http_error:
            status_code = int(http_error.code or 500)
            text = http_error.read().decode("utf-8", errors="replace")
            payload = self._parse_response_payload(status_code, text)
            return AuthHttpResult(
                ok=False,
                status_code=status_code,
                payload=payload,
                error=payload.get("error"),
                reason=payload.get("reason") or payload.get("status") or payload.get("auth_state"),
                auth_state=payload.get("auth_state"),
                code=payload.get("code"),
            )

        except URLError as url_error:
            raise VectoplanAuthClientError(str(url_error)) from url_error

    def _parse_response_payload(self, status_code: int, text: str) -> Dict[str, Any]:
        if not text:
            return {
                "ok": 200 <= status_code < 300,
                "status_code": status_code,
            }

        try:
            payload = json.loads(text)
        except Exception:
            return {
                "ok": False,
                "status_code": status_code,
                "error": "auth_response_not_json",
                "code": AUTH_STATE_INVALID_PAYLOAD,
                "auth_state": AUTH_STATE_INVALID_PAYLOAD,
                "message": "Auth service returned a non-JSON response.",
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "status_code": status_code,
                "error": "auth_response_invalid",
                "code": AUTH_STATE_INVALID_PAYLOAD,
                "auth_state": AUTH_STATE_INVALID_PAYLOAD,
                "message": "Auth service returned a non-object JSON response.",
            }

        payload.setdefault("status_code", status_code)
        payload.setdefault("ok", 200 <= status_code < 300 and bool(payload.get("ok", True)))

        return payload

    def _normalize_failed_http_result(self, result: AuthHttpResult, *, endpoint: str, elapsed_ms: Optional[int]) -> AuthHttpResult:
        payload = dict(result.payload or {})
        http_status = int(result.status_code or 0)

        if http_status >= 500:
            code = AUTH_STATE_HTTP_5XX
            auth_state = AUTH_STATE_HTTP_5XX
            message = "vectoplan-auth returned a server error."
            status_code = 503
        elif http_status in {401, 403}:
            code = AUTH_STATE_ACCESS_DENIED
            auth_state = AUTH_STATE_ACCESS_DENIED
            message = "vectoplan-auth rejected the server-to-server request."
            status_code = 503
        elif http_status >= 400:
            code = _safe_str(payload.get("code"), AUTH_STATE_HTTP_ERROR, 120)
            auth_state = _safe_str(payload.get("auth_state"), AUTH_STATE_HTTP_ERROR, 120)
            message = _safe_str(payload.get("message"), "vectoplan-auth returned an HTTP error.", 500)
            status_code = 503
        else:
            code = _safe_str(payload.get("code"), AUTH_STATE_REQUEST_FAILED, 120)
            auth_state = _safe_str(payload.get("auth_state"), AUTH_STATE_REQUEST_FAILED, 120)
            message = _safe_str(payload.get("message"), "vectoplan-auth request failed.", 500)
            status_code = 503

        if not payload or code in {AUTH_STATE_INVALID_PAYLOAD, "auth_response_invalid", "auth_response_not_json"}:
            unavailable = self.unavailable_payload(
                endpoint=endpoint,
                code=code,
                auth_state=auth_state,
                message=message,
                error=result.error or payload.get("error"),
                elapsed_ms=elapsed_ms,
            )
            return AuthHttpResult(
                ok=False,
                status_code=status_code,
                payload=unavailable,
                error=result.error or payload.get("error"),
                reason=auth_state,
                endpoint=endpoint,
                elapsed_ms=elapsed_ms,
                auth_state=auth_state,
                code=code,
            )

        payload.setdefault("ok", False)
        payload.setdefault("code", code)
        payload.setdefault("auth_state", auth_state)
        payload.setdefault("message", message)
        payload.setdefault("status_code", http_status)

        return AuthHttpResult(
            ok=False,
            status_code=http_status,
            payload=payload,
            error=result.error or payload.get("error"),
            reason=auth_state,
            endpoint=endpoint,
            elapsed_ms=elapsed_ms,
            auth_state=auth_state,
            code=code,
        )

    def _build_headers(
        self,
        incoming_headers: Mapping[str, Any],
        headers_extra: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "X-Requested-With": "service",
            "X-VECTOPLAN-Service": self.service_name,
        }

        request_id = self._get_or_create_request_id(incoming_headers)
        headers["X-Request-ID"] = request_id
        headers["X-Correlation-ID"] = _safe_str(
            incoming_headers.get("X-Correlation-ID") or incoming_headers.get("x-correlation-id"),
            default=request_id,
        )

        for key, value in incoming_headers.items():
            normalized = _mask_header_name(key)
            if normalized in FORWARDED_HEADER_ALLOWLIST:
                text = _safe_str(value)
                if text:
                    canonical_name = self._canonical_header_name(normalized)
                    headers[canonical_name] = text

        if headers_extra:
            for key, value in headers_extra.items():
                text = _safe_str(value)
                if text:
                    headers[key] = text

        return headers

    def _canonical_header_name(self, normalized_name: str) -> str:
        mapping = {
            "cookie": "Cookie",
            "user-agent": "User-Agent",
            "x-forwarded-for": "X-Forwarded-For",
            "x-forwarded-proto": "X-Forwarded-Proto",
            "x-forwarded-host": "X-Forwarded-Host",
            "x-real-ip": "X-Real-IP",
            "x-request-id": "X-Request-ID",
            "x-correlation-id": "X-Correlation-ID",
            "accept-language": "Accept-Language",
        }
        return mapping.get(normalized_name, normalized_name)

    def _get_or_create_request_id(self, incoming_headers: Mapping[str, Any]) -> str:
        existing = _safe_str(
            incoming_headers.get("X-Request-ID")
            or incoming_headers.get("x-request-id")
            or incoming_headers.get("X-Correlation-ID")
            or incoming_headers.get("x-correlation-id")
        )
        if existing:
            return existing[:160]
        return f"req_{uuid.uuid4().hex}"

    def _cache_key(
        self,
        method: str,
        endpoint: str,
        incoming_headers: Mapping[str, Any],
        json_body: Optional[Mapping[str, Any]],
    ) -> str:
        cookie = _safe_str(incoming_headers.get("Cookie") or incoming_headers.get("cookie"))
        authorization = _safe_str(incoming_headers.get("Authorization") or incoming_headers.get("authorization"))
        user_agent = _safe_str(incoming_headers.get("User-Agent") or incoming_headers.get("user-agent"))
        forwarded_for = _safe_str(incoming_headers.get("X-Forwarded-For") or incoming_headers.get("x-forwarded-for"))

        identity_fingerprint = _sha256_text("|".join([cookie, authorization, user_agent, forwarded_for]))
        body_fingerprint = _sha256_text(_json_dumps_safe(json_body or {}))
        base_fingerprint = _sha256_text(self.base_url + "|" + str(self.timeout_seconds))
        return f"{base_fingerprint}:{method}:{endpoint}:{identity_fingerprint}:{body_fingerprint}"

    def _request_cache_dict(self) -> Optional[MutableMapping[str, _CacheEntry]]:
        try:
            if has_request_context() and g is not None:
                cache = getattr(g, REQUEST_CACHE_ATTR, None)
                if cache is None:
                    cache = {}
                    setattr(g, REQUEST_CACHE_ATTR, cache)
                return cache
        except Exception:
            return None
        return None

    def _cache_get(self, key: str) -> Optional[_CacheEntry]:
        request_cache = self._request_cache_dict()
        if request_cache is not None:
            entry = request_cache.get(key)
            if entry and entry.expires_at >= _now():
                return entry
            if entry:
                request_cache.pop(key, None)

        if self.context_cache_seconds > 0:
            entry = _PROCESS_CACHE.get(key)
            if entry and entry.expires_at >= _now():
                return entry
            if entry:
                _PROCESS_CACHE.pop(key, None)

        return None

    def _cache_set(self, key: str, payload: Dict[str, Any], status_code: int, *, ok: bool) -> None:
        ttl = 300.0 if ok else max(0.0, self.negative_cache_seconds)

        request_cache = self._request_cache_dict()
        if request_cache is not None and ttl > 0:
            request_cache[key] = _CacheEntry(
                expires_at=_now() + ttl,
                payload=dict(payload),
                status_code=status_code,
                ok=ok,
            )

        if ok and self.context_cache_seconds > 0:
            expires_at = _now() + min(self.context_cache_seconds, MAX_PROCESS_CACHE_SECONDS)
            _PROCESS_CACHE[key] = _CacheEntry(
                expires_at=expires_at,
                payload=dict(payload),
                status_code=status_code,
                ok=ok,
            )
            self._prune_process_cache()

        if not ok and self.negative_cache_seconds > 0:
            expires_at = _now() + min(self.negative_cache_seconds, MAX_PROCESS_CACHE_SECONDS)
            _PROCESS_CACHE[key] = _CacheEntry(
                expires_at=expires_at,
                payload=dict(payload),
                status_code=status_code,
                ok=ok,
            )
            self._prune_process_cache()

    def _prune_process_cache(self) -> None:
        if len(_PROCESS_CACHE) < 512:
            return

        now = _now()
        expired_keys = [key for key, entry in _PROCESS_CACHE.items() if entry.expires_at < now]
        for key in expired_keys:
            _PROCESS_CACHE.pop(key, None)

        if len(_PROCESS_CACHE) > 1024:
            for key in list(_PROCESS_CACHE.keys())[:256]:
                _PROCESS_CACHE.pop(key, None)

    def unavailable_payload(
        self,
        endpoint: str,
        error: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
        *,
        code: str = AUTH_STATE_UNAVAILABLE,
        auth_state: str = AUTH_STATE_UNAVAILABLE,
        message: str = "vectoplan-auth is unavailable.",
    ) -> Dict[str, Any]:
        safe_code = _safe_str(code, AUTH_STATE_UNAVAILABLE, 160)
        safe_state = _safe_str(auth_state, AUTH_STATE_UNAVAILABLE, 160)

        payload: Dict[str, Any] = {
            "ok": False,
            "authenticated": False,
            "is_authenticated": False,
            "auth_available": False,
            "auth_state": safe_state,
            "reason": safe_state,
            "reason_code": safe_code,
            "code": safe_code,
            "status": safe_state,
            "status_code": 503,
            "service": "vectoplan-auth",
            "endpoint": endpoint,
            "user": None,
            "app_user_id": None,
            "subject": {
                "type": "unknown",
                "id": None,
            },
            "roles": {
                "primary": "guest",
                "primary_role": "guest",
                "names": ["guest"],
                "roles": ["guest"],
                "is_admin": False,
                "is_staff": False,
                "is_system": False,
            },
            "account": {
                "available": False,
                "account_id": None,
                "account_name": None,
                "account_type": None,
                "member_role": None,
                "can_manage_account": False,
                "can_manage_members": False,
                "can_invite_members": False,
            },
            "access": {
                "plan": None,
                "plan_key": None,
                "plan_status": None,
                "entitlements": [],
                "blocked": False,
                "user_blocked": False,
                "access_blocked": True,
                "blocked_reason": safe_code,
                "access_blocked_reason": safe_code,
                "demo_project_access": False,
                "cloud_access": False,
                "api_key_access": False,
            },
            "ui": {
                "show_login": bool(self.public_url),
                "show_logout": False,
                "show_account_dashboard": False,
                "dashboard_allowed": False,
                "blocked": False,
                "access_blocked": True,
                "auth_service_unavailable": True,
            },
            "cleanup": {
                "clear_session": False,
                "clear_frontend_storage": False,
                "preserve_client_identity": True,
            },
            "links": {
                "login_url": self.login_url(next_url=_get_request_path_for_next("/")) if self.public_url else "",
                "register_url": self.register_url(next_url=_get_request_path_for_next("/")) if self.public_url else "",
                "logout_url": self.logout_url() if self.public_url else "",
                "account_dashboard_url": self.account_dashboard_url() if self.public_url else "",
                "admin_dashboard_url": self.admin_dashboard_url() if self.public_url else "",
            },
            "errors": [safe_code],
            "requires_login": False,
            "session_valid": False,
            "can_demo": False,
            "can_persist": False,
            "can_project_sharing": False,
            "can_use_cloud": False,
            "can_use_bigdata": False,
            "can_manage_account": False,
            "can_manage_members": False,
            "can_manage_api_keys": False,
            "dependency": {
                "name": "vectoplan-auth",
                "available": False,
                "state": safe_state,
                "code": safe_code,
                "internal_url": _redact_url(self.base_url),
                "public_url": _redact_url(self.public_url),
                "timeout_seconds": self.timeout_seconds,
            },
            "message": message,
        }

        if error:
            payload["error"] = "auth_service_unavailable"
            payload["details"] = _safe_str(error)[:500]

        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms

        return payload

    def _log_request_error(
        self,
        method: str,
        endpoint: str,
        error: Exception,
        elapsed_ms: int,
        auth_state: str = AUTH_STATE_UNAVAILABLE,
    ) -> None:
        try:
            debug_enabled = _safe_bool(_read_config("VECTOPLAN_AUTH_DEBUG_CLIENT_ERRORS", False), False)
            message = (
                "vectoplan-auth request failed: "
                "method=%s endpoint=%s auth_state=%s elapsed_ms=%s error=%s"
            )
            LOGGER.warning(message, method, endpoint, auth_state, elapsed_ms, str(error))

            if debug_enabled:
                LOGGER.debug("vectoplan-auth traceback:\n%s", traceback.format_exc())
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Module-level API
# ─────────────────────────────────────────────────────────────

def get_auth_context_client(refresh: bool = False) -> VectoplanAuthClient:
    global _CLIENT_SINGLETON

    try:
        with _CLIENT_LOCK:
            if refresh or _CLIENT_SINGLETON is None:
                _CLIENT_SINGLETON = VectoplanAuthClient.from_config()
            return _CLIENT_SINGLETON
    except Exception:
        return VectoplanAuthClient.from_config()


def clear_auth_context_client_cache() -> None:
    try:
        _PROCESS_CACHE.clear()
    except Exception:
        pass


def get_auth_context_client_status() -> Dict[str, Any]:
    try:
        return get_auth_context_client().status()
    except Exception as exc:
        return {
            "ok": False,
            "service": "auth_context_client",
            "code": "auth_context_client_status_failed",
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }


def get_current_auth_me(use_cache: bool = True) -> Dict[str, Any]:
    return get_auth_context_client().me(use_cache=use_cache)


def get_current_auth_context(minimal: bool = True, use_cache: bool = True) -> Dict[str, Any]:
    return get_auth_context_client().context(minimal=minimal, use_cache=use_cache)


def get_current_auth_context_minimal(use_cache: bool = True) -> Dict[str, Any]:
    return get_current_auth_context(minimal=True, use_cache=use_cache)


def get_current_auth_context_full(use_cache: bool = True) -> Dict[str, Any]:
    return get_current_auth_context(minimal=False, use_cache=use_cache)


def is_auth_service_ready() -> bool:
    payload = get_auth_context_client().ready()
    return bool(payload.get("ok")) and _safe_str(payload.get("status")).lower() in {
        "ready",
        "healthy",
        "ok",
        "available",
    }


def build_login_url(next_url: Optional[str] = None, mode: str = "login") -> str:
    return get_auth_context_client().login_url(next_url=next_url, mode=mode)


def build_register_url(next_url: Optional[str] = None) -> str:
    return get_auth_context_client().register_url(next_url=next_url)


def build_account_dashboard_url() -> str:
    return get_auth_context_client().account_dashboard_url()


def build_admin_dashboard_url() -> str:
    return get_auth_context_client().admin_dashboard_url()


def make_auth_unavailable_payload(
    endpoint: str = DEFAULT_CONTEXT_MINIMAL_PATH,
    *,
    error: Optional[str] = None,
    elapsed_ms: Optional[int] = None,
    code: str = AUTH_STATE_UNAVAILABLE,
    auth_state: str = AUTH_STATE_UNAVAILABLE,
) -> Dict[str, Any]:
    return get_auth_context_client().unavailable_payload(
        endpoint=endpoint,
        error=error,
        elapsed_ms=elapsed_ms,
        code=code,
        auth_state=auth_state,
    )


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
    "AUTH_STATE_TIMEOUT",
    "AUTH_STATE_UNAVAILABLE",
    "AuthHttpResult",
    "VectoplanAuthClient",
    "VectoplanAuthClientError",
    "build_account_dashboard_url",
    "build_admin_dashboard_url",
    "build_login_url",
    "build_register_url",
    "clear_auth_context_client_cache",
    "get_auth_context_client",
    "get_auth_context_client_status",
    "get_current_auth_context",
    "get_current_auth_context_full",
    "get_current_auth_context_minimal",
    "get_current_auth_me",
    "is_auth_service_ready",
    "make_auth_unavailable_payload",
]