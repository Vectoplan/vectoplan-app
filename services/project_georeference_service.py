# services/vectoplan-app/services/project_georeference_service.py
"""Robuste Projekt-Georeferenzauflösung für ``vectoplan-app``.

Das Modul erzeugt aus einem App-Projekt eine kompakte, validierte Earth-Referenz
für das Provisioning in ``vectoplan-chunk``. Es führt selbst keine externe
Geocodierung durch, solange kein Geocoder explizit injiziert oder per
Konfiguration aktiviert wurde.

Quellen in absteigender Priorität
---------------------------------
1. explizite Referenz des Aufrufers,
2. kanonische Referenz in Projekt-Metadaten oder Settings,
3. GeoJSON-Point in Projekt-Metadaten,
4. direkte Projektspalten ``longitude``/``latitude``/``height``,
5. optionaler, explizit freigeschalteter Geocoder auf Basis der Projektadresse.

Sicherheits- und Datenregeln
----------------------------
* Koordinatenreihenfolge ist immer ``x=longitude``, ``y=latitude``.
* ``alwaysXY`` ist immer ``True``.
* Unbekannte oder nicht transformierbare CRS werden nicht still als WGS84
  interpretiert.
* Eine reine Freitextadresse ist ohne Geocoder keine Earth-Referenz.
* Der Geocoder ist optional, zeitlich begrenzt und darf keine Auth-Secrets in
  dieses Modul geben.
* Koordinaten werden standardmäßig nicht im Log ausgegeben.
* Der Cache ist pro Prozess, thread-sicher, TTL-basiert und größenbegrenzt.
* Negative Ergebnisse werden deutlich kürzer gecacht als valide Referenzen.
* Persistenz in ``Project.metadata_json`` ist optional und standardmäßig aus.
* Dieses Modul führt keine Migrationen und keine Tabellenanlage aus.

Primärer öffentlicher Vertrag::

    result = build_project_earth_reference(
        project,
        default_height=0.0,
        crs_id="EPSG:4979",
        required=False,
    )

Das Ergebnis ist ein Dataclass-Objekt mit ``to_dict()`` und ist direkt mit
``project_chunk_provisioning_service.py`` kompatibel.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib import import_module
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional, Protocol, Sequence


LOGGER = logging.getLogger(__name__)

SERVICE_VERSION = "1.0.0"
POLICY_VERSION = "project-georeference-policy.v1"
RESULT_VERSION = "project-earth-reference-result.v1"
REFERENCE_SCHEMA_VERSION = "app-earth-reference.schema.v1"
METADATA_NAMESPACE = "projectGeoreference"

DEFAULT_TARGET_CRS_ID = "EPSG:4979"
DEFAULT_SOURCE_CRS_ID = "EPSG:4326"
DEFAULT_HEIGHT = 0.0
DEFAULT_SUCCESS_CACHE_TTL_SECONDS = 300.0
DEFAULT_NEGATIVE_CACHE_TTL_SECONDS = 30.0
DEFAULT_CACHE_MAX_ENTRIES = 512
DEFAULT_GEOCODER_TIMEOUT_SECONDS = 4.0
DEFAULT_COORDINATE_PRECISION = 12
DEFAULT_HEIGHT_PRECISION = 6

_DIRECT_LONGITUDE_FIELDS = (
    "longitude",
    "lon",
    "lng",
    "coordinate_x",
    "x_coordinate",
)
_DIRECT_LATITUDE_FIELDS = (
    "latitude",
    "lat",
    "coordinate_y",
    "y_coordinate",
)
_DIRECT_HEIGHT_FIELDS = (
    "height",
    "altitude",
    "elevation",
    "coordinate_z",
    "z_coordinate",
)
_DIRECT_CRS_FIELDS = (
    "coordinate_crs_id",
    "coordinate_srid",
    "srid",
    "crs_id",
    "crs",
)
_PROJECT_MAPPING_FIELDS = (
    "metadata_json",
    "settings",
    "service_refs",
    "artifact_refs",
)
_REFERENCE_PATHS = (
    ("earthReference",),
    ("earth_reference",),
    ("globalReference",),
    ("global_reference",),
    ("georeference",),
    ("geoReference",),
    ("geo_reference",),
    ("location", "earthReference"),
    ("location", "georeference"),
    ("location", "coordinates"),
    ("coordinates",),
)
_GEOJSON_PATHS = (
    ("geojson",),
    ("geometry",),
    ("location", "geojson"),
    ("location", "geometry"),
    ("georeference", "geometry"),
)

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "raw_key",
)


class GeoreferenceStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    ERROR = "error"


class GeoreferenceSource(str, Enum):
    EXPLICIT = "explicit"
    PROJECT_METADATA = "project_metadata"
    PROJECT_SETTINGS = "project_settings"
    PROJECT_SERVICE_REFS = "project_service_refs"
    PROJECT_ARTIFACT_REFS = "project_artifact_refs"
    PROJECT_GEOJSON = "project_geojson"
    PROJECT_COLUMNS = "project_columns"
    GEOCODER = "geocoder"
    CACHE = "cache"
    UNAVAILABLE = "unavailable"


class ProjectGeoreferenceError(RuntimeError):
    """Stabiler Fehlervertrag für Route, Service, Provisioning und Tests."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = _code(code, "project_georeference_failed")
        self.message = _text(message, "Project georeference failed.", 1000)
        self.status_code = _integer(status_code, 422, 400, 599)
        self.retryable = bool(retryable)
        self.details = _sanitize(details or {}, depth=4)
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "statusCode": self.status_code,
            "retryable": self.retryable,
            "details": self.details,
        }


class GeocoderProtocol(Protocol):
    def geocode(self, address: str, **kwargs: Any) -> Any:
        ...


class TransformerFactoryProtocol(Protocol):
    def __call__(self, source_crs_id: str, target_crs_id: str) -> Any:
        ...


@dataclass(frozen=True)
class GeoreferencePolicy:
    target_crs_id: str = DEFAULT_TARGET_CRS_ID
    default_source_crs_id: str = DEFAULT_SOURCE_CRS_ID
    default_height: float = DEFAULT_HEIGHT
    allow_geocoder: bool = False
    discover_geocoder: bool = False
    allow_coordinate_swap: bool = False
    allow_default_source_crs: bool = True
    persist_result: bool = False
    success_cache_ttl_seconds: float = DEFAULT_SUCCESS_CACHE_TTL_SECONDS
    negative_cache_ttl_seconds: float = DEFAULT_NEGATIVE_CACHE_TTL_SECONDS
    cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES
    geocoder_timeout_seconds: float = DEFAULT_GEOCODER_TIMEOUT_SECONDS
    coordinate_precision: int = DEFAULT_COORDINATE_PRECISION
    height_precision: int = DEFAULT_HEIGHT_PRECISION

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": POLICY_VERSION,
            "targetCrsId": self.target_crs_id,
            "defaultSourceCrsId": self.default_source_crs_id,
            "defaultHeight": self.default_height,
            "allowGeocoder": self.allow_geocoder,
            "discoverGeocoder": self.discover_geocoder,
            "allowCoordinateSwap": self.allow_coordinate_swap,
            "allowDefaultSourceCrs": self.allow_default_source_crs,
            "persistResult": self.persist_result,
            "successCacheTtlSeconds": self.success_cache_ttl_seconds,
            "negativeCacheTtlSeconds": self.negative_cache_ttl_seconds,
            "cacheMaxEntries": self.cache_max_entries,
            "geocoderTimeoutSeconds": self.geocoder_timeout_seconds,
            "coordinatePrecision": self.coordinate_precision,
            "heightPrecision": self.height_precision,
        }


@dataclass(frozen=True)
class GeoreferenceCandidate:
    x: float
    y: float
    z: Optional[float]
    source_crs_id: Optional[str]
    source: str
    source_detail: Optional[str] = None
    address_used: Optional[str] = None
    supplied_height: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass
