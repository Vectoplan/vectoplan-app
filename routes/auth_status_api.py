# services/vectoplan-app/routes/auth_status_api.py
from __future__ import annotations

"""
Auth status API für vectoplan-app.

Zweck:
- Diagnose-Endpunkte für die Dependency vectoplan-auth bereitstellen.
- Auth-Service-Ausfall sauber als 503 sichtbar machen.
- Kein User-Login erzwingen, weil diese Endpunkte beim Debuggen gerade dann
  erreichbar sein müssen, wenn Auth kaputt ist.
- Keine Secrets ausgeben:
    keine Cookies
    keine Authorization Header
    keine API-Keys
    keine Service Tokens
- Kein Default-User.
- Kein Demo-Fallback.
"""

import json
import time
from typing import Any, Dict, Mapping, Optional, Tuple

try:
    from flask import Blueprint, Response, current_app, has_app_context, jsonify, request
except Exception:  # pragma: no cover
    Blueprint = None  # type: ignore
    Response = Any  # type: ignore
    current_app = None  # type: ignore
    request = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False

    def jsonify(*args: Any, **kwargs: Any) -> Dict[str, Any]:  # type: ignore
        if args and isinstance(args[0], dict):
            return args[0]
        return dict(kwargs)


try:
    from services.auth_dependency_service import (  # type: ignore
        check_auth_dependency,
        clear_auth_dependency_cache,
        get_auth_dependency_status,
    )
except Exception:  # pragma: no cover
    try:
        from ..services.auth_dependency_service import (  # type: ignore
            check_auth_dependency,
            clear_auth_dependency_cache,
            get_auth_dependency_status,
        )
    except Exception:
        check_auth_dependency = None  # type: ignore
        clear_auth_dependency_cache = None  # type: ignore
        get_auth_dependency_status = None  # type: ignore


try:
    from services.auth_context_client import (  # type: ignore
        clear_auth_context_client_cache,
        get_auth_context_client_status,
    )
except Exception:  # pragma: no cover
    try:
        from ..services.auth_context_client import (  # type: ignore
            clear_auth_context_client_cache,
            get_auth_context_client_status,
        )
    except Exception:
        clear_auth_context_client_cache = None  # type: ignore
        get_auth_context_client_status = None  # type: ignore


try:
    from services.auth_context import (  # type: ignore
        get_auth_context_status,
        get_current_auth_context,
    )
except Exception:  # pragma: no cover
    try:
        from ..services.auth_context import (  # type: ignore
            get_auth_context_status,
            get_current_auth_context,
        )
    except Exception:
        get_auth_context_status = None  # type: ignore
        get_current_auth_context = None  # type: ignore


try:
    from services.current_user import get_current_user_status  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..services.current_user import get_current_user_status  # type: ignore
    except Exception:
        get_current_user_status = None  # type: ignore


try:
    from services.auth_requirements import get_auth_requirements_status  # type: ignore
except Exception:  # pragma: no cover
    try:
        from ..services.auth_requirements import get_auth_requirements_status  # type: ignore
    except Exception:
        get_auth_requirements_status = None  # type: ignore


# ─────────────────────────────────────────────────────────────
# Blueprint
# ─────────────────────────────────────────────────────────────

bp = Blueprint("auth_status_api", __name__, url_prefix="/v1/auth") if Blueprint is not None else None


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _now() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


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

        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable", "ok", "ready"}:
            return True
        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable", "error", "failed"}:
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


def _json_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _config_bool(name: str, default: bool = False) -> bool:
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return _safe_bool(current_app.config.get(name), default)
    except Exception:
        pass
    return default


def _request_bool(name: str, default: bool = False) -> bool:
    try:
        if request is None:
            return default
        if name not in request.args:
            return default
        return _safe_bool(request.args.get(name), default)
    except Exception:
        return default


def _request_str(name: str, default: str = "", max_len: int = 400) -> str:
    try:
        if request is None:
            return default
        return _safe_str(request.args.get(name), default, max_len)
    except Exception:
        return default


def _include_private_allowed() -> bool:
    """
    Private Diagnosefelder dürfen nur ausgegeben werden, wenn die App explizit
    dafür konfiguriert wurde. Auch dann dürfen keine Secrets enthalten sein.
    """
    return bool(
        _request_bool("private", False)
        and _config_bool("VECTOPLAN_AUTH_STATUS_INCLUDE_PRIVATE", False)
    )


