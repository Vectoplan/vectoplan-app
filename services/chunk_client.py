# services/vectoplan-app/services/chunk_client.py
"""Robuster interner Client für ``vectoplan-app -> vectoplan-chunk``.

Der Client ist ausschließlich ein HTTP-/Vertragsadapter. Er schreibt niemals
in die Chunk-Datenbank und besitzt keine App-Datenbanktransaktion.

Harte Invarianten
-----------------
* Backend-Aufrufe verwenden ausschließlich ``VECTOPLAN_CHUNK_INTERNAL_URL``.
* Browser-/Public-URLs werden nie für Provisionierung oder Access-Sync genutzt.
* Als Projekt-Owner wird ausschließlich die kanonische ``auth_user_id`` aus
  ``vectoplan-auth`` übertragen; eine lokale ``AppUser.id`` ist kein gültiger
  Chunk-Principal.
* Neue App-Projekte fordern standardmäßig ``earth`` an und dürfen nur bei
  explizit erlaubten fachlichen Earth-Fehlern auf ``flat`` zurückfallen.
* Timeouts, DNS-, Transport-, HTTP-5xx- und Datenbankfehler werden niemals als
  erfolgreicher Flat-Fallback maskiert.
* Mutationen sind nur dann automatisch wiederholbar, wenn sie idempotent sind
  oder einen stabilen Idempotency-Key besitzen.
* Service-Credentials, Cookies und Authorization-Header werden weder geloggt
  noch serialisiert.
* GET-Status-/Access-Antworten dürfen sehr kurz prozesslokal gecacht werden;
  Mutationen werden nie aus dem Cache beantwortet und invalidieren betroffene
  Access-Caches.

Bevorzugter Provisionierungsvertrag
-----------------------------------
``project_chunk_provisioning_service.py`` ruft diese Funktion auf::

    provision_chunk_project_for_app_project(
        app_project_public_id=...,
        owner_user_id=...,
        requested_world_template="earth",
        fallback_world_template="flat",
        allow_world_fallback=True,
        earth_reference={...} | None,
        idempotency_key=...,
        request_id=...,
    )

Die älteren öffentlichen Helfer bleiben rückwärtskompatibel exportiert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover - Flask ist im produktiven Lauf vorhanden.
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[no-redef]
        return False


LOGGER = logging.getLogger(__name__)

CLIENT_VERSION = "2.0.0"
PROVISIONING_CONTRACT_VERSION = "app-chunk-provisioning-policy.v1"
ACCESS_SYNC_CONTRACT_VERSION = "app-chunk-access-sync.v1"
RESULT_CONTRACT_VERSION = "app-chunk-client-result.v2"

DEFAULT_CHUNK_INTERNAL_URL = "http://vectoplan-chunk:5000"
DEFAULT_CHUNK_PUBLIC_URL = "http://localhost:5102"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_SECONDS = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_USER_AGENT = "vectoplan-app/chunk-client"
DEFAULT_SERVICE_ID = "vectoplan-app"
DEFAULT_REQUESTED_WORLD_TEMPLATE = "earth"
DEFAULT_FALLBACK_WORLD_TEMPLATE = "flat"
DEFAULT_WORLD_ID = "world_spawn"
DEFAULT_EARTH_CRS_ID = "EPSG:4979"
DEFAULT_EARTH_HEIGHT = 0.0
DEFAULT_CACHE_TTL_SECONDS = 5.0
DEFAULT_CACHE_MAX_ENTRIES = 512

SUPPORTED_WORLD_TEMPLATES = frozenset({"earth", "flat"})
SUPPORTED_PROJECT_ROLES = frozenset({"owner", "admin", "editor", "viewer"})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
SUCCESS_HTTP_STATUSES = range(200, 300)

TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on", "enabled", "ja"})
FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off", "disabled", "nein"})

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "raw_key",
    "service_key",
)

FALLBACK_ELIGIBLE_CODES = frozenset(
    {
        "coordinates_unavailable",
        "earth_reference_missing",
        "earth_reference_required",
        "earth_reference_incomplete",
        "earth_reference_invalid",
        "earth_reference_not_available",
        "invalid_earth_reference",
        "project_coordinates_unavailable",
        "unsupported_coordinate_reference",
    }
)


# ---------------------------------------------------------------------------
# Defensive primitives
# ---------------------------------------------------------------------------


def _safe_str(value: Any, default: str = "", max_length: Optional[int] = None) -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        if max_length is not None and max_length > 0:
            return text[:max_length]
        return text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return bool(value)
        text = _safe_str(value).lower()
        if text in TRUE_VALUES:
            return True
        if text in FALSE_VALUES:
            return False
        return default
    except Exception:
        return default


def _safe_int(
    value: Any,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        if isinstance(value, bool):
            result = int(default)
        else:
            result = int(value)
    except Exception:
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def _safe_float(
    value: Any,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        if isinstance(value, bool):
            result = float(default)
        else:
            result = float(value)
    except Exception:
        result = float(default)
    if minimum is not None:
        result = max(float(minimum), result)
    if maximum is not None:
        result = min(float(maximum), result)
    return result


def _safe_mapping(value: Any) -> dict[str, Any]:
    try:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if is_dataclass(value):
            raw = asdict(value)
            return dict(raw) if isinstance(raw, Mapping) else {}
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            return dict(raw) if isinstance(raw, Mapping) else {}
        return {}
    except Exception:
        return {}


def _safe_sequence(value: Any) -> list[Any]:
    try:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        return [value]
    except Exception:
        return []


def _json_safe(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return _safe_str(value, "", 500)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = _safe_str(key, "", 200)
            if not text_key:
                continue
            result[text_key] = _json_safe(item, depth=depth - 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth - 1) for item in value]
    try:
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return isoformat()
    except Exception:
        pass
    if is_dataclass(value):
        try:
            return _json_safe(asdict(value), depth=depth - 1)
        except Exception:
            pass
    return _safe_str(value, repr(value), 2000)


def _redact(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return "<max-depth>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = _safe_str(key, "", 200)
            lowered = text_key.lower().replace("-", "_")
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                result[text_key] = "<redacted>"
            else:
                result[text_key] = _redact(item, depth=depth - 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item, depth=depth - 1) for item in value]
    return _json_safe(value, depth=depth)


def _env(name: str, default: Any = None) -> Any:
    try:
        return os.environ.get(name, default)
    except Exception:
        return default


def _config_value(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None and name in current_app.config:
            return current_app.config.get(name)
    except Exception:
        pass
    return _env(name, default)


def _config_first(names: Sequence[str], default: Any = None) -> Any:
    try:
        for name in names:
            value = _config_value(name, None)
            if value is not None and _safe_str(value) != "":
                return value
        return default
    except Exception:
        return default


def _config_str(name: str, default: str = "") -> str:
    return _safe_str(_config_value(name, default), default)


def _config_str_first(names: Sequence[str], default: str = "") -> str:
    return _safe_str(_config_first(names, default), default)


def _config_bool(name: str, default: bool = False) -> bool:
    return _safe_bool(_config_value(name, default), default)


def _config_bool_first(names: Sequence[str], default: bool = False) -> bool:
    return _safe_bool(_config_first(names, default), default)


def _config_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    return _safe_int(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _config_float(
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    return _safe_float(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _duration_ms(started: float) -> int:
    try:
        return max(0, int((time.monotonic() - started) * 1000))
    except Exception:
        return 0


def _normalize_base_url(url: Any, default: str) -> str:
    text = _safe_str(url, default).rstrip("/")
    return text or default.rstrip("/")


def _normalize_path(path: Any, default: str = "/") -> str:
    text = _safe_str(path, default)
    if not text.startswith("/"):
        text = "/" + text
    while "//" in text:
        text = text.replace("//", "/")
    return text or default


def _join_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url, DEFAULT_CHUNK_INTERNAL_URL)}{_normalize_path(path)}"


def _quote_segment(value: Any) -> str:
    return quote(_safe_str(value), safe="")


def _format_path(template: str, **values: Any) -> str:
    result = _normalize_path(template)
    try:
        encoded = {key: _quote_segment(value) for key, value in values.items()}
        return _normalize_path(result.format(**encoded))
    except Exception as exc:
        raise ChunkClientConfigurationError(
            "chunk_path_format_failed",
            f"Could not format chunk route: {template}",
            details={"template": template, "keys": sorted(values.keys())},
            cause=exc,
        ) from exc


def _world_template(value: Any, default: str = DEFAULT_REQUESTED_WORLD_TEMPLATE) -> str:
    text = _safe_str(value, default, 40).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "globe": "earth",
        "global": "earth",
        "wgs84": "earth",
        "wgs_84": "earth",
        "local": "flat",
        "plane": "flat",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in SUPPORTED_WORLD_TEMPLATES else default


def _project_role(value: Any, default: str = "viewer") -> str:
    text = _safe_str(value, default, 40).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "read": "viewer",
        "readonly": "viewer",
        "read_only": "viewer",
        "write": "editor",
        "member": "editor",
        "manager": "admin",
        "administrator": "admin",
    }
    normalized = aliases.get(text, text)
    if normalized not in SUPPORTED_PROJECT_ROLES:
        raise ValueError(f"Unsupported chunk project role: {text or '<empty>'}")
    return normalized


def _canonical_user_id(value: Any, *, required: bool = True) -> Optional[str]:
    text = _safe_str(value, "", 240)
    if not text:
        if required:
            raise ValueError("A canonical auth user id is required.")
        return None
    lowered = text.lower()
    if lowered in {"guest", "anonymous", "none", "null", "demo", "0"}:
        raise ValueError("Guest/demo identities are not valid persistent Chunk user ids.")
    return text


def _request_id(value: Any = None) -> str:
    text = _safe_str(value, "", 160)
    return text or f"req_{uuid.uuid4().hex}"


def _idempotency_key(value: Any = None, *, seed: Optional[str] = None) -> Optional[str]:
    text = _safe_str(value, "", 240)
    if text:
        return text
    if seed:
        digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
        return f"idem_{digest[:48]}"
    return None


def _build_external_app_project_url(app_project_public_id: str) -> Optional[str]:
    public_url = _config_str_first(
        ("VECTOPLAN_APP_PUBLIC_URL", "VECTOPLAN_APP_PUBLIC_BASE_URL", "APP_PUBLIC_URL"),
        "",
    )
    if not public_url:
        return None
    return f"{public_url.rstrip('/')}/project={quote(app_project_public_id, safe='')}"


def _parse_json_text(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _read_response_body(response: Any, *, max_bytes: int) -> tuple[str, bool]:
    try:
        raw = response.read(max_bytes + 1)
    except Exception:
        return "", False
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    try:
        return raw.decode("utf-8", errors="replace"), truncated
    except Exception:
        return "", truncated


def _headers_to_dict(headers: Any) -> dict[str, str]:
    try:
        if isinstance(headers, Message):
            return {str(key): str(value) for key, value in headers.items()}
        if isinstance(headers, Mapping):
            return {str(key): str(value) for key, value in headers.items()}
        items = getattr(headers, "items", None)
        if callable(items):
            return {str(key): str(value) for key, value in items()}
    except Exception:
        pass
    return {}


def _parse_retry_after(headers: Mapping[str, Any]) -> Optional[float]:
    raw = None
    for key, value in headers.items():
        if str(key).lower() == "retry-after":
            raw = value
            break
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
        return max(0.0, min(60.0, seconds))
    except Exception:
        return None


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, URLError, ConnectionError)):
        return True
    lowered = _safe_str(exc).lower()
    return any(
        marker in lowered
        for marker in (
            "timed out",
            "timeout",
            "connection refused",
            "connection reset",
            "temporary failure",
            "name or service not known",
            "nodename nor servname",
            "network is unreachable",
        )
    )


def _error_code_for_exception(exc: BaseException) -> str:
    lowered = _safe_str(exc).lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in lowered or "timeout" in lowered:
        return "chunk_service_timeout"
    if "name or service not known" in lowered or "nodename nor servname" in lowered or "gaierror" in lowered:
        return "chunk_service_dns_failed"
    if "connection refused" in lowered:
        return "chunk_service_connection_refused"
    if isinstance(exc, URLError):
        return "chunk_service_unreachable"
    return "chunk_request_exception"


def _error_from_payload(
    payload: Mapping[str, Any],
    *,
    status_code: int,
    default_code: str,
    default_message: str,
) -> dict[str, Any]:
    error = _safe_mapping(payload.get("error"))
    data = _safe_mapping(payload.get("data"))
    nested_error = _safe_mapping(data.get("error"))
    source = error or nested_error
    code = _safe_str(
        source.get("code")
        or payload.get("code")
        or data.get("code")
        or default_code,
        default_code,
        160,
    )
    message = _safe_str(
        source.get("message")
        or payload.get("message")
        or data.get("message")
        or default_message,
        default_message,
        2000,
    )
    retryable = _safe_bool(
        source.get("retryable", payload.get("retryable", status_code in RETRYABLE_HTTP_STATUSES)),
        status_code in RETRYABLE_HTTP_STATUSES,
    )
    details = _redact(source.get("details") or data.get("details") or {})
    return {
        "code": code,
        "message": message,
        "statusCode": status_code,
        "retryable": retryable,
        "details": details,
    }


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapping = _safe_mapping(value)
        if mapping:
            return mapping
    return {}


def _nested_payloads(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [dict(payload)]
    for key in ("data", "result", "preview", "bootstrap", "context"):
        nested = _safe_mapping(payload.get(key))
        if nested:
            result.append(nested)
    return result


def _extract_payload_ids(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = _nested_payloads(payload)
    ids_candidates = [_safe_mapping(item.get("ids")) for item in candidates]
    projects = [_safe_mapping(item.get("project")) for item in candidates]
    universes = [_safe_mapping(item.get("universe")) for item in candidates]
    worlds: list[dict[str, Any]] = []
    for item in candidates:
        worlds.extend(
            [
                _safe_mapping(item.get("spawnWorld")),
                _safe_mapping(item.get("world")),
            ]
        )

    def find(keys: Sequence[str], collections: Iterable[Mapping[str, Any]]) -> Any:
        for collection in collections:
            for key in keys:
                value = collection.get(key)
                if value not in (None, ""):
                    return value
        return None

    return {
        key: value
        for key, value in {
            "externalAppProjectId": find(
                ("externalAppProjectId", "appProjectPublicId", "app_project_public_id"),
                [*ids_candidates, *candidates, *projects],
            ),
            "chunkProjectId": find(
                ("chunkProjectId", "projectId", "publicId", "public_id"),
                [*ids_candidates, *candidates, *projects],
            ),
            "chunkUniverseId": find(
                ("chunkUniverseId", "universeId", "publicId", "public_id"),
                [*ids_candidates, *candidates, *universes],
            ),
            "chunkWorldId": find(
                (
                    "chunkWorldId",
                    "spawnWorldId",
                    "defaultWorldId",
                    "worldId",
                    "publicId",
                    "public_id",
                ),
                [*ids_candidates, *candidates, *worlds],
            ),
        }.items()
        if value not in (None, "")
    }


def _extract_route_hints(payload: Mapping[str, Any]) -> dict[str, Any]:
    for item in _nested_payloads(payload):
        hints = _safe_mapping(item.get("routeHints") or item.get("route_hints"))
        if hints:
            return hints
    return {}


def _extract_access(payload: Mapping[str, Any]) -> dict[str, Any]:
    for item in _nested_payloads(payload):
        access = _safe_mapping(item.get("access") or item.get("projectAccess"))
        if access:
            return access
    return {}


def _extract_world_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = _nested_payloads(payload)
    worlds: list[dict[str, Any]] = []
    for item in candidates:
        worlds.extend([_safe_mapping(item.get("spawnWorld")), _safe_mapping(item.get("world"))])
    effective = _world_template(
        _first(
            *[item.get("effectiveWorldTemplate") for item in candidates],
            *[item.get("worldTemplate") for item in candidates],
            *[world.get("templateId") for world in worlds],
            *[world.get("providerId") for world in worlds],
            DEFAULT_REQUESTED_WORLD_TEMPLATE,
        )
    )
    requested = _world_template(
        _first(*[item.get("requestedWorldTemplate") for item in candidates], effective),
        effective,
    )
    fallback = _world_template(
        _first(*[item.get("fallbackWorldTemplate") for item in candidates], DEFAULT_FALLBACK_WORLD_TEMPLATE),
        DEFAULT_FALLBACK_WORLD_TEMPLATE,
    )
    fallback_reason = _safe_str(
        _first(*[item.get("fallbackReason") for item in candidates]),
        "",
        160,
    ) or None
    fallback_used = _safe_bool(
        _first(*[item.get("fallbackUsed") for item in candidates]),
        effective != requested,
    ) or effective != requested
    fingerprint = _safe_str(
        _first(
            *[item.get("earthReferenceFingerprint") for item in candidates],
            *[world.get("globalReferenceFingerprint") for world in worlds],
            *[world.get("earthReferenceFingerprint") for world in worlds],
        ),
        "",
        160,
    ) or None
    return {
        "requestedWorldTemplate": requested,
        "fallbackWorldTemplate": fallback,
        "effectiveWorldTemplate": effective,
        "fallbackUsed": bool(fallback_used),
        "fallbackReason": fallback_reason,
        "earthReferenceFingerprint": fingerprint,
    }


def _response_ok(payload: Mapping[str, Any], status_code: int) -> bool:
    if status_code not in SUCCESS_HTTP_STATUSES:
        return False
    for item in _nested_payloads(payload):
        if item.get("ok") is False:
            return False
    return True


# ---------------------------------------------------------------------------
# Tiny bounded process cache for status/read operations
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    expires_at: float
    value: "ChunkClientResult"


_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, _CacheEntry] = {}


def _cache_get(key: str) -> Optional["ChunkClientResult"]:
    if not key:
        return None
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            _CACHE.pop(key, None)
            return None
        result = entry.value.clone()
        result.cached = True
        return result


def _cache_set(key: str, value: "ChunkClientResult", *, ttl_seconds: float, max_entries: int) -> None:
    if not key or ttl_seconds <= 0 or not value.ok:
        return
    with _CACHE_LOCK:
        now = time.monotonic()
        for expired in [name for name, entry in _CACHE.items() if entry.expires_at <= now]:
            _CACHE.pop(expired, None)
        _CACHE[key] = _CacheEntry(now + ttl_seconds, value.clone())
        if len(_CACHE) > max_entries:
            oldest = sorted(_CACHE.items(), key=lambda item: item[1].expires_at)
            for name, _ in oldest[: len(_CACHE) - max_entries]:
                _CACHE.pop(name, None)


def clear_chunk_client_cache(prefix: Optional[str] = None) -> int:
    """Entfernt prozesslokale Status-/Access-Cacheeinträge."""
    with _CACHE_LOCK:
        if not prefix:
            count = len(_CACHE)
            _CACHE.clear()
            return count
        keys = [key for key in _CACHE if key.startswith(prefix)]
        for key in keys:
            _CACHE.pop(key, None)
        return len(keys)


def _cache_key(method: str, path: str, query: Optional[Mapping[str, Any]]) -> str:
    canonical_query = json.dumps(_json_safe(dict(query or {})), sort_keys=True, separators=(",", ":"))
    return f"{method.upper()}:{path}:{canonical_query}"


# ---------------------------------------------------------------------------
# Config, result and errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkClientConfig:
    internal_url: str = DEFAULT_CHUNK_INTERNAL_URL
    public_url: str = DEFAULT_CHUNK_PUBLIC_URL
    enabled: bool = True
    required: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retries: int = DEFAULT_RETRIES
    retry_seconds: float = DEFAULT_RETRY_SECONDS
    retry_jitter_seconds: float = 0.15
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    requested_world_template: str = DEFAULT_REQUESTED_WORLD_TEMPLATE
    fallback_world_template: str = DEFAULT_FALLBACK_WORLD_TEMPLATE
    allow_world_fallback: bool = True
    allow_client_side_fallback: bool = True
    allow_world_template_change: bool = False
    default_world_id: str = DEFAULT_WORLD_ID
    earth_crs_id: str = DEFAULT_EARTH_CRS_ID
    default_earth_height: float = DEFAULT_EARTH_HEIGHT
    user_agent: str = DEFAULT_USER_AGENT
    service_id: str = DEFAULT_SERVICE_ID
    service_api_key: Optional[str] = None
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES
    access_sync_enabled: bool = True
    access_sync_required: bool = False
    access_sync_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    access_sync_retries: int = DEFAULT_RETRIES
    access_sync_retry_seconds: float = DEFAULT_RETRY_SECONDS
    ensure_by_app_path: str = "/projects/by-app/{app_project_public_id}"
    ensure_path: str = "/projects/ensure"
    preview_by_app_path: str = "/projects/preview/by-app/{app_project_public_id}"
    status_path: str = "/projects/_status"
    access_path: str = "/projects/{chunk_project_id}/access"
    access_initialize_path: str = "/projects/{chunk_project_id}/access/initialize"
    assignments_path: str = "/projects/{chunk_project_id}/assignments"
    transfer_owner_path: str = "/projects/{chunk_project_id}/access/transfer-owner"

    @property
    def default_template_id(self) -> str:
        """Legacy-Alias."""
        return self.requested_world_template

    @property
    def internal_token(self) -> Optional[str]:
        """Legacy-Alias."""
        return self.service_api_key

    @classmethod
    def from_app(cls) -> "ChunkClientConfig":
        internal_url = _normalize_base_url(
            _config_str_first(
                (
                    "VECTOPLAN_CHUNK_INTERNAL_URL",
                    "VECTOPLAN_CHUNK_INTERNAL_BASE_URL",
                    "CHUNK_INTERNAL_URL",
                ),
                DEFAULT_CHUNK_INTERNAL_URL,
            ),
            DEFAULT_CHUNK_INTERNAL_URL,
        )
        public_url = _normalize_base_url(
            _config_str_first(
                (
                    "VECTOPLAN_CHUNK_PUBLIC_URL",
                    "VECTOPLAN_CHUNK_PUBLIC_BASE_URL",
                    "CHUNK_PUBLIC_URL",
                ),
                DEFAULT_CHUNK_PUBLIC_URL,
            ),
            DEFAULT_CHUNK_PUBLIC_URL,
        )
        requested = _world_template(
            _config_str_first(
                (
                    "VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE",
                    "VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
                ),
                DEFAULT_REQUESTED_WORLD_TEMPLATE,
            ),
            DEFAULT_REQUESTED_WORLD_TEMPLATE,
        )
        fallback = _world_template(
            _config_str_first(
                (
                    "VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE",
                    "VECTOPLAN_CHUNK_PROVISION_FALLBACK_TEMPLATE_ID",
                ),
                DEFAULT_FALLBACK_WORLD_TEMPLATE,
            ),
            DEFAULT_FALLBACK_WORLD_TEMPLATE,
        )
        if fallback == requested:
            fallback = "flat" if requested == "earth" else "earth"
        service_key = _config_str_first(
            (
                "VECTOPLAN_APP_CHUNK_SERVICE_API_KEY",
                "VECTOPLAN_CHUNK_SERVICE_API_KEY",
                "VECTOPLAN_CHUNK_INTERNAL_TOKEN",
                "VECTOPLAN_CHUNK_API_TOKEN",
            ),
            "",
        )
        timeout = _config_float(
            "VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS",
            DEFAULT_TIMEOUT_SECONDS,
            minimum=0.1,
            maximum=120.0,
        )
        retries = _config_int(
            "VECTOPLAN_CHUNK_PROVISION_RETRIES",
            DEFAULT_RETRIES,
            minimum=0,
            maximum=10,
        )
        retry_seconds = _config_float(
            "VECTOPLAN_CHUNK_PROVISION_RETRY_SECONDS",
            DEFAULT_RETRY_SECONDS,
            minimum=0.0,
            maximum=60.0,
        )
        return cls(
            internal_url=internal_url,
            public_url=public_url,
            enabled=_config_bool_first(
                (
                    "VECTOPLAN_APP_CHUNK_PROVISION_ON_PROJECT_CREATE",
                    "VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE",
                ),
                True,
            ),
            required=_config_bool_first(
                (
                    "VECTOPLAN_APP_CHUNK_PROVISIONING_REQUIRED",
                    "VECTOPLAN_CHUNK_PROVISION_REQUIRED",
                ),
                False,
            ),
            timeout_seconds=timeout,
            retries=retries,
            retry_seconds=retry_seconds,
            retry_jitter_seconds=_config_float(
                "VECTOPLAN_CHUNK_RETRY_JITTER_SECONDS",
                0.15,
                minimum=0.0,
                maximum=5.0,
            ),
            max_response_bytes=_config_int(
                "VECTOPLAN_CHUNK_MAX_RESPONSE_BYTES",
                DEFAULT_MAX_RESPONSE_BYTES,
                minimum=1024,
                maximum=64 * 1024 * 1024,
            ),
            requested_world_template=requested,
            fallback_world_template=fallback,
            allow_world_fallback=_config_bool_first(
                ("VECTOPLAN_APP_ALLOW_WORLD_FALLBACK", "VECTOPLAN_CHUNK_ALLOW_WORLD_FALLBACK"),
                True,
            ),
            allow_client_side_fallback=_config_bool_first(
                (
                    "VECTOPLAN_APP_CLIENT_SIDE_WORLD_FALLBACK",
                    "VECTOPLAN_CHUNK_CLIENT_SIDE_WORLD_FALLBACK",
                ),
                True,
            ),
            allow_world_template_change=_config_bool_first(
                (
                    "VECTOPLAN_APP_ALLOW_WORLD_TEMPLATE_CHANGE",
                    "VECTOPLAN_CHUNK_ALLOW_WORLD_TEMPLATE_CHANGE",
                ),
                False,
            ),
            default_world_id=_config_str(
                "VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID",
                DEFAULT_WORLD_ID,
            ),
            earth_crs_id=_config_str("VECTOPLAN_APP_EARTH_CRS_ID", DEFAULT_EARTH_CRS_ID).upper(),
            default_earth_height=_config_float(
                "VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT",
                DEFAULT_EARTH_HEIGHT,
                minimum=-100_000.0,
                maximum=1_000_000.0,
            ),
            user_agent=_config_str("VECTOPLAN_CHUNK_CLIENT_USER_AGENT", DEFAULT_USER_AGENT),
            service_id=_config_str_first(
                ("VECTOPLAN_APP_CHUNK_SERVICE_ID", "VECTOPLAN_CHUNK_SERVICE_ID"),
                DEFAULT_SERVICE_ID,
            ),
            service_api_key=service_key or None,
            cache_ttl_seconds=_config_float(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_CACHE_SECONDS",
                DEFAULT_CACHE_TTL_SECONDS,
                minimum=0.0,
                maximum=300.0,
            ),
            cache_max_entries=_config_int(
                "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES",
                DEFAULT_CACHE_MAX_ENTRIES,
                minimum=16,
                maximum=100_000,
            ),
            access_sync_enabled=_config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True),
            access_sync_required=_config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED", False),
            access_sync_timeout_seconds=_config_float(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_TIMEOUT_SECONDS",
                timeout,
                minimum=0.1,
                maximum=120.0,
            ),
            access_sync_retries=_config_int(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRIES",
                retries,
                minimum=0,
                maximum=10,
            ),
            access_sync_retry_seconds=_config_float(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_RETRY_SECONDS",
                retry_seconds,
                minimum=0.0,
                maximum=60.0,
            ),
            ensure_by_app_path=_config_str(
                "VECTOPLAN_CHUNK_PROVISION_API_PATH_ENSURE_BY_APP",
                "/projects/by-app/{app_project_public_id}",
            ),
            ensure_path=_config_str("VECTOPLAN_CHUNK_PROVISION_API_PATH_ENSURE", "/projects/ensure"),
            preview_by_app_path=_config_str(
                "VECTOPLAN_CHUNK_PROVISION_API_PATH_PREVIEW_BY_APP",
                "/projects/preview/by-app/{app_project_public_id}",
            ),
            status_path=_config_str("VECTOPLAN_CHUNK_STATUS_API_PATH", "/projects/_status"),
            access_path=_config_str(
                "VECTOPLAN_CHUNK_ACCESS_API_PATH",
                "/projects/{chunk_project_id}/access",
            ),
            access_initialize_path=_config_str(
                "VECTOPLAN_CHUNK_ACCESS_INITIALIZE_API_PATH",
                "/projects/{chunk_project_id}/access/initialize",
            ),
            assignments_path=_config_str(
                "VECTOPLAN_CHUNK_ASSIGNMENTS_API_PATH",
                "/projects/{chunk_project_id}/assignments",
            ),
            transfer_owner_path=_config_str(
                "VECTOPLAN_CHUNK_TRANSFER_OWNER_API_PATH",
                "/projects/{chunk_project_id}/access/transfer-owner",
            ),
        )

    def public_status(self) -> dict[str, Any]:
        return {
            "clientVersion": CLIENT_VERSION,
            "enabled": self.enabled,
            "required": self.required,
            "internalUrlConfigured": bool(self.internal_url),
            "publicUrlConfigured": bool(self.public_url),
            "serviceId": self.service_id,
            "serviceCredentialConfigured": bool(self.service_api_key),
            "timeoutSeconds": self.timeout_seconds,
            "retries": self.retries,
            "requestedWorldTemplate": self.requested_world_template,
            "fallbackWorldTemplate": self.fallback_world_template,
            "allowWorldFallback": self.allow_world_fallback,
            "allowClientSideFallback": self.allow_client_side_fallback,
            "defaultWorldId": self.default_world_id,
            "earthCrsId": self.earth_crs_id,
            "accessSyncEnabled": self.access_sync_enabled,
            "accessSyncRequired": self.access_sync_required,
            "cacheTtlSeconds": self.cache_ttl_seconds,
        }


@dataclass
class ChunkClientResult:
    ok: bool
    status_code: int
    method: str
    url: str
    path: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[dict[str, Any]] = None
    request_body: Optional[dict[str, Any]] = None
    response_headers: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    attempts: int = 1
    retryable: bool = False
    truncated: bool = False
    raw_text: str = ""
    request_id: str = ""
    correlation_id: str = ""
    idempotency_key: Optional[str] = None
    cached: bool = False

    @property
    def created(self) -> bool:
        return _safe_bool(_first(self.payload.get("created"), _safe_mapping(self.payload.get("data")).get("created")), False)

    @property
    def updated(self) -> bool:
        return _safe_bool(_first(self.payload.get("updated"), _safe_mapping(self.payload.get("data")).get("updated")), False)

    @property
    def reused(self) -> bool:
        return _safe_bool(_first(self.payload.get("reused"), _safe_mapping(self.payload.get("data")).get("reused")), False)

    @property
    def response_code(self) -> str:
        return _safe_str(
            _first(self.payload.get("code"), _safe_mapping(self.payload.get("data")).get("code")),
            "",
            160,
        )

    @property
    def message(self) -> str:
        if self.error:
            return _safe_str(self.error.get("message"), "", 2000)
        return _safe_str(
            _first(self.payload.get("message"), _safe_mapping(self.payload.get("data")).get("message")),
            "",
            2000,
        )

    @property
    def ids(self) -> dict[str, Any]:
        return _extract_payload_ids(self.payload)

    @property
    def route_hints(self) -> dict[str, Any]:
        return _extract_route_hints(self.payload)

    @property
    def access(self) -> dict[str, Any]:
        return _extract_access(self.payload)

    @property
    def world_contract(self) -> dict[str, Any]:
        return _extract_world_contract(self.payload)

    @property
    def external_app_project_id(self) -> Optional[str]:
        return _safe_str(self.ids.get("externalAppProjectId"), "", 240) or None

    @property
    def chunk_project_id(self) -> Optional[str]:
        return _safe_str(self.ids.get("chunkProjectId"), "", 240) or None

    @property
    def chunk_universe_id(self) -> Optional[str]:
        return _safe_str(self.ids.get("chunkUniverseId"), "", 240) or None

    @property
    def chunk_world_id(self) -> Optional[str]:
        return _safe_str(self.ids.get("chunkWorldId"), "", 240) or None

    def clone(self) -> "ChunkClientResult":
        return ChunkClientResult(
            ok=self.ok,
            status_code=self.status_code,
            method=self.method,
            url=self.url,
            path=self.path,
            payload=_safe_mapping(_json_safe(self.payload)),
            error=_safe_mapping(_json_safe(self.error)) or None,
            request_body=_safe_mapping(_json_safe(self.request_body)) or None,
            response_headers=dict(self.response_headers),
            duration_ms=self.duration_ms,
            attempts=self.attempts,
            retryable=self.retryable,
            truncated=self.truncated,
            raw_text=self.raw_text,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            idempotency_key=self.idempotency_key,
            cached=self.cached,
        )

    def to_dict(
        self,
        *,
        include_raw: bool = False,
        include_request_body: bool = False,
        include_internal_url: bool = False,
        include_headers: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "resultVersion": RESULT_CONTRACT_VERSION,
            "ok": self.ok,
            "statusCode": self.status_code,
            "method": self.method,
            "path": self.path,
            "payload": _redact(self.payload),
            "error": _redact(self.error),
            "durationMs": self.duration_ms,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "truncated": self.truncated,
            "requestId": self.request_id,
            "correlationId": self.correlation_id,
            "idempotencyKey": self.idempotency_key,
            "cached": self.cached,
            "created": self.created,
            "updated": self.updated,
            "reused": self.reused,
            "ids": self.ids,
            "routeHints": self.route_hints,
            "access": self.access,
            **self.world_contract,
        }
        if include_internal_url:
            result["url"] = self.url
        if include_request_body:
            result["requestBody"] = _redact(self.request_body)
        if include_headers:
            result["responseHeaders"] = {
                key: value
                for key, value in self.response_headers.items()
                if key.lower() not in {"set-cookie", "authorization", "proxy-authorization"}
            }
        if include_raw:
            result["rawText"] = self.raw_text[:20_000]
        return result


class ChunkClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 503,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
        result: Optional[ChunkClientResult] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = _safe_str(code, "chunk_client_error", 160)
        self.message = _safe_str(message, "Chunk client error.", 2000)
        self.status_code = _safe_int(status_code, 503, minimum=400, maximum=599)
        self.retryable = bool(retryable)
        self.details = _safe_mapping(_redact(details or {}))
        self.result = result
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "statusCode": self.status_code,
            "retryable": self.retryable,
            "details": self.details,
        }


class ChunkClientConfigurationError(ChunkClientError):
    pass


class ChunkClientContractError(ChunkClientError):
    pass


# ---------------------------------------------------------------------------
# HTTP adapter
# ---------------------------------------------------------------------------


class ChunkClient:
    """Kleiner, thread-sicher nutzbarer stdlib-HTTP-Client."""

    def __init__(
        self,
        config: Optional[ChunkClientConfig] = None,
        *,
        opener: Optional[Callable[..., Any]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config = config or ChunkClientConfig.from_app()
        self._opener = opener or urlopen
        self._sleep = sleep or time.sleep

    def _headers(
        self,
        *,
        request_id: str,
        correlation_id: str,
        idempotency_key: Optional[str],
        extra_headers: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self.config.user_agent,
            "X-Requested-With": "service",
            "X-VECTOPLAN-Service": self.config.service_id,
            "X-VECTOPLAN-Service-ID": self.config.service_id,
            "X-VECTOPLAN-Client": "vectoplan-app",
            "X-VECTOPLAN-Client-Version": CLIENT_VERSION,
            "X-Request-ID": request_id,
            "X-Correlation-ID": correlation_id,
            # Legacy header retained until all services use the canonical casing.
            "X-Vectoplan-Request-Id": request_id,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
            headers["X-Idempotency-Key"] = idempotency_key
        if self.config.service_api_key:
            headers["Authorization"] = f"Bearer {self.config.service_api_key}"
            headers["X-API-Key"] = self.config.service_api_key
            headers["X-Vectoplan-Internal-Token"] = self.config.service_api_key
        for key, value in (extra_headers or {}).items():
            text_key = _safe_str(key, "", 160)
            text_value = _safe_str(value, "", 2000)
            if text_key and text_value:
                headers[text_key] = text_value
        return headers

    def _sleep_before_retry(
        self,
        *,
        attempt: int,
        base_seconds: float,
        retry_after: Optional[float],
    ) -> None:
        try:
            if retry_after is not None:
                delay = retry_after
            else:
                delay = base_seconds * max(1, attempt)
                if self.config.retry_jitter_seconds > 0:
                    delay += random.uniform(0.0, self.config.retry_jitter_seconds)
            if delay > 0:
                self._sleep(min(delay, 60.0))
        except Exception:
            pass

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
        timeout_seconds: Optional[float] = None,
        retries: Optional[int] = None,
        retry_seconds: Optional[float] = None,
        cache_ttl_seconds: float = 0.0,
        extra_headers: Optional[Mapping[str, Any]] = None,
    ) -> ChunkClientResult:
        method = _safe_str(method, "GET", 16).upper()
        path = _normalize_path(path)
        req_id = _request_id(request_id)
        corr_id = _request_id(correlation_id or req_id)
        idem_key = _idempotency_key(idempotency_key)

        if not self.config.internal_url:
            result = ChunkClientResult(
                ok=False,
                status_code=0,
                method=method,
                url="",
                path=path,
                error={
                    "code": "chunk_client_not_configured",
                    "message": "VECTOPLAN_CHUNK_INTERNAL_URL is not configured.",
                    "statusCode": 503,
                    "retryable": True,
                },
                request_body=_safe_mapping(_json_safe(body or {})),
                request_id=req_id,
                correlation_id=corr_id,
                idempotency_key=idem_key,
                retryable=True,
            )
            if raise_on_error:
                raise ChunkClientConfigurationError(
                    "chunk_client_not_configured",
                    result.message,
                    retryable=True,
                    result=result,
                )
            return result

        filtered_query = {
            str(key): value
            for key, value in (query or {}).items()
            if value not in (None, "")
        }
        url = _join_url(self.config.internal_url, path)
        if filtered_query:
            url = f"{url}?{urlencode(filtered_query, doseq=True)}"

        cache_key = ""
        if method in READ_METHODS and cache_ttl_seconds > 0:
            cache_key = _cache_key(method, path, filtered_query)
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached

        request_body = _safe_mapping(_json_safe(body or {})) if body is not None else None
        encoded_body: Optional[bytes] = None
        if request_body is not None:
            try:
                encoded_body = json.dumps(
                    request_body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except Exception as exc:
                result = ChunkClientResult(
                    ok=False,
                    status_code=0,
                    method=method,
                    url=url,
                    path=path,
                    error={
                        "code": "chunk_request_body_invalid",
                        "message": "Chunk request body is not JSON serializable.",
                        "statusCode": 400,
                        "retryable": False,
                        "details": {"exceptionType": type(exc).__name__},
                    },
                    request_body=request_body,
                    request_id=req_id,
                    correlation_id=corr_id,
                    idempotency_key=idem_key,
                )
                if raise_on_error:
                    raise ChunkClientContractError(
                        "chunk_request_body_invalid",
                        result.message,
                        status_code=400,
                        result=result,
                        cause=exc,
                    ) from exc
                return result

        effective_timeout = _safe_float(
            timeout_seconds,
            self.config.timeout_seconds,
            minimum=0.1,
            maximum=120.0,
        )
        effective_retries = _safe_int(
            retries,
            self.config.retries,
            minimum=0,
            maximum=10,
        )
        effective_retry_seconds = _safe_float(
            retry_seconds,
            self.config.retry_seconds,
            minimum=0.0,
            maximum=60.0,
        )
        retry_safe = method in IDEMPOTENT_METHODS or bool(idem_key)
        max_attempts = max(1, effective_retries + 1 if retry_safe else 1)
        last_result: Optional[ChunkClientResult] = None

        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            try:
                request = Request(
                    url,
                    data=encoded_body,
                    headers=self._headers(
                        request_id=req_id,
                        correlation_id=corr_id,
                        idempotency_key=idem_key,
                        extra_headers=extra_headers,
                    ),
                    method=method,
                )
                with self._opener(request, timeout=effective_timeout) as response:
                    status_code = int(getattr(response, "status", 200) or 200)
                    text, truncated = _read_response_body(
                        response,
                        max_bytes=self.config.max_response_bytes,
                    )
                    headers = _headers_to_dict(getattr(response, "headers", {}))
                    payload = _parse_json_text(text)

                ok = _response_ok(payload, status_code)
                retryable = status_code in RETRYABLE_HTTP_STATUSES
                error = None
                if not ok:
                    error = _error_from_payload(
                        payload,
                        status_code=status_code,
                        default_code="chunk_request_failed",
                        default_message="Chunk service request failed.",
                    )
                    retryable = _safe_bool(error.get("retryable"), retryable)

                result = ChunkClientResult(
                    ok=ok,
                    status_code=status_code,
                    method=method,
                    url=url,
                    path=path,
                    payload=payload,
                    error=error,
                    request_body=request_body,
                    response_headers=headers,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    truncated=truncated,
                    raw_text=text,
                    request_id=req_id,
                    correlation_id=corr_id,
                    idempotency_key=idem_key,
                )
                last_result = result
                if result.ok:
                    if cache_key:
                        _cache_set(
                            cache_key,
                            result,
                            ttl_seconds=cache_ttl_seconds,
                            max_entries=self.config.cache_max_entries,
                        )
                    return result
                if retryable and retry_safe and attempt < max_attempts:
                    self._sleep_before_retry(
                        attempt=attempt,
                        base_seconds=effective_retry_seconds,
                        retry_after=_parse_retry_after(headers),
                    )
                    continue
                if raise_on_error:
                    raise ChunkClientError(
                        _safe_str(error.get("code") if error else None, "chunk_request_failed"),
                        result.message or "Chunk request failed.",
                        status_code=status_code if status_code >= 400 else 502,
                        retryable=retryable,
                        details=error.get("details") if error else None,
                        result=result,
                    )
                return result

            except ChunkClientError:
                raise
            except HTTPError as exc:
                status_code = int(getattr(exc, "code", 0) or 0)
                text, truncated = _read_response_body(exc, max_bytes=self.config.max_response_bytes)
                headers = _headers_to_dict(getattr(exc, "headers", {}))
                payload = _parse_json_text(text)
                error = _error_from_payload(
                    payload,
                    status_code=status_code,
                    default_code="chunk_http_error",
                    default_message=_safe_str(getattr(exc, "reason", None), "HTTP error from chunk service."),
                )
                retryable = _safe_bool(error.get("retryable"), status_code in RETRYABLE_HTTP_STATUSES)
                result = ChunkClientResult(
                    ok=False,
                    status_code=status_code,
                    method=method,
                    url=url,
                    path=path,
                    payload=payload,
                    error=error,
                    request_body=request_body,
                    response_headers=headers,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    truncated=truncated,
                    raw_text=text,
                    request_id=req_id,
                    correlation_id=corr_id,
                    idempotency_key=idem_key,
                )
                last_result = result
                if retryable and retry_safe and attempt < max_attempts:
                    self._sleep_before_retry(
                        attempt=attempt,
                        base_seconds=effective_retry_seconds,
                        retry_after=_parse_retry_after(headers),
                    )
                    continue
                if raise_on_error:
                    raise ChunkClientError(
                        _safe_str(error.get("code"), "chunk_http_error"),
                        result.message or "Chunk HTTP error.",
                        status_code=status_code if status_code >= 400 else 502,
                        retryable=retryable,
                        details=error.get("details"),
                        result=result,
                        cause=exc,
                    ) from exc
                return result

            except Exception as exc:
                retryable = _is_retryable_exception(exc)
                code = _error_code_for_exception(exc)
                result = ChunkClientResult(
                    ok=False,
                    status_code=0,
                    method=method,
                    url=url,
                    path=path,
                    payload={},
                    error={
                        "code": code,
                        "message": _safe_str(exc, type(exc).__name__, 2000),
                        "statusCode": 503,
                        "retryable": retryable,
                        "details": {"exceptionType": type(exc).__name__},
                    },
                    request_body=request_body,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    request_id=req_id,
                    correlation_id=corr_id,
                    idempotency_key=idem_key,
                )
                last_result = result
                if retryable and retry_safe and attempt < max_attempts:
                    self._sleep_before_retry(
                        attempt=attempt,
                        base_seconds=effective_retry_seconds,
                        retry_after=None,
                    )
                    continue
                if raise_on_error:
                    raise ChunkClientError(
                        code,
                        result.message or "Chunk request exception.",
                        status_code=503,
                        retryable=retryable,
                        details={"exceptionType": type(exc).__name__},
                        result=result,
                        cause=exc,
                    ) from exc
                return result

        fallback = last_result or ChunkClientResult(
            ok=False,
            status_code=0,
            method=method,
            url=url,
            path=path,
            error={
                "code": "chunk_request_unknown_failure",
                "message": "Chunk request failed without a result.",
                "statusCode": 503,
                "retryable": True,
            },
            request_body=request_body,
            attempts=max_attempts,
            request_id=req_id,
            correlation_id=corr_id,
            idempotency_key=idem_key,
            retryable=True,
        )
        if raise_on_error:
            raise ChunkClientError(
                "chunk_request_unknown_failure",
                fallback.message or "Chunk request failed.",
                retryable=True,
                result=fallback,
            )
        return fallback

    # ----- Project provisioning -------------------------------------------------

    def health(self, *, raise_on_error: bool = False, refresh: bool = False) -> ChunkClientResult:
        return self.request_json(
            "GET",
            self.config.status_path,
            query={
                "includeConfig": "true",
                "includeCounts": "true",
                "includeModels": "true",
            },
            raise_on_error=raise_on_error,
            cache_ttl_seconds=0.0 if refresh else self.config.cache_ttl_seconds,
        )

    def preview_project_by_app(
        self,
        app_project_public_id: str,
        *,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        path = _format_path(
            self.config.preview_by_app_path,
            app_project_public_id=app_project_public_id,
        )
        return self.request_json("GET", path, raise_on_error=raise_on_error)

    def get_project_by_app(
        self,
        app_project_public_id: str,
        *,
        include_bootstrap: bool = True,
        raise_on_error: bool = False,
        refresh: bool = False,
    ) -> ChunkClientResult:
        path = _format_path(
            self.config.ensure_by_app_path,
            app_project_public_id=app_project_public_id,
        )
        return self.request_json(
            "GET",
            path,
            query={
                "includeBootstrap": "true" if include_bootstrap else "false",
                "includeRouteHints": "true",
                "includeWorlds": "true",
                "includeMetadata": "true",
                "includeAccess": "true",
            },
            raise_on_error=raise_on_error,
            cache_ttl_seconds=0.0 if refresh else self.config.cache_ttl_seconds,
        )

    def ensure_project_by_app(
        self,
        app_project_public_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        path = _format_path(
            self.config.ensure_by_app_path,
            app_project_public_id=app_project_public_id,
        )
        result = self.request_json(
            "PUT",
            path,
            body=dict(payload or {}),
            request_id=request_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            raise_on_error=raise_on_error,
        )
        if result.ok:
            clear_chunk_client_cache(f"GET:{path}:")
        return result

    def ensure_project_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        return self.request_json(
            "POST",
            self.config.ensure_path,
            body=dict(payload),
            request_id=request_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            raise_on_error=raise_on_error,
        )

    # ----- Project Access -------------------------------------------------------

    def get_project_access(
        self,
        chunk_project_id: str,
        *,
        include_inactive: bool = False,
        refresh: bool = False,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        path = _format_path(self.config.access_path, chunk_project_id=chunk_project_id)
        return self.request_json(
            "GET",
            path,
            query={"includeInactive": "true" if include_inactive else "false"},
            raise_on_error=raise_on_error,
            cache_ttl_seconds=0.0 if refresh else self.config.cache_ttl_seconds,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )

    def initialize_project_access(
        self,
        chunk_project_id: str,
        *,
        owner_user_id: str,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        owner = _canonical_user_id(owner_user_id)
        path = _format_path(
            self.config.access_initialize_path,
            chunk_project_id=chunk_project_id,
        )
        result = self.request_json(
            "PUT",
            path,
            body={
                "contractVersion": ACCESS_SYNC_CONTRACT_VERSION,
                "ownerUserId": owner,
                "owner_user_id": owner,
                "sourceService": self.config.service_id,
            },
            request_id=request_id,
            idempotency_key=idempotency_key or _idempotency_key(
                seed=f"access-init:{chunk_project_id}:{owner}"
            ),
            raise_on_error=raise_on_error,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )
        self._invalidate_project_access_cache(chunk_project_id)
        return result

    def list_project_assignments(
        self,
        chunk_project_id: str,
        *,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        include_inactive: bool = False,
        refresh: bool = False,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        path = _format_path(
            self.config.assignments_path,
            chunk_project_id=chunk_project_id,
        )
        return self.request_json(
            "GET",
            path,
            query={
                "subjectType": subject_type,
                "subjectId": subject_id,
                "includeInactive": "true" if include_inactive else "false",
            },
            raise_on_error=raise_on_error,
            cache_ttl_seconds=0.0 if refresh else self.config.cache_ttl_seconds,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )

    def upsert_project_user_assignment(
        self,
        chunk_project_id: str,
        *,
        user_id: str,
        role: str,
        assigned_by_user_id: Optional[str] = None,
        starts_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        permission_overrides: Optional[Mapping[str, Any]] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        principal = _canonical_user_id(user_id)
        role_name = _project_role(role)
        actor = _canonical_user_id(assigned_by_user_id, required=False)
        path = _format_path(
            self.config.assignments_path,
            chunk_project_id=chunk_project_id,
        )
        body = {
            "contractVersion": ACCESS_SYNC_CONTRACT_VERSION,
            "subjectType": "user",
            "subjectId": principal,
            "subjectKey": f"user:{principal}",
            "role": role_name,
            "roleKey": role_name,
            "status": "active",
            "assignedByUserId": actor,
            "startsAt": starts_at,
            "expiresAt": expires_at,
            "permissionOverrides": _json_safe(permission_overrides or {}),
            "sourceService": self.config.service_id,
        }
        result = self.request_json(
            "POST",
            path,
            body={key: value for key, value in body.items() if value not in (None, "")},
            request_id=request_id,
            idempotency_key=idempotency_key
            or _idempotency_key(seed=f"assignment:{chunk_project_id}:{principal}:{role_name}:active"),
            raise_on_error=raise_on_error,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )
        self._invalidate_project_access_cache(chunk_project_id)
        return result

    def update_project_assignment(
        self,
        chunk_project_id: str,
        assignment_id: str,
        *,
        status: Optional[str] = None,
        role: Optional[str] = None,
        starts_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        permission_overrides: Optional[Mapping[str, Any]] = None,
        revoked_by_user_id: Optional[str] = None,
        revocation_reason: Optional[str] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        base = _format_path(
            self.config.assignments_path,
            chunk_project_id=chunk_project_id,
        )
        path = f"{base.rstrip('/')}/{_quote_segment(assignment_id)}"
        body: dict[str, Any] = {}
        if status is not None:
            body["status"] = _safe_str(status, "", 40).lower()
        if role is not None:
            body["role"] = _project_role(role)
            body["roleKey"] = body["role"]
        if starts_at is not None:
            body["startsAt"] = starts_at
        if expires_at is not None:
            body["expiresAt"] = expires_at
        if permission_overrides is not None:
            body["permissionOverrides"] = _json_safe(permission_overrides)
        if revoked_by_user_id is not None:
            body["revokedByUserId"] = _canonical_user_id(revoked_by_user_id, required=False)
        if revocation_reason is not None:
            body["revocationReason"] = _safe_str(revocation_reason, "", 1000)
        body["contractVersion"] = ACCESS_SYNC_CONTRACT_VERSION
        body["sourceService"] = self.config.service_id
        result = self.request_json(
            "PATCH",
            path,
            body=body,
            request_id=request_id,
            idempotency_key=idempotency_key
            or _idempotency_key(
                seed=f"assignment-update:{chunk_project_id}:{assignment_id}:{json.dumps(_json_safe(body), sort_keys=True)}"
            ),
            raise_on_error=raise_on_error,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )
        self._invalidate_project_access_cache(chunk_project_id)
        return result

    def revoke_project_assignment(
        self,
        chunk_project_id: str,
        assignment_id: str,
        *,
        revoked_by_user_id: Optional[str] = None,
        reason: str = "membership_removed",
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        return self.update_project_assignment(
            chunk_project_id,
            assignment_id,
            status="revoked",
            revoked_by_user_id=revoked_by_user_id,
            revocation_reason=reason,
            request_id=request_id,
            idempotency_key=idempotency_key,
            raise_on_error=raise_on_error,
        )

    def transfer_project_owner(
        self,
        chunk_project_id: str,
        *,
        current_owner_user_id: str,
        new_owner_user_id: str,
        transferred_by_user_id: Optional[str] = None,
        reason: str = "owner_transfer",
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        current_owner = _canonical_user_id(current_owner_user_id)
        new_owner = _canonical_user_id(new_owner_user_id)
        actor = _canonical_user_id(transferred_by_user_id, required=False) or current_owner
        if current_owner == new_owner:
            raise ValueError("Current and new owner user ids must differ.")
        path = _format_path(
            self.config.transfer_owner_path,
            chunk_project_id=chunk_project_id,
        )
        result = self.request_json(
            "POST",
            path,
            body={
                "contractVersion": ACCESS_SYNC_CONTRACT_VERSION,
                "currentOwnerUserId": current_owner,
                "newOwnerUserId": new_owner,
                "transferredByUserId": actor,
                "reason": _safe_str(reason, "owner_transfer", 1000),
                "sourceService": self.config.service_id,
            },
            request_id=request_id,
            idempotency_key=idempotency_key
            or _idempotency_key(seed=f"owner-transfer:{chunk_project_id}:{current_owner}:{new_owner}"),
            raise_on_error=raise_on_error,
            timeout_seconds=self.config.access_sync_timeout_seconds,
            retries=self.config.access_sync_retries,
            retry_seconds=self.config.access_sync_retry_seconds,
        )
        self._invalidate_project_access_cache(chunk_project_id)
        return result

    def _invalidate_project_access_cache(self, chunk_project_id: str) -> None:
        quoted = _quote_segment(chunk_project_id)
        clear_chunk_client_cache(f"GET:/projects/{quoted}/")


# ---------------------------------------------------------------------------
# App-project payload and response normalization
# ---------------------------------------------------------------------------


def _attr_any(obj: Any, names: Sequence[str], default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        try:
            if isinstance(obj, Mapping) and name in obj:
                value = obj.get(name)
            else:
                value = getattr(obj, name)
            if value not in (None, ""):
                return value
        except Exception:
            continue
    return default


def _set_attr_if_exists(obj: Any, name: str, value: Any) -> bool:
    try:
        if obj is not None and hasattr(obj, name):
            setattr(obj, name, value)
            return True
    except Exception:
        pass
    return False


def get_app_project_public_id(project: Any) -> str:
    value = _attr_any(
        project,
        (
            "public_id",
            "project_public_id",
            "app_project_public_id",
            "project_id_public",
            "uuid",
            "id",
        ),
        None,
    )
    text = _safe_str(value, "", 240)
    if not text:
        raise ValueError("Could not resolve app project public id.")
    return text


def get_project_auth_owner_user_id(project: Any, *, required: bool = True) -> Optional[str]:
    """Liest nur kanonische Auth-Owner-Felder, niemals ``owner_user_id``."""
    value = _attr_any(
        project,
        (
            "auth_owner_user_id",
            "owner_auth_user_id",
            "auth_user_id",
            "canonical_owner_user_id",
        ),
        None,
    )
    return _canonical_user_id(value, required=required)


def _project_metadata(project: Any) -> dict[str, Any]:
    metadata = _safe_mapping(_attr_any(project, ("metadata_json", "metadata", "meta"), {}))
    settings = _safe_mapping(_attr_any(project, ("settings",), {}))
    return {"metadata": metadata, "settings": settings}


def _earth_reference_from_project(project: Any) -> Optional[dict[str, Any]]:
    latitude = _attr_any(project, ("latitude", "lat", "geo_latitude"), None)
    longitude = _attr_any(project, ("longitude", "lng", "lon", "geo_longitude"), None)
    if latitude is None or longitude is None:
        return None
    try:
        lat = float(latitude)
        lon = float(longitude)
    except Exception:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    height = _safe_float(
        _attr_any(project, ("height", "elevation", "altitude"), None),
        _config_float("VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT", DEFAULT_EARTH_HEIGHT),
    )
    source_crs = _safe_str(
        _attr_any(project, ("coordinate_srid", "srid", "crs_id"), "EPSG:4326"),
        "EPSG:4326",
        80,
    ).upper()
    # Direct model coordinates are expected to be WGS84 lon/lat. The dedicated
    # georeference service performs all non-trivial CRS transformations.
    if source_crs not in {"EPSG:4326", "4326", "EPSG:4979", "4979"}:
        return None
    return {
        "longitude": lon,
        "latitude": lat,
        "height": height,
        "crsId": _config_str("VECTOPLAN_APP_EARTH_CRS_ID", DEFAULT_EARTH_CRS_ID).upper(),
        "alwaysXY": True,
        "source": "project-model-coordinates",
    }


def build_chunk_project_payload(
    project: Any,
    *,
    owner_user_id: Optional[str] = None,
    requested_world_template: Optional[str] = None,
    fallback_world_template: Optional[str] = None,
    allow_world_fallback: Optional[bool] = None,
    earth_reference: Optional[Mapping[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    config = ChunkClientConfig.from_app()
    app_project_public_id = get_app_project_public_id(project)
    owner = _canonical_user_id(owner_user_id, required=False) or get_project_auth_owner_user_id(project)
    requested = _world_template(requested_world_template, config.requested_world_template)
    fallback = _world_template(fallback_world_template, config.fallback_world_template)
    allow_fallback = config.allow_world_fallback if allow_world_fallback is None else bool(allow_world_fallback)
    earth = _safe_mapping(earth_reference) or (_earth_reference_from_project(project) if requested == "earth" else None)
    req_id = _request_id(request_id)
    idem = _idempotency_key(
        idempotency_key,
        seed=f"project-provision:{app_project_public_id}:{owner}:{requested}:{fallback}",
    )
    metadata = _project_metadata(project)
    name = _attr_any(project, ("name", "title", "display_name", "project_name"), f"Project {app_project_public_id}")
    description = _attr_any(project, ("description", "summary", "project_description"), "")
    address_text = _attr_any(project, ("address_text", "address", "project_address"), None)
    payload: dict[str, Any] = {
        "contractVersion": PROVISIONING_CONTRACT_VERSION,
        "source": "vectoplan-app",
        "sourceService": config.service_id,
        "source_service": config.service_id,
        "appProjectPublicId": app_project_public_id,
        "app_project_public_id": app_project_public_id,
        "ownerUserId": owner,
        "owner_user_id": owner,
        "name": _safe_str(name, f"Project {app_project_public_id}", 255),
        "description": _safe_str(description, "", 10_000),
        "externalUrl": _build_external_app_project_url(app_project_public_id),
        "external_url": _build_external_app_project_url(app_project_public_id),
        "worldTemplate": requested,
        "requestedWorldTemplate": requested,
        "fallbackWorldTemplate": fallback,
        "allowWorldFallback": allow_fallback,
        "allowWorldTemplateChange": config.allow_world_template_change,
        "templateId": requested,
        "template_id": requested,
        "worldId": config.default_world_id,
        "world_id": config.default_world_id,
        "earthReference": _json_safe(earth) if earth else None,
        "idempotencyKey": idem,
        "requestId": req_id,
        "metadata": {
            "appProjectPublicId": app_project_public_id,
            "sourceService": config.service_id,
            "ownerUserId": owner,
            "addressText": _json_safe(address_text),
            "appProjectMetadata": _json_safe(metadata.get("metadata")),
            "appProjectSettings": _json_safe(metadata.get("settings")),
        },
    }
    if extra:
        payload.update(_json_safe(dict(extra)))
    return {key: value for key, value in payload.items() if value is not None}


def extract_chunk_refs(result: ChunkClientResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result, ChunkClientResult):
        payload = result.payload
        status_code = result.status_code
        ok = result.ok
        created = result.created
        updated = result.updated
        reused = result.reused
        error = result.error
        request_id = result.request_id
        idempotency_key = result.idempotency_key
    else:
        payload = dict(result)
        status_code = _safe_int(payload.get("statusCode"), 200)
        ok = _safe_bool(payload.get("ok"), status_code in SUCCESS_HTTP_STATUSES)
        created = _safe_bool(payload.get("created"), False)
        updated = _safe_bool(payload.get("updated"), False)
        reused = _safe_bool(payload.get("reused"), False)
        error = _safe_mapping(payload.get("error")) or None
        request_id = _safe_str(payload.get("requestId"), "")
        idempotency_key = _safe_str(payload.get("idempotencyKey"), "") or None
    ids = _extract_payload_ids(payload)
    world = _extract_world_contract(payload)
    return {
        "ok": ok,
        "status_code": status_code,
        "statusCode": status_code,
        "created": created,
        "updated": updated,
        "reused": reused,
        "external_app_project_id": ids.get("externalAppProjectId"),
        "chunk_project_id": ids.get("chunkProjectId"),
        "chunk_universe_id": ids.get("chunkUniverseId"),
        "chunk_world_id": ids.get("chunkWorldId") or DEFAULT_WORLD_ID,
        "route_hints": _extract_route_hints(payload),
        "access": _extract_access(payload),
        "requested_world_template": world.get("requestedWorldTemplate"),
        "fallback_world_template": world.get("fallbackWorldTemplate"),
        "effective_world_template": world.get("effectiveWorldTemplate"),
        "fallback_used": world.get("fallbackUsed"),
        "fallback_reason": world.get("fallbackReason"),
        "earth_reference_fingerprint": world.get("earthReferenceFingerprint"),
        "error": _redact(error),
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "raw": _redact(payload),
    }


def apply_chunk_refs_to_project(
    project: Any,
    result: ChunkClientResult | Mapping[str, Any],
) -> dict[str, Any]:
    """Wendet Referenzen nur im Speicher an; kein ``commit``/``flush``."""
    refs = extract_chunk_refs(result)
    applied: dict[str, bool] = {}
    field_map = {
        "chunk_project_id": refs.get("chunk_project_id"),
        "chunk_universe_id": refs.get("chunk_universe_id"),
        "chunk_world_id": refs.get("chunk_world_id"),
        "chunk_route_hints": refs.get("route_hints") or {},
        "chunk_world_template_requested": refs.get("requested_world_template"),
        "chunk_world_template_fallback": refs.get("fallback_world_template"),
        "chunk_world_template_effective": refs.get("effective_world_template"),
        "chunk_world_fallback_reason": refs.get("fallback_reason"),
        "earth_reference_fingerprint": refs.get("earth_reference_fingerprint"),
        "chunk_provisioning_request_id": refs.get("request_id"),
        "chunk_provisioning_idempotency_key": refs.get("idempotency_key"),
    }
    for name, value in field_map.items():
        if value is not None:
            applied[name] = _set_attr_if_exists(project, name, value)

    if refs.get("ok"):
        fallback_used = bool(refs.get("fallback_used"))
        status = "fallback_ready" if fallback_used else "ready"
        applied["chunk_provisioning_status"] = _set_attr_if_exists(project, "chunk_provisioning_status", status)
        applied["chunk_status"] = _set_attr_if_exists(project, "chunk_status", "ready")
        applied["chunk_ready"] = _set_attr_if_exists(project, "chunk_ready", True)
        _set_attr_if_exists(project, "chunk_last_error", None)
    else:
        applied["chunk_status"] = _set_attr_if_exists(project, "chunk_status", "error")
        applied["chunk_ready"] = _set_attr_if_exists(project, "chunk_ready", False)
        _set_attr_if_exists(project, "chunk_last_error", refs.get("error") or {})

    try:
        if hasattr(project, "service_refs"):
            service_refs = _safe_mapping(getattr(project, "service_refs", None))
            service_refs["chunk"] = {
                "status": "ready" if refs.get("ok") else "error",
                "ready": bool(refs.get("ok")),
                "chunk_project_id": refs.get("chunk_project_id"),
                "chunk_universe_id": refs.get("chunk_universe_id"),
                "chunk_world_id": refs.get("chunk_world_id"),
                "route_hints": refs.get("route_hints") or {},
                "provisioning": {
                    "requestedWorldTemplate": refs.get("requested_world_template"),
                    "fallbackWorldTemplate": refs.get("fallback_world_template"),
                    "effectiveWorldTemplate": refs.get("effective_world_template"),
                    "fallbackUsed": bool(refs.get("fallback_used")),
                    "fallbackReason": refs.get("fallback_reason"),
                    "earthReferenceFingerprint": refs.get("earth_reference_fingerprint"),
                    "requestId": refs.get("request_id"),
                    "idempotencyKey": refs.get("idempotency_key"),
                },
                "error": refs.get("error") or None,
            }
            setattr(project, "service_refs", service_refs)
            applied["service_refs"] = True
    except Exception:
        applied["service_refs"] = False

    return {"refs": refs, "applied": applied}


def normalize_provisioning_result(
    result: ChunkClientResult | Mapping[str, Any],
    *,
    requested_world_template: str,
    fallback_world_template: str,
) -> dict[str, Any]:
    refs = extract_chunk_refs(result)
    payload = result.payload if isinstance(result, ChunkClientResult) else dict(result)
    error = refs.get("error") or {}
    if not refs.get("ok"):
        return {
            "ok": False,
            "code": _safe_str(error.get("code") or payload.get("code"), "chunk_provisioning_rejected"),
            "message": _safe_str(error.get("message") or payload.get("message"), "Chunk provisioning failed."),
            "statusCode": refs.get("status_code") or 502,
            "retryable": _safe_bool(error.get("retryable"), (refs.get("status_code") or 0) >= 500),
            "error": _redact(error),
            "requestId": refs.get("request_id"),
            "idempotencyKey": refs.get("idempotency_key"),
            "responseSummary": {
                "statusCode": refs.get("status_code"),
                "rawCode": payload.get("code"),
            },
        }
    missing = [
        name
        for name, value in (
            ("chunkProjectId", refs.get("chunk_project_id")),
            ("chunkUniverseId", refs.get("chunk_universe_id")),
            ("chunkWorldId", refs.get("chunk_world_id")),
        )
        if not value
    ]
    if missing:
        return {
            "ok": False,
            "code": "chunk_provisioning_response_incomplete",
            "message": "Chunk provisioning response is missing required ids.",
            "statusCode": 502,
            "retryable": True,
            "error": {"code": "chunk_provisioning_response_incomplete", "missing": missing},
            "requestId": refs.get("request_id"),
            "idempotencyKey": refs.get("idempotency_key"),
        }
    requested = _world_template(
        refs.get("requested_world_template"),
        _world_template(requested_world_template),
    )
    fallback = _world_template(
        refs.get("fallback_world_template"),
        _world_template(fallback_world_template, DEFAULT_FALLBACK_WORLD_TEMPLATE),
    )
    effective = _world_template(refs.get("effective_world_template"), requested)
    fallback_used = bool(refs.get("fallback_used")) or effective != requested
    return {
        "ok": True,
        "code": _safe_str(payload.get("code"), "chunk_project_provisioned"),
        "statusCode": refs.get("status_code") or 200,
        "chunkProjectId": refs.get("chunk_project_id"),
        "chunkUniverseId": refs.get("chunk_universe_id"),
        "chunkWorldId": refs.get("chunk_world_id") or DEFAULT_WORLD_ID,
        "requestedWorldTemplate": requested,
        "fallbackWorldTemplate": fallback,
        "effectiveWorldTemplate": effective,
        "fallbackUsed": fallback_used,
        "fallbackReason": refs.get("fallback_reason"),
        "earthReferenceFingerprint": refs.get("earth_reference_fingerprint"),
        "created": bool(refs.get("created")),
        "updated": bool(refs.get("updated")),
        "reused": bool(refs.get("reused")),
        "routeHints": refs.get("route_hints") or {},
        "access": refs.get("access") or {},
        "warnings": _safe_sequence(payload.get("warnings")),
        "requestId": refs.get("request_id"),
        "idempotencyKey": refs.get("idempotency_key"),
        "responseSummary": {
            "statusCode": refs.get("status_code"),
            "responseCode": payload.get("code"),
            "created": bool(refs.get("created")),
            "updated": bool(refs.get("updated")),
            "reused": bool(refs.get("reused")),
        },
    }


# ---------------------------------------------------------------------------
# Public provisioning functions
# ---------------------------------------------------------------------------


def get_chunk_client() -> ChunkClient:
    return ChunkClient(ChunkClientConfig.from_app())


def is_chunk_provisioning_enabled() -> bool:
    return ChunkClientConfig.from_app().enabled


def is_chunk_provisioning_required() -> bool:
    return ChunkClientConfig.from_app().required


def is_chunk_access_sync_enabled() -> bool:
    return ChunkClientConfig.from_app().access_sync_enabled


def provision_chunk_project_for_app_project(
    *,
    app_project_public_id: str,
    owner_user_id: str,
    requested_world_template: str = DEFAULT_REQUESTED_WORLD_TEMPLATE,
    fallback_world_template: str = DEFAULT_FALLBACK_WORLD_TEMPLATE,
    allow_world_fallback: bool = True,
    earth_reference: Optional[Mapping[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    request_id: Optional[str] = None,
    project: Any = None,
    client: Optional[ChunkClient] = None,
    raise_on_error: Optional[bool] = None,
) -> dict[str, Any]:
    """Bevorzugter, normalisierter Vertrag für die App-Orchestrierung."""
    config = ChunkClientConfig.from_app()
    should_raise = config.required if raise_on_error is None else bool(raise_on_error)
    app_id = _safe_str(app_project_public_id, "", 240)
    if not app_id:
        raise ChunkClientContractError(
            "app_project_public_id_missing",
            "app_project_public_id is required.",
            status_code=400,
        )
    owner = _canonical_user_id(owner_user_id)
    requested = _world_template(requested_world_template, config.requested_world_template)
    fallback = _world_template(fallback_world_template, config.fallback_world_template)
    if requested == fallback:
        fallback = "flat" if requested == "earth" else "earth"
    req_id = _request_id(request_id)
    idem = _idempotency_key(
        idempotency_key,
        seed=f"project-provision:{app_id}:{owner}:{requested}:{fallback}",
    )

    if not config.enabled:
        result = {
            "ok": False,
            "code": "chunk_provisioning_disabled",
            "message": "Chunk provisioning is disabled by configuration.",
            "statusCode": 503 if should_raise else 200,
            "retryable": False,
            "requestId": req_id,
            "idempotencyKey": idem,
        }
        if should_raise:
            raise ChunkClientConfigurationError(
                result["code"],
                result["message"],
                status_code=503,
            )
        return result

    earth = _safe_mapping(earth_reference) or None
    if requested == "earth" and earth is None and not allow_world_fallback:
        failure = {
            "ok": False,
            "code": "earth_reference_required",
            "message": "Earth provisioning requires earth_reference when fallback is disabled.",
            "statusCode": 422,
            "retryable": False,
            "requestId": req_id,
            "idempotencyKey": idem,
        }
        if should_raise:
            raise ChunkClientContractError(
                failure["code"],
                failure["message"],
                status_code=422,
            )
        return failure

    payload = build_chunk_project_payload(
        project or {"public_id": app_id, "auth_owner_user_id": owner},
        owner_user_id=owner,
        requested_world_template=requested,
        fallback_world_template=fallback,
        allow_world_fallback=allow_world_fallback,
        earth_reference=earth,
        idempotency_key=idem,
        request_id=req_id,
    )
    active_client = client or ChunkClient(config)
    http_result = active_client.ensure_project_by_app(
        app_id,
        payload,
        request_id=req_id,
        correlation_id=req_id,
        idempotency_key=idem,
        raise_on_error=False,
    )
    normalized = normalize_provisioning_result(
        http_result,
        requested_world_template=requested,
        fallback_world_template=fallback,
    )
    if normalized.get("ok") and project is not None:
        apply_chunk_refs_to_project(project, http_result)
    if not normalized.get("ok") and should_raise:
        error = _safe_mapping(normalized.get("error"))
        raise ChunkClientError(
            _safe_str(normalized.get("code"), "chunk_provisioning_failed"),
            _safe_str(normalized.get("message"), "Chunk provisioning failed."),
            status_code=_safe_int(normalized.get("statusCode"), 502, minimum=400, maximum=599),
            retryable=_safe_bool(normalized.get("retryable"), False),
            details=error.get("details") if error else None,
            result=http_result,
        )
    return normalized


def ensure_chunk_project_for_app_project(**kwargs: Any) -> dict[str, Any]:
    """Kompatibilitätsalias für ältere Orchestrierung."""
    return provision_chunk_project_for_app_project(**kwargs)


def ensure_chunk_project(
    *,
    app_project_public_id: str,
    owner_user_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Kurzer Kompatibilitätsalias."""
    return provision_chunk_project_for_app_project(
        app_project_public_id=app_project_public_id,
        owner_user_id=owner_user_id,
        **kwargs,
    )


