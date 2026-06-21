# services/vectoplan-app/config.py
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence


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

_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on", "ja"})
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", "nein"})


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
    Cached parser for frame-ancestor / frame-src style origin lists.

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


# ─────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────

_DEFAULT_APP_PUBLIC_URL = "http://localhost:5103"
_DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
_DEFAULT_EDITOR_INTERNAL_URL = "http://vectoplan-editor:5000"
_DEFAULT_EDITOR_ROUTE = "/editor"
_DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"
_DEFAULT_OPENLAYER_INTERNAL_URL = "http://openlayer:8090"
_DEFAULT_OPENLAYER_ROUTE = "/map"

_DEFAULT_ALLOWED_FRAME_PARENTS = (
    "http://localhost:5103",
    "http://127.0.0.1:5103",
)

_DEFAULT_APP_ALLOWED_FRAME_SRC = (
    "self",
    "http://localhost:5100",
    "http://127.0.0.1:5100",
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

    # Browser-facing app origin. This is the parent origin for Editor/OpenLayer iframes.
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

    VECTOPLAN_APP_ALLOWED_FRAME_SRC = _space_join(VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST)

    # CSP-ready version. "self" becomes "'self'".
    CSP_FRAME_SRC = _origins_to_csp_value(VECTOPLAN_APP_ALLOWED_FRAME_SRC_LIST, include_self=False)
    SECURITY_CSP_FRAME_SRC = CSP_FRAME_SRC

    # CSP frame-ancestors for the services that are embedded into this app.
    CSP_FRAME_ANCESTORS = _origins_to_csp_value(VECTOPLAN_ALLOWED_FRAME_PARENTS_LIST, include_self=False)
    SECURITY_CSP_FRAME_ANCESTORS = CSP_FRAME_ANCESTORS

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

    # ───────── Chunk / Library service references ─────────
    # Available for later phases, but the current editor iframe route does not
    # create project/world structure and does not write chunk data.
    VECTOPLAN_CHUNK_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CHUNK_PUBLIC_URL",
                "VECTOPLAN_CHUNK_PUBLIC_BASE_URL",
                "CHUNK_PUBLIC_URL",
            ),
            "http://localhost:5102",
        ),
        "http://localhost:5102",
    )

    VECTOPLAN_CHUNK_PUBLIC_BASE_URL = VECTOPLAN_CHUNK_PUBLIC_URL

    VECTOPLAN_CHUNK_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_CHUNK_INTERNAL_URL",
                "CHUNK_INTERNAL_URL",
            ),
            "http://vectoplan-chunk:5000",
        ),
        "http://vectoplan-chunk:5000",
    )

    VECTOPLAN_LIBRARY_PUBLIC_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LIBRARY_PUBLIC_URL",
                "VECTOPLAN_LIBRARY_PUBLIC_BASE_URL",
                "LIBRARY_PUBLIC_URL",
            ),
            "http://localhost:5101",
        ),
        "http://localhost:5101",
    )

    VECTOPLAN_LIBRARY_PUBLIC_BASE_URL = VECTOPLAN_LIBRARY_PUBLIC_URL

    VECTOPLAN_LIBRARY_INTERNAL_URL = _norm_url(
        _env_str_first(
            (
                "VECTOPLAN_LIBRARY_INTERNAL_URL",
                "LIBRARY_INTERNAL_URL",
            ),
            "http://vectoplan-library:5000",
        ),
        "http://vectoplan-library:5000",
    )

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


__all__ = [
    "Config",
    "refresh_env_cache",
]