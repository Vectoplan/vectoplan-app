# services/vectoplan-app/models/project_audit.py
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    public_id,
    safe_bool,
    safe_dict,
    safe_int,
    safe_list,
    safe_slug,
    safe_str,
    utcnow,
)


AUDIT_CATEGORY_PROJECT = "project"
AUDIT_CATEGORY_ACCESS = "access"
AUDIT_CATEGORY_EMBED = "embed"
AUDIT_CATEGORY_SERVICE_LINK = "service_link"
AUDIT_CATEGORY_VERSION = "version"
AUDIT_CATEGORY_FILE = "file"
AUDIT_CATEGORY_WORKSPACE = "workspace"
AUDIT_CATEGORY_SYSTEM = "system"

AUDIT_CATEGORIES = {
    AUDIT_CATEGORY_PROJECT,
    AUDIT_CATEGORY_ACCESS,
    AUDIT_CATEGORY_EMBED,
    AUDIT_CATEGORY_SERVICE_LINK,
    AUDIT_CATEGORY_VERSION,
    AUDIT_CATEGORY_FILE,
    AUDIT_CATEGORY_WORKSPACE,
    AUDIT_CATEGORY_SYSTEM,
}

AUDIT_ACTION_CREATED = "created"
AUDIT_ACTION_UPDATED = "updated"
AUDIT_ACTION_DELETED = "deleted"
AUDIT_ACTION_RESTORED = "restored"
AUDIT_ACTION_ARCHIVED = "archived"
AUDIT_ACTION_TRANSFERRED = "transferred"
AUDIT_ACTION_VIEWED = "viewed"
AUDIT_ACTION_OPENED = "opened"
AUDIT_ACTION_EXPORTED = "exported"
AUDIT_ACTION_IMPORTED = "imported"
AUDIT_ACTION_LINKED = "linked"
AUDIT_ACTION_UNLINKED = "unlinked"
AUDIT_ACTION_PERMISSION_CHANGED = "permission_changed"
AUDIT_ACTION_EMBED_CHANGED = "embed_changed"
AUDIT_ACTION_VERSION_CREATED = "version_created"
AUDIT_ACTION_ERROR = "error"

AUDIT_ACTIONS = {
    AUDIT_ACTION_CREATED,
    AUDIT_ACTION_UPDATED,
    AUDIT_ACTION_DELETED,
    AUDIT_ACTION_RESTORED,
    AUDIT_ACTION_ARCHIVED,
    AUDIT_ACTION_TRANSFERRED,
    AUDIT_ACTION_VIEWED,
    AUDIT_ACTION_OPENED,
    AUDIT_ACTION_EXPORTED,
    AUDIT_ACTION_IMPORTED,
    AUDIT_ACTION_LINKED,
    AUDIT_ACTION_UNLINKED,
    AUDIT_ACTION_PERMISSION_CHANGED,
    AUDIT_ACTION_EMBED_CHANGED,
    AUDIT_ACTION_VERSION_CREATED,
    AUDIT_ACTION_ERROR,
}

AUDIT_SEVERITY_INFO = "info"
AUDIT_SEVERITY_WARNING = "warning"
AUDIT_SEVERITY_ERROR = "error"
AUDIT_SEVERITY_CRITICAL = "critical"

AUDIT_SEVERITIES = {
    AUDIT_SEVERITY_INFO,
    AUDIT_SEVERITY_WARNING,
    AUDIT_SEVERITY_ERROR,
    AUDIT_SEVERITY_CRITICAL,
}


# ─────────────────────────────────────────────────────────────
# Transitional import helpers
# ─────────────────────────────────────────────────────────────

def _metadata_has_table(table_name: str) -> bool:
    try:
        return str(table_name) in db.metadata.tables
    except Exception:
        return False


def _table_args(extend_existing: bool, *constraints: Any) -> Any:
    try:
        options = {"extend_existing": True} if extend_existing else {}

        if constraints:
            if options:
                return (*constraints, options)
            return constraints

        return options

    except Exception:
        return {"extend_existing": True} if extend_existing else {}