def ensure_chunk_project_for_project(
    project: Any,
    *,
    owner_user_id: Optional[str] = None,
    requested_world_template: Optional[str] = None,
    fallback_world_template: Optional[str] = None,
    allow_world_fallback: Optional[bool] = None,
    earth_reference: Optional[Mapping[str, Any]] = None,
    extra_payload: Optional[Mapping[str, Any]] = None,
    client: Optional[ChunkClient] = None,
    apply_to_project: bool = True,
    raise_on_error: Optional[bool] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> ChunkClientResult:
    """Legacy-Helfer; liefert weiterhin ``ChunkClientResult``."""
    config = ChunkClientConfig.from_app()
    should_raise = config.required if raise_on_error is None else bool(raise_on_error)
    app_id = get_app_project_public_id(project)
    owner = _canonical_user_id(owner_user_id, required=False) or get_project_auth_owner_user_id(project)
    requested = _world_template(requested_world_template, config.requested_world_template)
    fallback = _world_template(fallback_world_template, config.fallback_world_template)
    allow_fallback = config.allow_world_fallback if allow_world_fallback is None else bool(allow_world_fallback)
    req_id = _request_id(request_id)
    idem = _idempotency_key(
        idempotency_key,
        seed=f"project-provision:{app_id}:{owner}:{requested}:{fallback}",
    )
    payload = build_chunk_project_payload(
        project,
        owner_user_id=owner,
        requested_world_template=requested,
        fallback_world_template=fallback,
        allow_world_fallback=allow_fallback,
        earth_reference=earth_reference,
        idempotency_key=idem,
        request_id=req_id,
        extra=extra_payload,
    )
    if not config.enabled:
        result = ChunkClientResult(
            ok=False,
            status_code=0,
            method="PUT",
            url="",
            path="",
            payload={"ok": False, "code": "chunk_provisioning_disabled"},
            error={
                "code": "chunk_provisioning_disabled",
                "message": "Chunk provisioning is disabled by configuration.",
                "statusCode": 503,
                "retryable": False,
            },
            request_id=req_id,
            correlation_id=req_id,
            idempotency_key=idem,
        )
        if should_raise:
            raise ChunkClientConfigurationError(
                "chunk_provisioning_disabled",
                result.message,
                status_code=503,
                result=result,
            )
        return result
    active_client = client or ChunkClient(config)
    result = active_client.ensure_project_by_app(
        app_id,
        payload,
        request_id=req_id,
        correlation_id=req_id,
        idempotency_key=idem,
        raise_on_error=should_raise,
    )
    if result.ok and apply_to_project:
        apply_chunk_refs_to_project(project, result)
    return result


def ensure_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    owner_user_id: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    client: Optional[ChunkClient] = None,
    raise_on_error: Optional[bool] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> ChunkClientResult:
    config = ChunkClientConfig.from_app()
    should_raise = config.required if raise_on_error is None else bool(raise_on_error)
    app_id = _safe_str(app_project_public_id, "", 240)
    if not app_id:
        raise ValueError("app_project_public_id is required.")
    owner = _canonical_user_id(owner_user_id, required=False)
    req_id = _request_id(request_id)
    idem = _idempotency_key(
        idempotency_key,
        seed=f"project-provision:{app_id}:{owner or 'unknown'}:{config.requested_world_template}",
    )
    body = dict(payload or {})
    if not body:
        if owner is None:
            raise ValueError("owner_user_id is required when no complete payload is supplied.")
        body = build_chunk_project_payload(
            {"public_id": app_id, "auth_owner_user_id": owner},
            owner_user_id=owner,
            requested_world_template=config.requested_world_template,
            fallback_world_template=config.fallback_world_template,
            allow_world_fallback=config.allow_world_fallback,
            idempotency_key=idem,
            request_id=req_id,
        )
    active_client = client or ChunkClient(config)
    return active_client.ensure_project_by_app(
        app_id,
        body,
        request_id=req_id,
        correlation_id=req_id,
        idempotency_key=idem,
        raise_on_error=should_raise,
    )


def get_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    include_bootstrap: bool = True,
    client: Optional[ChunkClient] = None,
    raise_on_error: bool = False,
    refresh: bool = False,
) -> ChunkClientResult:
    return (client or get_chunk_client()).get_project_by_app(
        app_project_public_id,
        include_bootstrap=include_bootstrap,
        raise_on_error=raise_on_error,
        refresh=refresh,
    )