class ProjectEarthReferenceResult:
    available: bool
    status: str
    project_public_id: Optional[str]
    source: str
    reference: Optional[dict[str, Any]] = None
    fingerprint: Optional[str] = None
    error_code: Optional[str] = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    input_crs_id: Optional[str] = None
    target_crs_id: str = DEFAULT_TARGET_CRS_ID
    transformed: bool = False
    coordinate_swap_applied: bool = False
    height_defaulted: bool = False
    address_used: Optional[str] = None
    cache_hit: bool = False
    persisted: bool = False
    retryable: bool = False
    request_id: Optional[str] = None
    resolved_at: Optional[str] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resultVersion": RESULT_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "schemaVersion": REFERENCE_SCHEMA_VERSION,
            "available": self.available,
            "status": self.status,
            "projectPublicId": self.project_public_id,
            "source": self.source,
            "reference": copy.deepcopy(self.reference),
            "fingerprint": self.fingerprint,
            "errorCode": self.error_code,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "inputCrsId": self.input_crs_id,
            "targetCrsId": self.target_crs_id,
            "transformed": self.transformed,
            "coordinateSwapApplied": self.coordinate_swap_applied,
            "heightDefaulted": self.height_defaulted,
            "addressUsed": self.address_used,
            "cacheHit": self.cache_hit,
            "persisted": self.persisted,
            "retryable": self.retryable,
            "requestId": self.request_id,
            "resolvedAt": self.resolved_at,
            "diagnostics": _sanitize(self.diagnostics, depth=4),
        }


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: ProjectEarthReferenceResult


_CACHE: "OrderedDict[str, _CacheEntry]" = OrderedDict()
_CACHE_LOCK = threading.RLock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_LOCKS_LOCK = threading.RLock()
_TRANSFORMER_CACHE: dict[tuple[str, str], Any] = {}
_TRANSFORMER_CACHE_LOCK = threading.RLock()