def _core_model_if_registered(model_name: str, table_name: str) -> Any:
    """
    Transitional guard.

    While models/core.py still defines ProjectAuditEvent, this module returns
    the already registered model instead of defining a duplicate SQLAlchemy
    table. Once core.py becomes an aggregator, this module owns the model.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None:
                return model
        except Exception:
            return None

    except Exception:
        return None

    return None


def _resolve_model(model_name: str, table_name: str, factory: Any) -> Any:
    try:
        existing = _core_model_if_registered(model_name, table_name)
        if existing is not None:
            return existing

        return factory(extend_existing=_metadata_has_table(table_name))

    except Exception:
        return factory(extend_existing=True)


# ─────────────────────────────────────────────────────────────
# Normalization helpers
# ─────────────────────────────────────────────────────────────

def normalize_audit_category(value: Any, default: str = AUDIT_CATEGORY_PROJECT) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "membership": AUDIT_CATEGORY_ACCESS,
            "member": AUDIT_CATEGORY_ACCESS,
            "permission": AUDIT_CATEGORY_ACCESS,
            "permissions": AUDIT_CATEGORY_ACCESS,
            "policy": AUDIT_CATEGORY_EMBED,
            "iframe": AUDIT_CATEGORY_EMBED,
            "service": AUDIT_CATEGORY_SERVICE_LINK,
            "link": AUDIT_CATEGORY_SERVICE_LINK,
            "service_ref": AUDIT_CATEGORY_SERVICE_LINK,
            "service_reference": AUDIT_CATEGORY_SERVICE_LINK,
            "revision": AUDIT_CATEGORY_VERSION,
            "snapshot": AUDIT_CATEGORY_VERSION,
            "blob": AUDIT_CATEGORY_FILE,
            "upload": AUDIT_CATEGORY_FILE,
            "editor": AUDIT_CATEGORY_WORKSPACE,
            "map": AUDIT_CATEGORY_WORKSPACE,
            "2d": AUDIT_CATEGORY_WORKSPACE,
            "cad2d": AUDIT_CATEGORY_WORKSPACE,
            "lv": AUDIT_CATEGORY_WORKSPACE,
        }

        normalized = aliases.get(text, text)

        if normalized not in AUDIT_CATEGORIES:
            return default

        return normalized

    except Exception:
        return default


def normalize_audit_action(value: Any, default: str = AUDIT_ACTION_UPDATED) -> str:
    try:
        text = safe_slug(value, default=default, max_len=100).replace("-", "_")

        aliases = {
            "create": AUDIT_ACTION_CREATED,
            "add": AUDIT_ACTION_CREATED,
            "new": AUDIT_ACTION_CREATED,
            "update": AUDIT_ACTION_UPDATED,
            "edit": AUDIT_ACTION_UPDATED,
            "change": AUDIT_ACTION_UPDATED,
            "patch": AUDIT_ACTION_UPDATED,
            "remove": AUDIT_ACTION_DELETED,
            "delete": AUDIT_ACTION_DELETED,
            "soft_delete": AUDIT_ACTION_DELETED,
            "restore": AUDIT_ACTION_RESTORED,
            "unarchive": AUDIT_ACTION_RESTORED,
            "archive": AUDIT_ACTION_ARCHIVED,
            "transfer": AUDIT_ACTION_TRANSFERRED,
            "ownership_transfer": AUDIT_ACTION_TRANSFERRED,
            "view": AUDIT_ACTION_VIEWED,
            "open": AUDIT_ACTION_OPENED,
            "download": AUDIT_ACTION_EXPORTED,
            "export": AUDIT_ACTION_EXPORTED,
            "upload": AUDIT_ACTION_IMPORTED,
            "import": AUDIT_ACTION_IMPORTED,
            "connect": AUDIT_ACTION_LINKED,
            "linked": AUDIT_ACTION_LINKED,
            "disconnect": AUDIT_ACTION_UNLINKED,
            "unlinked": AUDIT_ACTION_UNLINKED,
            "permission": AUDIT_ACTION_PERMISSION_CHANGED,
            "permissions": AUDIT_ACTION_PERMISSION_CHANGED,
            "role_changed": AUDIT_ACTION_PERMISSION_CHANGED,
            "embed": AUDIT_ACTION_EMBED_CHANGED,
            "embed_policy": AUDIT_ACTION_EMBED_CHANGED,
            "version": AUDIT_ACTION_VERSION_CREATED,
            "version_create": AUDIT_ACTION_VERSION_CREATED,
            "fail": AUDIT_ACTION_ERROR,
            "failed": AUDIT_ACTION_ERROR,
            "exception": AUDIT_ACTION_ERROR,
        }

        normalized = aliases.get(text, text)

        if normalized not in AUDIT_ACTIONS:
            return default

        return normalized

    except Exception:
        return default


def normalize_audit_severity(value: Any, default: str = AUDIT_SEVERITY_INFO) -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "warn": AUDIT_SEVERITY_WARNING,
            "warning": AUDIT_SEVERITY_WARNING,
            "err": AUDIT_SEVERITY_ERROR,
            "failed": AUDIT_SEVERITY_ERROR,
            "failure": AUDIT_SEVERITY_ERROR,
            "fatal": AUDIT_SEVERITY_CRITICAL,
        }

        normalized = aliases.get(text, text)

        if normalized not in AUDIT_SEVERITIES:
            return default

        return normalized

    except Exception:
        return default


def normalize_actor_type(value: Any, default: str = "user") -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "human": "user",
            "person": "user",
            "system_user": "system",
            "service": "service",
            "api": "api",
            "automation": "automation",
            "job": "job",
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def normalize_request_context(value: Any) -> Dict[str, Any]:
    try:
        data = safe_dict(value)

        result: Dict[str, Any] = {}

        for key in (
            "request_id",
            "trace_id",
            "session_id",
            "ip",
            "remote_addr",
            "user_agent",
            "method",
            "path",
            "endpoint",
            "origin",
            "referer",
        ):
            if key in data:
                result[key] = safe_str(data.get(key), "", 2000)

        for key, item in data.items():
            if key in result:
                continue

            clean_key = safe_slug(key, "", max_len=120)
            if clean_key:
                result[clean_key] = item

        return result

    except Exception:
        return {}


def build_request_context_from_flask() -> Dict[str, Any]:
    try:
        from flask import request

        return normalize_request_context(
            {
                "request_id": request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID"),
                "trace_id": request.headers.get("X-Trace-ID"),
                "session_id": request.cookies.get("session"),
                "ip": request.headers.get("X-Forwarded-For") or request.remote_addr,
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get("User-Agent"),
                "method": request.method,
                "path": request.path,
                "endpoint": request.endpoint,
                "origin": request.headers.get("Origin"),
                "referer": request.headers.get("Referer"),
            }
        )

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# ProjectAuditEvent model
# ─────────────────────────────────────────────────────────────

def _define_project_audit_event_model(*, extend_existing: bool = False):
    class ProjectAuditEvent(TimestampMixin, SerializationMixin, db.Model):
        """
        Append-only audit event for app-owned project actions.

        This table records what happened in the app shell:
        - project creation/update/delete/archive/transfer
        - membership/permission changes
        - embed policy changes
        - service-link changes
        - version/save-state registration
        - upload/file/workspace events

        It stores event facts and references only. It does not store service-owned
        geometry, chunks, map features or LV contents.
        """

        __tablename__ = "project_audit_events"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        event_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("aud"),
        )

        project_id = db.Column(db.Integer, nullable=True, index=True)
        project_public_id = db.Column(db.String(120), nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)

        category = db.Column(db.String(80), nullable=False, default=AUDIT_CATEGORY_PROJECT, index=True)
        action = db.Column(db.String(120), nullable=False, default=AUDIT_ACTION_UPDATED, index=True)
        severity = db.Column(db.String(40), nullable=False, default=AUDIT_SEVERITY_INFO, index=True)

        actor_user_id = db.Column(db.Integer, nullable=True, index=True)
        actor_type = db.Column(db.String(40), nullable=False, default="user", index=True)
        actor_label = db.Column(db.String(255), nullable=True)

        target_type = db.Column(db.String(120), nullable=True, index=True)
        target_id = db.Column(db.String(255), nullable=True, index=True)
        target_label = db.Column(db.String(255), nullable=True)

        service = db.Column(db.String(120), nullable=True, index=True)
        resource_type = db.Column(db.String(120), nullable=True, index=True)
        resource_id = db.Column(db.String(255), nullable=True, index=True)

        message = db.Column(db.Text, nullable=True)

        before = db.Column(json_type(), nullable=True)
        after = db.Column(json_type(), nullable=True)
        changes = db.Column(json_type(), nullable=False, default=dict)

        payload = db.Column(json_type(), nullable=False, default=dict)
        result = db.Column(json_type(), nullable=True)

        request_context = db.Column(json_type(), nullable=False, default=dict)
        request_id = db.Column(db.String(160), nullable=True, index=True)
        trace_id = db.Column(db.String(160), nullable=True, index=True)
        session_id = db.Column(db.String(255), nullable=True, index=True)
        ip_address = db.Column(db.String(160), nullable=True, index=True)
        user_agent = db.Column(db.Text, nullable=True)

        tags = db.Column(json_type(), nullable=False, default=list)

        status = db.Column(db.String(40), nullable=False, default="recorded", index=True)
        error = db.Column(db.Text, nullable=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectAuditEvent event_id={self.event_id!r} "
                    f"project_id={self.project_id!r} action={self.action!r}>"
                )
            except Exception:
                return "<ProjectAuditEvent>"

        @property
        def is_error(self) -> bool:
            try:
                return (
                    self.severity in {AUDIT_SEVERITY_ERROR, AUDIT_SEVERITY_CRITICAL}
                    or self.action == AUDIT_ACTION_ERROR
                    or bool(self.error)
                )
            except Exception:
                return False

        @property
        def actor(self) -> Dict[str, Any]:
            try:
                return {
                    "user_id": self.actor_user_id,
                    "type": self.actor_type,
                    "label": self.actor_label,
                }
            except Exception:
                return {}

        @property
        def target(self) -> Dict[str, Any]:
            try:
                return {
                    "type": self.target_type,
                    "id": self.target_id,
                    "label": self.target_label,
                }
            except Exception:
                return {}

        @property
        def resource(self) -> Dict[str, Any]:
            try:
                return {
                    "service": self.service,
                    "resource_type": self.resource_type,
                    "resource_id": self.resource_id,
                }
            except Exception:
                return {}

        def normalize(self) -> "ProjectAuditEvent":
            try:
                if not self.event_id:
                    self.event_id = public_id("aud")

                self.project_id = safe_int(self.project_id, 0) or None
                self.project_public_id = safe_str(self.project_public_id, "", 120) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None

                self.category = normalize_audit_category(self.category, AUDIT_CATEGORY_PROJECT)
                self.action = normalize_audit_action(self.action, AUDIT_ACTION_UPDATED)
                self.severity = normalize_audit_severity(self.severity, AUDIT_SEVERITY_INFO)

                self.actor_user_id = safe_int(self.actor_user_id, 0) or None
                self.actor_type = normalize_actor_type(self.actor_type, "user")
                self.actor_label = safe_str(self.actor_label, "", 255) or None

                self.target_type = safe_str(self.target_type, "", 120) or None
                self.target_id = safe_str(self.target_id, "", 255) or None
                self.target_label = safe_str(self.target_label, "", 255) or None

                self.service = safe_slug(self.service, "", 120) or None
                self.resource_type = safe_slug(self.resource_type, "", 120) or None
                self.resource_id = safe_str(self.resource_id, "", 255) or None

                self.message = safe_str(self.message, "", 8000) or None

                self.before = safe_dict(self.before) if self.before is not None else None
                self.after = safe_dict(self.after) if self.after is not None else None
                self.changes = safe_dict(self.changes)
                self.payload = safe_dict(self.payload)
                self.result = safe_dict(self.result) if self.result is not None else None

                self.request_context = normalize_request_context(self.request_context)
                self.request_id = safe_str(self.request_id or self.request_context.get("request_id"), "", 160) or None
                self.trace_id = safe_str(self.trace_id or self.request_context.get("trace_id"), "", 160) or None
                self.session_id = safe_str(self.session_id or self.request_context.get("session_id"), "", 255) or None
                self.ip_address = safe_str(
                    self.ip_address
                    or self.request_context.get("ip")
                    or self.request_context.get("remote_addr"),
                    "",
                    160,
                ) or None
                self.user_agent = safe_str(self.user_agent or self.request_context.get("user_agent"), "", 2000) or None

                self.tags = safe_list(self.tags)
                self.status = safe_str(self.status, "recorded", 40) or "recorded"
                self.error = safe_str(self.error, "", 8000) or None
                self.metadata_json = safe_dict(self.metadata_json)

                if self.error and self.severity == AUDIT_SEVERITY_INFO:
                    self.severity = AUDIT_SEVERITY_ERROR

                if self.action == AUDIT_ACTION_ERROR:
                    self.severity = AUDIT_SEVERITY_ERROR

                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> "ProjectAuditEvent":
            try:
                data = safe_dict(payload)

                field_map = {
                    "event_id": "event_id",
                    "eventId": "event_id",
                    "project_id": "project_id",
                    "projectId": "project_id",
                    "project_public_id": "project_public_id",
                    "projectPublicId": "project_public_id",
                    "conversation_id": "conversation_id",
                    "conversationId": "conversation_id",
                    "chat_id": "conversation_id",
                    "chatId": "conversation_id",
                    "category": "category",
                    "action": "action",
                    "severity": "severity",
                    "actor_user_id": "actor_user_id",
                    "actorUserId": "actor_user_id",
                    "user_id": "actor_user_id",
                    "userId": "actor_user_id",
                    "actor_type": "actor_type",
                    "actorType": "actor_type",
                    "actor_label": "actor_label",
                    "actorLabel": "actor_label",
                    "target_type": "target_type",
                    "targetType": "target_type",
                    "target_id": "target_id",
                    "targetId": "target_id",
                    "target_label": "target_label",
                    "targetLabel": "target_label",
                    "service": "service",
                    "resource_type": "resource_type",
                    "resourceType": "resource_type",
                    "resource_id": "resource_id",
                    "resourceId": "resource_id",
                    "message": "message",
                    "request_id": "request_id",
                    "requestId": "request_id",
                    "trace_id": "trace_id",
                    "traceId": "trace_id",
                    "session_id": "session_id",
                    "sessionId": "session_id",
                    "ip_address": "ip_address",
                    "ipAddress": "ip_address",
                    "user_agent": "user_agent",
                    "userAgent": "user_agent",
                    "status": "status",
                    "error": "error",
                }

                for source_key, target_key in field_map.items():
                    if source_key in data:
                        setattr(self, target_key, data.get(source_key))

                if "before" in data:
                    self.before = safe_dict(data.get("before"))

                if "after" in data:
                    self.after = safe_dict(data.get("after"))

                if "changes" in data or "diff" in data:
                    self.changes = safe_dict(data.get("changes") or data.get("diff"))

                if "payload" in data:
                    self.payload = safe_dict(data.get("payload"))

                if "result" in data:
                    self.result = safe_dict(data.get("result"))

                if "request_context" in data or "requestContext" in data:
                    self.request_context = normalize_request_context(
                        data.get("request_context") or data.get("requestContext")
                    )

                if "tags" in data:
                    self.tags = safe_list(data.get("tags"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def mark_error(self, error: Any, *, severity: str = AUDIT_SEVERITY_ERROR) -> None:
            try:
                self.error = safe_str(error, "unknown error", 8000)
                self.action = AUDIT_ACTION_ERROR
                self.severity = normalize_audit_severity(severity, AUDIT_SEVERITY_ERROR)
                self.status = "error"
                self.touch()
            except Exception:
                pass

        def add_tag(self, tag: Any) -> None:
            try:
                clean = safe_slug(tag, "", max_len=80)
                if not clean:
                    return

                tags = safe_list(self.tags)
                if clean not in tags:
                    tags.append(clean)
                    self.tags = tags
                    self.touch()

            except Exception:
                pass

        def to_dict(
            self,
            *,
            include_private: bool = False,
            include_payload: bool = True,
            include_diff: bool = True,
            include_request: bool = False,
        ) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "event_id": self.event_id,
                    "eventId": self.event_id,
                    "project_id": self.project_id,
                    "projectId": self.project_id,
                    "project_public_id": self.project_public_id,
                    "projectPublicId": self.project_public_id,
                    "conversation_id": self.conversation_id,
                    "conversationId": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "category": self.category,
                    "action": self.action,
                    "severity": self.severity,
                    "actor": self.actor,
                    "actor_user_id": self.actor_user_id,
                    "actorUserId": self.actor_user_id,
                    "actor_type": self.actor_type,
                    "actorType": self.actor_type,
                    "actor_label": self.actor_label,
                    "actorLabel": self.actor_label,
                    "target": self.target,
                    "target_type": self.target_type,
                    "targetType": self.target_type,
                    "target_id": self.target_id,
                    "targetId": self.target_id,
                    "target_label": self.target_label,
                    "targetLabel": self.target_label,
                    "resource": self.resource,
                    "service": self.service,
                    "resource_type": self.resource_type,
                    "resourceType": self.resource_type,
                    "resource_id": self.resource_id,
                    "resourceId": self.resource_id,
                    "message": self.message or "",
                    "status": self.status,
                    "is_error": self.is_error,
                    "isError": self.is_error,
                    "error": self.error,
                    "tags": safe_list(self.tags),
                    "created_at": isoformat(self.created_at),
                    "createdAt": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "updatedAt": isoformat(self.updated_at),
                }

                if include_diff:
                    payload["before"] = safe_dict(self.before)
                    payload["after"] = safe_dict(self.after)
                    payload["changes"] = safe_dict(self.changes)

                if include_payload:
                    payload["payload"] = safe_dict(self.payload)
                    payload["result"] = safe_dict(self.result)

                if include_request or include_private:
                    payload["request_context"] = safe_dict(self.request_context)
                    payload["requestContext"] = safe_dict(self.request_context)
                    payload["request_id"] = self.request_id
                    payload["requestId"] = self.request_id
                    payload["trace_id"] = self.trace_id
                    payload["traceId"] = self.trace_id
                    payload["session_id"] = self.session_id
                    payload["sessionId"] = self.session_id
                    payload["ip_address"] = self.ip_address
                    payload["ipAddress"] = self.ip_address
                    payload["user_agent"] = self.user_agent
                    payload["userAgent"] = self.user_agent

                if include_private:
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "event_id": getattr(self, "event_id", None),
                    "project_id": getattr(self, "project_id", None),
                    "action": getattr(self, "action", None),
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any = None,
            project_public_id: Any = "",
            conversation_id: Any = "",
            category: Any = AUDIT_CATEGORY_PROJECT,
            action: Any = AUDIT_ACTION_UPDATED,
            severity: Any = AUDIT_SEVERITY_INFO,
            actor_user_id: Optional[int] = None,
            actor_type: str = "user",
            actor_label: str = "",
            target_type: str = "",
            target_id: Any = "",
            target_label: str = "",
            service: str = "",
            resource_type: str = "",
            resource_id: Any = "",
            message: str = "",
            before: Optional[Mapping[str, Any]] = None,
            after: Optional[Mapping[str, Any]] = None,
            changes: Optional[Mapping[str, Any]] = None,
            payload: Optional[Mapping[str, Any]] = None,
            result: Optional[Mapping[str, Any]] = None,
            request_context: Optional[Mapping[str, Any]] = None,
            tags: Optional[List[Any]] = None,
            metadata: Optional[Mapping[str, Any]] = None,
        ) -> "ProjectAuditEvent":
            event = cls()
            event.project_id = safe_int(project_id, 0) or None
            event.project_public_id = safe_str(project_public_id, "", 120) or None
            event.conversation_id = safe_str(conversation_id, "", 80) or None

            event.category = normalize_audit_category(category)
            event.action = normalize_audit_action(action)
            event.severity = normalize_audit_severity(severity)

            event.actor_user_id = safe_int(actor_user_id, 0) or None
            event.actor_type = normalize_actor_type(actor_type, "user")
            event.actor_label = safe_str(actor_label, "", 255) or None

            event.target_type = safe_str(target_type, "", 120) or None
            event.target_id = safe_str(target_id, "", 255) or None
            event.target_label = safe_str(target_label, "", 255) or None

            event.service = safe_slug(service, "", 120) or None
            event.resource_type = safe_slug(resource_type, "", 120) or None
            event.resource_id = safe_str(resource_id, "", 255) or None

            event.message = safe_str(message, "", 8000) or None

            event.before = safe_dict(before) if before is not None else None
            event.after = safe_dict(after) if after is not None else None
            event.changes = safe_dict(changes)
            event.payload = safe_dict(payload)
            event.result = safe_dict(result) if result is not None else None
            event.request_context = normalize_request_context(request_context or build_request_context_from_flask())
            event.tags = safe_list(tags)
            event.metadata_json = safe_dict(metadata)
            event.status = "recorded"

            event.normalize()

            return event

    return ProjectAuditEvent


ProjectAuditEvent = _resolve_model(
    "ProjectAuditEvent",
    "project_audit_events",
    _define_project_audit_event_model,
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def build_audit_event(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    category: Any = AUDIT_CATEGORY_PROJECT,
    action: Any = AUDIT_ACTION_UPDATED,
    severity: Any = AUDIT_SEVERITY_INFO,
    actor_user_id: Optional[int] = None,
    actor_type: str = "user",
    actor_label: str = "",
    target_type: str = "",
    target_id: Any = "",
    target_label: str = "",
    service: str = "",
    resource_type: str = "",
    resource_id: Any = "",
    message: str = "",
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    changes: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    request_context: Optional[Mapping[str, Any]] = None,
    tags: Optional[List[Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> ProjectAuditEvent:
    try:
        if hasattr(ProjectAuditEvent, "build"):
            return ProjectAuditEvent.build(
                project_id=project_id,
                project_public_id=project_public_id,
                conversation_id=conversation_id,
                category=category,
                action=action,
                severity=severity,
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                actor_label=actor_label,
                target_type=target_type,
                target_id=target_id,
                target_label=target_label,
                service=service,
                resource_type=resource_type,
                resource_id=resource_id,
                message=message,
                before=before,
                after=after,
                changes=changes,
                payload=payload,
                result=result,
                request_context=request_context,
                tags=tags,
                metadata=metadata,
            )

        event = ProjectAuditEvent()
        event.project_id = safe_int(project_id, 0) or None
        event.project_public_id = safe_str(project_public_id, "", 120) or None
        event.conversation_id = safe_str(conversation_id, "", 80) or None
        event.category = normalize_audit_category(category)
        event.action = normalize_audit_action(action)
        event.severity = normalize_audit_severity(severity)
        event.actor_user_id = safe_int(actor_user_id, 0) or None
        event.actor_type = normalize_actor_type(actor_type)
        event.message = safe_str(message, "", 8000) or None

        if hasattr(event, "normalize"):
            event.normalize()

        return event

    except Exception:
        event = ProjectAuditEvent()
        try:
            event.project_id = safe_int(project_id, 0) or None
            event.action = normalize_audit_action(action)
        except Exception:
            pass
        return event


def record_audit_event(
    *,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    event = build_audit_event(**kwargs)

    try:
        db.session.add(event)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return event

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return event


def record_project_audit_event(
    project: Any,
    *,
    action: Any,
    category: Any = AUDIT_CATEGORY_PROJECT,
    actor_user_id: Optional[int] = None,
    message: str = "",
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    changes: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
    commit: bool = True,
    **kwargs: Any,
) -> ProjectAuditEvent:
    try:
        return record_audit_event(
            project_id=getattr(project, "id", None),
            project_public_id=getattr(project, "public_id", ""),
            conversation_id=getattr(project, "conversation_id", ""),
            category=category,
            action=action,
            actor_user_id=actor_user_id,
            message=message,
            before=before,
            after=after,
            changes=changes,
            payload=payload,
            commit=commit,
            **kwargs,
        )

    except Exception:
        return record_audit_event(
            category=category,
            action=action,
            actor_user_id=actor_user_id,
            message=message,
            payload=payload,
            commit=commit,
            **kwargs,
        )


def get_audit_event_by_id(event_ref: Any) -> Optional[ProjectAuditEvent]:
    try:
        value = safe_str(event_ref, "", 180)
        if not value:
            return None

        numeric_id = safe_int(value, 0)
        if numeric_id:
            item = ProjectAuditEvent.query.get(numeric_id)
            if item is not None:
                return item

        return ProjectAuditEvent.query.filter_by(event_id=value).one_or_none()

    except Exception:
        return None


def list_project_audit_events(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    category: Any = "",
    action: Any = "",
    actor_user_id: Any = None,
    include_errors_only: bool = False,
    limit: int = 200,
) -> List[ProjectAuditEvent]:
    try:
        query = ProjectAuditEvent.query

        resolved_project_id = safe_int(project_id, 0)
        resolved_project_public_id = safe_str(project_public_id, "", 120)
        resolved_conversation_id = safe_str(conversation_id, "", 80)

        if resolved_project_id:
            query = query.filter_by(project_id=resolved_project_id)

        if resolved_project_public_id:
            query = query.filter_by(project_public_id=resolved_project_public_id)

        if resolved_conversation_id:
            query = query.filter_by(conversation_id=resolved_conversation_id)

        if category:
            query = query.filter_by(category=normalize_audit_category(category))

        if action:
            query = query.filter_by(action=normalize_audit_action(action))

        resolved_actor_user_id = safe_int(actor_user_id, 0)
        if resolved_actor_user_id:
            query = query.filter_by(actor_user_id=resolved_actor_user_id)

        if include_errors_only:
            query = query.filter(
                ProjectAuditEvent.severity.in_(
                    [AUDIT_SEVERITY_ERROR, AUDIT_SEVERITY_CRITICAL]
                )
            )

        return list(
            query.order_by(ProjectAuditEvent.created_at.desc())
            .limit(safe_int(limit, 200, minimum=1, maximum=2000))
            .all()
        )

    except Exception:
        return []


def serialize_audit_event(
    event: Any,
    *,
    include_private: bool = False,
    include_payload: bool = True,
    include_diff: bool = True,
    include_request: bool = False,
) -> Dict[str, Any]:
    try:
        if event is None:
            return {}

        if hasattr(event, "to_dict"):
            try:
                return event.to_dict(
                    include_private=include_private,
                    include_payload=include_payload,
                    include_diff=include_diff,
                    include_request=include_request,
                )
            except TypeError:
                return event.to_dict()

        return {
            "id": getattr(event, "id", None),
            "event_id": getattr(event, "event_id", None),
            "project_id": getattr(event, "project_id", None),
            "project_public_id": getattr(event, "project_public_id", None),
            "category": getattr(event, "category", None),
            "action": getattr(event, "action", None),
            "severity": getattr(event, "severity", None),
            "message": getattr(event, "message", None),
            "created_at": isoformat(getattr(event, "created_at", None)),
        }

    except Exception:
        return {}


def serialize_audit_events(events: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_audit_event(item, **kwargs)
            for item in list(events or [])
            if item is not None
        ]
    except Exception:
        return []


def get_project_audit_model_classes() -> List[Any]:
    return [ProjectAuditEvent]


def get_project_audit_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(ProjectAuditEvent.query.count())
        except Exception:
            count = -1

        return {
            "ok": True,
            "models": ["ProjectAuditEvent"],
            "tables": [getattr(ProjectAuditEvent, "__tablename__", "project_audit_events")],
            "count": count,
            "categories": sorted(AUDIT_CATEGORIES),
            "actions": sorted(AUDIT_ACTIONS),
            "severities": sorted(AUDIT_SEVERITIES),
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectAuditEvent"],
            "tables": ["project_audit_events"],
            "error": str(exc),
        }


__all__ = [
    "AUDIT_CATEGORY_PROJECT",
    "AUDIT_CATEGORY_ACCESS",
    "AUDIT_CATEGORY_EMBED",
    "AUDIT_CATEGORY_SERVICE_LINK",
    "AUDIT_CATEGORY_VERSION",
    "AUDIT_CATEGORY_FILE",
    "AUDIT_CATEGORY_WORKSPACE",
    "AUDIT_CATEGORY_SYSTEM",
    "AUDIT_CATEGORIES",
    "AUDIT_ACTION_CREATED",
    "AUDIT_ACTION_UPDATED",
    "AUDIT_ACTION_DELETED",
    "AUDIT_ACTION_RESTORED",
    "AUDIT_ACTION_ARCHIVED",
    "AUDIT_ACTION_TRANSFERRED",
    "AUDIT_ACTION_VIEWED",
    "AUDIT_ACTION_OPENED",
    "AUDIT_ACTION_EXPORTED",
    "AUDIT_ACTION_IMPORTED",
    "AUDIT_ACTION_LINKED",
    "AUDIT_ACTION_UNLINKED",
    "AUDIT_ACTION_PERMISSION_CHANGED",
    "AUDIT_ACTION_EMBED_CHANGED",
    "AUDIT_ACTION_VERSION_CREATED",
    "AUDIT_ACTION_ERROR",
    "AUDIT_ACTIONS",
    "AUDIT_SEVERITY_INFO",
    "AUDIT_SEVERITY_WARNING",
    "AUDIT_SEVERITY_ERROR",
    "AUDIT_SEVERITY_CRITICAL",
    "AUDIT_SEVERITIES",
    "ProjectAuditEvent",
    "normalize_audit_category",
    "normalize_audit_action",
    "normalize_audit_severity",
    "normalize_actor_type",
    "normalize_request_context",
    "build_request_context_from_flask",
    "build_audit_event",
    "record_audit_event",
    "record_project_audit_event",
    "get_audit_event_by_id",
    "list_project_audit_events",
    "serialize_audit_event",
    "serialize_audit_events",
    "get_project_audit_model_classes",
    "get_project_audit_model_status",
]