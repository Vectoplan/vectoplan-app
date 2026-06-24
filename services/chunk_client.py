# services/vectoplan-app/services/chunk_client.py
"""
Server-side client for vectoplan-app -> vectoplan-chunk communication.

Purpose:
    vectoplan-app creates and owns App projects.
    vectoplan-chunk owns Chunk projects.

This client is used by vectoplan-app to idempotently create or fetch the
corresponding chunk-side Project/Universe/WorldInstance for an app project.

Important rules:
- Use VECTOPLAN_CHUNK_INTERNAL_URL only for server-to-server calls.
- Do not use VECTOPLAN_CHUNK_PUBLIC_URL for backend requests.
- Do not call the chunk service from the browser for provisioning.
- Do not write directly into the chunk database from vectoplan-app.
- Do not create ProjectServiceLink rows here. project_service.py owns app DB writes.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover - app runtime should have Flask.
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[no-redef]
        return False


DEFAULT_CHUNK_INTERNAL_URL = "http://vectoplan-chunk:5000"
DEFAULT_CHUNK_PUBLIC_URL = "http://localhost:5102"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_SECONDS = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_USER_AGENT = "vectoplan-app/chunk-client"


TRUE_VALUES = {"1", "true", "t", "yes", "y", "on", "enabled"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off", "disabled"}


# -----------------------------------------------------------------------------
# Small defensive helpers
# -----------------------------------------------------------------------------

def _safe_str(value: Any, default: str = "") -> str:
    """Convert a value to stripped text."""
    if value is None:
        return default

    try:
        text = str(value).strip()
    except Exception:
        return default

    return text or default


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Convert common bool-ish values to bool."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return bool(value)

    text = _safe_str(value).lower()

    if text in TRUE_VALUES:
        return True

    if text in FALSE_VALUES:
        return False

    return default


def _safe_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Convert value to int with optional bounds."""
    try:
        result = int(value)
    except Exception:
        result = default

    if minimum is not None:
        result = max(minimum, result)

    if maximum is not None:
        result = min(maximum, result)

    return result


