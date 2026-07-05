# services/vectoplan-app/services/app_user_link_service.py
"""
Lokale AppUser-Verknüpfung zwischen vectoplan-auth und vectoplan-app.

Zweck:
- vectoplan-auth bleibt die kanonische User-/Session-/Plan-/Blocked-Wahrheit.
- vectoplan-app behält lokale AppUser nur als technische Verknüpfung für lokale Tabellen:
  Project.owner_user_id, ProjectMembership.user_id, Audit, lokale Rechte usw.
- Diese Datei stellt sicher, dass ein authentifizierter Auth-User einen lokalen AppUser bekommt.
- Guests/Demo-Kontexte bekommen keinen echten persistenten AppUser-Link.

Wichtige Regeln:
- Nie aus Guest/ClientIdentity einen AppUser erzeugen.
- Nie aus subject.id einen AppUser erzeugen, wenn subject.type != "user".
- AppUser speichert keine Passwörter, keine Cookies, keine Session-Tokens, keine Raw API-Keys.
- Wenn User gebannt/blockiert ist, wird kein neuer AppUser erzeugt.
- Diese Datei ist schema-defensiv, damit sie auch vor der finalen Erweiterung von models/users.py importierbar ist.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple


try:
    from flask import g, has_request_context
except Exception:  # pragma: no cover - Import außerhalb Flask erlauben
    g = None  # type: ignore

    def has_request_context() -> bool:  # type: ignore
        return False


try:
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
except Exception:  # pragma: no cover
    IntegrityError = Exception  # type: ignore
    SQLAlchemyError = Exception  # type: ignore


try:
    from services.auth_context import AuthContext, get_current_auth_context
except Exception:  # pragma: no cover - Package-Import-Fallback
    from .auth_context import AuthContext, get_current_auth_context


LOGGER = logging.getLogger(__name__)


REQUEST_LINK_CACHE_ATTR = "_vectoplan_app_user_link_cache"


AUTH_USER_ID_FIELDS = (
    "auth_user_id",
    "external_auth_user_id",
    "external_user_id",
    "identity_user_id",
    "platform_user_id",
    "auth_subject_id",
    "auth_public_id",
)

AUTH_ACCOUNT_ID_FIELDS = (
    "auth_account_id",
    "account_id",
    "platform_account_id",
)

EMAIL_FIELDS = (
    "email",
    "primary_email",
    "auth_email",
)

USERNAME_FIELDS = (
    "username",
    "handle",
)

DISPLAY_NAME_FIELDS = (
    "display_name",
    "name",
    "full_name",
)

AUTH_STATUS_FIELDS = (
    "auth_status",
    "identity_status",
)

USER_STATUS_FIELDS = (
    "status",
    "state",
)

PLAN_FIELDS = (
    "auth_plan",
    "plan",
    "plan_key",
)

SYNC_TIME_FIELDS = (
    "last_auth_sync_at",
    "auth_synced_at",
    "last_seen_at",
)

JSON_META_FIELDS = (
    "metadata_json",
    "meta_json",
    "settings",
    "profile",
    "auth_meta",
)

BOOLEAN_PLACEHOLDER_FIELDS = (
    "is_placeholder",
    "placeholder",
)

BOOLEAN_SYSTEM_FIELDS = (
    "is_system",
    "system",
)

BOOLEAN_ACTIVE_FIELDS = (
    "is_active",
    "active",
)

ROLE_FIELDS = (
    "role",
    "primary_role",
)

PUBLIC_ID_FIELDS = (
    "public_id",
    "uuid",
    "external_id",
)

SAFE_SYNC_META_KEYS = {
    "auth_user_id",
    "auth_account_id",
    "auth_state",
    "email",
    "display_name",
    "plan",
    "roles",
    "entitlements",
    "synced_at",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


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


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _slug(value: str, fallback: str = "user") -> str:
    text = _safe_str(value).lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    text = text.strip("-._")
    return text[:80] or fallback


def _short_hash(value: str, length: int = 16) -> str:
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()
    return digest[:length]


def _deterministic_public_id(auth_user_id: str) -> str:
    return f"u_auth_{_short_hash(auth_user_id, 20)}"


def _fallback_handle(context: AuthContext) -> str:
    if context.user.username:
        return _slug(context.user.username, fallback="user")
    if context.user.email and "@" in context.user.email:
        return _slug(context.user.email.split("@", 1)[0], fallback="user")
    if context.user_id:
        return f"user-{_short_hash(context.user_id, 8)}"
    return f"user-{uuid.uuid4().hex[:8]}"


def _fallback_display_name(context: AuthContext) -> str:
    if context.user.display_name:
        return context.user.display_name
    if context.user.email:
        return context.user.email
    if context.user.username:
        return context.user.username
    if context.user_id:
        return f"User {_short_hash(context.user_id, 8)}"
    return "User"


def _get_request_cache() -> Optional[MutableMapping[str, "AppUserLinkResult"]]:
    try:
        if has_request_context() and g is not None:
            cache = getattr(g, REQUEST_LINK_CACHE_ATTR, None)
            if cache is None:
                cache = {}
                setattr(g, REQUEST_LINK_CACHE_ATTR, cache)
            return cache
    except Exception:
        return None
    return None


def clear_app_user_link_cache() -> None:
    try:
        if has_request_context() and g is not None:
            setattr(g, REQUEST_LINK_CACHE_ATTR, {})
    except Exception:
        pass


def _import_app_user_model() -> Any:
    try:
        from models import AppUser  # type: ignore

        return AppUser
    except Exception:
        pass

    try:
        from models.users import AppUser  # type: ignore

        return AppUser
    except Exception as exc:
        raise RuntimeError("AppUser model could not be imported.") from exc


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


def _first_existing_field(model_cls: Any, field_names: Sequence[str]) -> Optional[str]:
    for field_name in field_names:
        if _model_has_field(model_cls, field_name):
            return field_name
    return None


def _all_existing_fields(model_cls: Any, field_names: Sequence[str]) -> Tuple[str, ...]:
    return tuple(field_name for field_name in field_names if _model_has_field(model_cls, field_name))


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, field_name, default)
    except Exception:
        return default


def _set_field(obj: Any, field_name: str, value: Any, *, only_if_empty: bool = False) -> bool:
    if not field_name:
        return False

    try:
        if only_if_empty:
            current = getattr(obj, field_name, None)
            if current not in {None, ""}:
                return False

        setattr(obj, field_name, value)
        return True
    except Exception:
        return False


def _set_first_existing(
    obj: Any,
    model_cls: Any,
    fields: Sequence[str],
    value: Any,
    *,
    only_if_empty: bool = False,
) -> Optional[str]:
    for field_name in fields:
        if _model_has_field(model_cls, field_name):
            if _set_field(obj, field_name, value, only_if_empty=only_if_empty):
                return field_name
    return None


def _set_all_existing(
    obj: Any,
    model_cls: Any,
    fields: Sequence[str],
    value: Any,
    *,
    only_if_empty: bool = False,
) -> Tuple[str, ...]:
    changed = []
    for field_name in fields:
        if _model_has_field(model_cls, field_name):
            if _set_field(obj, field_name, value, only_if_empty=only_if_empty):
                changed.append(field_name)
    return tuple(changed)


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _merge_auth_meta(existing: Any, context: AuthContext) -> Dict[str, Any]:
    meta = _safe_json_dict(existing)
    vectoplan_auth = _safe_json_dict(meta.get("vectoplan_auth"))

    sync_payload = {
        "auth_user_id": context.user_id,
        "auth_account_id": context.account_id,
        "auth_state": context.auth_state,
        "email": context.user.email,
        "display_name": context.user.display_name,
        "plan": context.plan,
        "roles": list(context.roles),
        "entitlements": list(context.access.entitlements),
        "synced_at": _now_iso(),
    }

    for key, value in sync_payload.items():
        if key in SAFE_SYNC_META_KEYS:
            vectoplan_auth[key] = value

    meta["vectoplan_auth"] = vectoplan_auth
    return meta


def _query() -> Any:
    AppUser = _import_app_user_model()
    db = _import_db()

    query_obj = getattr(AppUser, "query", None)
    if query_obj is not None:
        return query_obj

    return db.session.query(AppUser)


def _query_first_by(field_name: str, value: Any) -> Any:
    if value is None or value == "":
        return None

    AppUser = _import_app_user_model()

    if not _model_has_field(AppUser, field_name):
        return None

    try:
        return _query().filter(getattr(AppUser, field_name) == value).first()
    except Exception:
        LOGGER.debug("AppUser lookup failed for field=%s", field_name, exc_info=True)
        return None


def _query_all_candidates_by_json_meta(auth_user_id: str, limit: int = 500) -> Any:
    """
    Fallback, falls models/users.py noch kein auth_user_id-Feld besitzt,
    aber ein JSON-Metafeld vorhanden ist.

    Bewusst begrenzt, damit keine großen Tabellen gescannt werden.
    Sobald auth_user_id existiert, wird dieser Pfad praktisch nicht mehr gebraucht.
    """
    AppUser = _import_app_user_model()
    json_fields = _all_existing_fields(AppUser, JSON_META_FIELDS)
    if not json_fields:
        return None

    try:
        rows = _query().limit(limit).all()
    except Exception:
        LOGGER.debug("AppUser JSON-meta fallback lookup failed.", exc_info=True)
        return None

    for row in rows:
        for field_name in json_fields:
            meta = _safe_json_dict(_get_field(row, field_name))
            auth_meta = _safe_json_dict(meta.get("vectoplan_auth"))
            if _safe_str(auth_meta.get("auth_user_id")) == auth_user_id:
                return row

    return None


def _find_existing_app_user(context: AuthContext) -> Any:
    AppUser = _import_app_user_model()
    auth_user_id = _safe_str(context.user_id)
    if not auth_user_id:
        return None

    # 1. Kanonische Auth-ID-Felder.
    for field_name in AUTH_USER_ID_FIELDS:
        found = _query_first_by(field_name, auth_user_id)
        if found is not None:
            return found

    # 2. Deterministische public_id, falls AppUser noch kein auth_user_id-Feld hat.
    deterministic_public_id = _deterministic_public_id(auth_user_id)
    for field_name in PUBLIC_ID_FIELDS:
        found = _query_first_by(field_name, deterministic_public_id)
        if found is not None:
            return found

    # 3. JSON-Meta-Fallback.
    found = _query_all_candidates_by_json_meta(auth_user_id)
    if found is not None:
        return found

    # 4. E-Mail-Fallback nur, wenn kein stabileres Feld vorhanden ist.
    #    Das ist bewusst nachrangig, da E-Mail sich ändern kann.
    if context.user.email:
        for field_name in EMAIL_FIELDS:
            found = _query_first_by(field_name, context.user.email)
            if found is not None:
                return found

    return None


def _is_valid_context_for_linking(context: Optional[AuthContext]) -> Tuple[bool, str]:
    if context is None:
        return False, "auth_context_missing"

    if context.blocked:
        return False, context.blocked_reason or context.reason_code or "blocked"

    if not context.authenticated:
        return False, "authenticated_user_required"

    if not context.user_id:
        return False, "auth_user_id_missing"

    if context.subject_type and context.subject_type in {"guest", "client_identity"}:
        return False, "guest_subject_not_linkable"

    return True, "ok"


def _prepare_new_app_user(context: AuthContext) -> Any:
    AppUser = _import_app_user_model()

    app_user = AppUser()

    auth_user_id = _safe_str(context.user_id)
    public_id = _deterministic_public_id(auth_user_id)
    email = _safe_str(context.user.email)
    username = _safe_str(context.user.username)
    handle = username or _fallback_handle(context)
    display_name = _fallback_display_name(context)
    role = "admin" if context.is_admin else "user"

    # Identität.
    _set_all_existing(app_user, AppUser, AUTH_USER_ID_FIELDS, auth_user_id, only_if_empty=True)
    _set_all_existing(app_user, AppUser, AUTH_ACCOUNT_ID_FIELDS, context.account_id, only_if_empty=True)

    # Deterministische öffentliche lokale ID, falls vorhanden.
    _set_first_existing(app_user, AppUser, PUBLIC_ID_FIELDS, public_id, only_if_empty=True)

    # Profil.
    if email:
        _set_first_existing(app_user, AppUser, EMAIL_FIELDS, email, only_if_empty=True)

    _set_first_existing(app_user, AppUser, USERNAME_FIELDS, handle, only_if_empty=True)
    _set_first_existing(app_user, AppUser, DISPLAY_NAME_FIELDS, display_name, only_if_empty=True)

    # Status/Rolle.
    _set_first_existing(app_user, AppUser, ROLE_FIELDS, role, only_if_empty=True)
    _set_first_existing(app_user, AppUser, USER_STATUS_FIELDS, "active", only_if_empty=True)
    _set_first_existing(app_user, AppUser, AUTH_STATUS_FIELDS, context.auth_state or "authenticated", only_if_empty=True)
    _set_first_existing(app_user, AppUser, PLAN_FIELDS, context.plan, only_if_empty=True)

    # Flags.
    _set_all_existing(app_user, AppUser, BOOLEAN_PLACEHOLDER_FIELDS, False)
    _set_all_existing(app_user, AppUser, BOOLEAN_ACTIVE_FIELDS, True, only_if_empty=True)
    _set_all_existing(app_user, AppUser, BOOLEAN_SYSTEM_FIELDS, bool(context.is_system), only_if_empty=True)

    # Zeitstempel.
    _set_first_existing(app_user, AppUser, SYNC_TIME_FIELDS, _now())

    # JSON-Meta.
    for field_name in _all_existing_fields(AppUser, JSON_META_FIELDS):
        current = _get_field(app_user, field_name)
        _set_field(app_user, field_name, _merge_auth_meta(current, context))

    return app_user


def _sync_existing_app_user(app_user: Any, context: AuthContext) -> Tuple[bool, Tuple[str, ...]]:
    AppUser = _import_app_user_model()

    changed_fields = []

    auth_user_id = _safe_str(context.user_id)
    email = _safe_str(context.user.email)
    username = _safe_str(context.user.username)
    handle = username or _fallback_handle(context)
    display_name = _fallback_display_name(context)
    role = "admin" if context.is_admin else "user"

    # Auth-ID-Felder immer setzen, wenn vorhanden und leer.
    for field_name in _all_existing_fields(AppUser, AUTH_USER_ID_FIELDS):
        current = _safe_str(_get_field(app_user, field_name))
        if not current and auth_user_id:
            if _set_field(app_user, field_name, auth_user_id):
                changed_fields.append(field_name)

    # Account-ID aktualisieren, wenn vorhanden.
    for field_name in _all_existing_fields(AppUser, AUTH_ACCOUNT_ID_FIELDS):
        current = _safe_str(_get_field(app_user, field_name))
        new_value = _safe_str(context.account_id)
        if new_value and current != new_value:
            if _set_field(app_user, field_name, new_value):
                changed_fields.append(field_name)

    # E-Mail aktualisieren, wenn Feld existiert und Auth-Service eine E-Mail liefert.
    for field_name in _all_existing_fields(AppUser, EMAIL_FIELDS):
        current = _safe_str(_get_field(app_user, field_name))
        if email and current != email:
            if _set_field(app_user, field_name, email):
                changed_fields.append(field_name)

    # Username/Handle nur setzen, wenn leer. Handle kann lokal bewusst geändert worden sein.
    for field_name in _all_existing_fields(AppUser, USERNAME_FIELDS):
        current = _safe_str(_get_field(app_user, field_name))
        if not current and handle:
            if _set_field(app_user, field_name, handle):
                changed_fields.append(field_name)

    # Display Name kann aus Auth aktualisiert werden, wenn vorhanden.
    for field_name in _all_existing_fields(AppUser, DISPLAY_NAME_FIELDS):
        current = _safe_str(_get_field(app_user, field_name))
        if display_name and current != display_name:
            if _set_field(app_user, field_name, display_name):
                changed_fields.append(field_name)

    # Rolle nicht aggressiv degradieren. Admin setzen, aber User nicht zwingend überschreiben.
    for field_name in _all_existing_fields(AppUser, ROLE_FIELDS):
        current = _lower(_get_field(app_user, field_name))
        if context.is_admin and current not in {"admin", "system_admin", "staff"}:
            if _set_field(app_user, field_name, role):
                changed_fields.append(field_name)
        elif not current:
            if _set_field(app_user, field_name, role):
                changed_fields.append(field_name)

    # Status/Auth-Status/Plan.
    for field_name in _all_existing_fields(AppUser, AUTH_STATUS_FIELDS):
        new_value = context.auth_state or "authenticated"
        current = _safe_str(_get_field(app_user, field_name))
        if new_value and current != new_value:
            if _set_field(app_user, field_name, new_value):
                changed_fields.append(field_name)

    for field_name in _all_existing_fields(AppUser, PLAN_FIELDS):
        new_value = _safe_str(context.plan)
        current = _safe_str(_get_field(app_user, field_name))
        if new_value and current != new_value:
            if _set_field(app_user, field_name, new_value):
                changed_fields.append(field_name)

    # User-Status nur auf active setzen, wenn leer oder alter Placeholder-Wert.
    for field_name in _all_existing_fields(AppUser, USER_STATUS_FIELDS):
        current = _lower(_get_field(app_user, field_name))
        if current in {"", "placeholder", "demo", "guest"}:
            if _set_field(app_user, field_name, "active"):
                changed_fields.append(field_name)

    # Flags.
    for field_name in _all_existing_fields(AppUser, BOOLEAN_PLACEHOLDER_FIELDS):
        current = _safe_bool(_get_field(app_user, field_name), default=False)
        if current is True:
            if _set_field(app_user, field_name, False):
                changed_fields.append(field_name)

    for field_name in _all_existing_fields(AppUser, BOOLEAN_ACTIVE_FIELDS):
        current = _safe_bool(_get_field(app_user, field_name), default=True)
        if current is False:
            if _set_field(app_user, field_name, True):
                changed_fields.append(field_name)

    # is_system nur hochsetzen, nicht ungefragt zurücksetzen.
    for field_name in _all_existing_fields(AppUser, BOOLEAN_SYSTEM_FIELDS):
        current = _safe_bool(_get_field(app_user, field_name), default=False)
        if context.is_system and not current:
            if _set_field(app_user, field_name, True):
                changed_fields.append(field_name)

    # Sync-Zeit.
    for field_name in _all_existing_fields(AppUser, SYNC_TIME_FIELDS):
        if _set_field(app_user, field_name, _now()):
            changed_fields.append(field_name)
            break

    # JSON-Meta.
    for field_name in _all_existing_fields(AppUser, JSON_META_FIELDS):
        current = _get_field(app_user, field_name)
        new_meta = _merge_auth_meta(current, context)
        if new_meta != current:
            if _set_field(app_user, field_name, new_meta):
                changed_fields.append(field_name)

    return bool(changed_fields), tuple(dict.fromkeys(changed_fields))


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


def _app_user_id(app_user: Any) -> Optional[int]:
    return _safe_int(_get_field(app_user, "id"), default=None)


@dataclass
class AppUserLinkResult:
    ok: bool
    reason: str
    app_user: Any = None
    app_user_id: Optional[int] = None
    auth_user_id: Optional[str] = None
    created: bool = False
    updated: bool = False
    persistent: bool = False
    changed_fields: Tuple[str, ...] = field(default_factory=tuple)
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
            "app_user_id": self.app_user_id,
            "auth_user_id": self.auth_user_id,
            "created": self.created,
            "updated": self.updated,
            "persistent": self.persistent,
            "changed_fields": list(self.changed_fields),
            "warnings": list(self.warnings),
            "error": self.error,
        }

        if include_context and self.context is not None:
            data["auth"] = self.context.to_public_dict(include_raw=False)

        return data


def ensure_app_user_for_auth_context(
    context: Optional[AuthContext] = None,
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> AppUserLinkResult:
    """
    Stellt sicher, dass ein authentifizierter Auth-User einen lokalen AppUser besitzt.

    Gibt keinen lokalen AppUser zurück für:
    - Guest
    - Demo
    - blocked/banned
    - service_unavailable
    - stale session
    - Auth-Kontext ohne user.id

    create:
        Wenn False, wird nur gesucht, aber nicht angelegt.

    update:
        Wenn True, werden lokale sichere Profil-/Syncfelder aktualisiert.

    commit:
        Wenn True, wird die DB-Session committed.
        Wenn False, wird nur geflusht. Nützlich für äußere Transaktionen.
    """
    if context is None:
        context = get_current_auth_context(
            minimal=True,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )

    valid, reason = _is_valid_context_for_linking(context)
    if not valid:
        return AppUserLinkResult(
            ok=False,
            reason=reason,
            auth_user_id=context.user_id if context else None,
            persistent=False,
            context=context,
        )

    auth_user_id = _safe_str(context.user_id)
    cache_key = f"auth_user:{auth_user_id}:create={create}:update={update}"

    if use_cache:
        cache = _get_request_cache()
        if cache is not None and cache_key in cache:
            return cache[cache_key]

    try:
        db = _import_db()
        app_user = _find_existing_app_user(context)
        created = False
        updated = False
        changed_fields: Tuple[str, ...] = tuple()
        warnings = []

        if app_user is None:
            if not create:
                result = AppUserLinkResult(
                    ok=False,
                    reason="app_user_not_found",
                    auth_user_id=auth_user_id,
                    persistent=True,
                    context=context,
                )
                if use_cache:
                    cache = _get_request_cache()
                    if cache is not None:
                        cache[cache_key] = result
                return result

            app_user = _prepare_new_app_user(context)
            db.session.add(app_user)
            created = True

            try:
                _flush_or_commit(commit=commit)
            except IntegrityError:
                # Race-condition oder paralleler Request: rollback und erneut suchen.
                _rollback_safely()
                app_user = _find_existing_app_user(context)
                if app_user is None:
                    raise
                created = False
                warnings.append("integrity_retry_resolved")

        elif update:
            updated, changed_fields = _sync_existing_app_user(app_user, context)
            if updated:
                _flush_or_commit(commit=commit)

        app_user_id = _app_user_id(app_user)

        result = AppUserLinkResult(
            ok=True,
            reason="created" if created else ("updated" if updated else "linked"),
            app_user=app_user,
            app_user_id=app_user_id,
            auth_user_id=auth_user_id,
            created=created,
            updated=updated,
            persistent=True,
            changed_fields=changed_fields,
            warnings=tuple(warnings),
            context=context.with_app_user_id(app_user_id),
        )

        if use_cache:
            cache = _get_request_cache()
            if cache is not None:
                cache[cache_key] = result

        return result

    except Exception as exc:
        _rollback_safely()
        LOGGER.exception("Failed to ensure local AppUser for auth_user_id=%s: %s", auth_user_id, exc)
        return AppUserLinkResult(
            ok=False,
            reason="app_user_link_failed",
            auth_user_id=auth_user_id,
            persistent=False,
            error=str(exc),
            context=context,
        )


def find_app_user_for_auth_context(
    context: Optional[AuthContext] = None,
    *,
    use_cache: bool = True,
) -> AppUserLinkResult:
    """
    Sucht lokalen AppUser für AuthContext, ohne ihn anzulegen.
    """
    return ensure_app_user_for_auth_context(
        context=context,
        create=False,
        update=False,
        commit=False,
        use_cache=use_cache,
    )


def get_or_create_current_app_user(
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> AppUserLinkResult:
    """
    Lädt aktuellen AuthContext und stellt lokalen AppUser sicher.
    """
    context = get_current_auth_context(
        minimal=True,
        use_cache=use_cache,
        force_refresh=force_refresh,
    )
    return ensure_app_user_for_auth_context(
        context=context,
        create=create,
        update=update,
        commit=commit,
        use_cache=use_cache,
    )


def get_current_app_user_optional(
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
) -> Any:
    """
    Gibt lokalen AppUser zurück oder None.

    Für bestehende Codepfade, die bisher optional mit current_user_id gearbeitet haben.
    """
    result = get_or_create_current_app_user(
        create=create,
        update=update,
        commit=commit,
        use_cache=use_cache,
    )
    return result.app_user if result.ok else None


def get_current_app_user_id_optional(
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
) -> Optional[int]:
    """
    Gibt lokale AppUser.id zurück oder None.
    """
    result = get_or_create_current_app_user(
        create=create,
        update=update,
        commit=commit,
        use_cache=use_cache,
    )
    return result.app_user_id if result.ok else None


def require_current_app_user(
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
) -> Any:
    """
    Gibt lokalen AppUser zurück oder wirft PermissionError.

    Für Services, die persistenten User zwingend brauchen.
    """
    result = get_or_create_current_app_user(
        create=create,
        update=update,
        commit=commit,
        use_cache=use_cache,
    )

    if result.ok and result.app_user is not None:
        return result.app_user

    raise PermissionError(result.reason)


def require_current_app_user_id(
    *,
    create: bool = True,
    update: bool = True,
    commit: bool = True,
    use_cache: bool = True,
) -> int:
    """
    Gibt lokale AppUser.id zurück oder wirft PermissionError.
    """
    result = get_or_create_current_app_user(
        create=create,
        update=update,
        commit=commit,
        use_cache=use_cache,
    )

    if result.ok and result.app_user_id is not None:
        return int(result.app_user_id)

    raise PermissionError(result.reason)


def sync_app_user_from_auth_context(
    app_user: Any,
    context: AuthContext,
    *,
    commit: bool = True,
) -> AppUserLinkResult:
    """
    Synchronisiert einen bereits bekannten lokalen AppUser mit AuthContext.
    """
    valid, reason = _is_valid_context_for_linking(context)
    if not valid:
        return AppUserLinkResult(
            ok=False,
            reason=reason,
            app_user=app_user,
            app_user_id=_app_user_id(app_user),
            auth_user_id=context.user_id if context else None,
            context=context,
        )

    try:
        updated, changed_fields = _sync_existing_app_user(app_user, context)
        if updated:
            _flush_or_commit(commit=commit)

        app_user_id = _app_user_id(app_user)

        return AppUserLinkResult(
            ok=True,
            reason="updated" if updated else "linked",
            app_user=app_user,
            app_user_id=app_user_id,
            auth_user_id=context.user_id,
            created=False,
            updated=updated,
            persistent=True,
            changed_fields=changed_fields,
            context=context.with_app_user_id(app_user_id),
        )
    except Exception as exc:
        _rollback_safely()
        LOGGER.exception("Failed to sync local AppUser from AuthContext: %s", exc)
        return AppUserLinkResult(
            ok=False,
            reason="app_user_sync_failed",
            app_user=app_user,
            app_user_id=_app_user_id(app_user),
            auth_user_id=context.user_id,
            error=str(exc),
            context=context,
        )


def app_user_to_auth_debug_dict(app_user: Any) -> Dict[str, Any]:
    """
    Debug-/Diagnose-Darstellung ohne Secrets.

    Keine Cookies, keine Tokens, keine API-Keys.
    """
    if app_user is None:
        return {}

    AppUser = _import_app_user_model()
    data: Dict[str, Any] = {
        "id": _app_user_id(app_user),
    }

    for group in (
        AUTH_USER_ID_FIELDS,
        AUTH_ACCOUNT_ID_FIELDS,
        EMAIL_FIELDS,
        USERNAME_FIELDS,
        DISPLAY_NAME_FIELDS,
        ROLE_FIELDS,
        USER_STATUS_FIELDS,
        AUTH_STATUS_FIELDS,
        PLAN_FIELDS,
        PUBLIC_ID_FIELDS,
    ):
        for field_name in group:
            if _model_has_field(AppUser, field_name):
                value = _get_field(app_user, field_name)
                if value is not None:
                    data[field_name] = value

    return data


def app_user_model_support_report() -> Dict[str, Any]:
    """
    Diagnose: Welche Felder unterstützt das aktuelle AppUser-Model bereits?

    Nützlich vor/nach Bearbeitung von models/users.py.
    """
    try:
        AppUser = _import_app_user_model()
        columns = _model_columns(AppUser)

        return {
            "ok": True,
            "model": f"{AppUser.__module__}.{AppUser.__name__}",
            "columns": list(columns),
            "supports_auth_user_id": bool(_all_existing_fields(AppUser, AUTH_USER_ID_FIELDS)),
            "supports_email": bool(_all_existing_fields(AppUser, EMAIL_FIELDS)),
            "supports_auth_account_id": bool(_all_existing_fields(AppUser, AUTH_ACCOUNT_ID_FIELDS)),
            "supports_public_id": bool(_all_existing_fields(AppUser, PUBLIC_ID_FIELDS)),
            "supports_json_meta": bool(_all_existing_fields(AppUser, JSON_META_FIELDS)),
            "auth_user_id_fields": list(_all_existing_fields(AppUser, AUTH_USER_ID_FIELDS)),
            "email_fields": list(_all_existing_fields(AppUser, EMAIL_FIELDS)),
            "account_fields": list(_all_existing_fields(AppUser, AUTH_ACCOUNT_ID_FIELDS)),
            "json_meta_fields": list(_all_existing_fields(AppUser, JSON_META_FIELDS)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


__all__ = [
    "AppUserLinkResult",
    "AUTH_ACCOUNT_ID_FIELDS",
    "AUTH_STATUS_FIELDS",
    "AUTH_USER_ID_FIELDS",
    "BOOLEAN_ACTIVE_FIELDS",
    "BOOLEAN_PLACEHOLDER_FIELDS",
    "BOOLEAN_SYSTEM_FIELDS",
    "DISPLAY_NAME_FIELDS",
    "EMAIL_FIELDS",
    "JSON_META_FIELDS",
    "PLAN_FIELDS",
    "PUBLIC_ID_FIELDS",
    "ROLE_FIELDS",
    "SYNC_TIME_FIELDS",
    "USERNAME_FIELDS",
    "USER_STATUS_FIELDS",
    "app_user_model_support_report",
    "app_user_to_auth_debug_dict",
    "clear_app_user_link_cache",
    "ensure_app_user_for_auth_context",
    "find_app_user_for_auth_context",
    "get_current_app_user_id_optional",
    "get_current_app_user_optional",
    "get_or_create_current_app_user",
    "require_current_app_user",
    "require_current_app_user_id",
    "sync_app_user_from_auth_context",
]