def preview_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    client: Optional[ChunkClient] = None,
    raise_on_error: bool = False,
) -> ChunkClientResult:
    return (client or get_chunk_client()).preview_project_by_app(
        app_project_public_id,
        raise_on_error=raise_on_error,
    )


def get_chunk_health(
    *,
    client: Optional[ChunkClient] = None,
    raise_on_error: bool = False,
    refresh: bool = False,
) -> ChunkClientResult:
    return (client or get_chunk_client()).health(
        raise_on_error=raise_on_error,
        refresh=refresh,
    )


# ---------------------------------------------------------------------------
# Public Access-Sync convenience functions
# ---------------------------------------------------------------------------


def get_chunk_project_access(
    chunk_project_id: str,
    *,
    client: Optional[ChunkClient] = None,
    refresh: bool = False,
    raise_on_error: bool = False,
) -> ChunkClientResult:
    return (client or get_chunk_client()).get_project_access(
        chunk_project_id,
        refresh=refresh,
        raise_on_error=raise_on_error,
    )


def initialize_chunk_project_access(
    chunk_project_id: str,
    *,
    owner_user_id: str,
    client: Optional[ChunkClient] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    raise_on_error: Optional[bool] = None,
) -> ChunkClientResult:
    config = ChunkClientConfig.from_app()
    should_raise = config.access_sync_required if raise_on_error is None else bool(raise_on_error)
    return (client or ChunkClient(config)).initialize_project_access(
        chunk_project_id,
        owner_user_id=owner_user_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
        raise_on_error=should_raise,
    )