class ProjectGeoreferenceService:
    """Löst eine kanonische Earth-Referenz aus App-Projektdaten auf."""

    def __init__(
        self,
        *,
        session: Any = None,
        geocoder: Any = None,
        transformer_factory: Optional[TransformerFactoryProtocol] = None,
    ) -> None:
        self.session = session or _db_session()
        self.geocoder = geocoder
        self.transformer_factory = transformer_factory

    def build(
        self,
        project: Any,
        *,
        explicit_reference: Optional[Mapping[str, Any]] = None,
        default_height: Optional[float] = None,
        crs_id: Optional[str] = None,
        source_crs_id: Optional[str] = None,
        required: bool = False,
        allow_geocoder: Optional[bool] = None,
        discover_geocoder: Optional[bool] = None,
        allow_coordinate_swap: Optional[bool] = None,
        allow_default_source_crs: Optional[bool] = None,
        persist_result: Optional[bool] = None,
        use_cache: bool = True,
        force: bool = False,
        commit: bool = False,
        raise_on_error: bool = False,
        request_id: Optional[str] = None,
    ) -> ProjectEarthReferenceResult:
        """Erzeugt eine validierte, auf das Ziel-CRS normalisierte Referenz.

        ``required=True`` führt bei fehlender Referenz zu einem kontrollierten
        ``ProjectGeoreferenceError``. Technische Fehler werden nur dann erneut
        ausgelöst, wenn ``raise_on_error=True`` oder ``required=True`` gesetzt
        ist. Andernfalls wird ein strukturiertes Fehlerergebnis zurückgegeben.
        """

        resolved_request_id = request_id or _request_id()
        project_public_id = _project_public_id(project)
        lock_key = project_public_id or f"object:{id(project)}"
        policy = self._policy(
            default_height=default_height,
            crs_id=crs_id,
            source_crs_id=source_crs_id,
            allow_geocoder=allow_geocoder,
            discover_geocoder=discover_geocoder,
            allow_coordinate_swap=allow_coordinate_swap,
            allow_default_source_crs=allow_default_source_crs,
            persist_result=persist_result,
        )

        with _project_lock(lock_key):
            try:
                cache_key = _cache_key(
                    project,
                    explicit_reference=explicit_reference,
                    policy=policy,
                )
                if use_cache and not force:
                    cached = _cache_get(cache_key)
                    if cached is not None:
                        cached.cache_hit = True
                        cached.request_id = resolved_request_id
                        cached.diagnostics = {
                            **cached.diagnostics,
                            "cache": "hit",
                        }
                        return cached

                result = self._build_uncached(
                    project,
                    explicit_reference=explicit_reference,
                    policy=policy,
                    request_id=resolved_request_id,
                )

                if policy.persist_result:
                    try:
                        result.persisted = self._persist_result(
                            project,
                            result,
                            commit=commit,
                        )
                    except Exception as exc:
                        LOGGER.exception(
                            "Project georeference persistence failed for project=%s",
                            project_public_id or "unknown",
                        )
                        result.warnings = _append_unique(
                            result.warnings,
                            f"georeference_persist_failed:{exc.__class__.__name__}",
                        )

                if use_cache:
                    ttl = (
                        policy.success_cache_ttl_seconds
                        if result.available
                        else policy.negative_cache_ttl_seconds
                    )
                    _cache_set(
                        cache_key,
                        result,
                        ttl_seconds=ttl,
                        max_entries=policy.cache_max_entries,
                    )

                if required and not result.available:
                    raise ProjectGeoreferenceError(
                        result.error_code or "earth_reference_required",
                        "A valid Earth reference is required for this project.",
                        status_code=422,
                        retryable=result.retryable,
                        details=result.to_dict(),
                    )
                return result

            except ProjectGeoreferenceError:
                if raise_on_error or required:
                    raise
                exc = _current_exception()
                return self._error_result(
                    project_public_id=project_public_id,
                    policy=policy,
                    request_id=resolved_request_id,
                    error=exc,
                )
            except Exception as exc:
                wrapped = ProjectGeoreferenceError(
                    "unexpected_project_georeference_error",
                    "An unexpected error occurred while resolving the project georeference.",
                    status_code=500,
                    retryable=False,
                    details={
                        "exceptionType": exc.__class__.__name__,
                        "message": _text(exc, "", 500),
                    },
                    cause=exc,
                )
                LOGGER.exception(
                    "Unexpected project georeference failure for project=%s",
                    project_public_id or "unknown",
                )
                if raise_on_error or required:
                    raise wrapped from exc
                return self._error_result(
                    project_public_id=project_public_id,
                    policy=policy,
                    request_id=resolved_request_id,
                    error=wrapped,
                )

    def _policy(
        self,
        *,
        default_height: Optional[float],
        crs_id: Optional[str],
        source_crs_id: Optional[str],
        allow_geocoder: Optional[bool],
        discover_geocoder: Optional[bool],
        allow_coordinate_swap: Optional[bool],
        allow_default_source_crs: Optional[bool],
        persist_result: Optional[bool],
    ) -> GeoreferencePolicy:
        target = _normalize_crs_id(
            crs_id
            or _config("VECTOPLAN_APP_EARTH_CRS_ID", DEFAULT_TARGET_CRS_ID)
        )
        default_source = _normalize_crs_id(
            source_crs_id
            or _config(
                "VECTOPLAN_APP_PROJECT_COORDINATE_CRS_ID",
                DEFAULT_SOURCE_CRS_ID,
            )
        )
        if not target:
            raise ProjectGeoreferenceError(
                "target_crs_invalid",
                "The configured Earth target CRS is invalid.",
                status_code=500,
            )
        if not default_source:
            raise ProjectGeoreferenceError(
                "default_source_crs_invalid",
                "The configured default project coordinate CRS is invalid.",
                status_code=500,
            )

        return GeoreferencePolicy(
            target_crs_id=target,
            default_source_crs_id=default_source,
            default_height=_number(
                default_height,
                _number(
                    _config("VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT", DEFAULT_HEIGHT),
                    DEFAULT_HEIGHT,
                ),
            ),
            allow_geocoder=_boolean(
                allow_geocoder,
                _config("VECTOPLAN_APP_GEOREFERENCE_GEOCODER_ENABLED", False),
                False,
            ),
            discover_geocoder=_boolean(
                discover_geocoder,
                _config("VECTOPLAN_APP_GEOREFERENCE_DISCOVER_GEOCODER", False),
                False,
            ),
            allow_coordinate_swap=_boolean(
                allow_coordinate_swap,
                _config("VECTOPLAN_APP_GEOREFERENCE_ALLOW_COORDINATE_SWAP", False),
                False,
            ),
            allow_default_source_crs=_boolean(
                allow_default_source_crs,
                _config("VECTOPLAN_APP_GEOREFERENCE_ALLOW_DEFAULT_SOURCE_CRS", True),
                True,
            ),
            persist_result=_boolean(
                persist_result,
                _config("VECTOPLAN_APP_PERSIST_DERIVED_GEOREFERENCE", False),
                False,
            ),
            success_cache_ttl_seconds=max(
                0.0,
                _number(
                    _config(
                        "VECTOPLAN_APP_GEOREFERENCE_CACHE_SECONDS",
                        DEFAULT_SUCCESS_CACHE_TTL_SECONDS,
                    ),
                    DEFAULT_SUCCESS_CACHE_TTL_SECONDS,
                ),
            ),
            negative_cache_ttl_seconds=max(
                0.0,
                _number(
                    _config(
                        "VECTOPLAN_APP_GEOREFERENCE_NEGATIVE_CACHE_SECONDS",
                        DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
                    ),
                    DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
                ),
            ),
            cache_max_entries=max(
                16,
                _integer(
                    _config(
                        "VECTOPLAN_APP_GEOREFERENCE_CACHE_MAX_ENTRIES",
                        DEFAULT_CACHE_MAX_ENTRIES,
                    ),
                    DEFAULT_CACHE_MAX_ENTRIES,
                    16,
                    100_000,
                ),
            ),
            geocoder_timeout_seconds=max(
                0.1,
                _number(
                    _config(
                        "VECTOPLAN_APP_GEOREFERENCE_GEOCODER_TIMEOUT_SECONDS",
                        DEFAULT_GEOCODER_TIMEOUT_SECONDS,
                    ),
                    DEFAULT_GEOCODER_TIMEOUT_SECONDS,
                ),
            ),
            coordinate_precision=_integer(
                _config(
                    "VECTOPLAN_APP_GEOREFERENCE_COORDINATE_PRECISION",
                    DEFAULT_COORDINATE_PRECISION,
                ),
                DEFAULT_COORDINATE_PRECISION,
                6,
                15,
            ),
            height_precision=_integer(
                _config(
                    "VECTOPLAN_APP_GEOREFERENCE_HEIGHT_PRECISION",
                    DEFAULT_HEIGHT_PRECISION,
                ),
                DEFAULT_HEIGHT_PRECISION,
                0,
                12,
            ),
        )

    def _build_uncached(
        self,
        project: Any,
        *,
        explicit_reference: Optional[Mapping[str, Any]],
        policy: GeoreferencePolicy,
        request_id: Optional[str],
    ) -> ProjectEarthReferenceResult:
        project_public_id = _project_public_id(project)
        attempted_sources: list[str] = []
        rejected_sources: list[dict[str, Any]] = []
        warnings: tuple[str, ...] = ()

        candidate_factories: list[Callable[[], Optional[GeoreferenceCandidate]]] = []
        if explicit_reference is not None:
            candidate_factories.append(
                lambda: self._candidate_from_mapping(
                    explicit_reference,
                    source=GeoreferenceSource.EXPLICIT.value,
                    source_detail="explicit_reference",
                    policy=policy,
                )
            )

        candidate_factories.extend(
            [
                lambda: self._candidate_from_project_mapping(
                    project,
                    field_name="metadata_json",
                    source=GeoreferenceSource.PROJECT_METADATA.value,
                    policy=policy,
                ),
                lambda: self._candidate_from_project_mapping(
                    project,
                    field_name="settings",
                    source=GeoreferenceSource.PROJECT_SETTINGS.value,
                    policy=policy,
                ),
                lambda: self._candidate_from_project_mapping(
                    project,
                    field_name="service_refs",
                    source=GeoreferenceSource.PROJECT_SERVICE_REFS.value,
                    policy=policy,
                ),
                lambda: self._candidate_from_project_mapping(
                    project,
                    field_name="artifact_refs",
                    source=GeoreferenceSource.PROJECT_ARTIFACT_REFS.value,
                    policy=policy,
                ),
                lambda: self._candidate_from_project_columns(project, policy=policy),
            ]
        )

        for factory in candidate_factories:
            try:
                candidate = factory()
                if candidate is None:
                    continue
                attempted_sources.append(candidate.source)
                try:
                    result = self._candidate_to_result(
                        candidate,
                        project_public_id=project_public_id,
                        policy=policy,
                        request_id=request_id,
                    )
                    result.warnings = _append_unique(result.warnings, *warnings)
                    result.diagnostics = {
                        "attemptedSources": attempted_sources,
                        "rejectedSources": rejected_sources,
                        "cache": "miss",
                    }
                    return result
                except ProjectGeoreferenceError as exc:
                    rejected_sources.append(
                        {
                            "source": candidate.source,
                            "sourceDetail": candidate.source_detail,
                            "code": exc.code,
                            "retryable": exc.retryable,
                        }
                    )
                    warnings = _append_unique(
                        warnings,
                        f"candidate_rejected:{candidate.source}:{exc.code}",
                    )
            except ProjectGeoreferenceError as exc:
                rejected_sources.append(
                    {
                        "source": "candidate_factory",
                        "code": exc.code,
                        "retryable": exc.retryable,
                    }
                )
                warnings = _append_unique(
                    warnings,
                    f"candidate_factory_failed:{exc.code}",
                )
            except Exception as exc:
                LOGGER.exception(
                    "Project georeference candidate extraction failed for project=%s",
                    project_public_id or "unknown",
                )
                rejected_sources.append(
                    {
                        "source": "candidate_factory",
                        "code": "candidate_extraction_failed",
                        "exceptionType": exc.__class__.__name__,
                    }
                )
                warnings = _append_unique(
                    warnings,
                    f"candidate_extraction_failed:{exc.__class__.__name__}",
                )

        if policy.allow_geocoder:
            attempted_sources.append(GeoreferenceSource.GEOCODER.value)
            try:
                candidate = self._candidate_from_geocoder(
                    project,
                    policy=policy,
                    request_id=request_id,
                )
                if candidate is not None:
                    result = self._candidate_to_result(
                        candidate,
                        project_public_id=project_public_id,
                        policy=policy,
                        request_id=request_id,
                    )
                    result.warnings = _append_unique(result.warnings, *warnings)
                    result.diagnostics = {
                        "attemptedSources": attempted_sources,
                        "rejectedSources": rejected_sources,
                        "cache": "miss",
                    }
                    return result
            except ProjectGeoreferenceError as exc:
                rejected_sources.append(
                    {
                        "source": GeoreferenceSource.GEOCODER.value,
                        "code": exc.code,
                        "retryable": exc.retryable,
                    }
                )
                warnings = _append_unique(
                    warnings,
                    f"geocoder_failed:{exc.code}",
                )
            except Exception as exc:
                LOGGER.exception(
                    "Project geocoder integration failed for project=%s",
                    project_public_id or "unknown",
                )
                rejected_sources.append(
                    {
                        "source": GeoreferenceSource.GEOCODER.value,
                        "code": "geocoder_unexpected_error",
                        "exceptionType": exc.__class__.__name__,
                    }
                )
                warnings = _append_unique(
                    warnings,
                    f"geocoder_failed:{exc.__class__.__name__}",
                )

        address = _project_address(project)
        errors: tuple[str, ...] = (
            ("coordinates_unavailable",)
            if not address
            else ("coordinates_unavailable", "address_requires_geocoder")
        )
        return ProjectEarthReferenceResult(
            available=False,
            status=GeoreferenceStatus.UNAVAILABLE.value,
            project_public_id=project_public_id,
            source=GeoreferenceSource.UNAVAILABLE.value,
            reference=None,
            fingerprint=None,
            error_code="coordinates_unavailable",
            errors=errors,
            warnings=warnings,
            input_crs_id=None,
            target_crs_id=policy.target_crs_id,
            transformed=False,
            coordinate_swap_applied=False,
            height_defaulted=False,
            address_used=None,
            cache_hit=False,
            persisted=False,
            retryable=False,
            request_id=request_id,
            resolved_at=_iso(_utcnow()),
            diagnostics={
                "attemptedSources": attempted_sources,
                "rejectedSources": rejected_sources,
                "addressPresent": bool(address),
                "geocoderAllowed": policy.allow_geocoder,
                "cache": "miss",
            },
        )

    def _candidate_from_project_mapping(
        self,
        project: Any,
        *,
        field_name: str,
        source: str,
        policy: GeoreferencePolicy,
    ) -> Optional[GeoreferenceCandidate]:
        raw = _mapping(_attr(project, field_name))
        if not raw:
            return None

        for path in _REFERENCE_PATHS:
            value = _deep(raw, *path)
            if value is None:
                continue
            candidate = self._candidate_from_mapping(
                value,
                source=source,
                source_detail=f"{field_name}.{'.'.join(path)}",
                policy=policy,
            )
            if candidate is not None:
                return candidate

        for path in _GEOJSON_PATHS:
            value = _deep(raw, *path)
            if value is None:
                continue
            candidate = self._candidate_from_geojson(
                value,
                source=GeoreferenceSource.PROJECT_GEOJSON.value,
                source_detail=f"{field_name}.{'.'.join(path)}",
                policy=policy,
            )
            if candidate is not None:
                return candidate

        # Manche Altstände speichern die Referenz ohne Wrapper direkt im JSON.
        if _looks_like_coordinate_mapping(raw):
            return self._candidate_from_mapping(
                raw,
                source=source,
                source_detail=field_name,
                policy=policy,
            )
        return None

    def _candidate_from_mapping(
        self,
        raw: Any,
        *,
        source: str,
        source_detail: str,
        policy: GeoreferencePolicy,
    ) -> Optional[GeoreferenceCandidate]:
        mapping = _mapping(raw)
        if not mapping:
            return None

        if _is_geojson(mapping):
            return self._candidate_from_geojson(
                mapping,
                source=GeoreferenceSource.PROJECT_GEOJSON.value,
                source_detail=source_detail,
                policy=policy,
            )

        nested = _first_mapping(
            mapping.get("reference"),
            mapping.get("earthReference"),
            mapping.get("earth_reference"),
            mapping.get("globalReference"),
            mapping.get("global_reference"),
            mapping,
        )
        coordinate = _first_mapping(
            nested.get("coordinate"),
            nested.get("coordinates") if isinstance(nested.get("coordinates"), Mapping) else None,
        )

        x = _first_number(
            nested.get("longitude"),
            nested.get("lon"),
            nested.get("lng"),
            nested.get("x"),
            coordinate.get("longitude"),
            coordinate.get("x"),
        )
        y = _first_number(
            nested.get("latitude"),
            nested.get("lat"),
            nested.get("y"),
            coordinate.get("latitude"),
            coordinate.get("y"),
        )
        z = _first_number(
            nested.get("height"),
            nested.get("altitude"),
            nested.get("elevation"),
            nested.get("z"),
            coordinate.get("height"),
            coordinate.get("z"),
        )
        if x is None or y is None:
            return None

        source_crs = _normalize_crs_id(
            _first(
                nested.get("sourceCrsId"),
                nested.get("source_crs_id"),
                nested.get("crsId"),
                nested.get("crs_id"),
                nested.get("coordinateSrid"),
                nested.get("coordinate_srid"),
                nested.get("srid"),
                _deep(nested, "crs", "id"),
                _deep(nested, "crs", "code"),
            )
        )
        warnings: tuple[str, ...] = ()
        if source_crs is None and policy.allow_default_source_crs:
            source_crs = policy.default_source_crs_id
            warnings = _append_unique(warnings, "source_crs_defaulted")

        metadata = {
            "sourceDetail": source_detail,
        }
        accuracy = _first(
            nested.get("accuracy"),
            nested.get("accuracyMeters"),
            nested.get("accuracy_meters"),
        )
        if accuracy is not None:
            metadata["accuracyMeters"] = _number(accuracy, 0.0)

        return GeoreferenceCandidate(
            x=x,
            y=y,
            z=z,
            source_crs_id=source_crs,
            source=source,
            source_detail=source_detail,
            supplied_height=z is not None,
            metadata=metadata,
            warnings=warnings,
        )

    def _candidate_from_project_columns(
        self,
        project: Any,
        *,
        policy: GeoreferencePolicy,
    ) -> Optional[GeoreferenceCandidate]:
        x = _first_number(*(_attr(project, name) for name in _DIRECT_LONGITUDE_FIELDS))
        y = _first_number(*(_attr(project, name) for name in _DIRECT_LATITUDE_FIELDS))
        z = _first_number(*(_attr(project, name) for name in _DIRECT_HEIGHT_FIELDS))
        if x is None or y is None:
            return None

        raw_crs = _first(*(_attr(project, name) for name in _DIRECT_CRS_FIELDS))
        source_crs = _normalize_crs_id(raw_crs)
        warnings: tuple[str, ...] = ()
        if source_crs is None and policy.allow_default_source_crs:
            source_crs = policy.default_source_crs_id
            warnings = _append_unique(warnings, "source_crs_defaulted")

        return GeoreferenceCandidate(
            x=x,
            y=y,
            z=z,
            source_crs_id=source_crs,
            source=GeoreferenceSource.PROJECT_COLUMNS.value,
            source_detail="project.longitude_latitude",
            supplied_height=z is not None,
            metadata={
                "coordinateSrid": raw_crs,
            },
            warnings=warnings,
        )

    def _candidate_from_geojson(
        self,
        raw: Any,
        *,
        source: str,
        source_detail: str,
        policy: GeoreferencePolicy,
    ) -> Optional[GeoreferenceCandidate]:
        mapping = _mapping(raw)
        if not mapping:
            return None

        geometry = mapping
        if str(mapping.get("type") or "").lower() == "feature":
            geometry = _mapping(mapping.get("geometry"))
        if str(geometry.get("type") or "").lower() != "point":
            return None

        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)):
            return None
        if len(coordinates) < 2:
            return None

        x = _number_or_none(coordinates[0])
        y = _number_or_none(coordinates[1])
        z = _number_or_none(coordinates[2]) if len(coordinates) >= 3 else None
        if x is None or y is None:
            return None

        source_crs = _geojson_crs_id(mapping) or _geojson_crs_id(geometry)
        warnings: tuple[str, ...] = ()
        if source_crs is None and policy.allow_default_source_crs:
            source_crs = policy.default_source_crs_id
            warnings = _append_unique(warnings, "geojson_crs_defaulted")

        return GeoreferenceCandidate(
            x=x,
            y=y,
            z=z,
            source_crs_id=source_crs,
            source=source,
            source_detail=source_detail,
            supplied_height=z is not None,
            metadata={"geojsonType": "Point"},
            warnings=warnings,
        )

    def _candidate_from_geocoder(
        self,
        project: Any,
        *,
        policy: GeoreferencePolicy,
        request_id: Optional[str],
    ) -> Optional[GeoreferenceCandidate]:
        address = _project_address(project)
        if not address:
            return None

        geocoder = self.geocoder
        if geocoder is None and policy.discover_geocoder:
            geocoder = _discover_geocoder()
        if geocoder is None:
            raise ProjectGeoreferenceError(
                "geocoder_not_configured",
                "Geocoding is enabled but no geocoder adapter is configured.",
                status_code=503,
                retryable=True,
            )

        callable_obj = _geocoder_callable(geocoder)
        try:
            raw = _call_adapted(
                callable_obj,
                {
                    "address": address,
                    "query": address,
                    "project": project,
                    "request_id": request_id,
                    "timeout": policy.geocoder_timeout_seconds,
                    "timeout_seconds": policy.geocoder_timeout_seconds,
                    "limit": 1,
                },
                preferred_keys=("address", "project", "request_id", "timeout_seconds"),
            )
        except ProjectGeoreferenceError:
            raise
        except TimeoutError as exc:
            raise ProjectGeoreferenceError(
                "geocoder_timeout",
                "The configured geocoder timed out.",
                status_code=503,
                retryable=True,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise ProjectGeoreferenceError(
                "geocoder_request_failed",
                "The configured geocoder failed.",
                status_code=503,
                retryable=True,
                details={"exceptionType": exc.__class__.__name__},
                cause=exc,
            ) from exc

        item = _first_geocoder_result(raw)
        if item is None:
            return None
        candidate = self._candidate_from_mapping(
            item,
            source=GeoreferenceSource.GEOCODER.value,
            source_detail="geocoder.result[0]",
            policy=policy,
        )
        if candidate is None:
            return None
        return GeoreferenceCandidate(
            x=candidate.x,
            y=candidate.y,
            z=candidate.z,
            source_crs_id=candidate.source_crs_id,
            source=GeoreferenceSource.GEOCODER.value,
            source_detail=candidate.source_detail,
            address_used=address,
            supplied_height=candidate.supplied_height,
            metadata={
                **dict(candidate.metadata),
                "geocoder": _geocoder_name(geocoder),
            },
            warnings=candidate.warnings,
        )

    def _candidate_to_result(
        self,
        candidate: GeoreferenceCandidate,
        *,
        project_public_id: Optional[str],
        policy: GeoreferencePolicy,
        request_id: Optional[str],
    ) -> ProjectEarthReferenceResult:
        source_crs = _normalize_crs_id(candidate.source_crs_id)
        if source_crs is None:
            raise ProjectGeoreferenceError(
                "source_crs_missing",
                "The coordinate source CRS is missing.",
                status_code=422,
                details={"source": candidate.source},
            )

        x = _finite(candidate.x, "longitude_or_x")
        y = _finite(candidate.y, "latitude_or_y")
        height_defaulted = candidate.z is None
        z = (
            _finite(candidate.z, "height")
            if candidate.z is not None
            else _finite(policy.default_height, "default_height")
        )

        swap_applied = False
        warnings = tuple(candidate.warnings)
        if _is_geographic_crs(source_crs):
            x, y, swap_applied, range_warnings = _validate_geographic_order(
                x,
                y,
                allow_swap=policy.allow_coordinate_swap,
            )
            warnings = _append_unique(warnings, *range_warnings)

        transformed = source_crs != policy.target_crs_id
        if transformed:
            x, y, z = self._transform_coordinates(
                x,
                y,
                z,
                source_crs_id=source_crs,
                target_crs_id=policy.target_crs_id,
            )

        # Das Ziel für Earth muss nach der Transformation geografisch sein.
        if _is_geographic_crs(policy.target_crs_id):
            x, y, _, target_warnings = _validate_geographic_order(
                x,
                y,
                allow_swap=False,
            )
            warnings = _append_unique(warnings, *target_warnings)

        longitude = round(x, policy.coordinate_precision)
        latitude = round(y, policy.coordinate_precision)
        height = round(z, policy.height_precision)
        reference_core = {
            "longitude": longitude,
            "latitude": latitude,
            "height": height,
            "crsId": policy.target_crs_id,
            "alwaysXY": True,
        }
        fingerprint = _fingerprint(reference_core)
        reference = {
            **reference_core,
            "schemaVersion": REFERENCE_SCHEMA_VERSION,
            "referenceVersion": 1,
            "fingerprint": fingerprint,
            "coordinate": {
                "x": longitude,
                "y": latitude,
                "z": height,
                "dimension": 3,
            },
            "source": {
                "type": candidate.source,
                "detail": candidate.source_detail,
                "inputCrsId": source_crs,
                "transformed": transformed,
                "coordinateSwapApplied": swap_applied,
                "heightDefaulted": height_defaulted,
            },
        }
        clean_metadata = _sanitize(candidate.metadata, depth=3)
        if clean_metadata:
            reference["metadata"] = clean_metadata

        return ProjectEarthReferenceResult(
            available=True,
            status=GeoreferenceStatus.AVAILABLE.value,
            project_public_id=project_public_id,
            source=candidate.source,
            reference=reference,
            fingerprint=fingerprint,
            error_code=None,
            errors=(),
            warnings=warnings,
            input_crs_id=source_crs,
            target_crs_id=policy.target_crs_id,
            transformed=transformed,
            coordinate_swap_applied=swap_applied,
            height_defaulted=height_defaulted,
            address_used=candidate.address_used,
            cache_hit=False,
            persisted=False,
            retryable=False,
            request_id=request_id,
            resolved_at=_iso(_utcnow()),
            diagnostics={
                "sourceDetail": candidate.source_detail,
                "policyVersion": POLICY_VERSION,
            },
        )

    def _transform_coordinates(
        self,
        x: float,
        y: float,
        z: float,
        *,
        source_crs_id: str,
        target_crs_id: str,
    ) -> tuple[float, float, float]:
        # EPSG:4326 -> EPSG:4979 ist eine reine 2D-zu-3D-WGS84-Erweiterung.
        if source_crs_id == "EPSG:4326" and target_crs_id == "EPSG:4979":
            return x, y, z
        if source_crs_id == target_crs_id:
            return x, y, z

        transformer = _get_transformer(
            source_crs_id,
            target_crs_id,
            factory=self.transformer_factory,
        )
        if transformer is None:
            raise ProjectGeoreferenceError(
                "coordinate_transform_unavailable",
                "The project coordinate CRS cannot be transformed because no compatible transformer is available.",
                status_code=422,
                retryable=False,
                details={
                    "sourceCrsId": source_crs_id,
                    "targetCrsId": target_crs_id,
                },
            )

        try:
            if hasattr(transformer, "transform"):
                try:
                    transformed = transformer.transform(x, y, z, errcheck=True)
                except TypeError:
                    transformed = transformer.transform(x, y, z)
            elif callable(transformer):
                transformed = transformer(x, y, z)
            else:
                raise TypeError("Transformer is neither callable nor exposes transform().")

            if not isinstance(transformed, Sequence) or len(transformed) < 2:
                raise ValueError("Transformer returned no coordinate sequence.")
            tx = _finite(transformed[0], "transformed_x")
            ty = _finite(transformed[1], "transformed_y")
            tz = _finite(transformed[2], "transformed_z") if len(transformed) >= 3 else z
            return tx, ty, tz
        except ProjectGeoreferenceError:
            raise
        except Exception as exc:
            raise ProjectGeoreferenceError(
                "coordinate_transform_failed",
                "The project coordinates could not be transformed into the Earth target CRS.",
                status_code=422,
                retryable=False,
                details={
                    "sourceCrsId": source_crs_id,
                    "targetCrsId": target_crs_id,
                    "exceptionType": exc.__class__.__name__,
                },
                cause=exc,
            ) from exc

    def _persist_result(
        self,
        project: Any,
        result: ProjectEarthReferenceResult,
        *,
        commit: bool,
    ) -> bool:
        if project is None:
            return False

        metadata = _mapping(_attr(project, "metadata_json"))
        metadata[METADATA_NAMESPACE] = {
            "schemaVersion": REFERENCE_SCHEMA_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "status": result.status,
            "available": result.available,
            "source": result.source,
            "fingerprint": result.fingerprint,
            "reference": copy.deepcopy(result.reference),
            "errorCode": result.error_code,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "resolvedAt": result.resolved_at,
        }
        try:
            setattr(project, "metadata_json", metadata)
        except Exception as exc:
            raise ProjectGeoreferenceError(
                "georeference_metadata_write_failed",
                "The derived project georeference could not be written to project metadata.",
                status_code=500,
                details={"exceptionType": exc.__class__.__name__},
                cause=exc,
            ) from exc

        # Vorbereitete explizite Modellfelder werden automatisch bedient, sobald
        # sie existieren. Fehlende Felder sind im aktuellen Schema kein Fehler.
        _set_if_present(project, "earth_reference_fingerprint", result.fingerprint)
        _set_if_present(project, "georeference_status", result.status)
        _set_if_present(project, "georeference_source", result.source)
        _set_if_present(project, "georeference_resolved_at", _utcnow())

        if self.session is not None:
            try:
                if hasattr(self.session, "add"):
                    self.session.add(project)
                if commit and hasattr(self.session, "commit"):
                    self.session.commit()
                elif hasattr(self.session, "flush"):
                    self.session.flush()
            except Exception:
                if hasattr(self.session, "rollback"):
                    try:
                        self.session.rollback()
                    except Exception:
                        LOGGER.exception("Rollback after georeference persistence failed.")
                raise
        return True

    @staticmethod
    def _error_result(
        *,
        project_public_id: Optional[str],
        policy: GeoreferencePolicy,
        request_id: Optional[str],
        error: ProjectGeoreferenceError,
    ) -> ProjectEarthReferenceResult:
        return ProjectEarthReferenceResult(
            available=False,
            status=GeoreferenceStatus.ERROR.value,
            project_public_id=project_public_id,
            source=GeoreferenceSource.UNAVAILABLE.value,
            reference=None,
            fingerprint=None,
            error_code=error.code,
            errors=(error.code,),
            warnings=(),
            input_crs_id=None,
            target_crs_id=policy.target_crs_id,
            transformed=False,
            coordinate_swap_applied=False,
            height_defaulted=False,
            address_used=None,
            cache_hit=False,
            persisted=False,
            retryable=error.retryable,
            request_id=request_id,
            resolved_at=_iso(_utcnow()),
            diagnostics={"error": error.to_dict()},
        )


# ---------------------------------------------------------------------------
# Öffentliche Fassade
# ---------------------------------------------------------------------------


def build_project_earth_reference(
    project: Any,
    *,
    explicit_reference: Optional[Mapping[str, Any]] = None,
    default_height: Optional[float] = None,
    crs_id: Optional[str] = None,
    source_crs_id: Optional[str] = None,
    required: bool = False,
    allow_geocoder: Optional[bool] = None,
    discover_geocoder: Optional[bool] = None,
    allow_coordinate_swap: Optional[bool] = None,
    allow_default_source_crs: Optional[bool] = None,
    persist_result: Optional[bool] = None,
    use_cache: bool = True,
    force: bool = False,
    commit: bool = False,
    raise_on_error: bool = False,
    request_id: Optional[str] = None,
    session: Any = None,
    geocoder: Any = None,
    transformer_factory: Optional[TransformerFactoryProtocol] = None,
) -> ProjectEarthReferenceResult:
    """Kompatible Funktionsfassade für die Chunk-Provisionierungsorchestrierung."""

    service = ProjectGeoreferenceService(
        session=session,
        geocoder=geocoder,
        transformer_factory=transformer_factory,
    )
    return service.build(
        project,
        explicit_reference=explicit_reference,
        default_height=default_height,
        crs_id=crs_id,
        source_crs_id=source_crs_id,
        required=required,
        allow_geocoder=allow_geocoder,
        discover_geocoder=discover_geocoder,
        allow_coordinate_swap=allow_coordinate_swap,
        allow_default_source_crs=allow_default_source_crs,
        persist_result=persist_result,
        use_cache=use_cache,
        force=force,
        commit=commit,
        raise_on_error=raise_on_error,
        request_id=request_id,
    )


def serialize_project_georeference_status(project: Any) -> dict[str, Any]:
    """Liest den zuletzt optional persistierten Georeferenzstatus ohne Mutation."""

    metadata = _mapping(_attr(project, "metadata_json"))
    state = _mapping(metadata.get(METADATA_NAMESPACE))
    return {
        "resultVersion": RESULT_VERSION,
        "serviceVersion": SERVICE_VERSION,
        "projectPublicId": _project_public_id(project),
        "available": bool(state.get("available")),
        "status": _text(state.get("status"), GeoreferenceStatus.UNAVAILABLE.value, 64),
        "source": _text(state.get("source"), GeoreferenceSource.UNAVAILABLE.value, 128),
        "fingerprint": _text(state.get("fingerprint"), "", 128) or None,
        "errorCode": _text(state.get("errorCode"), "", 128) or None,
        "errors": list(_strings(state.get("errors"))),
        "warnings": list(_strings(state.get("warnings"))),
        "resolvedAt": _text(state.get("resolvedAt"), "", 64) or None,
    }


def clear_project_georeference_cache(project_public_id: Optional[str] = None) -> int:
    """Leert den gesamten Cache oder nur Einträge eines App-Projekts."""

    with _CACHE_LOCK:
        if project_public_id is None:
            count = len(_CACHE)
            _CACHE.clear()
            return count
        prefix = f"project:{_text(project_public_id, '', 240)}|"
        keys = [key for key in _CACHE if key.startswith(prefix)]
        for key in keys:
            _CACHE.pop(key, None)
        return len(keys)


def clear_coordinate_transformer_cache() -> int:
    with _TRANSFORMER_CACHE_LOCK:
        count = len(_TRANSFORMER_CACHE)
        _TRANSFORMER_CACHE.clear()
        return count


resolve_project_earth_reference = build_project_earth_reference
get_project_georeference_status = serialize_project_georeference_status


# ---------------------------------------------------------------------------
# Cache und Locks
# ---------------------------------------------------------------------------


def _cache_key(
    project: Any,
    *,
    explicit_reference: Optional[Mapping[str, Any]],
    policy: GeoreferencePolicy,
) -> str:
    public_id = _project_public_id(project)
    identity = f"project:{public_id}" if public_id else f"object:{id(project)}"
    relevant = {
        "explicit": _sanitize(explicit_reference or {}, depth=5),
        "direct": {
            "longitude": _first(*(_attr(project, name) for name in _DIRECT_LONGITUDE_FIELDS)),
            "latitude": _first(*(_attr(project, name) for name in _DIRECT_LATITUDE_FIELDS)),
            "height": _first(*(_attr(project, name) for name in _DIRECT_HEIGHT_FIELDS)),
            "crs": _first(*(_attr(project, name) for name in _DIRECT_CRS_FIELDS)),
        },
        "address": _project_address(project),
        "mappings": {
            field_name: _sanitize(_mapping(_attr(project, field_name)), depth=5)
            for field_name in _PROJECT_MAPPING_FIELDS
        },
        "policy": policy.to_dict(),
    }
    digest = hashlib.sha256(_canonical_json(relevant).encode("utf-8")).hexdigest()
    return f"{identity}|{digest}"


def _cache_get(key: str) -> Optional[ProjectEarthReferenceResult]:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(entry.result)


def _cache_set(
    key: str,
    result: ProjectEarthReferenceResult,
    *,
    ttl_seconds: float,
    max_entries: int,
) -> None:
    if ttl_seconds <= 0:
        return
    entry = _CacheEntry(
        expires_at=time.monotonic() + ttl_seconds,
        result=copy.deepcopy(result),
    )
    with _CACHE_LOCK:
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        while len(_CACHE) > max_entries:
            _CACHE.popitem(last=False)


def _project_lock(key: str):
    class _LockContext:
        def __enter__(self_inner):
            with _PROJECT_LOCKS_LOCK:
                lock = _PROJECT_LOCKS.get(key)
                if lock is None:
                    lock = threading.RLock()
                    _PROJECT_LOCKS[key] = lock
                self_inner.lock = lock
            self_inner.lock.acquire()
            return self_inner.lock

        def __exit__(self_inner, exc_type, exc, tb):
            self_inner.lock.release()
            return False

    return _LockContext()


# ---------------------------------------------------------------------------
# Transformation und Geocoder-Adapter
# ---------------------------------------------------------------------------


def _get_transformer(
    source_crs_id: str,
    target_crs_id: str,
    *,
    factory: Optional[TransformerFactoryProtocol],
) -> Any:
    cache_key = (source_crs_id, target_crs_id)
    with _TRANSFORMER_CACHE_LOCK:
        if cache_key in _TRANSFORMER_CACHE:
            return _TRANSFORMER_CACHE[cache_key]

    transformer = None
    if factory is not None:
        try:
            transformer = factory(source_crs_id, target_crs_id)
        except Exception as exc:
            raise ProjectGeoreferenceError(
                "coordinate_transformer_factory_failed",
                "The configured coordinate transformer factory failed.",
                status_code=500,
                details={"exceptionType": exc.__class__.__name__},
                cause=exc,
            ) from exc
    else:
        try:
            pyproj = import_module("pyproj")
            transformer_type = getattr(pyproj, "Transformer", None)
            if transformer_type is not None:
                transformer = transformer_type.from_crs(
                    source_crs_id,
                    target_crs_id,
                    always_xy=True,
                )
        except ModuleNotFoundError:
            transformer = None
        except Exception as exc:
            raise ProjectGeoreferenceError(
                "coordinate_transformer_initialization_failed",
                "The coordinate transformer could not be initialized.",
                status_code=422,
                details={
                    "sourceCrsId": source_crs_id,
                    "targetCrsId": target_crs_id,
                    "exceptionType": exc.__class__.__name__,
                },
                cause=exc,
            ) from exc

    if transformer is not None:
        with _TRANSFORMER_CACHE_LOCK:
            _TRANSFORMER_CACHE[cache_key] = transformer
    return transformer


def _discover_geocoder() -> Any:
    candidates = (
        ("services.geocoding_client", "geocode_address"),
        ("services.geocoder_client", "geocode_address"),
        ("services.geocoding_service", "geocode_address"),
    )
    for module_name, attribute_name in candidates:
        try:
            candidate = getattr(import_module(module_name), attribute_name, None)
            if callable(candidate):
                return candidate
        except Exception:
            continue
    return None


def _geocoder_callable(geocoder: Any) -> Callable[..., Any]:
    if callable(geocoder) and not inspect.ismodule(geocoder):
        return geocoder
    for name in ("geocode", "geocode_address", "resolve_address"):
        candidate = getattr(geocoder, name, None)
        if callable(candidate):
            return candidate
    raise ProjectGeoreferenceError(
        "geocoder_contract_missing",
        "The configured geocoder exposes no supported geocoding function.",
        status_code=503,
        retryable=False,
        details={"expectedFunctions": ["geocode", "geocode_address", "resolve_address"]},
    )


def _geocoder_name(geocoder: Any) -> str:
    if inspect.ismodule(geocoder):
        return _text(getattr(geocoder, "__name__", None), "module", 160)
    return _text(
        getattr(geocoder, "name", None)
        or getattr(geocoder, "__name__", None)
        or geocoder.__class__.__name__,
        "geocoder",
        160,
    )


def _first_geocoder_result(raw: Any) -> Optional[Mapping[str, Any]]:
    if raw is None:
        return None
    if is_dataclass(raw):
        raw = asdict(raw)
    elif hasattr(raw, "to_dict") and callable(raw.to_dict):
        raw = raw.to_dict()

    if isinstance(raw, Mapping):
        if raw.get("ok") is False:
            error = _mapping(raw.get("error"))
            raise ProjectGeoreferenceError(
                _code(raw.get("code") or error.get("code"), "geocoder_rejected"),
                _text(
                    raw.get("message") or error.get("message"),
                    "The configured geocoder rejected the request.",
                    1000,
                ),
                status_code=_integer(raw.get("statusCode"), 503, 400, 599),
                retryable=bool(raw.get("retryable", True)),
            )
        for key in ("result", "data", "location", "reference"):
            nested = raw.get(key)
            if isinstance(nested, Mapping) and _looks_like_coordinate_mapping(nested):
                return nested
        for key in ("results", "items", "features", "candidates"):
            nested = raw.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                for item in nested:
                    mapping = _mapping(item)
                    if mapping:
                        if str(mapping.get("type") or "").lower() == "feature":
                            return mapping
                        if _looks_like_coordinate_mapping(mapping):
                            return mapping
        if _looks_like_coordinate_mapping(raw) or _is_geojson(raw):
            return raw
        return None

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for item in raw:
            mapping = _mapping(item)
            if mapping:
                return mapping
    return None


def _call_adapted(
    callable_obj: Callable[..., Any],
    kwargs: Mapping[str, Any],
    *,
    preferred_keys: Optional[Sequence[str]] = None,
) -> Any:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        selected = (
            {key: kwargs[key] for key in preferred_keys if key in kwargs}
            if preferred_keys
            else dict(kwargs)
        )
        return callable_obj(**selected)

    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs:
        return callable_obj(**dict(kwargs))

    selected: dict[str, Any] = {}
    for name, parameter in parameters.items():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        if name in kwargs:
            selected[name] = kwargs[name]
    if not selected and preferred_keys:
        selected = {key: kwargs[key] for key in preferred_keys if key in parameters}
    return callable_obj(**selected)


# ---------------------------------------------------------------------------
# Extraktion, Validierung und Serialisierung
# ---------------------------------------------------------------------------


def _validate_geographic_order(
    x: float,
    y: float,
    *,
    allow_swap: bool,
) -> tuple[float, float, bool, tuple[str, ...]]:
    if -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0:
        return x, y, False, ()

    swap_plausible = -180.0 <= y <= 180.0 and -90.0 <= x <= 90.0
    if swap_plausible and allow_swap:
        return y, x, True, ("coordinate_order_swapped",)
    if swap_plausible:
        raise ProjectGeoreferenceError(
            "coordinate_order_suspected_swapped",
            "The coordinate order appears to be latitude/longitude instead of longitude/latitude.",
            status_code=422,
            details={"allowCoordinateSwap": False},
        )
    if not -180.0 <= x <= 180.0:
        raise ProjectGeoreferenceError(
            "longitude_out_of_range",
            "Longitude must be between -180 and 180 degrees.",
            status_code=422,
        )
    raise ProjectGeoreferenceError(
        "latitude_out_of_range",
        "Latitude must be between -90 and 90 degrees.",
        status_code=422,
    )


def _is_geographic_crs(crs_id: str) -> bool:
    normalized = _normalize_crs_id(crs_id)
    if normalized in {"EPSG:4326", "EPSG:4979", "OGC:CRS84"}:
        return True
    try:
        pyproj = import_module("pyproj")
        crs_type = getattr(pyproj, "CRS", None)
        if crs_type is not None:
            return bool(crs_type.from_user_input(normalized).is_geographic)
    except Exception:
        pass
    return False


def _normalize_crs_id(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"EPSG:{value}"
    if isinstance(value, float) and value.is_integer():
        return f"EPSG:{int(value)}"
    if isinstance(value, Mapping):
        authority = _text(value.get("authority"), "", 32).upper()
        code = _text(value.get("code") or value.get("id"), "", 64)
        if authority and code:
            return f"{authority}:{code}"
        return _normalize_crs_id(value.get("name") or value.get("href"))

    text = _text(value, "", 256).strip()
    if not text:
        return None
    upper = text.upper().replace(" ", "")
    if upper.isdigit():
        return f"EPSG:{int(upper)}"
    epsg_match = re.search(r"EPSG(?::|/|::)(\d+)$", upper)
    if epsg_match:
        return f"EPSG:{int(epsg_match.group(1))}"
    urn_match = re.search(r"EPSG(?::|/)+0*(\d+)$", upper)
    if urn_match:
        return f"EPSG:{int(urn_match.group(1))}"
    if upper in {"CRS84", "OGC:CRS84"}:
        return "OGC:CRS84"
    if re.fullmatch(r"[A-Z][A-Z0-9_-]{1,31}:[A-Z0-9_.-]{1,64}", upper):
        return upper
    return text


def _geojson_crs_id(mapping: Mapping[str, Any]) -> Optional[str]:
    crs = mapping.get("crs")
    if isinstance(crs, str):
        return _normalize_crs_id(crs)
    if isinstance(crs, Mapping):
        properties = _mapping(crs.get("properties"))
        return _normalize_crs_id(
            properties.get("name")
            or properties.get("href")
            or crs.get("name")
            or crs.get("id")
        )
    return None


def _is_geojson(mapping: Mapping[str, Any]) -> bool:
    return str(mapping.get("type") or "").lower() in {"point", "feature"}


def _looks_like_coordinate_mapping(mapping: Mapping[str, Any]) -> bool:
    keys = {str(key) for key in mapping.keys()}
    return bool(
        keys
        & {
            "longitude",
            "latitude",
            "lon",
            "lat",
            "lng",
            "x",
            "y",
            "coordinate",
            "coordinates",
            "geometry",
        }
    )


def _project_address(project: Any) -> Optional[str]:
    direct = _text(_attr(project, "address_text"), "", 2000).strip()
    if direct:
        return direct
    parts = [
        _text(_attr(project, "street"), "", 500),
        _text(_attr(project, "house_number"), "", 100),
        _text(_attr(project, "postal_code"), "", 100),
        _text(_attr(project, "city"), "", 500),
        _text(_attr(project, "region"), "", 500),
        _text(_attr(project, "country"), "", 500),
    ]
    normalized = [part.strip() for part in parts if part and part.strip()]
    return ", ".join(normalized) if normalized else None


def _project_public_id(project: Any) -> Optional[str]:
    return _text(
        _first(
            _attr(project, "public_id"),
            _attr(project, "project_public_id"),
            _attr(project, "id") if isinstance(_attr(project, "id"), str) else None,
        ),
        "",
        240,
    ) or None


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            value = value.to_dict()
        except Exception:
            return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _deep(mapping: Mapping[str, Any], *path: str) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapping = _mapping(value)
        if mapping:
            return mapping
    return {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        number = _number_or_none(value)
        if number is not None:
            return number
    return None


def _number_or_none(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _finite(value: Any, field_name: str) -> float:
    number = _number_or_none(value)
    if number is None:
        raise ProjectGeoreferenceError(
            f"{_code(field_name, 'coordinate')}_invalid",
            f"{field_name} must be a finite number.",
            status_code=422,
        )
    return number


def _fingerprint(reference: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(reference).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_safe(value: Any, *, depth: int = 8) -> Any:
    if depth < 0:
        return None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value), depth=depth - 1)
    if isinstance(value, Mapping):
        return {
            _text(key, "", 256): _json_safe(item, depth=depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item, depth=depth - 1) for item in value]
    return _text(value, "", 2000)


def _sanitize(value: Any, *, depth: int) -> Any:
    if depth < 0:
        return None
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = _text(key, "", 256)
            lower = key_text.lower()
            if any(part in lower for part in _SENSITIVE_KEY_PARTS):
                output[key_text] = "[redacted]"
            else:
                output[key_text] = _sanitize(item, depth=depth - 1)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item, depth=depth - 1) for item in value]
    return _json_safe(value, depth=depth)


def _append_unique(values: Sequence[str], *new_values: Any) -> tuple[str, ...]:
    result = [item for item in values if item]
    seen = set(result)
    for value in new_values:
        for item in _strings(value):
            if item and item not in seen:
                result.append(item)
                seen.add(item)
    return tuple(result)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output: list[str] = []
        for item in value:
            text = _text(item, "", 500).strip()
            if text:
                output.append(text)
        return tuple(output)
    text = _text(value, "", 500).strip()
    return (text,) if text else ()


def _attr(instance: Any, name: str) -> Any:
    if instance is None:
        return None
    if isinstance(instance, Mapping):
        return instance.get(name)
    try:
        return getattr(instance, name, None)
    except Exception:
        return None


def _set_if_present(instance: Any, name: str, value: Any) -> bool:
    if instance is None or not hasattr(instance, name):
        return False
    try:
        setattr(instance, name, value)
        return True
    except Exception:
        return False


def _text(value: Any, default: str = "", limit: int = 1000) -> str:
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        return default
    text = text.strip()
    return text[:limit] if text else default


def _code(value: Any, default: str) -> str:
    text = _text(value, default, 160).lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "_", text).strip("_")
    return text or default