def _safe_float(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    """Convert value to float with optional bounds."""
    try:
        result = float(value)
    except Exception:
        result = default

    if minimum is not None:
        result = max(minimum, result)

    if maximum is not None:
        result = min(maximum, result)

    return result


def _env(name: str, default: Any = None) -> Any:
    """Read environment value defensively."""
    try:
        return os.environ.get(name, default)
    except Exception:
        return default


def _config_value(name: str, default: Any = None) -> Any:
    """Read Flask config first, then environment, then default."""
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return current_app.config.get(name)
    except Exception:
        pass

    return _env(name, default)


def _config_str(name: str, default: str = "") -> str:
    return _safe_str(_config_value(name, default), default)


def _config_bool(name: str, default: bool = False) -> bool:
    return _safe_bool(_config_value(name, default), default)


def _config_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    return _safe_int(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _config_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    return _safe_float(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _json_safe(value: Any) -> Any:
    """Convert unknown objects to JSON-safe structures."""
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]

    try:
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            return isoformat()
    except Exception:
        pass

    try:
        return str(value)
    except Exception:
        return repr(value)


def _attr_any(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    """Read first non-empty attribute/key from object or mapping."""
    if obj is None:
        return default

    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            value = obj.get(name)
            if value not in (None, ""):
                return value

        try:
            value = getattr(obj, name)
            if value not in (None, ""):
                return value
        except Exception:
            pass

    return default


def _set_attr_if_exists(obj: Any, name: str, value: Any) -> bool:
    """Set attribute only if the object already exposes it."""
    if obj is None:
        return False

    try:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return True
    except Exception:
        return False

    return False


def _ensure_mapping(value: Any) -> dict[str, Any]:
    """Convert mapping-like value to dict."""
    if isinstance(value, dict):
        return dict(value)

    if isinstance(value, Mapping):
        try:
            return dict(value)
        except Exception:
            return {}

    return {}


def _join_url(base_url: str, path: str) -> str:
    """Join base URL and path without double slashes."""
    base = _safe_str(base_url, DEFAULT_CHUNK_INTERNAL_URL).rstrip("/")
    clean_path = "/" + _safe_str(path, "/").lstrip("/")
    return f"{base}{clean_path}"


def _quote_segment(value: Any) -> str:
    """Quote one URL path segment."""
    return quote(_safe_str(value), safe="")


def _build_external_app_project_url(app_project_public_id: str) -> str | None:
    """Build browser URL for the app project if app public URL is configured."""
    public_url = _config_str("VECTOPLAN_APP_PUBLIC_URL", "")
    if not public_url:
        public_url = _config_str("VECTOPLAN_APP_PUBLIC_BASE_URL", "")

    if not public_url:
        return None

    return f"{public_url.rstrip('/')}/project={quote(app_project_public_id, safe='')}"


def _parse_json_text(text: str) -> dict[str, Any]:
    """Parse JSON object; return fallback dict for non-object JSON."""
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except Exception:
        return {
            "raw": text,
        }

    if isinstance(parsed, dict):
        return parsed

    return {
        "value": parsed,
    }


def _read_response_body(response: Any, *, max_bytes: int) -> tuple[str, bool]:
    """Read response body with size cap."""
    try:
        raw = response.read(max_bytes + 1)
    except Exception:
        return "", False

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = ""

    return text, truncated


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, URLError)):
        return True
    return False


def _extract_payload_ids(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract ids from known chunk response shapes."""
    ids = _ensure_mapping(payload.get("ids"))

    preview = _ensure_mapping(payload.get("preview"))
    bootstrap = _ensure_mapping(payload.get("bootstrap"))

    result: dict[str, Any] = {
        "externalAppProjectId": ids.get("externalAppProjectId")
        or payload.get("externalAppProjectId")
        or payload.get("appProjectPublicId")
        or preview.get("externalAppProjectId"),
        "chunkProjectId": ids.get("chunkProjectId")
        or payload.get("chunkProjectId")
        or preview.get("chunkProjectId")
        or bootstrap.get("projectId")
        or bootstrap.get("chunkProjectId"),
        "chunkUniverseId": ids.get("chunkUniverseId")
        or payload.get("chunkUniverseId")
        or preview.get("chunkUniverseId")
        or bootstrap.get("universeId")
        or bootstrap.get("chunkUniverseId"),
        "chunkWorldId": ids.get("chunkWorldId")
        or payload.get("chunkWorldId")
        or preview.get("chunkWorldId")
        or bootstrap.get("spawnWorldId")
        or bootstrap.get("defaultWorldId")
        or bootstrap.get("worldId")
        or bootstrap.get("chunkWorldId"),
    }

    return {
        key: value
        for key, value in result.items()
        if value not in (None, "")
    }


def _extract_route_hints(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract route hints from known chunk response shapes."""
    route_hints = _ensure_mapping(payload.get("routeHints"))
    if route_hints:
        return route_hints

    preview = _ensure_mapping(payload.get("preview"))
    route_hints = _ensure_mapping(preview.get("routeHints"))
    if route_hints:
        return route_hints

    bootstrap = _ensure_mapping(payload.get("bootstrap"))
    route_hints = _ensure_mapping(bootstrap.get("routeHints"))
    if route_hints:
        return route_hints

    return {}


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ChunkClientConfig:
    """Runtime config for ChunkClient."""

    internal_url: str = DEFAULT_CHUNK_INTERNAL_URL
    public_url: str = DEFAULT_CHUNK_PUBLIC_URL
    enabled: bool = True
    required: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    retries: int = DEFAULT_RETRIES
    retry_seconds: float = DEFAULT_RETRY_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    default_template_id: str = "flat"
    default_world_id: str = "world_spawn"
    user_agent: str = DEFAULT_USER_AGENT
    internal_token: str | None = None

    @classmethod
    def from_app(cls) -> "ChunkClientConfig":
        """Build config from Flask config/environment."""
        internal_url = _config_str(
            "VECTOPLAN_CHUNK_INTERNAL_URL",
            _config_str("VECTOPLAN_CHUNK_INTERNAL_BASE_URL", DEFAULT_CHUNK_INTERNAL_URL),
        )

        public_url = _config_str(
            "VECTOPLAN_CHUNK_PUBLIC_URL",
            _config_str("VECTOPLAN_CHUNK_PUBLIC_BASE_URL", DEFAULT_CHUNK_PUBLIC_URL),
        )

        token = _config_str(
            "VECTOPLAN_CHUNK_INTERNAL_TOKEN",
            _config_str("VECTOPLAN_CHUNK_API_TOKEN", ""),
        )

        return cls(
            internal_url=internal_url.rstrip("/") or DEFAULT_CHUNK_INTERNAL_URL,
            public_url=public_url.rstrip("/") or DEFAULT_CHUNK_PUBLIC_URL,
            enabled=_config_bool("VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True),
            required=_config_bool("VECTOPLAN_CHUNK_PROVISION_REQUIRED", False),
            timeout_seconds=_config_float(
                "VECTOPLAN_CHUNK_PROVISION_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
                minimum=0.1,
                maximum=120.0,
            ),
            retries=_config_int(
                "VECTOPLAN_CHUNK_PROVISION_RETRIES",
                DEFAULT_RETRIES,
                minimum=0,
                maximum=10,
            ),
            retry_seconds=_config_float(
                "VECTOPLAN_CHUNK_PROVISION_RETRY_SECONDS",
                DEFAULT_RETRY_SECONDS,
                minimum=0.0,
                maximum=60.0,
            ),
            max_response_bytes=_config_int(
                "VECTOPLAN_CHUNK_MAX_RESPONSE_BYTES",
                DEFAULT_MAX_RESPONSE_BYTES,
                minimum=1024,
                maximum=64 * 1024 * 1024,
            ),
            default_template_id=_config_str(
                "VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID",
                "flat",
            ),
            default_world_id=_config_str(
                "VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID",
                "world_spawn",
            ),
            user_agent=_config_str(
                "VECTOPLAN_CHUNK_CLIENT_USER_AGENT",
                DEFAULT_USER_AGENT,
            ),
            internal_token=token or None,
        )


@dataclass
class ChunkClientResult:
    """Structured HTTP result from vectoplan-chunk."""

    ok: bool
    status_code: int
    method: str
    url: str
    path: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    request_body: dict[str, Any] | None = None
    duration_ms: int = 0
    attempts: int = 1
    retryable: bool = False
    truncated: bool = False
    raw_text: str = ""
    request_id: str = ""

    @property
    def created(self) -> bool:
        return _safe_bool(self.payload.get("created"), False)

    @property
    def updated(self) -> bool:
        return _safe_bool(self.payload.get("updated"), False)

    @property
    def response_code(self) -> str:
        return _safe_str(self.payload.get("code"), "")

    @property
    def message(self) -> str:
        if self.error:
            return _safe_str(self.error.get("message"), "")
        return _safe_str(self.payload.get("message"), "")

    @property
    def ids(self) -> dict[str, Any]:
        return _extract_payload_ids(self.payload)

    @property
    def route_hints(self) -> dict[str, Any]:
        return _extract_route_hints(self.payload)

    @property
    def external_app_project_id(self) -> str | None:
        value = self.ids.get("externalAppProjectId")
        return _safe_str(value) or None

    @property
    def chunk_project_id(self) -> str | None:
        value = self.ids.get("chunkProjectId")
        return _safe_str(value) or None

    @property
    def chunk_universe_id(self) -> str | None:
        value = self.ids.get("chunkUniverseId")
        return _safe_str(value) or None

    @property
    def chunk_world_id(self) -> str | None:
        value = self.ids.get("chunkWorldId")
        return _safe_str(value) or None

    def to_dict(self, *, include_raw: bool = False, include_request_body: bool = False) -> dict[str, Any]:
        """Serialize result for app logs/status responses."""
        result = {
            "ok": self.ok,
            "statusCode": self.status_code,
            "method": self.method,
            "url": self.url,
            "path": self.path,
            "payload": _json_safe(self.payload),
            "error": _json_safe(self.error),
            "durationMs": self.duration_ms,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "truncated": self.truncated,
            "requestId": self.request_id,
            "created": self.created,
            "updated": self.updated,
            "ids": self.ids,
            "routeHints": self.route_hints,
        }

        if include_request_body:
            result["requestBody"] = _json_safe(self.request_body)

        if include_raw:
            result["rawText"] = self.raw_text

        return result


class ChunkClientError(RuntimeError):
    """Raised when ChunkClient is configured to fail hard."""

    def __init__(self, message: str, *, result: ChunkClientResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class ChunkClientConfigurationError(ChunkClientError):
    """Raised for invalid chunk client configuration."""


# -----------------------------------------------------------------------------
# HTTP client
# -----------------------------------------------------------------------------

class ChunkClient:
    """Small stdlib HTTP client for vectoplan-chunk internal API."""

    def __init__(self, config: ChunkClientConfig | None = None) -> None:
        self.config = config or ChunkClientConfig.from_app()

    def _headers(self, *, request_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self.config.user_agent,
            "X-Vectoplan-Client": "vectoplan-app",
            "X-Vectoplan-Request-Id": request_id,
        }

        if self.config.internal_token:
            headers["Authorization"] = f"Bearer {self.config.internal_token}"
            headers["X-Vectoplan-Internal-Token"] = self.config.internal_token

        return headers

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        """Send JSON request to vectoplan-chunk."""
        method = _safe_str(method, "GET").upper()
        request_id = uuid.uuid4().hex

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
                },
                request_body=dict(body or {}),
                request_id=request_id,
            )
            if raise_on_error:
                raise ChunkClientConfigurationError(result.message, result=result)
            return result

        url = _join_url(self.config.internal_url, path)
        if query:
            filtered_query = {
                key: value
                for key, value in query.items()
                if value not in (None, "")
            }
            if filtered_query:
                url = f"{url}?{urlencode(filtered_query)}"

        request_body = _json_safe(dict(body or {})) if body is not None else None
        encoded_body = None

        if request_body is not None:
            encoded_body = json.dumps(
                request_body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")

        max_attempts = max(1, self.config.retries + 1)
        last_result: ChunkClientResult | None = None

        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()

            try:
                request = Request(
                    url,
                    data=encoded_body,
                    headers=self._headers(request_id=request_id),
                    method=method,
                )

                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    status_code = int(getattr(response, "status", 200))
                    text, truncated = _read_response_body(
                        response,
                        max_bytes=self.config.max_response_bytes,
                    )
                    payload = _parse_json_text(text)

                response_ok = 200 <= status_code < 300 and payload.get("ok", True) is not False
                retryable = _is_retryable_status(status_code)

                result = ChunkClientResult(
                    ok=response_ok,
                    status_code=status_code,
                    method=method,
                    url=url,
                    path=path,
                    payload=payload,
                    error=None if response_ok else _ensure_mapping(payload.get("error")) or {
                        "code": payload.get("code") or "chunk_request_failed",
                        "message": payload.get("message") or "Chunk service request failed.",
                    },
                    request_body=request_body,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    truncated=truncated,
                    raw_text=text,
                    request_id=request_id,
                )

                last_result = result

                if result.ok:
                    return result

                if retryable and attempt < max_attempts:
                    time.sleep(self.config.retry_seconds * attempt)
                    continue

                if raise_on_error:
                    raise ChunkClientError(result.message or "Chunk request failed.", result=result)

                return result

            except HTTPError as exc:
                status_code = int(getattr(exc, "code", 0) or 0)
                text, truncated = _read_response_body(
                    exc,
                    max_bytes=self.config.max_response_bytes,
                )
                payload = _parse_json_text(text)
                retryable = _is_retryable_status(status_code)

                result = ChunkClientResult(
                    ok=False,
                    status_code=status_code,
                    method=method,
                    url=url,
                    path=path,
                    payload=payload,
                    error=_ensure_mapping(payload.get("error")) or {
                        "code": "chunk_http_error",
                        "message": _safe_str(getattr(exc, "reason", ""), "HTTP error from chunk service."),
                    },
                    request_body=request_body,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    truncated=truncated,
                    raw_text=text,
                    request_id=request_id,
                )

                last_result = result

                if retryable and attempt < max_attempts:
                    time.sleep(self.config.retry_seconds * attempt)
                    continue

                if raise_on_error:
                    raise ChunkClientError(result.message or "Chunk HTTP error.", result=result)

                return result

            except Exception as exc:
                retryable = _is_retryable_exception(exc)

                result = ChunkClientResult(
                    ok=False,
                    status_code=0,
                    method=method,
                    url=url,
                    path=path,
                    payload={},
                    error={
                        "code": "chunk_request_exception",
                        "message": _safe_str(exc, type(exc).__name__),
                        "type": type(exc).__name__,
                    },
                    request_body=request_body,
                    duration_ms=_duration_ms(started),
                    attempts=attempt,
                    retryable=retryable,
                    truncated=False,
                    raw_text="",
                    request_id=request_id,
                )

                last_result = result

                if retryable and attempt < max_attempts:
                    time.sleep(self.config.retry_seconds * attempt)
                    continue

                if raise_on_error:
                    raise ChunkClientError(result.message or "Chunk request exception.", result=result)

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
            },
            request_body=request_body,
            attempts=max_attempts,
            request_id=request_id,
        )

        if raise_on_error:
            raise ChunkClientError(fallback.message or "Chunk request failed.", result=fallback)

        return fallback

    def health(self, *, raise_on_error: bool = False) -> ChunkClientResult:
        """Fetch chunk project route status."""
        return self.request_json(
            "GET",
            "/projects/_status",
            query={
                "includeConfig": "true",
                "includeCounts": "true",
                "includeModels": "true",
            },
            raise_on_error=raise_on_error,
        )

    def preview_project_by_app(
        self,
        app_project_public_id: str,
        *,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        """Preview deterministic chunk ids without creating DB rows."""
        path = f"/projects/preview/by-app/{_quote_segment(app_project_public_id)}"
        return self.request_json("GET", path, raise_on_error=raise_on_error)

    def get_project_by_app(
        self,
        app_project_public_id: str,
        *,
        include_bootstrap: bool = True,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        """Fetch existing chunk project by app project id."""
        path = f"/projects/by-app/{_quote_segment(app_project_public_id)}"
        return self.request_json(
            "GET",
            path,
            query={
                "includeBootstrap": "true" if include_bootstrap else "false",
                "includeRouteHints": "true",
                "includeWorlds": "true",
                "includeMetadata": "true",
            },
            raise_on_error=raise_on_error,
        )

    def ensure_project_by_app(
        self,
        app_project_public_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        """Idempotently ensure chunk project for app project id."""
        path = f"/projects/by-app/{_quote_segment(app_project_public_id)}"
        return self.request_json(
            "PUT",
            path,
            body=dict(payload or {}),
            raise_on_error=raise_on_error,
        )

    def ensure_project_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        raise_on_error: bool = False,
    ) -> ChunkClientResult:
        """Idempotently ensure chunk project using payload body."""
        return self.request_json(
            "POST",
            "/projects/ensure",
            body=dict(payload),
            raise_on_error=raise_on_error,
        )


# -----------------------------------------------------------------------------
# Payload builders for vectoplan-app project objects
# -----------------------------------------------------------------------------

def get_app_project_public_id(project: Any) -> str:
    """
    Resolve the public app project id from a vectoplan-app Project object.

    Important:
        Do not send this as `project_id` to vectoplan-chunk, because there
        `project_id` means chunk_project_id.
    """
    value = _attr_any(
        project,
        (
            "public_id",
            "project_public_id",
            "app_project_public_id",
            "project_id_public",
            "uuid",
        ),
        None,
    )

    if value is None:
        value = _attr_any(project, ("id",), None)

    text = _safe_str(value)

    if not text:
        raise ValueError("Could not resolve app project public id.")

    return text


def build_chunk_project_payload(
    project: Any,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build provisioning payload for vectoplan-chunk.

    Deliberately avoids `project_id` key because chunk-service interprets that
    as explicit chunk project id.
    """
    app_project_public_id = get_app_project_public_id(project)

    name = _attr_any(
        project,
        (
            "name",
            "title",
            "display_name",
            "project_name",
        ),
        f"Project {app_project_public_id}",
    )

    description = _attr_any(
        project,
        (
            "description",
            "summary",
            "project_description",
        ),
        "",
    )

    owner_id = _attr_any(
        project,
        (
            "owner_id",
            "created_by_user_id",
            "user_id",
        ),
        None,
    )

    address = _attr_any(
        project,
        (
            "address",
            "project_address",
            "site_address",
            "street_address",
        ),
        None,
    )

    city = _attr_any(project, ("city", "project_city", "site_city"), None)
    postal_code = _attr_any(project, ("postal_code", "postcode", "zip_code"), None)
    country = _attr_any(project, ("country", "country_code"), None)

    latitude = _attr_any(project, ("latitude", "lat", "geo_latitude"), None)
    longitude = _attr_any(project, ("longitude", "lng", "lon", "geo_longitude"), None)

    metadata = _ensure_mapping(
        _attr_any(
            project,
            (
                "metadata",
                "metadata_json",
                "meta",
                "settings",
            ),
            {},
        )
    )

    payload: dict[str, Any] = {
        "app_project_public_id": app_project_public_id,
        "name": _safe_str(name, f"Project {app_project_public_id}"),
        "description": _safe_str(description, ""),
        "source_service": "vectoplan-app",
        "external_url": _build_external_app_project_url(app_project_public_id),
        "template_id": _config_str("VECTOPLAN_CHUNK_PROVISION_DEFAULT_TEMPLATE_ID", "flat"),
        "world_id": None,
        "metadata": {
            "appProjectPublicId": app_project_public_id,
            "sourceService": "vectoplan-app",
            "ownerId": _json_safe(owner_id),
            "address": _json_safe(address),
            "city": _json_safe(city),
            "postalCode": _json_safe(postal_code),
            "country": _json_safe(country),
            "coordinates": {
                "latitude": _json_safe(latitude),
                "longitude": _json_safe(longitude),
            },
            "appProjectMetadata": _json_safe(metadata),
        },
    }

    default_world_id = _config_str("VECTOPLAN_CHUNK_PROVISION_DEFAULT_WORLD_ID", "")
    if default_world_id:
        payload["world_id"] = default_world_id

    if extra:
        # Extra values may intentionally include chunk-specific ids.
        payload.update(_json_safe(dict(extra)))

    # Remove empty None values at top level.
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def extract_chunk_refs(result: ChunkClientResult | Mapping[str, Any]) -> dict[str, Any]:
    """Extract normalized chunk refs from a client result or payload."""
    if isinstance(result, ChunkClientResult):
        payload = result.payload
        ids = result.ids
        route_hints = result.route_hints
        ok = result.ok
        created = result.created
        status_code = result.status_code
    else:
        payload = dict(result)
        ids = _extract_payload_ids(payload)
        route_hints = _extract_route_hints(payload)
        ok = _safe_bool(payload.get("ok"), False)
        created = _safe_bool(payload.get("created"), False)
        status_code = _safe_int(payload.get("statusCode"), 0)

    return {
        "ok": ok,
        "status_code": status_code,
        "created": created,
        "external_app_project_id": ids.get("externalAppProjectId"),
        "chunk_project_id": ids.get("chunkProjectId"),
        "chunk_universe_id": ids.get("chunkUniverseId"),
        "chunk_world_id": ids.get("chunkWorldId"),
        "route_hints": route_hints,
        "raw": _json_safe(payload),
    }


def apply_chunk_refs_to_project(project: Any, result: ChunkClientResult | Mapping[str, Any]) -> dict[str, Any]:
    """
    Best-effort in-memory application of chunk refs to an app Project object.

    This function does not commit.
    project_service.py remains responsible for db.session.add/commit and
    ProjectServiceLink creation.
    """
    refs = extract_chunk_refs(result)

    chunk_project_id = refs.get("chunk_project_id")
    chunk_world_id = refs.get("chunk_world_id")
    chunk_universe_id = refs.get("chunk_universe_id")

    applied: dict[str, bool] = {}

    if chunk_project_id:
        applied["chunk_project_id"] = _set_attr_if_exists(project, "chunk_project_id", chunk_project_id)

    if chunk_world_id:
        applied["chunk_world_id"] = _set_attr_if_exists(project, "chunk_world_id", chunk_world_id)

    if chunk_universe_id:
        applied["chunk_universe_id"] = _set_attr_if_exists(project, "chunk_universe_id", chunk_universe_id)

    # Best-effort update for dict-like service_refs, if the model already has it.
    try:
        if hasattr(project, "service_refs"):
            current = getattr(project, "service_refs", None)
            refs_dict = _ensure_mapping(current)
            refs_dict["chunk"] = {
                "chunk_project_id": chunk_project_id,
                "chunk_universe_id": chunk_universe_id,
                "chunk_world_id": chunk_world_id,
                "route_hints": refs.get("route_hints") or {},
            }
            setattr(project, "service_refs", refs_dict)
            applied["service_refs"] = True
    except Exception:
        applied["service_refs"] = False

    return {
        "refs": refs,
        "applied": applied,
    }


# -----------------------------------------------------------------------------
# Public convenience functions for project_service.py
# -----------------------------------------------------------------------------

def get_chunk_client() -> ChunkClient:
    """Create a ChunkClient from current Flask config/environment."""
    return ChunkClient(ChunkClientConfig.from_app())


def is_chunk_provisioning_enabled() -> bool:
    """Return whether app project creation should attempt chunk provisioning."""
    return ChunkClientConfig.from_app().enabled


def is_chunk_provisioning_required() -> bool:
    """Return whether app project creation must fail when chunk provisioning fails."""
    return ChunkClientConfig.from_app().required


def ensure_chunk_project_for_project(
    project: Any,
    *,
    extra_payload: Mapping[str, Any] | None = None,
    client: ChunkClient | None = None,
    apply_to_project: bool = True,
    raise_on_error: bool | None = None,
) -> ChunkClientResult:
    """
    Ensure a chunk project for a vectoplan-app Project object.

    Intended use in project_service.py after the app Project has been persisted
    and has a stable public id.
    """
    config = ChunkClientConfig.from_app()

    if not config.enabled:
        return ChunkClientResult(
            ok=False,
            status_code=0,
            method="PUT",
            url="",
            path="",
            payload={
                "ok": False,
                "code": "chunk_provisioning_disabled",
                "message": "Chunk provisioning is disabled by configuration.",
            },
            error={
                "code": "chunk_provisioning_disabled",
                "message": "Chunk provisioning is disabled by configuration.",
            },
            retryable=False,
            request_id=uuid.uuid4().hex,
        )

    should_raise = config.required if raise_on_error is None else bool(raise_on_error)
    app_project_public_id = get_app_project_public_id(project)
    payload = build_chunk_project_payload(project, extra=extra_payload)

    active_client = client or ChunkClient(config)
    result = active_client.ensure_project_by_app(
        app_project_public_id,
        payload,
        raise_on_error=should_raise,
    )

    if result.ok and apply_to_project:
        apply_chunk_refs_to_project(project, result)

    if should_raise and not result.ok:
        raise ChunkClientError(result.message or "Chunk provisioning failed.", result=result)

    return result


def ensure_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    payload: Mapping[str, Any] | None = None,
    client: ChunkClient | None = None,
    raise_on_error: bool | None = None,
) -> ChunkClientResult:
    """Ensure a chunk project for an app project public id."""
    config = ChunkClientConfig.from_app()
    should_raise = config.required if raise_on_error is None else bool(raise_on_error)

    active_client = client or ChunkClient(config)
    return active_client.ensure_project_by_app(
        app_project_public_id,
        payload or {
            "app_project_public_id": app_project_public_id,
            "source_service": "vectoplan-app",
            "template_id": config.default_template_id,
            "world_id": config.default_world_id,
        },
        raise_on_error=should_raise,
    )


def get_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    include_bootstrap: bool = True,
    client: ChunkClient | None = None,
    raise_on_error: bool = False,
) -> ChunkClientResult:
    """Fetch existing chunk project for app project id."""
    active_client = client or get_chunk_client()
    return active_client.get_project_by_app(
        app_project_public_id,
        include_bootstrap=include_bootstrap,
        raise_on_error=raise_on_error,
    )


def preview_chunk_project_for_app_project_id(
    app_project_public_id: str,
    *,
    client: ChunkClient | None = None,
    raise_on_error: bool = False,
) -> ChunkClientResult:
    """Preview chunk ids for app project id."""
    active_client = client or get_chunk_client()
    return active_client.preview_project_by_app(
        app_project_public_id,
        raise_on_error=raise_on_error,
    )


def get_chunk_health(
    *,
    client: ChunkClient | None = None,
    raise_on_error: bool = False,
) -> ChunkClientResult:
    """Fetch chunk service status."""
    active_client = client or get_chunk_client()
    return active_client.health(raise_on_error=raise_on_error)


__all__ = [
    "ChunkClient",
    "ChunkClientConfig",
    "ChunkClientResult",
    "ChunkClientError",
    "ChunkClientConfigurationError",
    "get_chunk_client",
    "is_chunk_provisioning_enabled",
    "is_chunk_provisioning_required",
    "get_app_project_public_id",
    "build_chunk_project_payload",
    "extract_chunk_refs",
    "apply_chunk_refs_to_project",
    "ensure_chunk_project_for_project",
    "ensure_chunk_project_for_app_project_id",
    "get_chunk_project_for_app_project_id",
    "preview_chunk_project_for_app_project_id",
    "get_chunk_health",
]