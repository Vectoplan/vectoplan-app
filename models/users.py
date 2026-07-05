# services/vectoplan-app/models/users.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    public_id,
    safe_bool,
    safe_dict,
    safe_slug,
    safe_str,
    utcnow,
)


AUTH_STATUS_LINKED = "linked"
AUTH_STATUS_UNLINKED = "unlinked"
AUTH_STATUS_BLOCKED = "blocked"
AUTH_STATUS_STALE = "stale"

AUTH_SUBJECT_USER = "user"

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_SYSTEM_ADMIN = "system_admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLE_SUPPORT = "support"

DISPLAY_NAME_FALLBACK = "User"


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        from flask import current_app

        current_app.logger.warning(message, *args, **kwargs)
    except Exception:
        pass


def _log_exception(message: str, exc: Optional[Exception] = None) -> None:
    try:
        from flask import current_app

        if exc is None:
            current_app.logger.exception(message)
        else:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


def _safe_tuple(value: Any) -> Tuple[str, ...]:
    try:
        if value is None:
            return tuple()

        if isinstance(value, str):
            raw = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw = list(value)
        else:
            raw = [value]

        result = []
        for item in raw:
            text = safe_str(item, "", 160)
            if text and text not in result:
                result.append(text)

        return tuple(result)

    except Exception:
        return tuple()


def _safe_lower_tuple(value: Any) -> Tuple[str, ...]:
    try:
        return tuple(dict.fromkeys(item.lower() for item in _safe_tuple(value) if item))
    except Exception:
        return tuple()


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    try:
        return safe_dict(value)
    except Exception:
        return {}


def normalize_user_role(value: Any, fallback: Optional[str] = ROLE_USER) -> Optional[str]:
    try:
        role = safe_slug(value, default=fallback or "", max_len=40)

        aliases = {
            "administrator": ROLE_ADMIN,
            "system-admin": ROLE_SYSTEM_ADMIN,
            "systemadmin": ROLE_SYSTEM_ADMIN,
            "owner": ROLE_ADMIN,
            "manager": ROLE_ADMIN,
            "default": ROLE_USER,
            "member": ROLE_USER,
            "reader": ROLE_VIEWER,
            "readonly": ROLE_VIEWER,
            "read-only": ROLE_VIEWER,
            "read_only": ROLE_VIEWER,
            "guest": ROLE_VIEWER,
            "demo": ROLE_VIEWER,
        }

        normalized = aliases.get(role, role or fallback)

        allowed = {
            ROLE_ADMIN,
            ROLE_SYSTEM_ADMIN,
            ROLE_STAFF,
            ROLE_USER,
            ROLE_EDITOR,
            ROLE_VIEWER,
            ROLE_SUPPORT,
        }

        if normalized not in allowed:
            return fallback

        return normalized

    except Exception:
        return fallback


def normalize_auth_status(value: Any, fallback: str = AUTH_STATUS_UNLINKED) -> str:
    try:
        status = safe_slug(value, default=fallback, max_len=40)

        aliases = {
            "authenticated": AUTH_STATUS_LINKED,
            "active": AUTH_STATUS_LINKED,
            "ok": AUTH_STATUS_LINKED,
            "guest": AUTH_STATUS_UNLINKED,
            "anonymous": AUTH_STATUS_UNLINKED,
            "demo": AUTH_STATUS_UNLINKED,
            "banned": AUTH_STATUS_BLOCKED,
            "disabled": AUTH_STATUS_BLOCKED,
            "inactive": AUTH_STATUS_BLOCKED,
            "suspended": AUTH_STATUS_BLOCKED,
            "locked": AUTH_STATUS_BLOCKED,
            "stale_session": AUTH_STATUS_STALE,
            "missing": AUTH_STATUS_STALE,
        }

        normalized = aliases.get(status, status or fallback)

        if normalized not in {
            AUTH_STATUS_LINKED,
            AUTH_STATUS_UNLINKED,
            AUTH_STATUS_BLOCKED,
            AUTH_STATUS_STALE,
        }:
            return fallback

        return normalized

    except Exception:
        return fallback


def normalize_plan(value: Any) -> Optional[str]:
    try:
        plan = safe_slug(value, default="", max_len=80)
        return plan or None
    except Exception:
        return None