def upsert_chunk_project_user_role(
    chunk_project_id: str,
    *,
    user_id: str,
    role: str,
    assigned_by_user_id: Optional[str] = None,
    client: Optional[ChunkClient] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    raise_on_error: Optional[bool] = None,
) -> ChunkClientResult:
    config = ChunkClientConfig.from_app()
    should_raise = config.access_sync_required if raise_on_error is None else bool(raise_on_error)
    return (client or ChunkClient(config)).upsert_project_user_assignment(
        chunk_project_id,
        user_id=user_id,
        role=role,
        assigned_by_user_id=assigned_by_user_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
        raise_on_error=should_raise,
    )


def revoke_chunk_project_assignment(
    chunk_project_id: str,
    assignment_id: str,
    *,
    revoked_by_user_id: Optional[str] = None,
    reason: str = "membership_removed",
    client: Optional[ChunkClient] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    raise_on_error: Optional[bool] = None,
) -> ChunkClientResult:
    config = ChunkClientConfig.from_app()
    should_raise = config.access_sync_required if raise_on_error is None else bool(raise_on_error)
    return (client or ChunkClient(config)).revoke_project_assignment(
        chunk_project_id,
        assignment_id,
        revoked_by_user_id=revoked_by_user_id,
        reason=reason,
        request_id=request_id,
        idempotency_key=idempotency_key,
        raise_on_error=should_raise,
    )


