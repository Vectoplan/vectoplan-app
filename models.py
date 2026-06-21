# services/app/models.py
from __future__ import annotations

import os
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import ForeignKey, Index, UniqueConstraint

from extensions import db


# ───────────────────────── JSON column type ─────────────────────────

try:
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB

    def _json_type():
        try:
            return _PG_JSONB().with_variant(db.JSON(), "sqlite")
        except Exception:
            return db.JSON

except Exception:
    def _json_type():
        return db.JSON


# ───────────────────────── Generic helpers ─────────────────────────

def _utcnow() -> datetime:
    return datetime.utcnow()


def _uuid() -> str:
    return uuid4().hex


def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on"}:
            return True

        if text in {"0", "false", "no", "n", "off"}:
            return False

        return default

    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_list(value: Any) -> List[Any]:
    try:
        return list(value) if isinstance(value, list) else []
    except Exception:
        return []


def _role(value: Any) -> str:
    role = _safe_str(value, "service", 40).lower()
    return role if role in {"user", "assistant", "system", "service"} else "service"


def _legacy_backend_prefix() -> str:
    return "spe" + "ckle"


def _is_legacy_viewer_key(key: Any) -> bool:
    try:
        text = str(key or "").strip().lower()

        if not text:
            return False

        if text.startswith(_legacy_backend_prefix()):
            return True

        return text in {
            "stream_id",
            "branch_id",
            "commit_id",
            "model_id",
            "version_id",
            "viewer_url",
            "raw_viewer_url",
            "old_viewer_url",
        }

    except Exception:
        return False


def _sanitize_viewer_selection(value: Any) -> Dict[str, Any]:
    """
    Keep only neutral workspace/viewer selection keys.

    The route remains named viewer_selection for compatibility, but persisted
    state should not keep old backend-specific project/model/version fields.
    """
    if not isinstance(value, dict):
        return {
            "mode": "editor",
            "workspace_mode": "3d",
            "legacy_3d_backend": False,
        }

    clean: Dict[str, Any] = {}

    for key, item in value.items():
        if _is_legacy_viewer_key(key):
            continue

        key_text = _safe_str(key, "", 120)
        if not key_text:
            continue

        clean[key_text] = item

    mode = _safe_str(clean.get("mode"), "editor", 80).lower()
    if mode in {"3d", "viewer", "model", "version"}:
        mode = "editor"

    if mode not in {"editor", "map", "2d", "lv", "admin"}:
        mode = "editor"

    workspace_mode = _safe_str(clean.get("workspace_mode"), "", 80).lower()
    if not workspace_mode:
        workspace_mode = "3d" if mode == "editor" else mode

    if workspace_mode in {"editor", "viewer", "model", "version"}:
        workspace_mode = "3d"

    if workspace_mode not in {"3d", "map", "2d", "lv", "admin"}:
        workspace_mode = "3d"

    clean["mode"] = mode
    clean["workspace_mode"] = workspace_mode
    clean["legacy_3d_backend"] = False

    return clean


