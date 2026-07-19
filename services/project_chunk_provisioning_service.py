# services/vectoplan-app/services/project_chunk_provisioning_service.py
"""Automatische Chunk-Provisionierung für Projekte aus ``vectoplan-app``.

Das Modul orchestriert ausschließlich den App-seitigen Ablauf. HTTP-Transport
zu ``vectoplan-chunk`` bleibt Aufgabe von ``services.chunk_client``; die spätere
Georeferenzlogik bleibt Aufgabe von ``services.project_georeference_service``.

Invarianten
-----------
* Als Owner wird nur die kanonische ``auth_user_id`` aus ``vectoplan-auth``
  verwendet. Die lokale ``AppUser.id`` wird niemals an Chunk gesendet.
* Neue App-Projekte fordern standardmäßig ``earth`` an.
* Fehlt eine gültige Earth-Referenz, wird kontrolliert ``flat`` verwendet.
* Technische Fehler wie Timeout, DNS, Datenbankfehler oder HTTP 5xx lösen keinen
  stillen Flat-Fallback aus.
* Ein bestehender Welttyp wird bei Retry nicht unbemerkt gewechselt.
* Der Chunk-Aufruf ist idempotent und besitzt einen stabilen Idempotency-Key.
* Erfolg und Fehler werden best-effort am App-Projekt gespeichert, damit eine
  spätere Reparatur möglich bleibt.
* Das Modul legt keine Tabellen an, führt keine Migrationen aus und erzeugt
  keine Benutzerkonten.

Bevorzugter Vertrag zu ``services.chunk_client``::

    provision_chunk_project_for_app_project(
        *,
        app_project_public_id: str,
        owner_user_id: str,
        requested_world_template: str,
        fallback_world_template: str,
        allow_world_fallback: bool,
        earth_reference: dict | None,
        idempotency_key: str,
        request_id: str | None,
    ) -> Mapping[str, Any]

Als Übergang wird zusätzlich ``ensure_chunk_project_for_app_project`` erkannt.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib import import_module
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol, Sequence


LOGGER = logging.getLogger(__name__)

SERVICE_VERSION = "1.0.0"
POLICY_VERSION = "app-chunk-provisioning-policy.v1"
RESULT_VERSION = "app-chunk-provisioning-result.v1"
METADATA_NAMESPACE = "chunkProvisioning"

DEFAULT_WORLD_TEMPLATE = "earth"
DEFAULT_FALLBACK_TEMPLATE = "flat"
DEFAULT_EARTH_CRS_ID = "EPSG:4979"
DEFAULT_EARTH_HEIGHT = 0.0
DEFAULT_CACHE_TTL_SECONDS = 10.0
SUPPORTED_WORLD_TEMPLATES = frozenset({"earth", "flat"})

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

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "raw_key",
)


class ProvisioningStatus(str, Enum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    READY = "ready"
    FALLBACK_READY = "fallback_ready"
    FAILED = "failed"
    REPAIR_REQUIRED = "repair_required"


class ProjectChunkProvisioningError(RuntimeError):
    """Stabiler Fehlervertrag für Route, Service und Tests."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 500,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = _code(code, "chunk_provisioning_failed")
        self.message = _text(message, "Chunk provisioning failed.", 1000)
        self.status_code = _integer(status_code, 500, 400, 599)
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


class ChunkClientProtocol(Protocol):
    def provision_chunk_project_for_app_project(self, **kwargs: Any) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class ProvisioningPolicy:
    requested_template: str
    fallback_template: str
    allow_fallback: bool
    allow_client_fallback: bool
    allow_existing_template_change: bool
    default_earth_height: float
    earth_crs_id: str
    cache_ttl_seconds: float
    persist_failure_state: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": POLICY_VERSION,
            "requestedWorldTemplate": self.requested_template,
            "fallbackWorldTemplate": self.fallback_template,
            "allowWorldFallback": self.allow_fallback,
            "allowClientSideFallback": self.allow_client_fallback,
            "allowExistingTemplateChange": self.allow_existing_template_change,
            "defaultEarthHeight": self.default_earth_height,
            "earthCrsId": self.earth_crs_id,
            "cacheTtlSeconds": self.cache_ttl_seconds,
            "persistFailureState": self.persist_failure_state,
        }


@dataclass(frozen=True)
class EarthReferenceResolution:
    available: bool
    reference: Optional[dict[str, Any]] = None
    source: str = "unavailable"
    fingerprint: Optional[str] = None
    error_code: Optional[str] = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reference": self.reference,
            "source": self.source,
            "fingerprint": self.fingerprint,
            "errorCode": self.error_code,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class ProjectChunkProvisioningResult:
    ok: bool
    code: str
    status: str
    project_public_id: str
    owner_user_id: Optional[str]
    requested_world_template: str
    effective_world_template: Optional[str] = None
    fallback_world_template: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    chunk_project_id: Optional[str] = None
    chunk_universe_id: Optional[str] = None
    chunk_world_id: Optional[str] = None
    earth_reference_fingerprint: Optional[str] = None
    created: bool = False
    updated: bool = False
    reused: bool = False
    cached: bool = False
    retryable: bool = False
    status_code: int = 200
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    error: Optional[dict[str, Any]] = None
    response_summary: dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resultVersion": RESULT_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "ok": self.ok,
            "code": self.code,
            "status": self.status,
            "projectPublicId": self.project_public_id,
            "ownerUserId": self.owner_user_id,
            "requestedWorldTemplate": self.requested_world_template,
            "effectiveWorldTemplate": self.effective_world_template,
            "fallbackWorldTemplate": self.fallback_world_template,
            "fallbackUsed": self.fallback_used,
            "fallbackReason": self.fallback_reason,
            "chunkProjectId": self.chunk_project_id,
            "chunkUniverseId": self.chunk_universe_id,
            "chunkWorldId": self.chunk_world_id,
            "earthReferenceFingerprint": self.earth_reference_fingerprint,
            "created": self.created,
            "updated": self.updated,
            "reused": self.reused,
            "cached": self.cached,
            "retryable": self.retryable,
            "statusCode": self.status_code,
            "requestId": self.request_id,
            "idempotencyKey": self.idempotency_key,
            "warnings": list(self.warnings),
            "error": self.error,
            "responseSummary": self.response_summary,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: ProjectChunkProvisioningResult


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_LOCK = threading.RLock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_LOCKS_LOCK = threading.RLock()