def _set_if_available(instance: Any, field: str, value: Any) -> None:
    try:
        if hasattr(instance, field):
            setattr(instance, field, value)
    except Exception:
        pass


def _get_attr(instance: Any, field: str, fallback: Any = None) -> Any:
    try:
        if instance is None:
            return fallback
        return getattr(instance, field, fallback)
    except Exception:
        return fallback


def _auth_meta_from_context(context: Any) -> Dict[str, Any]:
    try:
        if context is None:
            return {}

        if hasattr(context, "to_public_dict") and callable(context.to_public_dict):
            return safe_dict(context.to_public_dict(include_raw=False))

        if hasattr(context, "to_dict") and callable(context.to_dict):
            return safe_dict(context.to_dict())

        if isinstance(context, dict):
            return safe_dict(context)

        return {}

    except Exception:
        return {}


def _extract_context_user_id(context: Any) -> Optional[str]:
    try:
        if context is None:
            return None

        if hasattr(context, "user_id"):
            return safe_str(getattr(context, "user_id"), "", 160) or None

        data = _auth_meta_from_context(context)

        user = safe_dict(data.get("user"))
        subject = safe_dict(data.get("subject"))

        if safe_str(subject.get("type"), "", 40) == AUTH_SUBJECT_USER:
            subject_id = safe_str(subject.get("id"), "", 160)
            if subject_id:
                return subject_id

        return (
            safe_str(data.get("user_id"), "", 160)
            or safe_str(data.get("auth_user_id"), "", 160)
            or safe_str(user.get("id"), "", 160)
            or None
        )

    except Exception:
        return None