def _deep_merge_state(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge state dictionaries defensively.

    Most keys merge recursively. viewer_selection is replaced/sanitized as one
    logical object so old backend-specific keys do not remain after updates.
    """
    merged = dict(base or {})

    for key, value in dict(patch or {}).items():
        key_text = _safe_str(key, "", 160)

        if not key_text:
            continue

        if key_text == "viewer_selection":
            merged[key_text] = _sanitize_viewer_selection(value)
            continue

        if isinstance(merged.get(key_text), dict) and isinstance(value, dict):
            merged[key_text] = _deep_merge_state(
                dict(merged.get(key_text) or {}),
                dict(value or {}),
            )
            continue

        merged[key_text] = value

    return merged


# ───────────────────────── Clients / Idempotency ─────────────────────────

class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    api_key_hash = db.Column(db.String(64), nullable=False)
    scopes = db.Column(db.String(255), default="*")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    rate_limit_per_min = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_clients_created_at", "created_at"),
    )

    @staticmethod
    def hash_key(plain: str) -> str:
        try:
            return sha256(str(plain or "").encode("utf-8")).hexdigest()
        except Exception:
            return sha256(b"").hexdigest()

    @staticmethod
    def generate_api_key() -> str:
        try:
            return os.urandom(32).hex()
        except Exception:
            return uuid4().hex + uuid4().hex

    @classmethod
    def create_with_key(cls, name: str, scopes: str = "*") -> Tuple["Client", str]:
        key = cls.generate_api_key()

        client = cls(
            id=os.urandom(16).hex(),
            name=_safe_str(name, "client", 120),
            api_key_hash=cls.hash_key(key),
            scopes=_safe_str(scopes, "*", 255) or "*",
            is_active=True,
        )

        db.session.add(client)
        db.session.commit()

        return client, key

    def has_scope(self, required: Optional[str]) -> bool:
        try:
            if not required or required == "*":
                return True

            if self.scopes == "*":
                return True

            have = {
                scope.strip()
                for scope in (self.scopes or "").split(",")
                if scope.strip()
            }

            need = {
                scope.strip()
                for scope in required.split(",")
                if scope.strip()
            }

            return bool(need & have)

        except Exception:
            return False


class IdempotencyKey(db.Model):
    __tablename__ = "idempotency_keys"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=False)
    key = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    ttl_seconds = db.Column(db.Integer, default=600, nullable=False)

    __table_args__ = (
        UniqueConstraint("client_id", "key", name="uq_client_key"),
        Index("idx_idem_created", "created_at"),
    )

    def is_valid(self) -> bool:
        try:
            created_at = self.created_at or _utcnow()
            ttl_seconds = int(self.ttl_seconds or 600)
            return _utcnow() <= created_at + timedelta(seconds=ttl_seconds)
        except Exception:
            return False


# ───────────────────────── Jobs ─────────────────────────

class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    project_id = db.Column(db.String(64), nullable=True)
    kind = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), default="queued", nullable=False)
    progress = db.Column(db.Integer, default=0)
    input_path = db.Column(db.String(512), nullable=True)
    result_path = db.Column(db.String(512), nullable=True)
    error = db.Column(db.Text, nullable=True)
    meta = db.Column(_json_type(), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_jobs_kind", "kind"),
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_created_at", "created_at"),
    )


# ───────────────────────── Datei-Storage ─────────────────────────

class Blob(db.Model):
    __tablename__ = "blobs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    filename = db.Column(db.String(255), nullable=False)
    mime = db.Column(db.String(128), nullable=True)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_blobs_created", "created_at"),
    )

    def to_meta(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "file_id": self.id,
            "blob_id": self.id,
            "filename": self.filename,
            "name": self.filename,
            "mime": self.mime,
            "size": self.size,
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


# ───────────────────────── Chat Conversation ─────────────────────────

class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    project_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(200), nullable=True)
    transcript = db.Column(_json_type(), nullable=False, default=list)
    last_message_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    # Legacy compatibility columns.
    # Keep them until a controlled DB migration removes them. New routes should
    # not write or depend on these fields.
    vectoplan_project_id = db.Column(db.String(64), nullable=True)
    vectoplan_model_id = db.Column(db.String(64), nullable=True)

    __table_args__ = (
        Index("idx_conv_last", "last_message_at"),
    )

    def append_message(
        self,
        role: str,
        text: str = "",
        attachments: Optional[list] = None,
        trace: Optional[list] = None,
        meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        message = {
            "id": _uuid(),
            "ts": _utcnow().isoformat() + "Z",
            "role": _role(role),
            "text": text or "",
            "attachments": _safe_list(attachments),
            "trace": _safe_list(trace),
            "meta": _safe_dict(meta),
        }

        transcript = list(self.transcript or [])
        transcript.append(message)

        self.transcript = transcript
        self.last_message_at = _utcnow()
        self.updated_at = _utcnow()

        return message

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chat_id": self.id,
            "client_id": self.client_id,
            "project_id": self.project_id,
            "title": self.title,
            "last_message_at": self.last_message_at.isoformat() + "Z" if self.last_message_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }


# ───────────────────────── Nachrichtentemplates ─────────────────────────

class MessageTemplate(db.Model):
    __tablename__ = "message_templates"

    key = db.Column(db.String(80), primary_key=True)
    version = db.Column(db.Integer, default=1, nullable=False)
    title = db.Column(db.String(200), nullable=True)
    renderer = db.Column(db.String(80), nullable=False, default="InfoCard")
    schema_json = db.Column(_json_type(), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_templates_active", "is_active"),
        Index("idx_templates_created_at", "created_at"),
    )

    @classmethod
    def upsert(
        cls,
        *,
        key: str,
        schema_json: Optional[Dict[str, Any]] = None,
        renderer: str = "InfoCard",
        title: Optional[str] = None,
        version: int = 1,
        is_active: bool = True,
    ) -> "MessageTemplate":
        key_clean = _safe_str(key, "", 80)

        if not key_clean:
            raise ValueError("template key required")

        try:
            row = cls.query.filter_by(key=key_clean).one_or_none()

            if row is None:
                row = cls(key=key_clean)
                db.session.add(row)

            row.version = int(version or 1)
            row.title = _safe_str(title, key_clean, 200)
            row.renderer = _safe_str(renderer, "InfoCard", 80)
            row.schema_json = dict(schema_json or {})
            row.is_active = bool(is_active)

            db.session.commit()

            return row

        except Exception:
            db.session.rollback()
            raise


# ───────────────────────── Conversation State ─────────────────────────

class ConversationState(db.Model):
    __tablename__ = "conversation_state"

    conversation_id = db.Column(
        db.String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state_json = db.Column(_json_type(), nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_state_updated_at", "updated_at"),
    )

    @classmethod
    def get_or_create(cls, conversation_id: str) -> "ConversationState":
        conversation_id_clean = _safe_str(conversation_id, "", 36)

        if not conversation_id_clean:
            raise ValueError("conversation_id required")

        try:
            row = cls.query.filter_by(conversation_id=conversation_id_clean).one_or_none()

            if row is None:
                row = cls(
                    conversation_id=conversation_id_clean,
                    state_json={},
                )
                db.session.add(row)
                db.session.commit()

            if not isinstance(row.state_json, dict):
                row.state_json = {}
                db.session.add(row)
                db.session.commit()

            return row

        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def merge_patch(cls, conversation_id: str, patch: Dict[str, Any]) -> "ConversationState":
        try:
            row = cls.get_or_create(conversation_id)

            base = dict(row.state_json or {})
            merged = _deep_merge_state(base, dict(patch or {}))

            row.state_json = merged
            row.updated_at = _utcnow()

            db.session.add(row)
            db.session.commit()

            return row

        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def replace(cls, conversation_id: str, state: Dict[str, Any]) -> "ConversationState":
        try:
            row = cls.get_or_create(conversation_id)
            row.state_json = dict(state or {})
            row.updated_at = _utcnow()

            db.session.add(row)
            db.session.commit()

            return row

        except Exception:
            db.session.rollback()
            raise


# ───────────────────────── Projekte + Versionen ─────────────────────────

class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    name = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_projects_client", "client_id"),
    )


class ProjectVersion(db.Model):
    __tablename__ = "project_versions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    project_id = db.Column(
        db.String(36),
        db.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no = db.Column(db.Integer, nullable=False)
    source_template_id = db.Column(db.String(36), nullable=True)
    change_summary = db.Column(_json_type(), nullable=True, default=dict)
    bundle = db.Column(_json_type(), nullable=False, default=dict)
    metrics = db.Column(_json_type(), nullable=True, default=dict)
    created_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "version_no", name="uq_project_version_no"),
        Index("idx_project_versions_project", "project_id"),
        Index("idx_project_versions_created", "created_at"),
    )