class ProjectChunkProvisioningService:
    """Transaktionsbewusste App-seitige Provisionierungsorchestrierung."""

    def __init__(
        self,
        *,
        session: Any = None,
        chunk_client: Any = None,
        georeference_builder: Optional[Callable[..., Any]] = None,
        audit_writer: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.session = session or _db_session()
        self.chunk_client = chunk_client
        self.georeference_builder = georeference_builder
        self.audit_writer = audit_writer

    def provision(
        self,
        project: Any,
        *,
        owner_auth_user_id: Optional[str] = None,
        requested_world_template: Optional[str] = None,
        fallback_world_template: Optional[str] = None,
        allow_world_fallback: Optional[bool] = None,
        allow_client_side_fallback: Optional[bool] = None,
        allow_existing_template_change: Optional[bool] = None,
        earth_reference: Optional[Mapping[str, Any]] = None,
        force: bool = False,
        commit: bool = True,
        persist_failure_state: Optional[bool] = None,
        raise_on_error: bool = False,
        request_id: Optional[str] = None,
    ) -> ProjectChunkProvisioningResult:
        """Provisioniert oder repariert den Chunk-Graph eines App-Projekts.

        Der empfohlene Aufruf erfolgt nach dem ersten Commit des App-Projekts.
        So bleibt das Projekt bei temporärem Chunk-Ausfall sichtbar und kann
        später über dieselbe idempotente Operation repariert werden.
        """

        started_at = _iso(_utcnow())
        project_public_id = _require_project_public_id(project)
        resolved_request_id = request_id or _request_id()
        resolved_owner_user_id: Optional[str] = None
        policy = self._policy(
            requested_world_template=requested_world_template,
            fallback_world_template=fallback_world_template,
            allow_world_fallback=allow_world_fallback,
            allow_client_side_fallback=allow_client_side_fallback,
            allow_existing_template_change=allow_existing_template_change,
            persist_failure_state=persist_failure_state,
        )

        with _project_lock(project_public_id):
            try:
                resolved_owner_user_id = self._owner_auth_user_id(
                    project,
                    explicit=owner_auth_user_id,
                )
                policy = self._preserve_existing_template(project, policy)

                if not force:
                    existing = self._existing_result(
                        project,
                        owner_user_id=resolved_owner_user_id,
                        policy=policy,
                        request_id=resolved_request_id,
                        started_at=started_at,
                    )
                    if existing is not None:
                        return existing

                earth = self._earth_reference(
                    project,
                    policy=policy,
                    explicit=earth_reference,
                )
                cache_key = _cache_key(
                    project_public_id,
                    resolved_owner_user_id,
                    policy,
                    earth.fingerprint,
                )

                if not force:
                    cached = _cache_get(cache_key, project)
                    if cached is not None:
                        cached.cached = True
                        cached.request_id = resolved_request_id
                        cached.started_at = started_at
                        cached.finished_at = _iso(_utcnow())
                        return cached

                attempt = _attempt_count(project) + 1
                idempotency_key = _idempotency_key(project_public_id)
                self._mark_started(
                    project,
                    policy=policy,
                    owner_user_id=resolved_owner_user_id,
                    earth=earth,
                    attempt=attempt,
                    request_id=resolved_request_id,
                    idempotency_key=idempotency_key,
                    started_at=started_at,
                )
                self._flush()
                self._audit(
                    project,
                    "chunk_provisioning_started",
                    resolved_owner_user_id,
                    {
                        "attempt": attempt,
                        "requestId": resolved_request_id,
                        "policy": policy.to_dict(),
                        "earthReference": earth.to_dict(),
                    },
                )

                response, local_fallback_reason = self._call_chunk_with_policy(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=resolved_owner_user_id,
                    policy=policy,
                    earth=earth,
                    idempotency_key=idempotency_key,
                    request_id=resolved_request_id,
                )
                normalized = _normalize_chunk_response(
                    response,
                    requested_template=policy.requested_template,
                    fallback_template=policy.fallback_template,
                    local_fallback_reason=local_fallback_reason,
                )
                result = self._success_result(
                    project_public_id=project_public_id,
                    owner_user_id=resolved_owner_user_id,
                    policy=policy,
                    earth=earth,
                    response=normalized,
                    request_id=resolved_request_id,
                    idempotency_key=idempotency_key,
                    started_at=started_at,
                )
                self._mark_success(project, result=result, attempt=attempt)
                self._audit(
                    project,
                    (
                        "chunk_provisioning_fallback"
                        if result.fallback_used
                        else f"chunk_provisioned_{result.effective_world_template or 'unknown'}"
                    ),
                    resolved_owner_user_id,
                    result.to_dict(),
                )
                self._finish(commit=commit)
                _cache_set(cache_key, result, policy.cache_ttl_seconds)
                return result

            except ProjectChunkProvisioningError as exc:
                result = self._failure_result(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=resolved_owner_user_id,
                    policy=policy,
                    error=exc,
                    request_id=resolved_request_id,
                    started_at=started_at,
                    commit=commit,
                )
                if raise_on_error:
                    raise
                return result
            except Exception as exc:
                wrapped = ProjectChunkProvisioningError(
                    "unexpected_chunk_provisioning_error",
                    "An unexpected error occurred while provisioning the chunk project.",
                    status_code=500,
                    retryable=False,
                    details={
                        "exceptionType": exc.__class__.__name__,
                        "message": _text(exc, "", 500),
                    },
                    cause=exc,
                )
                result = self._failure_result(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=resolved_owner_user_id,
                    policy=policy,
                    error=wrapped,
                    request_id=resolved_request_id,
                    started_at=started_at,
                    commit=commit,
                )
                if raise_on_error:
                    raise wrapped from exc
                return result

    def _policy(
        self,
        *,
        requested_world_template: Optional[str],
        fallback_world_template: Optional[str],
        allow_world_fallback: Optional[bool],
        allow_client_side_fallback: Optional[bool],
        allow_existing_template_change: Optional[bool],
        persist_failure_state: Optional[bool],
    ) -> ProvisioningPolicy:
        requested = _world_template(
            requested_world_template
            or _config("VECTOPLAN_APP_DEFAULT_WORLD_TEMPLATE", DEFAULT_WORLD_TEMPLATE)
        )
        fallback = _world_template(
            fallback_world_template
            or _config("VECTOPLAN_APP_FALLBACK_WORLD_TEMPLATE", DEFAULT_FALLBACK_TEMPLATE)
        )
        fallback_allowed = _boolean(
            allow_world_fallback,
            _config("VECTOPLAN_APP_ALLOW_WORLD_FALLBACK", True),
            True,
        )
        if requested == fallback:
            fallback_allowed = False

        return ProvisioningPolicy(
            requested_template=requested,
            fallback_template=fallback,
            allow_fallback=fallback_allowed,
            allow_client_fallback=_boolean(
                allow_client_side_fallback,
                _config("VECTOPLAN_APP_CLIENT_SIDE_WORLD_FALLBACK", True),
                True,
            ),
            allow_existing_template_change=_boolean(
                allow_existing_template_change,
                _config("VECTOPLAN_APP_ALLOW_WORLD_TEMPLATE_CHANGE", False),
                False,
            ),
            default_earth_height=_number(
                _config("VECTOPLAN_APP_DEFAULT_EARTH_HEIGHT", DEFAULT_EARTH_HEIGHT),
                DEFAULT_EARTH_HEIGHT,
            ),
            earth_crs_id=_text(
                _config("VECTOPLAN_APP_EARTH_CRS_ID", DEFAULT_EARTH_CRS_ID),
                DEFAULT_EARTH_CRS_ID,
                128,
            ),
            cache_ttl_seconds=max(
                0.0,
                _number(
                    _config(
                        "VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_SECONDS",
                        DEFAULT_CACHE_TTL_SECONDS,
                    ),
                    DEFAULT_CACHE_TTL_SECONDS,
                ),
            ),
            persist_failure_state=_boolean(
                persist_failure_state,
                _config("VECTOPLAN_APP_PERSIST_CHUNK_FAILURE_STATE", True),
                True,
            ),
        )

    def _preserve_existing_template(
        self,
        project: Any,
        policy: ProvisioningPolicy,
    ) -> ProvisioningPolicy:
        if policy.allow_existing_template_change:
            return policy

        existing = _state_get(
            project,
            "effectiveWorldTemplate",
            attrs=("chunk_world_template_effective",),
        ) or _state_get(
            project,
            "requestedWorldTemplate",
            attrs=("chunk_world_template_requested",),
        )
        if not existing:
            return policy

        existing_template = _world_template(existing)
        return ProvisioningPolicy(
            requested_template=existing_template,
            fallback_template=policy.fallback_template,
            allow_fallback=(
                policy.allow_fallback and existing_template != policy.fallback_template
            ),
            allow_client_fallback=policy.allow_client_fallback,
            allow_existing_template_change=False,
            default_earth_height=policy.default_earth_height,
            earth_crs_id=policy.earth_crs_id,
            cache_ttl_seconds=policy.cache_ttl_seconds,
            persist_failure_state=policy.persist_failure_state,
        )

    def _owner_auth_user_id(self, project: Any, *, explicit: Optional[str]) -> str:
        candidates: list[Any] = [explicit]
        candidates.extend(
            [
                _attr(project, "owner_auth_user_id"),
                _attr(project, "auth_owner_user_id"),
            ]
        )

        for relation_name in ("owner_user", "owner"):
            relation = _attr(project, relation_name)
            if relation is not None:
                candidates.extend(
                    [
                        _attr(relation, "auth_user_id"),
                        _attr(relation, "external_user_id"),
                    ]
                )

        local_owner_id = _attr(project, "owner_user_id")
        if local_owner_id not in (None, ""):
            candidates.append(self._app_user_auth_id(local_owner_id))

        candidates.append(_current_owner_auth_user_id(project))

        for candidate in candidates:
            normalized = _external_user_id(candidate)
            if normalized:
                return normalized

        raise ProjectChunkProvisioningError(
            "owner_auth_user_id_missing",
            "No canonical vectoplan-auth user ID is available for the project owner.",
            status_code=422,
            details={
                "projectPublicId": _project_public_id(project),
                "localOwnerUserIdPresent": local_owner_id not in (None, ""),
            },
        )

    def _app_user_auth_id(self, local_owner_id: Any) -> Optional[str]:
        if self.session is None:
            return None
        try:
            model = getattr(import_module("models"), "AppUser", None)
            if model is None:
                return None
            instance = None
            if hasattr(self.session, "get"):
                instance = self.session.get(model, local_owner_id)
            if instance is None and hasattr(model, "query"):
                instance = model.query.filter_by(id=local_owner_id).first()
            return _text(_attr(instance, "auth_user_id"), "", 240) or None
        except Exception as exc:
            LOGGER.debug("AppUser auth link lookup failed: %s", exc)
            return None

    def _earth_reference(
        self,
        project: Any,
        *,
        policy: ProvisioningPolicy,
        explicit: Optional[Mapping[str, Any]],
    ) -> EarthReferenceResolution:
        if policy.requested_template != "earth":
            return EarthReferenceResolution(False, source="not_required")

        if explicit is not None:
            return _normalize_earth_reference(
                explicit,
                source="explicit",
                default_height=policy.default_earth_height,
                default_crs=policy.earth_crs_id,
            )

        builder = self.georeference_builder or _georeference_builder()
        builder_warning: Optional[str] = None
        if builder is not None:
            try:
                raw = _call_adapted(
                    builder,
                    {
                        "project": project,
                        "default_height": policy.default_earth_height,
                        "crs_id": policy.earth_crs_id,
                        "required": False,
                    },
                )
                resolved = _normalize_earth_reference(
                    raw,
                    source="project_georeference_service",
                    default_height=policy.default_earth_height,
                    default_crs=policy.earth_crs_id,
                )
                if resolved.available:
                    return resolved
                builder_warning = resolved.error_code or "georeference_unavailable"
            except Exception as exc:
                LOGGER.warning("Project georeference builder failed: %s", exc)
                builder_warning = f"georeference_builder_failed:{exc.__class__.__name__}"
        else:
            builder_warning = "georeference_builder_not_available"

        longitude = _first_number(
            _attr(project, "longitude"),
            _attr(project, "lng"),
            _attr(project, "lon"),
            _project_json(project, "longitude"),
            _project_json(project, "lng"),
        )
        latitude = _first_number(
            _attr(project, "latitude"),
            _attr(project, "lat"),
            _project_json(project, "latitude"),
            _project_json(project, "lat"),
        )
        height = _first_number(
            _attr(project, "height"),
            _attr(project, "elevation"),
            _attr(project, "altitude"),
            _project_json(project, "height"),
            _project_json(project, "elevation"),
            _project_json(project, "altitude"),
            policy.default_earth_height,
        )

        if longitude is None or latitude is None:
            return EarthReferenceResolution(
                False,
                source="project_coordinates",
                error_code="coordinates_unavailable",
                errors=("longitude_or_latitude_missing",),
                warnings=(builder_warning,) if builder_warning else (),
            )

        return _normalize_earth_reference(
            {
                "longitude": longitude,
                "latitude": latitude,
                "height": height,
                "crsId": policy.earth_crs_id,
                "alwaysXY": True,
            },
            source="project_coordinates",
            default_height=policy.default_earth_height,
            default_crs=policy.earth_crs_id,
            warnings=(builder_warning,) if builder_warning else (),
        )

    def _call_chunk_with_policy(
        self,
        *,
        project: Any,
        project_public_id: str,
        owner_user_id: str,
        policy: ProvisioningPolicy,
        earth: EarthReferenceResolution,
        idempotency_key: str,
        request_id: Optional[str],
    ) -> tuple[Mapping[str, Any], Optional[str]]:
        if policy.requested_template == "earth" and not earth.available:
            if not policy.allow_fallback:
                raise ProjectChunkProvisioningError(
                    earth.error_code or "earth_reference_missing",
                    "Earth provisioning requires a valid georeference.",
                    status_code=422,
                    details=earth.to_dict(),
                )
            return (
                self._invoke_chunk(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=owner_user_id,
                    requested_template=policy.fallback_template,
                    fallback_template=policy.fallback_template,
                    allow_fallback=False,
                    earth_reference=None,
                    idempotency_key=f"{idempotency_key}:flat-fallback",
                    request_id=request_id,
                ),
                earth.error_code or "coordinates_unavailable",
            )

        try:
            return (
                self._invoke_chunk(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=owner_user_id,
                    requested_template=policy.requested_template,
                    fallback_template=policy.fallback_template,
                    allow_fallback=policy.allow_fallback,
                    earth_reference=earth.reference if earth.available else None,
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                ),
                None,
            )
        except ProjectChunkProvisioningError as exc:
            if not _client_fallback_allowed(exc, policy):
                raise
            LOGGER.warning(
                "Earth provisioning returned %s; retrying flat for %s",
                exc.code,
                project_public_id,
            )
            return (
                self._invoke_chunk(
                    project=project,
                    project_public_id=project_public_id,
                    owner_user_id=owner_user_id,
                    requested_template=policy.fallback_template,
                    fallback_template=policy.fallback_template,
                    allow_fallback=False,
                    earth_reference=None,
                    idempotency_key=f"{idempotency_key}:flat-fallback",
                    request_id=request_id,
                ),
                exc.code,
            )

    def _invoke_chunk(
        self,
        *,
        project: Any,
        project_public_id: str,
        owner_user_id: str,
        requested_template: str,
        fallback_template: str,
        allow_fallback: bool,
        earth_reference: Optional[Mapping[str, Any]],
        idempotency_key: str,
        request_id: Optional[str],
    ) -> Mapping[str, Any]:
        client = self.chunk_client or _chunk_client_module()
        callable_client = _chunk_callable(client)
        payload = {
            "source": "vectoplan-app",
            "contractVersion": POLICY_VERSION,
            "appProjectPublicId": project_public_id,
            "ownerUserId": owner_user_id,
            "worldTemplate": requested_template,
            "requestedWorldTemplate": requested_template,
            "fallbackWorldTemplate": fallback_template,
            "allowWorldFallback": bool(allow_fallback),
            "earthReference": _json_safe(earth_reference),
            "idempotencyKey": idempotency_key,
            "requestId": request_id,
        }
        kwargs = {
            "app_project_public_id": project_public_id,
            "owner_user_id": owner_user_id,
            "requested_world_template": requested_template,
            "fallback_world_template": fallback_template,
            "allow_world_fallback": bool(allow_fallback),
            "earth_reference": earth_reference,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
            # Übergangskompatibilität für den bestehenden Client.
            "project": project,
            "project_public_id": project_public_id,
            "app_project_id": project_public_id,
            "world_template": requested_template,
            "payload": payload,
            "data": payload,
            "body": payload,
        }
        preferred = (
            "app_project_public_id",
            "owner_user_id",
            "requested_world_template",
            "fallback_world_template",
            "allow_world_fallback",
            "earth_reference",
            "idempotency_key",
            "request_id",
        )
        try:
            raw = _call_adapted(callable_client, kwargs, preferred_keys=preferred)
            return _mapping_result(raw)
        except ProjectChunkProvisioningError:
            raise
        except Exception as exc:
            raise _chunk_exception(exc) from exc

    def _success_result(
        self,
        *,
        project_public_id: str,
        owner_user_id: str,
        policy: ProvisioningPolicy,
        earth: EarthReferenceResolution,
        response: Mapping[str, Any],
        request_id: Optional[str],
        idempotency_key: str,
        started_at: str,
    ) -> ProjectChunkProvisioningResult:
        effective = _world_template(response.get("effectiveWorldTemplate"))
        fallback_used = bool(response.get("fallbackUsed")) or (
            effective != policy.requested_template
        )
        return ProjectChunkProvisioningResult(
            ok=True,
            code=_code(response.get("code"), "chunk_project_provisioned"),
            status=(
                ProvisioningStatus.FALLBACK_READY.value
                if fallback_used
                else ProvisioningStatus.READY.value
            ),
            project_public_id=project_public_id,
            owner_user_id=owner_user_id,
            requested_world_template=policy.requested_template,
            effective_world_template=effective,
            fallback_world_template=policy.fallback_template,
            fallback_used=fallback_used,
            fallback_reason=_code(response.get("fallbackReason"), "") or None,
            chunk_project_id=_text(response.get("chunkProjectId"), "", 240) or None,
            chunk_universe_id=_text(response.get("chunkUniverseId"), "", 240) or None,
            chunk_world_id=_text(response.get("chunkWorldId"), "world_spawn", 240),
            earth_reference_fingerprint=(
                _text(
                    response.get("earthReferenceFingerprint") or earth.fingerprint,
                    "",
                    128,
                )
                or None
            ),
            created=bool(response.get("created")),
            updated=bool(response.get("updated")),
            reused=bool(response.get("reused")),
            status_code=_integer(response.get("statusCode"), 200, 200, 299),
            request_id=request_id,
            idempotency_key=idempotency_key,
            warnings=_dedupe([*earth.warnings, *_strings(response.get("warnings"))]),
            response_summary=_sanitize(response.get("responseSummary") or {}, depth=4),
            started_at=started_at,
            finished_at=_iso(_utcnow()),
        )

    def _existing_result(
        self,
        project: Any,
        *,
        owner_user_id: str,
        policy: ProvisioningPolicy,
        request_id: Optional[str],
        started_at: str,
    ) -> Optional[ProjectChunkProvisioningResult]:
        if not _complete_refs(project):
            return None
        status = _project_status(project)
        if status not in {
            ProvisioningStatus.READY.value,
            ProvisioningStatus.FALLBACK_READY.value,
        }:
            return None
        requested = _world_template(
            _state_get(
                project,
                "requestedWorldTemplate",
                attrs=("chunk_world_template_requested",),
                default=policy.requested_template,
            )
        )
        effective = _world_template(
            _state_get(
                project,
                "effectiveWorldTemplate",
                attrs=("chunk_world_template_effective",),
                default=requested,
            )
        )
        return ProjectChunkProvisioningResult(
            ok=True,
            code="chunk_project_already_provisioned",
            status=status,
            project_public_id=_require_project_public_id(project),
            owner_user_id=owner_user_id,
            requested_world_template=requested,
            effective_world_template=effective,
            fallback_world_template=policy.fallback_template,
            fallback_used=status == ProvisioningStatus.FALLBACK_READY.value,
            fallback_reason=(
                _code(
                    _state_get(
                        project,
                        "fallbackReason",
                        attrs=("chunk_world_fallback_reason",),
                    ),
                    "",
                )
                or None
            ),
            chunk_project_id=_text(_attr(project, "chunk_project_id"), "", 240),
            chunk_universe_id=_text(_attr(project, "chunk_universe_id"), "", 240),
            chunk_world_id=_text(_attr(project, "chunk_world_id"), "world_spawn", 240),
            earth_reference_fingerprint=_state_get(
                project,
                "earthReferenceFingerprint",
                attrs=("earth_reference_fingerprint",),
            ),
            reused=True,
            request_id=request_id,
            response_summary={"source": "app_project_state"},
            started_at=started_at,
            finished_at=_iso(_utcnow()),
        )

    def _mark_started(
        self,
        project: Any,
        *,
        policy: ProvisioningPolicy,
        owner_user_id: str,
        earth: EarthReferenceResolution,
        attempt: int,
        request_id: Optional[str],
        idempotency_key: str,
        started_at: str,
    ) -> None:
        _state_set(
            project,
            "status",
            ProvisioningStatus.PROVISIONING.value,
            attrs=("chunk_provisioning_status",),
        )
        _state_set(
            project,
            "requestedWorldTemplate",
            policy.requested_template,
            attrs=("chunk_world_template_requested",),
        )
        _state_set(
            project,
            "fallbackWorldTemplate",
            policy.fallback_template,
            attrs=("chunk_world_template_fallback",),
        )
        _state_set(
            project,
            "earthReferenceFingerprint",
            earth.fingerprint,
            attrs=("earth_reference_fingerprint",),
        )
        _state_set(
            project,
            "lastErrorCode",
            None,
            attrs=("chunk_provisioning_error_code",),
        )
        _state_set(
            project,
            "lastErrorMessage",
            None,
            attrs=("chunk_provisioning_error_message",),
        )
        _metadata_update(
            project,
            {
                "serviceVersion": SERVICE_VERSION,
                "policyVersion": POLICY_VERSION,
                "attemptCount": attempt,
                "ownerAuthUserId": owner_user_id,
                "lastStartedAt": started_at,
                "lastFinishedAt": None,
                "lastRequestId": request_id,
                "lastIdempotencyKey": idempotency_key,
                "policy": policy.to_dict(),
                "earthReference": earth.to_dict(),
            },
        )

    def _mark_success(
        self,
        project: Any,
        *,
        result: ProjectChunkProvisioningResult,
        attempt: int,
    ) -> None:
        setattr(project, "chunk_project_id", result.chunk_project_id)
        setattr(project, "chunk_universe_id", result.chunk_universe_id)
        setattr(project, "chunk_world_id", result.chunk_world_id or "world_spawn")
        _state_set(
            project,
            "status",
            result.status,
            attrs=("chunk_provisioning_status",),
        )
        _state_set(
            project,
            "requestedWorldTemplate",
            result.requested_world_template,
            attrs=("chunk_world_template_requested",),
        )
        _state_set(
            project,
            "effectiveWorldTemplate",
            result.effective_world_template,
            attrs=("chunk_world_template_effective",),
        )
        _state_set(
            project,
            "fallbackReason",
            result.fallback_reason,
            attrs=("chunk_world_fallback_reason",),
        )
        _state_set(
            project,
            "earthReferenceFingerprint",
            result.earth_reference_fingerprint,
            attrs=("earth_reference_fingerprint",),
        )
        _state_set(
            project,
            "lastErrorCode",
            None,
            attrs=("chunk_provisioning_error_code",),
        )
        _state_set(
            project,
            "lastErrorMessage",
            None,
            attrs=("chunk_provisioning_error_message",),
        )
        if hasattr(project, "chunk_provisioned_at"):
            setattr(project, "chunk_provisioned_at", _utcnow())
        _metadata_update(
            project,
            {
                "attemptCount": attempt,
                "lastFinishedAt": result.finished_at,
                "lastResultCode": result.code,
                "lastErrorCode": None,
                "lastErrorMessage": None,
                "lastResponseSummary": result.response_summary,
                "fallbackUsed": result.fallback_used,
                "fallbackReason": result.fallback_reason,
                "chunkProjectId": result.chunk_project_id,
                "chunkUniverseId": result.chunk_universe_id,
                "chunkWorldId": result.chunk_world_id,
            },
        )

    def _failure_result(
        self,
        *,
        project: Any,
        project_public_id: str,
        owner_user_id: Optional[str],
        policy: ProvisioningPolicy,
        error: ProjectChunkProvisioningError,
        request_id: Optional[str],
        started_at: str,
        commit: bool,
    ) -> ProjectChunkProvisioningResult:
        finished_at = _iso(_utcnow())
        LOGGER.warning(
            "Chunk provisioning failed project=%s code=%s retryable=%s",
            project_public_id,
            error.code,
            error.retryable,
        )
        self._rollback()

        if policy.persist_failure_state:
            try:
                _state_set(
                    project,
                    "status",
                    ProvisioningStatus.FAILED.value,
                    attrs=("chunk_provisioning_status",),
                )
                _state_set(
                    project,
                    "requestedWorldTemplate",
                    policy.requested_template,
                    attrs=("chunk_world_template_requested",),
                )
                _state_set(
                    project,
                    "lastErrorCode",
                    error.code,
                    attrs=("chunk_provisioning_error_code",),
                )
                _state_set(
                    project,
                    "lastErrorMessage",
                    error.message,
                    attrs=("chunk_provisioning_error_message",),
                )
                _metadata_update(
                    project,
                    {
                        "lastFinishedAt": finished_at,
                        "lastRequestId": request_id,
                        "lastErrorCode": error.code,
                        "lastErrorMessage": error.message,
                        "lastErrorStatusCode": error.status_code,
                        "lastErrorRetryable": error.retryable,
                        "lastErrorDetails": error.details,
                    },
                )
                self._audit(
                    project,
                    "chunk_provisioning_failed",
                    owner_user_id,
                    error.to_dict(),
                )
                self._finish(commit=commit)
            except Exception:
                self._rollback()
                LOGGER.exception(
                    "Unable to persist provisioning failure state for %s",
                    project_public_id,
                )

        return ProjectChunkProvisioningResult(
            ok=False,
            code=error.code,
            status=ProvisioningStatus.FAILED.value,
            project_public_id=project_public_id,
            owner_user_id=owner_user_id,
            requested_world_template=policy.requested_template,
            fallback_world_template=policy.fallback_template,
            retryable=error.retryable,
            status_code=error.status_code,
            request_id=request_id,
            error=error.to_dict(),
            started_at=started_at,
            finished_at=finished_at,
        )

    def _audit(
        self,
        project: Any,
        action: str,
        actor_auth_user_id: Optional[str],
        payload: Mapping[str, Any],
    ) -> None:
        if self.audit_writer is None:
            return
        try:
            _call_adapted(
                self.audit_writer,
                {
                    "project": project,
                    "project_id": _attr(project, "id"),
                    "project_public_id": _project_public_id(project),
                    "action": action,
                    "actor_auth_user_id": actor_auth_user_id,
                    "actor_user_id": actor_auth_user_id,
                    "payload": _sanitize(payload, depth=5),
                    "metadata": _sanitize(payload, depth=5),
                    "session": self.session,
                    "commit": False,
                },
            )
        except Exception:
            LOGGER.exception("Project audit writer failed for action %s", action)

    def _flush(self) -> None:
        if self.session is None:
            return
        try:
            self.session.flush()
        except Exception as exc:
            self._rollback()
            raise ProjectChunkProvisioningError(
                "app_project_state_flush_failed",
                "The app project provisioning state could not be flushed.",
                status_code=500,
                retryable=True,
                details={"exceptionType": exc.__class__.__name__},
                cause=exc,
            ) from exc

    def _finish(self, *, commit: bool) -> None:
        if self.session is None:
            return
        try:
            if commit:
                self.session.commit()
            else:
                self.session.flush()
        except Exception as exc:
            self._rollback()
            raise ProjectChunkProvisioningError(
                "app_project_state_persist_failed",
                "Chunk provisioning completed, but the app project state could not be persisted.",
                status_code=500,
                retryable=True,
                details={"exceptionType": exc.__class__.__name__},
                cause=exc,
            ) from exc

    def _rollback(self) -> None:
        if self.session is None:
            return
        try:
            self.session.rollback()
        except Exception:
            LOGGER.exception("SQLAlchemy rollback failed during chunk provisioning.")


# ---------------------------------------------------------------------------
# Öffentliche Funktionsfassade
# ---------------------------------------------------------------------------


def provision_project_chunk_graph(project: Any, **kwargs: Any) -> ProjectChunkProvisioningResult:
    service_kwargs = {
        key: kwargs.pop(key)
        for key in (
            "session",
            "chunk_client",
            "georeference_builder",
            "audit_writer",
        )
        if key in kwargs
    }
    return ProjectChunkProvisioningService(**service_kwargs).provision(project, **kwargs)


def provision_project_chunk_graph_or_raise(
    project: Any,
    **kwargs: Any,
) -> ProjectChunkProvisioningResult:
    kwargs["raise_on_error"] = True
    return provision_project_chunk_graph(project, **kwargs)


def retry_project_chunk_provisioning(
    project: Any,
    **kwargs: Any,
) -> ProjectChunkProvisioningResult:
    kwargs["force"] = True
    return provision_project_chunk_graph(project, **kwargs)


def serialize_project_chunk_provisioning_status(project: Any) -> dict[str, Any]:
    metadata = _metadata(project)
    return {
        "resultVersion": RESULT_VERSION,
        "serviceVersion": SERVICE_VERSION,
        "projectPublicId": _project_public_id(project),
        "status": _project_status(project),
        "requestedWorldTemplate": _state_get(
            project,
            "requestedWorldTemplate",
            attrs=("chunk_world_template_requested",),
        ),
        "effectiveWorldTemplate": _state_get(
            project,
            "effectiveWorldTemplate",
            attrs=("chunk_world_template_effective",),
        ),
        "fallbackReason": _state_get(
            project,
            "fallbackReason",
            attrs=("chunk_world_fallback_reason",),
        ),
        "chunkProjectId": _attr(project, "chunk_project_id"),
        "chunkUniverseId": _attr(project, "chunk_universe_id"),
        "chunkWorldId": _attr(project, "chunk_world_id"),
        "earthReferenceFingerprint": _state_get(
            project,
            "earthReferenceFingerprint",
            attrs=("earth_reference_fingerprint",),
        ),
        "attemptCount": _integer(metadata.get("attemptCount"), 0, 0),
        "lastErrorCode": _state_get(
            project,
            "lastErrorCode",
            attrs=("chunk_provisioning_error_code",),
        ),
        "lastErrorMessage": _state_get(
            project,
            "lastErrorMessage",
            attrs=("chunk_provisioning_error_message",),
        ),
        "lastStartedAt": metadata.get("lastStartedAt"),
        "lastFinishedAt": metadata.get("lastFinishedAt"),
        "lastRequestId": metadata.get("lastRequestId"),
        "lastResponseSummary": _sanitize(
            metadata.get("lastResponseSummary") or {}, depth=4
        ),
        "ready": _complete_refs(project),
    }


def clear_project_chunk_provisioning_cache(project_public_id: Optional[str] = None) -> int:
    with _CACHE_LOCK:
        if project_public_id is None:
            count = len(_CACHE)
            _CACHE.clear()
            return count
        prefix = f"{_text(project_public_id, '', 240)}|"
        keys = [key for key in _CACHE if key.startswith(prefix)]
        for key in keys:
            _CACHE.pop(key, None)
        return len(keys)


ensure_project_chunk_provisioned = provision_project_chunk_graph
provision_chunk_project_for_project = provision_project_chunk_graph
get_project_chunk_provisioning_status = serialize_project_chunk_provisioning_status


# ---------------------------------------------------------------------------
# Referenz- und Clientnormalisierung
# ---------------------------------------------------------------------------


def _normalize_earth_reference(
    raw: Any,
    *,
    source: str,
    default_height: float,
    default_crs: str,
    warnings: Sequence[str] = (),
) -> EarthReferenceResolution:
    if raw is None:
        return EarthReferenceResolution(
            False,
            source=source,
            error_code="earth_reference_not_available",
            errors=("empty_reference",),
            warnings=tuple(warnings),
        )
    if is_dataclass(raw):
        raw = asdict(raw)
    elif hasattr(raw, "to_dict") and callable(raw.to_dict):
        raw = raw.to_dict()
    if not isinstance(raw, Mapping):
        raise ProjectChunkProvisioningError(
            "earth_reference_invalid",
            "Earth reference must be an object.",
            status_code=422,
            details={"type": type(raw).__name__},
        )

    nested = raw.get("reference")
    payload = nested if isinstance(nested, Mapping) else raw
    if raw.get("available") is False:
        return EarthReferenceResolution(
            False,
            source=_text(raw.get("source"), source, 120),
            fingerprint=_text(raw.get("fingerprint"), "", 128) or None,
            error_code=_code(
                raw.get("errorCode") or raw.get("error_code"),
                "earth_reference_not_available",
            ),
            errors=_strings(raw.get("errors")) or ("reference_unavailable",),
            warnings=(*_strings(raw.get("warnings")), *warnings),
        )

    longitude = _first_number(
        payload.get("longitude"),
        payload.get("lon"),
        payload.get("x"),
        _deep(payload, "coordinate", "x"),
    )
    latitude = _first_number(
        payload.get("latitude"),
        payload.get("lat"),
        payload.get("y"),
        _deep(payload, "coordinate", "y"),
    )
    height = _first_number(
        payload.get("height"),
        payload.get("altitude"),
        payload.get("elevation"),
        payload.get("z"),
        _deep(payload, "coordinate", "z"),
        default_height,
    )
    crs_id = _text(
        payload.get("crsId")
        or payload.get("crs_id")
        or _deep(payload, "crs", "id")
        or default_crs,
        default_crs,
        128,
    )

    errors: list[str] = []
    if longitude is None:
        errors.append("longitude_missing")
    elif not -180.0 <= longitude <= 180.0:
        errors.append("longitude_out_of_range")
    if latitude is None:
        errors.append("latitude_missing")
    elif not -90.0 <= latitude <= 90.0:
        errors.append("latitude_out_of_range")
    if height is None or not math.isfinite(height):
        errors.append("height_invalid")
    if not crs_id:
        errors.append("crs_missing")

    if errors:
        return EarthReferenceResolution(
            False,
            source=source,
            error_code="earth_reference_invalid",
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    reference = {
        "longitude": longitude,
        "latitude": latitude,
        "height": height,
        "crsId": crs_id,
        "alwaysXY": bool(payload.get("alwaysXY", True)),
    }
    for key in ("name", "label", "coordinateEpoch", "verticalDatum", "metadata"):
        if payload.get(key) is not None:
            reference[key] = _json_safe(payload.get(key))

    fingerprint = _text(
        raw.get("fingerprint")
        or payload.get("fingerprint")
        or _fingerprint(reference),
        "",
        128,
    )
    return EarthReferenceResolution(
        True,
        reference=reference,
        source=_text(raw.get("source"), source, 120),
        fingerprint=fingerprint or None,
        warnings=(*_strings(raw.get("warnings")), *warnings),
    )


def _normalize_chunk_response(
    response: Mapping[str, Any],
    *,
    requested_template: str,
    fallback_template: str,
    local_fallback_reason: Optional[str],
) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), Mapping) else {}
    result = response.get("result") if isinstance(response.get("result"), Mapping) else {}
    project = _first_mapping(response.get("project"), data.get("project"), result.get("project"))
    universe = _first_mapping(
        response.get("universe"), data.get("universe"), result.get("universe")
    )
    world = _first_mapping(
        response.get("spawnWorld"),
        response.get("world"),
        data.get("spawnWorld"),
        data.get("world"),
        result.get("spawnWorld"),
        result.get("world"),
    )

    status_code = _integer(
        _first(response.get("statusCode"), data.get("statusCode"), 200),
        200,
        100,
        599,
    )
    ok = _first(response.get("ok"), data.get("ok"), result.get("ok"))
    if ok is False or status_code >= 400:
        error = _first_mapping(response.get("error"), data.get("error"), result.get("error"))
        raise ProjectChunkProvisioningError(
            _code(
                _first(response.get("code"), data.get("code"), error.get("code")),
                "chunk_provisioning_rejected",
            ),
            _text(
                _first(
                    response.get("message"),
                    data.get("message"),
                    error.get("message"),
                ),
                "Chunk service rejected project provisioning.",
                1000,
            ),
            status_code=status_code if status_code >= 400 else 502,
            retryable=bool(
                _first(response.get("retryable"), data.get("retryable"), status_code >= 500)
            ),
            details={"responseSummary": _response_summary(response)},
        )

    chunk_project_id = _text(
        _first(
            response.get("chunkProjectId"),
            response.get("projectId"),
            data.get("chunkProjectId"),
            data.get("projectId"),
            result.get("chunkProjectId"),
            project.get("projectId"),
            project.get("publicId"),
            project.get("public_id"),
        ),
        "",
        240,
    )
    chunk_universe_id = _text(
        _first(
            response.get("chunkUniverseId"),
            response.get("universeId"),
            data.get("chunkUniverseId"),
            data.get("universeId"),
            result.get("chunkUniverseId"),
            universe.get("universeId"),
            universe.get("publicId"),
            universe.get("public_id"),
        ),
        "",
        240,
    )
    chunk_world_id = _text(
        _first(
            response.get("chunkWorldId"),
            response.get("spawnWorldId"),
            response.get("defaultWorldId"),
            data.get("chunkWorldId"),
            data.get("spawnWorldId"),
            result.get("chunkWorldId"),
            world.get("worldId"),
            world.get("publicId"),
            world.get("public_id"),
            "world_spawn",
        ),
        "world_spawn",
        240,
    )
    if not chunk_project_id or not chunk_universe_id or not chunk_world_id:
        raise ProjectChunkProvisioningError(
            "chunk_provisioning_response_incomplete",
            "Chunk provisioning response is missing required resource IDs.",
            status_code=502,
            retryable=True,
            details={"responseSummary": _response_summary(response)},
        )

    effective = _world_template(
        _first(
            response.get("effectiveWorldTemplate"),
            response.get("worldTemplate"),
            data.get("effectiveWorldTemplate"),
            data.get("worldTemplate"),
            result.get("effectiveWorldTemplate"),
            world.get("templateId"),
            world.get("providerId"),
            fallback_template if local_fallback_reason else requested_template,
        )
    )
    fallback_reason = _code(
        _first(
            local_fallback_reason,
            response.get("fallbackReason"),
            data.get("fallbackReason"),
            result.get("fallbackReason"),
        ),
        "",
    ) or None
    fallback_used = bool(
        local_fallback_reason
        or _first(
            response.get("fallbackUsed"),
            data.get("fallbackUsed"),
            result.get("fallbackUsed"),
            effective != requested_template,
        )
    )

    return {
        "code": _code(
            _first(response.get("code"), data.get("code"), result.get("code")),
            "chunk_project_provisioned",
        ),
        "statusCode": status_code,
        "chunkProjectId": chunk_project_id,
        "chunkUniverseId": chunk_universe_id,
        "chunkWorldId": chunk_world_id,
        "effectiveWorldTemplate": effective,
        "fallbackUsed": fallback_used,
        "fallbackReason": fallback_reason,
        "earthReferenceFingerprint": _text(
            _first(
                response.get("earthReferenceFingerprint"),
                data.get("earthReferenceFingerprint"),
                result.get("earthReferenceFingerprint"),
                world.get("globalReferenceFingerprint"),
            ),
            "",
            128,
        )
        or None,
        "created": bool(_first(response.get("created"), data.get("created"), False)),
        "updated": bool(_first(response.get("updated"), data.get("updated"), False)),
        "reused": bool(_first(response.get("reused"), data.get("reused"), False)),
        "warnings": [
            *_strings(response.get("warnings")),
            *_strings(data.get("warnings")),
            *_strings(result.get("warnings")),
        ],
        "responseSummary": _response_summary(response),
    }