def _force_refresh_requested() -> bool:
    return _request_bool("refresh", False) or _request_bool("force_refresh", False) or _request_bool("no_cache", False)


def _status_from_payload(payload: Mapping[str, Any], default: int = 200) -> int:
    data = _safe_dict(payload)

    if _safe_bool(data.get("ok"), False):
        return 200

    status = _safe_int(data.get("status_code") or data.get("http_status"), 0)
    if status > 0:
        return status

    code = _safe_str(data.get("code") or data.get("auth_state") or data.get("reason"), "", 160).lower()
    if code in {
        "auth_service_unavailable",
        "service_unavailable",
        "dependency_unavailable",
        "dns_failed",
        "connection_refused",
        "timeout",
        "http_5xx",
        "request_failed",
        "not_configured",
        "requests_unavailable",
    }:
        return 503

    return default


def _api_response(payload: Mapping[str, Any], status_code: Optional[int] = None) -> Any:
    status = status_code if status_code is not None else _status_from_payload(payload, 200)

    response_payload = dict(payload)
    response_payload.setdefault("status_code", status)

    try:
        response = jsonify(response_payload)
        response.status_code = status
        return response
    except Exception:
        response_payload["status_code"] = status
        return response_payload


def _safe_call(name: str, func: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    if func is None:
        return {
            "ok": False,
            "available": False,
            "code": f"{name}_unavailable",
            "message": f"{name} is not importable.",
        }

    try:
        result = func(*args, **kwargs)
        data = _safe_dict(result)
        if "ok" not in data:
            data["ok"] = True
        return data
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "code": f"{name}_failed",
            "message": str(exc),
            "error_type": exc.__class__.__name__,
        }


def _redact_payload(value: Any) -> Any:
    """
    Defensive Redaction für Diagnoseantworten.

    Die darunterliegenden Services sollen bereits keine Secrets liefern. Diese
    Funktion entfernt zusätzlich bekannte riskante Keys, falls alte Module etwas
    durchreichen.
    """
    secret_keys = {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-vectoplan-api-key",
        "api_key",
        "apiKey",
        "api_token",
        "apiToken",
        "service_token",
        "serviceToken",
        "access_token",
        "accessToken",
        "refresh_token",
        "refreshToken",
        "token",
        "secret",
        "password",
        "session",
        "session_cookie",
        "sessionCookie",
    }

    try:
        if isinstance(value, dict):
            result: Dict[str, Any] = {}
            for key, item in value.items():
                if str(key).lower() in {secret.lower() for secret in secret_keys}:
                    result[key] = "<redacted>"
                else:
                    result[key] = _redact_payload(item)
            return result

        if isinstance(value, list):
            return [_redact_payload(item) for item in value]

        if isinstance(value, tuple):
            return [_redact_payload(item) for item in value]

        return value
    except Exception:
        return value


