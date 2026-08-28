# services/vectoplan-app/config.py
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse


# ─────────────────────────────────────────────────────────────
# Env cache
# ─────────────────────────────────────────────────────────────

try:
    _ENV_CACHE: Dict[str, str] = dict(os.environ)
except Exception:
    _ENV_CACHE = {}


def refresh_env_cache() -> Dict[str, str]:
    """
    Refresh the internal environment cache.

    Normal startup should not need this. It exists for tests, reload tooling and
    unusual app-factory flows where env vars are patched after module import.
    """
    global _ENV_CACHE

    try:
        _ENV_CACHE = dict(os.environ)
    except Exception:
        _ENV_CACHE = {}

    try:
        _cached_origin_list.cache_clear()
    except Exception:
        pass

    return dict(_ENV_CACHE)


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Best-effort cached environment getter.

    The config module reads from a stable snapshot first and falls back to
    os.getenv. This makes startup deterministic while still tolerating unusual
    runtime environments.
    """
    try:
        if key in _ENV_CACHE:
            value = _ENV_CACHE.get(key)
            return value if value is not None else default
        return os.getenv(key, default)
    except Exception:
        return default


def _env_first(keys: Sequence[str], default: Optional[str] = None) -> Optional[str]:
    """
    Return the first non-empty env value from a list of keys.
    """
    try:
        for key in keys:
            value = _env(str(key), None)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return default
    except Exception:
        return default


def _env_str(key: str, default: str = "") -> str:
    try:
        value = _env(key, None)
        return str(value) if value is not None else default
    except Exception:
        return default


def _env_str_first(keys: Sequence[str], default: str = "") -> str:
    try:
        value = _env_first(keys, None)
        return str(value) if value is not None else default
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────

_STYLE_RE = re.compile(r"^[a-z0-9\-]+/[a-z0-9\-\.]+$", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[\s,;]+")

_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on", "ja", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", "nein", "disabled"})


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return bool(value)

        normalized = str(value).strip().lower()

        if normalized in _TRUE_VALUES:
            return True

        if normalized in _FALSE_VALUES:
            return False

        return default

    except Exception:
        return default


def _as_int(value: Optional[str], default: int) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _as_float(value: Optional[str], default: float) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        return float(str(value).strip())
    except Exception:
        return float(default)


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        return max(int(minimum), min(int(maximum), int(value)))
    except Exception:
        return int(minimum)


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    try:
        return max(float(minimum), min(float(maximum), float(value)))
    except Exception:
        return float(minimum)


def _safe_string(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def _as_choice(
    value: Any,
    choices: Iterable[str],
    default: str,
    *,
    lowercase: bool = True,
) -> str:
    """Return a normalized value only when it belongs to ``choices``."""
    try:
        allowed = {
            (_safe_string(item, "").strip().lower() if lowercase else _safe_string(item, "").strip())
            for item in choices
            if _safe_string(item, "").strip()
        }

        fallback = _safe_string(default, "").strip()
        if lowercase:
            fallback = fallback.lower()

        candidate = _safe_string(value, "").strip()
        if lowercase:
            candidate = candidate.lower()

        if candidate in allowed:
            return candidate

        if fallback in allowed:
            return fallback

        return sorted(allowed)[0] if allowed else fallback

    except Exception:
        return _safe_string(default, "").strip().lower() if lowercase else _safe_string(default, "").strip()


def _as_world_template(value: Any, default: str = "earth") -> str:
    """Normalize the supported App -> Chunk world template contract."""
    try:
        return _as_choice(value, ("earth", "flat"), default)
    except Exception:
        return "earth" if str(default).strip().lower() != "flat" else "flat"


def _as_fallback_world_template(value: Any, requested: Any, default: str = "flat") -> str:
    """Return a supported fallback that never equals the requested template."""
    try:
        requested_template = _as_world_template(requested, "earth")
        fallback = _as_world_template(value, default)

        if fallback == requested_template:
            return "flat" if requested_template == "earth" else "earth"

        return fallback

    except Exception:
        return "flat"


def _as_code_list(value: Optional[str], default: Iterable[str]) -> List[str]:
    """Parse stable lowercase error/status codes without accepting arbitrary text."""
    result: List[str] = []

    try:
        for item in _as_text_list(value, default):
            code = re.sub(r"[^a-z0-9_]+", "_", _safe_string(item, "").strip().lower()).strip("_")
            if code and code not in result:
                result.append(code)
    except Exception:
        result = []

    if result:
        return result

    try:
        return [
            re.sub(r"[^a-z0-9_]+", "_", _safe_string(item, "").strip().lower()).strip("_")
            for item in default
            if _safe_string(item, "").strip()
        ]
    except Exception:
        return []


def _as_json_list(value: Optional[str]) -> List[Any]:
    """
    Robust list parser.

    Accepted formats:
    - JSON array: ["a", "b"]
    - CSV: a,b
    - whitespace list: a b
    - semicolon list: a;b
    """
    try:
        if value is None:
            return []

        text = str(value).strip()

        if not text:
            return []

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return []
        except Exception:
            pass

        if "," in text or ";" in text:
            return [part.strip() for part in re.split(r"[,;]+", text) if part.strip()]

        if " " in text or "\t" in text or "\n" in text:
            return [part.strip() for part in _SPLIT_RE.split(text) if part.strip()]

        return [text]

    except Exception:
        return []


def _as_text_list(value: Optional[str], default: Optional[Iterable[str]] = None) -> List[str]:
    """
    Parse env list values into normalized strings.
    """
    try:
        parsed = _as_json_list(value)

        if not parsed and default is not None:
            parsed = list(default)

        result: List[str] = []
        for item in parsed:
            text = _safe_string(item, "").strip()
            if text and text not in result:
                result.append(text)

        return result

    except Exception:
        try:
            return list(default or [])
        except Exception:
            return []


def _norm_url(url: str, default: str = "") -> str:
    """
    Normalize a base URL.

    - strips whitespace
    - removes trailing slash
    - keeps scheme, host, port and path otherwise unchanged
    """
    try:
        text = str(url or "").strip()

        if not text:
            return default.rstrip("/") if default else default

        return text.rstrip("/")

    except Exception:
        return default.rstrip("/") if default else default


def _norm_path(path: str, default: str = "/") -> str:
    """
    Normalize a URL path.

    - ensures a leading slash
    - collapses duplicate slashes
    - never returns an empty string
    """
    try:
        fallback = default if default else "/"
        if not fallback.startswith("/"):
            fallback = "/" + fallback

        text = str(path or "").strip()

        if not text:
            return fallback

        if not text.startswith("/"):
            text = "/" + text

        while "//" in text:
            text = text.replace("//", "/")

        return text or fallback

    except Exception:
        return default if default else "/"


def _join_url(base_url: str, path: str, default: str = "") -> str:
    """
    Join a normalized base URL and normalized path without swallowing paths.
    """
    try:
        base = _norm_url(base_url, "")
        route = _norm_path(path, "/")

        if not base:
            return default

        if route == "/":
            return base

        return f"{base}{route}"

    except Exception:
        return default


def _sanitize_style_id(style_id: str, default: str) -> str:
    """
    Accept only Mapbox style IDs in the format '<owner>/<style-id>'.
    """
    try:
        text = str(style_id or "").strip()

        if not text:
            return default

        return text if _STYLE_RE.match(text) else default

    except Exception:
        return default


def _as_center_pair(value: Optional[str], default_lon: float, default_lat: float) -> List[float]:
    """
    Parse MAP_DEFAULT_CENTER.

    Supported values:
    - JSON list: [11.576124, 48.137154]
    - CSV: 11.576124,48.137154

    Result is always [lon, lat] in WGS84.
    """
    try:
        raw = _as_json_list(value)

        lon = float(raw[0]) if len(raw) > 0 else float(default_lon)
        lat = float(raw[1]) if len(raw) > 1 else float(default_lat)

        return [
            _clamp_float(lon, -180.0, 180.0),
            _clamp_float(lat, -90.0, 90.0),
        ]

    except Exception:
        return [float(default_lon), float(default_lat)]


@lru_cache(maxsize=64)
def _cached_origin_list(raw: str, fallback: str = "") -> List[str]:
    """
    Cached parser for frame-ancestor / frame-src / connect-src style origin lists.

    Keeps values stable and avoids repeated parsing in app-factory/security code
    that may read config several times.
    """
    try:
        parsed = _as_text_list(raw, _as_text_list(fallback))
        return [item for item in parsed if item]
    except Exception:
        return _as_text_list(fallback)


def _origins_to_csp_value(origins: Sequence[str], include_self: bool = False) -> str:
    """
    Convert origins to a CSP-friendly value.

    Values like "self" are normalized to "'self'".
    Plain http(s) origins remain unchanged.
    """
    try:
        result: List[str] = []

        if include_self:
            result.append("'self'")

        for origin in origins:
            text = _safe_string(origin, "").strip()
            if not text:
                continue

            normalized = "'self'" if text in {"self", "'self'"} else text

            if normalized not in result:
                result.append(normalized)

        return " ".join(result)

    except Exception:
        return "'self'" if include_self else ""


def _space_join(values: Sequence[str]) -> str:
    try:
        return " ".join([str(value).strip() for value in values if str(value).strip()])
    except Exception:
        return ""


def _dedupe_texts(values: Iterable[str]) -> List[str]:
    """Return non-empty unique strings preserving order."""
    result: List[str] = []

    try:
        for value in values:
            text = _safe_string(value, "").strip()
            if text and text not in result:
                result.append(text)
    except Exception:
        pass

    return result


# ─────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────

_DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"

_DEFAULT_AUTH_PUBLIC_URL = "http://127.0.0.1:5000"
_DEFAULT_AUTH_INTERNAL_URL = "http://vectoplan-auth:5000"
_DEFAULT_AUTH_ROUTE = "/auth"
_DEFAULT_AUTH_ME_PATH = "/auth/me"
_DEFAULT_AUTH_CONTEXT_PATH = "/auth/context"
_DEFAULT_AUTH_CONTEXT_MINIMAL_PATH = "/auth/context/minimal"
_DEFAULT_AUTH_ACCOUNT_DASHBOARD_PATH = "/auth/account/dashboard"
_DEFAULT_AUTH_ADMIN_DASHBOARD_PATH = "/auth/admin/dashboard"
_DEFAULT_AUTH_LOGOUT_PATH = "/auth/logout"
_DEFAULT_AUTH_HEALTH_READY_PATH = "/health/ready"
_DEFAULT_AUTH_HEALTH_LIVE_PATH = "/health/live"

_DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
_DEFAULT_EDITOR_INTERNAL_URL = "http://vectoplan-editor:5000"
_DEFAULT_EDITOR_ROUTE = "/editor"

_DEFAULT_CAD_PUBLIC_URL = "http://localhost:5104"
_DEFAULT_CAD_INTERNAL_URL = "http://vectoplan-cad:5000"
_DEFAULT_CAD_ROUTE = "/cad"

_DEFAULT_LV_PUBLIC_URL = "http://localhost:5105"
_DEFAULT_LV_INTERNAL_URL = "http://vectoplan-lv:5000"
_DEFAULT_LV_ROUTE = "/lv"

_DEFAULT_FILECLOUD_PUBLIC_URL = "http://localhost:5107"
_DEFAULT_FILECLOUD_INTERNAL_URL = "http://vectoplan-filecloud:5000"
_DEFAULT_FILECLOUD_ROUTE = "/files"

_DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"
_DEFAULT_OPENLAYER_INTERNAL_URL = "http://openlayer:8090"
_DEFAULT_OPENLAYER_ROUTE = "/map"

_DEFAULT_CHUNK_PUBLIC_URL = "http://localhost:5102"
_DEFAULT_CHUNK_INTERNAL_URL = "http://vectoplan-chunk:5000"
_DEFAULT_CORE_INTERNAL_URL = "http://vectoplan-core:5000"

# App-project provisioning policy. This does not change the independent
# vectoplan-chunk bootstrap/default-project policy. New App projects request
# Earth first and use Flat only as a controlled fallback.
_DEFAULT_APP_WORLD_TEMPLATE = "earth"
_DEFAULT_APP_FALLBACK_WORLD_TEMPLATE = "flat"
_DEFAULT_APP_EARTH_CRS_ID = "EPSG:4979"
_DEFAULT_APP_PROJECT_COORDINATE_CRS_ID = "EPSG:4326"
_DEFAULT_APP_EARTH_HEIGHT = 0.0
_DEFAULT_CHUNK_DEFAULT_TEMPLATE_ID = _DEFAULT_APP_WORLD_TEMPLATE
_DEFAULT_CHUNK_FALLBACK_TEMPLATE_ID = _DEFAULT_APP_FALLBACK_WORLD_TEMPLATE
_DEFAULT_CHUNK_DEFAULT_WORLD_ID = "world_spawn"

_DEFAULT_CHUNK_FALLBACK_ERROR_CODES = (
    "coordinates_unavailable",
    "earth_reference_missing",
    "earth_reference_required",
    "earth_reference_incomplete",
    "earth_reference_invalid",
    "earth_reference_not_available",
    "invalid_earth_reference",
    "project_coordinates_unavailable",
    "unsupported_coordinate_reference",
)

_DEFAULT_LIBRARY_PUBLIC_URL = "http://localhost:5101"
_DEFAULT_LIBRARY_INTERNAL_URL = "http://vectoplan-library:5000"

_DEFAULT_ALLOWED_FRAME_PARENTS = (
    "http://localhost:5103",
    "http://127.0.0.1:5103",
)

_DEFAULT_APP_ALLOWED_FRAME_SRC = (
    "self",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5100",
    "http://127.0.0.1:5100",
    "http://localhost:5104",
    "http://127.0.0.1:5104",
    "http://localhost:5105",
    "http://127.0.0.1:5105",
    "http://localhost:5107",
    "http://127.0.0.1:5107",
    "http://localhost:5190",
    "http://127.0.0.1:5190",
)

_DEFAULT_APP_ALLOWED_CONNECT_SRC = (
    "self",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5100",
    "http://127.0.0.1:5100",
    "http://localhost:5101",
    "http://127.0.0.1:5101",
    "http://localhost:5102",
    "http://127.0.0.1:5102",
    "http://localhost:5104",
    "http://127.0.0.1:5104",
    "http://localhost:5107",
    "http://127.0.0.1:5107",
    "http://localhost:5110",
    "http://127.0.0.1:5110",
    "http://localhost:5182",
    "http://127.0.0.1:5182",
    "http://localhost:5190",
    "http://127.0.0.1:5190",
)


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

class Config:
    # ───────── Flask / Core ─────────
    SECRET_KEY = _env_str_first(
        (
            "SECRET_KEY",
            "VECTOPLAN_APP_SECRET_KEY",
        ),
        "dev-vectoplan-app-secret-change-me",
    )

    SQLALCHEMY_DATABASE_URI = _env_first(
        (
            "DATABASE_URL",
            "SQLALCHEMY_DATABASE_URI",
            "VECTOPLAN_APP_DATABASE_URL",
        ),
        None,
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    FLASK_ENV = _env_str_first(("FLASK_ENV", "ENV", "VECTOPLAN_APP_FLASK_ENV"), "prod")

    # ───────── Server ─────────
    HOST = _env_str_first(("HOST", "VECTOPLAN_APP_HOST"), "0.0.0.0")
    PORT = _as_int(_env_first(("PORT", "VECTOPLAN_APP_INTERNAL_PORT"), "8000"), 8000)
    MEDIA_ROOT = _env_str_first(("MEDIA_ROOT", "VECTOPLAN_APP_MEDIA_ROOT"), "/app/media")
    MAX_CONTENT_LENGTH = _as_int(
        _env_first(("MAX_CONTENT_LENGTH", "VECTOPLAN_APP_MAX_CONTENT_LENGTH"), None),
        512 * 1024 * 1024,
    )

    # Browser-facing app origin. This is the parent origin for Editor/OpenLayer/Auth iframes.
    VECTOPLAN_APP_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_APP_PUBLIC_URL",
                "VECTOPLAN_APP_PUBLIC_BASE_URL",
                "APP_PUBLIC_URL",
                "PUBLIC_APP_URL",
            ),
            _DEFAULT_APP_PUBLIC_URL,
        ),
        _DEFAULT_APP_PUBLIC_URL,
    )
    VECTOPLAN_APP_PUBLIC_BASE_URL = VECTOPLAN_APP_PUBLIC_URL
    APP_PUBLIC_URL = VECTOPLAN_APP_PUBLIC_URL

    # Internal base URL of this app service for server-to-server calls.
    WEB_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "WEB_INTERNAL_URL",
                "VECTOPLAN_APP_INTERNAL_URL",
            ),
            "http://vectoplan-app:8000",
        ),
        "http://vectoplan-app:8000",
    )

    VECTOPLAN_APP_INTERNAL_URL = WEB_INTERNAL_URL

    # ───────── Auth service integration ─────────
    # vectoplan-auth is the canonical source of truth for:
    # - authenticated / guest / stale / blocked
    # - user.id
    # - roles
    # - account
    # - plan
    # - entitlements
    # - API-key context
    #
    # Browser/public URL is used for links and optional iframe/overlay login.
    # Internal/base URL is used for server-to-server calls from vectoplan-app.
    VECTOPLAN_AUTH_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_PUBLIC_URL",
                "VECTOPLAN_AUTH_PUBLIC_BASE_URL",
                "AUTH_PUBLIC_URL",
                "AUTH_PUBLIC_BASE_URL",
            ),
            _DEFAULT_AUTH_PUBLIC_URL,
        ),
        _DEFAULT_AUTH_PUBLIC_URL,
    )
    VECTOPLAN_AUTH_PUBLIC_BASE_URL = VECTOPLAN_AUTH_PUBLIC_URL
    AUTH_PUBLIC_URL = VECTOPLAN_AUTH_PUBLIC_URL
    AUTH_PUBLIC_BASE_URL = VECTOPLAN_AUTH_PUBLIC_URL
    VECTOPLAN_AUTH_SESSION_COOKIE_NAME = _env_str_first(
        (
            "VECTOPLAN_AUTH_SESSION_COOKIE_NAME",
            "AUTH_SESSION_COOKIE_NAME",
        ),
        "vectoplan_auth_session",
    )

    VECTOPLAN_AUTH_BASE_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_BASE_URL",
                "VECTOPLAN_AUTH_INTERNAL_URL",
                "VECTOPLAN_AUTH_INTERNAL_BASE_URL",
                "AUTH_BASE_URL",
                "AUTH_INTERNAL_URL",
                "AUTH_INTERNAL_BASE_URL",
            ),
            _DEFAULT_AUTH_INTERNAL_URL,
        ),
        _DEFAULT_AUTH_INTERNAL_URL,
    )
    VECTOPLAN_AUTH_INTERNAL_URL = VECTOPLAN_AUTH_BASE_URL
    VECTOPLAN_AUTH_INTERNAL_BASE_URL = VECTOPLAN_AUTH_BASE_URL
    AUTH_INTERNAL_URL = VECTOPLAN_AUTH_BASE_URL
    AUTH_INTERNAL_BASE_URL = VECTOPLAN_AUTH_BASE_URL

    VECTOPLAN_AUTH_SERVICE_NAME = _env_str_first(
        (
            "VECTOPLAN_AUTH_SERVICE_NAME",
            "AUTH_SERVICE_NAME",
        ),
        "vectoplan-app",
    )

    VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS",
                    "VECTOPLAN_AUTH_TIMEOUT_SECONDS",
                    "AUTH_REQUEST_TIMEOUT_SECONDS",
                    "AUTH_TIMEOUT_SECONDS",
                ),
                None,
            ),
            3.0,
        ),
        0.2,
        30.0,
    )

    VECTOPLAN_AUTH_VERIFY_TLS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_AUTH_VERIFY_TLS",
                "AUTH_VERIFY_TLS",
            ),
            None,
        ),
        True,
    )

    # Request-local auth-context cache remains handled by services/auth_context.py.
    # This value only enables optional very short process cache in auth_context_client.py.
    # Default 0 means: no process-global auth-state cache.
    VECTOPLAN_AUTH_CONTEXT_CACHE_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_AUTH_CONTEXT_CACHE_SECONDS",
                    "AUTH_CONTEXT_CACHE_SECONDS",
                ),
                None,
            ),
            0.0,
        ),
        0.0,
        60.0,
    )

    VECTOPLAN_AUTH_API_KEY_VERIFY_CACHE_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_AUTH_API_KEY_VERIFY_CACHE_SECONDS",
                    "AUTH_API_KEY_VERIFY_CACHE_SECONDS",
                ),
                None,
            ),
            30.0,
        ),
        0.0,
        60.0,
    )

    # Protected resources fail closed if auth is unavailable.
    # Public/unlisted project routes may decide explicitly at route level.
    VECTOPLAN_AUTH_FAIL_OPEN_FOR_PUBLIC_ROUTES = _as_bool(
        _env_first(
            (
                "VECTOPLAN_AUTH_FAIL_OPEN_FOR_PUBLIC_ROUTES",
                "AUTH_FAIL_OPEN_FOR_PUBLIC_ROUTES",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_AUTH_DEBUG_CLIENT_ERRORS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_AUTH_DEBUG_CLIENT_ERRORS",
                "AUTH_DEBUG_CLIENT_ERRORS",
            ),
            None,
        ),
        False,
    )

    # Route paths used by auth_context_client.py and UI links.
    VECTOPLAN_AUTH_ROUTE = _norm_path(
        _env_str_first(("VECTOPLAN_AUTH_ROUTE", "AUTH_ROUTE"), _DEFAULT_AUTH_ROUTE),
        _DEFAULT_AUTH_ROUTE,
    )
    VECTOPLAN_AUTH_ME_PATH = _norm_path(
        _env_str_first(("VECTOPLAN_AUTH_ME_PATH", "AUTH_ME_PATH"), _DEFAULT_AUTH_ME_PATH),
        _DEFAULT_AUTH_ME_PATH,
    )
    VECTOPLAN_AUTH_CONTEXT_PATH = _norm_path(
        _env_str_first(("VECTOPLAN_AUTH_CONTEXT_PATH", "AUTH_CONTEXT_PATH"), _DEFAULT_AUTH_CONTEXT_PATH),
        _DEFAULT_AUTH_CONTEXT_PATH,
    )
    VECTOPLAN_AUTH_CONTEXT_MINIMAL_PATH = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_CONTEXT_MINIMAL_PATH",
                "AUTH_CONTEXT_MINIMAL_PATH",
            ),
            _DEFAULT_AUTH_CONTEXT_MINIMAL_PATH,
        ),
        _DEFAULT_AUTH_CONTEXT_MINIMAL_PATH,
    )
    VECTOPLAN_AUTH_ACCOUNT_DASHBOARD_PATH = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_ACCOUNT_DASHBOARD_PATH",
                "AUTH_ACCOUNT_DASHBOARD_PATH",
            ),
            _DEFAULT_AUTH_ACCOUNT_DASHBOARD_PATH,
        ),
        _DEFAULT_AUTH_ACCOUNT_DASHBOARD_PATH,
    )
    VECTOPLAN_AUTH_ADMIN_DASHBOARD_PATH = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_ADMIN_DASHBOARD_PATH",
                "AUTH_ADMIN_DASHBOARD_PATH",
            ),
            _DEFAULT_AUTH_ADMIN_DASHBOARD_PATH,
        ),
        _DEFAULT_AUTH_ADMIN_DASHBOARD_PATH,
    )
    VECTOPLAN_AUTH_LOGOUT_PATH = _norm_path(
        _env_str_first(("VECTOPLAN_AUTH_LOGOUT_PATH", "AUTH_LOGOUT_PATH"), _DEFAULT_AUTH_LOGOUT_PATH),
        _DEFAULT_AUTH_LOGOUT_PATH,
    )
    VECTOPLAN_AUTH_HEALTH_READY_PATH = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_HEALTH_READY_PATH",
                "AUTH_HEALTH_READY_PATH",
            ),
            _DEFAULT_AUTH_HEALTH_READY_PATH,
        ),
        _DEFAULT_AUTH_HEALTH_READY_PATH,
    )
    VECTOPLAN_AUTH_HEALTH_LIVE_PATH = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_AUTH_HEALTH_LIVE_PATH",
                "AUTH_HEALTH_LIVE_PATH",
            ),
            _DEFAULT_AUTH_HEALTH_LIVE_PATH,
        ),
        _DEFAULT_AUTH_HEALTH_LIVE_PATH,
    )

    VECTOPLAN_AUTH_LOGIN_URL = _join_url(
        VECTOPLAN_AUTH_PUBLIC_URL,
        VECTOPLAN_AUTH_ROUTE,
        f"{_DEFAULT_AUTH_PUBLIC_URL}{_DEFAULT_AUTH_ROUTE}",
    )
    VECTOPLAN_AUTH_REGISTER_URL = f"{VECTOPLAN_AUTH_LOGIN_URL}?mode=register"
    VECTOPLAN_AUTH_LOGOUT_URL = _join_url(
        VECTOPLAN_AUTH_PUBLIC_URL,
        VECTOPLAN_AUTH_LOGOUT_PATH,
        f"{_DEFAULT_AUTH_PUBLIC_URL}{_DEFAULT_AUTH_LOGOUT_PATH}",
    )
    VECTOPLAN_AUTH_ACCOUNT_DASHBOARD_URL = _join_url(
        VECTOPLAN_AUTH_PUBLIC_URL,
        VECTOPLAN_AUTH_ACCOUNT_DASHBOARD_PATH,
        f"{_DEFAULT_AUTH_PUBLIC_URL}{_DEFAULT_AUTH_ACCOUNT_DASHBOARD_PATH}",
    )
    VECTOPLAN_AUTH_ADMIN_DASHBOARD_URL = _join_url(
        VECTOPLAN_AUTH_PUBLIC_URL,
        VECTOPLAN_AUTH_ADMIN_DASHBOARD_PATH,
        f"{_DEFAULT_AUTH_PUBLIC_URL}{_DEFAULT_AUTH_ADMIN_DASHBOARD_PATH}",
    )

    AUTH_LOGIN_URL = VECTOPLAN_AUTH_LOGIN_URL
    AUTH_REGISTER_URL = VECTOPLAN_AUTH_REGISTER_URL
    AUTH_LOGOUT_URL = VECTOPLAN_AUTH_LOGOUT_URL
    AUTH_ACCOUNT_DASHBOARD_URL = VECTOPLAN_AUTH_ACCOUNT_DASHBOARD_URL
    AUTH_ADMIN_DASHBOARD_URL = VECTOPLAN_AUTH_ADMIN_DASHBOARD_URL

    # Trusted header mode is intentionally off by default.
    # If a future reverse proxy/gateway verifies auth centrally, public incoming
    # X-VECTOPLAN-* headers must be stripped before trusting gateway-set headers.
    VECTOPLAN_AUTH_TRUSTED_GATEWAY_HEADERS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_AUTH_TRUSTED_GATEWAY_HEADERS",
                "AUTH_TRUSTED_GATEWAY_HEADERS",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_AUTH_STRIP_INCOMING_PLATFORM_HEADERS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_AUTH_STRIP_INCOMING_PLATFORM_HEADERS",
                "AUTH_STRIP_INCOMING_PLATFORM_HEADERS",
            ),
            None,
        ),
        True,
    )

    # ───────── Guest/Demo project integration ─────────
    # Guest-Demo is allowed only when vectoplan-auth returns:
    # authenticated=false and demo_project_access entitlement.
    VECTOPLAN_DEMO_PROJECTS_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECTS_ENABLED",
                "DEMO_PROJECTS_ENABLED",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_DEMO_PROJECT_TTL_SECONDS = _clamp_int(
        _as_int(
            _env_first(
                (
                    "VECTOPLAN_DEMO_PROJECT_TTL_SECONDS",
                    "DEMO_PROJECT_TTL_SECONDS",
                    "VECTOPLAN_GUEST_DEMO_TTL_SECONDS",
                    "GUEST_DEMO_TTL_SECONDS",
                ),
                None,
            ),
            3600,
        ),
        60,
        24 * 60 * 60,
    )

    VECTOPLAN_DEMO_PROJECT_CLEANUP_ON_ENSURE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_CLEANUP_ON_ENSURE",
                "DEMO_PROJECT_CLEANUP_ON_ENSURE",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_DEMO_PROJECT_CHUNK_PROVISIONING = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_CHUNK_PROVISIONING",
                "DEMO_PROJECT_CHUNK_PROVISIONING",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_DEMO_PROJECT_NAME = _env_str_first(
        (
            "VECTOPLAN_DEMO_PROJECT_NAME",
            "DEMO_PROJECT_NAME",
        ),
        "Demo-Projekt",
    )

    VECTOPLAN_DEMO_PROJECT_DESCRIPTION = _env_str_first(
        (
            "VECTOPLAN_DEMO_PROJECT_DESCRIPTION",
            "DEMO_PROJECT_DESCRIPTION",
        ),
        "Temporäres Demo-Projekt. Änderungen werden nicht dauerhaft gespeichert.",
    )

    VECTOPLAN_DEMO_PROJECT_ADDRESS_TEXT = _env_str_first(
        (
            "VECTOPLAN_DEMO_PROJECT_ADDRESS_TEXT",
            "DEMO_PROJECT_ADDRESS_TEXT",
        ),
        "Demo-Adresse",
    )

    VECTOPLAN_DEMO_PROJECT_VISIBILITY = _env_str_first(
        (
            "VECTOPLAN_DEMO_PROJECT_VISIBILITY",
            "DEMO_PROJECT_VISIBILITY",
        ),
        "private",
    )

    VECTOPLAN_DEMO_PROJECT_ALLOW_PUBLICATION = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_ALLOW_PUBLICATION",
                "DEMO_PROJECT_ALLOW_PUBLICATION",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_DEMO_PROJECT_ALLOW_TEAM = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_ALLOW_TEAM",
                "DEMO_PROJECT_ALLOW_TEAM",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_DEMO_PROJECT_ALLOW_INVITATIONS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_ALLOW_INVITATIONS",
                "DEMO_PROJECT_ALLOW_INVITATIONS",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_DEMO_PROJECT_ALLOW_ADMIN = _as_bool(
        _env_first(
            (
                "VECTOPLAN_DEMO_PROJECT_ALLOW_ADMIN",
                "DEMO_PROJECT_ALLOW_ADMIN",
            ),
            None,
        ),
        False,
    )

    DEMO_PROJECTS_ENABLED = VECTOPLAN_DEMO_PROJECTS_ENABLED
    DEMO_PROJECT_TTL_SECONDS = VECTOPLAN_DEMO_PROJECT_TTL_SECONDS

    # ───────── Interne Services ─────────
    CHATAI_URL = _env_str("CHATAI_URL", "http://chatai:8001/chat")
    FPA_URL = _env_str("FPA_URL", "http://fpa:8080")
    DAA_URL = _env_str("DAA_URL", "http://daa:8000")
    BGA_URL = _env_str("BGA_URL", "http://bga:4903")
    BPA_URL = _env_str("BPA_URL", "http://bpa:5744")
    CITY_URL = _env_str("CITY_URL", "http://cityloader:8000")
    DATALOADER_URL = _env("DATALOADER_URL")

    IDENTITY_INTERNAL_URL = _norm_url(_env_str("IDENTITY_INTERNAL_URL", "http://identity:9000"), "http://identity:9000")
    REGISTRY_INTERNAL_URL = _norm_url(_env_str("REGISTRY_INTERNAL_URL", "http://registry:8000"), "http://registry:8000")

    # ───────── Editor iframe integration ─────────
    # Browser-facing editor URL.
    # Used by /ui/chat/<chat_id>/editor as iframe redirect target.
    VECTOPLAN_EDITOR_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_EDITOR_PUBLIC_URL",
                "VECTOPLAN_EDITOR_PUBLIC_BASE_URL",
                "EDITOR_PUBLIC_URL",
                "EDITOR_PUBLIC_BASE_URL",
            ),
            _DEFAULT_EDITOR_PUBLIC_URL,
        ),
        _DEFAULT_EDITOR_PUBLIC_URL,
    )

    VECTOPLAN_EDITOR_PUBLIC_BASE_URL = VECTOPLAN_EDITOR_PUBLIC_URL

    # Internal editor URL.
    # Kept for server-to-server diagnostics only.
    # The browser iframe must not receive this value.
    VECTOPLAN_EDITOR_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_EDITOR_INTERNAL_URL",
                "EDITOR_INTERNAL_URL",
            ),
            _DEFAULT_EDITOR_INTERNAL_URL,
        ),
        _DEFAULT_EDITOR_INTERNAL_URL,
    )

    VECTOPLAN_EDITOR_ROUTE = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_EDITOR_ROUTE",
                "EDITOR_ROUTE",
            ),
            _DEFAULT_EDITOR_ROUTE,
        ),
        _DEFAULT_EDITOR_ROUTE,
    )

    VECTOPLAN_EDITOR_EMBED_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_EDITOR_EMBED_ENABLED",
                "EDITOR_EMBED_ENABLED",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_EDITOR_IFRAME_URL = _join_url(
        VECTOPLAN_EDITOR_PUBLIC_URL,
        VECTOPLAN_EDITOR_ROUTE,
        f"{_DEFAULT_EDITOR_PUBLIC_URL}{_DEFAULT_EDITOR_ROUTE}",
    )

    # Generic aliases supported by routes/ui/editor.py and future shared helpers.
    EDITOR_PUBLIC_URL = VECTOPLAN_EDITOR_PUBLIC_URL
    EDITOR_PUBLIC_BASE_URL = VECTOPLAN_EDITOR_PUBLIC_BASE_URL
    EDITOR_INTERNAL_URL = VECTOPLAN_EDITOR_INTERNAL_URL
    EDITOR_ROUTE = VECTOPLAN_EDITOR_ROUTE
    EDITOR_IFRAME_URL = VECTOPLAN_EDITOR_IFRAME_URL
    EDITOR_EMBED_ENABLED = VECTOPLAN_EDITOR_EMBED_ENABLED

    # Stateless CAD microservice iframe integration.
    VECTOPLAN_CAD_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CAD_PUBLIC_URL",
                "VECTOPLAN_CAD_PUBLIC_BASE_URL",
                "CAD_PUBLIC_URL",
            ),
            _DEFAULT_CAD_PUBLIC_URL,
        ),
        _DEFAULT_CAD_PUBLIC_URL,
    )
    VECTOPLAN_CAD_PUBLIC_BASE_URL = VECTOPLAN_CAD_PUBLIC_URL
    VECTOPLAN_CAD_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CAD_INTERNAL_URL",
                "CAD_INTERNAL_URL",
            ),
            _DEFAULT_CAD_INTERNAL_URL,
        ),
        _DEFAULT_CAD_INTERNAL_URL,
    )
    VECTOPLAN_CAD_ROUTE = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_CAD_ROUTE",
                "VECTOPLAN_CAD_EMBED_ROUTE",
                "CAD_ROUTE",
            ),
            _DEFAULT_CAD_ROUTE,
        ),
        _DEFAULT_CAD_ROUTE,
    )
    VECTOPLAN_CAD_EMBED_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_CAD_EMBED_ENABLED",
                "CAD_EMBED_ENABLED",
            ),
            None,
        ),
        True,
    )
    VECTOPLAN_CAD_IFRAME_URL = _join_url(
        VECTOPLAN_CAD_PUBLIC_URL,
        VECTOPLAN_CAD_ROUTE,
        f"{_DEFAULT_CAD_PUBLIC_URL}{_DEFAULT_CAD_ROUTE}",
    )
    CAD_PUBLIC_URL = VECTOPLAN_CAD_PUBLIC_URL
    CAD_PUBLIC_BASE_URL = VECTOPLAN_CAD_PUBLIC_BASE_URL
    CAD_INTERNAL_URL = VECTOPLAN_CAD_INTERNAL_URL
    CAD_ROUTE = VECTOPLAN_CAD_ROUTE
    CAD_IFRAME_URL = VECTOPLAN_CAD_IFRAME_URL
    CAD_EMBED_ENABLED = VECTOPLAN_CAD_EMBED_ENABLED

    # Initial LV microservice iframe integration. The App only forwards the
    # public project key; LV domain data remains owned by vectoplan-lv.
    VECTOPLAN_LV_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LV_PUBLIC_URL",
                "VECTOPLAN_LV_PUBLIC_BASE_URL",
                "LV_PUBLIC_URL",
            ),
            _DEFAULT_LV_PUBLIC_URL,
        ),
        _DEFAULT_LV_PUBLIC_URL,
    )
    VECTOPLAN_LV_PUBLIC_BASE_URL = VECTOPLAN_LV_PUBLIC_URL
    VECTOPLAN_LV_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LV_INTERNAL_URL",
                "LV_INTERNAL_URL",
            ),
            _DEFAULT_LV_INTERNAL_URL,
        ),
        _DEFAULT_LV_INTERNAL_URL,
    )
    VECTOPLAN_LV_ROUTE = _norm_path(
        _env_str_first(
            (
                "VECTOPLAN_LV_ROUTE",
                "VECTOPLAN_LV_EMBED_ROUTE",
                "LV_ROUTE",
            ),
            _DEFAULT_LV_ROUTE,
        ),
        _DEFAULT_LV_ROUTE,
    )
    VECTOPLAN_LV_EMBED_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_LV_EMBED_ENABLED",
                "LV_EMBED_ENABLED",
            ),
            None,
        ),
        True,
    )
    VECTOPLAN_LV_IFRAME_URL = _join_url(
        VECTOPLAN_LV_PUBLIC_URL,
        VECTOPLAN_LV_ROUTE,
        f"{_DEFAULT_LV_PUBLIC_URL}{_DEFAULT_LV_ROUTE}",
    )
    LV_PUBLIC_URL = VECTOPLAN_LV_PUBLIC_URL
    LV_PUBLIC_BASE_URL = VECTOPLAN_LV_PUBLIC_BASE_URL
    LV_INTERNAL_URL = VECTOPLAN_LV_INTERNAL_URL
    LV_ROUTE = VECTOPLAN_LV_ROUTE
    LV_IFRAME_URL = VECTOPLAN_LV_IFRAME_URL
    LV_EMBED_ENABLED = VECTOPLAN_LV_EMBED_ENABLED

    # Project-scoped Filecloud microservice. Browser embedding uses a short-lived
    # signed access ticket which Filecloud exchanges for its own scoped session.
    # This avoids attempting to render the central login page in a nested iframe.
    VECTOPLAN_FILECLOUD_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_FILECLOUD_PUBLIC_URL",
                "VECTOPLAN_FILECLOUD_PUBLIC_BASE_URL",
                "FILECLOUD_PUBLIC_URL",
            ),
            _DEFAULT_FILECLOUD_PUBLIC_URL,
        ),
        _DEFAULT_FILECLOUD_PUBLIC_URL,
    )
    VECTOPLAN_FILECLOUD_PUBLIC_BASE_URL = VECTOPLAN_FILECLOUD_PUBLIC_URL
    VECTOPLAN_FILECLOUD_INTERNAL_URL = _norm_url(
        _env_str_first(
            ("VECTOPLAN_FILECLOUD_INTERNAL_URL", "FILECLOUD_INTERNAL_URL"),
            _DEFAULT_FILECLOUD_INTERNAL_URL,
        ),
        _DEFAULT_FILECLOUD_INTERNAL_URL,
    )
    VECTOPLAN_FILECLOUD_ROUTE = _norm_path(
        _env_str_first(("VECTOPLAN_FILECLOUD_ROUTE", "FILECLOUD_ROUTE"), _DEFAULT_FILECLOUD_ROUTE),
        _DEFAULT_FILECLOUD_ROUTE,
    )
    VECTOPLAN_FILECLOUD_EMBED_ENABLED = _as_bool(
        _env_first(("VECTOPLAN_FILECLOUD_EMBED_ENABLED", "FILECLOUD_EMBED_ENABLED"), None),
        True,
    )
    VECTOPLAN_FILECLOUD_ACCESS_TICKET_SECRET = _env_str(
        "VECTOPLAN_FILECLOUD_ACCESS_TICKET_SECRET",
        "",
    )
    VECTOPLAN_FILECLOUD_ACCESS_TICKET_TTL_SECONDS = _clamp_int(
        _as_int(_env("VECTOPLAN_FILECLOUD_ACCESS_TICKET_TTL_SECONDS"), 300),
        30,
        900,
    )
    FILECLOUD_PUBLIC_URL = VECTOPLAN_FILECLOUD_PUBLIC_URL
    FILECLOUD_INTERNAL_URL = VECTOPLAN_FILECLOUD_INTERNAL_URL
    FILECLOUD_ROUTE = VECTOPLAN_FILECLOUD_ROUTE
    # ───────── OpenLayer microservice iframe integration ─────────
    # Browser-facing OpenLayer URL.
    # This must point to the published host port, not the internal container port.
    OPENLAYER_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "OPENLAYER_PUBLIC_URL",
                "OPENLAYER_PUBLIC_BASE_URL",
                "VECTOPLAN_OPENLAYER_PUBLIC_URL",
                "VECTOPLAN_OPENLAYER_PUBLIC_BASE_URL",
            ),
            _DEFAULT_OPENLAYER_PUBLIC_URL,
        ),
        _DEFAULT_OPENLAYER_PUBLIC_URL,
    )

    OPENLAYER_PUBLIC_BASE_URL = OPENLAYER_PUBLIC_URL
    VECTOPLAN_OPENLAYER_PUBLIC_URL = OPENLAYER_PUBLIC_URL
    VECTOPLAN_OPENLAYER_PUBLIC_BASE_URL = OPENLAYER_PUBLIC_URL

    # Internal OpenLayer URL.
    # This remains Docker-internal and must not be used as a browser redirect target.
    OPENLAYER_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "OPENLAYER_INTERNAL_URL",
                "VECTOPLAN_OPENLAYER_INTERNAL_URL",
            ),
            _DEFAULT_OPENLAYER_INTERNAL_URL,
        ),
        _DEFAULT_OPENLAYER_INTERNAL_URL,
    )

    VECTOPLAN_OPENLAYER_INTERNAL_URL = OPENLAYER_INTERNAL_URL

    OPENLAYER_ROUTE = _norm_path(
        _env_str_first(
            (
                "OPENLAYER_ROUTE",
                "VECTOPLAN_OPENLAYER_ROUTE",
                "MAP_ROUTE",
            ),
            _DEFAULT_OPENLAYER_ROUTE,
        ),
        _DEFAULT_OPENLAYER_ROUTE,
    )

    OPENLAYER_EMBED_ENABLED = _as_bool(
        _env_first(
            (
                "OPENLAYER_EMBED_ENABLED",
                "VECTOPLAN_OPENLAYER_EMBED_ENABLED",
                "MAP_EMBED_ENABLED",
            ),
            None,
        ),
        True,
    )

    OPENLAYER_IFRAME_URL = _join_url(
        OPENLAYER_PUBLIC_URL,
        OPENLAYER_ROUTE,
        f"{_DEFAULT_OPENLAYER_PUBLIC_URL}{_DEFAULT_OPENLAYER_ROUTE}",
    )

    # Backward-compatible aliases.
    # OPENLAYER_BASE_URL intentionally points to the browser-facing URL because
    # legacy UI routes used it for iframe construction.
    OPENLAYER_BASE_URL = OPENLAYER_PUBLIC_URL
    OPENLAYER_PUBLIC_BASE = OPENLAYER_PUBLIC_URL
    OPENLAYER_INTERNAL_BASE_URL = OPENLAYER_INTERNAL_URL
    MAP_PUBLIC_URL = OPENLAYER_PUBLIC_URL
    MAP_PUBLIC_BASE_URL = OPENLAYER_PUBLIC_URL
    MAP_INTERNAL_URL = OPENLAYER_INTERNAL_URL
    MAP_ROUTE = OPENLAYER_ROUTE
    MAP_IFRAME_URL = OPENLAYER_IFRAME_URL

    # ───────── Chunk / Library service references ─────────
    # Chunk PUBLIC URL is browser-facing only.
    # Chunk INTERNAL URL is the only valid backend URL for project provisioning.
    VECTOPLAN_CHUNK_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CHUNK_PUBLIC_URL",
                "VECTOPLAN_CHUNK_PUBLIC_BASE_URL",
                "CHUNK_PUBLIC_URL",
                "CHUNK_PUBLIC_BASE_URL",
            ),
            _DEFAULT_CHUNK_PUBLIC_URL,
        ),
        _DEFAULT_CHUNK_PUBLIC_URL,
    )

    VECTOPLAN_CHUNK_PUBLIC_BASE_URL = VECTOPLAN_CHUNK_PUBLIC_URL
    CHUNK_PUBLIC_URL = VECTOPLAN_CHUNK_PUBLIC_URL
    CHUNK_PUBLIC_BASE_URL = VECTOPLAN_CHUNK_PUBLIC_BASE_URL

    VECTOPLAN_CHUNK_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CHUNK_INTERNAL_URL",
                "VECTOPLAN_CHUNK_INTERNAL_BASE_URL",
                "CHUNK_INTERNAL_URL",
                "CHUNK_INTERNAL_BASE_URL",
            ),
            _DEFAULT_CHUNK_INTERNAL_URL,
        ),
        _DEFAULT_CHUNK_INTERNAL_URL,
    )

    VECTOPLAN_CHUNK_INTERNAL_BASE_URL = VECTOPLAN_CHUNK_INTERNAL_URL
    CHUNK_INTERNAL_URL = VECTOPLAN_CHUNK_INTERNAL_URL
    CHUNK_INTERNAL_BASE_URL = VECTOPLAN_CHUNK_INTERNAL_BASE_URL

    # Server-side App -> Core project provisioning. Core is the translation
    # boundary between CAD and Chunk and owns a separate database.
    VECTOPLAN_CORE_INTERNAL_URL = _norm_url(
        _env_str_first(("VECTOPLAN_CORE_INTERNAL_URL", "CORE_INTERNAL_URL"), _DEFAULT_CORE_INTERNAL_URL),
        _DEFAULT_CORE_INTERNAL_URL,
    )
    VECTOPLAN_APP_CORE_PROVISION_ON_PROJECT_CREATE = _as_bool(
        _env_first(("VECTOPLAN_APP_CORE_PROVISION_ON_PROJECT_CREATE",), None), True
    )
    VECTOPLAN_APP_CORE_PROVISIONING_REQUIRED = _as_bool(
        _env_first(("VECTOPLAN_APP_CORE_PROVISIONING_REQUIRED",), None), False
    )
    VECTOPLAN_APP_CORE_TIMEOUT_SECONDS = _clamp_float(
        _as_float(_env_first(("VECTOPLAN_APP_CORE_TIMEOUT_SECONDS",), None), 10.0), 0.1, 120.0
    )
    VECTOPLAN_APP_CORE_SERVICE_API_KEY = _env_str_first(
        ("VECTOPLAN_APP_CORE_SERVICE_API_KEY", "VECTOPLAN_CORE_INTERNAL_API_KEY"), ""
    )

    # Server-side app -> chunk provisioning.
    # project_service.py will call services/chunk_client.py after the app Project
    # has a stable public id. The app DB remains the app truth. The chunk DB
    # remains the chunk-world truth.
    VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE",
                "CHUNK_PROVISION_ON_PROJECT_CREATE",
            ),
            None,
        ),
        True,
    )

    # Development default is intentionally soft:
    # the App project can still be created if the Chunk service is temporarily
    # unavailable. project_service.py will store pending/error state and allow retry.
    VECTOPLAN_CHUNK_PROVISION_REQUIRED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_CHUNK_PROVISION_REQUIRED",
                "CHUNK_PROVISION_REQUIRED",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS",
                    "CHUNK_PROVISION_TIMEOUT_SECONDS",
                    "VECTOPLAN_CHUNK_REQUEST_TIMEOUT",
                ),
                None,
            ),
            10.0,
        ),
        0.1,
        120.0,
    )

    VECTOPLAN_CHUNK_PROVISION_RETRIES = _clamp_int(
        _as_int(
            _env_first(
                (
                    "VECTOPLAN_CHUNK_PROVISION_RETRIES",
                    "CHUNK_PROVISION_RETRIES",
                    "VECTOPLAN_CHUNK_REQUEST_RETRIES",
                ),
                None,
            ),
            2,
        ),
        0,
        10,
    )

    VECTOPLAN_CHUNK_PROVISION_RETRY_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_CHUNK_PROVISION_RETRY_SECONDS",
                    "CHUNK_PROVISION_RETRY_SECONDS",
                    "VECTOPLAN_CHUNK_RETRY_SECONDS",
                ),
                None,
            ),
            1.0,
        ),
        0.0,
        60.0,
    )

    VECTOPLAN_CHUNK_MAX_RESPONSE_BYTES = _clamp_int(
        _as_int(
            _env_first(
                (
                    "VECTOPLAN_CHUNK_MAX_RESPONSE_BYTES",
                    "CHUNK_MAX_RESPONSE_BYTES",
                ),
                None,
            ),
            8 * 1024 * 1024,
        ),
        1024,
        64 * 1024 * 1024,
    )

    VECTOPLAN_CHUNK_CLIENT_USER_AGENT = _env_str_first(
        (
            "VECTOPLAN_CHUNK_CLIENT_USER_AGENT",
            "CHUNK_CLIENT_USER_AGENT",
        ),
        "vectoplan-app/chunk-client",
    )

    # Service identity for App -> Chunk calls. The raw credential must never be
    # serialized into browser responses, logs or status payloads.
    VECTOPLAN_APP_CHUNK_SERVICE_ID = _env_str_first(
        (
            "VECTOPLAN_APP_CHUNK_SERVICE_ID",
            "VECTOPLAN_CHUNK_SERVICE_ID",
            "CHUNK_SERVICE_ID",
        ),
        "vectoplan-app",
    )

    VECTOPLAN_APP_CHUNK_SERVICE_API_KEY = _env_str_first(
        (
            "VECTOPLAN_APP_CHUNK_SERVICE_API_KEY",
            "VECTOPLAN_CHUNK_SERVICE_API_KEY",
            "VECTOPLAN_CHUNK_INTERNAL_TOKEN",
            "VECTOPLAN_CHUNK_API_TOKEN",
            "CHUNK_SERVICE_API_KEY",
            "CHUNK_INTERNAL_TOKEN",
            "CHUNK_API_TOKEN",
        ),
        "",
    )

    # Backward-compatible aliases used by the existing chunk client.
    VECTOPLAN_CHUNK_SERVICE_ID = VECTOPLAN_APP_CHUNK_SERVICE_ID
    VECTOPLAN_CHUNK_SERVICE_API_KEY = VECTOPLAN_APP_CHUNK_SERVICE_API_KEY
    VECTOPLAN_CHUNK_INTERNAL_TOKEN = VECTOPLAN_APP_CHUNK_SERVICE_API_KEY
    VECTOPLAN_CHUNK_API_TOKEN = VECTOPLAN_APP_CHUNK_SERVICE_API_KEY

    # ───────── App project world / georeference policy ─────────
    VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE = _as_world_template(
        _env_first(
            (
                "VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE",
                "VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
                "VECTOPLAN_CHUNK_PROJECT_PROVISIONING_DEFAULT_TEMPLATE_ID",
                "CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
            ),
            _DEFAULT_APP_WORLD_TEMPLATE,
        ),
        _DEFAULT_APP_WORLD_TEMPLATE,
    )

    VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE = _as_fallback_world_template(
        _env_first(
            (
                "VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE",
                "VECTOPLAN_CHUNK_PROVISION_FALLBACK_TEMPLATE_ID",
                "CHUNK_PROVISION_FALLBACK_TEMPLATE_ID",
            ),
            _DEFAULT_APP_FALLBACK_WORLD_TEMPLATE,
        ),
        VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE,
        _DEFAULT_APP_FALLBACK_WORLD_TEMPLATE,
    )

    VECTOPLAN_APP_ALLOW_WORLD_FALLBACK = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_ALLOW_WORLD_FALLBACK",
                "VECTOPLAN_CHUNK_ALLOW_WORLD_FALLBACK",
                "CHUNK_ALLOW_WORLD_FALLBACK",
            ),
            None,
        ),
        True,
    )

    # Client-side fallback is limited to the explicit business error-code list.
    # Timeouts, DNS failures, HTTP 5xx and database failures are never converted
    # silently into Flat projects.
    VECTOPLAN_APP_CLIENT_SIDE_WORLD_FALLBACK = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_CLIENT_SIDE_WORLD_FALLBACK",
                "VECTOPLAN_CHUNK_CLIENT_SIDE_WORLD_FALLBACK",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_APP_ALLOW_WORLD_TEMPLATE_CHANGE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_ALLOW_WORLD_TEMPLATE_CHANGE",
                "VECTOPLAN_CHUNK_ALLOW_WORLD_TEMPLATE_CHANGE",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_EARTH_CRS_ID = _env_str_first(
        (
            "VECTOPLAN_APP_EARTH_CRS_ID",
            "VECTOPLAN_CHUNK_EARTH_CRS_ID",
            "EARTH_CRS_ID",
        ),
        _DEFAULT_APP_EARTH_CRS_ID,
    ).strip().upper() or _DEFAULT_APP_EARTH_CRS_ID

    VECTOPLAN_APP_PROJECT_COORDINATE_CRS_ID = _env_str_first(
        (
            "VECTOPLAN_APP_PROJECT_COORDINATE_CRS_ID",
            "VECTOPLAN_APP_DEFAULT_PROJECT_CRS_ID",
            "PROJECT_COORDINATE_CRS_ID",
        ),
        _DEFAULT_APP_PROJECT_COORDINATE_CRS_ID,
    ).strip().upper() or _DEFAULT_APP_PROJECT_COORDINATE_CRS_ID

    VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT",
                    "VECTOPLAN_CHUNK_DEFAULT_EARTH_HEIGHT",
                    "DEFAULT_EARTH_HEIGHT",
                ),
                None,
            ),
            _DEFAULT_APP_EARTH_HEIGHT,
        ),
        -100_000.0,
        1_000_000.0,
    )

    VECTOPLAN_APP_CHUNK_FALLBACK_ERROR_CODES = _as_code_list(
        _env_first(
            (
                "VECTOPLAN_APP_CHUNK_FALLBACK_ERROR_CODES",
                "VECTOPLAN_CHUNK_FALLBACK_ERROR_CODES",
                "CHUNK_FALLBACK_ERROR_CODES",
            ),
            None,
        ),
        _DEFAULT_CHUNK_FALLBACK_ERROR_CODES,
    )

    VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_SECONDS = _clamp_float(
        _as_float(
            _env_first(
                (
                    "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_SECONDS",
                    "VECTOPLAN_CHUNK_PROVISIONING_CACHE_SECONDS",
                ),
                None,
            ),
            10.0,
        ),
        0.0,
        300.0,
    )

    VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES = _clamp_int(
        _as_int(
            _env_first(
                (
                    "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES",
                    "VECTOPLAN_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES",
                ),
                None,
            ),
            512,
        ),
        16,
        100_000,
    )

    VECTOPLAN_APP_PERSIST_CHUNK_FAILURE_STATE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_PERSIST_CHUNK_FAILURE_STATE",
                "VECTOPLAN_CHUNK_PERSIST_FAILURE_STATE",
            ),
            None,
        ),
        True,
    )

    # Georeference resolution is local and deterministic by default. External
    # geocoding remains disabled until a concrete provider is integrated.
    VECTOPLAN_APP_GEOREFERENCE_GEOCODER_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_GEOREFERENCE_GEOCODER_ENABLED",
                "VECTOPLAN_APP_GEOCODER_ENABLED",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_GEOREFERENCE_DISCOVER_GEOCODER = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_GEOREFERENCE_DISCOVER_GEOCODER",
                "VECTOPLAN_APP_DISCOVER_GEOCODER",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_GEOREFERENCE_ALLOW_COORDINATE_SWAP = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_GEOREFERENCE_ALLOW_COORDINATE_SWAP",
                "VECTOPLAN_APP_ALLOW_COORDINATE_SWAP",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_GEOREFERENCE_ALLOW_DEFAULT_SOURCE_CRS = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_GEOREFERENCE_ALLOW_DEFAULT_SOURCE_CRS",
                "VECTOPLAN_APP_ALLOW_DEFAULT_SOURCE_CRS",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_APP_PERSIST_DERIVED_GEOREFERENCE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_PERSIST_DERIVED_GEOREFERENCE",
                "VECTOPLAN_APP_GEOREFERENCE_PERSIST_RESULT",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_GEOREFERENCE_CACHE_SECONDS = _clamp_float(
        _as_float(_env("VECTOPLAN_APP_GEOREFERENCE_CACHE_SECONDS"), 300.0),
        0.0,
        3600.0,
    )

    VECTOPLAN_APP_GEOREFERENCE_NEGATIVE_CACHE_SECONDS = _clamp_float(
        _as_float(_env("VECTOPLAN_APP_GEOREFERENCE_NEGATIVE_CACHE_SECONDS"), 30.0),
        0.0,
        300.0,
    )

    VECTOPLAN_APP_GEOREFERENCE_CACHE_MAX_ENTRIES = _clamp_int(
        _as_int(_env("VECTOPLAN_APP_GEOREFERENCE_CACHE_MAX_ENTRIES"), 512),
        16,
        100_000,
    )

    VECTOPLAN_APP_GEOREFERENCE_GEOCODER_TIMEOUT_SECONDS = _clamp_float(
        _as_float(_env("VECTOPLAN_APP_GEOREFERENCE_GEOCODER_TIMEOUT_SECONDS"), 4.0),
        0.1,
        60.0,
    )

    VECTOPLAN_APP_GEOREFERENCE_COORDINATE_PRECISION = _clamp_int(
        _as_int(_env("VECTOPLAN_APP_GEOREFERENCE_COORDINATE_PRECISION"), 12),
        6,
        15,
    )

    VECTOPLAN_APP_GEOREFERENCE_HEIGHT_PRECISION = _clamp_int(
        _as_int(_env("VECTOPLAN_APP_GEOREFERENCE_HEIGHT_PRECISION"), 6),
        0,
        12,
    )

    # Existing/legacy chunk-client aliases now follow the App provisioning policy.
    VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID = _as_world_template(
        _env_first(
            (
                "VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
                "VECTOPLAN_CHUNK_PROJECT_PROVISIONING_DEFAULT_TEMPLATE_ID",
                "CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
            ),
            VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE,
        ),
        VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE,
    )

    VECTOPLAN_CHUNK_PROVISION_FALLBACK_TEMPLATE_ID = _as_fallback_world_template(
        _env_first(
            (
                "VECTOPLAN_CHUNK_PROVISION_FALLBACK_TEMPLATE_ID",
                "CHUNK_PROVISION_FALLBACK_TEMPLATE_ID",
            ),
            VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE,
        ),
        VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID,
        VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE,
    )

    VECTOPLAN_CHUNK_PROJECT_PROVISIONING_DEFAULT_TEMPLATE_ID = VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID
    VECTOPLAN_CHUNK_PROJECT_PROVISIONING_FALLBACK_TEMPLATE_ID = VECTOPLAN_CHUNK_PROVISION_FALLBACK_TEMPLATE_ID

    VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID = _env_str_first(
        (
            "VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID",
            "VECTOPLAN_CHUNK_PROJECT_PROVISIONING_DEFAULT_WORLD_ID",
            "CHUNK_PROVISION_DEFAULT_WORLD_ID",
        ),
        _DEFAULT_CHUNK_DEFAULT_WORLD_ID,
    )

    VECTOPLAN_APP_CHUNK_PROVISIONING_REQUIRED = VECTOPLAN_CHUNK_PROVISION_REQUIRED
    VECTOPLAN_APP_CHUNK_PROVISION_ON_PROJECT_CREATE = VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE

    # ───────── App -> Chunk project-access synchronization ─────────
    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED",
                "VECTOPLAN_CHUNK_ACCESS_SYNC_ENABLED",
                "CHUNK_ACCESS_SYNC_ENABLED",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED = _as_bool(
        _env_first(
            (
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED",
                "VECTOPLAN_CHUNK_ACCESS_SYNC_REQUIRED",
            ),
            None,
        ),
        False,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_PROJECT_CREATE = _as_bool(
        _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_PROJECT_CREATE"),
        True,
    )
    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_MEMBERSHIP_CHANGE = _as_bool(
        _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_MEMBERSHIP_CHANGE"),
        True,
    )
    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_INVITATION_ACCEPT = _as_bool(
        _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_INVITATION_ACCEPT"),
        True,
    )
    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_OWNER_TRANSFER = _as_bool(
        _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ON_OWNER_TRANSFER"),
        True,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_TIMEOUT_SECONDS = _clamp_float(
        _as_float(
            _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_TIMEOUT_SECONDS"),
            VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS,
        ),
        0.1,
        120.0,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRIES = _clamp_int(
        _as_int(
            _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRIES"),
            VECTOPLAN_CHUNK_PROVISION_RETRIES,
        ),
        0,
        10,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRY_SECONDS = _clamp_float(
        _as_float(
            _env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRY_SECONDS"),
            VECTOPLAN_CHUNK_PROVISION_RETRY_SECONDS,
        ),
        0.0,
        60.0,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_SYNC_CACHE_SECONDS = _clamp_float(
        _as_float(_env("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_CACHE_SECONDS"), 5.0),
        0.0,
        300.0,
    )

    VECTOPLAN_APP_CHUNK_ACCESS_ROLE_OWNER = "owner"
    VECTOPLAN_APP_CHUNK_ACCESS_ROLE_ADMIN = "admin"
    VECTOPLAN_APP_CHUNK_ACCESS_ROLE_EDITOR = "editor"
    VECTOPLAN_APP_CHUNK_ACCESS_ROLE_VIEWER = "viewer"
    VECTOPLAN_APP_CHUNK_ACCESS_ALLOWED_ROLES = (
        VECTOPLAN_APP_CHUNK_ACCESS_ROLE_OWNER,
        VECTOPLAN_APP_CHUNK_ACCESS_ROLE_ADMIN,
        VECTOPLAN_APP_CHUNK_ACCESS_ROLE_EDITOR,
        VECTOPLAN_APP_CHUNK_ACCESS_ROLE_VIEWER,
    )

    VECTOPLAN_CHUNK_SERVICE_LINK_AUTO_CREATE = _as_bool(
        _env_first(
            (
                "VECTOPLAN_CHUNK_SERVICE_LINK_AUTO_CREATE",
                "CHUNK_SERVICE_LINK_AUTO_CREATE",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN = _as_bool(
        _env_first(
            (
                "VECTOPLAN_CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
                "CHUNK_PROVISION_RETRY_ON_WORKSPACE_OPEN",
            ),
            None,
        ),
        True,
    )

    VECTOPLAN_CHUNK_PROVISION_STATUS_PENDING = "pending"
    VECTOPLAN_CHUNK_PROVISION_STATUS_PROVISIONING = "provisioning"
    VECTOPLAN_CHUNK_PROVISION_STATUS_READY = "ready"
    VECTOPLAN_CHUNK_PROVISION_STATUS_FALLBACK_READY = "fallback_ready"
    VECTOPLAN_CHUNK_PROVISION_STATUS_FAILED = "failed"
    VECTOPLAN_CHUNK_PROVISION_STATUS_REPAIR_REQUIRED = "repair_required"
    VECTOPLAN_CHUNK_PROVISION_STATUS_ERROR = "error"
    VECTOPLAN_CHUNK_PROVISION_STATUS_DISABLED = "disabled"

    VECTOPLAN_CHUNK_PROVISION_API_PATH_ENSURE_BY_APP = "/projects/by-app/{app_project_public_id}"
    VECTOPLAN_CHUNK_PROVISION_API_PATH_ENSURE = "/projects/ensure"
    VECTOPLAN_CHUNK_PROVISION_API_PATH_PREVIEW_BY_APP = "/projects/preview/by-app/{app_project_public_id}"
    VECTOPLAN_CHUNK_STATUS_API_PATH = "/projects/_status"

    VECTOPLAN_CHUNK_ACCESS_API_PATH = "/projects/{chunk_project_id}/access"
    VECTOPLAN_CHUNK_ACCESS_INITIALIZE_API_PATH = "/projects/{chunk_project_id}/access/initialize"
    VECTOPLAN_CHUNK_ASSIGNMENTS_API_PATH = "/projects/{chunk_project_id}/assignments"
    VECTOPLAN_CHUNK_TRANSFER_OWNER_API_PATH = "/projects/{chunk_project_id}/access/transfer-owner"

    VECTOPLAN_LIBRARY_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LIBRARY_PUBLIC_URL",
                "VECTOPLAN_LIBRARY_PUBLIC_BASE_URL",
                "LIBRARY_PUBLIC_URL",
            ),
            _DEFAULT_LIBRARY_PUBLIC_URL,
        ),
        _DEFAULT_LIBRARY_PUBLIC_URL,
    )

    VECTOPLAN_LIBRARY_PUBLIC_BASE_URL = VECTOPLAN_LIBRARY_PUBLIC_URL
    LIBRARY_PUBLIC_URL = VECTOPLAN_LIBRARY_PUBLIC_URL
    LIBRARY_PUBLIC_BASE_URL = VECTOPLAN_LIBRARY_PUBLIC_BASE_URL

    VECTOPLAN_LIBRARY_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LIBRARY_INTERNAL_URL",
                "LIBRARY_INTERNAL_URL",
            ),
            _DEFAULT_LIBRARY_INTERNAL_URL,
        ),
        _DEFAULT_LIBRARY_INTERNAL_URL,
    )

    LIBRARY_INTERNAL_URL = VECTOPLAN_LIBRARY_INTERNAL_URL

    # ───────── Frame / CSP integration ─────────
    # These values are consumed later by app.py and service-specific security code.
    VECTOPLAN_ALLOWED_FRAME_PARENTS_LIST = _cached_origin_list(
        _env_str_first(
            (
                "VECTOPLAN_ALLOWED_FRAME_PARENTS",
                "VECTOPLAN_FRAME_ANCESTORS",
                "FRAME_ANCESTORS",
            ),
            _space_join(_DEFAULT_ALLOWED_FRAME_PARENTS),
        ),
        _space_join(_DEFAULT_ALLOWED_FRAME_PARENTS),
    )

    VECTOPLAN_ALLOWED_FRAME_PARENTS = _space_join(VECTOPLAN_ALLOWED_FRAME_PARENTS_LIST)
    VECTOPLAN_FRAME_ANCESTORS = VECTOPLAN_ALLOWED_FRAME_PARENTS

    VECTOPLAN_EDITOR_FRAME_ANCESTORS = _env_str_first(
        (
            "VECTOPLAN_EDITOR_FRAME_ANCESTORS",
            "EDITOR_FRAME_ANCESTORS",
        ),
        VECTOPLAN_ALLOWED_FRAME_PARENTS,
    )

    OPENLAYER_FRAME_ANCESTORS = _env_str_first(
        (
            "OPENLAYER_FRAME_ANCESTORS",
            "OPENLAYER_ALLOWED_FRAME_PARENTS",
        ),
        VECTOPLAN_ALLOWED_FRAME_PARENTS,
    )

    # If vectoplan-auth embeds login/register as overlay/iframe inside vectoplan-app,
    # auth's own CSP must allow VECTOPLAN_APP_PUBLIC_URL as frame ancestor.
    # This value is exported for diagnostics/docs and compose alignment.
    VECTOPLAN_AUTH_FRAME_ANCESTORS = _env_str_first(
        (
            "VECTOPLAN_AUTH_FRAME_ANCESTORS",
            "AUTH_FRAME_ANCESTORS",
        ),
        VECTOPLAN_ALLOWED_FRAME_PARENTS,
    )

    VECTOPLAN_VERGABE_PUBLIC_URL = _norm_url(
        _env_str("VECTOPLAN_VERGABE_PUBLIC_URL", "http://localhost:5203/vergabe")
    )

    VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST = _cached_origin_list(
        _env_str_first(
            (
                "VECTOPLAN_APP_ALLOWED_FRAME_SRC",
                "APP_ALLOWED_FRAME_SRC",
                "CSP_FRAME_SRC",
            ),
            _space_join(_DEFAULT_APP_ALLOWED_FRAME_SRC),
        ),
        _space_join(_DEFAULT_APP_ALLOWED_FRAME_SRC),
    )

    VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST = _dedupe_texts(
        [
            *VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST,
            VECTOPLAN_VERGABE_PUBLIC_URL,
            *(["http://localhost:5203", "http://127.0.0.1:5203"] if urlparse(VECTOPLAN_VERGABE_PUBLIC_URL).hostname in {"localhost", "127.0.0.1"} else []),
            VECTOPLAN_AUTH_PUBLIC_URL,
            VECTOPLAN_EDITOR_PUBLIC_URL,
            VECTOPLAN_CAD_PUBLIC_URL,
            VECTOPLAN_LV_PUBLIC_URL,
            VECTOPLAN_FILECLOUD_PUBLIC_URL,
            OPENLAYER_PUBLIC_URL,
        ]
    )

    VECTOPLAN_APP_ALLOWED_FRAME_SRC = _space_join(VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST)

    # CSP-ready version. "self" becomes "'self'".
    CSP_FRAME_SRC = _origins_to_csp_value(VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST, include_self=False)
    SECURITY_CSP_FRAME_SRC = CSP_FRAME_SRC

    # CSP frame-ancestors for the services that are embedded into this app.
    CSP_FRAME_ANCESTORS = _origins_to_csp_value(VECTOPLAN_ALLOWED_FRAME_PARENTS_LIST, include_self=False)
    SECURITY_CSP_FRAME_ANCESTORS = CSP_FRAME_ANCESTORS

    # connect-src can be used by app.py/security helpers later. This includes
    # public service origins for browser-side status/diagnostic fetches. Server-
    # side provisioning still uses INTERNAL_URL and does not depend on CSP.
    VECTOPLAN_APP_ALLOWED_CONNECT_SRC_LIST = _cached_origin_list(
        _env_str_first(
            (
                "VECTOPLAN_APP_ALLOWED_CONNECT_SRC",
                "APP_ALLOWED_CONNECT_SRC",
                "CSP_CONNECT_SRC",
            ),
            _space_join(_DEFAULT_APP_ALLOWED_CONNECT_SRC),
        ),
        _space_join(_DEFAULT_APP_ALLOWED_CONNECT_SRC),
    )

    VECTOPLAN_APP_ALLOWED_CONNECT_SRC_LIST = _dedupe_texts(
        [
            *VECTOPLAN_APP_ALLOWED_CONNECT_SRC_LIST,
            VECTOPLAN_APP_PUBLIC_URL,
            VECTOPLAN_AUTH_PUBLIC_URL,
            VECTOPLAN_EDITOR_PUBLIC_URL,
            VECTOPLAN_CAD_PUBLIC_URL,
            VECTOPLAN_LV_PUBLIC_URL,
            VECTOPLAN_FILECLOUD_PUBLIC_URL,
            OPENLAYER_PUBLIC_URL,
            VECTOPLAN_CHUNK_PUBLIC_URL,
            VECTOPLAN_LIBRARY_PUBLIC_URL,
        ]
    )

    VECTOPLAN_APP_ALLOWED_CONNECT_SRC = _space_join(VECTOPLAN_APP_ALLOWED_CONNECT_SRC_LIST)
    CSP_CONNECT_SRC = _origins_to_csp_value(VECTOPLAN_APP_ALLOWED_CONNECT_SRC_LIST, include_self=False)
    SECURITY_CSP_CONNECT_SRC = CSP_CONNECT_SRC

    # ───────── Legacy 3D backend removal guardrails ─────────
    # Explicitly disabled during the Speckle removal phase.
    # 3D files may still be uploaded as files/blobs where existing routes allow it,
    # but they must not be auto-published into any legacy 3D backend.
    LEGACY_SPECKLE_ENABLED = False
    AUTO_UPLOAD_ATTACHMENTS = _as_bool(_env("AUTO_UPLOAD_ATTACHMENTS"), False)

    # Legacy envs are intentionally not used for active runtime integration.
    VECTOPLAN_HOST = ""
    VECTOPLAN_TOKEN = ""
    VECTOPLAN_EMBED_TOKEN = ""
    SPECKLE_UPLOAD_TIMEOUT = 0

    # ───────── Geo services ─────────
    GEOSERVER_ORCHESTRATOR_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "GEOSERVER_ORCHESTRATOR_PUBLIC_URL",
                "SERVICE_PUBLIC_BASE_URL",
            ),
            "http://localhost:5110",
        ),
        "http://localhost:5110",
    )

    GEOSERVER_ORCHESTRATOR_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "GEOSERVER_ORCHESTRATOR_INTERNAL_URL",
                "GEOSERVER_ORCHESTRATOR_URL",
            ),
            "http://geoserver-orchestrator:8010",
        ),
        "http://geoserver-orchestrator:8010",
    )

    GEOSERVER_PUBLIC_BASE_URL = _norm_url(
        _env_str("GEOSERVER_PUBLIC_BASE_URL", "http://localhost:5182/geoserver"),
        "http://localhost:5182/geoserver",
    )

    GEOSERVER_INTERNAL_BASE_URL = _norm_url(
        _env_str("GEOSERVER_INTERNAL_BASE_URL", "http://geoserver:8080/geoserver"),
        "http://geoserver:8080/geoserver",
    )

    GEOSERVER_REST_BASE_URL = _norm_url(
        _env_str("GEOSERVER_REST_BASE_URL", "http://geoserver:8080/geoserver/rest"),
        "http://geoserver:8080/geoserver/rest",
    )

    # ───────── CAD viewer microservice ─────────
    CADVIEWER_BASE_URL = _norm_url(
        _env_str("CADVIEWER_BASE_URL", "http://cad:8050"),
        "http://cad:8050",
    )

    CADVIEWER_PUBLIC_URL = _norm_url(
        _env_str("CADVIEWER_PUBLIC_URL", "http://localhost:8050"),
        "http://localhost:8050",
    )

    CADVIEWER_TIMEOUT = _as_int(_env("CADVIEWER_TIMEOUT"), 20)
    CADVIEWER_UPLOAD_FIELD = _env_str("CADVIEWER_UPLOAD_FIELD", "file")

    # ───────── Version/file retention ─────────
    KEEP_VERSIONS_PER_PROJECT = _as_int(_env("KEEP_VERSIONS_PER_PROJECT"), 10)

    FILE_CACHE_MAX_AGE = _as_int(_env("FILE_CACHE_MAX_AGE"), 3600)
    FILE_CONTENT_CACHE_MAX_AGE = _as_int(_env("FILE_CONTENT_CACHE_MAX_AGE"), 3600)

    ATTACHMENT_INLINE_BASE64_MAX = _as_int(
        _env("ATTACHMENT_INLINE_BASE64_MAX"),
        10 * 1024 * 1024,
    )

    BASE64_UPLOAD_MAX_MB = _as_int(_env("BASE64_UPLOAD_MAX_MB"), 50)

    # ───────── Templates / Cards / State ─────────
    ENABLE_TEMPLATE_API = _as_bool(_env("ENABLE_TEMPLATE_API"), True)
    TEMPLATE_SEED_PATH = _env("TEMPLATE_SEED_PATH")
    TEMPLATE_SEED_JSON = _env("TEMPLATE_SEED_JSON")
    TEMPLATE_SEED = _as_json_list(TEMPLATE_SEED_JSON)
    TEMPLATE_IMPORT_TO_DB_ON_STARTUP = _as_bool(
        _env("TEMPLATE_IMPORT_TO_DB_ON_STARTUP"),
        False,
    )

    # ───────── Project welcome card ─────────
    PROJECT_WELCOME_WFS_URL = _env_str("PROJECT_WELCOME_WFS_URL", "")
    PROJECT_WELCOME_LAYER = _env_str("PROJECT_WELCOME_LAYER", "")
    PROJECT_WELCOME_HINT = _env_str(
        "PROJECT_WELCOME_HINT",
        (
            "Dies ist eine offene Alpha-Testversion von Vectoplan. "
            "Alle Systeme können kostenlos genutzt werden. "
            "Hier testen wir neue Systeme zur BigData-Auswertung und "
            "automatischen Gebäudegenerierung."
        ),
    )

    # ───────── 2D viewer fallback ─────────
    PLAN2D_FALLBACK_URL_TEMPLATE = _env_str(
        "PLAN2D_FALLBACK_URL_TEMPLATE",
        "/static/test/plan.dxf",
    )

    # ───────── UI restrictions ─────────
    VIEW_ONLY_MODE = _as_bool(_env("VIEW_ONLY_MODE"), True)
    DISABLE_UI_UPLOADS = _as_bool(_env("DISABLE_UI_UPLOADS"), True)
    DISABLE_API_UPLOADS = _as_bool(_env("DISABLE_API_UPLOADS"), True)
    ALLOW_CDN = _as_bool(_env("ALLOW_CDN"), False)

    # ───────── Logging ─────────
    LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

    # ───────── Map iframe defaults ─────────
    MAP_DEFAULT_CENTER = _as_center_pair(
        _env("MAP_DEFAULT_CENTER"),
        11.576124,
        48.137154,
    )

    MAP_DEFAULT_LON = _clamp_float(_as_float(_env("MAP_DEFAULT_LON"), MAP_DEFAULT_CENTER[0]), -180.0, 180.0)
    MAP_DEFAULT_LAT = _clamp_float(_as_float(_env("MAP_DEFAULT_LAT"), MAP_DEFAULT_CENTER[1]), -90.0, 90.0)

    MAP_DEFAULT_ZOOM = _clamp_int(_as_int(_env("MAP_DEFAULT_ZOOM"), 14), 0, 22)
    MAP_PROJECT_ZOOM = _clamp_int(_as_int(_env("MAP_PROJECT_ZOOM"), 17), 0, 22)
    MAP_MIN_ZOOM = _clamp_int(_as_int(_env("MAP_MIN_ZOOM"), 0), 0, 22)
    MAP_MAX_ZOOM = _clamp_int(_as_int(_env("MAP_MAX_ZOOM"), 22), MAP_MIN_ZOOM, 22)

    _MAP_DISABLE_SCROLL_LEGACY = _as_bool(_env("MAP_DISABLE_SCROLL"), False)
    MAP_MOUSE_WHEEL_ZOOM = _as_bool(
        _env("MAP_MOUSE_WHEEL_ZOOM"),
        not _MAP_DISABLE_SCROLL_LEGACY,
    )
    MAP_DISABLE_SCROLL = not MAP_MOUSE_WHEEL_ZOOM

    MAP_STYLE_ID = _sanitize_style_id(
        _env_str("MAP_STYLE_ID", "mapbox/satellite-streets-v12"),
        "mapbox/satellite-streets-v12",
    )

    MAP_FORWARD_STYLE_TO_IFRAME = _as_bool(
        _env("MAP_FORWARD_STYLE_TO_IFRAME"),
        False,
    )

    MAP_IFRAME_SCROLL_DEFAULT = "1" if MAP_MOUSE_WHEEL_ZOOM else "0"

    # ───────── Crawlab admin iframe ─────────
    CRAWLAB_PUBLIC_URL = _norm_url(
        _env_str("CRAWLAB_PUBLIC_URL", "http://localhost:8080"),
        "http://localhost:8080",
    )

    CRAWLAB_INTERNAL_URL = _norm_url(
        _env_str("CRAWLAB_INTERNAL_URL", "http://crawlab:8080"),
        "http://crawlab:8080",
    )

    CRAWLAB_BASE_PATH = _norm_path(_env_str("CRAWLAB_BASE_PATH", "/"), "/")

    # ───────── Superset admin iframe ─────────
    SUPERSET_PUBLIC_URL = _norm_url(
        _env_str("SUPERSET_PUBLIC_URL", "http://localhost:8088"),
        "http://localhost:8088",
    )

    SUPERSET_INTERNAL_URL = _norm_url(
        _env_str("SUPERSET_INTERNAL_URL", "http://superset:8088"),
        "http://superset:8088",
    )

    SUPERSET_BASE_PATH = _norm_path(_env_str("SUPERSET_BASE_PATH", "/"), "/")


def get_project_chunk_config_status(config: Any = None) -> Dict[str, Any]:
    """Return a non-sensitive readiness snapshot for App -> Chunk integration."""
    try:
        cfg = config or Config
        errors: List[str] = []
        warnings: List[str] = []

        requested = _as_world_template(
            getattr(cfg, "VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE", "earth"),
            "earth",
        )
        fallback = _as_fallback_world_template(
            getattr(cfg, "VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE", "flat"),
            requested,
            "flat",
        )
        internal_url = _norm_url(
            _safe_string(getattr(cfg, "VECTOPLAN_CHUNK_INTERNAL_URL", ""), ""),
            "",
        )
        public_url = _norm_url(
            _safe_string(getattr(cfg, "VECTOPLAN_CHUNK_PUBLIC_URL", ""), ""),
            "",
        )
        service_id = _safe_string(
            getattr(cfg, "VECTOPLAN_APP_CHUNK_SERVICE_ID", "vectoplan-app"),
            "vectoplan-app",
        ).strip()
        service_key = _safe_string(
            getattr(cfg, "VECTOPLAN_APP_CHUNK_SERVICE_API_KEY", ""),
            "",
        )

        if requested not in {"earth", "flat"}:
            errors.append("default_world_template_invalid")
        if fallback not in {"earth", "flat"}:
            errors.append("fallback_world_template_invalid")
        if fallback == requested:
            errors.append("fallback_world_template_matches_requested")
        if requested == "earth" and fallback != "flat":
            warnings.append("earth_default_without_flat_fallback")
        if not internal_url:
            errors.append("chunk_internal_url_missing")
        if not service_id:
            errors.append("chunk_service_id_missing")
        if not service_key:
            warnings.append("chunk_service_api_key_not_configured")
        if bool(getattr(cfg, "VECTOPLAN_APP_GEOREFERENCE_GEOCODER_ENABLED", False)) and not bool(
            getattr(cfg, "VECTOPLAN_APP_GEOREFERENCE_DISCOVER_GEOCODER", False)
        ):
            warnings.append("geocoder_enabled_without_discovery")

        return {
            "ok": not errors,
            "ready": not errors,
            "errors": errors,
            "warnings": warnings,
            "projectProvisioning": {
                "enabled": bool(getattr(cfg, "VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True)),
                "required": bool(getattr(cfg, "VECTOPLAN_CHUNK_PROVISION_REQUIRED", False)),
                "requestedWorldTemplate": requested,
                "fallbackWorldTemplate": fallback,
                "allowWorldFallback": bool(getattr(cfg, "VECTOPLAN_APP_ALLOW_WORLD_FALLBACK", True)),
                "allowClientSideFallback": bool(
                    getattr(cfg, "VECTOPLAN_APP_CLIENT_SIDE_WORLD_FALLBACK", True)
                ),
                "allowExistingTemplateChange": bool(
                    getattr(cfg, "VECTOPLAN_APP_ALLOW_WORLD_TEMPLATE_CHANGE", False)
                ),
                "persistFailureState": bool(
                    getattr(cfg, "VECTOPLAN_APP_PERSIST_CHUNK_FAILURE_STATE", True)
                ),
                "defaultWorldId": _safe_string(
                    getattr(cfg, "VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID", "world_spawn"),
                    "world_spawn",
                ),
                "cacheSeconds": float(
                    getattr(cfg, "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_SECONDS", 10.0)
                ),
                "cacheMaxEntries": int(
                    getattr(cfg, "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES", 512)
                ),
                "fallbackErrorCodes": list(
                    getattr(cfg, "VECTOPLAN_APP_CHUNK_FALLBACK_ERROR_CODES", ()) or ()
                ),
            },
            "georeference": {
                "earthCrsId": _safe_string(
                    getattr(cfg, "VECTOPLAN_APP_EARTH_CRS_ID", "EPSG:4979"),
                    "EPSG:4979",
                ),
                "projectCoordinateCrsId": _safe_string(
                    getattr(cfg, "VECTOPLAN_APP_PROJECT_COORDINATE_CRS_ID", "EPSG:4326"),
                    "EPSG:4326",
                ),
                "defaultEarthHeight": float(
                    getattr(cfg, "VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT", 0.0)
                ),
                "geocoderEnabled": bool(
                    getattr(cfg, "VECTOPLAN_APP_GEOREFERENCE_GEOCODER_ENABLED", False)
                ),
                "persistDerivedReference": bool(
                    getattr(cfg, "VECTOPLAN_APP_PERSIST_DERIVED_GEOREFERENCE", False)
                ),
            },
            "accessSync": {
                "enabled": bool(
                    getattr(cfg, "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True)
                ),
                "required": bool(
                    getattr(cfg, "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED", False)
                ),
                "roles": list(
                    getattr(
                        cfg,
                        "VECTOPLAN_APP_CHUNK_ACCESS_ALLOWED_ROLES",
                        ("owner", "admin", "editor", "viewer"),
                    )
                ),
            },
            "transport": {
                "internalUrl": internal_url,
                "publicUrl": public_url,
                "serviceId": service_id,
                "serviceApiKeyConfigured": bool(service_key),
                "timeoutSeconds": float(
                    getattr(cfg, "VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS", 10.0)
                ),
                "retries": int(getattr(cfg, "VECTOPLAN_CHUNK_PROVISION_RETRIES", 2)),
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "ready": False,
            "errors": ["project_chunk_config_status_failed"],
            "warnings": [],
            "error": {
                "type": exc.__class__.__name__,
                "message": _safe_string(exc, "Configuration status failed."),
            },
        }


__all__ = [
    "Config",
    "refresh_env_cache",
    "get_project_chunk_config_status",
]