def _mapping_result(raw: Any) -> Mapping[str, Any]:
    if raw is None:
        raise ProjectChunkProvisioningError(
            "chunk_client_empty_response",
            "The chunk client returned no response.",
            status_code=502,
            retryable=True,
        )
    if is_dataclass(raw):
        raw = asdict(raw)
    elif hasattr(raw, "to_dict") and callable(raw.to_dict):
        raw = raw.to_dict()
    elif hasattr(raw, "json") and callable(raw.json):
        raw = raw.json()
    if not isinstance(raw, Mapping):
        raise ProjectChunkProvisioningError(
            "chunk_client_invalid_response",
            "The chunk client returned a non-object response.",
            status_code=502,
            retryable=True,
            details={"type": type(raw).__name__},
        )
    return raw


def _chunk_exception(exc: BaseException) -> ProjectChunkProvisioningError:
    code = _code(
        _first(_attr(exc, "code"), _attr(exc, "error_code")),
        "chunk_client_request_failed",
    )
    status_code = _integer(
        _first(
            _attr(exc, "status_code"),
            _attr(exc, "http_status"),
            _attr(_attr(exc, "response"), "status_code"),
            503,
        ),
        503,
        400,
        599,
    )
    retryable = bool(_attr(exc, "retryable", status_code >= 500))
    lowered = str(exc).lower()
    if "timeout" in lowered or "timed out" in lowered:
        code, status_code, retryable = "chunk_service_timeout", 503, True
    elif "connection refused" in lowered or "connection error" in lowered:
        code, status_code, retryable = "chunk_service_connection_failed", 503, True
    elif "name or service not known" in lowered or "gaierror" in lowered:
        code, status_code, retryable = "chunk_service_dns_failed", 503, True
    return ProjectChunkProvisioningError(
        code,
        _text(exc, "Chunk client request failed.", 1000),
        status_code=status_code,
        retryable=retryable,
        details={"exceptionType": exc.__class__.__name__},
        cause=exc,
    )


