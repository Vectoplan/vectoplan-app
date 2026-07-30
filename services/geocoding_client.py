"""Server-side Mapbox Geocoding v6 adapter for vectoplan-app.

The access token never leaves the App service. Autocomplete results are
temporary; project persistence resolves the selected address again with
``permanent=true`` so stored coordinates follow Mapbox's storage contract.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional

import requests


DEFAULT_ENDPOINT = "https://api.mapbox.com/search/geocode/v6/forward"
DEFAULT_TIMEOUT_SECONDS = 4.0
MAX_QUERY_LENGTH = 256
MAX_RESULT_LIMIT = 6


class GeocodingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 503,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "geocoding_failed")
        self.message = str(message or "Die Adresse konnte nicht aufgelöst werden.")
        self.status_code = int(status_code or 503)
        self.retryable = bool(retryable)


def _text(value: Any, default: str = "", max_len: int = 500) -> str:
    try:
        text = str(value if value is not None else default).strip()
    except Exception:
        text = str(default or "").strip()
    return text[:max_len]


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError, OverflowError):
        return None


def _bool_env(name: str, default: bool = False) -> bool:
    value = _text(os.getenv(name), "", 20).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


def _timeout(value: Any = None) -> float:
    raw = value if value is not None else os.getenv(
        "VECTOPLAN_APP_MAPBOX_TIMEOUT_SECONDS",
        str(DEFAULT_TIMEOUT_SECONDS),
    )
    try:
        return min(15.0, max(0.5, float(raw)))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TIMEOUT_SECONDS


def _configured_token() -> str:
    return _text(os.getenv("MAPBOX_ACCESS_TOKEN"), "", 2048)


def geocoding_status() -> Dict[str, Any]:
    token = _configured_token()
    enabled = _bool_env("VECTOPLAN_APP_MAPBOX_GEOCODING_ENABLED", bool(token))
    return {
        "ok": bool(enabled and token),
        "enabled": enabled,
        "configured": bool(token),
        "provider": "mapbox",
        "api": "geocoding-v6",
        "permanent_storage": _bool_env(
            "VECTOPLAN_APP_MAPBOX_PERMANENT_GEOCODING",
            True,
        ),
    }


def _context_value(properties: Mapping[str, Any], key: str, *names: str) -> str:
    context = properties.get("context")
    if not isinstance(context, Mapping):
        return ""
    item = context.get(key)
    if not isinstance(item, Mapping):
        return ""
    for name in names:
        value = _text(item.get(name), "", 255)
        if value:
            return value
    return ""


def _normalized_feature(feature: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    properties = feature.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    geometry = feature.get("geometry")
    geometry = geometry if isinstance(geometry, Mapping) else {}
    coordinates = properties.get("coordinates")
    coordinates = coordinates if isinstance(coordinates, Mapping) else {}
    geometry_coordinates = geometry.get("coordinates")
    geometry_coordinates = (
        geometry_coordinates
        if isinstance(geometry_coordinates, (list, tuple))
        else []
    )

    longitude = _float(
        coordinates.get("longitude")
        if coordinates.get("longitude") is not None
        else geometry_coordinates[0]
        if len(geometry_coordinates) >= 2
        else None
    )
    latitude = _float(
        coordinates.get("latitude")
        if coordinates.get("latitude") is not None
        else geometry_coordinates[1]
        if len(geometry_coordinates) >= 2
        else None
    )
    if longitude is None or latitude is None:
        return None
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        return None

    name = _text(properties.get("name_preferred") or properties.get("name"), "", 255)
    place = _text(properties.get("place_formatted"), "", 500)
    label = _text(
        properties.get("full_address")
        or ", ".join(part for part in (name, place) if part),
        "",
        MAX_QUERY_LENGTH,
    )
    if not label:
        return None

    match_code = properties.get("match_code")
    match_code = match_code if isinstance(match_code, Mapping) else {}
    feature_type = _text(
        properties.get("feature_type")
        or feature.get("feature_type")
        or feature.get("type"),
        "",
        80,
    )

    return {
        "id": _text(
            properties.get("mapbox_id") or feature.get("id"),
            "",
            255,
        ),
        "mapbox_id": _text(
            properties.get("mapbox_id") or feature.get("id"),
            "",
            255,
        ),
        "label": label,
        "address_text": label,
        "feature_type": feature_type,
        "longitude": longitude,
        "latitude": latitude,
        "x": longitude,
        "y": latitude,
        "coordinate_srid": "EPSG:4326",
        "source_crs_id": "EPSG:4326",
        "street": _context_value(properties, "address", "street_name")
        or (name if feature_type == "street" else ""),
        "house_number": _context_value(
            properties,
            "address",
            "address_number",
        ),
        "postal_code": _context_value(properties, "postcode", "name"),
        "city": _context_value(properties, "place", "name"),
        "region": _context_value(properties, "region", "name"),
        "country": _context_value(
            properties,
            "country",
            "name",
            "country_code",
        ),
        "quality": _text(
            coordinates.get("accuracy")
            or match_code.get("confidence")
            or feature_type,
            "",
            80,
        ),
        "status": "resolved",
        "source": "mapbox-geocoding-v6",
    }


def search_addresses(
    query: str,
    *,
    limit: int = 5,
    autocomplete: bool = True,
    permanent: bool = False,
    timeout: Any = None,
    timeout_seconds: Any = None,
) -> List[Dict[str, Any]]:
    status = geocoding_status()
    if not status["enabled"]:
        raise GeocodingError(
            "geocoding_disabled",
            "Die Adresssuche ist nicht aktiviert.",
            status_code=503,
        )

    token = _configured_token()
    if not token:
        raise GeocodingError(
            "mapbox_token_missing",
            "Die Adresssuche ist noch nicht konfiguriert.",
            status_code=503,
        )

    text_query = _text(query, "", MAX_QUERY_LENGTH)
    if len(text_query) < 3:
        return []
    if ";" in text_query:
        raise GeocodingError(
            "geocoding_query_invalid",
            "Die Adresse enthält ein nicht unterstütztes Zeichen.",
            status_code=422,
        )

    try:
        result_limit = min(MAX_RESULT_LIMIT, max(1, int(limit or 5)))
    except (TypeError, ValueError, OverflowError):
        result_limit = 5

    params: Dict[str, Any] = {
        "q": text_query,
        "access_token": token,
        "autocomplete": "true" if autocomplete else "false",
        "permanent": "true" if permanent else "false",
        "limit": result_limit,
        "language": _text(
            os.getenv("VECTOPLAN_APP_MAPBOX_LANGUAGE"),
            "de",
            20,
        ),
    }
    countries = _text(
        os.getenv("VECTOPLAN_APP_MAPBOX_COUNTRIES"),
        "",
        120,
    )
    if countries:
        params["country"] = countries

    request_timeout = _timeout(
        timeout_seconds if timeout_seconds is not None else timeout
    )
    endpoint = _text(
        os.getenv("VECTOPLAN_APP_MAPBOX_GEOCODING_ENDPOINT"),
        DEFAULT_ENDPOINT,
        1000,
    )

    try:
        response = requests.get(
            endpoint,
            params=params,
            timeout=request_timeout,
            headers={
                "Accept": "application/geo+json, application/json",
                "User-Agent": "vectoplan-app-geocoder/1.0",
            },
        )
    except requests.Timeout as exc:
        raise GeocodingError(
            "mapbox_timeout",
            "Die Adresssuche hat zu lange gedauert. Bitte erneut versuchen.",
            status_code=503,
            retryable=True,
        ) from exc
    except requests.RequestException as exc:
        raise GeocodingError(
            "mapbox_unavailable",
            "Die Adresssuche ist vorübergehend nicht erreichbar.",
            status_code=503,
            retryable=True,
        ) from exc

    if response.status_code == 429:
        raise GeocodingError(
            "mapbox_rate_limited",
            "Die Adresssuche ist ausgelastet. Bitte kurz warten.",
            status_code=503,
            retryable=True,
        )
    if response.status_code in {401, 403}:
        raise GeocodingError(
            "mapbox_token_rejected",
            "Die Adresssuche ist nicht korrekt autorisiert.",
            status_code=503,
        )
    if not 200 <= response.status_code < 300:
        raise GeocodingError(
            "mapbox_request_failed",
            "Die Adresse konnte derzeit nicht geprüft werden.",
            status_code=503 if response.status_code >= 500 else 422,
            retryable=response.status_code >= 500,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise GeocodingError(
            "mapbox_response_invalid",
            "Die Adresssuche hat eine ungültige Antwort geliefert.",
            status_code=503,
            retryable=True,
        ) from exc

    features = payload.get("features") if isinstance(payload, Mapping) else []
    if not isinstance(features, list):
        features = []

    normalized: List[Dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, Mapping):
            continue
        item = _normalized_feature(feature)
        if item is not None:
            normalized.append(item)
        if len(normalized) >= result_limit:
            break
    return normalized


def geocode_address(
    address: Optional[str] = None,
    *,
    query: Optional[str] = None,
    limit: int = 1,
    permanent: Optional[bool] = None,
    timeout: Any = None,
    timeout_seconds: Any = None,
    **_: Any,
) -> Dict[str, Any]:
    persist = (
        _bool_env("VECTOPLAN_APP_MAPBOX_PERMANENT_GEOCODING", True)
        if permanent is None
        else bool(permanent)
    )
    results = search_addresses(
        address or query or "",
        limit=limit,
        autocomplete=False,
        permanent=persist,
        timeout=timeout,
        timeout_seconds=timeout_seconds,
    )
    return {
        "ok": True,
        "provider": "mapbox",
        "result": results[0] if results else None,
        "results": results,
    }


__all__ = [
    "GeocodingError",
    "geocode_address",
    "geocoding_status",
    "search_addresses",
]