def transfer_chunk_project_owner(
    chunk_project_id: str,
    *,
    current_owner_user_id: str,
    new_owner_user_id: str,
    transferred_by_user_id: Optional[str] = None,
    reason: str = "owner_transfer",
    client: Optional[ChunkClient] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    raise_on_error: Optional[bool] = None,
) -> ChunkClientResult:
    config = ChunkClientConfig.from_app()
    should_raise = config.access_sync_required if raise_on_error is None else bool(raise_on_error)
    return (client or ChunkClient(config)).transfer_project_owner(
        chunk_project_id,
        current_owner_user_id=current_owner_user_id,
        new_owner_user_id=new_owner_user_id,
        transferred_by_user_id=transferred_by_user_id,
        reason=reason,
        request_id=request_id,
        idempotency_key=idempotency_key,
        raise_on_error=should_raise,
    )


def get_chunk_client_status(*, check_service: bool = False) -> dict[str, Any]:
    config = ChunkClientConfig.from_app()
    status = {
        "ok": True,
        "clientVersion": CLIENT_VERSION,
        "provisioningContractVersion": PROVISIONING_CONTRACT_VERSION,
        "accessSyncContractVersion": ACCESS_SYNC_CONTRACT_VERSION,
        "config": config.public_status(),
        "cacheEntries": len(_CACHE),
        "fallbackEligibleCodes": sorted(FALLBACK_ELIGIBLE_CODES),
        "supportedWorldTemplates": sorted(SUPPORTED_WORLD_TEMPLATES),
        "supportedProjectRoles": sorted(SUPPORTED_PROJECT_ROLES),
    }
    if check_service:
        result = ChunkClient(config).health(raise_on_error=False, refresh=True)
        status["service"] = result.to_dict(include_internal_url=False)
        status["ok"] = bool(result.ok)
    return status