# ---------------------------------------------------------------------------
# Projektzustand und Metadaten
# ---------------------------------------------------------------------------


def _state_get(
    project: Any,
    key: str,
    *,
    attrs: Sequence[str] = (),
    default: Any = None,
) -> Any:
    for name in attrs:
        value = _attr(project, name)
        if value not in (None, ""):
            return value
    value = _metadata(project).get(key)
    return default if value in (None, "") else value


def _state_set(
    project: Any,
    key: str,
    value: Any,
    *,
    attrs: Sequence[str] = (),
) -> None:
    for name in attrs:
        if hasattr(project, name):
            try:
                setattr(project, name, value)
                break
            except Exception:
                LOGGER.debug("Unable to set Project.%s", name, exc_info=True)
    _metadata_update(project, {key: value})


def _metadata(project: Any) -> dict[str, Any]:
    container = _metadata_container(project)
    if container is None:
        return {}
    root = _attr(project, container)
    if not isinstance(root, Mapping):
        return {}
    value = root.get(METADATA_NAMESPACE)
    return dict(value) if isinstance(value, Mapping) else {}


def _metadata_update(project: Any, updates: Mapping[str, Any]) -> None:
    container = _metadata_container(project)
    if container is None:
        return
    root_value = _attr(project, container)
    root = dict(root_value) if isinstance(root_value, Mapping) else {}
    current = root.get(METADATA_NAMESPACE)
    namespace = dict(current) if isinstance(current, Mapping) else {}
    namespace.update({key: _json_safe(value) for key, value in updates.items()})
    root[METADATA_NAMESPACE] = namespace
    setattr(project, container, root)


