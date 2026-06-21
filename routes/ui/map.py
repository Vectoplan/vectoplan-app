# services/vectoplan-app/routes/ui/map.py
from __future__ import annotations

import base64
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, abort, current_app, jsonify, make_response, request
from werkzeug.wrappers import Response

from extensions import db
from models import Conversation


bp = Blueprint("ui_map", __name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

_STYLE_RE = re.compile(r"^[a-z0-9\-]+/[a-z0-9\-\.]+$", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[\s,;]+")

_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on", "ja"})
_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", "nein"})

# Browser-facing default. This must be the published host port.
DEFAULT_OPENLAYER_PUBLIC_URL = "http://localhost:5190"

# Docker-internal default. This must not be sent to the browser as iframe URL.
DEFAULT_OPENLAYER_INTERNAL_URL = "http://openlayer:8090"

# Known legacy/wrong browser targets that appeared during the iframe integration.
LEGACY_OPENLAYER_BROWSER_URLS = frozenset(
    {
        "http://localhost:8090",
        "http://127.0.0.1:8090",
        "http://openlayer:8090",
        "http://vectoplan-openlayer:8090",
    }
)

DEFAULT_OPENLAYER_ROUTE = "/map"

DEFAULT_GEOSERVER_WFS_BASE = ""
DEFAULT_MAP_CENTER = [11.576124, 48.137154]
DEFAULT_MAP_ZOOM = 14
DEFAULT_MAP_MIN_ZOOM = 0
DEFAULT_MAP_MAX_ZOOM = 22
DEFAULT_WFS_SRS = "EPSG:25833"
DEFAULT_WFS_OUTPUT_FORMAT = "application/json"

DEFAULT_ALLOWED_FRAME_PARENTS = [
    "http://localhost:5103",
    "http://127.0.0.1:5103",
]

DEFAULT_ALLOWED_TYPENAMES = [
    "de_flurstueck:",
    "de_flurstueck:fluerstuck_",
    "de_flurstueck:flurstueck_",
]

WFS_ALLOWED_QUERY_PARAMS = {
    "service",
    "version",
    "request",
    "typeNames",
    "typeName",
    "srsName",
    "bbox",
    "outputFormat",
    "maxFeatures",
    "startIndex",
    "propertyName",
    "cql_filter",
    "filter",
    "count",
    "sortBy",
}

WFS_DEFAULT_PARAMS = {
    "service": "WFS",
    "version": "1.0.0",
    "request": "GetFeature",
    "outputFormat": DEFAULT_WFS_OUTPUT_FORMAT,
}

MAP_FORWARD_QUERY_KEYS = {
    "dataset_id",
    "dataset",
    "layer",
    "mode",
    "r",
}


# ─────────────────────────────────────────────────────────────
# Cached parsing helpers
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _cached_split_text_list(raw: str, fallback: str = "") -> Tuple[str, ...]:
    try:
        text = str(raw or "").strip()

        if not text:
            text = str(fallback or "").strip()

        if not text:
            return tuple()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                result = []
                for item in parsed:
                    item_text = str(item or "").strip()
                    if item_text and item_text not in result:
                        result.append(item_text)
                return tuple(result)
        except Exception:
            pass

        result = []
        for part in _SPLIT_RE.split(text):
            item_text = str(part or "").strip()
            if item_text and item_text not in result:
                result.append(item_text)

        return tuple(result)

    except Exception:
        try:
            return tuple(str(fallback or "").split())
        except Exception:
            return tuple()


@lru_cache(maxsize=128)
def _cached_normalize_url_base(value: str, default: str = "") -> str:
    try:
        text = str(value or "").strip()

        if not text:
            return str(default or "").strip().rstrip("/")

        return text.rstrip("/")

    except Exception:
        return str(default or "").strip().rstrip("/")


@lru_cache(maxsize=128)
def _cached_normalize_path(value: str, default: str = "/") -> str:
    try:
        fallback = str(default or "/").strip() or "/"
        if not fallback.startswith("/"):
            fallback = "/" + fallback

        text = str(value or "").strip()

        if not text:
            return fallback

        if not text.startswith("/"):
            text = "/" + text

        while "//" in text:
            text = text.replace("//", "/")

        return text or fallback

    except Exception:
        return default or "/"


# ─────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────

def _apply_json_headers(resp: Response) -> Response:
    try:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return resp


def _json_response(payload: Mapping[str, Any], status: int = 200) -> Response:
    response = jsonify(payload)
    response.status_code = status
    return _apply_json_headers(response)


def _json_error(
    *,
    message: str,
    status: int = 400,
    code: str = "error",
    chat_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "status": "error",
        "chat_id": chat_id,
        "error": {
            "code": code,
            "message": str(message or "error"),
        },
        "legacy_3d_backend": False,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status=status)


def _cache_headers(resp: Response, *, strong: bool = False) -> Response:
    try:
        dev = str(current_app.config.get("FLASK_ENV", "") or "").lower().startswith("dev")

        if dev:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
        else:
            if strong:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=120"

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


def _frame_ancestors_value() -> str:
    """
    CSP frame-ancestors for app-side iframe entry routes.

    This route is same-origin to the app first and then redirects to OpenLayer.
    It still receives explicit frame headers to avoid global SAMEORIGIN headers
    interfering with the app shell.
    """
    try:
        raw = _cfg_first(
            [
                "VECTOPLAN_ALLOWED_FRAME_PARENTS",
                "VECTOPLAN_FRAME_ANCESTORS",
                "FRAME_ANCESTORS",
            ],
            " ".join(DEFAULT_ALLOWED_FRAME_PARENTS),
        )

        ancestors = list(_cached_split_text_list(raw, " ".join(DEFAULT_ALLOWED_FRAME_PARENTS)))

        result = ["'self'"]
        for item in ancestors:
            text = str(item or "").strip()
            if not text:
                continue
            normalized = "'self'" if text in {"self", "'self'"} else text
            if normalized not in result:
                result.append(normalized)

        return " ".join(result)

    except Exception:
        return "'self' http://localhost:5103 http://127.0.0.1:5103"


def _apply_frame_headers(resp: Response, *, allow_embed: bool = True) -> Response:
    """
    Apply conservative frame headers.

    Never uses frame-ancestors '*'. The actual OpenLayer page must also allow
    framing in its own service when /map?embed=1 is requested.
    """
    try:
        if allow_embed:
            resp.headers["Content-Security-Policy"] = f"frame-ancestors {_frame_ancestors_value()}"
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


def _redirect_response(target: str) -> Response:
    resp = make_response("", 302)
    resp.headers["Location"] = target
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return _apply_frame_headers(resp, allow_embed=True)


# ─────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────

def _env_get(key: str, default: str = "") -> str:
    try:
        return str(os.environ.get(key, default) or default).strip()
    except Exception:
        return default


def _cfg_raw(key: str, default: Any = None) -> Any:
    try:
        value = current_app.config.get(key)

        if value is not None and value != "":
            return value

        env_value = os.environ.get(key)
        if env_value is not None and str(env_value).strip() != "":
            return env_value

        return default

    except Exception:
        return default


def _cfg_str(key: str, default: str = "") -> str:
    try:
        value = _cfg_raw(key, default)
        return str(value if value is not None else default).strip()
    except Exception:
        return default


def _cfg_first(keys: Sequence[str], default: str = "") -> str:
    try:
        for key in keys:
            value = _cfg_str(str(key), "")
            if value:
                return value
        return default
    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_cfg_raw(key, default))
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(_cfg_raw(key, default))
    except Exception:
        return default


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        value = _cfg_raw(key, default)

        if isinstance(value, bool):
            return bool(value)

        text = str(value).strip().lower()

        if text in _TRUE_VALUES:
            return True

        if text in _FALSE_VALUES:
            return False

        return default

    except Exception:
        return default


def _cfg_list(key: str, default: Sequence[str]) -> List[str]:
    try:
        value = _cfg_raw(key, default)

        if isinstance(value, (list, tuple, set)):
            result = []
            for item in value:
                item_text = str(item or "").strip()
                if item_text and item_text not in result:
                    result.append(item_text)
            return result

        if isinstance(value, str):
            parsed = list(_cached_split_text_list(value, ",".join(default)))
            return parsed or list(default)

        return list(default)

    except Exception:
        return list(default)


# ─────────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────────

def _log_warning(message: str, *args: Any) -> None:
    try:
        current_app.logger.warning(message, *args)
    except Exception:
        pass


def _log_exception(message: str, exc: Exception | None = None) -> None:
    try:
        if exc is None:
            current_app.logger.exception(message)
        else:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


def _clean_text(value: Any, default: str = "", max_len: int = 500) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
        return max(minimum, min(maximum, parsed))
    except Exception:
        return minimum


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
        return max(minimum, min(maximum, parsed))
    except Exception:
        return minimum


def _normalize_url_base(value: str, default: str = "") -> str:
    return _cached_normalize_url_base(value, default)


def _normalize_path(value: str, default: str = "/") -> str:
    return _cached_normalize_path(value, default)


def _url_netloc(value: str) -> str:
    try:
        return urlsplit(value).netloc.lower()
    except Exception:
        return ""


def _url_scheme(value: str) -> str:
    try:
        return urlsplit(value).scheme.lower()
    except Exception:
        return ""


def _is_known_legacy_openlayer_public_url(value: str) -> bool:
    try:
        normalized = _normalize_url_base(value, "")
        return normalized in LEGACY_OPENLAYER_BROWSER_URLS
    except Exception:
        return False


def _is_docker_internal_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = str(parsed.hostname or "").strip().lower()

        return host in {
            "openlayer",
            "vectoplan-openlayer",
            "server-openlayer",
        }

    except Exception:
        return False


def _safe_openlayer_public_url() -> str:
    """
    Return the browser-facing OpenLayer URL.

    This intentionally repairs stale config values such as http://localhost:8090.
    Port 8090 is the container port, not the browser-facing mapped port in the
    local VECTOPLAN stack.
    """
    configured = _cfg_first(
        [
            "OPENLAYER_PUBLIC_URL",
            "OPENLAYER_PUBLIC_BASE_URL",
            "VECTOPLAN_OPENLAYER_PUBLIC_URL",
            "VECTOPLAN_OPENLAYER_PUBLIC_BASE_URL",
        ],
        DEFAULT_OPENLAYER_PUBLIC_URL,
    )

    public_url = _normalize_url_base(configured, DEFAULT_OPENLAYER_PUBLIC_URL)

    allow_internal_public = _cfg_bool("OPENLAYER_ALLOW_INTERNAL_PUBLIC_URL", False)

    try:
        if not allow_internal_public:
            if _is_known_legacy_openlayer_public_url(public_url) or _is_docker_internal_url(public_url):
                _log_warning(
                    "Ignoring unsafe OpenLayer public URL for browser redirect: %s; using %s",
                    public_url,
                    DEFAULT_OPENLAYER_PUBLIC_URL,
                )
                return DEFAULT_OPENLAYER_PUBLIC_URL

        if _url_scheme(public_url) not in {"http", "https"}:
            _log_warning(
                "Ignoring OpenLayer public URL with invalid scheme: %s; using %s",
                public_url,
                DEFAULT_OPENLAYER_PUBLIC_URL,
            )
            return DEFAULT_OPENLAYER_PUBLIC_URL

        if not _url_netloc(public_url):
            _log_warning(
                "Ignoring OpenLayer public URL without netloc: %s; using %s",
                public_url,
                DEFAULT_OPENLAYER_PUBLIC_URL,
            )
            return DEFAULT_OPENLAYER_PUBLIC_URL

        return public_url

    except Exception:
        return DEFAULT_OPENLAYER_PUBLIC_URL


def _safe_openlayer_internal_url() -> str:
    return _normalize_url_base(
        _cfg_first(
            [
                "OPENLAYER_INTERNAL_URL",
                "VECTOPLAN_OPENLAYER_INTERNAL_URL",
            ],
            DEFAULT_OPENLAYER_INTERNAL_URL,
        ),
        DEFAULT_OPENLAYER_INTERNAL_URL,
    )


def _safe_openlayer_route() -> str:
    return _normalize_path(
        _cfg_first(
            [
                "OPENLAYER_ROUTE",
                "VECTOPLAN_OPENLAYER_ROUTE",
                "MAP_ROUTE",
            ],
            DEFAULT_OPENLAYER_ROUTE,
        ),
        DEFAULT_OPENLAYER_ROUTE,
    )


def _join_public_url(base: str, route: str) -> str:
    """
    Join public base URL and route.

    Supports both:
    - base=http://localhost:5190, route=/map
    - base=http://localhost:5190/map, route=/map
    """
    try:
        normalized_base = _normalize_url_base(base, DEFAULT_OPENLAYER_PUBLIC_URL)
        normalized_route = _normalize_path(route, DEFAULT_OPENLAYER_ROUTE)

        if normalized_base.endswith(normalized_route):
            return normalized_base

        if normalized_route == "/":
            return normalized_base

        return f"{normalized_base}{normalized_route}"

    except Exception:
        return f"{DEFAULT_OPENLAYER_PUBLIC_URL}{DEFAULT_OPENLAYER_ROUTE}"


def _ensure_conversation(chat_id: Optional[str]) -> Conversation:
    """
    Return an existing conversation or create one.

    Kept for compatibility with existing UI iframe routes.
    """
    try:
        if chat_id:
            conv = Conversation.query.get(str(chat_id))
            if conv:
                return conv

        conv = Conversation()
        db.session.add(conv)
        db.session.commit()

        return conv

    except Exception:
        try:
            db.session.rollback()
            _log_exception("create conversation failed")
        except Exception:
            pass

        abort(404, description="conversation not available")


def _get_default_center() -> List[float]:
    try:
        center = current_app.config.get("MAP_DEFAULT_CENTER", DEFAULT_MAP_CENTER)

        if isinstance(center, (list, tuple)) and len(center) >= 2:
            lon = _clamp_float(center[0], -180.0, 180.0)
            lat = _clamp_float(center[1], -90.0, 90.0)
            return [lon, lat]

        if isinstance(center, str):
            parts = [item.strip() for item in center.replace(";", ",").split(",") if item.strip()]
            if len(parts) >= 2:
                lon = _clamp_float(parts[0], -180.0, 180.0)
                lat = _clamp_float(parts[1], -90.0, 90.0)
                return [lon, lat]

    except Exception:
        pass

    lon = _clamp_float(
        _cfg_raw("MAP_DEFAULT_LON", DEFAULT_MAP_CENTER[0]),
        -180.0,
        180.0,
    )
    lat = _clamp_float(
        _cfg_raw("MAP_DEFAULT_LAT", DEFAULT_MAP_CENTER[1]),
        -90.0,
        90.0,
    )

    return [lon, lat]


def _map_min_zoom() -> int:
    return _clamp_int(
        _cfg_int("MAP_MIN_ZOOM", DEFAULT_MAP_MIN_ZOOM),
        DEFAULT_MAP_MIN_ZOOM,
        DEFAULT_MAP_MAX_ZOOM,
    )


def _map_max_zoom() -> int:
    return _clamp_int(
        _cfg_int("MAP_MAX_ZOOM", DEFAULT_MAP_MAX_ZOOM),
        _map_min_zoom(),
        DEFAULT_MAP_MAX_ZOOM,
    )


def _parse_lon_lat_zoom() -> Tuple[float, float, int]:
    center = _get_default_center()
    default_lon = center[0]
    default_lat = center[1]
    min_zoom = _map_min_zoom()
    max_zoom = _map_max_zoom()

    default_zoom = _clamp_int(
        _cfg_int("MAP_DEFAULT_ZOOM", DEFAULT_MAP_ZOOM),
        min_zoom,
        max_zoom,
    )

    lon = _clamp_float(
        request.args.get("lon", default_lon),
        -180.0,
        180.0,
    )

    lat = _clamp_float(
        request.args.get("lat", default_lat),
        -90.0,
        90.0,
    )

    zoom = _clamp_int(
        request.args.get("zoom", default_zoom),
        min_zoom,
        max_zoom,
    )

    return lon, lat, zoom


def _sanitize_style(style: Optional[str]) -> str:
    try:
        value = str(style or "").strip()

        if not value:
            return ""

        return value if _STYLE_RE.match(value) else ""

    except Exception:
        return ""


def _normalize_bool_query(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    try:
        text = str(value).strip().lower()
    except Exception:
        return None

    if text in _TRUE_VALUES:
        return "1"

    if text in _FALSE_VALUES:
        return "0"

    return None


def _forward_safe_query_value(key: str, max_len: int = 160) -> Optional[str]:
    try:
        value = request.args.get(key)
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        if len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return None


def _build_openlayer_query(chat_id: str) -> Dict[str, Any]:
    """
    Build the browser-facing OpenLayer query.

    Rules:
    - embed=1 is always sent when OpenLayer embedding is enabled.
    - chat_id is sent so OpenLayer can later adapt behavior to app context.
    - style is forwarded only when explicitly provided or configured.
    - scroll defaults to enabled for iframe usability.
    """
    lon, lat, zoom = _parse_lon_lat_zoom()

    explicit_style = _sanitize_style(request.args.get("style"))
    configured_style = _sanitize_style(_cfg_str("MAP_STYLE_ID", ""))
    explicit_scroll = _normalize_bool_query(request.args.get("scroll"))
    configured_scroll = _cfg_str("MAP_IFRAME_SCROLL_DEFAULT", "1")

    scroll_value = explicit_scroll if explicit_scroll is not None else _normalize_bool_query(configured_scroll)
    if scroll_value is None:
        scroll_value = "1"

    query: Dict[str, Any] = {
        "lon": lon,
        "lat": lat,
        "zoom": zoom,
        "scroll": scroll_value,
        "chat_id": str(chat_id),
    }

    if _cfg_bool("OPENLAYER_EMBED_ENABLED", True):
        query["embed"] = "1"

    if explicit_style:
        query["style"] = explicit_style
    elif _cfg_bool("MAP_FORWARD_STYLE_TO_IFRAME", False) and configured_style:
        query["style"] = configured_style

    for key in MAP_FORWARD_QUERY_KEYS:
        value = _forward_safe_query_value(key)
        if value is not None:
            query[key] = value

    return query


def _build_openlayer_target(chat_id: str) -> str:
    base = _safe_openlayer_public_url()
    route = _safe_openlayer_route()
    page_url = _join_public_url(base, route)
    query = _build_openlayer_query(chat_id)

    separator = "&" if "?" in page_url else "?"
    return f"{page_url}{separator}{urlencode(query)}"


# ─────────────────────────────────────────────────────────────
# WFS helpers
# ─────────────────────────────────────────────────────────────

def _wfs_base() -> str:
    """
    GeoServer base URL without /wfs.

    Supported config/env keys, in order:
    - GEOSERVER_WFS_BASE
    - GEOSERVER_INTERNAL_BASE_URL
    - GEOSERVER_PUBLIC_BASE_URL

    The WFS proxy is server-side, so internal URL is preferred where available.
    """
    base = _cfg_first(
        [
            "GEOSERVER_WFS_BASE",
            "GEOSERVER_INTERNAL_BASE_URL",
            "GEOSERVER_PUBLIC_BASE_URL",
        ],
        DEFAULT_GEOSERVER_WFS_BASE,
    )

    return _normalize_url_base(base, DEFAULT_GEOSERVER_WFS_BASE)


def _wfs_auth_header() -> Dict[str, str]:
    """
    Build server-side Basic Auth header.

    Credentials never go to the browser.
    """
    try:
        user = _cfg_first(
            [
                "GEOSERVER_WFS_USER",
                "GEOSERVER_USERNAME",
                "GEOSERVER_ADMIN_USER",
            ],
            "",
        )

        password = _cfg_first(
            [
                "GEOSERVER_WFS_PASSWORD",
                "GEOSERVER_PASSWORD",
                "GEOSERVER_ADMIN_PASSWORD",
            ],
            "",
        )

        if not user or not password:
            return {}

        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")

        return {
            "Authorization": f"Basic {token}",
        }

    except Exception:
        return {}


def _is_typename_allowed(name: str, allow: Sequence[str]) -> bool:
    try:
        value = str(name or "").strip()

        if not value:
            return False

        for allowed in allow:
            allowed_value = str(allowed or "").strip()

            if not allowed_value:
                continue

            if value == allowed_value:
                return True

            if value.startswith(allowed_value):
                return True

        return False

    except Exception:
        return False


def _sanitize_typenames(raw: str, allow: Sequence[str]) -> str:
    try:
        parts = [part.strip() for part in str(raw or "").split(",") if part.strip()]
        safe = [part for part in parts if _is_typename_allowed(part, allow)]
        return ",".join(safe)
    except Exception:
        return ""


def _sanitize_wfs_params() -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """
    Return (safe_params, error_message).
    """
    try:
        safe: Dict[str, str] = {}

        for key, value in request.args.items():
            try:
                if key not in WFS_ALLOWED_QUERY_PARAMS:
                    continue

                if value is None:
                    continue

                text = str(value)
                if len(text) > 20_000:
                    return None, f"query param too long: {key}"

                safe[key] = text
            except Exception:
                continue

        for key, value in WFS_DEFAULT_PARAMS.items():
            safe.setdefault(key, value)

        allowed_types = _cfg_list("WFS_ALLOWED_TYPENAMES", DEFAULT_ALLOWED_TYPENAMES)
        raw_typenames = safe.get("typeNames") or safe.get("typeName") or ""
        sanitized_typenames = _sanitize_typenames(raw_typenames, allowed_types)

        if not sanitized_typenames:
            return None, "typeNames not allowed or empty"

        safe["typeNames"] = sanitized_typenames
        safe.pop("typeName", None)

        safe.setdefault("srsName", _cfg_str("WFS_DEFAULT_SRS", DEFAULT_WFS_SRS))

        # Conservative bounds for paging-like params.
        if "count" in safe:
            safe["count"] = str(_clamp_int(safe.get("count"), 1, 10000))

        if "maxFeatures" in safe:
            safe["maxFeatures"] = str(_clamp_int(safe.get("maxFeatures"), 1, 10000))

        if "startIndex" in safe:
            safe["startIndex"] = str(_clamp_int(safe.get("startIndex"), 0, 1_000_000))

        return safe, None

    except Exception as exc:
        return None, str(exc)


def _build_wfs_url(base: str, params: Dict[str, str]) -> str:
    try:
        base_clean = base.rstrip("/")
        wfs_url = base_clean if base_clean.endswith("/wfs") else f"{base_clean}/wfs"
    except Exception:
        wfs_url = f"{base.rstrip('/')}/wfs"

    query = urlencode(params, doseq=True)
    return f"{wfs_url}?{query}"


def _fetch_wfs(url: str) -> Tuple[bytes, int, str]:
    """
    Fetch WFS data server-side.

    Returns:
      (content, status_code, content_type)
    """
    headers = {
        "Accept": "application/json",
    }
    headers.update(_wfs_auth_header())

    timeout = _cfg_float("WFS_PROXY_TIMEOUT", 20.0)

    try:
        import requests

        response = requests.get(url, headers=headers, timeout=timeout)
        content_type = response.headers.get("Content-Type", DEFAULT_WFS_OUTPUT_FORMAT)

        return response.content, response.status_code, content_type

    except Exception:
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        request_obj = Request(url, headers=headers)

        try:
            with urlopen(request_obj, timeout=timeout) as response:
                content = response.read()
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", DEFAULT_WFS_OUTPUT_FORMAT)
                return content, status, content_type

        except HTTPError as exc:
            content = exc.read()
            content_type = exc.headers.get("Content-Type", DEFAULT_WFS_OUTPUT_FORMAT)
            return content, exc.code, content_type


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@bp.get("/ui/chat/<chat_id>/map")
def map_page(chat_id: str) -> Response:
    """
    Iframe target for the map workspace.

    Redirects directly to the OpenLayer microservice. No double iframe.

    Important:
    - Uses OPENLAYER_PUBLIC_URL, never OPENLAYER_INTERNAL_URL.
    - Adds embed=1 and chat_id for app-shell integration.
    - Repairs stale localhost:8090 config to localhost:5190 by default.
    """
    conv = _ensure_conversation(chat_id)

    try:
        target = _build_openlayer_target(str(conv.id))
        return _redirect_response(target)

    except Exception as exc:
        _log_exception("map_page redirect failed", exc)
        return _json_error(
            message=str(exc),
            status=500,
            code="map_redirect_failed",
            chat_id=str(conv.id),
            extra={
                "openlayer": {
                    "public_url": _safe_openlayer_public_url(),
                    "internal_url_configured": bool(_safe_openlayer_internal_url()),
                    "route": _safe_openlayer_route(),
                    "expected_public_url": DEFAULT_OPENLAYER_PUBLIC_URL,
                    "expected_route": DEFAULT_OPENLAYER_ROUTE,
                }
            },
        )


@bp.get("/ui/chat/<chat_id>/map.json")
def map_json(chat_id: str) -> Response:
    """
    Return non-secret map configuration for the frontend.

    This endpoint intentionally exposes only browser-safe values.
    Internal URLs and credentials are not returned.
    """
    conv = _ensure_conversation(chat_id)

    try:
        min_zoom = _map_min_zoom()
        max_zoom = _map_max_zoom()

        zoom = _clamp_int(
            _cfg_int("MAP_DEFAULT_ZOOM", DEFAULT_MAP_ZOOM),
            min_zoom,
            max_zoom,
        )

        center = _get_default_center()
        allowed_typenames = _cfg_list("WFS_ALLOWED_TYPENAMES", DEFAULT_ALLOWED_TYPENAMES)

        public_url = _safe_openlayer_public_url()
        route = _safe_openlayer_route()
        iframe_url = _build_openlayer_target(str(conv.id))

        payload = {
            "ok": True,
            "chat_id": str(conv.id),
            "mode": "map",
            "workspace_mode": "map",
            "view": {
                "center": center,
                "lon": center[0],
                "lat": center[1],
                "zoom": zoom,
                "min_zoom": min_zoom,
                "max_zoom": max_zoom,
            },
            "iframe_defaults": {
                "embed": True,
                "mouse_wheel_zoom_enabled": _cfg_bool("MAP_MOUSE_WHEEL_ZOOM", True),
                "scroll": _cfg_str("MAP_IFRAME_SCROLL_DEFAULT", "1"),
                "style_delegated_to_openlayer_service": True,
            },
            "openlayer": {
                "public_url": public_url,
                "route": route,
                "map_path": route,
                "iframe_url": iframe_url,
                "embed_enabled": _cfg_bool("OPENLAYER_EMBED_ENABLED", True),
                "expected_public_url": DEFAULT_OPENLAYER_PUBLIC_URL,
                "legacy_public_url_repaired": _is_known_legacy_openlayer_public_url(
                    _cfg_str("OPENLAYER_PUBLIC_URL", "")
                ),
            },
            "wfs": {
                "proxy_url": f"/ui/chat/{conv.id}/wfs",
                "allowed_typeNames": allowed_typenames,
                "srsName": _cfg_str("WFS_DEFAULT_SRS", DEFAULT_WFS_SRS),
                "featureProjection": "EPSG:3857",
                "outputFormat": DEFAULT_WFS_OUTPUT_FORMAT,
            },
            "security": {
                "frame_ancestors": _frame_ancestors_value(),
                "wildcard_frame_ancestors": False,
            },
            "legacy_3d_backend": False,
        }

        resp = make_response(jsonify(payload), 200)
        _cache_headers(resp, strong=False)
        _apply_frame_headers(resp, allow_embed=True)

        return resp

    except Exception as exc:
        _log_exception("map_json failed", exc)
        return _json_error(
            message=str(exc),
            status=500,
            code="map_json_failed",
            chat_id=str(conv.id),
        )


@bp.get("/ui/chat/<chat_id>/wfs")
def wfs_proxy(chat_id: str) -> Response:
    """
    Safe server-side WFS proxy.

    Security:
    - GET only
    - query parameter allowlist
    - typeNames/typeName allowlist
    - server-side credentials only
    - no credential leakage to frontend
    """
    conv = _ensure_conversation(chat_id)

    base = _wfs_base()
    if not base:
        return _json_error(
            message="WFS base not configured",
            status=500,
            code="wfs_base_missing",
            chat_id=str(conv.id),
        )

    try:
        safe_params, error = _sanitize_wfs_params()

        if error or not safe_params:
            return _json_error(
                message=error or "invalid WFS params",
                status=400,
                code="invalid_wfs_params",
                chat_id=str(conv.id),
            )

        wfs_url = _build_wfs_url(base, safe_params)
        content, status_code, content_type = _fetch_wfs(wfs_url)

        resp = make_response(content, status_code)
        resp.headers["Content-Type"] = content_type or DEFAULT_WFS_OUTPUT_FORMAT

        _cache_headers(resp, strong=False)

        return resp

    except Exception as exc:
        _log_exception("wfs_proxy failed", exc)
        return _json_error(
            message=str(exc),
            status=500,
            code="wfs_proxy_failed",
            chat_id=str(conv.id),
        )