__all__ = [
    "CLIENT_VERSION",
    "PROVISIONING_CONTRACT_VERSION",
    "ACCESS_SYNC_CONTRACT_VERSION",
    "FALLBACK_ELIGIBLE_CODES",
    "SUPPORTED_WORLD_TEMPLATES",
    "SUPPORTED_PROJECT_ROLES",
    "ChunkClient",
    "ChunkClientConfig",
    "ChunkClientResult",
    "ChunkClientError",
    "ChunkClientConfigurationError",
    "ChunkClientContractError",
    "get_chunk_client",
    "get_chunk_client_status",
    "clear_chunk_client_cache",
    "is_chunk_provisioning_enabled",
    "is_chunk_provisioning_required",
    "is_chunk_access_sync_enabled",
    "get_app_project_public_id",
    "get_project_auth_owner_user_id",
    "build_chunk_project_payload",
    "extract_chunk_refs",
    "apply_chunk_refs_to_project",
    "normalize_provisioning_result",
    "provision_chunk_project_for_app_project",
    "ensure_chunk_project_for_app_project",
    "ensure_chunk_project",
    "ensure_chunk_project_for_project",
    "ensure_chunk_project_for_app_project_id",
    "get_chunk_project_for_app_project_id",
    "preview_chunk_project_for_app_project_id",
    "get_chunk_health",
    "get_chunk_project_access",
    "initialize_chunk_project_access",
    "upsert_chunk_project_user_role",
    "revoke_chunk_project_assignment",
    "transfer_chunk_project_owner",
]
