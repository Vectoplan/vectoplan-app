# services/vectoplan-app/models/project_invitations.py
"""
VECTOPLAN project invitations model.

Zweck:
- Speichert Projekt-Einladungen in vectoplan-app.
- Erzeugt KEINE echten Benutzeraccounts.
- Hält nur Einladungs-, Rollen-, Status- und externe Auth-Identity-Referenzen.
- Unterstützt den späteren Flow:
    registrierte E-Mail im Auth-/Registrierungsdienst gefunden
      → ProjectInvitation pending
      → Einladung versendet
      → User loggt sich später ein
      → Auth-Identität wird mit AppUser verknüpft
      → ProjectMembership wird aktiviert/erstellt

Wichtige Architekturregel:
- Registrierung/Login/Account/Abo/Bigdata-Zugriff liegen NICHT in vectoplan-app.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Veröffentlichungen und Projektfrontend.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import secrets
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Robust imports
# ---------------------------------------------------------------------------

try:
    from .base import db  # type: ignore
except Exception:  # pragma: no cover
    try:
        from extensions import db  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "ProjectInvitation requires SQLAlchemy db from models.base or extensions."
        ) from exc


try:
    from .base import TimestampMixin as _TimestampMixin  # type: ignore
except Exception:  # pragma: no cover

    class _TimestampMixin:  # type: ignore
        created_at = db.Column(db.DateTime(timezone=True), nullable=True)
        updated_at = db.Column(db.DateTime(timezone=True), nullable=True)


try:
    from .base import SoftDeleteMixin as _SoftDeleteMixin  # type: ignore
except Exception:  # pragma: no cover

    class _SoftDeleteMixin:  # type: ignore
        deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
        is_deleted = db.Column(db.Boolean, nullable=False, default=False)


try:
    from .base import SerializationMixin as _SerializationMixin  # type: ignore
except Exception:  # pragma: no cover

    class _SerializationMixin:  # type: ignore
        pass


try:
    from .base import JSON as _JSONColumnType  # type: ignore
except Exception:  # pragma: no cover
    _JSONColumnType = getattr(db, "JSON", None)

if _JSONColumnType is None:  # pragma: no cover
    _JSONColumnType = db.JSON


try:
    from sqlalchemy import event
except Exception:  # pragma: no cover
    event = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_MAX_LENGTH = 320

INVITATION_PUBLIC_ID_PREFIX = "pinv_"

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

VALID_PROJECT_ROLES = {
    ROLE_OWNER,
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_VIEWER,
}

INVITABLE_PROJECT_ROLES = {
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_VIEWER,
}

DEFAULT_INVITATION_ROLE = ROLE_VIEWER

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"

ACTIVE_INVITATION_STATUSES = {
    STATUS_PENDING,
}

TERMINAL_INVITATION_STATUSES = {
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_REVOKED,
    STATUS_EXPIRED,
    STATUS_FAILED,
}

VALID_INVITATION_STATUSES = ACTIVE_INVITATION_STATUSES | TERMINAL_INVITATION_STATUSES

DISPATCH_PENDING = "pending"
DISPATCH_SENT = "sent"
DISPATCH_PLACEHOLDER = "placeholder"
DISPATCH_SKIPPED = "skipped"
DISPATCH_FAILED = "failed"

VALID_DISPATCH_STATUSES = {
    DISPATCH_PENDING,
    DISPATCH_SENT,
    DISPATCH_PLACEHOLDER,
    DISPATCH_SKIPPED,
    DISPATCH_FAILED,
}

DEFAULT_INVITATION_EXPIRY_DAYS = 14

EMAIL_RE = re.compile(
    r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Small safe helpers
# ---------------------------------------------------------------------------


def utcnow() -> _dt.datetime:
    """
    UTC timestamp helper.

    Gibt bewusst timezone-aware UTC zurück. Falls andere App-Models naive UTC
    nutzen, kann SQLAlchemy/Postgres das in der Regel trotzdem speichern.
    """
    try:
        return _dt.datetime.now(_dt.timezone.utc)
    except Exception:  # pragma: no cover
        return _dt.datetime.utcnow()


def _safe_str(value: Any, default: str = "", max_len: Optional[int] = None) -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        if max_len is not None and max_len > 0:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        return default
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return dict(value.to_dict())
        return {}
    except Exception:
        return {}


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def normalize_email(email: Any) -> str:
    text = _safe_str(email, default="", max_len=EMAIL_MAX_LENGTH).lower()
    return text


def is_valid_email(email: Any) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    if len(normalized) > EMAIL_MAX_LENGTH:
        return False
    try:
        return EMAIL_RE.match(normalized) is not None
    except Exception:
        return False


def normalize_invitation_role(role: Any, allow_owner: bool = False) -> str:
    """
    Normalisiert Rollen für Projekt-Einladungen.

    Owner-Einladungen sind standardmäßig deaktiviert, damit ein normaler
    Einladungsvorgang nicht versehentlich Projektownership erzeugt.
    Ownership-Transfer sollte später über einen eigenen Transfer-Flow laufen.
    """
    text = _safe_str(role, default=DEFAULT_INVITATION_ROLE, max_len=40).lower()

    aliases = {
        "read": ROLE_VIEWER,
        "readonly": ROLE_VIEWER,
        "reader": ROLE_VIEWER,
        "view": ROLE_VIEWER,
        "viewer": ROLE_VIEWER,
        "write": ROLE_EDITOR,
        "writer": ROLE_EDITOR,
        "edit": ROLE_EDITOR,
        "editor": ROLE_EDITOR,
        "member": ROLE_EDITOR,
        "manage": ROLE_ADMIN,
        "manager": ROLE_ADMIN,
        "admin": ROLE_ADMIN,
        "administrator": ROLE_ADMIN,
        "owner": ROLE_OWNER,
    }

    normalized = aliases.get(text, text)

    if normalized == ROLE_OWNER and not allow_owner:
        return ROLE_ADMIN

    if normalized not in VALID_PROJECT_ROLES:
        return DEFAULT_INVITATION_ROLE

    if normalized not in INVITABLE_PROJECT_ROLES and not allow_owner:
        return DEFAULT_INVITATION_ROLE

    return normalized


def normalize_invitation_status(status: Any) -> str:
    text = _safe_str(status, default=STATUS_PENDING, max_len=40).lower()

    aliases = {
        "new": STATUS_PENDING,
        "created": STATUS_PENDING,
        "open": STATUS_PENDING,
        "active": STATUS_PENDING,
        "sent": STATUS_PENDING,
        "accepted": STATUS_ACCEPTED,
        "accept": STATUS_ACCEPTED,
        "joined": STATUS_ACCEPTED,
        "rejected": STATUS_REJECTED,
        "declined": STATUS_REJECTED,
        "deny": STATUS_REJECTED,
        "denied": STATUS_REJECTED,
        "revoked": STATUS_REVOKED,
        "cancelled": STATUS_REVOKED,
        "canceled": STATUS_REVOKED,
        "removed": STATUS_REVOKED,
        "expired": STATUS_EXPIRED,
        "timeout": STATUS_EXPIRED,
        "failed": STATUS_FAILED,
        "error": STATUS_FAILED,
    }

    normalized = aliases.get(text, text)
    if normalized not in VALID_INVITATION_STATUSES:
        return STATUS_PENDING
    return normalized


def normalize_dispatch_status(status: Any) -> str:
    text = _safe_str(status, default=DISPATCH_PENDING, max_len=40).lower()

    aliases = {
        "new": DISPATCH_PENDING,
        "created": DISPATCH_PENDING,
        "queued": DISPATCH_PENDING,
        "pending": DISPATCH_PENDING,
        "ok": DISPATCH_SENT,
        "success": DISPATCH_SENT,
        "sent": DISPATCH_SENT,
        "dispatched": DISPATCH_SENT,
        "placeholder": DISPATCH_PLACEHOLDER,
        "mock": DISPATCH_PLACEHOLDER,
        "dev": DISPATCH_PLACEHOLDER,
        "skipped": DISPATCH_SKIPPED,
        "disabled": DISPATCH_SKIPPED,
        "failed": DISPATCH_FAILED,
        "error": DISPATCH_FAILED,
    }

    normalized = aliases.get(text, text)
    if normalized not in VALID_DISPATCH_STATUSES:
        return DISPATCH_PENDING
    return normalized


def generate_project_invitation_public_id() -> str:
    """
    Erzeugt eine stabile öffentliche Einladungs-ID.

    Diese ID ist nicht der geheime Einladungs-Token. Sie darf in API-Antworten
    und Logs auftauchen.
    """
    try:
        return INVITATION_PUBLIC_ID_PREFIX + secrets.token_hex(16)
    except Exception:  # pragma: no cover
        seed = f"{utcnow().isoformat()}:{secrets.randbits(64)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return INVITATION_PUBLIC_ID_PREFIX + digest


def generate_plain_invitation_token() -> str:
    """
    Erzeugt einen geheimen Token für spätere Einladungslinks.

    Der Klartext-Token sollte nur einmalig ausgegeben werden.
    In der DB wird nur der Hash gespeichert.
    """
    try:
        return secrets.token_urlsafe(32)
    except Exception:  # pragma: no cover
        return hashlib.sha256(
            f"{utcnow().isoformat()}:{secrets.randbits(128)}".encode("utf-8")
        ).hexdigest()


def hash_invitation_token(token: Any) -> str:
    text = _safe_str(token)
    if not text:
        return ""
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def default_expires_at(days: int = DEFAULT_INVITATION_EXPIRY_DAYS) -> _dt.datetime:
    safe_days = _safe_int(days, DEFAULT_INVITATION_EXPIRY_DAYS) or DEFAULT_INVITATION_EXPIRY_DAYS
    try:
        return utcnow() + _dt.timedelta(days=safe_days)
    except Exception:  # pragma: no cover
        return utcnow()


def _maybe_datetime_iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    except Exception:
        return None


def _permission_flags_for_role(role: Any) -> Dict[str, bool]:
    normalized = normalize_invitation_role(role, allow_owner=True)

    if normalized == ROLE_OWNER:
        return {
            "view": True,
            "edit": True,
            "manage": True,
            "delete": True,
            "transfer": True,
            "embed": True,
        }

    if normalized == ROLE_ADMIN:
        return {
            "view": True,
            "edit": True,
            "manage": True,
            "delete": False,
            "transfer": False,
            "embed": True,
        }

    if normalized == ROLE_EDITOR:
        return {
            "view": True,
            "edit": True,
            "manage": False,
            "delete": False,
            "transfer": False,
            "embed": False,
        }

    return {
        "view": True,
        "edit": False,
        "manage": False,
        "delete": False,
        "transfer": False,
        "embed": False,
    }


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class ProjectInvitation(
    db.Model,
    _TimestampMixin,
    _SoftDeleteMixin,
    _SerializationMixin,
):
    """
    Einladung eines extern registrierten Users zu einem Projekt.

    Keine Account-Erzeugung:
    - email/email_normalized identifizieren die eingeladene Adresse.
    - auth_user_id verweist auf den späteren Auth-/Registrierungsdienst.
    - target_user_id ist optional und darf nur gesetzt werden, wenn bereits
      ein lokaler AppUser-Link existiert.
    """

    __tablename__ = "project_invitations"

    id = db.Column(db.Integer, primary_key=True)

    public_id = db.Column(
        db.String(80),
        nullable=False,
        unique=True,
        index=True,
        default=generate_project_invitation_public_id,
    )

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalisiert für spätere Links/Debug/Responses ohne Join.
    project_public_id = db.Column(db.String(80), nullable=True, index=True)

    # Eingeladene externe Identität.
    email = db.Column(db.String(EMAIL_MAX_LENGTH), nullable=False, index=True)
    email_normalized = db.Column(db.String(EMAIL_MAX_LENGTH), nullable=False, index=True)

    auth_user_id = db.Column(db.String(160), nullable=True, index=True)
    auth_email_verified = db.Column(db.Boolean, nullable=False, default=False)

    display_name_snapshot = db.Column(db.String(240), nullable=True)
    account_plan_snapshot = db.Column(db.String(80), nullable=True)
    account_status_snapshot = db.Column(db.String(80), nullable=True)
    can_use_bigdata_snapshot = db.Column(db.Boolean, nullable=False, default=False)

    # Optionaler lokaler Link, falls diese Auth-Identität bereits mit einem
    # lokalen AppUser verknüpft ist. Wird NICHT beim Einladen erzeugt.
    target_user_id = db.Column(
        db.Integer,
        db.ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    role = db.Column(db.String(40), nullable=False, default=DEFAULT_INVITATION_ROLE, index=True)

    status = db.Column(db.String(40), nullable=False, default=STATUS_PENDING, index=True)

    dispatch_status = db.Column(
        db.String(40),
        nullable=False,
        default=DISPATCH_PENDING,
        index=True,
    )
    dispatch_code = db.Column(db.String(120), nullable=True)
    dispatch_attempts = db.Column(db.Integer, nullable=False, default=0)
    dispatch_error = db.Column(db.Text, nullable=True)

    invited_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invited_by_auth_user_id = db.Column(db.String(160), nullable=True, index=True)

    accepted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    accepted_by_auth_user_id = db.Column(db.String(160), nullable=True, index=True)

    revoked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    revoked_by_auth_user_id = db.Column(db.String(160), nullable=True, index=True)

    # Optionaler Verweis auf später erzeugte/aktivierte ProjectMembership.
    # Bewusst ohne FK, damit Tabellen-/Importreihenfolge und alte Dev-Schemas
    # weniger fragil sind.
    accepted_membership_id = db.Column(db.Integer, nullable=True, index=True)

    invited_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_dispatch_at = db.Column(db.DateTime(timezone=True), nullable=True)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    rejected_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expired_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    reject_reason = db.Column(db.String(500), nullable=True)
    revoke_reason = db.Column(db.String(500), nullable=True)

    # Nur Hash speichern. Klartext-Token nur einmalig beim Erzeugen ausgeben.
    invitation_token_hash = db.Column(db.String(128), nullable=True, index=True)

    invitation_url = db.Column(db.Text, nullable=True)
    message = db.Column(db.Text, nullable=True)

    # Snapshots/Rohdaten aus Auth-/Einladungsdienst.
    auth_identity_json = db.Column(_JSONColumnType, nullable=False, default=dict)
    dispatch_response_json = db.Column(_JSONColumnType, nullable=False, default=dict)
    metadata_json = db.Column(_JSONColumnType, nullable=False, default=dict)

    last_error = db.Column(db.Text, nullable=True)

    try:
        project = db.relationship(
            "Project",
            foreign_keys=[project_id],
            lazy="select",
        )
        target_user = db.relationship(
            "AppUser",
            foreign_keys=[target_user_id],
            lazy="select",
        )
        invited_by_user = db.relationship(
            "AppUser",
            foreign_keys=[invited_by_user_id],
            lazy="select",
        )
        accepted_by_user = db.relationship(
            "AppUser",
            foreign_keys=[accepted_by_user_id],
            lazy="select",
        )
        revoked_by_user = db.relationship(
            "AppUser",
            foreign_keys=[revoked_by_user_id],
            lazy="select",
        )
    except Exception:  # pragma: no cover
        # Relationship-Konfiguration soll das Model nicht unbrauchbar machen,
        # falls AppUser/Project in Tests nicht geladen sind.
        pass

    __table_args__ = (
        db.Index("ix_project_invitations_project_status", "project_id", "status"),
        db.Index("ix_project_invitations_project_email", "project_id", "email_normalized"),
        db.Index("ix_project_invitations_project_role", "project_id", "role"),
        db.Index("ix_project_invitations_auth_status", "auth_user_id", "status"),
        db.Index("ix_project_invitations_dispatch", "dispatch_status", "last_dispatch_at"),
        {"extend_existing": True},
    )

    # ---------------------------------------------------------------------
    # Constructors
    # ---------------------------------------------------------------------

    def __init__(self, **kwargs: Any) -> None:
        try:
            super().__init__(**kwargs)
        except TypeError:
            # Sehr defensive Fallback-Zuweisung, falls alte SQLAlchemy-Mixins
            # unerwartete kwargs nicht mögen.
            for key, value in kwargs.items():
                try:
                    setattr(self, key, value)
                except Exception:
                    pass

        try:
            self.prepare_for_save()
        except Exception:
            pass

    @classmethod
    def create_pending(
        cls,
        project_id: Any,
        email: Any,
        role: Any = DEFAULT_INVITATION_ROLE,
        invited_by_user_id: Any = None,
        invited_by_auth_user_id: Any = None,
        project_public_id: Any = None,
        identity: Optional[Mapping[str, Any]] = None,
        message: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
        expires_at_value: Any = None,
        expires_in_days: int = DEFAULT_INVITATION_EXPIRY_DAYS,
        generate_token: bool = True,
    ) -> Tuple["ProjectInvitation", Optional[str]]:
        """
        Baut eine neue pending Einladung, speichert sie aber nicht automatisch.

        Rückgabe:
          (invitation, plain_token)

        plain_token:
          Nur vorhanden, wenn generate_token=True.
          Wird nicht in der DB gespeichert, sondern nur als Hash.
        """
        normalized_email = normalize_email(email)

        plain_token: Optional[str] = None

        invitation = cls(
            project_id=_safe_int(project_id),
            project_public_id=_safe_str(project_public_id, default=None, max_len=80),  # type: ignore[arg-type]
            email=normalized_email,
            email_normalized=normalized_email,
            role=normalize_invitation_role(role),
            status=STATUS_PENDING,
            dispatch_status=DISPATCH_PENDING,
            invited_by_user_id=_safe_int(invited_by_user_id),
            invited_by_auth_user_id=_safe_str(invited_by_auth_user_id, default=None, max_len=160),  # type: ignore[arg-type]
            message=_safe_str(message, default=None),  # type: ignore[arg-type]
            metadata_json=_safe_dict(metadata),
            invited_at=utcnow(),
            expires_at=expires_at_value if expires_at_value is not None else default_expires_at(expires_in_days),
        )

        if identity:
            invitation.apply_identity(identity)

        if generate_token:
            plain_token = generate_plain_invitation_token()
            invitation.set_plain_token(plain_token)

        invitation.prepare_for_save()
        return invitation, plain_token

    # ---------------------------------------------------------------------
    # State helpers
    # ---------------------------------------------------------------------

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_INVITATION_STATUSES

    @property
    def is_accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED

    @property
    def is_revoked(self) -> bool:
        return self.status == STATUS_REVOKED

    @property
    def is_expired(self) -> bool:
        try:
            if self.status == STATUS_EXPIRED:
                return True
            if self.expires_at is None:
                return False
            return utcnow() >= self.expires_at
        except Exception:
            return False

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_INVITATION_STATUSES and not self.is_expired

    @property
    def permissions(self) -> Dict[str, bool]:
        return _permission_flags_for_role(self.role)

    def can_accept(self, auth_user_id: Optional[Any] = None, email: Optional[Any] = None) -> bool:
        try:
            if not self.is_active:
                return False

            wanted_auth_user_id = _safe_str(auth_user_id)
            if wanted_auth_user_id and self.auth_user_id and wanted_auth_user_id != self.auth_user_id:
                return False

            wanted_email = normalize_email(email)
            if wanted_email and self.email_normalized and wanted_email != self.email_normalized:
                return False

            return True
        except Exception:
            return False

    def can_revoke(self) -> bool:
        try:
            return self.status not in TERMINAL_INVITATION_STATUSES
        except Exception:
            return False

    def ensure_not_expired(self) -> bool:
        """
        Aktualisiert den Status auf expired, wenn expires_at überschritten ist.

        Rückgabe:
          True, wenn Status geändert wurde.
        """
        try:
            if self.status in TERMINAL_INVITATION_STATUSES:
                return False
            if self.expires_at is None:
                return False
            if utcnow() < self.expires_at:
                return False

            self.status = STATUS_EXPIRED
            self.expired_at = utcnow()
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------------
    # Mutators
    # ---------------------------------------------------------------------

    def prepare_for_save(self) -> None:
        """
        Normalisiert sichere Pflichtfelder vor Insert/Update.
        """
        try:
            if not _safe_str(getattr(self, "public_id", "")):
                self.public_id = generate_project_invitation_public_id()

            normalized_email = normalize_email(getattr(self, "email_normalized", "") or getattr(self, "email", ""))
            self.email_normalized = normalized_email
            self.email = normalize_email(getattr(self, "email", "") or normalized_email)

            self.role = normalize_invitation_role(getattr(self, "role", DEFAULT_INVITATION_ROLE))
            self.status = normalize_invitation_status(getattr(self, "status", STATUS_PENDING))
            self.dispatch_status = normalize_dispatch_status(
                getattr(self, "dispatch_status", DISPATCH_PENDING)
            )

            if getattr(self, "invited_at", None) is None:
                self.invited_at = utcnow()

            if getattr(self, "expires_at", None) is None and self.status == STATUS_PENDING:
                self.expires_at = default_expires_at()

            if getattr(self, "auth_identity_json", None) is None:
                self.auth_identity_json = {}

            if getattr(self, "dispatch_response_json", None) is None:
                self.dispatch_response_json = {}

            if getattr(self, "metadata_json", None) is None:
                self.metadata_json = {}

            if hasattr(self, "updated_at"):
                try:
                    self.updated_at = utcnow()
                except Exception:
                    pass

            if hasattr(self, "created_at") and getattr(self, "created_at", None) is None:
                try:
                    self.created_at = utcnow()
                except Exception:
                    pass
        except Exception:
            pass

    def set_email(self, email: Any) -> None:
        normalized = normalize_email(email)
        self.email = normalized
        self.email_normalized = normalized

    def set_role(self, role: Any, allow_owner: bool = False) -> None:
        self.role = normalize_invitation_role(role, allow_owner=allow_owner)

    def set_status(self, status: Any) -> None:
        self.status = normalize_invitation_status(status)

    def set_plain_token(self, token: Any) -> None:
        token_hash = hash_invitation_token(token)
        if token_hash:
            self.invitation_token_hash = token_hash

    def verify_plain_token(self, token: Any) -> bool:
        try:
            token_hash = hash_invitation_token(token)
            if not token_hash or not self.invitation_token_hash:
                return False
            return secrets.compare_digest(token_hash, self.invitation_token_hash)
        except Exception:
            return False

    def apply_identity(self, identity: Mapping[str, Any]) -> None:
        """
        Übernimmt Snapshot-Daten aus auth_identity_client.

        Erwartet z. B.:
          {
            "registered": true,
            "auth_user_id": "...",
            "email": "...",
            "display_name": "...",
            "account_plan": "free",
            "account_status": "active",
            "can_use_bigdata": false
          }
        """
        data = _safe_dict(identity)

        try:
            identity_email = normalize_email(data.get("email"))
            if identity_email:
                self.email = identity_email
                self.email_normalized = identity_email

            auth_user_id = _safe_str(
                data.get("auth_user_id")
                or data.get("user_id")
                or data.get("external_user_id"),
                default="",
                max_len=160,
            )
            if auth_user_id:
                self.auth_user_id = auth_user_id
                self.auth_email_verified = True

            display_name = _safe_str(data.get("display_name") or data.get("name"), default="", max_len=240)
            if display_name:
                self.display_name_snapshot = display_name

            account_plan = _safe_str(
                data.get("account_plan")
                or data.get("plan")
                or data.get("subscription_plan"),
                default="",
                max_len=80,
            )
            if account_plan:
                self.account_plan_snapshot = account_plan

            account_status = _safe_str(data.get("account_status") or data.get("status"), default="", max_len=80)
            if account_status:
                self.account_status_snapshot = account_status

            self.can_use_bigdata_snapshot = _safe_bool(
                data.get("can_use_bigdata")
                or data.get("bigdata_access"),
                default=bool(getattr(self, "can_use_bigdata_snapshot", False)),
            )

            self.auth_identity_json = data
        except Exception as exc:
            self.last_error = f"apply_identity failed: {exc}"

    def merge_metadata(self, patch: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        base = _safe_dict(getattr(self, "metadata_json", None))
        incoming = _safe_dict(patch)

        try:
            base.update(incoming)
            self.metadata_json = base
            return base
        except Exception:
            return base

    def mark_dispatch_attempt(self) -> None:
        try:
            self.dispatch_attempts = int(self.dispatch_attempts or 0) + 1
        except Exception:
            self.dispatch_attempts = 1
        self.last_dispatch_at = utcnow()

    def apply_dispatch_result(self, result: Any) -> None:
        """
        Übernimmt Ergebnis aus auth_identity_client.dispatch_project_invitation().
        """
        data = _safe_dict(result)

        try:
            self.mark_dispatch_attempt()

            ok = _safe_bool(data.get("ok"), default=False)
            code = _safe_str(data.get("code"), default="")
            self.dispatch_code = code or None
            self.dispatch_response_json = data

            invitation_url = _safe_str(data.get("invitation_url"), default="")
            if invitation_url:
                self.invitation_url = invitation_url

            external_sent = _safe_bool(data.get("external_sent"), default=False)
            placeholder = _safe_bool(data.get("placeholder"), default=False)

            if ok:
                if external_sent:
                    self.dispatch_status = DISPATCH_SENT
                elif placeholder:
                    self.dispatch_status = DISPATCH_PLACEHOLDER
                else:
                    self.dispatch_status = DISPATCH_SENT

                self.sent_at = self.sent_at or utcnow()
                self.dispatch_error = None
                return

            self.dispatch_status = DISPATCH_FAILED
            self.dispatch_error = _safe_str(
                data.get("error") or data.get("message") or code,
                default="Invitation dispatch failed.",
            )
            self.last_error = self.dispatch_error
        except Exception as exc:
            self.dispatch_status = DISPATCH_FAILED
            self.dispatch_error = str(exc)
            self.last_error = str(exc)

    def mark_accepted(
        self,
        accepted_by_user_id: Any = None,
        accepted_by_auth_user_id: Any = None,
        membership_id: Any = None,
    ) -> None:
        self.status = STATUS_ACCEPTED
        self.accepted_at = utcnow()
        self.accepted_by_user_id = _safe_int(accepted_by_user_id)
        self.accepted_by_auth_user_id = _safe_str(
            accepted_by_auth_user_id,
            default=getattr(self, "auth_user_id", None),  # type: ignore[arg-type]
            max_len=160,
        )
        self.accepted_membership_id = _safe_int(membership_id)
        self.last_error = None

    def mark_rejected(self, reason: Any = None) -> None:
        self.status = STATUS_REJECTED
        self.rejected_at = utcnow()
        self.reject_reason = _safe_str(reason, default=None, max_len=500)  # type: ignore[arg-type]

    def mark_revoked(
        self,
        revoked_by_user_id: Any = None,
        revoked_by_auth_user_id: Any = None,
        reason: Any = None,
    ) -> None:
        self.status = STATUS_REVOKED
        self.revoked_at = utcnow()
        self.revoked_by_user_id = _safe_int(revoked_by_user_id)
        self.revoked_by_auth_user_id = _safe_str(
            revoked_by_auth_user_id,
            default=None,  # type: ignore[arg-type]
            max_len=160,
        )
        self.revoke_reason = _safe_str(reason, default=None, max_len=500)  # type: ignore[arg-type]

    def mark_expired(self) -> None:
        self.status = STATUS_EXPIRED
        self.expired_at = utcnow()

    def mark_failed(self, error: Any = None) -> None:
        self.status = STATUS_FAILED
        self.last_error = _safe_str(error, default="Invitation failed.")

    # ---------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------

    def to_dict(
        self,
        include_private: bool = False,
        include_auth: bool = True,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        self.ensure_not_expired()

        data: Dict[str, Any] = {
            "id": self.public_id,
            "public_id": self.public_id,
            "project_id": self.project_id,
            "project_public_id": self.project_public_id,
            "email": self.email_normalized or self.email,
            "display_name": self.display_name_snapshot,
            "role": self.role,
            "status": self.status,
            "dispatch_status": self.dispatch_status,
            "dispatch_code": self.dispatch_code,
            "permissions": self.permissions,
            "is_pending": self.is_pending,
            "is_active": self.is_active,
            "is_accepted": self.is_accepted,
            "is_expired": self.is_expired,
            "invited_by_user_id": self.invited_by_user_id,
            "target_user_id": self.target_user_id,
            "accepted_membership_id": self.accepted_membership_id,
            "invited_at": _maybe_datetime_iso(self.invited_at),
            "sent_at": _maybe_datetime_iso(self.sent_at),
            "accepted_at": _maybe_datetime_iso(self.accepted_at),
            "rejected_at": _maybe_datetime_iso(self.rejected_at),
            "revoked_at": _maybe_datetime_iso(self.revoked_at),
            "expired_at": _maybe_datetime_iso(self.expired_at),
            "expires_at": _maybe_datetime_iso(self.expires_at),
            "message": self.message,
            "metadata": _safe_dict(self.metadata_json),
        }

        if include_auth:
            data.update(
                {
                    "auth_user_id": self.auth_user_id,
                    "auth_email_verified": bool(self.auth_email_verified),
                    "account_plan": self.account_plan_snapshot,
                    "account_status": self.account_status_snapshot,
                    "can_use_bigdata": bool(self.can_use_bigdata_snapshot),
                }
            )

        if include_private:
            data.update(
                {
                    "db_id": self.id,
                    "email_raw": self.email,
                    "email_normalized": self.email_normalized,
                    "invited_by_auth_user_id": self.invited_by_auth_user_id,
                    "accepted_by_user_id": self.accepted_by_user_id,
                    "accepted_by_auth_user_id": self.accepted_by_auth_user_id,
                    "revoked_by_user_id": self.revoked_by_user_id,
                    "revoked_by_auth_user_id": self.revoked_by_auth_user_id,
                    "dispatch_attempts": self.dispatch_attempts,
                    "dispatch_error": self.dispatch_error,
                    "reject_reason": self.reject_reason,
                    "revoke_reason": self.revoke_reason,
                    "last_error": self.last_error,
                    "has_token": bool(self.invitation_token_hash),
                    "invitation_url": self.invitation_url,
                    "created_at": _maybe_datetime_iso(getattr(self, "created_at", None)),
                    "updated_at": _maybe_datetime_iso(getattr(self, "updated_at", None)),
                    "deleted_at": _maybe_datetime_iso(getattr(self, "deleted_at", None)),
                    "is_deleted": bool(getattr(self, "is_deleted", False)),
                }
            )

        if include_raw:
            data.update(
                {
                    "auth_identity": _safe_dict(self.auth_identity_json),
                    "dispatch_response": _safe_dict(self.dispatch_response_json),
                }
            )

        return data

    def serialize(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.to_dict(*args, **kwargs)

    def to_public_dict(self) -> Dict[str, Any]:
        return self.to_dict(include_private=False, include_auth=False, include_raw=False)

    def to_admin_dict(self) -> Dict[str, Any]:
        return self.to_dict(include_private=True, include_auth=True, include_raw=True)

    # ---------------------------------------------------------------------
    # Query helpers
    # ---------------------------------------------------------------------

    @classmethod
    def find_by_public_id(cls, public_id: Any) -> Optional["ProjectInvitation"]:
        safe_id = _safe_str(public_id, max_len=100)
        if not safe_id:
            return None

        try:
            return cls.query.filter(cls.public_id == safe_id).first()
        except Exception:
            return None

    @classmethod
    def find_active_for_email(
        cls,
        project_id: Any,
        email: Any,
    ) -> Optional["ProjectInvitation"]:
        normalized = normalize_email(email)
        safe_project_id = _safe_int(project_id)

        if not safe_project_id or not normalized:
            return None

        try:
            items = (
                cls.query.filter(
                    cls.project_id == safe_project_id,
                    cls.email_normalized == normalized,
                    cls.status.in_(list(ACTIVE_INVITATION_STATUSES)),
                )
                .order_by(cls.invited_at.desc())
                .all()
            )
        except Exception:
            return None

        for item in items:
            try:
                if item.ensure_not_expired():
                    continue
                if item.is_active:
                    return item
            except Exception:
                continue

        return None

    @classmethod
    def list_for_project(
        cls,
        project_id: Any,
        include_terminal: bool = True,
        include_deleted: bool = False,
    ) -> list:
        safe_project_id = _safe_int(project_id)
        if not safe_project_id:
            return []

        try:
            query = cls.query.filter(cls.project_id == safe_project_id)

            if not include_terminal:
                query = query.filter(cls.status.in_(list(ACTIVE_INVITATION_STATUSES)))

            if hasattr(cls, "is_deleted") and not include_deleted:
                query = query.filter(cls.is_deleted.is_(False))

            return query.order_by(cls.invited_at.desc()).all()
        except Exception:
            return []

    @classmethod
    def expire_old_pending(cls, project_id: Optional[Any] = None) -> int:
        """
        Markiert abgelaufene pending Einladungen als expired.

        Gibt Anzahl geänderter Objekte zurück.
        Commit wird bewusst NICHT automatisch ausgeführt.
        """
        try:
            query = cls.query.filter(
                cls.status.in_(list(ACTIVE_INVITATION_STATUSES)),
                cls.expires_at.isnot(None),
                cls.expires_at <= utcnow(),
            )

            safe_project_id = _safe_int(project_id)
            if safe_project_id:
                query = query.filter(cls.project_id == safe_project_id)

            count = 0
            for invitation in query.all():
                try:
                    invitation.mark_expired()
                    count += 1
                except Exception:
                    continue

            return count
        except Exception:
            return 0

    # ---------------------------------------------------------------------
    # Representation
    # ---------------------------------------------------------------------

    def __repr__(self) -> str:
        try:
            return (
                "<ProjectInvitation "
                f"public_id={self.public_id!r} "
                f"project_id={self.project_id!r} "
                f"email={self.email_normalized!r} "
                f"role={self.role!r} "
                f"status={self.status!r}>"
            )
        except Exception:
            return "<ProjectInvitation>"


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks
# ---------------------------------------------------------------------------


def _prepare_invitation_before_save(mapper: Any, connection: Any, target: ProjectInvitation) -> None:
    try:
        target.prepare_for_save()
    except Exception:
        pass


if event is not None:
    try:
        event.listen(ProjectInvitation, "before_insert", _prepare_invitation_before_save)
        event.listen(ProjectInvitation, "before_update", _prepare_invitation_before_save)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def serialize_project_invitation(
    invitation: Optional[ProjectInvitation],
    include_private: bool = False,
    include_auth: bool = True,
    include_raw: bool = False,
) -> Optional[Dict[str, Any]]:
    if invitation is None:
        return None

    try:
        return invitation.to_dict(
            include_private=include_private,
            include_auth=include_auth,
            include_raw=include_raw,
        )
    except Exception:
        return None


def serialize_project_invitations(
    invitations: Iterable[ProjectInvitation],
    include_private: bool = False,
    include_auth: bool = True,
    include_raw: bool = False,
) -> list:
    result = []

    try:
        for invitation in invitations or []:
            serialized = serialize_project_invitation(
                invitation,
                include_private=include_private,
                include_auth=include_auth,
                include_raw=include_raw,
            )
            if serialized is not None:
                result.append(serialized)
    except Exception:
        pass

    return result


def invitation_status_counts(invitations: Iterable[ProjectInvitation]) -> Dict[str, int]:
    counts = {
        STATUS_PENDING: 0,
        STATUS_ACCEPTED: 0,
        STATUS_REJECTED: 0,
        STATUS_REVOKED: 0,
        STATUS_EXPIRED: 0,
        STATUS_FAILED: 0,
        "total": 0,
    }

    try:
        for invitation in invitations or []:
            status = normalize_invitation_status(getattr(invitation, "status", STATUS_PENDING))
            counts[status] = counts.get(status, 0) + 1
            counts["total"] = counts.get("total", 0) + 1
    except Exception:
        pass

    return counts


__all__ = [
    "ACTIVE_INVITATION_STATUSES",
    "DEFAULT_INVITATION_EXPIRY_DAYS",
    "DEFAULT_INVITATION_ROLE",
    "DISPATCH_FAILED",
    "DISPATCH_PENDING",
    "DISPATCH_PLACEHOLDER",
    "DISPATCH_SENT",
    "DISPATCH_SKIPPED",
    "INVITABLE_PROJECT_ROLES",
    "ProjectInvitation",
    "ROLE_ADMIN",
    "ROLE_EDITOR",
    "ROLE_OWNER",
    "ROLE_VIEWER",
    "STATUS_ACCEPTED",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "STATUS_REVOKED",
    "TERMINAL_INVITATION_STATUSES",
    "VALID_INVITATION_STATUSES",
    "VALID_PROJECT_ROLES",
    "default_expires_at",
    "generate_plain_invitation_token",
    "generate_project_invitation_public_id",
    "hash_invitation_token",
    "invitation_status_counts",
    "is_valid_email",
    "normalize_email",
    "normalize_dispatch_status",
    "normalize_invitation_role",
    "normalize_invitation_status",
    "serialize_project_invitation",
    "serialize_project_invitations",
]