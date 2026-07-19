# services/vectoplan-app/services/project_chunk_access_sync_service.py
"""Idempotente Synchronisation von App-Projektzugriffen zu vectoplan-chunk.

Fachliche Grenze
================

``vectoplan-app`` ist die führende Wahrheit für Projekt-Owner und direkte
Projektmitgliedschaften. ``vectoplan-chunk`` hält eine projektgescopte,
lokale Projektion dieser Zugriffe, damit Chunk-, World- und Command-Routen
ohne direkten Datenbankzugriff auf vectoplan-app autorisieren können.

Dieser Service:

* verwendet ausschließlich kanonische ``auth_user_id``-Werte als externe
  opaque User-IDs;
* überträgt niemals die lokale ``AppUser.id`` als Chunk-Identität;
* synchronisiert die kanonischen Rollen ``owner``, ``admin``, ``editor`` und
  ``viewer``;
* stellt sicher, dass ``viewer`` nur lesen und keine Mutation ausführen kann;
* behandelt Owner-Transfer ausschließlich über den dedizierten Chunk-Endpunkt;
* gleicht den vollständigen direkten User-Assignment-Zustand ab;
* widerruft veraltete direkte User-Assignments, lässt Gruppen-Assignments aber
  unangetastet;
* ist idempotent und nach Teilfehlern sicher wiederholbar;
* führt bei ``commit=False`` keine entfernte Mutation aus;
* persistiert einen reparierbaren lokalen Sync-Status vor und nach Remote-I/O;
* nutzt einen kleinen, begrenzten, thread-sicheren Erfolgscache;
* enthält keine verteilte Transaktion und behauptet keine globale Atomizität.

Wichtig: Die tatsächliche Durchsetzung der gespeicherten Rollen geschieht im
zentralen Authorizer von vectoplan-chunk. Dieser Service synchronisiert die
Datenbasis, er ersetzt den Chunk-Authorizer nicht.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover - Flask ist im normalen Runtimepfad vorhanden.
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[no-redef]
        return False


try:
    from extensions import db
except Exception:  # pragma: no cover - ermöglicht isolierte Import-/Contracttests.
    db = None  # type: ignore[assignment]


try:
    from models import AppUser, ProjectMembership
except Exception:  # pragma: no cover
    AppUser = None  # type: ignore[assignment]
    ProjectMembership = None  # type: ignore[assignment]


try:
    from services.chunk_client import (
        ACCESS_SYNC_CONTRACT_VERSION as CHUNK_CLIENT_ACCESS_SYNC_CONTRACT_VERSION,
        ChunkClient,
        ChunkClientConfig,
        ChunkClientError,
        ChunkClientResult,
        get_chunk_client,
    )
except Exception:  # pragma: no cover
    CHUNK_CLIENT_ACCESS_SYNC_CONTRACT_VERSION = "chunk-project-access-sync.v1"
    ChunkClient = None  # type: ignore[assignment]
    ChunkClientConfig = None  # type: ignore[assignment]
    ChunkClientResult = None  # type: ignore[assignment]

    class ChunkClientError(RuntimeError):  # type: ignore[no-redef]
        pass

    def get_chunk_client() -> Any:  # type: ignore[no-redef]
        raise RuntimeError("services.chunk_client is unavailable")


SERVICE_VERSION = "1.0.0"
SYNC_CONTRACT_VERSION = "vectoplan-app-project-chunk-access-sync.v1"
REMOTE_ACCESS_CONTRACT_VERSION = CHUNK_CLIENT_ACCESS_SYNC_CONTRACT_VERSION

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
SUPPORTED_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER})
ROLE_PRIORITY = {
    ROLE_VIEWER: 10,
    ROLE_EDITOR: 20,
    ROLE_ADMIN: 30,
    ROLE_OWNER: 40,
}

SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_SYNCING = "syncing"
SYNC_STATUS_READY = "ready"
SYNC_STATUS_FAILED = "failed"
SYNC_STATUS_REPAIR_REQUIRED = "repair_required"
SYNC_STATUS_DISABLED = "disabled"

ACTIVE_MEMBERSHIP_STATUSES = frozenset({"active", "enabled", "accepted", "joined"})
INACTIVE_MEMBERSHIP_STATUSES = frozenset(
    {"inactive", "disabled", "revoked", "removed", "deleted", "expired", "rejected"}
)
ACTIVE_ASSIGNMENT_STATUSES = frozenset({"active", "enabled"})
INACTIVE_ASSIGNMENT_STATUSES = frozenset({"inactive", "revoked", "removed", "deleted", "expired"})

VIEWER_MUTATION_PERMISSIONS = frozenset(
    {
        "edit",
        "write",
        "set",
        "delete",
        "manage",
        "manage_team",
        "manage_settings",
        "transfer",
        "admin",
        "command",
        "commands",
        "materialize",
        "create",
        "update",
        "remove",
        "place_object",
        "remove_object",
        "set_block",
        "remove_block",
        "replace_block",
    }
)

DEFAULT_CACHE_TTL_SECONDS = 5.0
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_MAX_MEMBERSHIPS = 10_000
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_VERIFY_AFTER_SYNC = True
DEFAULT_PRUNE_STALE_ASSIGNMENTS = True
DEFAULT_STRICT_IDENTITIES = True
DEFAULT_FORWARD_PERMISSION_OVERRIDES = False
DEFAULT_FAIL_ON_PARTIAL_ERROR = True


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------


def _safe_str(value: Any, default: str = "", max_len: int = 2000) -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return text[:max_len] if max_len > 0 else text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = _safe_str(value, "", 40).lower()
        if text in {"1", "true", "t", "yes", "y", "on", "ja", "enabled"}:
            return True
        if text in {"0", "false", "f", "no", "n", "off", "nein", "disabled"}:
            return False
        return default
    except Exception:
        return default


def _safe_int(
    value: Any,
    default: int = 0,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        if value is None or isinstance(value, bool):
            result = int(default)
        else:
            result = int(str(value).strip())
    except Exception:
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def _safe_float(
    value: Any,
    default: float = 0.0,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        if value is None or isinstance(value, bool):
            result = float(default)
        else:
            result = float(str(value).strip())
    except Exception:
        result = float(default)
    if minimum is not None:
        result = max(float(minimum), result)
    if maximum is not None:
        result = min(float(maximum), result)
    return result


def _safe_mapping(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            result = value.to_dict()
            return dict(result) if isinstance(result, Mapping) else {}
    except Exception:
        pass
    return {}


def _safe_list(value: Any) -> List[Any]:
    try:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, (tuple, set, frozenset)):
            return list(value)
    except Exception:
        pass
    return []


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "<max-depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            _safe_str(key, "", 240): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    try:
        if hasattr(value, "isoformat") and callable(value.isoformat):
            return value.isoformat()
    except Exception:
        pass
    return _safe_str(value, repr(value), 4000)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat") and callable(value.isoformat):
            return value.isoformat()
        return _safe_str(value, "", 120) or None
    except Exception:
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            result = value
        else:
            text = _safe_str(value, "", 120)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            result = datetime.fromisoformat(text)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
        return None


def _duration_ms(started: float) -> int:
    try:
        return max(0, int((time.monotonic() - started) * 1000))
    except Exception:
        return 0


def _request_id(value: Any = None) -> str:
    return _safe_str(value, "", 180) or uuid.uuid4().hex


def _hash_payload(value: Any) -> str:
    try:
        encoded = json.dumps(
            _json_safe(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except Exception:
        return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()


def _env(name: str, default: Any = None) -> Any:
    try:
        return os.environ.get(name, default)
    except Exception:
        return default


def _config_value(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None and name in current_app.config:
            return current_app.config.get(name, default)
    except Exception:
        pass
    return _env(name, default)


def _config_bool(name: str, default: bool = False) -> bool:
    return _safe_bool(_config_value(name, default), default)


def _config_int(name: str, default: int, minimum: int, maximum: int) -> int:
    return _safe_int(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _config_float(name: str, default: float, minimum: float, maximum: float) -> float:
    return _safe_float(_config_value(name, default), default, minimum=minimum, maximum=maximum)


def _log(level: str, message: str, *args: Any) -> None:
    try:
        if not has_app_context() or current_app is None:
            return
        logger = current_app.logger
        fn = getattr(logger, level, logger.info)
        fn(message, *args)
    except Exception:
        pass


def _canonical_auth_user_id(value: Any, *, required: bool = True) -> Optional[str]:
    text = _safe_str(value, "", 160)
    if not text:
        if required:
            raise ValueError("canonical auth_user_id is required")
        return None
    lowered = text.lower()
    if lowered in {"guest", "anonymous", "none", "null", "unknown", "default", "demo"}:
        if required:
            raise ValueError("guest/default identity is not a canonical auth_user_id")
        return None
    if lowered.startswith("cid_") or lowered.startswith("guest_"):
        if required:
            raise ValueError("client/guest identity must not be used as auth_user_id")
        return None
    return text


def _normalize_role(value: Any, default: str = ROLE_VIEWER) -> str:
    text = _safe_str(value, default, 80).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "read": ROLE_VIEWER,
        "readonly": ROLE_VIEWER,
        "read_only": ROLE_VIEWER,
        "spectator": ROLE_VIEWER,
        "member": ROLE_VIEWER,
        "write": ROLE_EDITOR,
        "author": ROLE_EDITOR,
        "manager": ROLE_ADMIN,
        "administrator": ROLE_ADMIN,
        "project_admin": ROLE_ADMIN,
        "project_owner": ROLE_OWNER,
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in SUPPORTED_ROLES else default


def _project_public_id(project: Any) -> str:
    try:
        return (
            _safe_str(getattr(project, "public_id", None), "", 240)
            or _safe_str(getattr(project, "app_project_public_id", None), "", 240)
            or _safe_str(getattr(project, "id", None), "", 240)
        )
    except Exception:
        return ""


def _chunk_project_id(project: Any) -> str:
    try:
        direct = _safe_str(getattr(project, "chunk_project_id", None), "", 240)
        if direct:
            return direct
        refs = _safe_mapping(getattr(project, "service_refs", None))
        chunk = _safe_mapping(refs.get("chunk"))
        direct = _safe_str(chunk.get("chunk_project_id") or chunk.get("chunkProjectId"), "", 240)
        if direct:
            return direct
        metadata = _safe_mapping(getattr(project, "metadata_json", None))
        chunk = _safe_mapping(metadata.get("chunk"))
        return _safe_str(chunk.get("chunk_project_id") or chunk.get("chunkProjectId"), "", 240)
    except Exception:
        return ""


def _project_is_demo(project: Any) -> bool:
    try:
        if _safe_bool(getattr(project, "is_demo", False), False):
            return True
        if _safe_str(getattr(project, "project_scope", ""), "", 40).lower() == "demo":
            return True
        metadata = _safe_mapping(getattr(project, "metadata_json", None))
        return _safe_bool(_safe_mapping(metadata.get("vectoplan_demo")).get("enabled"), False)
    except Exception:
        return False


def _project_deleted(project: Any) -> bool:
    try:
        if _safe_bool(getattr(project, "is_deleted", False), False):
            return True
        if getattr(project, "deleted_at", None) is not None:
            return True
        return _safe_str(getattr(project, "status", ""), "", 40).lower() in {"deleted", "expired"}
    except Exception:
        return False


def _project_metadata(project: Any) -> Dict[str, Any]:
    try:
        return _safe_mapping(getattr(project, "metadata_json", None))
    except Exception:
        return {}


def _set_project_metadata(project: Any, metadata: Mapping[str, Any]) -> None:
    try:
        if hasattr(project, "metadata_json"):
            project.metadata_json = _safe_mapping(metadata)
    except Exception:
        pass


def _db_session() -> Any:
    try:
        return getattr(db, "session", None)
    except Exception:
        return None


def _db_add(project: Any) -> None:
    session = _db_session()
    if session is None:
        return
    try:
        session.add(project)
    except Exception:
        pass


def _db_commit() -> None:
    session = _db_session()
    if session is None:
        return
    session.commit()


def _db_flush() -> None:
    session = _db_session()
    if session is None:
        return
    session.flush()


def _db_rollback() -> None:
    session = _db_session()
    if session is None:
        return
    try:
        session.rollback()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectChunkAccessSyncConfig:
    enabled: bool = True
    required: bool = False
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES
    max_memberships: int = DEFAULT_MAX_MEMBERSHIPS
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    verify_after_sync: bool = DEFAULT_VERIFY_AFTER_SYNC
    prune_stale_direct_assignments: bool = DEFAULT_PRUNE_STALE_ASSIGNMENTS
    strict_identities: bool = DEFAULT_STRICT_IDENTITIES
    forward_permission_overrides: bool = DEFAULT_FORWARD_PERMISSION_OVERRIDES
    fail_on_partial_error: bool = DEFAULT_FAIL_ON_PARTIAL_ERROR

    @classmethod
    def from_app(cls) -> "ProjectChunkAccessSyncConfig":
        return cls(
            enabled=_config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_ENABLED", True),
            required=_config_bool("VECTOPLAN_APP_CHUNK_ACCESS_SYNC_REQUIRED", False),
            cache_ttl_seconds=_config_float(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_CACHE_SECONDS",
                DEFAULT_CACHE_TTL_SECONDS,
                0.0,
                300.0,
            ),
            cache_max_entries=_config_int(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_CACHE_MAX_ENTRIES",
                DEFAULT_CACHE_MAX_ENTRIES,
                0,
                10_000,
            ),
            max_memberships=_config_int(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_MAX_MEMBERSHIPS",
                DEFAULT_MAX_MEMBERSHIPS,
                1,
                100_000,
            ),
            lock_timeout_seconds=_config_float(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_LOCK_TIMEOUT_SECONDS",
                DEFAULT_LOCK_TIMEOUT_SECONDS,
                0.1,
                300.0,
            ),
            verify_after_sync=_config_bool(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_VERIFY_AFTER_SYNC",
                DEFAULT_VERIFY_AFTER_SYNC,
            ),
            prune_stale_direct_assignments=_config_bool(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_PRUNE_STALE_DIRECT_ASSIGNMENTS",
                DEFAULT_PRUNE_STALE_ASSIGNMENTS,
            ),
            strict_identities=_config_bool(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_STRICT_IDENTITIES",
                DEFAULT_STRICT_IDENTITIES,
            ),
            forward_permission_overrides=_config_bool(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_FORWARD_PERMISSION_OVERRIDES",
                DEFAULT_FORWARD_PERMISSION_OVERRIDES,
            ),
            fail_on_partial_error=_config_bool(
                "VECTOPLAN_APP_CHUNK_ACCESS_SYNC_FAIL_ON_PARTIAL_ERROR",
                DEFAULT_FAIL_ON_PARTIAL_ERROR,
            ),
        )

    def public_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "required": self.required,
            "cacheTtlSeconds": self.cache_ttl_seconds,
            "cacheMaxEntries": self.cache_max_entries,
            "maxMemberships": self.max_memberships,
            "lockTimeoutSeconds": self.lock_timeout_seconds,
            "verifyAfterSync": self.verify_after_sync,
            "pruneStaleDirectAssignments": self.prune_stale_direct_assignments,
            "strictIdentities": self.strict_identities,
            "forwardPermissionOverrides": self.forward_permission_overrides,
            "failOnPartialError": self.fail_on_partial_error,
        }


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesiredProjectAssignment:
    auth_user_id: str
    role: str
    local_user_id: Optional[int] = None
    membership_id: Optional[str] = None
    starts_at: Optional[str] = None
    expires_at: Optional[str] = None
    permission_overrides: Dict[str, Any] = field(default_factory=dict)
    source: str = "membership"

    def to_dict(self, *, include_local_ids: bool = False) -> Dict[str, Any]:
        payload = {
            "authUserId": self.auth_user_id,
            "role": self.role,
            "startsAt": self.starts_at,
            "expiresAt": self.expires_at,
            "permissionOverrides": _json_safe(self.permission_overrides),
            "source": self.source,
        }
        if include_local_ids:
            payload["localUserId"] = self.local_user_id
            payload["membershipId"] = self.membership_id
        return payload


@dataclass(frozen=True)
class DesiredProjectAccessState:
    app_project_public_id: str
    chunk_project_id: str
    owner_auth_user_id: str
    assignments: Tuple[DesiredProjectAssignment, ...]
    fingerprint: str
    warnings: Tuple[Dict[str, Any], ...] = ()
    errors: Tuple[Dict[str, Any], ...] = ()

    @property
    def roles_by_user(self) -> Dict[str, str]:
        return {item.auth_user_id: item.role for item in self.assignments}

    def to_dict(self, *, include_local_ids: bool = False) -> Dict[str, Any]:
        return {
            "appProjectPublicId": self.app_project_public_id,
            "chunkProjectId": self.chunk_project_id,
            "ownerAuthUserId": self.owner_auth_user_id,
            "fingerprint": self.fingerprint,
            "assignments": [
                item.to_dict(include_local_ids=include_local_ids)
                for item in self.assignments
            ],
            "warnings": [_json_safe(item) for item in self.warnings],
            "errors": [_json_safe(item) for item in self.errors],
        }


@dataclass(frozen=True)
class ProjectChunkAccessSyncResult:
    ok: bool
    code: str
    status_code: int
    app_project_public_id: str = ""
    chunk_project_id: str = ""
    owner_auth_user_id: str = ""
    actor_auth_user_id: Optional[str] = None
    request_id: str = ""
    fingerprint: str = ""
    desired_count: int = 0
    remote_owner_before: Optional[str] = None
    remote_owner_after: Optional[str] = None
    authz_enforced: Optional[bool] = None
    stats: Dict[str, int] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    warnings: Tuple[Dict[str, Any], ...] = ()
    errors: Tuple[Dict[str, Any], ...] = ()
    duration_ms: int = 0
    retryable: bool = False
    repair_required: bool = False
    cache_hit: bool = False

    @property
    def message(self) -> str:
        if self.errors:
            return _safe_str(self.errors[0].get("message"), self.code, 2000)
        return self.code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resultVersion": SYNC_CONTRACT_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "ok": self.ok,
            "code": self.code,
            "statusCode": self.status_code,
            "appProjectPublicId": self.app_project_public_id,
            "chunkProjectId": self.chunk_project_id,
            "ownerAuthUserId": self.owner_auth_user_id,
            "actorAuthUserId": self.actor_auth_user_id,
            "requestId": self.request_id,
            "fingerprint": self.fingerprint,
            "desiredCount": self.desired_count,
            "remoteOwnerBefore": self.remote_owner_before,
            "remoteOwnerAfter": self.remote_owner_after,
            "authzEnforced": self.authz_enforced,
            "stats": dict(self.stats),
            "verification": _json_safe(self.verification),
            "warnings": [_json_safe(item) for item in self.warnings],
            "errors": [_json_safe(item) for item in self.errors],
            "durationMs": self.duration_ms,
            "retryable": self.retryable,
            "repairRequired": self.repair_required,
            "cacheHit": self.cache_hit,
            "message": self.message,
        }


class ProjectChunkAccessSyncError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
        repair_required: bool = False,
        result: Optional[ProjectChunkAccessSyncResult] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = _safe_str(code, "chunk_access_sync_failed", 160)
        self.message = _safe_str(message, "Chunk access synchronization failed.", 2000)
        self.status_code = _safe_int(status_code, 502, minimum=400, maximum=599)
        self.retryable = bool(retryable)
        self.repair_required = bool(repair_required)
        self.result = result
        self.details = _safe_mapping(details)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "statusCode": self.status_code,
            "retryable": self.retryable,
            "repairRequired": self.repair_required,
            "details": _json_safe(self.details),
        }


class ProjectChunkAccessContractError(ProjectChunkAccessSyncError):
    pass


# ---------------------------------------------------------------------------
# Tiny bounded process cache and project locks
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    expires_at: float
    result: ProjectChunkAccessSyncResult


_CACHE_LOCK = threading.RLock()
_CACHE: "OrderedDict[str, _CacheEntry]" = OrderedDict()
_LOCKS_GUARD = threading.RLock()
_PROJECT_LOCKS: "OrderedDict[str, threading.Lock]" = OrderedDict()
_MAX_PROJECT_LOCKS = 1024


def _cache_key(chunk_project_id: str, fingerprint: str) -> str:
    return f"{chunk_project_id}:{fingerprint}"


def _cache_get(key: str) -> Optional[ProjectChunkAccessSyncResult]:
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
        _CACHE.move_to_end(key)
        return replace(copy.deepcopy(entry.result), cache_hit=True)


def _cache_set(
    key: str,
    result: ProjectChunkAccessSyncResult,
    *,
    ttl_seconds: float,
    max_entries: int,
) -> None:
    if not key or not result.ok or ttl_seconds <= 0 or max_entries <= 0:
        return
    now = time.monotonic()
    with _CACHE_LOCK:
        expired = [name for name, entry in _CACHE.items() if entry.expires_at <= now]
        for name in expired:
            _CACHE.pop(name, None)
        _CACHE[key] = _CacheEntry(now + ttl_seconds, copy.deepcopy(result))
        _CACHE.move_to_end(key)
        while len(_CACHE) > max_entries:
            _CACHE.popitem(last=False)


def clear_project_chunk_access_sync_cache(project_ref: Any = None) -> int:
    """Leert den prozesslokalen Erfolgscache vollständig oder projektspezifisch."""
    with _CACHE_LOCK:
        if project_ref is None:
            count = len(_CACHE)
            _CACHE.clear()
            return count
        prefix = _safe_str(project_ref, "", 240)
        if not prefix:
            return 0
        keys = [key for key in _CACHE if key.startswith(f"{prefix}:")]
        for key in keys:
            _CACHE.pop(key, None)
        return len(keys)


def _project_lock(chunk_project_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(chunk_project_id)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[chunk_project_id] = lock
        _PROJECT_LOCKS.move_to_end(chunk_project_id)
        while len(_PROJECT_LOCKS) > _MAX_PROJECT_LOCKS:
            _PROJECT_LOCKS.popitem(last=False)
        return lock


# ---------------------------------------------------------------------------
# Local desired-state extraction
# ---------------------------------------------------------------------------


def _app_user_auth_user_id(app_user: Any) -> Optional[str]:
    if app_user is None:
        return None
    for field_name in ("auth_user_id", "platform_user_id", "external_user_id"):
        try:
            value = _canonical_auth_user_id(getattr(app_user, field_name, None), required=False)
            if value:
                return value
        except Exception:
            continue
    return None


def _bulk_app_users(local_user_ids: Iterable[int]) -> Dict[int, Any]:
    ids = sorted({int(value) for value in local_user_ids if _safe_int(value, 0) > 0})
    if not ids or AppUser is None:
        return {}
    result: Dict[int, Any] = {}
    try:
        field = getattr(AppUser, "id", None)
        query = getattr(AppUser, "query", None)
        if field is not None and query is not None and hasattr(field, "in_"):
            for row in query.filter(field.in_(ids)).all():
                row_id = _safe_int(getattr(row, "id", None), 0)
                if row_id:
                    result[row_id] = row
            return result
    except Exception:
        pass
    query = getattr(AppUser, "query", None)
    for local_id in ids:
        try:
            row = query.get(local_id) if query is not None else None
            if row is not None:
                result[local_id] = row
        except Exception:
            continue
    return result


def _membership_rows(project: Any, *, limit: int) -> List[Any]:
    if ProjectMembership is None:
        return []
    project_id = _safe_int(getattr(project, "id", None), 0)
    if project_id <= 0:
        return []
    try:
        query = ProjectMembership.query.filter_by(project_id=project_id)
        try:
            query = query.order_by(ProjectMembership.created_at.asc())
        except Exception:
            pass
        try:
            return list(query.limit(limit + 1).all())
        except Exception:
            return list(query.all())[: limit + 1]
    except Exception as exc:
        raise ProjectChunkAccessSyncError(
            "project_memberships_load_failed",
            f"Project memberships could not be loaded: {exc}",
            status_code=500,
            retryable=True,
        ) from exc


def _membership_local_user_id(row: Any) -> Optional[int]:
    for field_name in ("user_id", "app_user_id", "member_user_id"):
        try:
            value = _safe_int(getattr(row, field_name, None), 0)
            if value > 0:
                return value
        except Exception:
            continue
    return None


def _membership_id(row: Any) -> Optional[str]:
    for field_name in ("public_id", "id", "membership_id"):
        try:
            value = _safe_str(getattr(row, field_name, None), "", 180)
            if value:
                return value
        except Exception:
            continue
    return None


def _membership_active(row: Any) -> bool:
    try:
        if getattr(row, "deleted_at", None) is not None or getattr(row, "revoked_at", None) is not None:
            return False
        status = _safe_str(getattr(row, "status", None), "", 40).lower()
        if status in INACTIVE_MEMBERSHIP_STATUSES:
            return False
        expires_at = _parse_datetime(getattr(row, "expires_at", None))
        if expires_at is not None and expires_at <= _utcnow():
            return False
        explicit = getattr(row, "is_active", None)
        if explicit is not None and not _safe_bool(explicit, True):
            return False
        if status:
            return status in ACTIVE_MEMBERSHIP_STATUSES
        return True
    except Exception:
        return False


def _membership_role(row: Any) -> str:
    candidates: List[Any] = []
    for field_name in ("role", "project_role", "role_name", "access_role"):
        try:
            candidates.append(getattr(row, field_name, None))
        except Exception:
            pass
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            candidate = candidate.get("key") or candidate.get("name") or candidate.get("role")
        elif candidate is not None and not isinstance(candidate, str):
            candidate = getattr(candidate, "key", None) or getattr(candidate, "name", None) or candidate
        text = _safe_str(candidate, "", 80)
        if text:
            return _normalize_role(text)
    return ROLE_VIEWER


def _membership_direct_auth_user_id(row: Any) -> Optional[str]:
    for field_name in ("auth_user_id", "platform_user_id", "external_user_id"):
        try:
            value = _canonical_auth_user_id(getattr(row, field_name, None), required=False)
            if value:
                return value
        except Exception:
            continue
    return None


def _membership_overrides(row: Any, *, role: str, forward: bool) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    if forward:
        for field_name in ("permission_overrides", "permissions", "overrides"):
            try:
                raw = _safe_mapping(getattr(row, field_name, None))
                if raw:
                    break
            except Exception:
                continue
    if role != ROLE_VIEWER:
        return raw
    # Viewer darf durch alte/lokale Overrides niemals mutierende Rechte erhalten.
    allow = {
        _safe_str(item, "", 120).lower()
        for item in _safe_list(raw.get("allow") or raw.get("allows"))
        if _safe_str(item, "", 120)
    }
    allow = {item for item in allow if item not in VIEWER_MUTATION_PERMISSIONS}
    deny = {
        _safe_str(item, "", 120).lower()
        for item in _safe_list(raw.get("deny") or raw.get("denies"))
        if _safe_str(item, "", 120)
    }
    deny.update(VIEWER_MUTATION_PERMISSIONS)
    return {
        "allow": sorted(allow or {"view"}),
        "deny": sorted(deny),
        "policy": "viewer-read-only",
    }


def _assignment_fingerprint_payload(assignments: Sequence[DesiredProjectAssignment]) -> List[Dict[str, Any]]:
    return [
        {
            "authUserId": item.auth_user_id,
            "role": item.role,
            "startsAt": item.starts_at,
            "expiresAt": item.expires_at,
            "permissionOverrides": _json_safe(item.permission_overrides),
        }
        for item in sorted(assignments, key=lambda entry: (entry.auth_user_id, entry.role))
    ]


def build_desired_project_chunk_access(
    project: Any,
    *,
    config: Optional[ProjectChunkAccessSyncConfig] = None,
) -> DesiredProjectAccessState:
    """Ermittelt die vollständige gewünschte direkte User-Rollenprojektion."""
    cfg = config or ProjectChunkAccessSyncConfig.from_app()
    if project is None:
        raise ProjectChunkAccessContractError("project_required", "project is required", status_code=400)
    app_project_public_id = _project_public_id(project)
    chunk_project_id = _chunk_project_id(project)
    owner_auth_user_id = _canonical_auth_user_id(
        getattr(project, "auth_owner_user_id", None),
        required=True,
    )
    rows = _membership_rows(project, limit=cfg.max_memberships)
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if len(rows) > cfg.max_memberships:
        raise ProjectChunkAccessContractError(
            "project_membership_limit_exceeded",
            f"Project has more than {cfg.max_memberships} memberships.",
            status_code=409,
            repair_required=True,
        )
    local_ids = [value for value in (_membership_local_user_id(row) for row in rows) if value]
    users = _bulk_app_users(local_ids)
    desired: Dict[str, DesiredProjectAssignment] = {
        owner_auth_user_id: DesiredProjectAssignment(
            auth_user_id=owner_auth_user_id,
            role=ROLE_OWNER,
            local_user_id=_safe_int(getattr(project, "owner_user_id", None), 0) or None,
            source="project_owner",
        )
    }
    for row in rows:
        if not _membership_active(row):
            continue
        local_user_id = _membership_local_user_id(row)
        direct_auth_user_id = _membership_direct_auth_user_id(row)
        app_user = users.get(local_user_id) if local_user_id else None
        auth_user_id = direct_auth_user_id or _app_user_auth_user_id(app_user)
        if not auth_user_id:
            issue = {
                "code": "membership_auth_user_id_missing",
                "message": "Active ProjectMembership has no canonical AppUser.auth_user_id.",
                "membershipId": _membership_id(row),
                "localUserId": local_user_id,
            }
            (errors if cfg.strict_identities else warnings).append(issue)
            continue
        role = _membership_role(row)
        if auth_user_id == owner_auth_user_id:
            role = ROLE_OWNER
        elif role == ROLE_OWNER:
            errors.append(
                {
                    "code": "non_owner_has_owner_role",
                    "message": "Only Project.auth_owner_user_id may receive the owner role.",
                    "membershipId": _membership_id(row),
                    "authUserId": auth_user_id,
                }
            )
            continue
        assignment = DesiredProjectAssignment(
            auth_user_id=auth_user_id,
            role=role,
            local_user_id=local_user_id,
            membership_id=_membership_id(row),
            starts_at=_iso(getattr(row, "starts_at", None)),
            expires_at=_iso(getattr(row, "expires_at", None)),
            permission_overrides=_membership_overrides(
                row,
                role=role,
                forward=cfg.forward_permission_overrides,
            ),
            source="membership",
        )
        existing = desired.get(auth_user_id)
        if existing is None:
            desired[auth_user_id] = assignment
            continue
        if existing.role == ROLE_OWNER:
            continue
        selected = assignment if ROLE_PRIORITY[assignment.role] > ROLE_PRIORITY[existing.role] else existing
        desired[auth_user_id] = selected
        warnings.append(
            {
                "code": "duplicate_project_membership_identity",
                "message": "Multiple memberships resolve to the same auth_user_id; highest role was selected.",
                "authUserId": auth_user_id,
                "selectedRole": selected.role,
            }
        )
    assignments = tuple(sorted(desired.values(), key=lambda item: (item.role != ROLE_OWNER, item.auth_user_id)))
    fingerprint = _hash_payload(
        {
            "contractVersion": SYNC_CONTRACT_VERSION,
            "appProjectPublicId": app_project_public_id,
            "chunkProjectId": chunk_project_id,
            "ownerAuthUserId": owner_auth_user_id,
            "assignments": _assignment_fingerprint_payload(assignments),
        }
    )
    return DesiredProjectAccessState(
        app_project_public_id=app_project_public_id,
        chunk_project_id=chunk_project_id,
        owner_auth_user_id=owner_auth_user_id,
        assignments=assignments,
        fingerprint=fingerprint,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# Remote payload normalization
# ---------------------------------------------------------------------------


def _result_payload(result: Any) -> Dict[str, Any]:
    try:
        payload = getattr(result, "payload", None)
        if isinstance(payload, Mapping):
            return dict(payload)
        data = _safe_mapping(result)
        if isinstance(data.get("payload"), Mapping):
            return _safe_mapping(data.get("payload"))
        return data
    except Exception:
        return {}


def _result_ok(result: Any) -> bool:
    try:
        if hasattr(result, "ok"):
            return bool(result.ok)
        return _safe_bool(_safe_mapping(result).get("ok"), False)
    except Exception:
        return False


def _result_status_code(result: Any) -> int:
    try:
        return _safe_int(
            getattr(result, "status_code", None)
            or _safe_mapping(result).get("statusCode")
            or _safe_mapping(result).get("status_code"),
            0,
        )
    except Exception:
        return 0


def _result_retryable(result: Any) -> bool:
    try:
        return _safe_bool(getattr(result, "retryable", None), False) or _safe_bool(
            _safe_mapping(result).get("retryable"), False
        )
    except Exception:
        return False


def _result_error(result: Any, fallback_code: str) -> Dict[str, Any]:
    try:
        error = _safe_mapping(getattr(result, "error", None))
        payload = _result_payload(result)
        error = error or _safe_mapping(payload.get("error"))
        return {
            "code": _safe_str(error.get("code") or payload.get("code"), fallback_code, 160),
            "message": _safe_str(
                error.get("message") or payload.get("message") or getattr(result, "message", None),
                fallback_code,
                2000,
            ),
            "statusCode": _result_status_code(result),
            "retryable": _result_retryable(result),
        }
    except Exception:
        return {"code": fallback_code, "message": fallback_code, "statusCode": 0, "retryable": False}


def _nested_candidates(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = [_safe_mapping(payload)]
    for key in ("data", "access", "projectAccess", "project_access", "bootstrap", "context", "result"):
        value = _safe_mapping(payload.get(key))
        if value:
            result.append(value)
            for nested_key in ("access", "projectAccess", "project_access", "data"):
                nested = _safe_mapping(value.get(nested_key))
                if nested:
                    result.append(nested)
    return result


def _extract_owner_user_id(payload: Mapping[str, Any], assignments: Sequence[Mapping[str, Any]] = ()) -> Optional[str]:
    for container in _nested_candidates(payload):
        for key in ("ownerUserId", "owner_user_id", "ownerId", "owner_id"):
            value = _canonical_auth_user_id(container.get(key), required=False)
            if value:
                return value
        project = _safe_mapping(container.get("project"))
        for key in ("ownerUserId", "owner_user_id", "ownerId", "owner_id"):
            value = _canonical_auth_user_id(project.get(key), required=False)
            if value:
                return value
        owner_assignment = _safe_mapping(container.get("ownerAssignment") or container.get("owner_assignment"))
        value = _canonical_auth_user_id(
            owner_assignment.get("subjectId") or owner_assignment.get("subject_id"),
            required=False,
        )
        if value:
            return value
    for assignment in assignments:
        normalized = _normalize_remote_assignment(assignment)
        if normalized.get("subjectType") == "user" and normalized.get("role") == ROLE_OWNER and normalized.get("active"):
            return _canonical_auth_user_id(normalized.get("subjectId"), required=False)
    return None


def _extract_authz_enforced(payload: Mapping[str, Any]) -> Optional[bool]:
    for container in _nested_candidates(payload):
        for key in ("authzEnforced", "authz_enforced", "authorizationEnforced"):
            if key in container:
                return _safe_bool(container.get(key), False)
    return None


def _extract_assignments(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    for container in _nested_candidates(payload):
        for key in ("assignments", "roleAssignments", "role_assignments", "items"):
            value = container.get(key)
            if isinstance(value, list):
                return [_safe_mapping(item) for item in value if isinstance(item, Mapping)]
    return []


def _normalize_remote_assignment(raw: Mapping[str, Any]) -> Dict[str, Any]:
    data = _safe_mapping(raw)
    subject = _safe_mapping(data.get("subject"))
    role_obj = _safe_mapping(data.get("role"))
    assignment_id = _safe_str(
        data.get("assignmentId")
        or data.get("assignment_id")
        or data.get("publicId")
        or data.get("public_id")
        or data.get("id"),
        "",
        240,
    ) or None
    subject_type = _safe_str(
        data.get("subjectType") or data.get("subject_type") or subject.get("type"),
        "",
        40,
    ).lower()
    subject_id = _safe_str(
        data.get("subjectId")
        or data.get("subject_id")
        or data.get("userId")
        or data.get("user_id")
        or subject.get("id"),
        "",
        160,
    ) or None
    role = _normalize_role(
        data.get("roleKey")
        or data.get("role_key")
        or data.get("roleName")
        or data.get("role_name")
        or role_obj.get("key")
        or role_obj.get("name")
        or (data.get("role") if isinstance(data.get("role"), str) else None),
        ROLE_VIEWER,
    )
    status = _safe_str(data.get("status"), "active", 40).lower()
    effective = _safe_bool(data.get("effective"), status in ACTIVE_ASSIGNMENT_STATUSES)
    expires_at = _parse_datetime(data.get("expiresAt") or data.get("expires_at"))
    active = status in ACTIVE_ASSIGNMENT_STATUSES and effective
    if expires_at is not None and expires_at <= _utcnow():
        active = False
    return {
        "assignmentId": assignment_id,
        "subjectType": subject_type,
        "subjectId": subject_id,
        "role": role,
        "status": status,
        "active": active,
        "effective": effective,
        "permissionOverrides": _safe_mapping(
            data.get("permissionOverrides") or data.get("permission_overrides")
        ),
        "raw": data,
    }


def _viewer_override_is_safe(assignment: Mapping[str, Any]) -> bool:
    overrides = _safe_mapping(assignment.get("permissionOverrides"))
    allow = {
        _safe_str(item, "", 120).lower()
        for item in _safe_list(overrides.get("allow") or overrides.get("allows"))
        if _safe_str(item, "", 120)
    }
    return not bool(allow.intersection(VIEWER_MUTATION_PERMISSIONS))


# ---------------------------------------------------------------------------
# Local status persistence
# ---------------------------------------------------------------------------


def _write_status_metadata(
    project: Any,
    *,
    status: str,
    request_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    result: Optional[ProjectChunkAccessSyncResult] = None,
    error: Optional[Mapping[str, Any]] = None,
) -> None:
    try:
        metadata = _project_metadata(project)
        chunk = _safe_mapping(metadata.get("chunk"))
        current = _safe_mapping(chunk.get("accessSync") or chunk.get("access_sync"))
        current.update(
            {
                "contractVersion": SYNC_CONTRACT_VERSION,
                "serviceVersion": SERVICE_VERSION,
                "status": status,
                "requestId": request_id or current.get("requestId"),
                "fingerprint": fingerprint or current.get("fingerprint"),
                "updatedAt": _iso(_utcnow()),
            }
        )
        if result is not None:
            result_payload = result.to_dict()
            current.update(
                {
                    "status": SYNC_STATUS_READY if result.ok else (
                        SYNC_STATUS_REPAIR_REQUIRED if result.repair_required else SYNC_STATUS_FAILED
                    ),
                    "syncedAt": _iso(_utcnow()) if result.ok else current.get("syncedAt"),
                    "desiredCount": result.desired_count,
                    "remoteOwnerBefore": result.remote_owner_before,
                    "remoteOwnerAfter": result.remote_owner_after,
                    "authzEnforced": result.authz_enforced,
                    "stats": _json_safe(result.stats),
                    "verification": _json_safe(result.verification),
                    "warnings": _json_safe(list(result.warnings)[:50]),
                    "errors": _json_safe(list(result.errors)[:50]),
                    "code": result.code,
                    "retryable": result.retryable,
                    "repairRequired": result.repair_required,
                    "durationMs": result.duration_ms,
                }
            )
        if error:
            current["error"] = _json_safe(error)
        elif result is not None and result.ok:
            current.pop("error", None)
        chunk["accessSync"] = current
        chunk["access_sync"] = copy.deepcopy(current)
        metadata["chunk"] = chunk
        _set_project_metadata(project, metadata)
    except Exception:
        pass


def _mark_started(project: Any, request_id: str, fingerprint: str) -> None:
    try:
        if hasattr(project, "mark_chunk_access_sync_started"):
            project.mark_chunk_access_sync_started(request_id=request_id)
        else:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = SYNC_STATUS_SYNCING
            if hasattr(project, "chunk_access_sync_request_id"):
                project.chunk_access_sync_request_id = request_id
            if hasattr(project, "chunk_access_sync_started_at"):
                project.chunk_access_sync_started_at = _utcnow()
            if hasattr(project, "chunk_access_sync_attempt_count"):
                project.chunk_access_sync_attempt_count = _safe_int(
                    getattr(project, "chunk_access_sync_attempt_count", 0), 0, minimum=0
                ) + 1
        _write_status_metadata(
            project,
            status=SYNC_STATUS_SYNCING,
            request_id=request_id,
            fingerprint=fingerprint,
        )
    except Exception:
        pass


def _mark_success(project: Any, result: ProjectChunkAccessSyncResult) -> None:
    try:
        if hasattr(project, "mark_chunk_access_synced"):
            project.mark_chunk_access_synced()
        else:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = SYNC_STATUS_READY
            if hasattr(project, "chunk_access_synced_at"):
                project.chunk_access_synced_at = _utcnow()
            if hasattr(project, "chunk_access_sync_error_code"):
                project.chunk_access_sync_error_code = None
            if hasattr(project, "chunk_access_sync_error_message"):
                project.chunk_access_sync_error_message = None
        _write_status_metadata(
            project,
            status=SYNC_STATUS_READY,
            request_id=result.request_id,
            fingerprint=result.fingerprint,
            result=result,
        )
    except Exception:
        pass


def _mark_failure(project: Any, result: ProjectChunkAccessSyncResult) -> None:
    error = result.errors[0] if result.errors else {"code": result.code, "message": result.message}
    try:
        if hasattr(project, "mark_chunk_access_sync_failed"):
            project.mark_chunk_access_sync_failed(
                code=error.get("code") or result.code,
                message=error.get("message") or result.message,
                repair_required=result.repair_required,
            )
        else:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = (
                    SYNC_STATUS_REPAIR_REQUIRED if result.repair_required else SYNC_STATUS_FAILED
                )
            if hasattr(project, "chunk_access_sync_error_code"):
                project.chunk_access_sync_error_code = _safe_str(error.get("code"), result.code, 160)
            if hasattr(project, "chunk_access_sync_error_message"):
                project.chunk_access_sync_error_message = _safe_str(error.get("message"), result.message, 2000)
        _write_status_metadata(
            project,
            status=SYNC_STATUS_REPAIR_REQUIRED if result.repair_required else SYNC_STATUS_FAILED,
            request_id=result.request_id,
            fingerprint=result.fingerprint,
            result=result,
            error=error,
        )
    except Exception:
        pass


def _mark_disabled(project: Any) -> None:
    try:
        if hasattr(project, "chunk_access_sync_status"):
            project.chunk_access_sync_status = SYNC_STATUS_DISABLED
        _write_status_metadata(project, status=SYNC_STATUS_DISABLED)
    except Exception:
        pass


def _persist(project: Any, *, commit: bool) -> None:
    _db_add(project)
    if commit:
        _db_commit()
    else:
        _db_flush()


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------


def _call_error(
    errors: List[Dict[str, Any]],
    result: Any,
    *,
    code: str,
    operation: str,
    subject_id: Optional[str] = None,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    error = _result_error(result, code)
    error["operation"] = operation
    if subject_id:
        error["authUserId"] = subject_id
    if role:
        error["role"] = role
    errors.append(error)
    return error


def _remote_assignment_map(assignments: Sequence[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for raw in assignments:
        item = _normalize_remote_assignment(raw)
        if item.get("subjectType") != "user" or not item.get("subjectId"):
            continue
        grouped[str(item["subjectId"])].append(item)
    return dict(grouped)


def _matching_active_assignment(
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    user_id: str,
    role: str,
) -> Optional[Dict[str, Any]]:
    for item in grouped.get(user_id, ()):
        if _safe_bool(item.get("active"), False) and item.get("role") == role:
            return _safe_mapping(item)
    return None


def _verify_remote_state(
    desired: DesiredProjectAccessState,
    *,
    owner_user_id: Optional[str],
    assignments: Sequence[Mapping[str, Any]],
    prune_stale: bool,
) -> Dict[str, Any]:
    normalized = [_normalize_remote_assignment(item) for item in assignments]
    grouped = _remote_assignment_map(normalized)
    errors: List[Dict[str, Any]] = []
    expected = desired.roles_by_user
    if owner_user_id and owner_user_id != desired.owner_auth_user_id:
        errors.append(
            {
                "code": "remote_owner_mismatch",
                "message": "Chunk project owner differs from App project owner.",
                "expected": desired.owner_auth_user_id,
                "actual": owner_user_id,
            }
        )
    for user_id, role in expected.items():
        active = [item for item in grouped.get(user_id, ()) if _safe_bool(item.get("active"), False)]
        matching = [item for item in active if item.get("role") == role]
        if not matching:
            errors.append(
                {
                    "code": "required_assignment_missing",
                    "message": "Required direct user assignment is missing.",
                    "authUserId": user_id,
                    "role": role,
                }
            )
        conflicting = [item for item in active if item.get("role") != role]
        if conflicting:
            errors.append(
                {
                    "code": "conflicting_assignment_active",
                    "message": "Conflicting direct user assignment remains active.",
                    "authUserId": user_id,
                    "expectedRole": role,
                    "actualRoles": sorted({str(item.get("role")) for item in conflicting}),
                }
            )
        if role == ROLE_VIEWER:
            for item in matching:
                if not _viewer_override_is_safe(item):
                    errors.append(
                        {
                            "code": "viewer_has_mutation_override",
                            "message": "Viewer assignment grants a mutating permission override.",
                            "authUserId": user_id,
                        }
                    )
    if prune_stale:
        for user_id, items in grouped.items():
            if user_id in expected:
                continue
            if any(_safe_bool(item.get("active"), False) for item in items):
                errors.append(
                    {
                        "code": "stale_assignment_remains_active",
                        "message": "A direct user assignment not present in vectoplan-app remains active.",
                        "authUserId": user_id,
                    }
                )
    return {
        "ok": not errors,
        "ownerMatches": not owner_user_id or owner_user_id == desired.owner_auth_user_id,
        "desiredCount": len(expected),
        "remoteDirectUserCount": len(grouped),
        "errors": errors,
    }


def _audit(
    writer: Optional[Callable[..., Any]],
    *,
    project: Any,
    action: str,
    actor_auth_user_id: Optional[str],
    payload: Mapping[str, Any],
) -> None:
    if not callable(writer):
        return
    try:
        writer(
            project=project,
            action=action,
            actor_auth_user_id=actor_auth_user_id,
            payload=_json_safe(payload),
            commit=False,
        )
    except TypeError:
        try:
            writer(project, action=action, payload=_json_safe(payload))
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main synchronization
# ---------------------------------------------------------------------------


def sync_project_chunk_access(
    project: Any,
    *,
    actor_auth_user_id: Optional[str] = None,
    force: bool = False,
    commit: bool = True,
    client: Optional[Any] = None,
    audit_writer: Optional[Callable[..., Any]] = None,
    raise_on_error: bool = False,
    request_id: Optional[str] = None,
) -> ProjectChunkAccessSyncResult:
    """Gleicht Owner und direkte App-Mitgliedschaften vollständig zu Chunk ab.

    ``commit=False`` ist bewusst ein reiner lokaler Deferred-Modus. Es wird in
    diesem Fall kein Remote-Request gesendet, weil ein noch nicht dauerhaft
    gespeicherter App-Zustand nicht als externe Wahrheit publiziert werden darf.
    """
    started = time.monotonic()
    cfg = ProjectChunkAccessSyncConfig.from_app()
    req_id = _request_id(request_id)
    app_project_public_id = _project_public_id(project)
    chunk_project_id = _chunk_project_id(project)
    actor = _canonical_auth_user_id(actor_auth_user_id, required=False)

    def finish_error(
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool,
        repair_required: bool,
        fingerprint: str = "",
        owner: str = "",
        warnings: Sequence[Mapping[str, Any]] = (),
        errors: Sequence[Mapping[str, Any]] = (),
        stats: Optional[Mapping[str, int]] = None,
        verification: Optional[Mapping[str, Any]] = None,
    ) -> ProjectChunkAccessSyncResult:
        error_items = list(errors) or [{"code": code, "message": message}]
        result = ProjectChunkAccessSyncResult(
            ok=False,
            code=code,
            status_code=status_code,
            app_project_public_id=app_project_public_id,
            chunk_project_id=chunk_project_id,
            owner_auth_user_id=owner,
            actor_auth_user_id=actor,
            request_id=req_id,
            fingerprint=fingerprint,
            desired_count=0,
            stats=dict(stats or {}),
            verification=_safe_mapping(verification),
            warnings=tuple(_safe_mapping(item) for item in warnings),
            errors=tuple(_safe_mapping(item) for item in error_items),
            duration_ms=_duration_ms(started),
            retryable=retryable,
            repair_required=repair_required,
        )
        try:
            _mark_failure(project, result)
            if commit:
                _persist(project, commit=True)
        except Exception:
            _db_rollback()
        _audit(
            audit_writer,
            project=project,
            action="chunk_access_sync_failed",
            actor_auth_user_id=actor,
            payload=result.to_dict(),
        )
        if raise_on_error:
            raise ProjectChunkAccessSyncError(
                code,
                message,
                status_code=status_code,
                retryable=retryable,
                repair_required=repair_required,
                result=result,
            )
        return result

    if project is None:
        return finish_error(
            "project_required",
            "project is required",
            status_code=400,
            retryable=False,
            repair_required=False,
        )
    if _project_is_demo(project):
        return ProjectChunkAccessSyncResult(
            ok=True,
            code="chunk_access_sync_not_applicable_demo",
            status_code=200,
            app_project_public_id=app_project_public_id,
            chunk_project_id=chunk_project_id,
            actor_auth_user_id=actor,
            request_id=req_id,
            stats={"skipped": 1},
            duration_ms=_duration_ms(started),
        )
    if _project_deleted(project):
        return finish_error(
            "project_not_active",
            "Deleted or expired project cannot be synchronized.",
            status_code=409,
            retryable=False,
            repair_required=False,
        )
    if not cfg.enabled:
        try:
            _mark_disabled(project)
            if commit:
                _persist(project, commit=True)
        except Exception:
            _db_rollback()
        return ProjectChunkAccessSyncResult(
            ok=True,
            code="chunk_access_sync_disabled",
            status_code=200,
            app_project_public_id=app_project_public_id,
            chunk_project_id=chunk_project_id,
            actor_auth_user_id=actor,
            request_id=req_id,
            stats={"disabled": 1},
            duration_ms=_duration_ms(started),
        )
    if not commit:
        try:
            if hasattr(project, "chunk_access_sync_status"):
                project.chunk_access_sync_status = SYNC_STATUS_PENDING
            _write_status_metadata(project, status=SYNC_STATUS_PENDING, request_id=req_id)
            _persist(project, commit=False)
        except Exception:
            pass
        result = ProjectChunkAccessSyncResult(
            ok=False,
            code="chunk_access_sync_deferred_until_commit",
            status_code=409,
            app_project_public_id=app_project_public_id,
            chunk_project_id=chunk_project_id,
            actor_auth_user_id=actor,
            request_id=req_id,
            stats={"deferred": 1},
            duration_ms=_duration_ms(started),
            retryable=True,
        )
        if raise_on_error:
            raise ProjectChunkAccessSyncError(
                result.code,
                "Chunk access synchronization is deferred until the App transaction is committed.",
                status_code=409,
                retryable=True,
                result=result,
            )
        return result
    if not chunk_project_id:
        return finish_error(
            "chunk_project_not_ready",
            "Project has no chunk_project_id.",
            status_code=409,
            retryable=True,
            repair_required=False,
        )

    try:
        desired = build_desired_project_chunk_access(project, config=cfg)
    except ProjectChunkAccessSyncError as exc:
        return finish_error(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            retryable=exc.retryable,
            repair_required=exc.repair_required,
        )
    except Exception as exc:
        return finish_error(
            "desired_access_state_failed",
            str(exc),
            status_code=500,
            retryable=True,
            repair_required=True,
        )

    if desired.errors:
        return finish_error(
            "local_project_access_invariant_failed",
            "Local project memberships cannot be safely synchronized.",
            status_code=409,
            retryable=False,
            repair_required=True,
            fingerprint=desired.fingerprint,
            owner=desired.owner_auth_user_id,
            warnings=desired.warnings,
            errors=desired.errors,
        )

    cache_key = _cache_key(chunk_project_id, desired.fingerprint)
    if not force:
        cached = _cache_get(cache_key)
        if cached is not None:
            try:
                _mark_success(project, cached)
                _persist(project, commit=True)
            except Exception:
                _db_rollback()
            return replace(cached, duration_ms=_duration_ms(started), cache_hit=True)

    lock = _project_lock(chunk_project_id)
    acquired = lock.acquire(timeout=cfg.lock_timeout_seconds)
    if not acquired:
        return finish_error(
            "chunk_access_sync_lock_timeout",
            "Another access synchronization is still running for this project.",
            status_code=409,
            retryable=True,
            repair_required=False,
            fingerprint=desired.fingerprint,
            owner=desired.owner_auth_user_id,
            warnings=desired.warnings,
        )

    try:
        if not force:
            cached = _cache_get(cache_key)
            if cached is not None:
                try:
                    _mark_success(project, cached)
                    _persist(project, commit=True)
                except Exception:
                    _db_rollback()
                return replace(cached, duration_ms=_duration_ms(started), cache_hit=True)

        try:
            _mark_started(project, req_id, desired.fingerprint)
            _persist(project, commit=True)
        except Exception as exc:
            _db_rollback()
            return finish_error(
                "chunk_access_sync_start_persist_failed",
                f"Sync start state could not be persisted: {exc}",
                status_code=500,
                retryable=True,
                repair_required=False,
                fingerprint=desired.fingerprint,
                owner=desired.owner_auth_user_id,
                warnings=desired.warnings,
            )

        try:
            active_client = client or get_chunk_client()
        except Exception as exc:
            return finish_error(
                "chunk_client_unavailable",
                str(exc),
                status_code=503,
                retryable=True,
                repair_required=False,
                fingerprint=desired.fingerprint,
                owner=desired.owner_auth_user_id,
                warnings=desired.warnings,
            )

        stats: Dict[str, int] = {
            "desired": len(desired.assignments),
            "initialized": 0,
            "ownerTransferred": 0,
            "createdOrUpdated": 0,
            "reused": 0,
            "revoked": 0,
            "skippedGroups": 0,
            "failed": 0,
        }
        warnings: List[Dict[str, Any]] = [dict(item) for item in desired.warnings]
        errors: List[Dict[str, Any]] = []

        access_result = active_client.get_project_access(
            chunk_project_id,
            include_inactive=True,
            refresh=True,
            raise_on_error=False,
        )
        if not _result_ok(access_result):
            _call_error(
                errors,
                access_result,
                code="chunk_access_read_failed",
                operation="get_project_access",
            )
            stats["failed"] += 1
            return finish_error(
                "chunk_access_read_failed",
                errors[-1]["message"],
                status_code=_result_status_code(access_result) or 502,
                retryable=_result_retryable(access_result),
                repair_required=False,
                fingerprint=desired.fingerprint,
                owner=desired.owner_auth_user_id,
                warnings=warnings,
                errors=errors,
                stats=stats,
            )
        assignments_result = active_client.list_project_assignments(
            chunk_project_id,
            include_inactive=True,
            refresh=True,
            raise_on_error=False,
        )
        if not _result_ok(assignments_result):
            _call_error(
                errors,
                assignments_result,
                code="chunk_assignments_read_failed",
                operation="list_project_assignments",
            )
            stats["failed"] += 1
            return finish_error(
                "chunk_assignments_read_failed",
                errors[-1]["message"],
                status_code=_result_status_code(assignments_result) or 502,
                retryable=_result_retryable(assignments_result),
                repair_required=False,
                fingerprint=desired.fingerprint,
                owner=desired.owner_auth_user_id,
                warnings=warnings,
                errors=errors,
                stats=stats,
            )

        access_payload = _result_payload(access_result)
        remote_assignments = _extract_assignments(_result_payload(assignments_result))
        remote_owner_before = _extract_owner_user_id(access_payload, remote_assignments)
        remote_owner_after = remote_owner_before
        authz_enforced = _extract_authz_enforced(access_payload)

        if remote_owner_before and remote_owner_before != desired.owner_auth_user_id:
            transfer_result = active_client.transfer_project_owner(
                chunk_project_id,
                current_owner_user_id=remote_owner_before,
                new_owner_user_id=desired.owner_auth_user_id,
                transferred_by_user_id=actor or remote_owner_before,
                reason="vectoplan-app-owner-reconciliation",
                request_id=req_id,
                idempotency_key=_hash_payload(
                    {
                        "operation": "owner-transfer",
                        "chunkProjectId": chunk_project_id,
                        "currentOwner": remote_owner_before,
                        "newOwner": desired.owner_auth_user_id,
                    }
                ),
                raise_on_error=False,
            )
            if not _result_ok(transfer_result):
                _call_error(
                    errors,
                    transfer_result,
                    code="chunk_owner_transfer_failed",
                    operation="transfer_project_owner",
                    subject_id=desired.owner_auth_user_id,
                    role=ROLE_OWNER,
                )
                stats["failed"] += 1
                return finish_error(
                    "chunk_owner_transfer_failed",
                    errors[-1]["message"],
                    status_code=_result_status_code(transfer_result) or 502,
                    retryable=_result_retryable(transfer_result),
                    repair_required=True,
                    fingerprint=desired.fingerprint,
                    owner=desired.owner_auth_user_id,
                    warnings=warnings,
                    errors=errors,
                    stats=stats,
                )
            remote_owner_after = desired.owner_auth_user_id
            stats["ownerTransferred"] += 1

        initialize_result = active_client.initialize_project_access(
            chunk_project_id,
            owner_user_id=desired.owner_auth_user_id,
            request_id=req_id,
            idempotency_key=_hash_payload(
                {
                    "operation": "access-initialize",
                    "chunkProjectId": chunk_project_id,
                    "owner": desired.owner_auth_user_id,
                }
            ),
            raise_on_error=False,
        )
        if not _result_ok(initialize_result):
            _call_error(
                errors,
                initialize_result,
                code="chunk_access_initialize_failed",
                operation="initialize_project_access",
                subject_id=desired.owner_auth_user_id,
                role=ROLE_OWNER,
            )
            stats["failed"] += 1
            return finish_error(
                "chunk_access_initialize_failed",
                errors[-1]["message"],
                status_code=_result_status_code(initialize_result) or 502,
                retryable=_result_retryable(initialize_result),
                repair_required=bool(remote_owner_before and remote_owner_before != desired.owner_auth_user_id),
                fingerprint=desired.fingerprint,
                owner=desired.owner_auth_user_id,
                warnings=warnings,
                errors=errors,
                stats=stats,
            )
        stats["initialized"] += 1
        remote_owner_after = desired.owner_auth_user_id

        grouped_before = _remote_assignment_map(remote_assignments)
        for assignment in desired.assignments:
            if assignment.role == ROLE_OWNER:
                continue
            matching = _matching_active_assignment(
                grouped_before,
                assignment.auth_user_id,
                assignment.role,
            )
            if matching is not None and not force:
                stats["reused"] += 1
                continue
            upsert_result = active_client.upsert_project_user_assignment(
                chunk_project_id,
                user_id=assignment.auth_user_id,
                role=assignment.role,
                assigned_by_user_id=actor or desired.owner_auth_user_id,
                starts_at=assignment.starts_at,
                expires_at=assignment.expires_at,
                permission_overrides=assignment.permission_overrides,
                request_id=req_id,
                idempotency_key=_hash_payload(
                    {
                        "operation": "assignment-upsert",
                        "chunkProjectId": chunk_project_id,
                        "authUserId": assignment.auth_user_id,
                        "role": assignment.role,
                        "fingerprint": desired.fingerprint,
                    }
                ),
                raise_on_error=False,
            )
            if _result_ok(upsert_result):
                stats["createdOrUpdated"] += 1
            else:
                _call_error(
                    errors,
                    upsert_result,
                    code="chunk_assignment_upsert_failed",
                    operation="upsert_project_user_assignment",
                    subject_id=assignment.auth_user_id,
                    role=assignment.role,
                )
                stats["failed"] += 1

        post_upsert_result = active_client.list_project_assignments(
            chunk_project_id,
            include_inactive=True,
            refresh=True,
            raise_on_error=False,
        )
        if not _result_ok(post_upsert_result):
            _call_error(
                errors,
                post_upsert_result,
                code="chunk_assignments_refresh_failed",
                operation="list_project_assignments_after_upsert",
            )
            stats["failed"] += 1
        post_assignments = (
            _extract_assignments(_result_payload(post_upsert_result))
            if _result_ok(post_upsert_result)
            else remote_assignments
        )

        if cfg.prune_stale_direct_assignments:
            desired_roles = desired.roles_by_user
            grouped_post = _remote_assignment_map(post_assignments)
            for user_id, items in grouped_post.items():
                active_items = [item for item in items if _safe_bool(item.get("active"), False)]
                expected_role = desired_roles.get(user_id)
                kept = False
                for item in active_items:
                    role = _safe_str(item.get("role"), ROLE_VIEWER, 40)
                    keep = bool(expected_role and role == expected_role and not kept)
                    if keep:
                        kept = True
                        continue
                    assignment_id = _safe_str(item.get("assignmentId"), "", 240)
                    if not assignment_id:
                        errors.append(
                            {
                                "code": "chunk_assignment_id_missing",
                                "message": "Stale/conflicting assignment cannot be revoked without assignment id.",
                                "authUserId": user_id,
                                "role": role,
                            }
                        )
                        stats["failed"] += 1
                        continue
                    revoke_result = active_client.revoke_project_assignment(
                        chunk_project_id,
                        assignment_id,
                        revoked_by_user_id=actor or desired.owner_auth_user_id,
                        reason=(
                            "membership_removed"
                            if expected_role is None
                            else "role_replaced_or_duplicate_assignment"
                        ),
                        request_id=req_id,
                        idempotency_key=_hash_payload(
                            {
                                "operation": "assignment-revoke",
                                "chunkProjectId": chunk_project_id,
                                "assignmentId": assignment_id,
                                "fingerprint": desired.fingerprint,
                            }
                        ),
                        raise_on_error=False,
                    )
                    if _result_ok(revoke_result):
                        stats["revoked"] += 1
                    else:
                        _call_error(
                            errors,
                            revoke_result,
                            code="chunk_assignment_revoke_failed",
                            operation="revoke_project_assignment",
                            subject_id=user_id,
                            role=role,
                        )
                        stats["failed"] += 1

        final_access_result = active_client.get_project_access(
            chunk_project_id,
            include_inactive=True,
            refresh=True,
            raise_on_error=False,
        )
        final_assignments_result = active_client.list_project_assignments(
            chunk_project_id,
            include_inactive=True,
            refresh=True,
            raise_on_error=False,
        )
        if not _result_ok(final_access_result):
            _call_error(
                errors,
                final_access_result,
                code="chunk_access_verification_read_failed",
                operation="get_project_access_verify",
            )
            stats["failed"] += 1
        if not _result_ok(final_assignments_result):
            _call_error(
                errors,
                final_assignments_result,
                code="chunk_assignments_verification_read_failed",
                operation="list_project_assignments_verify",
            )
            stats["failed"] += 1

        final_access_payload = (
            _result_payload(final_access_result)
            if _result_ok(final_access_result)
            else access_payload
        )
        final_assignments = (
            _extract_assignments(_result_payload(final_assignments_result))
            if _result_ok(final_assignments_result)
            else post_assignments
        )
        remote_owner_after = _extract_owner_user_id(final_access_payload, final_assignments) or remote_owner_after
        final_authz = _extract_authz_enforced(final_access_payload)
        if final_authz is not None:
            authz_enforced = final_authz
        verification = _verify_remote_state(
            desired,
            owner_user_id=remote_owner_after,
            assignments=final_assignments,
            prune_stale=cfg.prune_stale_direct_assignments,
        )
        if not cfg.verify_after_sync:
            verification = {
                "ok": not errors,
                "skipped": True,
                "reason": "disabled_by_config",
            }
        elif not verification.get("ok"):
            for item in _safe_list(verification.get("errors")):
                errors.append(_safe_mapping(item))
            stats["failed"] += len(_safe_list(verification.get("errors")))

        partial_failure = bool(errors)
        success = not partial_failure or not cfg.fail_on_partial_error
        repair_required = bool(partial_failure)
        if success:
            code = "chunk_access_synced" if not partial_failure else "chunk_access_synced_with_warnings"
            status_code = 200
        else:
            code = "chunk_access_sync_partial_failure"
            status_code = 502

        result = ProjectChunkAccessSyncResult(
            ok=success,
            code=code,
            status_code=status_code,
            app_project_public_id=desired.app_project_public_id,
            chunk_project_id=desired.chunk_project_id,
            owner_auth_user_id=desired.owner_auth_user_id,
            actor_auth_user_id=actor,
            request_id=req_id,
            fingerprint=desired.fingerprint,
            desired_count=len(desired.assignments),
            remote_owner_before=remote_owner_before,
            remote_owner_after=remote_owner_after,
            authz_enforced=authz_enforced,
            stats=stats,
            verification=verification,
            warnings=tuple(warnings),
            errors=tuple(errors),
            duration_ms=_duration_ms(started),
            retryable=any(_safe_bool(item.get("retryable"), False) for item in errors),
            repair_required=repair_required,
        )

        try:
            if result.ok:
                _mark_success(project, result)
            else:
                _mark_failure(project, result)
            _persist(project, commit=True)
        except Exception as exc:
            _db_rollback()
            persistence_error = {
                "code": "chunk_access_sync_local_persistence_failed",
                "message": f"Remote access may be synchronized, but local status persistence failed: {exc}",
                "retryable": True,
            }
            failed = replace(
                result,
                ok=False,
                code="chunk_access_sync_local_persistence_failed",
                status_code=500,
                errors=tuple(list(result.errors) + [persistence_error]),
                retryable=True,
                repair_required=True,
                duration_ms=_duration_ms(started),
            )
            try:
                _mark_failure(project, failed)
                _persist(project, commit=True)
            except Exception:
                _db_rollback()
            if raise_on_error:
                raise ProjectChunkAccessSyncError(
                    failed.code,
                    persistence_error["message"],
                    status_code=500,
                    retryable=True,
                    repair_required=True,
                    result=failed,
                )
            return failed

        _audit(
            audit_writer,
            project=project,
            action="chunk_access_synced" if result.ok else "chunk_access_sync_failed",
            actor_auth_user_id=actor,
            payload=result.to_dict(),
        )
        if result.ok:
            _cache_set(
                cache_key,
                result,
                ttl_seconds=cfg.cache_ttl_seconds,
                max_entries=cfg.cache_max_entries,
            )
        if raise_on_error and not result.ok:
            raise ProjectChunkAccessSyncError(
                result.code,
                result.message,
                status_code=result.status_code,
                retryable=result.retryable,
                repair_required=result.repair_required,
                result=result,
            )
        return result
    finally:
        try:
            lock.release()
        except Exception:
            pass


def retry_project_chunk_access_sync(
    project: Any,
    *,
    actor_auth_user_id: Optional[str] = None,
    commit: bool = True,
    client: Optional[Any] = None,
    audit_writer: Optional[Callable[..., Any]] = None,
    raise_on_error: bool = False,
) -> ProjectChunkAccessSyncResult:
    return sync_project_chunk_access(
        project,
        actor_auth_user_id=actor_auth_user_id,
        force=True,
        commit=commit,
        client=client,
        audit_writer=audit_writer,
        raise_on_error=raise_on_error,
    )


# ---------------------------------------------------------------------------
# Status and diagnostics
# ---------------------------------------------------------------------------


def serialize_project_chunk_access_sync_status(project: Any) -> Dict[str, Any]:
    try:
        cfg = ProjectChunkAccessSyncConfig.from_app()
        explicit = _safe_mapping(getattr(project, "chunk_access_sync", None))
        metadata = _project_metadata(project)
        metadata_status = _safe_mapping(
            _safe_mapping(metadata.get("chunk")).get("accessSync")
            or _safe_mapping(metadata.get("chunk")).get("access_sync")
        )
        status = _safe_str(
            explicit.get("status")
            or getattr(project, "chunk_access_sync_status", None)
            or metadata_status.get("status"),
            SYNC_STATUS_PENDING,
            80,
        )
        return {
            "contractVersion": SYNC_CONTRACT_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "enabled": cfg.enabled,
            "required": cfg.required,
            "status": status,
            "ready": status == SYNC_STATUS_READY,
            "repairRequired": status == SYNC_STATUS_REPAIR_REQUIRED,
            "appProjectPublicId": _project_public_id(project),
            "chunkProjectId": _chunk_project_id(project) or None,
            "ownerAuthUserId": _canonical_auth_user_id(
                getattr(project, "auth_owner_user_id", None), required=False
            ),
            "attemptCount": _safe_int(
                explicit.get("attemptCount")
                or explicit.get("attempt_count")
                or getattr(project, "chunk_access_sync_attempt_count", None),
                0,
                minimum=0,
            ),
            "startedAt": explicit.get("startedAt")
            or explicit.get("started_at")
            or _iso(getattr(project, "chunk_access_sync_started_at", None)),
            "syncedAt": explicit.get("syncedAt")
            or explicit.get("synced_at")
            or _iso(getattr(project, "chunk_access_synced_at", None)),
            "requestId": explicit.get("requestId")
            or explicit.get("request_id")
            or getattr(project, "chunk_access_sync_request_id", None),
            "errorCode": explicit.get("errorCode")
            or explicit.get("error_code")
            or getattr(project, "chunk_access_sync_error_code", None),
            "errorMessage": explicit.get("errorMessage")
            or explicit.get("error_message")
            or getattr(project, "chunk_access_sync_error_message", None),
            "fingerprint": metadata_status.get("fingerprint"),
            "desiredCount": metadata_status.get("desiredCount"),
            "authzEnforced": metadata_status.get("authzEnforced"),
            "stats": _safe_mapping(metadata_status.get("stats")),
            "verification": _safe_mapping(metadata_status.get("verification")),
            "retryable": _safe_bool(metadata_status.get("retryable"), False),
        }
    except Exception as exc:
        return {
            "contractVersion": SYNC_CONTRACT_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "enabled": False,
            "status": SYNC_STATUS_FAILED,
            "ready": False,
            "errorCode": "chunk_access_sync_status_failed",
            "errorMessage": _safe_str(exc, "status failed", 2000),
        }


def get_project_chunk_access_sync_service_status() -> Dict[str, Any]:
    cfg = ProjectChunkAccessSyncConfig.from_app()
    with _CACHE_LOCK:
        cache_entries = len(_CACHE)
    with _LOCKS_GUARD:
        project_locks = len(_PROJECT_LOCKS)
    return {
        "ok": True,
        "service": "project_chunk_access_sync_service",
        "serviceVersion": SERVICE_VERSION,
        "contractVersion": SYNC_CONTRACT_VERSION,
        "remoteContractVersion": REMOTE_ACCESS_CONTRACT_VERSION,
        "config": cfg.public_status(),
        "dependencies": {
            "dbAvailable": db is not None,
            "appUserModelAvailable": AppUser is not None,
            "projectMembershipModelAvailable": ProjectMembership is not None,
            "chunkClientAvailable": ChunkClient is not None,
        },
        "cacheEntries": cache_entries,
        "projectLocks": project_locks,
        "roles": {
            "owner": {"view": True, "edit": True, "manage": True, "transfer": True},
            "admin": {"view": True, "edit": True, "manage": True, "transfer": False},
            "editor": {"view": True, "edit": True, "manage": False, "transfer": False},
            "viewer": {"view": True, "edit": False, "manage": False, "transfer": False},
        },
        "viewerReadOnly": True,
        "prunesDirectUserAssignmentsOnly": True,
        "groupAssignmentsUntouched": True,
        "externalUserForeignKeys": False,
    }


__all__ = [
    "SERVICE_VERSION",
    "SYNC_CONTRACT_VERSION",
    "REMOTE_ACCESS_CONTRACT_VERSION",
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "ROLE_VIEWER",
    "SUPPORTED_ROLES",
    "SYNC_STATUS_PENDING",
    "SYNC_STATUS_SYNCING",
    "SYNC_STATUS_READY",
    "SYNC_STATUS_FAILED",
    "SYNC_STATUS_REPAIR_REQUIRED",
    "SYNC_STATUS_DISABLED",
    "ProjectChunkAccessSyncConfig",
    "DesiredProjectAssignment",
    "DesiredProjectAccessState",
    "ProjectChunkAccessSyncResult",
    "ProjectChunkAccessSyncError",
    "ProjectChunkAccessContractError",
    "build_desired_project_chunk_access",
    "sync_project_chunk_access",
    "retry_project_chunk_access_sync",
    "serialize_project_chunk_access_sync_status",
    "clear_project_chunk_access_sync_cache",
    "get_project_chunk_access_sync_service_status",
]
