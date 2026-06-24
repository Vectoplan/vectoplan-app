# services/vectoplan-app/models/project_versions.py
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


VERSION_KIND_PROJECT = "project"
VERSION_KIND_METADATA = "metadata"
VERSION_KIND_SERVICE_LINK = "service_link"
VERSION_KIND_CHUNK = "chunk"
VERSION_KIND_EDITOR3D = "editor3d"
VERSION_KIND_MAP = "map"
VERSION_KIND_2D = "cad2d"
VERSION_KIND_LV = "lv"
VERSION_KIND_FILE = "file"
VERSION_KIND_SNAPSHOT = "snapshot"
VERSION_KIND_EXPORT = "export"

VERSION_KINDS = {
    VERSION_KIND_PROJECT,
    VERSION_KIND_METADATA,
    VERSION_KIND_SERVICE_LINK,
    VERSION_KIND_CHUNK,
    VERSION_KIND_EDITOR3D,
    VERSION_KIND_MAP,
    VERSION_KIND_2D,
    VERSION_KIND_LV,
    VERSION_KIND_FILE,
    VERSION_KIND_SNAPSHOT,
    VERSION_KIND_EXPORT,
}

VERSION_STATUS_DRAFT = "draft"
VERSION_STATUS_PENDING = "pending"
VERSION_STATUS_STORED = "stored"
VERSION_STATUS_COMPLETE = "complete"
VERSION_STATUS_PUBLISHED = "published"
VERSION_STATUS_FAILED = "failed"
VERSION_STATUS_ARCHIVED = "archived"
VERSION_STATUS_DELETED = "deleted"

VERSION_STATUSES = {
    VERSION_STATUS_DRAFT,
    VERSION_STATUS_PENDING,
    VERSION_STATUS_STORED,
    VERSION_STATUS_COMPLETE,
    VERSION_STATUS_PUBLISHED,
    VERSION_STATUS_FAILED,
    VERSION_STATUS_ARCHIVED,
    VERSION_STATUS_DELETED,
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

    While models/core.py still defines ProjectVersion, this module returns
    that already registered model instead of creating a duplicate SQLAlchemy
    table. Once core.py becomes a compatibility aggregator, this module owns
    the model.
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

def normalize_version_kind(value: Any, default: str = VERSION_KIND_PROJECT) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "project_meta": VERSION_KIND_METADATA,
            "project_metadata": VERSION_KIND_METADATA,
            "meta": VERSION_KIND_METADATA,
            "link": VERSION_KIND_SERVICE_LINK,
            "service": VERSION_KIND_SERVICE_LINK,
            "service_ref": VERSION_KIND_SERVICE_LINK,
            "service_reference": VERSION_KIND_SERVICE_LINK,
            "chunk_project": VERSION_KIND_CHUNK,
            "world": VERSION_KIND_CHUNK,
            "3d": VERSION_KIND_EDITOR3D,
            "editor": VERSION_KIND_EDITOR3D,
            "editor_3d": VERSION_KIND_EDITOR3D,
            "openlayer": VERSION_KIND_MAP,
            "map_layer": VERSION_KIND_MAP,
            "2d": VERSION_KIND_2D,
            "cad": VERSION_KIND_2D,
            "plan2d": VERSION_KIND_2D,
            "plan_2d": VERSION_KIND_2D,
            "leistungsverzeichnis": VERSION_KIND_LV,
            "boq": VERSION_KIND_LV,
            "blob": VERSION_KIND_FILE,
            "upload": VERSION_KIND_FILE,
            "revision": VERSION_KIND_SNAPSHOT,
            "state": VERSION_KIND_SNAPSHOT,
            "download": VERSION_KIND_EXPORT,
        }

        normalized = aliases.get(text, text)

        if normalized not in VERSION_KINDS:
            return default

        return normalized

    except Exception:
        return default