def _metadata_container(project: Any) -> Optional[str]:
    for name in ("metadata_json", "settings", "service_refs"):
        if hasattr(project, name):
            return name
    return None


def _project_json(project: Any, key: str) -> Any:
    for name in ("metadata_json", "settings"):
        root = _attr(project, name)
        if not isinstance(root, Mapping):
            continue
        if key in root:
            return root.get(key)
        for nested_name in ("georeference", "geo", "location"):
            nested = root.get(nested_name)
            if isinstance(nested, Mapping) and key in nested:
                return nested.get(key)
    return None


def _project_status(project: Any) -> str:
    explicit = _state_get(
        project,
        "status",
        attrs=("chunk_provisioning_status",),
    )
    if explicit:
        return _text(explicit, ProvisioningStatus.PENDING.value, 64)
    if _complete_refs(project):
        requested = _state_get(
            project,
            "requestedWorldTemplate",
            attrs=("chunk_world_template_requested",),
        )
        effective = _state_get(
            project,
            "effectiveWorldTemplate",
            attrs=("chunk_world_template_effective",),
        )
        if requested and effective and requested != effective:
            return ProvisioningStatus.FALLBACK_READY.value
        return ProvisioningStatus.READY.value
    return ProvisioningStatus.PENDING.value


def _complete_refs(project: Any) -> bool:
    return all(
        _text(_attr(project, name), "", 240)
        for name in ("chunk_project_id", "chunk_universe_id", "chunk_world_id")
    )


