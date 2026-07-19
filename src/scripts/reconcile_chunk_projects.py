# services/vectoplan-app/src/scripts/reconcile_chunk_projects.py
"""Reconcile app projects with ``vectoplan-chunk``.

This operator script repairs the two projections owned by ``vectoplan-app``:

1. the idempotent Chunk project / universe / world provisioning graph; and
2. the direct project-owner and project-membership access projection in Chunk.

Design rules
------------
* ``vectoplan-app`` remains the source of truth for projects and memberships.
* ``vectoplan-auth`` user IDs are the only user IDs sent to Chunk.
* Local ``AppUser.id`` values are never sent to Chunk and are not printed in the
  default report.
* A project is committed in the app database before any remote mutation.
* Provisioning and access synchronization are independent, retryable phases.
* Earth is requested by the provisioning service; Flat fallback remains limited
  to the business/precondition errors allowed by that service.
* The script is safe to rerun. Remote writes carry stable idempotency contracts
  inside the called services.
* ``--dry-run`` performs no database commit and no remote mutation.
* A process lock prevents two local copies from running concurrently by default.

Typical usage::

    python src/scripts/reconcile_chunk_projects.py --dry-run
    python src/scripts/reconcile_chunk_projects.py --project prj_123 --force
    python src/scripts/reconcile_chunk_projects.py --only access --status failed
    python src/scripts/reconcile_chunk_projects.py --json-lines --output-json report.json

The Flask application factory may be selected explicitly::

    VECTOPLAN_APP_FACTORY=app:create_app \
      python src/scripts/reconcile_chunk_projects.py

For unusual factories, JSON keyword arguments may be supplied with
``VECTOPLAN_APP_FACTORY_KWARGS``.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import datetime as _dt
import enum
import hashlib
import importlib
import json
import logging
import os
import pathlib
import signal
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple


LOGGER = logging.getLogger("vectoplan.reconcile_chunk_projects")

SCRIPT_VERSION = "1.0.0"
REPORT_VERSION = "vectoplan-app-chunk-reconcile-report.v1"
DEFAULT_BATCH_SIZE = 100
DEFAULT_LOCK_FILE = "/tmp/vectoplan-app-reconcile-chunk-projects.lock"
DEFAULT_REQUEST_PREFIX = "vp-reconcile"

PROVISIONING_READY_STATUSES = frozenset({"ready", "fallback_ready"})
PROVISIONING_RETRY_STATUSES = frozenset(
    {"pending", "provisioning", "failed", "repair_required", "error", "unknown", ""}
)
PROVISIONING_DISABLED_STATUSES = frozenset({"disabled", "off"})
ACCESS_READY_STATUSES = frozenset({"ready", "synced"})
ACCESS_RETRY_STATUSES = frozenset(
    {"pending", "syncing", "failed", "repair_required", "error", "unknown", ""}
)
ACCESS_DISABLED_STATUSES = frozenset({"disabled", "off"})
TERMINAL_PROJECT_STATUSES = frozenset({"deleted", "expired"})
ARCHIVED_PROJECT_STATUSES = frozenset({"archived"})

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "token",
    "api_key",
    "apikey",
    "auth_user_id",
    "authuserid",
    "owner_user_id",
    "owneruserid",
    "local_user_id",
    "localuserid",
    "email",
)


class ExitCode(enum.IntEnum):
    OK = 0
    PARTIAL_FAILURE = 3
    INTERRUPTED = 4
    BOOTSTRAP_ERROR = 10
    LOCKED = 11
    INVALID_ARGUMENTS = 12


class ReconcileScope(str, enum.Enum):
    BOTH = "both"
    PROVISIONING = "provisioning"
    ACCESS = "access"


class ProjectOutcome(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    INTERRUPTED = "interrupted"


class ReconcileBootstrapError(RuntimeError):
    pass


class ReconcileLockError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Safe helpers
# ---------------------------------------------------------------------------


def _utcnow() -> _dt.datetime:
    try:
        return _dt.datetime.now(_dt.timezone.utc)
    except Exception:  # pragma: no cover
        return _dt.datetime.utcnow()


def _iso(value: Any = None) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    try:
        text = str(default if value is None else value).strip()
        if not text:
            text = str(default or "").strip()
        if max_len > 0 and len(text) > max_len:
            return text[:max_len]
        return text
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
            parsed = int(default)
        else:
            parsed = int(str(value).strip())
    except Exception:
        parsed = int(default)
    if minimum is not None:
        parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def _safe_float(
    value: Any,
    default: float = 0.0,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if minimum is not None:
        parsed = max(float(minimum), parsed)
    if maximum is not None:
        parsed = min(float(maximum), parsed)
    return parsed


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "active", "ok", "ja"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "inactive", "nein", ""}:
            return False
        return default
    except Exception:
        return default


def _safe_mapping(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if dataclasses.is_dataclass(value):
            return dict(dataclasses.asdict(value))
        if hasattr(value, "to_dict") and callable(value.to_dict):
            result = value.to_dict()
            return dict(result) if isinstance(result, Mapping) else {}
        return {}
    except Exception:
        return {}


def _safe_sequence(value: Any) -> List[Any]:
    try:
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, (tuple, set, frozenset)):
            return list(value)
        return []
    except Exception:
        return []


def _key_is_sensitive(key: Any) -> bool:
    text = _safe_str(key, "", 240).lower().replace("-", "_")
    return any(part in text for part in SENSITIVE_KEY_PARTS)


def _json_safe(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    if depth > max_depth:
        return "<max-depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = _safe_str(key, "", 240)
            if not key_text:
                continue
            if _key_is_sensitive(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _json_safe(item, depth=depth + 1, max_depth=max_depth)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1, max_depth=max_depth) for item in value]
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value), depth=depth + 1, max_depth=max_depth)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict(), depth=depth + 1, max_depth=max_depth)
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    try:
        return str(value)
    except Exception:
        return repr(value)


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _normalize_status(value: Any, default: str = "unknown") -> str:
    text = _safe_str(value, default, 100).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ok": "ready",
        "synced": "ready",
        "provisioned": "ready",
        "fallback": "fallback_ready",
        "failure": "failed",
        "error": "failed",
        "repair": "repair_required",
        "inconsistent": "repair_required",
        "running": "syncing",
        "in_progress": "syncing",
        "off": "disabled",
    }
    return aliases.get(text, text or default)


def _csv_set(values: Optional[Sequence[str]]) -> Set[str]:
    result: Set[str] = set()
    for raw in values or []:
        for item in _safe_str(raw, "", 4000).replace(";", ",").split(","):
            normalized = _normalize_status(item, "")
            if normalized:
                result.add(normalized)
    return result


def _result_dict(value: Any) -> Dict[str, Any]:
    try:
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return _safe_mapping(value.to_dict())
        return _safe_mapping(value)
    except Exception:
        return {}


def _result_ok(value: Any) -> bool:
    try:
        if hasattr(value, "ok"):
            return bool(getattr(value, "ok"))
        payload = _result_dict(value)
        return _safe_bool(payload.get("ok"), False)
    except Exception:
        return False


def _result_code(value: Any, default: str) -> str:
    try:
        if hasattr(value, "code"):
            return _safe_str(getattr(value, "code"), default, 160)
        return _safe_str(_result_dict(value).get("code"), default, 160)
    except Exception:
        return default


def _result_status_code(value: Any, default: int = 500) -> int:
    try:
        if hasattr(value, "status_code"):
            return _safe_int(getattr(value, "status_code"), default, minimum=0, maximum=599)
        payload = _result_dict(value)
        return _safe_int(payload.get("statusCode") or payload.get("status_code"), default, minimum=0, maximum=599)
    except Exception:
        return default


def _atomic_write_json(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_json_safe(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        with contextlib.suppress(Exception):
            os.unlink(temp_name)
        raise


# ---------------------------------------------------------------------------
# Bootstrap and runtime contracts
# ---------------------------------------------------------------------------


def _add_project_paths() -> pathlib.Path:
    script_path = pathlib.Path(__file__).resolve()
    try:
        app_root = script_path.parents[2]
    except Exception:
        app_root = pathlib.Path.cwd().resolve()
    candidates = [app_root, app_root / "src", pathlib.Path.cwd().resolve()]
    for candidate in candidates:
        text = str(candidate)
        if candidate.exists() and text not in sys.path:
            sys.path.insert(0, text)
    return app_root


def _parse_json_object(raw: Any, *, name: str) -> Dict[str, Any]:
    text = _safe_str(raw, "", 20000)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise ReconcileBootstrapError(f"{name} must contain a JSON object: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ReconcileBootstrapError(f"{name} must contain a JSON object.")
    return dict(parsed)


def _split_factory_spec(spec: str) -> Tuple[str, str]:
    text = _safe_str(spec, "", 500)
    if not text:
        raise ReconcileBootstrapError("Empty Flask app factory specification.")
    if ":" in text:
        module_name, attribute = text.split(":", 1)
    else:
        module_name, attribute = text, "app"
    module_name = module_name.strip()
    attribute = attribute.strip() or "app"
    if attribute.endswith("()"):
        attribute = attribute[:-2].strip() or "create_app"
    if not module_name:
        raise ReconcileBootstrapError(f"Invalid Flask app factory specification: {spec!r}")
    return module_name, attribute


def _load_app_from_spec(spec: str, kwargs: Mapping[str, Any]) -> Any:
    module_name, attribute = _split_factory_spec(spec)
    module = importlib.import_module(module_name)
    target = getattr(module, attribute)
    if hasattr(target, "app_context") and hasattr(target, "config"):
        return target
    if callable(target):
        try:
            app = target(**dict(kwargs))
        except TypeError:
            config_name = _safe_str(
                os.environ.get("VECTOPLAN_APP_CONFIG") or os.environ.get("FLASK_ENV"),
                "",
                160,
            )
            if kwargs or not config_name:
                raise
            app = target(config_name)
        if hasattr(app, "app_context") and hasattr(app, "config"):
            return app
        raise ReconcileBootstrapError(f"Factory {spec!r} did not return a Flask application.")
    raise ReconcileBootstrapError(f"Object {spec!r} is neither a Flask app nor a callable factory.")


def discover_flask_app(explicit_spec: Optional[str] = None) -> Any:
    _add_project_paths()
    kwargs = _parse_json_object(
        os.environ.get("VECTOPLAN_APP_FACTORY_KWARGS", ""),
        name="VECTOPLAN_APP_FACTORY_KWARGS",
    )
    candidates: List[str] = []
    for candidate in (
        explicit_spec,
        os.environ.get("VECTOPLAN_APP_FACTORY"),
        os.environ.get("FLASK_APP"),
        "app:create_app",
        "app:app",
        "wsgi:app",
        "main:create_app",
        "main:app",
    ):
        text = _safe_str(candidate, "", 500)
        if text and text not in candidates:
            candidates.append(text)

    errors: List[str] = []
    for spec in candidates:
        try:
            return _load_app_from_spec(spec, kwargs)
        except Exception as exc:
            errors.append(f"{spec}: {exc.__class__.__name__}: {exc}")

    raise ReconcileBootstrapError(
        "Could not discover the vectoplan-app Flask application. "
        "Set VECTOPLAN_APP_FACTORY=module:create_app. Attempts: "
        + " | ".join(errors[-8:])
    )


@dataclass(frozen=True)
class RuntimeServices:
    app: Any
    db: Any
    Project: Any
    provision_project_chunk_graph: Callable[..., Any]
    serialize_project_chunk_provisioning_status: Callable[[Any], Mapping[str, Any]]
    clear_project_chunk_provisioning_cache: Callable[[Optional[str]], int]
    sync_project_chunk_access: Callable[..., Any]
    serialize_project_chunk_access_sync_status: Callable[[Any], Mapping[str, Any]]
    clear_project_chunk_access_sync_cache: Callable[[Any], int]
    build_desired_project_chunk_access: Optional[Callable[..., Any]] = None
    audit_writer: Optional[Callable[..., Any]] = None


def _load_project_model() -> Any:
    errors: List[str] = []
    for module_name in ("models", "models.projects"):
        try:
            module = importlib.import_module(module_name)
            project_model = getattr(module, "Project", None)
            if project_model is not None:
                return project_model
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    raise ReconcileBootstrapError("Could not import Project model: " + " | ".join(errors))


def _build_audit_writer() -> Optional[Callable[..., Any]]:
    candidates: List[Callable[..., Any]] = []
    for module_name in ("models", "models.project_audit"):
        try:
            module = importlib.import_module(module_name)
            for name in ("record_project_audit_event", "create_project_audit_event"):
                fn = getattr(module, name, None)
                if callable(fn):
                    candidates.append(fn)
        except Exception:
            continue
    if not candidates:
        return None
    writer = candidates[0]

    def audit_writer(
        project: Any,
        *,
        action: str,
        actor_auth_user_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        commit: bool = False,
        **_: Any,
    ) -> Any:
        clean_payload = _safe_mapping(payload or metadata)
        if actor_auth_user_id:
            clean_payload.setdefault("actorAuthUserIdFingerprint", hashlib.sha256(actor_auth_user_id.encode("utf-8")).hexdigest()[:16])
        clean_payload.setdefault("source", "src/scripts/reconcile_chunk_projects.py")
        try:
            return writer(
                project,
                action=action,
                category="service_reconciliation",
                actor_user_id=None,
                payload=clean_payload,
                commit=commit,
            )
        except TypeError:
            try:
                return writer(project, action=action, metadata=clean_payload, commit=commit)
            except TypeError:
                return writer(project, action=action, payload=clean_payload)

    return audit_writer


def load_runtime_services(app: Any) -> RuntimeServices:
    try:
        extensions = importlib.import_module("extensions")
        db = getattr(extensions, "db")
        Project = _load_project_model()
        provisioning = importlib.import_module("services.project_chunk_provisioning_service")
        access_sync = importlib.import_module("services.project_chunk_access_sync_service")
    except Exception as exc:
        raise ReconcileBootstrapError(f"Could not import reconciliation dependencies: {exc}") from exc

    required = {
        "provision_project_chunk_graph": getattr(provisioning, "provision_project_chunk_graph", None),
        "serialize_project_chunk_provisioning_status": getattr(provisioning, "serialize_project_chunk_provisioning_status", None),
        "clear_project_chunk_provisioning_cache": getattr(provisioning, "clear_project_chunk_provisioning_cache", None),
        "sync_project_chunk_access": getattr(access_sync, "sync_project_chunk_access", None),
        "serialize_project_chunk_access_sync_status": getattr(access_sync, "serialize_project_chunk_access_sync_status", None),
        "clear_project_chunk_access_sync_cache": getattr(access_sync, "clear_project_chunk_access_sync_cache", None),
    }
    missing = [name for name, value in required.items() if not callable(value)]
    if missing:
        raise ReconcileBootstrapError("Missing reconciliation service functions: " + ", ".join(missing))

    return RuntimeServices(
        app=app,
        db=db,
        Project=Project,
        provision_project_chunk_graph=required["provision_project_chunk_graph"],
        serialize_project_chunk_provisioning_status=required["serialize_project_chunk_provisioning_status"],
        clear_project_chunk_provisioning_cache=required["clear_project_chunk_provisioning_cache"],
        sync_project_chunk_access=required["sync_project_chunk_access"],
        serialize_project_chunk_access_sync_status=required["serialize_project_chunk_access_sync_status"],
        clear_project_chunk_access_sync_cache=required["clear_project_chunk_access_sync_cache"],
        build_desired_project_chunk_access=getattr(access_sync, "build_desired_project_chunk_access", None),
        audit_writer=_build_audit_writer(),
    )


# ---------------------------------------------------------------------------
# Process lock and stop handling
# ---------------------------------------------------------------------------


class StopController:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.signal_name: Optional[str] = None
        self._previous: Dict[int, Any] = {}

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self, signal_name: str = "manual") -> None:
        self.signal_name = signal_name
        self._event.set()

    def _handler(self, signum: int, _frame: Any) -> None:
        try:
            name = signal.Signals(signum).name
        except Exception:
            name = str(signum)
        LOGGER.warning("Stop requested by %s; the current project will finish first.", name)
        self.request(name)

    def install(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous[int(signum)] = signal.getsignal(signum)
                signal.signal(signum, self._handler)
            except Exception:
                continue

    def restore(self) -> None:
        for signum, previous in self._previous.items():
            with contextlib.suppress(Exception):
                signal.signal(signum, previous)
        self._previous.clear()


@contextlib.contextmanager
def process_lock(path: str, *, enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
        return

    lock_path = pathlib.Path(path).expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            import fcntl  # POSIX; production containers are Linux.

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            LOGGER.warning("fcntl is unavailable; process-level reconciliation lock is disabled.")
        except BlockingIOError as exc:
            handle.seek(0)
            holder = handle.read().strip()
            raise ReconcileLockError(
                f"Another reconciliation process holds {lock_path}. Holder: {holder or 'unknown'}"
            ) from exc

        handle.seek(0)
        handle.truncate(0)
        json.dump(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "startedAt": _iso(_utcnow()),
                "scriptVersion": SCRIPT_VERSION,
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        with contextlib.suppress(Exception):
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        handle.close()


# ---------------------------------------------------------------------------
# Reconciliation contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileOptions:
    scope: ReconcileScope = ReconcileScope.BOTH
    dry_run: bool = False
    force: bool = False
    include_ready: bool = False
    include_disabled: bool = False
    include_demo: bool = False
    include_deleted: bool = False
    include_archived: bool = False
    continue_access_after_provisioning_failure: bool = False
    fail_fast: bool = False
    max_failures: int = 0
    batch_size: int = DEFAULT_BATCH_SIZE
    max_projects: int = 0
    after_id: int = 0
    sleep_seconds: float = 0.0
    provisioning_statuses: frozenset[str] = frozenset()
    access_statuses: frozenset[str] = frozenset()
    actor_auth_user_id: Optional[str] = None
    request_prefix: str = DEFAULT_REQUEST_PREFIX
    report_skipped: bool = False

    @property
    def run_provisioning(self) -> bool:
        return self.scope in {ReconcileScope.BOTH, ReconcileScope.PROVISIONING}

    @property
    def run_access(self) -> bool:
        return self.scope in {ReconcileScope.BOTH, ReconcileScope.ACCESS}


@dataclass(frozen=True)
class ProjectSnapshot:
    project_db_id: Optional[int]
    project_public_id: str
    project_status: str
    is_demo: bool
    is_deleted: bool
    is_archived: bool
    owner_auth_user_id_present: bool
    provisioning: Dict[str, Any]
    access_sync: Dict[str, Any]

    @property
    def provisioning_status(self) -> str:
        return _normalize_status(self.provisioning.get("status"), "unknown")

    @property
    def access_status(self) -> str:
        return _normalize_status(self.access_sync.get("status"), "unknown")

    @property
    def chunk_project_id(self) -> str:
        return _safe_str(
            self.provisioning.get("chunkProjectId")
            or self.provisioning.get("chunk_project_id"),
            "",
            240,
        )

    @property
    def chunk_world_id(self) -> str:
        return _safe_str(
            self.provisioning.get("chunkWorldId")
            or self.provisioning.get("chunk_world_id"),
            "",
            240,
        )

    @property
    def provisioning_ready(self) -> bool:
        return bool(
            self.provisioning_status in PROVISIONING_READY_STATUSES
            and self.chunk_project_id
            and self.chunk_world_id
            and _safe_bool(self.provisioning.get("ready"), True)
        )

    @property
    def access_ready(self) -> bool:
        return self.access_status in ACCESS_READY_STATUSES and _safe_bool(
            self.access_sync.get("ready"), True
        )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "projectDbId": self.project_db_id,
            "projectPublicId": self.project_public_id,
            "projectStatus": self.project_status,
            "isDemo": self.is_demo,
            "isDeleted": self.is_deleted,
            "isArchived": self.is_archived,
            "ownerAuthUserIdPresent": self.owner_auth_user_id_present,
            "provisioning": _json_safe(self.provisioning),
            "accessSync": _json_safe(self.access_sync),
        }


@dataclass(frozen=True)
class ProjectPlan:
    selected: bool
    actions: Tuple[str, ...] = ()
    skip_code: str = ""
    warnings: Tuple[Dict[str, Any], ...] = ()
    errors: Tuple[Dict[str, Any], ...] = ()
    desired_access: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected": self.selected,
            "actions": list(self.actions),
            "skipCode": self.skip_code or None,
            "warnings": [_json_safe(item) for item in self.warnings],
            "errors": [_json_safe(item) for item in self.errors],
            "desiredAccess": _json_safe(self.desired_access),
        }


@dataclass
class ProjectReconcileResult:
    project_public_id: str
    project_db_id: Optional[int]
    outcome: ProjectOutcome
    code: str
    message: str = ""
    actions: List[str] = field(default_factory=list)
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    provisioning_result: Dict[str, Any] = field(default_factory=dict)
    access_result: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    request_ids: List[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome in {ProjectOutcome.SUCCEEDED, ProjectOutcome.SKIPPED, ProjectOutcome.DRY_RUN}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "outcome": self.outcome.value,
            "code": self.code,
            "message": self.message,
            "projectDbId": self.project_db_id,
            "projectPublicId": self.project_public_id,
            "actions": list(self.actions),
            "before": _json_safe(self.before),
            "after": _json_safe(self.after),
            "provisioningResult": _json_safe(self.provisioning_result),
            "accessResult": _json_safe(self.access_result),
            "warnings": [_json_safe(item) for item in self.warnings],
            "errors": [_json_safe(item) for item in self.errors],
            "requestIds": list(self.request_ids),
            "durationMs": self.duration_ms,
        }


@dataclass
class ReconcileSummary:
    started_at: str
    options: ReconcileOptions
    host: str = field(default_factory=socket.gethostname)
    pid: int = field(default_factory=os.getpid)
    finished_at: Optional[str] = None
    duration_ms: int = 0
    scanned: int = 0
    selected: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: int = 0
    interrupted: bool = False
    stop_signal: Optional[str] = None
    results: List[ProjectReconcileResult] = field(default_factory=list)

    def add(self, result: ProjectReconcileResult, *, include_result: bool = True) -> None:
        if include_result:
            self.results.append(result)
        if result.outcome == ProjectOutcome.SUCCEEDED:
            self.succeeded += 1
        elif result.outcome == ProjectOutcome.FAILED:
            self.failed += 1
        elif result.outcome == ProjectOutcome.DRY_RUN:
            self.dry_run += 1
        elif result.outcome == ProjectOutcome.INTERRUPTED:
            self.interrupted = True
        else:
            self.skipped += 1

    def finish(self, started_monotonic: float, stop: StopController) -> None:
        self.finished_at = _iso(_utcnow())
        self.duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        self.interrupted = bool(self.interrupted or stop.requested)
        self.stop_signal = stop.signal_name

    def to_dict(self, *, include_results: bool = True) -> Dict[str, Any]:
        options_dict = dataclasses.asdict(self.options)
        options_dict["scope"] = self.options.scope.value
        options_dict["provisioning_statuses"] = sorted(self.options.provisioning_statuses)
        options_dict["access_statuses"] = sorted(self.options.access_statuses)
        options_dict["actor_auth_user_id"] = "<provided>" if self.options.actor_auth_user_id else None
        payload: Dict[str, Any] = {
            "reportVersion": REPORT_VERSION,
            "scriptVersion": SCRIPT_VERSION,
            "ok": self.failed == 0 and not self.interrupted,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationMs": self.duration_ms,
            "host": self.host,
            "pid": self.pid,
            "options": options_dict,
            "counts": {
                "scanned": self.scanned,
                "selected": self.selected,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "skipped": self.skipped,
                "dryRun": self.dry_run,
            },
            "interrupted": self.interrupted,
            "stopSignal": self.stop_signal,
        }
        if include_results:
            payload["results"] = [result.to_dict() for result in self.results]
        return _json_safe(payload)


# ---------------------------------------------------------------------------
# Project query and reconciliation engine
# ---------------------------------------------------------------------------


class ProjectReconciler:
    def __init__(
        self,
        runtime: RuntimeServices,
        options: ReconcileOptions,
        *,
        stop: Optional[StopController] = None,
    ) -> None:
        self.runtime = runtime
        self.options = options
        self.stop = stop or StopController()

    @property
    def session(self) -> Any:
        return self.runtime.db.session

    def _rollback(self) -> None:
        with contextlib.suppress(Exception):
            self.session.rollback()

    def _refresh_project(self, project: Any) -> Any:
        try:
            project_id = getattr(project, "id", None)
            if project_id is None:
                return project
            with contextlib.suppress(Exception):
                self.session.expire_all()
            if hasattr(self.session, "get"):
                refreshed = self.session.get(self.runtime.Project, project_id)
            else:
                refreshed = self.runtime.Project.query.get(project_id)
            return refreshed or project
        except Exception:
            return project

    def snapshot(self, project: Any) -> ProjectSnapshot:
        provisioning = _safe_mapping(self.runtime.serialize_project_chunk_provisioning_status(project))
        access_sync = _safe_mapping(self.runtime.serialize_project_chunk_access_sync_status(project))
        project_status = _normalize_status(getattr(project, "status", None), "unknown")
        is_demo = _safe_bool(getattr(project, "is_demo", False), False) or _safe_str(
            getattr(project, "project_scope", ""), "", 80
        ).lower() == "demo"
        is_deleted = _safe_bool(getattr(project, "is_deleted", False), False) or project_status in TERMINAL_PROJECT_STATUSES
        is_archived = project_status in ARCHIVED_PROJECT_STATUSES
        owner_auth_user_id = _safe_str(getattr(project, "auth_owner_user_id", None), "", 240)
        return ProjectSnapshot(
            project_db_id=_safe_int(getattr(project, "id", None), 0) or None,
            project_public_id=_safe_str(getattr(project, "public_id", None), "", 240),
            project_status=project_status,
            is_demo=is_demo,
            is_deleted=is_deleted,
            is_archived=is_archived,
            owner_auth_user_id_present=bool(owner_auth_user_id),
            provisioning=provisioning,
            access_sync=access_sync,
        )

    def plan(self, project: Any, snapshot: Optional[ProjectSnapshot] = None) -> ProjectPlan:
        snap = snapshot or self.snapshot(project)
        warnings: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        actions: List[str] = []

        if not snap.project_public_id:
            return ProjectPlan(False, skip_code="project_public_id_missing")
        if snap.is_demo and not self.options.include_demo:
            return ProjectPlan(False, skip_code="demo_project_excluded")
        if snap.is_deleted and not self.options.include_deleted:
            return ProjectPlan(False, skip_code="deleted_project_excluded")
        if snap.is_archived and not self.options.include_archived:
            return ProjectPlan(False, skip_code="archived_project_excluded")
        if self.options.provisioning_statuses and snap.provisioning_status not in self.options.provisioning_statuses:
            return ProjectPlan(False, skip_code="provisioning_status_filtered")
        if self.options.access_statuses and snap.access_status not in self.options.access_statuses:
            return ProjectPlan(False, skip_code="access_status_filtered")

        provisioning_disabled = snap.provisioning_status in PROVISIONING_DISABLED_STATUSES
        access_disabled = snap.access_status in ACCESS_DISABLED_STATUSES

        if self.options.run_provisioning:
            should_provision = bool(self.options.force or self.options.include_ready or not snap.provisioning_ready)
            if provisioning_disabled and not self.options.include_disabled:
                should_provision = False
                warnings.append(
                    {
                        "code": "provisioning_disabled",
                        "message": "Chunk provisioning is disabled for this project/configuration.",
                    }
                )
            if should_provision:
                actions.append("provision_chunk_graph")

        if self.options.run_access:
            should_sync = bool(self.options.force or self.options.include_ready or not snap.access_ready)
            if access_disabled and not self.options.include_disabled:
                should_sync = False
                warnings.append(
                    {
                        "code": "access_sync_disabled",
                        "message": "Chunk access synchronization is disabled.",
                    }
                )
            if should_sync:
                if snap.chunk_project_id or "provision_chunk_graph" in actions:
                    actions.append("sync_chunk_access")
                else:
                    errors.append(
                        {
                            "code": "chunk_project_missing",
                            "message": "Access synchronization requires a Chunk project reference.",
                        }
                    )

        desired_access: Optional[Dict[str, Any]] = None
        if self.options.dry_run and "sync_chunk_access" in actions and callable(self.runtime.build_desired_project_chunk_access):
            try:
                desired = self.runtime.build_desired_project_chunk_access(project)
                desired_payload = _result_dict(desired)
                if not desired_payload and hasattr(desired, "to_dict"):
                    desired_payload = _safe_mapping(desired.to_dict(include_local_ids=False))
                desired_access = desired_payload
            except Exception as exc:
                errors.append(
                    {
                        "code": _safe_str(getattr(exc, "code", None), "desired_access_build_failed", 160),
                        "message": _safe_str(exc, "Could not build desired project access state.", 1000),
                        "retryable": _safe_bool(getattr(exc, "retryable", False), False),
                    }
                )

        if not actions:
            if errors:
                return ProjectPlan(True, actions=(), skip_code="no_executable_action", warnings=tuple(warnings), errors=tuple(errors))
            return ProjectPlan(False, actions=(), skip_code="already_reconciled", warnings=tuple(warnings))

        return ProjectPlan(
            True,
            actions=tuple(actions),
            warnings=tuple(warnings),
            errors=tuple(errors),
            desired_access=desired_access,
        )

    def _request_id(self, project_public_id: str, phase: str) -> str:
        prefix = _safe_str(self.options.request_prefix, DEFAULT_REQUEST_PREFIX, 80).replace(" ", "-")
        digest = hashlib.sha256(f"{project_public_id}:{phase}".encode("utf-8")).hexdigest()[:10]
        return f"{prefix}-{phase}-{digest}-{uuid.uuid4().hex[:12]}"

    def _provision(self, project: Any, snapshot: ProjectSnapshot) -> Tuple[Any, Dict[str, Any], str]:
        request_id = self._request_id(snapshot.project_public_id, "provision")
        if self.options.force:
            with contextlib.suppress(Exception):
                self.runtime.clear_project_chunk_provisioning_cache(snapshot.project_public_id)
        owner_auth_user_id = _safe_str(getattr(project, "auth_owner_user_id", None), "", 240) or None
        result = self.runtime.provision_project_chunk_graph(
            project,
            owner_auth_user_id=owner_auth_user_id,
            force=self.options.force,
            commit=True,
            raise_on_error=False,
            request_id=request_id,
            audit_writer=self.runtime.audit_writer,
        )
        return result, _result_dict(result), request_id

    def _sync_access(self, project: Any, snapshot: ProjectSnapshot) -> Tuple[Any, Dict[str, Any], str]:
        request_id = self._request_id(snapshot.project_public_id, "access")
        if self.options.force:
            with contextlib.suppress(Exception):
                self.runtime.clear_project_chunk_access_sync_cache(snapshot.project_public_id)
        result = self.runtime.sync_project_chunk_access(
            project,
            actor_auth_user_id=self.options.actor_auth_user_id,
            force=self.options.force,
            commit=True,
            raise_on_error=False,
            request_id=request_id,
            audit_writer=self.runtime.audit_writer,
        )
        return result, _result_dict(result), request_id

    def reconcile_project(self, project: Any) -> ProjectReconcileResult:
        started = time.monotonic()
        before = self.snapshot(project)
        plan = self.plan(project, before)

        if not plan.selected:
            return ProjectReconcileResult(
                project_public_id=before.project_public_id,
                project_db_id=before.project_db_id,
                outcome=ProjectOutcome.SKIPPED,
                code=plan.skip_code or "project_skipped",
                message="Project does not require reconciliation under the selected filters.",
                actions=list(plan.actions),
                before=before.public_dict(),
                after=before.public_dict(),
                warnings=list(plan.warnings),
                errors=list(plan.errors),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )

        if plan.errors and not plan.actions:
            return ProjectReconcileResult(
                project_public_id=before.project_public_id,
                project_db_id=before.project_db_id,
                outcome=ProjectOutcome.FAILED,
                code=plan.skip_code or "no_executable_reconcile_action",
                message="The project requires repair, but no executable action is available in the selected scope.",
                actions=[],
                before=before.public_dict(),
                after=before.public_dict(),
                warnings=list(plan.warnings),
                errors=list(plan.errors),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )

        if self.options.dry_run:
            after = before.public_dict()
            after["plan"] = plan.to_dict()
            outcome = ProjectOutcome.DRY_RUN if not plan.errors else ProjectOutcome.FAILED
            return ProjectReconcileResult(
                project_public_id=before.project_public_id,
                project_db_id=before.project_db_id,
                outcome=outcome,
                code="reconcile_dry_run" if not plan.errors else "reconcile_dry_run_has_errors",
                message="No database commit or remote mutation was performed.",
                actions=list(plan.actions),
                before=before.public_dict(),
                after=after,
                warnings=list(plan.warnings),
                errors=list(plan.errors),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )

        result = ProjectReconcileResult(
            project_public_id=before.project_public_id,
            project_db_id=before.project_db_id,
            outcome=ProjectOutcome.SUCCEEDED,
            code="project_reconciled",
            actions=list(plan.actions),
            before=before.public_dict(),
            warnings=list(plan.warnings),
            errors=list(plan.errors),
        )

        provisioning_failed = False
        if "provision_chunk_graph" in plan.actions:
            try:
                provision_result, provision_payload, request_id = self._provision(project, before)
                result.request_ids.append(request_id)
                result.provisioning_result = provision_payload
                if not _result_ok(provision_result):
                    provisioning_failed = True
                    result.outcome = ProjectOutcome.FAILED
                    result.code = _result_code(provision_result, "chunk_provisioning_failed")
                    result.message = _safe_str(
                        provision_payload.get("message")
                        or _safe_mapping(provision_payload.get("error")).get("message"),
                        "Chunk provisioning failed.",
                        1000,
                    )
                    result.errors.append(
                        {
                            "code": result.code,
                            "message": result.message,
                            "statusCode": _result_status_code(provision_result, 502),
                            "retryable": _safe_bool(provision_payload.get("retryable"), False),
                        }
                    )
                project = self._refresh_project(project)
            except Exception as exc:
                self._rollback()
                provisioning_failed = True
                result.outcome = ProjectOutcome.FAILED
                result.code = _safe_str(getattr(exc, "code", None), "chunk_provisioning_exception", 160)
                result.message = _safe_str(exc, "Chunk provisioning raised an exception.", 1000)
                result.errors.append(
                    {
                        "code": result.code,
                        "message": result.message,
                        "type": exc.__class__.__name__,
                        "retryable": _safe_bool(getattr(exc, "retryable", False), False),
                    }
                )

        run_access = "sync_chunk_access" in plan.actions
        if provisioning_failed and not self.options.continue_access_after_provisioning_failure:
            run_access = False
            result.warnings.append(
                {
                    "code": "access_sync_skipped_after_provisioning_failure",
                    "message": "Access synchronization was skipped because provisioning failed.",
                }
            )

        if run_access:
            current_snapshot = self.snapshot(project)
            if not current_snapshot.chunk_project_id:
                result.outcome = ProjectOutcome.FAILED
                result.code = "chunk_project_missing"
                result.message = "Chunk access synchronization requires a Chunk project reference."
                result.errors.append({"code": result.code, "message": result.message})
            else:
                try:
                    access_result, access_payload, request_id = self._sync_access(project, current_snapshot)
                    result.request_ids.append(request_id)
                    result.access_result = access_payload
                    if not _result_ok(access_result):
                        result.outcome = ProjectOutcome.FAILED
                        result.code = _result_code(access_result, "chunk_access_sync_failed")
                        result.message = _safe_str(
                            access_payload.get("message")
                            or (_safe_sequence(access_payload.get("errors")) or [{}])[0].get("message"),
                            "Chunk access synchronization failed.",
                            1000,
                        )
                        result.errors.append(
                            {
                                "code": result.code,
                                "message": result.message,
                                "statusCode": _result_status_code(access_result, 502),
                                "retryable": _safe_bool(access_payload.get("retryable"), False),
                                "repairRequired": _safe_bool(access_payload.get("repairRequired"), False),
                            }
                        )
                    project = self._refresh_project(project)
                except Exception as exc:
                    self._rollback()
                    result.outcome = ProjectOutcome.FAILED
                    result.code = _safe_str(getattr(exc, "code", None), "chunk_access_sync_exception", 160)
                    result.message = _safe_str(exc, "Chunk access synchronization raised an exception.", 1000)
                    result.errors.append(
                        {
                            "code": result.code,
                            "message": result.message,
                            "type": exc.__class__.__name__,
                            "retryable": _safe_bool(getattr(exc, "retryable", False), False),
                        }
                    )

        project = self._refresh_project(project)
        after = self.snapshot(project)
        result.after = after.public_dict()
        if result.outcome == ProjectOutcome.SUCCEEDED:
            if "provision_chunk_graph" in plan.actions and not after.provisioning_ready:
                result.outcome = ProjectOutcome.FAILED
                result.code = "provisioning_not_ready_after_reconcile"
                result.message = "Provisioning returned without a complete ready project/world reference."
                result.errors.append({"code": result.code, "message": result.message})
            if "sync_chunk_access" in plan.actions and not after.access_ready:
                access_disabled = after.access_status in ACCESS_DISABLED_STATUSES
                if not access_disabled:
                    result.outcome = ProjectOutcome.FAILED
                    result.code = "access_not_ready_after_reconcile"
                    result.message = "Access synchronization did not reach ready state."
                    result.errors.append({"code": result.code, "message": result.message})

        if result.outcome == ProjectOutcome.SUCCEEDED:
            result.message = "Project Chunk provisioning and access projection are reconciled."
        result.duration_ms = max(0, int((time.monotonic() - started) * 1000))
        return result

    def _load_identifier(self, identifier: str) -> Any:
        value = _safe_str(identifier, "", 240)
        if not value:
            return None
        try:
            numeric = int(value)
        except Exception:
            numeric = None
        if numeric is not None:
            try:
                if hasattr(self.session, "get"):
                    project = self.session.get(self.runtime.Project, numeric)
                else:
                    project = self.runtime.Project.query.get(numeric)
                if project is not None:
                    return project
            except Exception:
                self._rollback()
        try:
            return self.runtime.Project.query.filter_by(public_id=value).one_or_none()
        except Exception:
            try:
                return self.runtime.Project.query.filter(self.runtime.Project.public_id == value).first()
            except Exception:
                self._rollback()
                return None

    def iter_explicit_projects(self, identifiers: Sequence[str]) -> Iterator[Any]:
        seen: Set[str] = set()
        for identifier in identifiers:
            if self.stop.requested:
                break
            project = self._load_identifier(identifier)
            if project is None:
                yield MissingProject(identifier)
                continue
            snapshot = self.snapshot(project)
            key = snapshot.project_public_id or f"db:{snapshot.project_db_id}"
            if key in seen:
                continue
            seen.add(key)
            yield project

    def iter_projects(self) -> Iterator[Any]:
        Project = self.runtime.Project
        cursor = max(0, self.options.after_id)
        batch_size = max(1, min(1000, self.options.batch_size))

        while not self.stop.requested:
            query = Project.query
            try:
                if hasattr(Project, "id") and cursor > 0:
                    query = query.filter(Project.id > cursor)
                if hasattr(Project, "is_demo") and not self.options.include_demo:
                    query = query.filter(Project.is_demo.is_(False))
                if hasattr(Project, "status") and not self.options.include_deleted:
                    query = query.filter(~Project.status.in_(list(TERMINAL_PROJECT_STATUSES)))
                if hasattr(Project, "status") and not self.options.include_archived:
                    query = query.filter(~Project.status.in_(list(ARCHIVED_PROJECT_STATUSES)))
                if hasattr(Project, "id"):
                    query = query.order_by(Project.id.asc())
                batch = list(query.limit(batch_size).all())
            except Exception as exc:
                self._rollback()
                raise RuntimeError(f"Project query failed: {exc}") from exc

            if not batch:
                break

            for project in batch:
                if self.stop.requested:
                    break
                project_id = _safe_int(getattr(project, "id", None), cursor)
                cursor = max(cursor, project_id)
                yield project

            if len(batch) < batch_size:
                break


@dataclass(frozen=True)
class MissingProject:
    identifier: str


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile vectoplan-app projects with vectoplan-chunk provisioning and access state.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--app-factory", help="Flask application or factory in module:attribute form.")
    parser.add_argument("--project", action="append", default=[], help="Project public ID or local DB ID. Repeatable.")
    parser.add_argument("--only", choices=[scope.value for scope in ReconcileScope], default=ReconcileScope.BOTH.value)
    parser.add_argument("--dry-run", action="store_true", help="Plan only; no commit and no remote mutation.")
    parser.add_argument("--force", action="store_true", help="Bypass successful-state caches and re-run selected phases.")
    parser.add_argument("--include-ready", action="store_true", help="Also verify projects already marked ready.")
    parser.add_argument("--include-disabled", action="store_true", help="Include disabled provisioning/access states.")
    parser.add_argument("--include-demo", action="store_true", help="Include demo projects. Services normally skip them.")
    parser.add_argument("--include-deleted", action="store_true", help="Include deleted and expired projects.")
    parser.add_argument("--include-archived", action="store_true", help="Include archived projects.")
    parser.add_argument(
        "--continue-access-after-provisioning-failure",
        action="store_true",
        help="Attempt access sync when an existing chunk project reference remains after provisioning failure.",
    )
    parser.add_argument("--status", action="append", default=[], help="Provisioning status filter; CSV or repeatable.")
    parser.add_argument("--access-status", action="append", default=[], help="Access-sync status filter; CSV or repeatable.")
    parser.add_argument("--after-id", type=int, default=0, help="Start after this local Project.id for resumable scans.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Database keyset batch size.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum selected projects; 0 means unlimited.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Pause between selected projects.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed project.")
    parser.add_argument("--max-failures", type=int, default=0, help="Stop after this many failures; 0 means unlimited.")
    parser.add_argument(
        "--actor-auth-user-id",
        default=None,
        help="Optional canonical auth user ID recorded as access-sync actor. Never printed in reports.",
    )
    parser.add_argument("--request-prefix", default=DEFAULT_REQUEST_PREFIX, help="Prefix for correlation IDs.")
    parser.add_argument("--lock-file", default=DEFAULT_LOCK_FILE, help="Local single-process lock file.")
    parser.add_argument("--no-process-lock", action="store_true", help="Disable the local process lock.")
    parser.add_argument("--json-lines", action="store_true", help="Print one JSON object per selected project.")
    parser.add_argument("--output-json", help="Atomically write the complete reconciliation report to this path.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-project human-readable output.")
    parser.add_argument(
        "--report-skipped",
        action="store_true",
        help="Include already-consistent or filtered projects in per-project output and the JSON result list.",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"), choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--traceback", action="store_true", help="Print tracebacks for bootstrap/runtime errors.")
    return parser


def _options_from_args(args: argparse.Namespace) -> ReconcileOptions:
    actor = _safe_str(args.actor_auth_user_id, "", 160) or None
    if actor and actor.lower() in {"guest", "anonymous", "demo", "default", "none", "null"}:
        raise ValueError("--actor-auth-user-id must be a canonical vectoplan-auth user ID.")
    return ReconcileOptions(
        scope=ReconcileScope(args.only),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        include_ready=bool(args.include_ready),
        include_disabled=bool(args.include_disabled),
        include_demo=bool(args.include_demo),
        include_deleted=bool(args.include_deleted),
        include_archived=bool(args.include_archived),
        continue_access_after_provisioning_failure=bool(args.continue_access_after_provisioning_failure),
        fail_fast=bool(args.fail_fast),
        max_failures=max(0, int(args.max_failures or 0)),
        batch_size=max(1, min(1000, int(args.batch_size or DEFAULT_BATCH_SIZE))),
        max_projects=max(0, int(args.limit or 0)),
        after_id=max(0, int(args.after_id or 0)),
        sleep_seconds=max(0.0, min(3600.0, float(args.sleep_seconds or 0.0))),
        provisioning_statuses=frozenset(_csv_set(args.status)),
        access_statuses=frozenset(_csv_set(args.access_status)),
        actor_auth_user_id=actor,
        request_prefix=_safe_str(args.request_prefix, DEFAULT_REQUEST_PREFIX, 80) or DEFAULT_REQUEST_PREFIX,
        report_skipped=bool(args.report_skipped),
    )


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _human_line(result: ProjectReconcileResult) -> str:
    marker = {
        ProjectOutcome.SUCCEEDED: "OK",
        ProjectOutcome.FAILED: "FAIL",
        ProjectOutcome.SKIPPED: "SKIP",
        ProjectOutcome.DRY_RUN: "PLAN",
        ProjectOutcome.INTERRUPTED: "STOP",
    }[result.outcome]
    actions = ",".join(result.actions) if result.actions else "none"
    return f"[{marker}] {result.project_public_id or '<missing-public-id>'} {result.code} actions={actions} durationMs={result.duration_ms}"


def _missing_result(identifier: str) -> ProjectReconcileResult:
    return ProjectReconcileResult(
        project_public_id=_safe_str(identifier, "", 240),
        project_db_id=None,
        outcome=ProjectOutcome.FAILED,
        code="project_not_found",
        message="Explicit project identifier was not found.",
        errors=[{"code": "project_not_found", "message": "Project not found."}],
    )


def run_reconciliation(
    runtime: RuntimeServices,
    options: ReconcileOptions,
    *,
    project_identifiers: Sequence[str] = (),
    stop: Optional[StopController] = None,
    json_lines: bool = False,
    quiet: bool = False,
) -> ReconcileSummary:
    stop = stop or StopController()
    reconciler = ProjectReconciler(runtime, options, stop=stop)
    started_monotonic = time.monotonic()
    summary = ReconcileSummary(started_at=_iso(_utcnow()) or "", options=options)

    iterator: Iterable[Any]
    if project_identifiers:
        iterator = reconciler.iter_explicit_projects(project_identifiers)
    else:
        iterator = reconciler.iter_projects()

    for item in iterator:
        if stop.requested:
            break
        summary.scanned += 1
        if isinstance(item, MissingProject):
            result = _missing_result(item.identifier)
            summary.selected += 1
        else:
            try:
                before = reconciler.snapshot(item)
                plan = reconciler.plan(item, before)
                if not plan.selected:
                    result = reconciler.reconcile_project(item)
                    summary.add(result, include_result=options.report_skipped)
                    if options.report_skipped:
                        if json_lines:
                            print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
                        elif not quiet:
                            print(_human_line(result), flush=True)
                    continue
                if options.max_projects and summary.selected >= options.max_projects:
                    break
                summary.selected += 1
                result = reconciler.reconcile_project(item)
            except Exception as exc:
                reconciler._rollback()
                project_public_id = _safe_str(getattr(item, "public_id", None), "", 240)
                result = ProjectReconcileResult(
                    project_public_id=project_public_id,
                    project_db_id=_safe_int(getattr(item, "id", None), 0) or None,
                    outcome=ProjectOutcome.FAILED,
                    code="project_reconcile_exception",
                    message=_safe_str(exc, "Project reconciliation failed.", 1000),
                    errors=[
                        {
                            "code": "project_reconcile_exception",
                            "message": _safe_str(exc, "", 1000),
                            "type": exc.__class__.__name__,
                        }
                    ],
                )

        summary.add(result)
        if json_lines:
            print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
        elif not quiet:
            print(_human_line(result), flush=True)

        if result.outcome == ProjectOutcome.FAILED:
            if options.fail_fast:
                break
            if options.max_failures and summary.failed >= options.max_failures:
                break
        if options.sleep_seconds > 0 and not stop.requested:
            time.sleep(options.sleep_seconds)

    summary.finish(started_monotonic, stop)
    return summary


def _summary_exit_code(summary: ReconcileSummary) -> ExitCode:
    if summary.interrupted:
        return ExitCode.INTERRUPTED
    if summary.failed:
        return ExitCode.PARTIAL_FAILURE
    return ExitCode.OK


def main(argv: Optional[Sequence[str]] = None, *, app: Any = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        _configure_logging(args.log_level)
        options = _options_from_args(args)
    except SystemExit:
        raise
    except Exception as exc:
        parser.error(str(exc))
        return int(ExitCode.INVALID_ARGUMENTS)

    stop = StopController()
    stop.install()
    try:
        with process_lock(args.lock_file, enabled=not args.no_process_lock):
            flask_app = app or discover_flask_app(args.app_factory)
            with flask_app.app_context():
                runtime = load_runtime_services(flask_app)
                summary = run_reconciliation(
                    runtime,
                    options,
                    project_identifiers=args.project,
                    stop=stop,
                    json_lines=bool(args.json_lines),
                    quiet=bool(args.quiet),
                )
                report = summary.to_dict(include_results=True)
                if args.output_json:
                    _atomic_write_json(pathlib.Path(args.output_json), report)
                if not args.json_lines:
                    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
                return int(_summary_exit_code(summary))
    except ReconcileLockError as exc:
        LOGGER.error("%s", exc)
        return int(ExitCode.LOCKED)
    except (ReconcileBootstrapError, ImportError, AttributeError) as exc:
        LOGGER.error("Bootstrap failed: %s", exc)
        if args.traceback:
            traceback.print_exc()
        return int(ExitCode.BOOTSTRAP_ERROR)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted.")
        return int(ExitCode.INTERRUPTED)
    except Exception as exc:
        LOGGER.error("Reconciliation failed: %s", exc)
        if args.traceback:
            traceback.print_exc()
        return int(ExitCode.PARTIAL_FAILURE)
    finally:
        stop.restore()


if __name__ == "__main__":
    raise SystemExit(main())
