# services/vectoplan-app/services/demo_project_service.py
"""
Temporärer Demo-Projekt-Service für vectoplan-app.

Zweck:
- Nicht eingeloggte Guests mit demo_project_access erhalten ein temporäres Demo-Projekt.
- Echte eingeloggte User sehen ihre persistenten Projekte und kein Demo-Projekt.
- Gebannte/blockierte User erhalten keinen Demo-Fallback.
- Demo-Projekte werden nach TTL ungültig und können bereinigt werden.
- Diese Datei ist schema-defensiv und funktioniert sowohl vor als auch nach Erweiterung
  von models/projects.py um explizite Demo-Felder.

Empfohlenes Zielmodell später in models/projects.py:
    is_demo: bool
    demo_client_identity_id: str | None
    demo_session_id: str | None
    demo_expires_at: datetime | None
    project_scope: "demo" | "personal" | "account"
    auth_account_id: str | None

Bis diese Felder existieren, speichert dieser Service Demo-Metadaten defensiv in:
    Project.metadata_json
    Project.settings
    Project.service_refs
    Project.artifact_refs

Wichtige Regeln:
- Demo ist kein echter User.
- Demo-Projekte gehören nicht zu persistenten User-Projektlisten.
- Demo-Projekte dürfen keine Team-/Invitation-/Publication-/Admin-Aktionen auslösen.
- Demo-Projekte dürfen technisch in DB/Chunk existieren, gelten aber fachlich als temporär.
- Auth-Service unavailable oder blocked/banned führt nicht zu Demo-Fallback.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple


try:
    from flask import current_app, has_app_context, has_request_context, request
except Exception:  # pragma: no cover
    current_app = None  # type: ignore
    request = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False

    def has_request_context() -> bool:  # type: ignore
        return False


try:
    from sqlalchemy import and_, or_
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
except Exception:  # pragma: no cover
    and_ = None  # type: ignore
    or_ = None  # type: ignore
    IntegrityError = Exception  # type: ignore
    SQLAlchemyError = Exception  # type: ignore


try:
    from services.auth_context import AuthContext, get_current_auth_context, is_demo_context
except Exception:  # pragma: no cover - Package-Import-Fallback
    from .auth_context import AuthContext, get_current_auth_context, is_demo_context


LOGGER = logging.getLogger(__name__)


DEFAULT_DEMO_PROJECT_TTL_SECONDS = 3600
DEFAULT_DEMO_PROJECT_NAME = "Demo-Projekt"
DEFAULT_DEMO_PROJECT_DESCRIPTION = "Temporäres Demo-Projekt. Änderungen werden nicht dauerhaft gespeichert."
DEFAULT_DEMO_PROJECT_ADDRESS_TEXT = "Demo-Adresse"
DEFAULT_DEMO_PROJECT_VISIBILITY = "private"
DEFAULT_DEMO_PROJECT_STATUS = "active"
DEFAULT_DEMO_PROJECT_SETUP_STATUS = "configured"
MAX_SCAN_DEMO_PROJECTS = 500


DEMO_META_KEY = "vectoplan_demo"
DEMO_SOURCE = "vectoplan-app-demo"


DEMO_FLAG_FIELDS = (
    "is_demo",
    "demo",
)

DEMO_CLIENT_IDENTITY_FIELDS = (
    "demo_client_identity_id",
    "client_identity_id",
)

DEMO_SESSION_FIELDS = (
    "demo_session_id",
    "demo_id",
)

DEMO_EXPIRES_AT_FIELDS = (
    "demo_expires_at",
    "expires_at",
)

PROJECT_SCOPE_FIELDS = (
    "project_scope",
    "scope",
)

AUTH_ACCOUNT_ID_FIELDS = (
    "auth_account_id",
    "account_id",
)

PUBLIC_ID_FIELDS = (
    "public_id",
    "project_public_id",
)

OWNER_USER_ID_FIELDS = (
    "owner_user_id",
    "user_id",
)

CONVERSATION_ID_FIELDS = (
    "conversation_id",
)

NAME_FIELDS = (
    "name",
    "title",
)

DESCRIPTION_FIELDS = (
    "description",
)

ADDRESS_TEXT_FIELDS = (
    "address_text",
    "address",
)

VISIBILITY_FIELDS = (
    "visibility",
)

IS_PUBLIC_FIELDS = (
    "is_public",
)

STATUS_FIELDS = (
    "status",
)

SETUP_STATUS_FIELDS = (
    "setup_status",
)

SETUP_COMPLETED_AT_FIELDS = (
    "setup_completed_at",
)

JSON_META_FIELDS = (
    "metadata_json",
    "meta_json",
    "settings",
    "service_refs",
    "artifact_refs",
)

CREATED_AT_FIELDS = (
    "created_at",
)

UPDATED_AT_FIELDS = (
    "updated_at",
)

SOFT_DELETE_FIELDS = (
    "deleted_at",
    "archived_at",
)

CHUNK_REF_FIELDS = (
    "chunk_project_id",
    "chunk_universe_id",
    "chunk_world_id",
)

SERVICE_LINK_MODEL_NAMES = (
    "ProjectServiceLink",
)

AUDIT_MODEL_NAMES = (
    "ProjectAuditEvent",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        return default
    return text.strip()


def _lower(value: Any, default: str = "") -> str:
    return _safe_str(value, default=default).lower()


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = _lower(value)
    if text in {"1", "true", "yes", "y", "on", "active", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "inactive", "disabled"}:
        return False
    return default


def _safe_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return number


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    text = _safe_str(value)
    if not text:
        return None

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _datetime_to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _config_value(name: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            if name in current_app.config:
                return current_app.config.get(name)
    except Exception:
        pass

    return os.getenv(name, default)


def _demo_ttl_seconds() -> int:
    value = _config_value("VECTOPLAN_DEMO_PROJECT_TTL_SECONDS", DEFAULT_DEMO_PROJECT_TTL_SECONDS)
    ttl = _safe_int(value, DEFAULT_DEMO_PROJECT_TTL_SECONDS)

    if ttl < 60:
        ttl = 60
    if ttl > 24 * 60 * 60:
        ttl = 24 * 60 * 60

    return ttl


def _demo_project_name() -> str:
    return _safe_str(_config_value("VECTOPLAN_DEMO_PROJECT_NAME", DEFAULT_DEMO_PROJECT_NAME), DEFAULT_DEMO_PROJECT_NAME)


def _demo_project_description() -> str:
    return _safe_str(
        _config_value("VECTOPLAN_DEMO_PROJECT_DESCRIPTION", DEFAULT_DEMO_PROJECT_DESCRIPTION),
        DEFAULT_DEMO_PROJECT_DESCRIPTION,
    )


def _demo_project_address_text() -> str:
    return _safe_str(
        _config_value("VECTOPLAN_DEMO_PROJECT_ADDRESS_TEXT", DEFAULT_DEMO_PROJECT_ADDRESS_TEXT),
        DEFAULT_DEMO_PROJECT_ADDRESS_TEXT,
    )


def _demo_cleanup_on_ensure_enabled() -> bool:
    return _safe_bool(_config_value("VECTOPLAN_DEMO_PROJECT_CLEANUP_ON_ENSURE", True), True)


def _demo_chunk_provisioning_enabled() -> bool:
    return _safe_bool(_config_value("VECTOPLAN_DEMO_PROJECT_CHUNK_PROVISIONING", True), True)


def _hash_text(value: str, length: int = 32) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()
    return digest[:length]


def _demo_identity_hash(identity: str) -> str:
    return _hash_text(f"vectoplan-demo:{identity}", 32)


def _demo_public_id(identity_hash: str) -> str:
    return f"demo_{identity_hash[:24]}"


def _import_db() -> Any:
    try:
        from models.base import db  # type: ignore

        return db
    except Exception:
        pass

    try:
        from extensions import db  # type: ignore

        return db
    except Exception as exc:
        raise RuntimeError("SQLAlchemy db instance could not be imported.") from exc


def _import_project_model() -> Any:
    try:
        from models import Project  # type: ignore

        return Project
    except Exception:
        pass

    try:
        from models.projects import Project  # type: ignore

        return Project
    except Exception as exc:
        raise RuntimeError("Project model could not be imported.") from exc


def _import_optional_model(name: str) -> Any:
    try:
        import models  # type: ignore

        model = getattr(models, name, None)
        if model is not None:
            return model
    except Exception:
        pass

    return None


def _model_columns(model_cls: Any) -> Tuple[str, ...]:
    try:
        table = getattr(model_cls, "__table__", None)
        if table is not None:
            return tuple(str(column.name) for column in table.columns)
    except Exception:
        pass
    return tuple()


def _model_has_field(model_cls: Any, field_name: str) -> bool:
    if not field_name:
        return False
    if hasattr(model_cls, field_name):
        return True
    return field_name in _model_columns(model_cls)


def _all_existing_fields(model_cls: Any, fields: Sequence[str]) -> Tuple[str, ...]:
    return tuple(field for field in fields if _model_has_field(model_cls, field))


def _first_existing_field(model_cls: Any, fields: Sequence[str]) -> Optional[str]:
    for field in fields:
        if _model_has_field(model_cls, field):
            return field
    return None


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, field_name, default)
    except Exception:
        return default


def _set_field(obj: Any, field_name: str, value: Any, *, only_if_exists: bool = True) -> bool:
    if not field_name:
        return False

    if only_if_exists:
        try:
            model_cls = obj.__class__
            if not _model_has_field(model_cls, field_name):
                return False
        except Exception:
            return False

    try:
        setattr(obj, field_name, value)
        return True
    except Exception:
        return False


def _set_first_existing(obj: Any, fields: Sequence[str], value: Any) -> Optional[str]:
    model_cls = obj.__class__
    for field_name in fields:
        if _model_has_field(model_cls, field_name):
            if _set_field(obj, field_name, value):
                return field_name
    return None


def _set_all_existing(obj: Any, fields: Sequence[str], value: Any) -> Tuple[str, ...]:
    model_cls = obj.__class__
    changed = []
    for field_name in fields:
        if _model_has_field(model_cls, field_name):
            if _set_field(obj, field_name, value):
                changed.append(field_name)
    return tuple(changed)


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _get_json_meta(project: Any) -> Dict[str, Any]:
    model_cls = project.__class__
    for field_name in _all_existing_fields(model_cls, JSON_META_FIELDS):
        value = _get_field(project, field_name)
        meta = _safe_json_dict(value)
        if meta:
            return meta
    return {}


def _set_json_meta(project: Any, meta: Mapping[str, Any]) -> Tuple[str, ...]:
    model_cls = project.__class__
    changed = []
    meta_dict = dict(meta)

    for field_name in _all_existing_fields(model_cls, JSON_META_FIELDS):
        existing = _safe_json_dict(_get_field(project, field_name))
        merged = dict(existing)
        merged.update(meta_dict)

        if _set_field(project, field_name, merged):
            changed.append(field_name)

    return tuple(changed)


def _get_demo_meta(project: Any) -> Dict[str, Any]:
    meta = _get_json_meta(project)
    demo = _safe_json_dict(meta.get(DEMO_META_KEY))
    return demo


def _set_demo_meta(
    project: Any,
    *,
    identity: "DemoIdentity",
    expires_at: datetime,
    created: bool,
    reason: Optional[str] = None,
) -> Tuple[str, ...]:
    meta = _get_json_meta(project)
    demo = _safe_json_dict(meta.get(DEMO_META_KEY))

    demo.update(
        {
            "enabled": True,
            "source": DEMO_SOURCE,
            "identity_type": identity.identity_type,
            "identity_hash": identity.identity_hash,
            "identity_present": bool(identity.raw_identity),
            "raw_identity_kind": identity.raw_identity_kind,
            "demo_session_id": identity.demo_session_id,
            "public_id": identity.public_id,
            "expires_at": _datetime_to_iso(expires_at),
            "ttl_seconds": identity.ttl_seconds,
            "created": bool(created),
            "updated_at": _datetime_to_iso(_utcnow()),
        }
    )

    if reason:
        demo["reason"] = reason

    if "created_at" not in demo:
        demo["created_at"] = _datetime_to_iso(_utcnow())

    meta[DEMO_META_KEY] = demo
    return _set_json_meta(project, meta)


def _query() -> Any:
    Project = _import_project_model()
    db = _import_db()

    query_obj = getattr(Project, "query", None)
    if query_obj is not None:
        return query_obj

    return db.session.query(Project)


def _query_first_by(field_name: str, value: Any) -> Any:
    if value is None or value == "":
        return None

    Project = _import_project_model()

    if not _model_has_field(Project, field_name):
        return None

    try:
        return _query().filter(getattr(Project, field_name) == value).first()
    except Exception:
        LOGGER.debug("Demo project lookup failed for field=%s", field_name, exc_info=True)
        return None


def _query_candidates_for_scan(limit: int = MAX_SCAN_DEMO_PROJECTS) -> Sequence[Any]:
    Project = _import_project_model()

    query = _query()

    try:
        status_field = _first_existing_field(Project, STATUS_FIELDS)
        if status_field:
            status_col = getattr(Project, status_field)
            query = query.filter(status_col.in_(["active", "demo", "draft", "configured"]))
    except Exception:
        pass

    try:
        deleted_field = _first_existing_field(Project, SOFT_DELETE_FIELDS)
        if deleted_field:
            query = query.filter(getattr(Project, deleted_field).is_(None))
    except Exception:
        pass

    try:
        created_field = _first_existing_field(Project, CREATED_AT_FIELDS)
        if created_field:
            query = query.order_by(getattr(Project, created_field).desc())
    except Exception:
        pass

    try:
        return query.limit(limit).all()
    except Exception:
        LOGGER.debug("Demo project scan query failed.", exc_info=True)
        return []


def _flush_or_commit(commit: bool) -> None:
    db = _import_db()
    if commit:
        db.session.commit()
    else:
        db.session.flush()


def _rollback_safely() -> None:
    try:
        db = _import_db()
        db.session.rollback()
    except Exception:
        pass


def _project_id(project: Any) -> Optional[int]:
    try:
        value = getattr(project, "id", None)
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _project_public_id(project: Any) -> Optional[str]:
    for field_name in PUBLIC_ID_FIELDS:
        value = _safe_str(_get_field(project, field_name))
        if value:
            return value
    return None


def _is_soft_deleted(project: Any) -> bool:
    model_cls = project.__class__
    for field_name in _all_existing_fields(model_cls, SOFT_DELETE_FIELDS):
        if _get_field(project, field_name) is not None:
            return True
    return False


@dataclass
class DemoIdentity:
    """
    Stabile Demo-Identität für einen Guest-Kontext.

    raw_identity:
        z. B. client_identity.id oder subject.id=cid_...

    identity_hash:
        wird für Suche/Public-ID verwendet, damit nicht zwingend raw cid in überall
        lesbaren Feldern auftauchen muss.

    public_id:
        deterministische public_id für Demo-Projekt, falls Project.public_id existiert.
    """

    raw_identity: Optional[str]
    raw_identity_kind: str
    identity_type: str
    identity_hash: str
    public_id: str
    demo_session_id: str
    ttl_seconds: int
    expires_at: datetime
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_identity_present": bool(self.raw_identity),
            "raw_identity_kind": self.raw_identity_kind,
            "identity_type": self.identity_type,
            "identity_hash": self.identity_hash,
            "public_id": self.public_id,
            "demo_session_id": self.demo_session_id,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": _datetime_to_iso(self.expires_at),
            "warnings": list(self.warnings),
        }


@dataclass
class DemoProjectResult:
    ok: bool
    reason: str
    project: Any = None
    project_id: Optional[int] = None
    project_public_id: Optional[str] = None
    created: bool = False
    reused: bool = False
    expired: bool = False
    cleaned: int = 0
    demo_identity: Optional[DemoIdentity] = None
    expires_at: Optional[datetime] = None
    ttl_seconds: int = DEFAULT_DEMO_PROJECT_TTL_SECONDS
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    error: Optional[str] = None
    context: Optional[AuthContext] = None

    @property
    def denied(self) -> bool:
        return not self.ok

    def to_dict(self, include_context: bool = False) -> Dict[str, Any]:
        data = {
            "ok": self.ok,
            "reason": self.reason,
            "project_id": self.project_id,
            "project_public_id": self.project_public_id,
            "created": self.created,
            "reused": self.reused,
            "expired": self.expired,
            "cleaned": self.cleaned,
            "expires_at": _datetime_to_iso(self.expires_at),
            "ttl_seconds": self.ttl_seconds,
            "warnings": list(self.warnings),
            "error": self.error,
            "demo_identity": self.demo_identity.to_dict() if self.demo_identity else None,
        }

        if include_context and self.context is not None:
            data["auth"] = self.context.to_public_dict(include_raw=False)

        return data


def can_use_demo_project(context: Optional[AuthContext]) -> Tuple[bool, str]:
    if context is None:
        return False, "auth_context_missing"

    if context.blocked:
        return False, context.blocked_reason or context.reason_code or "blocked"

    if context.authenticated:
        return False, "authenticated_user_uses_persistent_projects"

    if not context.can_demo:
        return False, "demo_project_access_missing"

    if not is_demo_context(context):
        return False, "not_demo_context"

    return True, "ok"


def build_demo_identity(context: AuthContext) -> DemoIdentity:
    ttl_seconds = _demo_ttl_seconds()
    expires_at = _utcnow() + timedelta(seconds=ttl_seconds)

    warnings = []

    candidates: Tuple[Tuple[str, Optional[str]], ...] = (
        ("client_identity.id", _safe_str(context.client_identity.get("id")) or None),
        ("client_identity.client_identity_id", _safe_str(context.client_identity.get("client_identity_id")) or None),
        ("client_identity.public_id", _safe_str(context.client_identity.get("public_id")) or None),
        ("subject.id", context.subject_id if context.subject_type in {"guest", "client_identity"} else None),
        ("session.client_identity_id", _safe_str(context.session.get("client_identity_id")) or None),
    )

    raw_identity = None
    raw_identity_kind = "missing"

    for kind, value in candidates:
        if value:
            raw_identity = value
            raw_identity_kind = kind
            break

    if raw_identity:
        identity_type = "client_identity"
        identity_hash = _demo_identity_hash(raw_identity)
    else:
        # Fallback: nicht ideal, aber verhindert geteilte globale Demo-Projekte.
        # Falls Auth korrekt läuft, sollte immer eine ClientIdentity vorhanden sein.
        request_fingerprint = None
        try:
            if has_request_context() and request is not None:
                request_fingerprint = "|".join(
                    [
                        _safe_str(request.headers.get("User-Agent")),
                        _safe_str(request.headers.get("Accept-Language")),
                        _safe_str(request.remote_addr),
                        uuid.uuid4().hex,
                    ]
                )
        except Exception:
            request_fingerprint = uuid.uuid4().hex

        raw_identity = None
        raw_identity_kind = "ephemeral_request"
        identity_type = "ephemeral"
        identity_hash = _demo_identity_hash(request_fingerprint or uuid.uuid4().hex)
        warnings.append("client_identity_missing_ephemeral_demo_identity_created")

    public_id = _demo_public_id(identity_hash)
    demo_session_id = f"demo_sess_{identity_hash[:24]}"

    return DemoIdentity(
        raw_identity=raw_identity,
        raw_identity_kind=raw_identity_kind,
        identity_type=identity_type,
        identity_hash=identity_hash,
        public_id=public_id,
        demo_session_id=demo_session_id,
        ttl_seconds=ttl_seconds,
        expires_at=expires_at,
        warnings=tuple(warnings),
    )


def is_demo_project(project: Any) -> bool:
    if project is None:
        return False

    model_cls = project.__class__

    for field_name in _all_existing_fields(model_cls, DEMO_FLAG_FIELDS):
        if _safe_bool(_get_field(project, field_name), default=False):
            return True

    scope_field = _first_existing_field(model_cls, PROJECT_SCOPE_FIELDS)
    if scope_field and _lower(_get_field(project, scope_field)) == "demo":
        return True

    demo_meta = _get_demo_meta(project)
    if _safe_bool(demo_meta.get("enabled"), default=False):
        return True

    if _safe_str(demo_meta.get("source")) == DEMO_SOURCE:
        return True

    public_id = _project_public_id(project)
    if public_id and public_id.startswith("demo_"):
        return True

    return False


def get_demo_project_expires_at(project: Any) -> Optional[datetime]:
    if project is None:
        return None

    model_cls = project.__class__

    for field_name in _all_existing_fields(model_cls, DEMO_EXPIRES_AT_FIELDS):
        value = _safe_datetime(_get_field(project, field_name))
        if value is not None:
            return value

    demo_meta = _get_demo_meta(project)
    return _safe_datetime(demo_meta.get("expires_at"))


def is_demo_project_expired(project: Any, *, now: Optional[datetime] = None) -> bool:
    expires_at = get_demo_project_expires_at(project)
    if expires_at is None:
        return False

    current = now or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at <= current


def is_demo_project_for_identity(project: Any, identity: DemoIdentity) -> bool:
    if project is None:
        return False

    if not is_demo_project(project):
        return False

    model_cls = project.__class__

    public_id = _project_public_id(project)
    if public_id and public_id == identity.public_id:
        return True

    for field_name in _all_existing_fields(model_cls, DEMO_CLIENT_IDENTITY_FIELDS):
        value = _safe_str(_get_field(project, field_name))
        if identity.raw_identity and value == identity.raw_identity:
            return True
        if value == identity.identity_hash:
            return True

    for field_name in _all_existing_fields(model_cls, DEMO_SESSION_FIELDS):
        value = _safe_str(_get_field(project, field_name))
        if value == identity.demo_session_id:
            return True

    demo_meta = _get_demo_meta(project)
    if _safe_str(demo_meta.get("identity_hash")) == identity.identity_hash:
        return True

    if _safe_str(demo_meta.get("demo_session_id")) == identity.demo_session_id:
        return True

    return False


def mark_project_as_demo(
    project: Any,
    identity: DemoIdentity,
    *,
    expires_at: Optional[datetime] = None,
    created: bool = False,
    reason: Optional[str] = None,
) -> Tuple[str, ...]:
    """
    Markiert ein Project defensiv als Demo-Projekt.

    Nutzt explizite Felder, wenn vorhanden, und zusätzlich JSON-Meta.
    """
    if project is None:
        return tuple()

    expires = expires_at or identity.expires_at
    changed = []

    changed.extend(_set_all_existing(project, DEMO_FLAG_FIELDS, True))

    if identity.raw_identity:
        changed.extend(_set_all_existing(project, DEMO_CLIENT_IDENTITY_FIELDS, identity.raw_identity))
    else:
        changed.extend(_set_all_existing(project, DEMO_CLIENT_IDENTITY_FIELDS, identity.identity_hash))

    changed.extend(_set_all_existing(project, DEMO_SESSION_FIELDS, identity.demo_session_id))
    changed.extend(_set_all_existing(project, DEMO_EXPIRES_AT_FIELDS, expires))
    changed.extend(_set_all_existing(project, PROJECT_SCOPE_FIELDS, "demo"))

    # Echte User-/Account-Zuordnung für Demo bewusst leeren, wenn Felder vorhanden sind.
    for field_name in _all_existing_fields(project.__class__, OWNER_USER_ID_FIELDS):
        _set_field(project, field_name, None)

    changed.extend(_set_all_existing(project, AUTH_ACCOUNT_ID_FIELDS, None))

    changed.extend(_set_demo_meta(project, identity=identity, expires_at=expires, created=created, reason=reason))

    return tuple(dict.fromkeys(changed))


def _find_demo_project_by_identity(identity: DemoIdentity) -> Any:
    Project = _import_project_model()

    # 1. Deterministische public_id.
    for field_name in PUBLIC_ID_FIELDS:
        found = _query_first_by(field_name, identity.public_id)
        if found is not None and is_demo_project_for_identity(found, identity):
            return found

    # 2. Explizite Demo-ClientIdentity-Felder.
    for field_name in DEMO_CLIENT_IDENTITY_FIELDS:
        if _model_has_field(Project, field_name):
            if identity.raw_identity:
                found = _query_first_by(field_name, identity.raw_identity)
                if found is not None and is_demo_project_for_identity(found, identity):
                    return found

            found = _query_first_by(field_name, identity.identity_hash)
            if found is not None and is_demo_project_for_identity(found, identity):
                return found

    # 3. Demo-Session-Felder.
    for field_name in DEMO_SESSION_FIELDS:
        found = _query_first_by(field_name, identity.demo_session_id)
        if found is not None and is_demo_project_for_identity(found, identity):
            return found

    # 4. JSON-Meta-Fallback.
    for project in _query_candidates_for_scan():
        if is_demo_project_for_identity(project, identity):
            return project

    return None


def _create_demo_project(identity: DemoIdentity, context: AuthContext) -> Any:
    Project = _import_project_model()
    project = Project()

    public_id = identity.public_id
    now = _utcnow()

    _set_first_existing(project, PUBLIC_ID_FIELDS, public_id)
    _set_first_existing(project, NAME_FIELDS, _demo_project_name())
    _set_first_existing(project, DESCRIPTION_FIELDS, _demo_project_description())
    _set_first_existing(project, ADDRESS_TEXT_FIELDS, _demo_project_address_text())
    _set_first_existing(project, VISIBILITY_FIELDS, DEFAULT_DEMO_PROJECT_VISIBILITY)
    _set_first_existing(project, IS_PUBLIC_FIELDS, False)
    _set_first_existing(project, STATUS_FIELDS, DEFAULT_DEMO_PROJECT_STATUS)
    _set_first_existing(project, SETUP_STATUS_FIELDS, DEFAULT_DEMO_PROJECT_SETUP_STATUS)
    _set_first_existing(project, SETUP_COMPLETED_AT_FIELDS, now)

    # Demo hat keinen lokalen persistenten Owner.
    for field_name in _all_existing_fields(Project, OWNER_USER_ID_FIELDS):
        _set_field(project, field_name, None)

    # Conversation ist optional. Wenn die Spalte non-null ist und kein Default existiert,
    # muss current project_service später ggf. die Conversation anlegen.
    # Diese Datei setzt nur, wenn es gefahrlos möglich ist.
    _set_first_existing(project, CREATED_AT_FIELDS, now)
    _set_first_existing(project, UPDATED_AT_FIELDS, now)

    mark_project_as_demo(project, identity, expires_at=identity.expires_at, created=True, reason="created")

    return project


def _refresh_demo_project(project: Any, identity: DemoIdentity, context: AuthContext) -> Tuple[bool, Tuple[str, ...]]:
    """
    Aktualisiert ein bestehendes Demo-Projekt defensiv.

    Aktuell verlängern wir die TTL beim Wiederöffnen nicht automatisch über eine neue Stunde hinaus,
    wenn bereits ein gültiges expires_at existiert. Dadurch bleibt Demo kalkulierbar.
    Wenn Demo-Projekt abgelaufen ist, wird später neu erzeugt oder ersetzt.
    """
    changed = []
    now = _utcnow()

    # Sicherstellen, dass Minimalfelder weiterhin gesetzt sind.
    for fields, value in (
        (NAME_FIELDS, _demo_project_name()),
        (DESCRIPTION_FIELDS, _demo_project_description()),
        (ADDRESS_TEXT_FIELDS, _demo_project_address_text()),
        (VISIBILITY_FIELDS, DEFAULT_DEMO_PROJECT_VISIBILITY),
        (IS_PUBLIC_FIELDS, False),
        (STATUS_FIELDS, DEFAULT_DEMO_PROJECT_STATUS),
        (SETUP_STATUS_FIELDS, DEFAULT_DEMO_PROJECT_SETUP_STATUS),
    ):
        model_cls = project.__class__
        field_name = _first_existing_field(model_cls, fields)
        if field_name:
            current = _get_field(project, field_name)
            if current in {None, ""}:
                if _set_field(project, field_name, value):
                    changed.append(field_name)

    # Bei bestehenden gültigen Demo-Projekten expires_at nicht unnötig verlängern.
    expires_at = get_demo_project_expires_at(project)
    if expires_at is None or expires_at <= now:
        expires_at = identity.expires_at

    changed.extend(mark_project_as_demo(project, identity, expires_at=expires_at, created=False, reason="refreshed"))

    _set_first_existing(project, UPDATED_AT_FIELDS, now)

    return bool(changed), tuple(dict.fromkeys(changed))


def _best_effort_provision_demo_chunk(project: Any) -> Tuple[bool, Optional[str]]:
    """
    Optionales Chunk-Provisioning für Demo-Projekte.

    Ziel:
    - Demo-3D soll denselben App→Chunk→Editor-Pfad nutzen können.
    - Wenn Chunk nicht erreichbar ist, darf Demo-Projekt trotzdem existieren.
    - Keine harte Abhängigkeit auf konkrete Signaturen von chunk_client.py.
    """
    if not _demo_chunk_provisioning_enabled():
        return False, "disabled"

    public_id = _project_public_id(project)
    if not public_id:
        return False, "missing_project_public_id"

    try:
        import services.chunk_client as chunk_client  # type: ignore
    except Exception:
        try:
            from . import chunk_client  # type: ignore
        except Exception:
            return False, "chunk_client_unavailable"

    function_names = (
        "ensure_chunk_project_for_app_project",
        "ensure_project_graph",
        "ensure_chunk_project",
        "provision_project",
        "ensure_app_project",
    )

    for function_name in function_names:
        fn = getattr(chunk_client, function_name, None)
        if not callable(fn):
            continue

        try:
            response = fn(public_id)
            _apply_chunk_response_to_project(project, response)
            return True, function_name
        except TypeError:
            try:
                response = fn(project)
                _apply_chunk_response_to_project(project, response)
                return True, function_name
            except Exception:
                continue
        except Exception:
            LOGGER.debug("Demo chunk provisioning failed via %s.", function_name, exc_info=True)
            continue

    return False, "no_supported_chunk_client_function"


def _apply_chunk_response_to_project(project: Any, response: Any) -> None:
    if not isinstance(response, Mapping):
        return

    possible = [
        response,
        response.get("data") if isinstance(response.get("data"), Mapping) else {},
        response.get("project") if isinstance(response.get("project"), Mapping) else {},
        response.get("chunk") if isinstance(response.get("chunk"), Mapping) else {},
    ]

    values: Dict[str, Any] = {}
    for item in possible:
        if not isinstance(item, Mapping):
            continue
        for key in (
            "chunk_project_id",
            "chunkProjectId",
            "project_id",
            "chunk_universe_id",
            "chunkUniverseId",
            "universe_id",
            "chunk_world_id",
            "chunkWorldId",
            "world_id",
        ):
            if key in item and item.get(key):
                values[key] = item.get(key)

    mapping = {
        "chunk_project_id": values.get("chunk_project_id") or values.get("chunkProjectId") or values.get("project_id"),
        "chunk_universe_id": values.get("chunk_universe_id") or values.get("chunkUniverseId") or values.get("universe_id"),
        "chunk_world_id": values.get("chunk_world_id") or values.get("chunkWorldId") or values.get("world_id") or "world_spawn",
    }

    for field_name, value in mapping.items():
        if value and _model_has_field(project.__class__, field_name):
            current = _safe_str(_get_field(project, field_name))
            if not current:
                _set_field(project, field_name, value)


def ensure_demo_project_for_context(
    context: Optional[AuthContext] = None,
    *,
    reset: bool = False,
    commit: bool = True,
    provision_chunk: Optional[bool] = None,
    cleanup_expired: Optional[bool] = None,
) -> DemoProjectResult:
    """
    Stellt für einen Guest-Kontext ein temporäres Demo-Projekt bereit.

    reset:
        Wenn True, wird ein bestehendes Demo-Projekt für dieselbe Identität als abgelaufen
        markiert und ein neues Projekt vorbereitet, soweit das Schema dies erlaubt.

    commit:
        Wenn True, wird die DB-Session committed.

    provision_chunk:
        Wenn True, wird best-effort Chunk-Provisioning versucht.
        Default kommt aus Config VECTOPLAN_DEMO_PROJECT_CHUNK_PROVISIONING.

    cleanup_expired:
        Wenn True, werden abgelaufene Demo-Projekte opportunistisch bereinigt.
        Default kommt aus Config VECTOPLAN_DEMO_PROJECT_CLEANUP_ON_ENSURE.
    """
    context = context or get_current_auth_context(minimal=True)

    allowed, denied_reason = can_use_demo_project(context)
    if not allowed:
        return DemoProjectResult(
            ok=False,
            reason=denied_reason,
            context=context,
        )

    identity = build_demo_identity(context)
    warnings = list(identity.warnings)
    cleanup_count = 0

    if cleanup_expired is None:
        cleanup_expired = _demo_cleanup_on_ensure_enabled()

    if cleanup_expired:
        cleanup_result = cleanup_expired_demo_projects(commit=False, limit=100)
        cleanup_count = cleanup_result.cleaned
        warnings.extend(cleanup_result.warnings)

    try:
        db = _import_db()

        existing = _find_demo_project_by_identity(identity)

        if existing is not None and reset:
            expire_demo_project(existing, commit=False, reason="reset_requested")
            existing = None

        if existing is not None and not is_demo_project_expired(existing) and not _is_soft_deleted(existing):
            changed, changed_fields = _refresh_demo_project(existing, identity, context)

            if provision_chunk is None:
                provision_chunk = _demo_chunk_provisioning_enabled()

            if provision_chunk:
                provisioned, provision_reason = _best_effort_provision_demo_chunk(existing)
                if not provisioned:
                    warnings.append(f"chunk_provisioning_not_applied:{provision_reason}")

            if changed:
                _flush_or_commit(commit=commit)
            elif commit:
                # Wenn cleanup vorher etwas geändert hat, commit trotzdem.
                try:
                    db.session.commit()
                except Exception:
                    pass

            return DemoProjectResult(
                ok=True,
                reason="reused",
                project=existing,
                project_id=_project_id(existing),
                project_public_id=_project_public_id(existing),
                created=False,
                reused=True,
                cleaned=cleanup_count,
                demo_identity=identity,
                expires_at=get_demo_project_expires_at(existing),
                ttl_seconds=identity.ttl_seconds,
                warnings=tuple(dict.fromkeys(warnings)),
                context=context,
            )

        project = _create_demo_project(identity, context)
        db.session.add(project)

        try:
            db.session.flush()
        except IntegrityError:
            # Paralleler Request oder public_id-Kollision.
            _rollback_safely()
            existing = _find_demo_project_by_identity(identity)
            if existing is not None and not is_demo_project_expired(existing):
                return DemoProjectResult(
                    ok=True,
                    reason="reused_after_integrity_retry",
                    project=existing,
                    project_id=_project_id(existing),
                    project_public_id=_project_public_id(existing),
                    created=False,
                    reused=True,
                    cleaned=cleanup_count,
                    demo_identity=identity,
                    expires_at=get_demo_project_expires_at(existing),
                    ttl_seconds=identity.ttl_seconds,
                    warnings=tuple(dict.fromkeys(warnings + ["integrity_retry_resolved"])),
                    context=context,
                )
            raise

        if provision_chunk is None:
            provision_chunk = _demo_chunk_provisioning_enabled()

        if provision_chunk:
            provisioned, provision_reason = _best_effort_provision_demo_chunk(project)
            if not provisioned:
                warnings.append(f"chunk_provisioning_not_applied:{provision_reason}")

        _flush_or_commit(commit=commit)

        return DemoProjectResult(
            ok=True,
            reason="created",
            project=project,
            project_id=_project_id(project),
            project_public_id=_project_public_id(project),
            created=True,
            reused=False,
            cleaned=cleanup_count,
            demo_identity=identity,
            expires_at=get_demo_project_expires_at(project),
            ttl_seconds=identity.ttl_seconds,
            warnings=tuple(dict.fromkeys(warnings)),
            context=context,
        )

    except Exception as exc:
        _rollback_safely()
        LOGGER.exception("Failed to ensure demo project: %s", exc)
        return DemoProjectResult(
            ok=False,
            reason="demo_project_failed",
            demo_identity=identity,
            ttl_seconds=identity.ttl_seconds,
            expires_at=identity.expires_at,
            warnings=tuple(dict.fromkeys(warnings)),
            error=str(exc),
            context=context,
        )


def get_current_demo_project(
    context: Optional[AuthContext] = None,
    *,
    create: bool = True,
    commit: bool = True,
) -> DemoProjectResult:
    """
    Gibt aktuelles Demo-Projekt zurück.

    create=True:
        erstellt ein Demo-Projekt, falls keins existiert.

    create=False:
        sucht nur.
    """
    context = context or get_current_auth_context(minimal=True)

    allowed, denied_reason = can_use_demo_project(context)
    if not allowed:
        return DemoProjectResult(
            ok=False,
            reason=denied_reason,
            context=context,
        )

    identity = build_demo_identity(context)

    if create:
        return ensure_demo_project_for_context(
            context=context,
            reset=False,
            commit=commit,
        )

    project = _find_demo_project_by_identity(identity)
    if project is None:
        return DemoProjectResult(
            ok=False,
            reason="demo_project_not_found",
            demo_identity=identity,
            ttl_seconds=identity.ttl_seconds,
            expires_at=identity.expires_at,
            context=context,
        )

    expired = is_demo_project_expired(project)

    return DemoProjectResult(
        ok=not expired,
        reason="expired" if expired else "found",
        project=project if not expired else None,
        project_id=_project_id(project),
        project_public_id=_project_public_id(project),
        reused=not expired,
        expired=expired,
        demo_identity=identity,
        expires_at=get_demo_project_expires_at(project),
        ttl_seconds=identity.ttl_seconds,
        context=context,
    )


def expire_demo_project(
    project: Any,
    *,
    commit: bool = True,
    reason: str = "expired",
) -> DemoProjectResult:
    """
    Markiert ein Demo-Projekt als abgelaufen/archiviert, ohne echte Projekte zu berühren.
    """
    if project is None:
        return DemoProjectResult(
            ok=False,
            reason="project_missing",
        )

    if not is_demo_project(project):
        return DemoProjectResult(
            ok=False,
            reason="not_demo_project",
            project=project,
            project_id=_project_id(project),
            project_public_id=_project_public_id(project),
        )

    try:
        now = _utcnow()
        model_cls = project.__class__

        for field_name in _all_existing_fields(model_cls, DEMO_EXPIRES_AT_FIELDS):
            _set_field(project, field_name, now)

        for field_name in _all_existing_fields(model_cls, STATUS_FIELDS):
            current = _lower(_get_field(project, field_name))
            if current not in {"deleted", "archived"}:
                _set_field(project, field_name, "expired")

        demo_meta = _get_demo_meta(project)
        if demo_meta:
            meta = _get_json_meta(project)
            demo_meta["expires_at"] = _datetime_to_iso(now)
            demo_meta["expired_at"] = _datetime_to_iso(now)
            demo_meta["expired_reason"] = reason
            demo_meta["enabled"] = True
            meta[DEMO_META_KEY] = demo_meta
            _set_json_meta(project, meta)

        _set_first_existing(project, UPDATED_AT_FIELDS, now)

        _flush_or_commit(commit=commit)

        return DemoProjectResult(
            ok=True,
            reason=reason,
            project=project,
            project_id=_project_id(project),
            project_public_id=_project_public_id(project),
            expired=True,
            expires_at=now,
        )

    except Exception as exc:
        _rollback_safely()
        LOGGER.exception("Failed to expire demo project: %s", exc)
        return DemoProjectResult(
            ok=False,
            reason="expire_demo_project_failed",
            project=project,
            project_id=_project_id(project),
            project_public_id=_project_public_id(project),
            error=str(exc),
        )


def cleanup_expired_demo_projects(
    *,
    commit: bool = True,
    hard_delete: bool = False,
    limit: int = MAX_SCAN_DEMO_PROJECTS,
) -> DemoProjectResult:
    """
    Bereinigt abgelaufene Demo-Projekte.

    hard_delete=False:
        bevorzugt Soft-Delete/Status-Update.

    hard_delete=True:
        löscht DB-Zeilen. Nur für Dev/Tests empfohlen.
    """
    try:
        db = _import_db()
        now = _utcnow()
        cleaned = 0
        warnings = []

        projects = list(_query_candidates_for_scan(limit=limit))

        for project in projects:
            if not is_demo_project(project):
                continue
            if not is_demo_project_expired(project, now=now):
                continue

            if hard_delete:
                try:
                    db.session.delete(project)
                    cleaned += 1
                except Exception:
                    warnings.append(f"hard_delete_failed:{_project_public_id(project) or _project_id(project)}")
                    continue
            else:
                model_cls = project.__class__
                changed = False

                # Soft delete, wenn möglich.
                deleted_field = _first_existing_field(model_cls, SOFT_DELETE_FIELDS)
                if deleted_field:
                    if _set_field(project, deleted_field, now):
                        changed = True

                # Zusätzlich Status setzen.
                for field_name in _all_existing_fields(model_cls, STATUS_FIELDS):
                    if _set_field(project, field_name, "expired"):
                        changed = True

                demo_meta = _get_demo_meta(project)
                if demo_meta:
                    meta = _get_json_meta(project)
                    demo_meta["expired_at"] = _datetime_to_iso(now)
                    demo_meta["cleanup_seen_at"] = _datetime_to_iso(now)
                    demo_meta["status"] = "expired"
                    meta[DEMO_META_KEY] = demo_meta
                    _set_json_meta(project, meta)
                    changed = True

                if changed:
                    cleaned += 1

        if cleaned or commit:
            _flush_or_commit(commit=commit)

        return DemoProjectResult(
            ok=True,
            reason="cleanup_completed",
            cleaned=cleaned,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    except Exception as exc:
        _rollback_safely()
        LOGGER.exception("Failed to cleanup expired demo projects: %s", exc)
        return DemoProjectResult(
            ok=False,
            reason="cleanup_failed",
            error=str(exc),
        )


def reset_current_demo_project(
    context: Optional[AuthContext] = None,
    *,
    commit: bool = True,
) -> DemoProjectResult:
    """
    Setzt das aktuelle Demo-Projekt zurück.

    Praktisch für:
    - Demo neu starten
    - späteren Button "Demo zurücksetzen"
    - Tests
    """
    context = context or get_current_auth_context(minimal=True)

    allowed, denied_reason = can_use_demo_project(context)
    if not allowed:
        return DemoProjectResult(
            ok=False,
            reason=denied_reason,
            context=context,
        )

    identity = build_demo_identity(context)
    project = _find_demo_project_by_identity(identity)

    if project is not None:
        expire_demo_project(project, commit=False, reason="reset")

    return ensure_demo_project_for_context(
        context=context,
        reset=True,
        commit=commit,
    )


def is_project_accessible_as_demo(
    project: Any,
    context: Optional[AuthContext] = None,
) -> Tuple[bool, str]:
    """
    Prüft, ob ein konkretes Projekt als Demo-Projekt im aktuellen Guest-Kontext zugänglich ist.
    """
    if project is None:
        return False, "project_missing"

    context = context or get_current_auth_context(minimal=True)

    allowed, denied_reason = can_use_demo_project(context)
    if not allowed:
        return False, denied_reason

    if not is_demo_project(project):
        return False, "not_demo_project"

    if is_demo_project_expired(project):
        return False, "demo_project_expired"

    identity = build_demo_identity(context)
    if not is_demo_project_for_identity(project, identity):
        return False, "demo_project_identity_mismatch"

    return True, "ok"


def demo_project_public_payload(project: Any, context: Optional[AuthContext] = None) -> Dict[str, Any]:
    """
    Kleine öffentliche Demo-Projekt-Zusammenfassung für Templates/JSON.
    """
    expires_at = get_demo_project_expires_at(project)
    now = _utcnow()
    remaining_seconds = None

    if expires_at:
        remaining_seconds = max(0, int((expires_at - now).total_seconds()))

    return {
        "is_demo": is_demo_project(project),
        "project_id": _project_id(project),
        "project_public_id": _project_public_id(project),
        "expires_at": _datetime_to_iso(expires_at),
        "remaining_seconds": remaining_seconds,
        "expired": is_demo_project_expired(project),
        "can_reset": bool(context and context.can_demo and not context.authenticated and not context.blocked),
        "message": "Demo-Projekt. Änderungen werden temporär gespeichert und später gelöscht.",
    }


def demo_project_model_support_report() -> Dict[str, Any]:
    """
    Diagnose: Welche Demo-Felder unterstützt das aktuelle Project-Model bereits?
    """
    try:
        Project = _import_project_model()
        columns = _model_columns(Project)

        return {
            "ok": True,
            "model": f"{Project.__module__}.{Project.__name__}",
            "columns": list(columns),
            "supports_is_demo": bool(_all_existing_fields(Project, DEMO_FLAG_FIELDS)),
            "supports_demo_client_identity": bool(_all_existing_fields(Project, DEMO_CLIENT_IDENTITY_FIELDS)),
            "supports_demo_session": bool(_all_existing_fields(Project, DEMO_SESSION_FIELDS)),
            "supports_demo_expires_at": bool(_all_existing_fields(Project, DEMO_EXPIRES_AT_FIELDS)),
            "supports_project_scope": bool(_all_existing_fields(Project, PROJECT_SCOPE_FIELDS)),
            "supports_json_meta": bool(_all_existing_fields(Project, JSON_META_FIELDS)),
            "supports_public_id": bool(_all_existing_fields(Project, PUBLIC_ID_FIELDS)),
            "demo_flag_fields": list(_all_existing_fields(Project, DEMO_FLAG_FIELDS)),
            "demo_client_identity_fields": list(_all_existing_fields(Project, DEMO_CLIENT_IDENTITY_FIELDS)),
            "demo_session_fields": list(_all_existing_fields(Project, DEMO_SESSION_FIELDS)),
            "demo_expires_at_fields": list(_all_existing_fields(Project, DEMO_EXPIRES_AT_FIELDS)),
            "json_meta_fields": list(_all_existing_fields(Project, JSON_META_FIELDS)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


__all__ = [
    "DEFAULT_DEMO_PROJECT_ADDRESS_TEXT",
    "DEFAULT_DEMO_PROJECT_DESCRIPTION",
    "DEFAULT_DEMO_PROJECT_NAME",
    "DEFAULT_DEMO_PROJECT_SETUP_STATUS",
    "DEFAULT_DEMO_PROJECT_STATUS",
    "DEFAULT_DEMO_PROJECT_TTL_SECONDS",
    "DEFAULT_DEMO_PROJECT_VISIBILITY",
    "DEMO_CLIENT_IDENTITY_FIELDS",
    "DEMO_EXPIRES_AT_FIELDS",
    "DEMO_FLAG_FIELDS",
    "DEMO_META_KEY",
    "DEMO_SESSION_FIELDS",
    "DEMO_SOURCE",
    "DemoIdentity",
    "DemoProjectResult",
    "build_demo_identity",
    "can_use_demo_project",
    "cleanup_expired_demo_projects",
    "demo_project_model_support_report",
    "demo_project_public_payload",
    "ensure_demo_project_for_context",
    "expire_demo_project",
    "get_current_demo_project",
    "get_demo_project_expires_at",
    "is_demo_project",
    "is_demo_project_expired",
    "is_demo_project_for_identity",
    "is_project_accessible_as_demo",
    "mark_project_as_demo",
    "reset_current_demo_project",
]