def _attempt_count(project: Any) -> int:
    return _integer(_metadata(project).get("attemptCount"), 0, 0)


# ---------------------------------------------------------------------------
# Cache, Locks und Integrationsadapter
# ---------------------------------------------------------------------------


@contextmanager
def _project_lock(project_public_id: str) -> Iterator[None]:
    with _PROJECT_LOCKS_LOCK:
        lock = _PROJECT_LOCKS.setdefault(project_public_id, threading.RLock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _cache_key(
    project_public_id: str,
    owner_user_id: str,
    policy: ProvisioningPolicy,
    earth_fingerprint: Optional[str],
) -> str:
    return "|".join(
        (
            project_public_id,
            owner_user_id,
            policy.requested_template,
            policy.fallback_template,
            "1" if policy.allow_fallback else "0",
            earth_fingerprint or "no-reference",
        )
    )


def _cache_get(
    key: str,
    project: Any,
) -> Optional[ProjectChunkProvisioningResult]:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            _CACHE.pop(key, None)
            return None
        result = entry.result
        if not _complete_refs(project):
            return None
        if _attr(project, "chunk_project_id") != result.chunk_project_id:
            return None
        if _attr(project, "chunk_universe_id") != result.chunk_universe_id:
            return None
        if _attr(project, "chunk_world_id") != result.chunk_world_id:
            return None
        return ProjectChunkProvisioningResult(**asdict(result))


def _cache_set(key: str, result: ProjectChunkProvisioningResult, ttl: float) -> None:
    if ttl <= 0 or not result.ok:
        return
    with _CACHE_LOCK:
        _CACHE[key] = _CacheEntry(
            time.monotonic() + ttl,
            ProjectChunkProvisioningResult(**asdict(result)),
        )
        now = time.monotonic()
        for expired_key in [k for k, value in _CACHE.items() if value.expires_at <= now]:
            _CACHE.pop(expired_key, None)
        max_entries = _integer(
            _config("VECTOPLAN_APP_CHUNK_PROVISIONING_CACHE_MAX_ENTRIES", 512),
            512,
            16,
            10000,
        )
        if len(_CACHE) > max_entries:
            oldest = sorted(_CACHE.items(), key=lambda item: item[1].expires_at)
            for old_key, _ in oldest[: len(_CACHE) - max_entries]:
                _CACHE.pop(old_key, None)


def _chunk_client_module() -> Any:
    try:
        return import_module("services.chunk_client")
    except Exception as exc:
        raise ProjectChunkProvisioningError(
            "chunk_client_unavailable",
            "services.chunk_client could not be imported.",
            status_code=503,
            retryable=True,
            details={"exceptionType": exc.__class__.__name__},
            cause=exc,
        ) from exc


def _chunk_callable(client: Any) -> Callable[..., Any]:
    if callable(client) and not inspect.ismodule(client):
        return client
    names = (
        "provision_chunk_project_for_app_project",
        "ensure_chunk_project_for_app_project",
        "ensure_chunk_project",
    )
    for name in names:
        candidate = getattr(client, name, None)
        if callable(candidate):
            return candidate
    raise ProjectChunkProvisioningError(
        "chunk_client_contract_missing",
        "The chunk client exposes no supported provisioning function.",
        status_code=503,
        details={"expectedFunctions": list(names)},
    )


def _georeference_builder() -> Optional[Callable[..., Any]]:
    try:
        candidate = getattr(
            import_module("services.project_georeference_service"),
            "build_project_earth_reference",
            None,
        )
        return candidate if callable(candidate) else None
    except Exception:
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
        selected = (
            {key: kwargs[key] for key in preferred_keys if key in kwargs}
            if preferred_keys
            else dict(kwargs)
        )
        return callable_obj(**selected)

    selected: dict[str, Any] = {}
    missing: list[str] = []
    for name, parameter in parameters.items():
        if name in {"self", "cls"}:
            continue
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
        }:
            if parameter.default is inspect.Parameter.empty:
                missing.append(name)
            continue
        if name in kwargs:
            selected[name] = kwargs[name]
        elif parameter.default is inspect.Parameter.empty:
            missing.append(name)
    if missing:
        raise ProjectChunkProvisioningError(
            "integration_signature_incompatible",
            "An integration callable has unsupported required parameters.",
            status_code=500,
            details={"missingRequiredParameters": missing},
        )
    return callable_obj(**selected)