def normalize_version_status(value: Any, default: str = VERSION_STATUS_STORED) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "new": VERSION_STATUS_DRAFT,
            "queued": VERSION_STATUS_PENDING,
            "running": VERSION_STATUS_PENDING,
            "processing": VERSION_STATUS_PENDING,
            "saved": VERSION_STATUS_STORED,
            "stored": VERSION_STATUS_STORED,
            "ok": VERSION_STATUS_COMPLETE,
            "done": VERSION_STATUS_COMPLETE,
            "completed": VERSION_STATUS_COMPLETE,
            "success": VERSION_STATUS_COMPLETE,
            "live": VERSION_STATUS_PUBLISHED,
            "published": VERSION_STATUS_PUBLISHED,
            "error": VERSION_STATUS_FAILED,
            "failed": VERSION_STATUS_FAILED,
            "failure": VERSION_STATUS_FAILED,
            "archive": VERSION_STATUS_ARCHIVED,
            "archived": VERSION_STATUS_ARCHIVED,
            "removed": VERSION_STATUS_DELETED,
            "deleted": VERSION_STATUS_DELETED,
        }

        normalized = aliases.get(text, text)

        if normalized not in VERSION_STATUSES:
            return default

        return normalized

    except Exception:
        return default


def normalize_service_name(value: Any, default: str = "app") -> str:
    try:
        text = safe_slug(value, default=default, max_len=120).replace("-", "_")

        aliases = {
            "vectoplan_app": "app",
            "portal": "app",
            "conversation": "chat",
            "chat_history": "chat",
            "vectoplan_chunk": "chunk",
            "chunks": "chunk",
            "3d": "editor3d",
            "editor": "editor3d",
            "editor_3d": "editor3d",
            "vectoplan_editor": "editor3d",
            "open_layer": "openlayer",
            "openlayers": "openlayer",
            "map": "openlayer",
            "2d": "cad2d",
            "cad": "cad2d",
            "plan2d": "cad2d",
            "boq": "lv",
            "leistungsverzeichnis": "lv",
            "file": "files",
            "blob": "files",
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def _display_label(kind: Any, version_no: Any, label: Any = "") -> str:
    try:
        explicit = safe_str(label, "", 255)
        if explicit:
            return explicit

        normalized_kind = normalize_version_kind(kind)
        number = safe_int(version_no, 1, minimum=1)

        if normalized_kind == VERSION_KIND_PROJECT:
            return f"Projektversion {number}"

        if normalized_kind == VERSION_KIND_METADATA:
            return f"Metadaten-Version {number}"

        if normalized_kind == VERSION_KIND_CHUNK:
            return f"Chunk-Version {number}"

        if normalized_kind == VERSION_KIND_EDITOR3D:
            return f"3D-Version {number}"

        if normalized_kind == VERSION_KIND_MAP:
            return f"Map-Version {number}"

        if normalized_kind == VERSION_KIND_2D:
            return f"2D-Version {number}"

        if normalized_kind == VERSION_KIND_LV:
            return f"LV-Version {number}"

        return f"Version {number}"

    except Exception:
        return "Version"


# ─────────────────────────────────────────────────────────────
# ProjectVersion model
# ─────────────────────────────────────────────────────────────

def _define_project_version_model(*, extend_existing: bool = False):
    class ProjectVersion(TimestampMixin, SerializationMixin, db.Model):
        """
        App-owned project version/link record.

        This table stores central references to project-related save states and
        service artifacts. It does not store service-owned truth.

        Preserved legacy fields:
        - version_no
        - source_template_id
        - change_summary
        - bundle
        - metrics
        - created_by

        Added/refactor fields:
        - version_id
        - project_id
        - conversation_id
        - kind/status
        - source/service/resource references
        - artifact_refs/service_refs/input_refs/output_refs
        """

        __tablename__ = "project_versions"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint("version_id", name="uq_project_versions_version_id"),
            db.UniqueConstraint("project_id", "version_no", name="uq_project_versions_project_version_no"),
        )

        id = db.Column(db.Integer, primary_key=True)

        version_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("ver"),
        )

        project_id = db.Column(db.Integer, nullable=True, index=True)
        project_public_id = db.Column(db.String(120), nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)

        version_no = db.Column(db.Integer, nullable=False, default=1, index=True)

        label = db.Column(db.String(255), nullable=True)
        title = db.Column(db.String(255), nullable=True)
        description = db.Column(db.Text, nullable=True)

        kind = db.Column(db.String(80), nullable=False, default=VERSION_KIND_PROJECT, index=True)
        status = db.Column(db.String(40), nullable=False, default=VERSION_STATUS_STORED, index=True)

        # Legacy compatibility.
        source_template_id = db.Column(db.Integer, nullable=True, index=True)
        change_summary = db.Column(db.Text, nullable=True)
        bundle = db.Column(json_type(), nullable=False, default=dict)
        metrics = db.Column(json_type(), nullable=False, default=dict)
        created_by = db.Column(db.String(160), nullable=True, index=True)

        created_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        updated_by_user_id = db.Column(db.Integer, nullable=True, index=True)

        source_service = db.Column(db.String(120), nullable=True, index=True)
        service = db.Column(db.String(120), nullable=True, index=True)
        service_name = db.Column(db.String(120), nullable=True, index=True)

        resource_type = db.Column(db.String(120), nullable=True, index=True)
        resource_id = db.Column(db.String(255), nullable=True, index=True)
        service_resource_id = db.Column(db.String(255), nullable=True, index=True)
        service_version_id = db.Column(db.String(255), nullable=True, index=True)

        parent_version_id = db.Column(db.String(120), nullable=True, index=True)
        source_version_id = db.Column(db.String(120), nullable=True, index=True)

        artifact_ref = db.Column(json_type(), nullable=True)
        artifact_refs = db.Column(json_type(), nullable=False, default=dict)
        service_refs = db.Column(json_type(), nullable=False, default=dict)
        input_refs = db.Column(json_type(), nullable=False, default=dict)
        output_refs = db.Column(json_type(), nullable=False, default=dict)
        snapshot_ref = db.Column(json_type(), nullable=True)

        request_payload = db.Column(json_type(), nullable=True)
        response_payload = db.Column(json_type(), nullable=True)

        tags = db.Column(json_type(), nullable=False, default=list)

        published_at = db.Column(db.DateTime, nullable=True, index=True)
        archived_at = db.Column(db.DateTime, nullable=True, index=True)
        failed_at = db.Column(db.DateTime, nullable=True, index=True)

        error = db.Column(db.Text, nullable=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectVersion project_id={self.project_id!r} "
                    f"version_no={self.version_no!r} kind={self.kind!r} "
                    f"status={self.status!r}>"
                )
            except Exception:
                return "<ProjectVersion>"

        @property
        def public_id(self) -> str:
            try:
                return self.version_id or str(self.id or "")
            except Exception:
                return ""

        @property
        def display_label(self) -> str:
            return _display_label(self.kind, self.version_no, self.label or self.title)

        @property
        def service_key(self) -> str:
            try:
                return normalize_service_name(self.service or self.service_name or self.source_service or "app")
            except Exception:
                return "app"

        @property
        def is_complete(self) -> bool:
            try:
                return self.status in {
                    VERSION_STATUS_STORED,
                    VERSION_STATUS_COMPLETE,
                    VERSION_STATUS_PUBLISHED,
                }
            except Exception:
                return False

        @property
        def is_published(self) -> bool:
            try:
                return self.status == VERSION_STATUS_PUBLISHED or self.published_at is not None
            except Exception:
                return False

        @property
        def is_archived(self) -> bool:
            try:
                return self.status == VERSION_STATUS_ARCHIVED or self.archived_at is not None
            except Exception:
                return False

        @property
        def is_failed(self) -> bool:
            try:
                return self.status == VERSION_STATUS_FAILED or self.failed_at is not None
            except Exception:
                return False

        @property
        def is_deleted(self) -> bool:
            try:
                return self.status == VERSION_STATUS_DELETED
            except Exception:
                return False

        def normalize(self) -> "ProjectVersion":
            try:
                if not self.version_id:
                    self.version_id = public_id("ver")

                self.project_id = safe_int(self.project_id, 0) or None
                self.project_public_id = safe_str(self.project_public_id, "", 120) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None

                self.version_no = safe_int(self.version_no, 1, minimum=1)

                self.kind = normalize_version_kind(self.kind, VERSION_KIND_PROJECT)
                self.status = normalize_version_status(self.status, VERSION_STATUS_STORED)

                self.label = safe_str(self.label, "", 255) or None
                self.title = safe_str(self.title, "", 255) or self.label or None
                self.description = safe_str(self.description, "", 4000) or None

                self.source_template_id = safe_int(self.source_template_id, 0) or None
                self.change_summary = safe_str(self.change_summary, "", 8000) or None

                self.bundle = safe_dict(self.bundle)
                self.metrics = safe_dict(self.metrics)

                self.created_by = safe_str(self.created_by, "", 160) or None
                self.created_by_user_id = safe_int(self.created_by_user_id, 0) or None
                self.updated_by_user_id = safe_int(self.updated_by_user_id, 0) or None

                self.source_service = normalize_service_name(self.source_service, "") or None
                self.service = normalize_service_name(self.service or self.source_service, "") or None
                self.service_name = normalize_service_name(self.service_name or self.service, "") or None

                self.resource_type = safe_slug(self.resource_type, "", 120) or None
                self.resource_id = safe_str(self.resource_id, "", 255) or None
                self.service_resource_id = safe_str(self.service_resource_id, "", 255) or None
                self.service_version_id = safe_str(self.service_version_id, "", 255) or None

                self.parent_version_id = safe_str(self.parent_version_id, "", 120) or None
                self.source_version_id = safe_str(self.source_version_id, "", 120) or None

                self.artifact_ref = safe_dict(self.artifact_ref) if self.artifact_ref is not None else None
                self.artifact_refs = safe_dict(self.artifact_refs)
                self.service_refs = safe_dict(self.service_refs)
                self.input_refs = safe_dict(self.input_refs)
                self.output_refs = safe_dict(self.output_refs)
                self.snapshot_ref = safe_dict(self.snapshot_ref) if self.snapshot_ref is not None else None

                self.request_payload = safe_dict(self.request_payload) if self.request_payload is not None else None
                self.response_payload = safe_dict(self.response_payload) if self.response_payload is not None else None

                self.tags = safe_list(self.tags)
                self.error = safe_str(self.error, "", 8000) or None
                self.metadata_json = safe_dict(self.metadata_json)

                if self.status == VERSION_STATUS_PUBLISHED and self.published_at is None:
                    self.published_at = utcnow()

                if self.status == VERSION_STATUS_ARCHIVED and self.archived_at is None:
                    self.archived_at = utcnow()

                if self.status == VERSION_STATUS_FAILED and self.failed_at is None:
                    self.failed_at = utcnow()

                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> "ProjectVersion":
            try:
                data = safe_dict(payload)

                field_map = {
                    "version_id": "version_id",
                    "versionId": "version_id",
                    "project_id": "project_id",
                    "projectId": "project_id",
                    "project_public_id": "project_public_id",
                    "projectPublicId": "project_public_id",
                    "conversation_id": "conversation_id",
                    "conversationId": "conversation_id",
                    "chat_id": "conversation_id",
                    "chatId": "conversation_id",
                    "version_no": "version_no",
                    "versionNo": "version_no",
                    "number": "version_no",
                    "label": "label",
                    "title": "title",
                    "description": "description",
                    "kind": "kind",
                    "type": "kind",
                    "status": "status",
                    "source_template_id": "source_template_id",
                    "sourceTemplateId": "source_template_id",
                    "change_summary": "change_summary",
                    "changeSummary": "change_summary",
                    "summary": "change_summary",
                    "created_by": "created_by",
                    "createdBy": "created_by",
                    "created_by_user_id": "created_by_user_id",
                    "createdByUserId": "created_by_user_id",
                    "updated_by_user_id": "updated_by_user_id",
                    "updatedByUserId": "updated_by_user_id",
                    "source_service": "source_service",
                    "sourceService": "source_service",
                    "service": "service",
                    "service_name": "service_name",
                    "serviceName": "service_name",
                    "resource_type": "resource_type",
                    "resourceType": "resource_type",
                    "resource_id": "resource_id",
                    "resourceId": "resource_id",
                    "service_resource_id": "service_resource_id",
                    "serviceResourceId": "service_resource_id",
                    "service_version_id": "service_version_id",
                    "serviceVersionId": "service_version_id",
                    "parent_version_id": "parent_version_id",
                    "parentVersionId": "parent_version_id",
                    "source_version_id": "source_version_id",
                    "sourceVersionId": "source_version_id",
                    "error": "error",
                }

                for source_key, target_key in field_map.items():
                    if source_key in data:
                        setattr(self, target_key, data.get(source_key))

                if "bundle" in data:
                    self.bundle = safe_dict(data.get("bundle"))

                if "metrics" in data:
                    self.metrics = safe_dict(data.get("metrics"))

                if "artifact_ref" in data or "artifactRef" in data:
                    self.artifact_ref = safe_dict(data.get("artifact_ref") or data.get("artifactRef"))

                if "artifact_refs" in data or "artifactRefs" in data:
                    self.artifact_refs = safe_dict(data.get("artifact_refs") or data.get("artifactRefs"))

                if "service_refs" in data or "serviceRefs" in data:
                    self.service_refs = safe_dict(data.get("service_refs") or data.get("serviceRefs"))

                if "input_refs" in data or "inputRefs" in data:
                    self.input_refs = safe_dict(data.get("input_refs") or data.get("inputRefs"))

                if "output_refs" in data or "outputRefs" in data:
                    self.output_refs = safe_dict(data.get("output_refs") or data.get("outputRefs"))

                if "snapshot_ref" in data or "snapshotRef" in data:
                    self.snapshot_ref = safe_dict(data.get("snapshot_ref") or data.get("snapshotRef"))

                if "request_payload" in data or "requestPayload" in data:
                    self.request_payload = safe_dict(data.get("request_payload") or data.get("requestPayload"))

                if "response_payload" in data or "responsePayload" in data:
                    self.response_payload = safe_dict(data.get("response_payload") or data.get("responsePayload"))

                if "tags" in data:
                    self.tags = safe_list(data.get("tags"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def mark_stored(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.status = VERSION_STATUS_STORED
                self.error = None

                if payload:
                    self.response_payload = safe_dict(payload)

                self.normalize()
                self.touch()

            except Exception:
                pass

        def mark_complete(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.status = VERSION_STATUS_COMPLETE
                self.error = None

                if payload:
                    self.response_payload = safe_dict(payload)

                self.normalize()
                self.touch()

            except Exception:
                pass

        def publish(self) -> None:
            try:
                self.status = VERSION_STATUS_PUBLISHED
                self.published_at = self.published_at or utcnow()
                self.touch()
            except Exception:
                pass

        def archive(self) -> None:
            try:
                self.status = VERSION_STATUS_ARCHIVED
                self.archived_at = self.archived_at or utcnow()
                self.touch()
            except Exception:
                pass

        def mark_failed(self, error: Any = "") -> None:
            try:
                self.status = VERSION_STATUS_FAILED
                self.failed_at = self.failed_at or utcnow()
                self.error = safe_str(error, "unknown error", 8000)
                self.touch()
            except Exception:
                pass

        def mark_deleted(self) -> None:
            try:
                self.status = VERSION_STATUS_DELETED
                self.touch()
            except Exception:
                pass

        def to_dict(
            self,
            *,
            include_private: bool = False,
            include_bundle: bool = True,
            include_metrics: bool = True,
            include_refs: bool = True,
            include_payloads: bool = False,
        ) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "version_id": self.version_id,
                    "versionId": self.version_id,
                    "public_id": self.version_id,
                    "project_id": self.project_id,
                    "projectId": self.project_id,
                    "project_public_id": self.project_public_id,
                    "projectPublicId": self.project_public_id,
                    "conversation_id": self.conversation_id,
                    "conversationId": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "version_no": self.version_no,
                    "versionNo": self.version_no,
                    "label": self.label or self.display_label,
                    "title": self.title or self.label or self.display_label,
                    "display_label": self.display_label,
                    "displayLabel": self.display_label,
                    "description": self.description or "",
                    "kind": self.kind,
                    "type": self.kind,
                    "status": self.status,
                    "is_complete": self.is_complete,
                    "isComplete": self.is_complete,
                    "is_published": self.is_published,
                    "isPublished": self.is_published,
                    "is_archived": self.is_archived,
                    "isArchived": self.is_archived,
                    "is_failed": self.is_failed,
                    "isFailed": self.is_failed,
                    "source_template_id": self.source_template_id,
                    "sourceTemplateId": self.source_template_id,
                    "change_summary": self.change_summary or "",
                    "changeSummary": self.change_summary or "",
                    "created_by": self.created_by,
                    "createdBy": self.created_by,
                    "created_by_user_id": self.created_by_user_id,
                    "createdByUserId": self.created_by_user_id,
                    "source_service": self.source_service,
                    "sourceService": self.source_service,
                    "service": self.service_key,
                    "service_name": self.service_name,
                    "serviceName": self.service_name,
                    "resource_type": self.resource_type,
                    "resourceType": self.resource_type,
                    "resource_id": self.resource_id,
                    "resourceId": self.resource_id,
                    "service_resource_id": self.service_resource_id,
                    "serviceResourceId": self.service_resource_id,
                    "service_version_id": self.service_version_id,
                    "serviceVersionId": self.service_version_id,
                    "parent_version_id": self.parent_version_id,
                    "parentVersionId": self.parent_version_id,
                    "source_version_id": self.source_version_id,
                    "sourceVersionId": self.source_version_id,
                    "tags": safe_list(self.tags),
                    "published_at": isoformat(self.published_at),
                    "publishedAt": isoformat(self.published_at),
                    "archived_at": isoformat(self.archived_at),
                    "archivedAt": isoformat(self.archived_at),
                    "failed_at": isoformat(self.failed_at),
                    "failedAt": isoformat(self.failed_at),
                    "error": self.error,
                    "created_at": isoformat(self.created_at),
                    "createdAt": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "updatedAt": isoformat(self.updated_at),
                }

                if include_bundle:
                    payload["bundle"] = safe_dict(self.bundle)

                if include_metrics:
                    payload["metrics"] = safe_dict(self.metrics)

                if include_refs:
                    payload["artifact_ref"] = safe_dict(self.artifact_ref)
                    payload["artifactRef"] = safe_dict(self.artifact_ref)
                    payload["artifact_refs"] = safe_dict(self.artifact_refs)
                    payload["artifactRefs"] = safe_dict(self.artifact_refs)
                    payload["service_refs"] = safe_dict(self.service_refs)
                    payload["serviceRefs"] = safe_dict(self.service_refs)
                    payload["input_refs"] = safe_dict(self.input_refs)
                    payload["inputRefs"] = safe_dict(self.input_refs)
                    payload["output_refs"] = safe_dict(self.output_refs)
                    payload["outputRefs"] = safe_dict(self.output_refs)
                    payload["snapshot_ref"] = safe_dict(self.snapshot_ref)
                    payload["snapshotRef"] = safe_dict(self.snapshot_ref)

                if include_payloads or include_private:
                    payload["request_payload"] = safe_dict(self.request_payload)
                    payload["requestPayload"] = safe_dict(self.request_payload)
                    payload["response_payload"] = safe_dict(self.response_payload)
                    payload["responsePayload"] = safe_dict(self.response_payload)

                if include_private:
                    payload["metadata"] = safe_dict(self.metadata_json)
                    payload["updated_by_user_id"] = self.updated_by_user_id
                    payload["updatedByUserId"] = self.updated_by_user_id

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "version_id": getattr(self, "version_id", None),
                    "project_id": getattr(self, "project_id", None),
                    "version_no": getattr(self, "version_no", None),
                    "status": getattr(self, "status", None),
                }

        def to_sidebar_item(self) -> Dict[str, Any]:
            try:
                return {
                    "id": self.version_id,
                    "version_id": self.version_id,
                    "title": self.display_label,
                    "subtitle": self.change_summary or self.kind or "Version",
                    "status": self.status,
                    "kind": self.kind,
                    "version_no": self.version_no,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }
            except Exception:
                return {
                    "id": getattr(self, "version_id", None),
                    "title": "Version",
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any = None,
            project_public_id: Any = "",
            conversation_id: Any = "",
            version_no: Any = 1,
            kind: Any = VERSION_KIND_PROJECT,
            status: Any = VERSION_STATUS_STORED,
            label: str = "",
            change_summary: str = "",
            created_by: Any = "",
            created_by_user_id: Optional[int] = None,
            payload: Optional[Mapping[str, Any]] = None,
        ) -> "ProjectVersion":
            version = cls()
            version.project_id = safe_int(project_id, 0) or None
            version.project_public_id = safe_str(project_public_id, "", 120) or None
            version.conversation_id = safe_str(conversation_id, "", 80) or None
            version.version_no = safe_int(version_no, 1, minimum=1)
            version.kind = normalize_version_kind(kind, VERSION_KIND_PROJECT)
            version.status = normalize_version_status(status, VERSION_STATUS_STORED)
            version.label = safe_str(label, "", 255) or None
            version.change_summary = safe_str(change_summary, "", 8000) or None
            version.created_by = safe_str(created_by, "", 160) or None
            version.created_by_user_id = safe_int(created_by_user_id, 0) or None
            version.bundle = {}
            version.metrics = {}
            version.artifact_refs = {}
            version.service_refs = {}
            version.input_refs = {}
            version.output_refs = {}
            version.tags = []
            version.metadata_json = {}

            if payload:
                version.update_from_payload(payload)

            version.normalize()

            return version

    return ProjectVersion


ProjectVersion = _resolve_model(
    "ProjectVersion",
    "project_versions",
    _define_project_version_model,
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def build_project_version(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    version_no: Any = 1,
    kind: Any = VERSION_KIND_PROJECT,
    status: Any = VERSION_STATUS_STORED,
    label: str = "",
    change_summary: str = "",
    created_by: Any = "",
    created_by_user_id: Optional[int] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> ProjectVersion:
    try:
        if hasattr(ProjectVersion, "build"):
            return ProjectVersion.build(
                project_id=project_id,
                project_public_id=project_public_id,
                conversation_id=conversation_id,
                version_no=version_no,
                kind=kind,
                status=status,
                label=label,
                change_summary=change_summary,
                created_by=created_by,
                created_by_user_id=created_by_user_id,
                payload=payload,
            )

        version = ProjectVersion()
        version.project_id = safe_int(project_id, 0) or None
        version.project_public_id = safe_str(project_public_id, "", 120) or None
        version.conversation_id = safe_str(conversation_id, "", 80) or None
        version.version_no = safe_int(version_no, 1, minimum=1)
        version.kind = normalize_version_kind(kind)
        version.status = normalize_version_status(status)
        version.label = safe_str(label, "", 255) or None
        version.change_summary = safe_str(change_summary, "", 8000) or None
        version.created_by = safe_str(created_by, "", 160) or None
        version.created_by_user_id = safe_int(created_by_user_id, 0) or None

        if hasattr(version, "update_from_payload") and payload:
            version.update_from_payload(payload)

        if hasattr(version, "normalize"):
            version.normalize()

        return version

    except Exception:
        version = ProjectVersion()
        try:
            version.project_id = safe_int(project_id, 0) or None
            version.version_no = safe_int(version_no, 1, minimum=1)
            version.kind = normalize_version_kind(kind)
            version.status = normalize_version_status(status)
        except Exception:
            pass
        return version


def get_project_version_by_id(version_ref: Any) -> Optional[ProjectVersion]:
    try:
        value = safe_str(version_ref, "", 180)
        if not value:
            return None

        numeric_id = safe_int(value, 0)
        if numeric_id:
            item = ProjectVersion.query.get(numeric_id)
            if item is not None:
                return item

        return ProjectVersion.query.filter_by(version_id=value).one_or_none()

    except Exception:
        return None


def get_project_version_by_public_id(version_id: Any) -> Optional[ProjectVersion]:
    try:
        value = safe_str(version_id, "", 180)
        if not value:
            return None

        return ProjectVersion.query.filter_by(version_id=value).one_or_none()

    except Exception:
        return None


def next_project_version_no(project_id: Any = None, conversation_id: Any = None) -> int:
    try:
        query = ProjectVersion.query

        resolved_project_id = safe_int(project_id, 0)
        resolved_conversation_id = safe_str(conversation_id, "", 80)

        if resolved_project_id:
            query = query.filter_by(project_id=resolved_project_id)
        elif resolved_conversation_id:
            query = query.filter_by(conversation_id=resolved_conversation_id)
        else:
            return 1

        max_value = query.with_entities(db.func.max(ProjectVersion.version_no)).scalar()
        return safe_int(max_value, 0, minimum=0) + 1

    except Exception:
        return 1


def list_project_versions(
    *,
    project_id: Any = None,
    conversation_id: Any = None,
    kind: Any = None,
    status: Any = None,
    include_archived: bool = False,
    include_deleted: bool = False,
    limit: int = 100,
) -> List[ProjectVersion]:
    try:
        query = ProjectVersion.query

        resolved_project_id = safe_int(project_id, 0)
        resolved_conversation_id = safe_str(conversation_id, "", 80)

        if resolved_project_id:
            query = query.filter_by(project_id=resolved_project_id)

        if resolved_conversation_id:
            query = query.filter_by(conversation_id=resolved_conversation_id)

        if kind:
            query = query.filter_by(kind=normalize_version_kind(kind))

        if status:
            query = query.filter_by(status=normalize_version_status(status))
        else:
            if not include_archived:
                query = query.filter(ProjectVersion.status != VERSION_STATUS_ARCHIVED)
            if not include_deleted:
                query = query.filter(ProjectVersion.status != VERSION_STATUS_DELETED)

        return list(
            query.order_by(
                ProjectVersion.version_no.desc(),
                ProjectVersion.created_at.desc(),
            )
            .limit(safe_int(limit, 100, minimum=1, maximum=1000))
            .all()
        )

    except Exception:
        return []


def get_latest_project_version(
    *,
    project_id: Any = None,
    conversation_id: Any = None,
    kind: Any = None,
) -> Optional[ProjectVersion]:
    try:
        items = list_project_versions(
            project_id=project_id,
            conversation_id=conversation_id,
            kind=kind,
            include_archived=False,
            include_deleted=False,
            limit=1,
        )

        return items[0] if items else None

    except Exception:
        return None


def serialize_project_version(
    version: Any,
    *,
    include_private: bool = False,
    include_bundle: bool = True,
    include_metrics: bool = True,
    include_refs: bool = True,
    include_payloads: bool = False,
) -> Dict[str, Any]:
    try:
        if version is None:
            return {}

        if hasattr(version, "to_dict"):
            try:
                return version.to_dict(
                    include_private=include_private,
                    include_bundle=include_bundle,
                    include_metrics=include_metrics,
                    include_refs=include_refs,
                    include_payloads=include_payloads,
                )
            except TypeError:
                return version.to_dict()

        return {
            "id": getattr(version, "id", None),
            "version_id": getattr(version, "version_id", None),
            "project_id": getattr(version, "project_id", None),
            "conversation_id": getattr(version, "conversation_id", None),
            "version_no": getattr(version, "version_no", None),
            "label": getattr(version, "label", None),
            "kind": getattr(version, "kind", None),
            "status": getattr(version, "status", None),
            "created_at": isoformat(getattr(version, "created_at", None)),
            "updated_at": isoformat(getattr(version, "updated_at", None)),
        }

    except Exception:
        return {}


def serialize_project_versions(versions: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_project_version(item, **kwargs)
            for item in list(versions or [])
            if item is not None
        ]
    except Exception:
        return []


def create_project_version(
    *,
    project_id: Any = None,
    project_public_id: Any = "",
    conversation_id: Any = "",
    version_no: Any = None,
    kind: Any = VERSION_KIND_PROJECT,
    status: Any = VERSION_STATUS_STORED,
    label: str = "",
    change_summary: str = "",
    created_by: Any = "",
    created_by_user_id: Optional[int] = None,
    payload: Optional[Mapping[str, Any]] = None,
    commit: bool = True,
) -> ProjectVersion:
    resolved_version_no = safe_int(version_no, 0)
    if not resolved_version_no:
        resolved_version_no = next_project_version_no(project_id=project_id, conversation_id=conversation_id)

    version = build_project_version(
        project_id=project_id,
        project_public_id=project_public_id,
        conversation_id=conversation_id,
        version_no=resolved_version_no,
        kind=kind,
        status=status,
        label=label,
        change_summary=change_summary,
        created_by=created_by,
        created_by_user_id=created_by_user_id,
        payload=payload,
    )

    try:
        db.session.add(version)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return version

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return version


def get_project_versions_model_classes() -> List[Any]:
    return [ProjectVersion]


def get_project_versions_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(ProjectVersion.query.count())
        except Exception:
            count = -1

        return {
            "ok": True,
            "models": ["ProjectVersion"],
            "tables": [getattr(ProjectVersion, "__tablename__", "project_versions")],
            "count": count,
            "kinds": sorted(VERSION_KINDS),
            "statuses": sorted(VERSION_STATUSES),
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectVersion"],
            "tables": ["project_versions"],
            "error": str(exc),
        }


ProjectVersionLink = ProjectVersion


__all__ = [
    "VERSION_KIND_PROJECT",
    "VERSION_KIND_METADATA",
    "VERSION_KIND_SERVICE_LINK",
    "VERSION_KIND_CHUNK",
    "VERSION_KIND_EDITOR3D",
    "VERSION_KIND_MAP",
    "VERSION_KIND_2D",
    "VERSION_KIND_LV",
    "VERSION_KIND_FILE",
    "VERSION_KIND_SNAPSHOT",
    "VERSION_KIND_EXPORT",
    "VERSION_KINDS",
    "VERSION_STATUS_DRAFT",
    "VERSION_STATUS_PENDING",
    "VERSION_STATUS_STORED",
    "VERSION_STATUS_COMPLETE",
    "VERSION_STATUS_PUBLISHED",
    "VERSION_STATUS_FAILED",
    "VERSION_STATUS_ARCHIVED",
    "VERSION_STATUS_DELETED",
    "VERSION_STATUSES",
    "ProjectVersion",
    "ProjectVersionLink",
    "normalize_version_kind",
    "normalize_version_status",
    "normalize_service_name",
    "build_project_version",
    "get_project_version_by_id",
    "get_project_version_by_public_id",
    "next_project_version_no",
    "list_project_versions",
    "get_latest_project_version",
    "serialize_project_version",
    "serialize_project_versions",
    "create_project_version",
    "get_project_versions_model_classes",
    "get_project_versions_model_status",
]