def _number(value: Any, default: float) -> float:
    number = _number_or_none(value)
    return default if number is None else number


def _integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    return max(minimum, min(maximum, result))


def _boolean(explicit: Any, configured: Any, default: bool) -> bool:
    value = explicit if explicit is not None else configured
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _text(value, "", 32).lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _config(name: str, default: Any = None) -> Any:
    try:
        flask = import_module("flask")
        has_app_context = getattr(flask, "has_app_context", None)
        current_app = getattr(flask, "current_app", None)
        if callable(has_app_context) and has_app_context() and current_app is not None:
            if name in current_app.config:
                return current_app.config.get(name)
    except Exception:
        pass
    return os.getenv(name, default)


def _db_session() -> Any:
    candidates = (
        ("extensions", "db"),
        ("models.base", "db"),
        ("models", "db"),
    )
    for module_name, attribute_name in candidates:
        try:
            db = getattr(import_module(module_name), attribute_name, None)
            session = getattr(db, "session", None)
            if session is not None:
                return session
        except Exception:
            continue
    return None


def _request_id() -> Optional[str]:
    try:
        flask = import_module("flask")
        has_request_context = getattr(flask, "has_request_context", None)
        request = getattr(flask, "request", None)
        if callable(has_request_context) and has_request_context() and request is not None:
            return _text(
                request.headers.get("X-Request-ID")
                or request.headers.get("X-Correlation-ID"),
                "",
                240,
            ) or None
    except Exception:
        return None
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _current_exception() -> ProjectGeoreferenceError:
    import sys

    exc = sys.exc_info()[1]
    if isinstance(exc, ProjectGeoreferenceError):
        return exc
    return ProjectGeoreferenceError(
        "project_georeference_failed",
        _text(exc, "Project georeference failed.", 1000),
        status_code=500,
        cause=exc if isinstance(exc, BaseException) else None,
    )


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_SOURCE_CRS_ID",
    "DEFAULT_TARGET_CRS_ID",
    "GeoreferenceCandidate",
    "GeoreferencePolicy",
    "GeoreferenceSource",
    "GeoreferenceStatus",
    "ProjectEarthReferenceResult",
    "ProjectGeoreferenceError",
    "ProjectGeoreferenceService",
    "SERVICE_VERSION",
    "build_project_earth_reference",
    "clear_coordinate_transformer_cache",
    "clear_project_georeference_cache",
    "get_project_georeference_status",
    "resolve_project_earth_reference",
    "serialize_project_georeference_status",
]