def _client_fallback_allowed(
    error: ProjectChunkProvisioningError,
    policy: ProvisioningPolicy,
) -> bool:
    if policy.requested_template != "earth":
        return False
    if policy.fallback_template != "flat":
        return False
    if not policy.allow_fallback or not policy.allow_client_fallback:
        return False
    configured = _config(
        "VECTOPLAN_APP_CHUNK_FALLBACK_ERROR_CODES",
        FALLBACK_ELIGIBLE_CODES,
    )
    allowed = {
        _code(item, "")
        for item in _config_values(configured)
        if _code(item, "")
    }
    return error.code in allowed


# ---------------------------------------------------------------------------
# Flask, Auth und allgemeine Helper
# ---------------------------------------------------------------------------


def _db_session() -> Any:
    try:
        db = getattr(import_module("extensions"), "db", None)
        return getattr(db, "session", None)
    except Exception:
        return None


def _current_owner_auth_user_id(project: Any) -> Optional[str]:
    """Verwendet CurrentUser nur, wenn er zum lokalen Projektowner passt."""

    try:
        getter = getattr(import_module("services.current_user"), "get_current_user_context", None)
        if not callable(getter):
            return None
        context = getter()
        if is_dataclass(context):
            context = asdict(context)
        if not isinstance(context, Mapping):
            return None
        if not bool(context.get("authenticated")):
            return None
        if bool(context.get("auth_unavailable") or context.get("user_blocked")):
            return None

        project_owner_id = _attr(project, "owner_user_id")
        current_local_id = _first(
            context.get("user_id"),
            context.get("local_user_id"),
            _deep(context, "user", "local_id"),
        )
        if (
            project_owner_id not in (None, "")
            and current_local_id not in (None, "")
            and str(project_owner_id) != str(current_local_id)
        ):
            return None

        return _text(
            _first(
                context.get("auth_user_id"),
                _deep(context, "auth", "user_id"),
                _deep(context, "auth", "user", "id"),
                _deep(context, "user", "auth_user_id"),
            ),
            "",
            240,
        ) or None
    except Exception as exc:
        LOGGER.debug("Current owner auth lookup unavailable: %s", exc)
        return None