def _extract_context_user_email(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        user = safe_dict(data.get("user"))

        email = (
            safe_str(user.get("email"), "", 255)
            or safe_str(data.get("email"), "", 255)
            or safe_str(getattr(getattr(context, "user", None), "email", None), "", 255)
        )

        return email.lower() if email else None

    except Exception:
        return None


def _extract_context_display_name(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        user = safe_dict(data.get("user"))

        return (
            safe_str(user.get("display_name"), "", 255)
            or safe_str(user.get("name"), "", 255)
            or safe_str(data.get("display_name"), "", 255)
            or safe_str(getattr(getattr(context, "user", None), "display_name", None), "", 255)
            or None
        )

    except Exception:
        return None


def _extract_context_username(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        user = safe_dict(data.get("user"))

        return (
            safe_str(user.get("username"), "", 120)
            or safe_str(user.get("handle"), "", 120)
            or safe_str(data.get("username"), "", 120)
            or None
        )

    except Exception:
        return None


def _extract_context_account_id(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        account = safe_dict(data.get("account"))

        return (
            safe_str(account.get("account_id"), "", 160)
            or safe_str(account.get("id"), "", 160)
            or safe_str(getattr(context, "account_id", None), "", 160)
            or None
        )

    except Exception:
        return None


def _extract_context_plan(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        access = safe_dict(data.get("access"))

        return normalize_plan(
            access.get("plan")
            or access.get("plan_key")
            or data.get("plan")
            or getattr(context, "plan", None)
        )

    except Exception:
        return None


def _extract_context_roles(context: Any) -> Tuple[str, ...]:
    try:
        data = _auth_meta_from_context(context)
        roles = safe_dict(data.get("roles"))

        return _safe_lower_tuple(
            roles.get("roles")
            or roles.get("names")
            or roles.get("role_names")
            or data.get("roles")
            or getattr(context, "roles", None)
        )

    except Exception:
        return tuple()


def _extract_context_entitlements(context: Any) -> Tuple[str, ...]:
    try:
        data = _auth_meta_from_context(context)
        access = safe_dict(data.get("access"))

        return _safe_tuple(
            access.get("entitlements")
            or data.get("entitlements")
            or getattr(getattr(context, "access", None), "entitlements", None)
        )

    except Exception:
        return tuple()


def _extract_context_blocked(context: Any) -> bool:
    try:
        data = _auth_meta_from_context(context)
        access = safe_dict(data.get("access"))
        security = safe_dict(data.get("security"))

        return bool(
            safe_bool(data.get("blocked"), False)
            or safe_bool(access.get("blocked"), False)
            or safe_bool(security.get("blocked"), False)
            or safe_bool(getattr(context, "blocked", False), False)
        )

    except Exception:
        return False


def _extract_context_blocked_reason(context: Any) -> Optional[str]:
    try:
        data = _auth_meta_from_context(context)
        access = safe_dict(data.get("access"))
        security = safe_dict(data.get("security"))

        return (
            safe_str(data.get("blocked_reason"), "", 500)
            or safe_str(access.get("blocked_reason"), "", 500)
            or safe_str(security.get("blocked_reason"), "", 500)
            or safe_str(getattr(context, "blocked_reason", None), "", 500)
            or None
        )

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Model definition
# ─────────────────────────────────────────────────────────────

def _define_app_user_model(*, extend_existing: bool = False):
    table_args = {"extend_existing": True} if extend_existing else {}

    class AppUser(TimestampMixin, SerializationMixin, db.Model):
        """
        Local vectoplan-app user link.

        This model is not the auth truth.

        Truth:
        - vectoplan-auth.auth_users
        - vectoplan-auth.auth_sessions
        - vectoplan-auth.auth_subscriptions
        - vectoplan-auth.auth_entitlements
        - vectoplan-auth.auth_accounts

        Local purpose:
        - stable FK target for Project.owner_user_id
        - stable FK target for ProjectMembership.user_id
        - audit attribution inside vectoplan-app
        - safe display/profile shadow
        """

        __tablename__ = "app_users"
        __table_args__ = table_args

        id = db.Column(db.Integer, primary_key=True)

        public_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("usr"),
        )

        # Canonical external Auth identity.
        auth_user_id = db.Column(db.String(160), unique=True, nullable=True, index=True)
        auth_subject_type = db.Column(db.String(40), nullable=True, default=AUTH_SUBJECT_USER, index=True)
        auth_account_id = db.Column(db.String(160), nullable=True, index=True)

        auth_status = db.Column(db.String(40), nullable=False, default=AUTH_STATUS_UNLINKED, index=True)
        auth_state = db.Column(db.String(80), nullable=True, index=True)
        auth_plan = db.Column(db.String(80), nullable=True, index=True)
        auth_plan_status = db.Column(db.String(80), nullable=True, index=True)

        auth_roles = db.Column(json_type(), nullable=False, default=list)
        auth_entitlements = db.Column(json_type(), nullable=False, default=list)
        auth_snapshot = db.Column(json_type(), nullable=False, default=dict)

        last_auth_sync_at = db.Column(db.DateTime, nullable=True, index=True)

        # Local safe display/profile shadow.
        email = db.Column(db.String(255), unique=True, nullable=True, index=True)
        username = db.Column(db.String(120), unique=True, nullable=True, index=True)
        handle = db.Column(db.String(120), unique=True, nullable=True, index=True)

        display_name = db.Column(db.String(255), nullable=True)
        first_name = db.Column(db.String(120), nullable=True)
        last_name = db.Column(db.String(120), nullable=True)

        # Local shadow role. Not the platform-role truth.
        role = db.Column(db.String(40), nullable=True, index=True)

        locale = db.Column(db.String(32), nullable=True)
        timezone = db.Column(db.String(80), nullable=True)
        avatar_url = db.Column(db.Text, nullable=True)

        # Local availability mirror. Auth blocked still wins in services/current_user.py.
        is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
        is_placeholder = db.Column(db.Boolean, nullable=False, default=False, index=True)
        is_system = db.Column(db.Boolean, nullable=False, default=False, index=True)

        last_seen_at = db.Column(db.DateTime, nullable=True, index=True)
        disabled_at = db.Column(db.DateTime, nullable=True)
        disabled_reason = db.Column(db.Text, nullable=True)

        settings = db.Column(json_type(), nullable=False, default=dict)
        profile = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        __serialize_exclude__ = ()

        def __repr__(self) -> str:
            try:
                return (
                    f"<AppUser id={self.id!r} public_id={self.public_id!r} "
                    f"auth_user_id={self.auth_user_id!r} display_name={self.display_name!r}>"
                )
            except Exception:
                return "<AppUser>"

        @property
        def user_id(self) -> Optional[int]:
            try:
                return int(self.id) if self.id is not None else None
            except Exception:
                return None

        @property
        def name(self) -> str:
            try:
                return (
                    self.display_name
                    or self.email
                    or self.username
                    or self.handle
                    or self.public_id
                    or DISPLAY_NAME_FALLBACK
                )
            except Exception:
                return DISPLAY_NAME_FALLBACK

        @property
        def full_name(self) -> str:
            try:
                parts = [self.first_name, self.last_name]
                text = " ".join([safe_str(part, "", 120) for part in parts if safe_str(part, "", 120)])
                return text or self.display_name or self.name
            except Exception:
                return self.name

        @property
        def is_admin(self) -> bool:
            try:
                role = normalize_user_role(self.role)
                auth_roles = self.auth_roles_tuple
                return bool(
                    role in {ROLE_ADMIN, ROLE_SYSTEM_ADMIN, ROLE_STAFF}
                    or ROLE_ADMIN in auth_roles
                    or ROLE_SYSTEM_ADMIN in auth_roles
                    or ROLE_STAFF in auth_roles
                    or self.is_system
                )
            except Exception:
                return False

        @property
        def is_enabled(self) -> bool:
            try:
                return bool(self.is_active) and self.disabled_at is None and self.auth_status != AUTH_STATUS_BLOCKED
            except Exception:
                return False

        @property
        def is_auth_linked(self) -> bool:
            try:
                return bool(self.auth_user_id) and self.auth_status not in {
                    AUTH_STATUS_UNLINKED,
                    AUTH_STATUS_STALE,
                    AUTH_STATUS_BLOCKED,
                }
            except Exception:
                return False

        @property
        def account_plan(self) -> Optional[str]:
            try:
                return self.auth_plan
            except Exception:
                return None

        @property
        def account_id(self) -> Optional[str]:
            try:
                return self.auth_account_id
            except Exception:
                return None

        @property
        def auth_roles_tuple(self) -> Tuple[str, ...]:
            try:
                values = _safe_lower_tuple(self.auth_roles)
                if values:
                    return values

                meta = safe_dict(self.metadata_json)
                auth_meta = safe_dict(meta.get("vectoplan_auth"))
                return _safe_lower_tuple(auth_meta.get("roles"))

            except Exception:
                return tuple()

        @property
        def auth_entitlements_tuple(self) -> Tuple[str, ...]:
            try:
                values = _safe_tuple(self.auth_entitlements)
                if values:
                    return values

                meta = safe_dict(self.metadata_json)
                auth_meta = safe_dict(meta.get("vectoplan_auth"))
                return _safe_tuple(auth_meta.get("entitlements"))

            except Exception:
                return tuple()

        def normalize(self) -> "AppUser":
            try:
                if not self.public_id:
                    self.public_id = public_id("usr")

                self.auth_user_id = safe_str(self.auth_user_id, "", 160) or None
                self.auth_subject_type = safe_str(self.auth_subject_type, AUTH_SUBJECT_USER, 40) or AUTH_SUBJECT_USER
                self.auth_account_id = safe_str(self.auth_account_id, "", 160) or None

                self.auth_status = normalize_auth_status(self.auth_status, AUTH_STATUS_UNLINKED)
                self.auth_state = safe_str(self.auth_state, "", 80) or None
                self.auth_plan = normalize_plan(self.auth_plan)
                self.auth_plan_status = safe_str(self.auth_plan_status, "", 80) or None

                self.auth_roles = list(_safe_lower_tuple(self.auth_roles))
                self.auth_entitlements = list(_safe_tuple(self.auth_entitlements))
                self.auth_snapshot = safe_dict(self.auth_snapshot)

                self.email = safe_str(self.email, "", 255).lower() or None
                self.username = safe_str(self.username, "", 120) or None
                self.handle = safe_str(self.handle, "", 120) or None

                self.display_name = safe_str(self.display_name, "", 255) or None
                self.first_name = safe_str(self.first_name, "", 120) or None
                self.last_name = safe_str(self.last_name, "", 120) or None

                self.role = normalize_user_role(self.role, None)

                self.locale = safe_str(self.locale, "", 32) or None
                self.timezone = safe_str(self.timezone, "", 80) or None
                self.avatar_url = safe_str(self.avatar_url, "", 2000) or None

                self.settings = safe_dict(self.settings)
                self.profile = safe_dict(self.profile)
                self.metadata_json = safe_dict(self.metadata_json)

                self.is_active = safe_bool(self.is_active, True)
                self.is_placeholder = safe_bool(self.is_placeholder, False)
                self.is_system = safe_bool(self.is_system, False)

                if self.auth_status == AUTH_STATUS_BLOCKED:
                    self.is_active = False

                return self

            except Exception:
                return self

        def mark_seen(self) -> None:
            try:
                self.last_seen_at = utcnow()
                self.touch()
            except Exception:
                pass

        def mark_auth_synced(self) -> None:
            try:
                self.last_auth_sync_at = utcnow()
                self.touch()
            except Exception:
                pass

        def activate(self) -> None:
            try:
                self.is_active = True
                self.disabled_at = None
                self.disabled_reason = None

                if self.auth_status == AUTH_STATUS_BLOCKED:
                    self.auth_status = AUTH_STATUS_LINKED if self.auth_user_id else AUTH_STATUS_UNLINKED

                self.touch()
            except Exception:
                pass

        def deactivate(self, reason: str = "") -> None:
            try:
                self.is_active = False
                self.disabled_at = utcnow()
                self.disabled_reason = safe_str(reason, "", 2000) or None
                self.touch()
            except Exception:
                pass

        def mark_blocked(self, reason: str = "") -> None:
            try:
                self.is_active = False
                self.auth_status = AUTH_STATUS_BLOCKED
                self.disabled_at = self.disabled_at or utcnow()
                self.disabled_reason = safe_str(reason, "blocked by auth service", 2000) or "blocked by auth service"
                self.touch()
            except Exception:
                pass

        def link_auth(
            self,
            *,
            auth_user_id: Any,
            auth_account_id: Any = None,
            email: Any = None,
            display_name: Any = None,
            username: Any = None,
            plan: Any = None,
            plan_status: Any = None,
            roles: Any = None,
            entitlements: Any = None,
            auth_state: Any = "authenticated",
            auth_status: Any = AUTH_STATUS_LINKED,
            raw_auth: Optional[Dict[str, Any]] = None,
        ) -> None:
            """
            Link this local AppUser to vectoplan-auth.

            Stores only safe shadow/link data:
            - no cookies
            - no session tokens
            - no raw API keys
            - no password data
            """
            try:
                resolved_auth_user_id = safe_str(auth_user_id, "", 160)
                if resolved_auth_user_id:
                    self.auth_user_id = resolved_auth_user_id

                self.auth_subject_type = AUTH_SUBJECT_USER

                resolved_account_id = safe_str(auth_account_id, "", 160)
                if resolved_account_id:
                    self.auth_account_id = resolved_account_id

                self.auth_state = safe_str(auth_state, "", 80) or None
                self.auth_status = normalize_auth_status(auth_status, AUTH_STATUS_LINKED)
                self.auth_plan = normalize_plan(plan)
                self.auth_plan_status = safe_str(plan_status, "", 80) or None

                role_values = _safe_lower_tuple(roles)
                entitlement_values = _safe_tuple(entitlements)

                self.auth_roles = list(role_values)
                self.auth_entitlements = list(entitlement_values)

                new_email = safe_str(email, "", 255).lower()
                if new_email:
                    self.email = new_email

                new_username = safe_str(username, "", 120)
                if new_username:
                    self.username = new_username
                    if not self.handle:
                        self.handle = new_username

                new_display_name = safe_str(display_name, "", 255)
                if new_display_name:
                    self.display_name = new_display_name

                if not self.role:
                    if ROLE_SYSTEM_ADMIN in role_values:
                        self.role = ROLE_SYSTEM_ADMIN
                    elif ROLE_STAFF in role_values:
                        self.role = ROLE_STAFF
                    elif ROLE_ADMIN in role_values:
                        self.role = ROLE_ADMIN
                    else:
                        self.role = ROLE_USER

                meta = safe_dict(self.metadata_json)
                auth_meta = safe_dict(meta.get("vectoplan_auth"))

                auth_meta.update(
                    {
                        "auth_user_id": self.auth_user_id,
                        "auth_account_id": self.auth_account_id,
                        "auth_subject_type": self.auth_subject_type,
                        "auth_status": self.auth_status,
                        "auth_state": self.auth_state,
                        "plan": self.auth_plan,
                        "plan_status": self.auth_plan_status,
                        "roles": list(role_values),
                        "entitlements": list(entitlement_values),
                        "synced_at": isoformat(utcnow()),
                    }
                )

                safe_raw = {}
                raw = safe_dict(raw_auth)
                for key in (
                    "authenticated",
                    "auth_state",
                    "reason",
                    "reason_code",
                    "blocked",
                    "blocked_reason",
                    "source",
                    "status_code",
                ):
                    if key in raw:
                        safe_raw[key] = raw.get(key)

                if safe_raw:
                    auth_meta["last_context"] = safe_raw

                meta["vectoplan_auth"] = auth_meta
                self.metadata_json = meta

                self.auth_snapshot = {
                    "auth_user_id": self.auth_user_id,
                    "auth_account_id": self.auth_account_id,
                    "auth_state": self.auth_state,
                    "auth_status": self.auth_status,
                    "plan": self.auth_plan,
                    "plan_status": self.auth_plan_status,
                    "roles": list(role_values),
                    "entitlements": list(entitlement_values),
                    "synced_at": isoformat(utcnow()),
                }

                self.is_placeholder = False
                self.mark_auth_synced()
                self.normalize()

            except Exception:
                pass

        def unlink_auth(self, reason: str = "") -> None:
            try:
                self.auth_status = AUTH_STATUS_UNLINKED
                self.auth_user_id = None
                self.auth_subject_type = None
                self.auth_account_id = None
                self.auth_state = None
                self.auth_plan = None
                self.auth_plan_status = None
                self.auth_roles = []
                self.auth_entitlements = []
                self.auth_snapshot = {}

                meta = safe_dict(self.metadata_json)
                auth_meta = safe_dict(meta.get("vectoplan_auth"))
                auth_meta["unlinked_at"] = isoformat(utcnow())
                auth_meta["unlink_reason"] = safe_str(reason, "", 500) or None
                meta["vectoplan_auth"] = auth_meta
                self.metadata_json = meta

                self.touch()
                self.normalize()
            except Exception:
                pass

        def update_profile(self, payload: Optional[Dict[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                if "display_name" in data or "displayName" in data or "name" in data:
                    self.display_name = safe_str(
                        data.get("display_name") or data.get("displayName") or data.get("name"),
                        self.display_name or "",
                        255,
                    ) or None

                if "email" in data:
                    self.email = safe_str(data.get("email"), "", 255).lower() or None

                if "username" in data:
                    self.username = safe_str(data.get("username"), "", 120) or None

                if "handle" in data:
                    self.handle = safe_str(data.get("handle"), "", 120) or None

                if "first_name" in data or "firstName" in data:
                    self.first_name = safe_str(data.get("first_name") or data.get("firstName"), "", 120) or None

                if "last_name" in data or "lastName" in data:
                    self.last_name = safe_str(data.get("last_name") or data.get("lastName"), "", 120) or None

                if "locale" in data:
                    self.locale = safe_str(data.get("locale"), "", 32) or None

                if "timezone" in data:
                    self.timezone = safe_str(data.get("timezone"), "", 80) or None

                if "avatar_url" in data or "avatarUrl" in data:
                    self.avatar_url = safe_str(
                        data.get("avatar_url") or data.get("avatarUrl"),
                        "",
                        2000,
                    ) or None

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "profile" in data:
                    self.profile = safe_dict(data.get("profile"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                self.normalize()
                self.touch()

            except Exception:
                pass

        def apply_auth_context(self, context: Any) -> None:
            """
            Best-effort sync from services.auth_context.AuthContext or dict.
            """
            try:
                auth_payload = _auth_meta_from_context(context)

                user_payload = safe_dict(auth_payload.get("user"))
                access_payload = safe_dict(auth_payload.get("access"))
                account_payload = safe_dict(auth_payload.get("account"))
                roles_payload = safe_dict(auth_payload.get("roles"))

                roles = (
                    roles_payload.get("roles")
                    or roles_payload.get("names")
                    or getattr(context, "roles", None)
                    or []
                )

                entitlements = (
                    access_payload.get("entitlements")
                    or getattr(getattr(context, "access", None), "entitlements", None)
                    or []
                )

                auth_user_id = _extract_context_user_id(context)
                blocked = _extract_context_blocked(context)

                self.link_auth(
                    auth_user_id=auth_user_id,
                    auth_account_id=_extract_context_account_id(context),
                    email=_extract_context_user_email(context),
                    display_name=_extract_context_display_name(context),
                    username=_extract_context_username(context),
                    plan=_extract_context_plan(context),
                    plan_status=access_payload.get("plan_status") or access_payload.get("status"),
                    roles=roles,
                    entitlements=entitlements,
                    auth_state=auth_payload.get("auth_state") or getattr(context, "auth_state", None),
                    auth_status=AUTH_STATUS_BLOCKED if blocked else AUTH_STATUS_LINKED,
                    raw_auth=auth_payload,
                )

                if blocked:
                    self.mark_blocked(_extract_context_blocked_reason(context) or "blocked")

            except Exception:
                pass

        def to_dict(self, *, include_private: bool = False, include_profile: bool = True) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "user_id": self.id,
                    "public_id": self.public_id,
                    "display_name": self.display_name,
                    "name": self.name,
                    "full_name": self.full_name,
                    "handle": self.handle,
                    "username": self.username,
                    "role": normalize_user_role(self.role, ROLE_USER),
                    "is_active": bool(self.is_active),
                    "is_enabled": self.is_enabled,
                    "is_placeholder": bool(self.is_placeholder),
                    "is_system": bool(self.is_system),
                    "is_admin": self.is_admin,
                    "is_auth_linked": self.is_auth_linked,
                    "auth_status": self.auth_status,
                    "auth_state": self.auth_state,
                    "auth_plan": self.auth_plan,
                    "auth_plan_status": self.auth_plan_status,
                    "auth_account_id": self.auth_account_id,
                    "locale": self.locale,
                    "timezone": self.timezone,
                    "avatar_url": self.avatar_url,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "last_seen_at": isoformat(self.last_seen_at),
                    "last_auth_sync_at": isoformat(self.last_auth_sync_at),
                    "disabled_at": isoformat(self.disabled_at),
                    "roles": list(self.auth_roles_tuple),
                    "entitlements": list(self.auth_entitlements_tuple),
                }

                if include_profile:
                    payload["settings"] = safe_dict(self.settings)
                    payload["profile"] = safe_dict(self.profile)

                if include_private:
                    payload["email"] = self.email
                    payload["auth_user_id"] = self.auth_user_id
                    payload["disabled_reason"] = self.disabled_reason
                    payload["metadata"] = safe_dict(self.metadata_json)
                    payload["auth_snapshot"] = safe_dict(self.auth_snapshot)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "user_id": getattr(self, "id", None),
                    "public_id": getattr(self, "public_id", None),
                    "display_name": getattr(self, "display_name", None),
                    "is_placeholder": bool(getattr(self, "is_placeholder", False)),
                    "auth_user_id": getattr(self, "auth_user_id", None),
                }

        def to_public_dict(self) -> Dict[str, Any]:
            try:
                return {
                    "id": self.id,
                    "user_id": self.id,
                    "public_id": self.public_id,
                    "display_name": self.display_name,
                    "name": self.name,
                    "handle": self.handle,
                    "username": self.username,
                    "avatar_url": self.avatar_url,
                    "is_placeholder": bool(self.is_placeholder),
                    "is_auth_linked": self.is_auth_linked,
                    "auth_status": self.auth_status,
                    "auth_plan": self.auth_plan,
                }
            except Exception:
                return {}

        @classmethod
        def from_auth_context(cls, context: Any) -> "AppUser":
            """
            Create an unsaved local AppUser link from vectoplan-auth context.

            The caller must add/flush/commit.
            """
            user = cls()
            user.public_id = public_id("usr")
            user.apply_auth_context(context)
            user.normalize()
            return user

    return AppUser


try:
    if "app_users" in getattr(db, "metadata").tables:
        try:
            from .core import AppUser as AppUser  # type: ignore
        except Exception:
            AppUser = _define_app_user_model(extend_existing=True)
    else:
        AppUser = _define_app_user_model(extend_existing=False)
except Exception:
    AppUser = _define_app_user_model(extend_existing=True)


# ─────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────

def get_user_by_id(user_id: Any) -> Optional[AppUser]:
    try:
        if user_id is None:
            return None

        resolved_user_id = int(user_id)
        if resolved_user_id <= 0:
            return None

        return AppUser.query.get(resolved_user_id)
    except Exception:
        return None


def get_user_by_public_id(public_id_value: Any) -> Optional[AppUser]:
    try:
        value = safe_str(public_id_value, "", 160)
        if not value:
            return None

        return AppUser.query.filter_by(public_id=value).one_or_none()

    except Exception:
        return None


def get_user_by_auth_user_id(auth_user_id: Any) -> Optional[AppUser]:
    try:
        value = safe_str(auth_user_id, "", 160)
        if not value:
            return None

        return AppUser.query.filter_by(auth_user_id=value).one_or_none()

    except Exception:
        return None


def get_user_by_email(email: Any) -> Optional[AppUser]:
    try:
        value = safe_str(email, "", 255).lower()
        if not value:
            return None

        return AppUser.query.filter_by(email=value).one_or_none()

    except Exception:
        return None


def serialize_user(user: Any, *, include_private: bool = False, include_profile: bool = True) -> Dict[str, Any]:
    try:
        if user is None:
            return {}

        if hasattr(user, "to_dict"):
            try:
                return user.to_dict(
                    include_private=include_private,
                    include_profile=include_profile,
                )
            except TypeError:
                return user.to_dict()

        return {
            "id": _get_attr(user, "id"),
            "user_id": _get_attr(user, "id"),
            "public_id": _get_attr(user, "public_id"),
            "display_name": _get_attr(user, "display_name"),
            "is_active": bool(_get_attr(user, "is_active", True)),
            "is_placeholder": bool(_get_attr(user, "is_placeholder", False)),
            "auth_user_id": _get_attr(user, "auth_user_id"),
        }

    except Exception:
        return {}


def build_user_from_auth_context(context: Any) -> AppUser:
    return AppUser.from_auth_context(context)


def get_user_model_status() -> Dict[str, Any]:
    try:
        count = 0
        linked_count = 0
        blocked_count = 0

        try:
            count = int(AppUser.query.count())
        except Exception:
            count = 0

        try:
            linked_count = int(AppUser.query.filter(AppUser.auth_user_id.isnot(None)).count())
        except Exception:
            linked_count = 0

        try:
            blocked_count = int(AppUser.query.filter(AppUser.auth_status == AUTH_STATUS_BLOCKED).count())
        except Exception:
            blocked_count = 0

        return {
            "ok": True,
            "model": "AppUser",
            "table": getattr(AppUser, "__tablename__", "app_users"),
            "count": count,
            "linked_count": linked_count,
            "blocked_count": blocked_count,
            "placeholder_contract_removed": True,
            "auth_truth": "vectoplan-auth",
            "local_role": "link/shadow only",
            "columns": sorted([column.name for column in AppUser.__table__.columns]),
        }

    except Exception as exc:
        return {
            "ok": False,
            "model": "AppUser",
            "table": "app_users",
            "error": str(exc),
            "placeholder_contract_removed": True,
            "auth_truth": "vectoplan-auth",
        }


__all__ = [
    "AUTH_STATUS_BLOCKED",
    "AUTH_STATUS_LINKED",
    "AUTH_STATUS_STALE",
    "AUTH_STATUS_UNLINKED",
    "AUTH_SUBJECT_USER",
    "DISPLAY_NAME_FALLBACK",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "ROLE_STAFF",
    "ROLE_SUPPORT",
    "ROLE_SYSTEM_ADMIN",
    "ROLE_USER",
    "ROLE_VIEWER",
    "AppUser",
    "build_user_from_auth_context",
    "get_user_by_auth_user_id",
    "get_user_by_email",
    "get_user_by_id",
    "get_user_by_public_id",
    "get_user_model_status",
    "normalize_auth_status",
    "normalize_plan",
    "normalize_user_role",
    "serialize_user",
]