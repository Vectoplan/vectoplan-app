# /services/DataLoader/models.py
from __future__ import annotations

import os
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4
from typing import Tuple, Optional, Dict, Any

from sqlalchemy import Index, UniqueConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from extensions import db


def _utcnow() -> datetime:
    return datetime.utcnow()


def _uuid() -> str:
    return uuid4().hex


# ───────── Clients / Idempotency ─────────

class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    api_key_hash = db.Column(db.String(64), nullable=False)
    scopes = db.Column(db.String(255), default="*")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    rate_limit_per_min = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    __table_args__ = (Index("idx_clients_created_at", "created_at"),)

    @staticmethod
    def hash_key(plain: str) -> str:
        return sha256(plain.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_api_key() -> str:
        return os.urandom(32).hex()

    @classmethod
    def create_with_key(cls, name: str, scopes: str = "*") -> Tuple["Client", str]:
        key = cls.generate_api_key()
        c = cls(
            id=os.urandom(16).hex(),
            name=name,
            api_key_hash=cls.hash_key(key),
            scopes=scopes,
            is_active=True,
        )
        db.session.add(c)
        db.session.commit()
        return c, key

    def has_scope(self, required: Optional[str]) -> bool:
        if not required or required == "*":
            return True
        if self.scopes == "*":
            return True
        have = {s.strip() for s in (self.scopes or "").split(",") if s.strip()}
        need = {s.strip() for s in required.split(",") if s.strip()}
        return bool(need & have)


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
        return _utcnow() <= self.created_at + timedelta(seconds=self.ttl_seconds)


# ───────── Jobs ─────────

class Job(db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.String(36), primary_key=True)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    project_id = db.Column(db.String(64), nullable=True)
    kind = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), default="queued", nullable=False)
    progress = db.Column(db.Integer, default=0)
    input_path = db.Column(db.String(512), nullable=True)
    result_path = db.Column(db.String(512), nullable=True)
    error = db.Column(db.Text, nullable=True)
    meta = db.Column(JSONB, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    __table_args__ = (
        Index("idx_jobs_kind", "kind"),
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_created_at", "created_at"),
    )


# ───────── Datei-Storage (Blob in DB) ─────────

class Blob(db.Model):
    __tablename__ = "blobs"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    filename = db.Column(db.String(255), nullable=False)
    mime = db.Column(db.String(128), nullable=True)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    __table_args__ = (Index("idx_blobs_created", "created_at"),)


# ───────── Chat-Conversation als JSON-Verlauf ─────────

class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    project_id = db.Column(db.String(64), nullable=True)
    title = db.Column(db.String(200), nullable=True)
    transcript = db.Column(JSONB, nullable=False, default=list)  # Liste von Nachrichten
    last_message_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    vectoplan_project_id = db.Column(db.String(64), nullable=True)
    vectoplan_model_id = db.Column(db.String(64), nullable=True)

    __table_args__ = (Index("idx_conv_last", "last_message_at"),)

    # helper
    def append_message(
        self,
        role: str,
        text: str = "",
        attachments: list = None,
        trace: list = None,
        meta: dict = None,
    ):
        msg = {
            "id": _uuid(),
            "ts": _utcnow().isoformat() + "Z",
            "role": role,                      # "user" | "assistant" | "system" | "service"
            "text": text or "",
            "attachments": attachments or [],  # [{file_id, name, mime, size}]
            "trace": trace or [],              # z.B. ["FPA","DAA","BGA"]
            "meta": meta or {},
        }
        arr = list(self.transcript or [])
        arr.append(msg)
        self.transcript = arr
        self.last_message_at = _utcnow()
        return msg


# ───────── Nachrichtentemplates (neu) ─────────

class MessageTemplate(db.Model):
    __tablename__ = "message_templates"
    key = db.Column(db.String(80), primary_key=True)                         # z.B. "project_welcome"
    version = db.Column(db.Integer, default=1, nullable=False)
    title = db.Column(db.String(200), nullable=True)
    renderer = db.Column(db.String(80), nullable=False, default="InfoCard")  # Clientseitige Komponente
    schema_json = db.Column(JSONB, nullable=True)                            # JSON-Schema für payload
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
        key = (key or "").strip()
        if not key:
            raise ValueError("template key required")
        try:
            row = cls.query.filter_by(key=key).one_or_none()
            if row is None:
                row = cls(key=key)
                db.session.add(row)
            row.version = int(version or 1)
            row.title = (title or key).strip() or key
            row.renderer = (renderer or "InfoCard").strip() or "InfoCard"
            row.schema_json = dict(schema_json or {})
            row.is_active = bool(is_active)
            db.session.commit()
            return row
        except Exception:
            db.session.rollback()
            raise


# ───────── Conversation-Zustand (neu) ─────────

class ConversationState(db.Model):
    __tablename__ = "conversation_state"
    conversation_id = db.Column(
        db.String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state_json = db.Column(JSONB, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    __table_args__ = (Index("idx_state_updated_at", "updated_at"),)

    @classmethod
    def get_or_create(cls, conversation_id: str) -> "ConversationState":
        try:
            row = cls.query.filter_by(conversation_id=conversation_id).one_or_none()
            if row is None:
                row = cls(conversation_id=conversation_id, state_json={})
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
            base.update(patch or {})
            row.state_json = base
            db.session.add(row)
            db.session.commit()
            return row
        except Exception:
            db.session.rollback()
            raise


# ───────── Projekte + Versionen (Bundle je Version) ─────────

class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    client_id = db.Column(db.String(36), db.ForeignKey("clients.id"), nullable=True)
    name = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    __table_args__ = (Index("idx_projects_client", "client_id"),)

class ProjectVersion(db.Model):
    __tablename__ = "project_versions"
    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    project_id = db.Column(db.String(36), db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version_no = db.Column(db.Integer, nullable=False)                         # 1..n
    source_template_id = db.Column(db.String(36), nullable=True)               # chatai_templates.id (optional)
    change_summary = db.Column(JSONB, nullable=True, default=dict)             # was wurde geändert (ops)
    bundle = db.Column(JSONB, nullable=False, default=dict)                    # gleiches Schema wie Template.bundle
    metrics = db.Column(JSONB, nullable=True, default=dict)                    # abgeleitete Kennzahlen

    created_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "version_no", name="uq_project_version_no"),
        Index("idx_project_versions_project", "project_id"),
        Index("idx_project_versions_created", "created_at"),
    )