def _external_user_id(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    normalized = _text(value, "", 240)
    lowered = normalized.lower()
    if not normalized or lowered in {"0", "none", "null", "guest", "anonymous"}:
        return None
    if lowered.startswith(("cid_", "guest_", "anonymous_")):
        return None
    return normalized


def _config(name: str, default: Any = None) -> Any:
    try:
        flask = import_module("flask")
        if getattr(flask, "has_app_context", lambda: False)():
            return flask.current_app.config.get(name, default)
    except Exception:
        pass
    return default


def _request_id() -> Optional[str]:
    try:
        flask = import_module("flask")
        if not getattr(flask, "has_request_context", lambda: False)():
            return None
        return _text(
            _first(
                flask.request.headers.get("X-Request-ID"),
                flask.request.headers.get("X-Correlation-ID"),
            ),
            "",
            200,
        ) or None
    except Exception:
        return None


def _require_project_public_id(project: Any) -> str:
    value = _project_public_id(project)
    if not value:
        raise ProjectChunkProvisioningError(
            "app_project_public_id_missing",
            "Project.public_id must exist before chunk provisioning.",
            status_code=422,
        )
    return value


def _project_public_id(project: Any) -> Optional[str]:
    return _text(
        _first(_attr(project, "public_id"), _attr(project, "project_public_id")),
        "",
        240,
    ) or None


def _world_template(value: Any) -> str:
    normalized = _text(value, DEFAULT_WORLD_TEMPLATE, 32).lower()
    normalized = {
        "earth-v1": "earth",
        "earth_world": "earth",
        "flat-v1": "flat",
        "flat_world": "flat",
        "default": "flat",
    }.get(normalized, normalized)
    if normalized not in SUPPORTED_WORLD_TEMPLATES:
        raise ProjectChunkProvisioningError(
            "unsupported_world_template",
            f"Unsupported world template: {normalized!r}.",
            status_code=422,
            details={"supported": sorted(SUPPORTED_WORLD_TEMPLATES)},
        )
    return normalized


def _idempotency_key(project_public_id: str) -> str:
    digest = hashlib.sha256(
        f"{POLICY_VERSION}:{project_public_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"vp-app-chunk:{project_public_id}:{digest}"


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _response_summary(response: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "ok",
        "code",
        "status",
        "statusCode",
        "created",
        "updated",
        "reused",
        "chunkProjectId",
        "projectId",
        "chunkUniverseId",
        "universeId",
        "chunkWorldId",
        "spawnWorldId",
        "defaultWorldId",
        "worldTemplate",
        "requestedWorldTemplate",
        "effectiveWorldTemplate",
        "fallbackUsed",
        "fallbackReason",
        "earthReferenceFingerprint",
        "authzEnforced",
        "warnings",
    )
    summary = {key: response.get(key) for key in allowed if key in response}
    for name in ("data", "result", "context"):
        nested = response.get(name)
        if isinstance(nested, Mapping):
            values = {key: nested.get(key) for key in allowed if key in nested}
            if values:
                summary[name] = values
    return _sanitize(summary, depth=4)


def _sanitize(value: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    if depth <= 0:
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _text(raw_key, "", 200)
        if not key:
            continue
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            result[key] = "[REDACTED]"
        else:
            result[key] = _json_safe(raw_value, depth=depth - 1)
    return result


def _json_safe(value: Any, *, depth: int = 5) -> Any:
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
        return _sanitize(value, depth=depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth - 1) for item in list(value)[:500]]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict(), depth=depth - 1)
        except Exception:
            pass
    return _text(value, f"<{type(value).__name__}>", 500)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default) if obj is not None else default
    except Exception:
        return default


def _deep(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, ()):
            return value
    return None


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _text(value: Any, default: str = "", max_len: int = 1000) -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
    except Exception:
        return default
    return (result or default)[:max_len]


def _code(value: Any, default: str) -> str:
    result = _text(value, default, 160).lower().replace("-", "_").replace(" ", "_")
    return "".join(char for char in result if char.isalnum() or char in {"_", "."}) or default


def _integer(
    value: Any,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _number(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        result = _number(value, None)
        if result is not None:
            return result
    return None


def _boolean(explicit: Any, configured: Any, default: bool) -> bool:
    value = explicit if explicit is not None else configured
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = _text(value, "", 16).lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        result = _text(value, "", 500)
        return (result,) if result else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return tuple(result for result in (_text(item, "", 500) for item in value) if result)
    result = _text(value, "", 500)
    return (result,) if result else ()


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _text(value, "", 500)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _config_values(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "EarthReferenceResolution",
    "POLICY_VERSION",
    "ProjectChunkProvisioningError",
    "ProjectChunkProvisioningResult",
    "ProjectChunkProvisioningService",
    "ProvisioningPolicy",
    "ProvisioningStatus",
    "RESULT_VERSION",
    "SERVICE_VERSION",
    "clear_project_chunk_provisioning_cache",
    "ensure_project_chunk_provisioned",
    "get_project_chunk_provisioning_status",
    "provision_chunk_project_for_project",
    "provision_project_chunk_graph",
    "provision_project_chunk_graph_or_raise",
    "retry_project_chunk_provisioning",
    "serialize_project_chunk_provisioning_status",
]