def _current_request_summary() -> Dict[str, Any]:
    try:
        if request is None:
            return {}

        return {
            "method": _safe_str(getattr(request, "method", None), "", 20),
            "path": _safe_str(getattr(request, "path", None), "", 400),
            "query_refresh": _force_refresh_requested(),
            "query_private": _include_private_allowed(),
            "wants_json": True,
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# Data builders
# ─────────────────────────────────────────────────────────────

def build_dependency_payload(
    *,
    force_refresh: bool = False,
    include_private: bool = False,
    endpoint: str = "context_minimal",
) -> Dict[str, Any]:
    if get_auth_dependency_status is None:
        return {
            "ok": False,
            "available": False,
            "code": "auth_dependency_service_missing",
            "auth_state": "dependency_unavailable",
            "message": "auth_dependency_service is not available.",
            "status_code": 503,
        }

    payload = get_auth_dependency_status(
        force_refresh=force_refresh,
        endpoint=endpoint,
        include_private=include_private,
    )

    return _safe_dict(_redact_payload(payload))


def build_client_payload() -> Dict[str, Any]:
    return _safe_dict(
        _redact_payload(
            _safe_call(
                "auth_context_client_status",
                get_auth_context_client_status,
            )
        )
    )


def build_normalized_context_payload(*, force_refresh: bool = False) -> Dict[str, Any]:
    if get_current_auth_context is None:
        return {
            "ok": False,
            "code": "auth_context_missing",
            "message": "auth_context service is not importable.",
            "status_code": 503,
        }

    try:
        context = get_current_auth_context(
            minimal=True,
            use_cache=not force_refresh,
            force_refresh=force_refresh,
        )

        if hasattr(context, "to_public_dict"):
            payload = context.to_public_dict(include_raw=False)
        elif hasattr(context, "to_dict"):
            payload = context.to_dict()
        else:
            payload = {}

        payload = _safe_dict(payload)
        payload.setdefault("ok", bool(getattr(context, "ok", False)))
        payload.setdefault("status_code", getattr(context, "status_code", None) or (503 if getattr(context, "auth_unavailable", False) else 200))
        payload.setdefault("service", "auth_context")
        return _safe_dict(_redact_payload(payload))

    except Exception as exc:
        return {
            "ok": False,
            "code": "auth_context_failed",
            "auth_state": "service_unavailable",
            "message": str(exc),
            "error_type": exc.__class__.__name__,
            "status_code": 503,
        }


def build_current_user_payload() -> Dict[str, Any]:
    return _safe_dict(
        _redact_payload(
            _safe_call(
                "current_user_status",
                get_current_user_status,
            )
        )
    )


def build_requirements_payload() -> Dict[str, Any]:
    return _safe_dict(
        _redact_payload(
            _safe_call(
                "auth_requirements_status",
                get_auth_requirements_status,
            )
        )
    )


def build_aggregate_status_payload(
    *,
    force_refresh: bool = False,
    include_private: bool = False,
    include_context: bool = True,
    include_current_user: bool = False,
    endpoint: str = "context_minimal",
) -> Dict[str, Any]:
    started = _now()

    dependency = build_dependency_payload(
        force_refresh=force_refresh,
        include_private=include_private,
        endpoint=endpoint,
    )

    client = build_client_payload()
    requirements = build_requirements_payload()

    context_payload: Dict[str, Any] = {}
    if include_context:
        context_payload = build_normalized_context_payload(force_refresh=force_refresh)

    current_user_payload: Dict[str, Any] = {}
    if include_current_user:
        current_user_payload = build_current_user_payload()

    dependency_ok = _safe_bool(dependency.get("ok"), False)
    dependency_available = _safe_bool(dependency.get("available"), dependency_ok)

    auth_state = _safe_str(
        dependency.get("auth_state")
        or dependency.get("code")
        or dependency.get("reason")
        or "unknown",
        "unknown",
        160,
    )

    status_code = 200 if dependency_ok and dependency_available else 503

    payload: Dict[str, Any] = {
        "ok": bool(dependency_ok and dependency_available),
        "service": "vectoplan-app",
        "component": "auth_status_api",
        "auth_truth": "vectoplan-auth",
        "auth_available": bool(dependency_available),
        "auth_state": auth_state,
        "code": "auth_available" if dependency_ok and dependency_available else _safe_str(dependency.get("code"), "auth_service_unavailable", 160),
        "message": "vectoplan-auth is reachable." if dependency_ok and dependency_available else "vectoplan-auth is not reachable or not usable.",
        "status_code": status_code,
        "checked_at": _now(),
        "elapsed_ms": int((_now() - started) * 1000),
        "dependency": dependency,
        "client": client,
        "requirements": requirements,
        "default_user": False,
        "demo_fallback_on_unavailable": False,
        "request": _current_request_summary(),
        "rules": {
            "vectoplan_auth_is_user_truth": True,
            "default_user_removed": True,
            "auth_unavailable_returns_503": True,
            "auth_unavailable_is_not_user_ban": True,
            "demo_requires_auth_context": True,
            "no_secrets_in_response": True,
            "no_local_auth_fallback": True,
        },
    }

    if include_context:
        payload["context"] = context_payload

    if include_current_user:
        payload["current_user"] = current_user_payload

    return _safe_dict(_redact_payload(payload))


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

if bp is not None:

    @bp.get("/status")
    def auth_status() -> Any:
        """
        Aggregierter Auth-Status.

        Query:
        - refresh=1: Cache umgehen
        - context=0: normalisierten Kontext nicht laden
        - current_user=1: current_user Diagnose zusätzlich laden
        - endpoint=context_minimal|context|me|ready: Dependency-Check-Ziel
        """
        force_refresh = _force_refresh_requested()
        include_private = _include_private_allowed()
        include_context = not _request_bool("context", True) is False
        include_current_user = _request_bool("current_user", False)
        endpoint = _request_str("endpoint", "context_minimal", 80) or "context_minimal"

        payload = build_aggregate_status_payload(
            force_refresh=force_refresh,
            include_private=include_private,
            include_context=include_context,
            include_current_user=include_current_user,
            endpoint=endpoint,
        )

        return _api_response(payload, _status_from_payload(payload, 503))

    @bp.get("/dependency")
    def auth_dependency() -> Any:
        """
        Reiner vectoplan-auth Dependency-Check.
        """
        force_refresh = _force_refresh_requested()
        include_private = _include_private_allowed()
        endpoint = _request_str("endpoint", "context_minimal", 80) or "context_minimal"

        payload = build_dependency_payload(
            force_refresh=force_refresh,
            include_private=include_private,
            endpoint=endpoint,
        )

        payload.setdefault("service", "vectoplan-app")
        payload.setdefault("component", "auth_dependency")
        payload.setdefault("auth_truth", "vectoplan-auth")
        payload.setdefault("default_user", False)
        payload.setdefault("demo_fallback_on_unavailable", False)

        return _api_response(payload, _status_from_payload(payload, 503))

    @bp.get("/client")
    def auth_client_status() -> Any:
        """
        Lokaler Auth-Client-Status ohne externen Request.
        """
        payload = build_client_payload()
        payload.setdefault("service", "vectoplan-app")
        payload.setdefault("component", "auth_context_client")
        payload.setdefault("auth_truth", "vectoplan-auth")
        payload.setdefault("default_user", False)

        return _api_response(payload, 200 if _safe_bool(payload.get("ok"), False) else 503)

    @bp.get("/context")
    def auth_context_status_route() -> Any:
        """
        Normalisierter AuthContext für den aktuellen Request.

        Nutzt Cookies/Header des Requests, gibt aber keine Secrets aus.
        """
        force_refresh = _force_refresh_requested()
        payload = build_normalized_context_payload(force_refresh=force_refresh)
        payload.setdefault("service", "vectoplan-app")
        payload.setdefault("component", "auth_context")
        payload.setdefault("auth_truth", "vectoplan-auth")
        payload.setdefault("default_user", False)

        return _api_response(payload, _status_from_payload(payload, 503))

    @bp.get("/current-user")
    def auth_current_user_status_route() -> Any:
        """
        CurrentUserContext-Diagnose.

        Kann DB/AppUser-Link-Status enthalten, aber keine Secrets.
        """
        payload = build_current_user_payload()
        payload.setdefault("service", "vectoplan-app")
        payload.setdefault("component", "current_user")
        payload.setdefault("auth_truth", "vectoplan-auth")
        payload.setdefault("default_user", False)

        return _api_response(payload, 200 if _safe_bool(payload.get("ok"), False) else _status_from_payload(payload, 503))

    @bp.post("/cache/clear")
    def auth_cache_clear() -> Any:
        """
        Löscht lokale Auth-Diagnose-/Client-Caches.

        Kein Auth-Truth-Mutationspfad. Keine Session wird gelöscht.
        """
        cleared = {
            "dependency_cache": False,
            "context_client_cache": False,
        }

        if clear_auth_dependency_cache is not None:
            try:
                clear_auth_dependency_cache()
                cleared["dependency_cache"] = True
            except Exception:
                cleared["dependency_cache"] = False

        if clear_auth_context_client_cache is not None:
            try:
                clear_auth_context_client_cache()
                cleared["context_client_cache"] = True
            except Exception:
                cleared["context_client_cache"] = False

        payload = {
            "ok": True,
            "service": "vectoplan-app",
            "component": "auth_cache_clear",
            "cleared": cleared,
            "message": "Local auth diagnostic/client caches cleared.",
            "default_user": False,
        }

        return _api_response(payload, 200)


# ─────────────────────────────────────────────────────────────
# Module-level status
# ─────────────────────────────────────────────────────────────

def get_auth_status_api_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "auth_status_api",
        "blueprint_available": bp is not None,
        "routes": [
            "/v1/auth/status",
            "/v1/auth/dependency",
            "/v1/auth/client",
            "/v1/auth/context",
            "/v1/auth/current-user",
            "/v1/auth/cache/clear",
        ],
        "default_user": False,
        "demo_fallback_on_unavailable": False,
        "no_secrets": True,
    }


__all__ = [
    "bp",
    "build_aggregate_status_payload",
    "build_client_payload",
    "build_current_user_payload",
    "build_dependency_payload",
    "build_normalized_context_payload",
    "build_requirements_payload",
    "get_auth_status_api_status",
]