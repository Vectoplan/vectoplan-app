# services/vectoplan-app/models/project_links.py
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
    safe_slug,
    safe_str,
    utcnow,
)


SERVICE_APP = "app"
SERVICE_CHAT = "chat"
SERVICE_CHUNK = "chunk"
SERVICE_EDITOR3D = "editor3d"
SERVICE_OPENLAYER = "openlayer"
SERVICE_2D = "cad2d"
SERVICE_LV = "lv"
SERVICE_GEOSERVER = "geoserver"
SERVICE_FILES = "files"
SERVICE_VERSIONING = "versioning"
SERVICE_EXTERNAL = "external"

KNOWN_SERVICES = {
    SERVICE_APP,
    SERVICE_CHAT,
    SERVICE_CHUNK,
    SERVICE_EDITOR3D,
    SERVICE_OPENLAYER,
    SERVICE_2D,
    SERVICE_LV,
    SERVICE_GEOSERVER,
    SERVICE_FILES,
    SERVICE_VERSIONING,
    SERVICE_EXTERNAL,
}

RESOURCE_PROJECT = "project"
RESOURCE_CONVERSATION = "conversation"
RESOURCE_WORLD = "world"
RESOURCE_CHUNK_PROJECT = "chunk_project"
RESOURCE_CHUNK_UNIVERSE = "universe"
RESOURCE_PLAN2D = "plan2d"
RESOURCE_LV = "lv"
RESOURCE_BLOB = "blob"
RESOURCE_VERSION = "version"
RESOURCE_ARTIFACT = "artifact"
RESOURCE_DATASET = "dataset"
RESOURCE_LAYER = "layer"
RESOURCE_URL = "url"

KNOWN_RESOURCE_TYPES = {
    RESOURCE_PROJECT,
    RESOURCE_CONVERSATION,
    RESOURCE_WORLD,
    RESOURCE_CHUNK_PROJECT,
    RESOURCE_CHUNK_UNIVERSE,
    RESOURCE_PLAN2D,
    RESOURCE_LV,
    RESOURCE_BLOB,
    RESOURCE_VERSION,
    RESOURCE_ARTIFACT,
    RESOURCE_DATASET,
    RESOURCE_LAYER,
    RESOURCE_URL,
}

CHUNK_RESOURCE_TYPES = {
    RESOURCE_CHUNK_PROJECT,
    RESOURCE_CHUNK_UNIVERSE,
    RESOURCE_WORLD,
}

LINK_STATUS_ACTIVE = "active"
LINK_STATUS_PENDING = "pending"
LINK_STATUS_DISABLED = "disabled"
LINK_STATUS_ERROR = "error"
LINK_STATUS_DELETED = "deleted"

LINK_STATUSES = {
    LINK_STATUS_ACTIVE,
    LINK_STATUS_PENDING,
    LINK_STATUS_DISABLED,
    LINK_STATUS_ERROR,
    LINK_STATUS_DELETED,
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

    While models/core.py still defines ProjectServiceLink, this module can
    return that already registered model. If the registered model does not
    expose the chunk helper methods added here, this module defines its own
    mapper with extend_existing=True.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None and hasattr(model, "apply_chunk_refs"):
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

def normalize_service(value: Any, default: str = SERVICE_EXTERNAL) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "vectoplan_app": SERVICE_APP,
            "app_shell": SERVICE_APP,
            "portal": SERVICE_APP,
            "conversation": SERVICE_CHAT,
            "chat_history": SERVICE_CHAT,
            "vectoplan_chunk": SERVICE_CHUNK,
            "chunk_service": SERVICE_CHUNK,
            "chunks": SERVICE_CHUNK,
            "3d": SERVICE_EDITOR3D,
            "editor": SERVICE_EDITOR3D,
            "editor_3d": SERVICE_EDITOR3D,
            "vectoplan_editor": SERVICE_EDITOR3D,
            "map": SERVICE_OPENLAYER,
            "open_layer": SERVICE_OPENLAYER,
            "openlayers": SERVICE_OPENLAYER,
            "cad": SERVICE_2D,
            "2d": SERVICE_2D,
            "cad_2d": SERVICE_2D,
            "plan": SERVICE_2D,
            "plan2d": SERVICE_2D,
            "leistungsverzeichnis": SERVICE_LV,
            "boq": SERVICE_LV,
            "bill_of_quantities": SERVICE_LV,
            "geo": SERVICE_GEOSERVER,
            "wfs": SERVICE_GEOSERVER,
            "file": SERVICE_FILES,
            "blob": SERVICE_FILES,
            "versions": SERVICE_VERSIONING,
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def normalize_resource_type(value: Any, default: str = RESOURCE_ARTIFACT) -> str:
    try:
        text = safe_slug(value, default=default, max_len=80).replace("-", "_")

        aliases = {
            "chat": RESOURCE_CONVERSATION,
            "conversation_id": RESOURCE_CONVERSATION,
            "chunk": RESOURCE_CHUNK_PROJECT,
            "chunks": RESOURCE_CHUNK_PROJECT,
            "chunk_project": RESOURCE_CHUNK_PROJECT,
            "chunk_project_id": RESOURCE_CHUNK_PROJECT,
            "chunkproject": RESOURCE_CHUNK_PROJECT,
            "project_chunk": RESOURCE_CHUNK_PROJECT,
            "chunk_universe": RESOURCE_CHUNK_UNIVERSE,
            "chunk_universe_id": RESOURCE_CHUNK_UNIVERSE,
            "universe": RESOURCE_CHUNK_UNIVERSE,
            "universe_id": RESOURCE_CHUNK_UNIVERSE,
            "chunk_world": RESOURCE_WORLD,
            "chunk_world_id": RESOURCE_WORLD,
            "world_id": RESOURCE_WORLD,
            "world_instance": RESOURCE_WORLD,
            "worldinstance": RESOURCE_WORLD,
            "plan": RESOURCE_PLAN2D,
            "plan_2d": RESOURCE_PLAN2D,
            "cad": RESOURCE_PLAN2D,
            "cad2d": RESOURCE_PLAN2D,
            "boq": RESOURCE_LV,
            "leistungsverzeichnis": RESOURCE_LV,
            "file": RESOURCE_BLOB,
            "upload": RESOURCE_BLOB,
            "revision": RESOURCE_VERSION,
            "dataset_id": RESOURCE_DATASET,
            "typename": RESOURCE_LAYER,
            "link": RESOURCE_URL,
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def normalize_link_status(value: Any, default: str = LINK_STATUS_ACTIVE) -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "ok": LINK_STATUS_ACTIVE,
            "ready": LINK_STATUS_ACTIVE,
            "enabled": LINK_STATUS_ACTIVE,
            "live": LINK_STATUS_ACTIVE,
            "linked": LINK_STATUS_ACTIVE,
            "provisioned": LINK_STATUS_ACTIVE,
            "created": LINK_STATUS_ACTIVE,
            "queued": LINK_STATUS_PENDING,
            "draft": LINK_STATUS_PENDING,
            "waiting": LINK_STATUS_PENDING,
            "provisioning": LINK_STATUS_PENDING,
            "inactive": LINK_STATUS_DISABLED,
            "off": LINK_STATUS_DISABLED,
            "failed": LINK_STATUS_ERROR,
            "failure": LINK_STATUS_ERROR,
            "unavailable": LINK_STATUS_ERROR,
            "removed": LINK_STATUS_DELETED,
        }

        normalized = aliases.get(text, text)

        if normalized not in LINK_STATUSES:
            return default

        return normalized

    except Exception:
        return default


def normalize_capabilities(value: Any) -> Dict[str, bool]:
    try:
        data = safe_dict(value)
        result: Dict[str, bool] = {}

        for key, item in data.items():
            clean_key = safe_slug(key, "", max_len=80)
            if not clean_key:
                continue
            result[clean_key] = safe_bool(item, False)

        return result

    except Exception:
        return {}


def default_resource_type_for_service(service: Any) -> str:
    try:
        normalized = normalize_service(service)

        mapping = {
            SERVICE_APP: RESOURCE_PROJECT,
            SERVICE_CHAT: RESOURCE_CONVERSATION,
            SERVICE_CHUNK: RESOURCE_CHUNK_PROJECT,
            SERVICE_EDITOR3D: RESOURCE_WORLD,
            SERVICE_OPENLAYER: RESOURCE_DATASET,
            SERVICE_2D: RESOURCE_PLAN2D,
            SERVICE_LV: RESOURCE_LV,
            SERVICE_GEOSERVER: RESOURCE_LAYER,
            SERVICE_FILES: RESOURCE_BLOB,
            SERVICE_VERSIONING: RESOURCE_VERSION,
        }

        return mapping.get(normalized, RESOURCE_ARTIFACT)

    except Exception:
        return RESOURCE_ARTIFACT


def _external_url_allowed(value: Any) -> bool:
    try:
        text = safe_str(value, "", 4000)

        if not text:
            return True

        if text.startswith("/"):
            return True

        if text.startswith("http://") or text.startswith("https://"):
            return True

        return False

    except Exception:
        return False


def _first_text(*values: Any, max_len: int = 255) -> str:
    try:
        for value in values:
            text = safe_str(value, "", max_len)
            if text:
                return text
    except Exception:
        pass

    return ""


def _route_hints_from_payload(value: Any) -> Dict[str, Any]:
    try:
        data = safe_dict(value)
        route_hints = (
            safe_dict(data.get("route_hints"))
            or safe_dict(data.get("routeHints"))
            or safe_dict(data.get("routes"))
            or safe_dict(data.get("links"))
        )
        return route_hints
    except Exception:
        return {}


def _extract_chunk_reference(value: Any) -> Dict[str, Any]:
    """
    Extract chunk ids and route hints from broad response/payload shapes.

    Accepted shapes:
    - direct snake_case/camelCase fields
    - {"ids": {...}}
    - {"chunk": {...}}
    - {"reference": {...}}
    - {"metadata": {"chunk": {...}}}
    - {"service_payload": {...}}
    """
    try:
        data = safe_dict(value)
        ids = safe_dict(data.get("ids"))
        chunk = safe_dict(data.get("chunk"))
        reference = safe_dict(data.get("reference") or data.get("ref"))
        metadata = safe_dict(data.get("metadata") or data.get("meta"))
        metadata_chunk = safe_dict(metadata.get("chunk"))
        service_payload = safe_dict(data.get("service_payload") or data.get("servicePayload") or data.get("payload"))

        route_hints = (
            _route_hints_from_payload(data)
            or _route_hints_from_payload(chunk)
            or _route_hints_from_payload(reference)
            or _route_hints_from_payload(metadata_chunk)
            or _route_hints_from_payload(service_payload)
        )

        error = (
            safe_dict(data.get("error"))
            or safe_dict(chunk.get("error"))
            or safe_dict(reference.get("error"))
            or safe_dict(metadata_chunk.get("error"))
        )

        return {
            "chunk_project_id": _first_text(
                data.get("chunk_project_id"),
                data.get("chunkProjectId"),
                data.get("external_project_id"),
                data.get("externalProjectId"),
                data.get("external_id"),
                ids.get("chunkProjectId"),
                ids.get("chunk_project_id"),
                chunk.get("chunk_project_id"),
                chunk.get("chunkProjectId"),
                reference.get("chunk_project_id"),
                reference.get("chunkProjectId"),
                reference.get("external_project_id"),
                reference.get("externalProjectId"),
                metadata_chunk.get("chunk_project_id"),
                metadata_chunk.get("chunkProjectId"),
                service_payload.get("chunk_project_id"),
                service_payload.get("chunkProjectId"),
                max_len=255,
            ),
            "chunk_universe_id": _first_text(
                data.get("chunk_universe_id"),
                data.get("chunkUniverseId"),
                data.get("external_universe_id"),
                data.get("externalUniverseId"),
                ids.get("chunkUniverseId"),
                ids.get("chunk_universe_id"),
                chunk.get("chunk_universe_id"),
                chunk.get("chunkUniverseId"),
                reference.get("chunk_universe_id"),
                reference.get("chunkUniverseId"),
                reference.get("external_universe_id"),
                reference.get("externalUniverseId"),
                metadata_chunk.get("chunk_universe_id"),
                metadata_chunk.get("chunkUniverseId"),
                service_payload.get("chunk_universe_id"),
                service_payload.get("chunkUniverseId"),
                max_len=255,
            ),
            "chunk_world_id": _first_text(
                data.get("chunk_world_id"),
                data.get("chunkWorldId"),
                data.get("external_world_id"),
                data.get("externalWorldId"),
                ids.get("chunkWorldId"),
                ids.get("chunk_world_id"),
                chunk.get("chunk_world_id"),
                chunk.get("chunkWorldId"),
                reference.get("chunk_world_id"),
                reference.get("chunkWorldId"),
                reference.get("external_world_id"),
                reference.get("externalWorldId"),
                metadata_chunk.get("chunk_world_id"),
                metadata_chunk.get("chunkWorldId"),
                service_payload.get("chunk_world_id"),
                service_payload.get("chunkWorldId"),
                max_len=255,
            ),
            "app_project_public_id": _first_text(
                data.get("app_project_public_id"),
                data.get("appProjectPublicId"),
                data.get("external_app_project_id"),
                data.get("externalAppProjectId"),
                ids.get("externalAppProjectId"),
                reference.get("app_project_public_id"),
                reference.get("appProjectPublicId"),
                metadata_chunk.get("app_project_public_id"),
                metadata_chunk.get("appProjectPublicId"),
                max_len=255,
            ),
            "status": _first_text(
                data.get("status"),
                data.get("chunk_status"),
                data.get("chunkStatus"),
                chunk.get("status"),
                reference.get("status"),
                metadata_chunk.get("status"),
                max_len=40,
            ),
            "route_hints": route_hints,
            "error": error,
            "raw": data,
        }

    except Exception:
        return {
            "chunk_project_id": "",
            "chunk_universe_id": "",
            "chunk_world_id": "",
            "app_project_public_id": "",
            "status": LINK_STATUS_ERROR,
            "route_hints": {},
            "error": {},
            "raw": {},
        }


def build_chunk_reference(
    *,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    app_project_public_id: Any = "",
    route_hints: Optional[Mapping[str, Any]] = None,
    status: Any = LINK_STATUS_ACTIVE,
    error: Optional[Mapping[str, Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build normalized chunk reference metadata."""
    try:
        clean_project_id = safe_str(chunk_project_id, "", 255) or None
        clean_universe_id = safe_str(chunk_universe_id, "", 255) or None
        clean_world_id = safe_str(chunk_world_id, "", 255) or None
        clean_app_project_id = safe_str(app_project_public_id, "", 255) or None

        has_refs = bool(clean_project_id or clean_universe_id or clean_world_id)

        clean_status = normalize_link_status(
            status,
            LINK_STATUS_ACTIVE if has_refs else LINK_STATUS_PENDING,
        )

        return {
            "app_project_public_id": clean_app_project_id,
            "appProjectPublicId": clean_app_project_id,
            "chunk_project_id": clean_project_id,
            "chunkProjectId": clean_project_id,
            "chunk_universe_id": clean_universe_id,
            "chunkUniverseId": clean_universe_id,
            "chunk_world_id": clean_world_id,
            "chunkWorldId": clean_world_id,
            "status": clean_status,
            "ready": bool(clean_project_id and clean_world_id and clean_status == LINK_STATUS_ACTIVE),
            "route_hints": safe_dict(route_hints),
            "routeHints": safe_dict(route_hints),
            "error": safe_dict(error),
            **safe_dict(extra),
        }

    except Exception:
        return {
            "app_project_public_id": None,
            "appProjectPublicId": None,
            "chunk_project_id": None,
            "chunkProjectId": None,
            "chunk_universe_id": None,
            "chunkUniverseId": None,
            "chunk_world_id": None,
            "chunkWorldId": None,
            "status": LINK_STATUS_ERROR,
            "ready": False,
            "route_hints": {},
            "routeHints": {},
            "error": {},
        }


def chunk_resource_id_for_type(
    *,
    resource_type: Any,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    fallback: Any = "",
) -> str:
    """Return the canonical resource_id for a chunk resource type."""
    normalized = normalize_resource_type(resource_type)

    if normalized == RESOURCE_WORLD:
        return _first_text(chunk_world_id, fallback, max_len=255)

    if normalized == RESOURCE_CHUNK_UNIVERSE:
        return _first_text(chunk_universe_id, fallback, max_len=255)

    if normalized == RESOURCE_CHUNK_PROJECT:
        return _first_text(chunk_project_id, fallback, max_len=255)

    return _first_text(fallback, chunk_world_id, chunk_universe_id, chunk_project_id, max_len=255)


# ─────────────────────────────────────────────────────────────
# ProjectServiceLink model
# ─────────────────────────────────────────────────────────────

def _define_project_service_link_model(*, extend_existing: bool = False):
    class ProjectServiceLink(TimestampMixin, SerializationMixin, db.Model):
        """
        Link between an app Project and external/internal microservice resources.

        This table stores references only:
        - chunk project id / universe id / world id
        - editor resource ref
        - OpenLayer dataset/layer ref
        - 2D plan ref
        - LV ref
        - file/blob/version/artifact refs

        It must not store service-owned truth such as chunk contents, map
        features, CAD geometry or LV line items.
        """

        __tablename__ = "project_service_links"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint(
                "project_id",
                "service",
                "resource_type",
                "resource_id",
                name="uq_project_service_links_project_service_resource",
            ),
        )

        id = db.Column(db.Integer, primary_key=True)

        link_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=lambda: public_id("psl"),
        )

        project_id = db.Column(db.Integer, nullable=False, index=True)

        service = db.Column(db.String(120), nullable=False, index=True)
        service_name = db.Column(db.String(120), nullable=True, index=True)
        service_label = db.Column(db.String(255), nullable=True)

        resource_type = db.Column(db.String(120), nullable=False, index=True)
        resource_id = db.Column(db.String(255), nullable=False, index=True)
        resource_label = db.Column(db.String(255), nullable=True)

        external_id = db.Column(db.String(255), nullable=True, index=True)
        parent_resource_id = db.Column(db.String(255), nullable=True, index=True)

        title = db.Column(db.String(255), nullable=True)
        description = db.Column(db.Text, nullable=True)

        status = db.Column(db.String(40), nullable=False, default=LINK_STATUS_ACTIVE, index=True)

        is_primary = db.Column(db.Boolean, nullable=False, default=False, index=True)
        is_enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
        is_required = db.Column(db.Boolean, nullable=False, default=False, index=True)
        is_public = db.Column(db.Boolean, nullable=False, default=False, index=True)

        # Browser-facing values.
        public_url = db.Column(db.Text, nullable=True)
        browser_url = db.Column(db.Text, nullable=True)
        iframe_url = db.Column(db.Text, nullable=True)
        api_path = db.Column(db.Text, nullable=True)

        # Server-only/internal references. These must only be serialized with
        # include_private=True.
        internal_url = db.Column(db.Text, nullable=True)
        internal_api_url = db.Column(db.Text, nullable=True)
        internal_ref = db.Column(json_type(), nullable=False, default=dict)

        capabilities = db.Column(json_type(), nullable=False, default=dict)
        service_payload = db.Column(json_type(), nullable=False, default=dict)
        reference = db.Column(json_type(), nullable=False, default=dict)
        settings = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        last_synced_at = db.Column(db.DateTime, nullable=True, index=True)
        sync_status = db.Column(db.String(40), nullable=True, index=True)
        sync_error = db.Column(db.Text, nullable=True)

        created_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        updated_by_user_id = db.Column(db.Integer, nullable=True, index=True)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectServiceLink project_id={self.project_id!r} "
                    f"service={self.service!r} resource_type={self.resource_type!r} "
                    f"resource_id={self.resource_id!r}>"
                )
            except Exception:
                return "<ProjectServiceLink>"

        @property
        def service_key(self) -> str:
            try:
                return normalize_service(self.service or self.service_name)
            except Exception:
                return SERVICE_EXTERNAL

        @property
        def resource_key(self) -> str:
            try:
                return normalize_resource_type(self.resource_type)
            except Exception:
                return RESOURCE_ARTIFACT

        @property
        def active(self) -> bool:
            try:
                return bool(self.is_enabled) and self.status == LINK_STATUS_ACTIVE
            except Exception:
                return False

        @property
        def url(self) -> str:
            try:
                return (
                    safe_str(self.public_url, "", 4000)
                    or safe_str(self.browser_url, "", 4000)
                    or safe_str(self.iframe_url, "", 4000)
                    or safe_str(self.api_path, "", 4000)
                )
            except Exception:
                return ""

        @property
        def is_chunk_link(self) -> bool:
            try:
                return self.service_key == SERVICE_CHUNK
            except Exception:
                return False

        @property
        def route_hints(self) -> Dict[str, Any]:
            try:
                reference = safe_dict(self.reference)
                metadata = safe_dict(self.metadata_json)
                service_payload = safe_dict(self.service_payload)

                return (
                    safe_dict(reference.get("route_hints"))
                    or safe_dict(reference.get("routeHints"))
                    or safe_dict(metadata.get("route_hints"))
                    or safe_dict(metadata.get("routeHints"))
                    or safe_dict(service_payload.get("route_hints"))
                    or safe_dict(service_payload.get("routeHints"))
                )
            except Exception:
                return {}

        @property
        def chunk_refs(self) -> Dict[str, Any]:
            try:
                if not self.is_chunk_link:
                    return {}

                extracted = _extract_chunk_reference(
                    {
                        "reference": self.reference,
                        "metadata": self.metadata_json,
                        "service_payload": self.service_payload,
                        "external_id": self.external_id,
                        "parent_resource_id": self.parent_resource_id,
                        "resource_id": self.resource_id,
                        "resource_type": self.resource_type,
                        "status": self.status,
                    }
                )

                resource_type = self.resource_key

                chunk_project_id = extracted.get("chunk_project_id")
                chunk_universe_id = extracted.get("chunk_universe_id")
                chunk_world_id = extracted.get("chunk_world_id")

                if resource_type == RESOURCE_CHUNK_PROJECT and not chunk_project_id:
                    chunk_project_id = self.resource_id

                if resource_type == RESOURCE_CHUNK_UNIVERSE and not chunk_universe_id:
                    chunk_universe_id = self.resource_id

                if resource_type == RESOURCE_WORLD and not chunk_world_id:
                    chunk_world_id = self.resource_id

                return build_chunk_reference(
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    app_project_public_id=extracted.get("app_project_public_id"),
                    route_hints=extracted.get("route_hints"),
                    status=self.status,
                    error=extracted.get("error"),
                )

            except Exception:
                return build_chunk_reference(status=LINK_STATUS_ERROR)

        @property
        def resource_ref(self) -> Dict[str, Any]:
            try:
                payload = {
                    "service": self.service_key,
                    "service_name": self.service_name or self.service_key,
                    "resource_type": self.resource_key,
                    "resource_id": self.resource_id,
                    "external_id": self.external_id,
                    "parent_resource_id": self.parent_resource_id,
                    "label": self.resource_label or self.title,
                }

                if self.is_chunk_link:
                    payload["chunk"] = self.chunk_refs
                    payload["routeHints"] = self.route_hints

                return payload
            except Exception:
                return {}

        def apply_chunk_refs(
            self,
            *,
            chunk_project_id: Any = "",
            chunk_universe_id: Any = "",
            chunk_world_id: Any = "",
            app_project_public_id: Any = "",
            route_hints: Optional[Mapping[str, Any]] = None,
            status: Any = LINK_STATUS_ACTIVE,
            error: Optional[Mapping[str, Any]] = None,
        ) -> "ProjectServiceLink":
            """Apply chunk reference metadata to this link."""
            try:
                reference = build_chunk_reference(
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    app_project_public_id=app_project_public_id,
                    route_hints=route_hints,
                    status=status,
                    error=error,
                )

                self.service = SERVICE_CHUNK
                self.service_name = SERVICE_CHUNK
                self.status = normalize_link_status(status, LINK_STATUS_ACTIVE if reference.get("ready") else LINK_STATUS_PENDING)
                self.is_enabled = self.status == LINK_STATUS_ACTIVE
                self.reference = {
                    **safe_dict(self.reference),
                    **reference,
                }
                self.metadata_json = {
                    **safe_dict(self.metadata_json),
                    "chunk": reference,
                }
                self.service_payload = {
                    **safe_dict(self.service_payload),
                    "chunk": reference,
                }

                resource_id = chunk_resource_id_for_type(
                    resource_type=self.resource_type,
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    fallback=self.resource_id,
                )

                if resource_id:
                    self.resource_id = resource_id

                if self.resource_key == RESOURCE_WORLD:
                    self.external_id = safe_str(chunk_world_id, "", 255) or self.external_id
                    self.parent_resource_id = safe_str(chunk_project_id, "", 255) or self.parent_resource_id
                elif self.resource_key == RESOURCE_CHUNK_UNIVERSE:
                    self.external_id = safe_str(chunk_universe_id, "", 255) or self.external_id
                    self.parent_resource_id = safe_str(chunk_project_id, "", 255) or self.parent_resource_id
                else:
                    self.external_id = safe_str(chunk_project_id, "", 255) or self.external_id

                self.last_synced_at = utcnow()
                self.sync_status = "synced" if self.status == LINK_STATUS_ACTIVE else self.status
                self.sync_error = safe_str(safe_dict(error).get("message"), "", 4000) or None

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def normalize(self) -> "ProjectServiceLink":
            try:
                if not self.link_id:
                    self.link_id = public_id("psl")

                self.project_id = safe_int(self.project_id, 0, minimum=1)

                self.service = normalize_service(self.service or self.service_name, SERVICE_EXTERNAL)
                self.service_name = safe_str(self.service_name or self.service, self.service, 120)
                self.service_label = safe_str(self.service_label, "", 255) or None

                self.resource_type = normalize_resource_type(
                    self.resource_type,
                    default_resource_type_for_service(self.service),
                )
                self.resource_id = safe_str(self.resource_id, "", 255)

                if self.service == SERVICE_CHUNK:
                    extracted = _extract_chunk_reference(
                        {
                            "reference": self.reference,
                            "metadata": self.metadata_json,
                            "service_payload": self.service_payload,
                            "external_id": self.external_id,
                            "parent_resource_id": self.parent_resource_id,
                            "resource_id": self.resource_id,
                            "resource_type": self.resource_type,
                            "status": self.status,
                        }
                    )

                    preferred_resource_id = chunk_resource_id_for_type(
                        resource_type=self.resource_type,
                        chunk_project_id=extracted.get("chunk_project_id"),
                        chunk_universe_id=extracted.get("chunk_universe_id"),
                        chunk_world_id=extracted.get("chunk_world_id"),
                        fallback=self.resource_id,
                    )

                    if preferred_resource_id:
                        self.resource_id = preferred_resource_id

                if not self.resource_id:
                    self.resource_id = self.link_id

                self.resource_label = safe_str(self.resource_label, "", 255) or None
                self.external_id = safe_str(self.external_id, "", 255) or None
                self.parent_resource_id = safe_str(self.parent_resource_id, "", 255) or None

                self.title = safe_str(self.title, "", 255) or self.resource_label or None
                self.description = safe_str(self.description, "", 4000) or None

                self.status = normalize_link_status(self.status, LINK_STATUS_ACTIVE)

                self.is_primary = safe_bool(self.is_primary, False)
                self.is_enabled = safe_bool(self.is_enabled, self.status == LINK_STATUS_ACTIVE)
                self.is_required = safe_bool(self.is_required, False)
                self.is_public = safe_bool(self.is_public, False)

                self.public_url = safe_str(self.public_url, "", 4000) or None
                self.browser_url = safe_str(self.browser_url, "", 4000) or None
                self.iframe_url = safe_str(self.iframe_url, "", 4000) or None
                self.api_path = safe_str(self.api_path, "", 4000) or None

                if not _external_url_allowed(self.public_url):
                    self.public_url = None

                if not _external_url_allowed(self.browser_url):
                    self.browser_url = None

                if not _external_url_allowed(self.iframe_url):
                    self.iframe_url = None

                if not _external_url_allowed(self.api_path):
                    self.api_path = None

                self.internal_url = safe_str(self.internal_url, "", 4000) or None
                self.internal_api_url = safe_str(self.internal_api_url, "", 4000) or None
                self.internal_ref = safe_dict(self.internal_ref)

                self.capabilities = normalize_capabilities(self.capabilities)
                self.service_payload = safe_dict(self.service_payload)
                self.reference = safe_dict(self.reference)
                self.settings = safe_dict(self.settings)
                self.metadata_json = safe_dict(self.metadata_json)

                if self.service == SERVICE_CHUNK:
                    refs = self.chunk_refs
                    self.reference = {
                        **self.reference,
                        **refs,
                    }
                    self.metadata_json = {
                        **self.metadata_json,
                        "chunk": refs,
                    }
                    self.service_payload = {
                        **self.service_payload,
                        "chunk": refs,
                    }

                    if refs.get("ready"):
                        self.status = LINK_STATUS_ACTIVE
                        self.is_enabled = True

                self.sync_status = safe_str(self.sync_status, "", 40) or None
                self.sync_error = safe_str(self.sync_error, "", 4000) or None

                self.created_by_user_id = safe_int(self.created_by_user_id, 0) or None
                self.updated_by_user_id = safe_int(self.updated_by_user_id, 0) or None

                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> "ProjectServiceLink":
            try:
                data = safe_dict(payload)

                field_map = {
                    "link_id": "link_id",
                    "linkId": "link_id",
                    "service": "service",
                    "service_name": "service_name",
                    "serviceName": "service_name",
                    "service_label": "service_label",
                    "serviceLabel": "service_label",
                    "resource_type": "resource_type",
                    "resourceType": "resource_type",
                    "kind": "resource_type",
                    "type": "resource_type",
                    "resource_id": "resource_id",
                    "resourceId": "resource_id",
                    "external_id": "external_id",
                    "externalId": "external_id",
                    "parent_resource_id": "parent_resource_id",
                    "parentResourceId": "parent_resource_id",
                    "resource_label": "resource_label",
                    "resourceLabel": "resource_label",
                    "title": "title",
                    "label": "title",
                    "description": "description",
                    "status": "status",
                    "is_primary": "is_primary",
                    "isPrimary": "is_primary",
                    "primary": "is_primary",
                    "is_enabled": "is_enabled",
                    "isEnabled": "is_enabled",
                    "enabled": "is_enabled",
                    "is_required": "is_required",
                    "isRequired": "is_required",
                    "required": "is_required",
                    "is_public": "is_public",
                    "isPublic": "is_public",
                    "public": "is_public",
                    "public_url": "public_url",
                    "publicUrl": "public_url",
                    "browser_url": "browser_url",
                    "browserUrl": "browser_url",
                    "iframe_url": "iframe_url",
                    "iframeUrl": "iframe_url",
                    "url": "public_url",
                    "href": "public_url",
                    "api_path": "api_path",
                    "apiPath": "api_path",
                    "internal_url": "internal_url",
                    "internalUrl": "internal_url",
                    "internal_api_url": "internal_api_url",
                    "internalApiUrl": "internal_api_url",
                    "sync_status": "sync_status",
                    "syncStatus": "sync_status",
                    "sync_error": "sync_error",
                    "syncError": "sync_error",
                    "created_by_user_id": "created_by_user_id",
                    "createdByUserId": "created_by_user_id",
                    "updated_by_user_id": "updated_by_user_id",
                    "updatedByUserId": "updated_by_user_id",
                }

                for source_key, target_key in field_map.items():
                    if source_key in data:
                        setattr(self, target_key, data.get(source_key))

                if "capabilities" in data:
                    self.capabilities = normalize_capabilities(data.get("capabilities"))

                if "service_payload" in data or "servicePayload" in data or "payload" in data:
                    self.service_payload = safe_dict(
                        data.get("service_payload")
                        or data.get("servicePayload")
                        or data.get("payload")
                    )

                if "reference" in data or "ref" in data:
                    self.reference = safe_dict(data.get("reference") or data.get("ref"))

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                if "internal_ref" in data or "internalRef" in data:
                    self.internal_ref = safe_dict(data.get("internal_ref") or data.get("internalRef"))

                chunk_data = _extract_chunk_reference(data)
                if any(
                    chunk_data.get(key)
                    for key in (
                        "chunk_project_id",
                        "chunk_universe_id",
                        "chunk_world_id",
                        "route_hints",
                        "app_project_public_id",
                    )
                ):
                    self.apply_chunk_refs(
                        chunk_project_id=chunk_data.get("chunk_project_id"),
                        chunk_universe_id=chunk_data.get("chunk_universe_id"),
                        chunk_world_id=chunk_data.get("chunk_world_id"),
                        app_project_public_id=chunk_data.get("app_project_public_id"),
                        route_hints=chunk_data.get("route_hints"),
                        status=chunk_data.get("status") or self.status,
                        error=chunk_data.get("error"),
                    )

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def mark_primary(self) -> None:
            try:
                self.is_primary = True
                self.touch()
            except Exception:
                pass

        def clear_primary(self) -> None:
            try:
                self.is_primary = False
                self.touch()
            except Exception:
                pass

        def enable(self) -> None:
            try:
                self.is_enabled = True
                self.status = LINK_STATUS_ACTIVE
                self.touch()
            except Exception:
                pass

        def disable(self) -> None:
            try:
                self.is_enabled = False
                self.status = LINK_STATUS_DISABLED
                self.touch()
            except Exception:
                pass

        def mark_pending(self) -> None:
            try:
                self.status = LINK_STATUS_PENDING
                self.is_enabled = True
                self.touch()
            except Exception:
                pass

        def mark_error(self, error: Any = "") -> None:
            try:
                self.status = LINK_STATUS_ERROR
                self.sync_status = LINK_STATUS_ERROR
                self.sync_error = safe_str(error, "", 4000) or None
                self.last_synced_at = utcnow()

                if self.is_chunk_link:
                    refs = self.chunk_refs
                    refs["status"] = LINK_STATUS_ERROR
                    refs["error"] = {"message": self.sync_error}
                    self.reference = {**safe_dict(self.reference), **refs}
                    self.metadata_json = {**safe_dict(self.metadata_json), "chunk": refs}

                self.touch()
            except Exception:
                pass

        def mark_synced(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.status = LINK_STATUS_ACTIVE
                self.sync_status = "synced"
                self.sync_error = None
                self.last_synced_at = utcnow()

                if payload:
                    self.service_payload = safe_dict(payload)
                    chunk_data = _extract_chunk_reference(payload)
                    if any(chunk_data.get(key) for key in ("chunk_project_id", "chunk_universe_id", "chunk_world_id")):
                        self.apply_chunk_refs(
                            chunk_project_id=chunk_data.get("chunk_project_id"),
                            chunk_universe_id=chunk_data.get("chunk_universe_id"),
                            chunk_world_id=chunk_data.get("chunk_world_id"),
                            app_project_public_id=chunk_data.get("app_project_public_id"),
                            route_hints=chunk_data.get("route_hints"),
                            status=LINK_STATUS_ACTIVE,
                        )

                self.touch()

            except Exception:
                pass

        def mark_deleted(self) -> None:
            try:
                self.status = LINK_STATUS_DELETED
                self.is_enabled = False
                self.touch()
            except Exception:
                pass

        def has_capability(self, capability: Any) -> bool:
            try:
                key = safe_slug(capability, "", max_len=80)
                if not key:
                    return False

                return safe_bool(safe_dict(self.capabilities).get(key), False)

            except Exception:
                return False

        def set_capability(self, capability: Any, value: Any = True) -> None:
            try:
                key = safe_slug(capability, "", max_len=80)
                if not key:
                    return

                capabilities = normalize_capabilities(self.capabilities)
                capabilities[key] = safe_bool(value, True)
                self.capabilities = capabilities
                self.touch()

            except Exception:
                pass

        def to_dict(self, *, include_private: bool = False) -> Dict[str, Any]:
            try:
                chunk_refs = self.chunk_refs if self.is_chunk_link else {}

                payload: Dict[str, Any] = {
                    "id": self.id,
                    "link_id": self.link_id,
                    "linkId": self.link_id,
                    "project_id": self.project_id,
                    "service": self.service_key,
                    "service_name": self.service_name or self.service_key,
                    "serviceName": self.service_name or self.service_key,
                    "service_label": self.service_label,
                    "serviceLabel": self.service_label,
                    "resource_type": self.resource_key,
                    "resourceType": self.resource_key,
                    "resource_id": self.resource_id,
                    "resourceId": self.resource_id,
                    "resource_label": self.resource_label,
                    "resourceLabel": self.resource_label,
                    "external_id": self.external_id,
                    "externalId": self.external_id,
                    "parent_resource_id": self.parent_resource_id,
                    "parentResourceId": self.parent_resource_id,
                    "title": self.title or self.resource_label or self.resource_id,
                    "label": self.title or self.resource_label or self.resource_id,
                    "description": self.description or "",
                    "status": self.status,
                    "active": self.active,
                    "is_primary": bool(self.is_primary),
                    "isPrimary": bool(self.is_primary),
                    "is_enabled": bool(self.is_enabled),
                    "isEnabled": bool(self.is_enabled),
                    "is_required": bool(self.is_required),
                    "isRequired": bool(self.is_required),
                    "is_public": bool(self.is_public),
                    "isPublic": bool(self.is_public),
                    "public_url": self.public_url,
                    "publicUrl": self.public_url,
                    "browser_url": self.browser_url,
                    "browserUrl": self.browser_url,
                    "iframe_url": self.iframe_url,
                    "iframeUrl": self.iframe_url,
                    "api_path": self.api_path,
                    "apiPath": self.api_path,
                    "url": self.url,
                    "href": self.url,
                    "capabilities": normalize_capabilities(self.capabilities),
                    "reference": safe_dict(self.reference),
                    "resource_ref": self.resource_ref,
                    "resourceRef": self.resource_ref,
                    "last_synced_at": isoformat(self.last_synced_at),
                    "lastSyncedAt": isoformat(self.last_synced_at),
                    "sync_status": self.sync_status,
                    "syncStatus": self.sync_status,
                    "sync_error": self.sync_error,
                    "syncError": self.sync_error,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if self.is_chunk_link:
                    payload["chunk"] = chunk_refs
                    payload["chunk_ready"] = safe_bool(chunk_refs.get("ready"), False)
                    payload["chunkReady"] = safe_bool(chunk_refs.get("ready"), False)
                    payload["chunk_project_id"] = chunk_refs.get("chunk_project_id")
                    payload["chunkProjectId"] = chunk_refs.get("chunk_project_id")
                    payload["chunk_universe_id"] = chunk_refs.get("chunk_universe_id")
                    payload["chunkUniverseId"] = chunk_refs.get("chunk_universe_id")
                    payload["chunk_world_id"] = chunk_refs.get("chunk_world_id")
                    payload["chunkWorldId"] = chunk_refs.get("chunk_world_id")
                    payload["route_hints"] = chunk_refs.get("route_hints") or {}
                    payload["routeHints"] = chunk_refs.get("route_hints") or {}

                if include_private:
                    payload["internal_url"] = self.internal_url
                    payload["internalUrl"] = self.internal_url
                    payload["internal_api_url"] = self.internal_api_url
                    payload["internalApiUrl"] = self.internal_api_url
                    payload["internal_ref"] = safe_dict(self.internal_ref)
                    payload["internalRef"] = safe_dict(self.internal_ref)
                    payload["service_payload"] = safe_dict(self.service_payload)
                    payload["servicePayload"] = safe_dict(self.service_payload)
                    payload["settings"] = safe_dict(self.settings)
                    payload["metadata"] = safe_dict(self.metadata_json)
                    payload["created_by_user_id"] = self.created_by_user_id
                    payload["updated_by_user_id"] = self.updated_by_user_id

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "project_id": getattr(self, "project_id", None),
                    "service": getattr(self, "service", SERVICE_EXTERNAL),
                    "resource_type": getattr(self, "resource_type", RESOURCE_ARTIFACT),
                    "resource_id": getattr(self, "resource_id", None),
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any,
            service: Any,
            resource_type: Any = "",
            resource_id: Any = "",
            title: str = "",
            payload: Optional[Mapping[str, Any]] = None,
            is_primary: bool = False,
            created_by_user_id: Optional[int] = None,
        ) -> "ProjectServiceLink":
            link = cls()
            link.project_id = safe_int(project_id, 0, minimum=1)
            link.service = normalize_service(service, SERVICE_EXTERNAL)
            link.service_name = link.service
            link.resource_type = normalize_resource_type(
                resource_type,
                default_resource_type_for_service(link.service),
            )

            payload_dict = safe_dict(payload)
            chunk_data = _extract_chunk_reference(payload_dict)

            link.resource_id = (
                safe_str(resource_id, "", 255)
                or chunk_resource_id_for_type(
                    resource_type=link.resource_type,
                    chunk_project_id=chunk_data.get("chunk_project_id"),
                    chunk_universe_id=chunk_data.get("chunk_universe_id"),
                    chunk_world_id=chunk_data.get("chunk_world_id"),
                    fallback="",
                )
                or public_id("res")
            )

            link.title = safe_str(title, "", 255) or None
            link.is_primary = bool(is_primary)
            link.created_by_user_id = safe_int(created_by_user_id, 0) or None
            link.updated_by_user_id = safe_int(created_by_user_id, 0) or None
            link.status = LINK_STATUS_ACTIVE
            link.is_enabled = True
            link.capabilities = {}
            link.service_payload = {}
            link.reference = {}
            link.settings = {}
            link.metadata_json = {}
            link.internal_ref = {}

            if payload:
                link.update_from_payload(payload)

            link.normalize()

            return link

    return ProjectServiceLink


ProjectServiceLink = _resolve_model(
    "ProjectServiceLink",
    "project_service_links",
    _define_project_service_link_model,
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def build_service_link(
    *,
    project_id: Any,
    service: Any,
    resource_type: Any = "",
    resource_id: Any = "",
    title: str = "",
    payload: Optional[Mapping[str, Any]] = None,
    is_primary: bool = False,
    created_by_user_id: Optional[int] = None,
) -> ProjectServiceLink:
    try:
        if hasattr(ProjectServiceLink, "build"):
            return ProjectServiceLink.build(
                project_id=project_id,
                service=service,
                resource_type=resource_type,
                resource_id=resource_id,
                title=title,
                payload=payload,
                is_primary=is_primary,
                created_by_user_id=created_by_user_id,
            )

        link = ProjectServiceLink()
        link.project_id = safe_int(project_id, 0, minimum=1)
        link.service = normalize_service(service)
        link.resource_type = normalize_resource_type(
            resource_type,
            default_resource_type_for_service(link.service),
        )
        link.resource_id = safe_str(resource_id, "", 255) or public_id("res")
        link.title = safe_str(title, "", 255) or None
        link.is_primary = bool(is_primary)

        if hasattr(link, "update_from_payload") and payload:
            link.update_from_payload(payload)

        if hasattr(link, "normalize"):
            link.normalize()

        return link

    except Exception:
        link = ProjectServiceLink()
        try:
            link.project_id = safe_int(project_id, 0)
            link.service = normalize_service(service)
            link.resource_type = normalize_resource_type(resource_type)
            link.resource_id = safe_str(resource_id, "", 255)
        except Exception:
            pass
        return link


def build_chunk_service_link(
    *,
    project_id: Any,
    resource_type: Any = RESOURCE_CHUNK_PROJECT,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    app_project_public_id: Any = "",
    route_hints: Optional[Mapping[str, Any]] = None,
    public_url: str = "",
    internal_url: str = "",
    status: str = LINK_STATUS_ACTIVE,
    is_primary: bool = False,
    created_by_user_id: Optional[int] = None,
) -> ProjectServiceLink:
    """Build a ProjectServiceLink for a chunk resource."""
    normalized_resource_type = normalize_resource_type(resource_type, RESOURCE_CHUNK_PROJECT)
    resource_id = chunk_resource_id_for_type(
        resource_type=normalized_resource_type,
        chunk_project_id=chunk_project_id,
        chunk_universe_id=chunk_universe_id,
        chunk_world_id=chunk_world_id,
        fallback="",
    )

    reference = build_chunk_reference(
        chunk_project_id=chunk_project_id,
        chunk_universe_id=chunk_universe_id,
        chunk_world_id=chunk_world_id,
        app_project_public_id=app_project_public_id,
        route_hints=route_hints,
        status=status,
    )

    return build_service_link(
        project_id=project_id,
        service=SERVICE_CHUNK,
        resource_type=normalized_resource_type,
        resource_id=resource_id or public_id("chunk"),
        title=f"chunk:{normalized_resource_type}",
        payload={
            "status": status,
            "public_url": public_url,
            "internal_url": internal_url,
            "reference": reference,
            "metadata": {
                "chunk": reference,
            },
            "service_payload": {
                "chunk": reference,
            },
        },
        is_primary=is_primary,
        created_by_user_id=created_by_user_id,
    )


def get_service_link_by_id(link_id: Any) -> Optional[ProjectServiceLink]:
    try:
        value = safe_str(link_id, "", 180)
        if not value:
            return None

        numeric_id = safe_int(value, 0)
        if numeric_id:
            item = ProjectServiceLink.query.get(numeric_id)
            if item is not None:
                return item

        return ProjectServiceLink.query.filter_by(link_id=value).one_or_none()

    except Exception:
        return None


def find_project_service_link(
    *,
    project_id: Any,
    service: Any,
    resource_type: Any = "",
    resource_id: Any = "",
    primary: Optional[bool] = None,
    active_only: bool = False,
) -> Optional[ProjectServiceLink]:
    try:
        resolved_project_id = safe_int(project_id, 0, minimum=1)

        if not resolved_project_id:
            return None

        normalized_service = normalize_service(service)

        query = ProjectServiceLink.query.filter_by(
            project_id=resolved_project_id,
            service=normalized_service,
        )

        if resource_type:
            query = query.filter_by(resource_type=normalize_resource_type(resource_type))

        if resource_id:
            query = query.filter_by(resource_id=safe_str(resource_id, "", 255))

        if primary is not None:
            query = query.filter_by(is_primary=bool(primary))

        if active_only:
            query = query.filter_by(status=LINK_STATUS_ACTIVE, is_enabled=True)

        return query.order_by(ProjectServiceLink.is_primary.desc(), ProjectServiceLink.updated_at.desc()).first()

    except Exception:
        return None


def find_chunk_service_link(
    *,
    project_id: Any,
    resource_type: Any = RESOURCE_CHUNK_PROJECT,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    active_only: bool = False,
) -> Optional[ProjectServiceLink]:
    """Find a chunk service link by canonical chunk ids."""
    normalized_resource_type = normalize_resource_type(resource_type, RESOURCE_CHUNK_PROJECT)
    resource_id = chunk_resource_id_for_type(
        resource_type=normalized_resource_type,
        chunk_project_id=chunk_project_id,
        chunk_universe_id=chunk_universe_id,
        chunk_world_id=chunk_world_id,
        fallback="",
    )

    if not resource_id:
        return None

    return find_project_service_link(
        project_id=project_id,
        service=SERVICE_CHUNK,
        resource_type=normalized_resource_type,
        resource_id=resource_id,
        active_only=active_only,
    )


def list_project_service_links(
    project_id: Any,
    *,
    service: Any = "",
    resource_type: Any = "",
    active_only: bool = False,
    include_disabled: bool = True,
) -> List[ProjectServiceLink]:
    try:
        resolved_project_id = safe_int(project_id, 0, minimum=1)

        if not resolved_project_id:
            return []

        query = ProjectServiceLink.query.filter_by(project_id=resolved_project_id)

        if service:
            query = query.filter_by(service=normalize_service(service))

        if resource_type:
            query = query.filter_by(resource_type=normalize_resource_type(resource_type))

        if active_only:
            query = query.filter_by(status=LINK_STATUS_ACTIVE, is_enabled=True)
        elif not include_disabled:
            query = query.filter(ProjectServiceLink.status != LINK_STATUS_DELETED)

        return list(
            query.order_by(
                ProjectServiceLink.service.asc(),
                ProjectServiceLink.is_primary.desc(),
                ProjectServiceLink.updated_at.desc(),
            ).all()
        )

    except Exception:
        return []


def list_project_chunk_links(
    project_id: Any,
    *,
    active_only: bool = False,
) -> List[ProjectServiceLink]:
    """List chunk-related links for one app project."""
    return list_project_service_links(
        project_id,
        service=SERVICE_CHUNK,
        active_only=active_only,
        include_disabled=not active_only,
    )


def serialize_service_link(link: Any, *, include_private: bool = False) -> Dict[str, Any]:
    try:
        if link is None:
            return {}

        if hasattr(link, "to_dict"):
            try:
                return link.to_dict(include_private=include_private)
            except TypeError:
                return link.to_dict()

        return {
            "id": getattr(link, "id", None),
            "link_id": getattr(link, "link_id", None),
            "project_id": getattr(link, "project_id", None),
            "service": getattr(link, "service", None),
            "resource_type": getattr(link, "resource_type", None),
            "resource_id": getattr(link, "resource_id", None),
            "status": getattr(link, "status", None),
        }

    except Exception:
        return {}


def serialize_service_links(links: Any, *, include_private: bool = False) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_service_link(item, include_private=include_private)
            for item in list(links or [])
            if item is not None
        ]
    except Exception:
        return []


def upsert_service_link(
    *,
    project_id: Any,
    service: Any,
    resource_type: Any = "",
    resource_id: Any = "",
    payload: Optional[Mapping[str, Any]] = None,
    is_primary: bool = False,
    commit: bool = True,
) -> ProjectServiceLink:
    resolved_project_id = safe_int(project_id, 0, minimum=1)
    normalized_service = normalize_service(service)
    payload_dict = safe_dict(payload)
    chunk_data = _extract_chunk_reference(payload_dict)

    normalized_resource_type = normalize_resource_type(
        resource_type or payload_dict.get("resource_type") or payload_dict.get("resourceType"),
        default_resource_type_for_service(normalized_service),
    )

    normalized_resource_id = (
        safe_str(resource_id, "", 255)
        or safe_str(payload_dict.get("resource_id") or payload_dict.get("resourceId"), "", 255)
    )

    if normalized_service == SERVICE_CHUNK and not normalized_resource_id:
        normalized_resource_id = chunk_resource_id_for_type(
            resource_type=normalized_resource_type,
            chunk_project_id=chunk_data.get("chunk_project_id"),
            chunk_universe_id=chunk_data.get("chunk_universe_id"),
            chunk_world_id=chunk_data.get("chunk_world_id"),
            fallback="",
        )

    try:
        link = None

        if normalized_resource_id:
            link = find_project_service_link(
                project_id=resolved_project_id,
                service=normalized_service,
                resource_type=normalized_resource_type,
                resource_id=normalized_resource_id,
            )

        if link is None and is_primary:
            link = find_project_service_link(
                project_id=resolved_project_id,
                service=normalized_service,
                resource_type=normalized_resource_type,
                primary=True,
            )

        if link is None:
            link = build_service_link(
                project_id=resolved_project_id,
                service=normalized_service,
                resource_type=normalized_resource_type,
                resource_id=normalized_resource_id or public_id("res"),
                payload=payload_dict,
                is_primary=is_primary,
            )
        else:
            if payload_dict and hasattr(link, "update_from_payload"):
                link.update_from_payload(payload_dict)

            if is_primary:
                try:
                    link.is_primary = True
                except Exception:
                    pass

            if hasattr(link, "normalize"):
                link.normalize()

        db.session.add(link)

        if is_primary:
            try:
                siblings = list_project_service_links(
                    resolved_project_id,
                    service=normalized_service,
                    resource_type=normalized_resource_type,
                    include_disabled=True,
                )

                for sibling in siblings:
                    if sibling is not link and getattr(sibling, "id", None) != getattr(link, "id", None):
                        sibling.is_primary = False
                        db.session.add(sibling)
            except Exception:
                pass

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return link

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return build_service_link(
            project_id=resolved_project_id,
            service=normalized_service,
            resource_type=normalized_resource_type,
            resource_id=normalized_resource_id or public_id("res"),
            payload=payload_dict,
            is_primary=is_primary,
        )


def upsert_chunk_service_link(
    *,
    project_id: Any,
    resource_type: Any = RESOURCE_CHUNK_PROJECT,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    app_project_public_id: Any = "",
    route_hints: Optional[Mapping[str, Any]] = None,
    public_url: str = "",
    internal_url: str = "",
    status: str = LINK_STATUS_ACTIVE,
    is_primary: bool = False,
    commit: bool = True,
) -> ProjectServiceLink:
    """Idempotently upsert one chunk service link."""
    normalized_resource_type = normalize_resource_type(resource_type, RESOURCE_CHUNK_PROJECT)
    reference = build_chunk_reference(
        chunk_project_id=chunk_project_id,
        chunk_universe_id=chunk_universe_id,
        chunk_world_id=chunk_world_id,
        app_project_public_id=app_project_public_id,
        route_hints=route_hints,
        status=status,
    )
    resource_id = chunk_resource_id_for_type(
        resource_type=normalized_resource_type,
        chunk_project_id=chunk_project_id,
        chunk_universe_id=chunk_universe_id,
        chunk_world_id=chunk_world_id,
        fallback=public_id("chunk"),
    )

    return upsert_service_link(
        project_id=project_id,
        service=SERVICE_CHUNK,
        resource_type=normalized_resource_type,
        resource_id=resource_id,
        payload={
            "status": status,
            "public_url": public_url,
            "internal_url": internal_url,
            "reference": reference,
            "metadata": {
                "chunk": reference,
            },
            "service_payload": {
                "chunk": reference,
            },
        },
        is_primary=is_primary,
        commit=commit,
    )


def upsert_chunk_service_links(
    *,
    project_id: Any,
    chunk_project_id: Any = "",
    chunk_universe_id: Any = "",
    chunk_world_id: Any = "",
    app_project_public_id: Any = "",
    route_hints: Optional[Mapping[str, Any]] = None,
    public_url: str = "",
    internal_url: str = "",
    status: str = LINK_STATUS_ACTIVE,
    commit: bool = True,
) -> List[ProjectServiceLink]:
    """
    Idempotently upsert the standard chunk link set:
    - chunk_project
    - universe, when available
    - world, when available
    """
    rows: List[ProjectServiceLink] = []

    try:
        rows.append(
            upsert_chunk_service_link(
                project_id=project_id,
                resource_type=RESOURCE_CHUNK_PROJECT,
                chunk_project_id=chunk_project_id,
                chunk_universe_id=chunk_universe_id,
                chunk_world_id=chunk_world_id,
                app_project_public_id=app_project_public_id,
                route_hints=route_hints,
                public_url=public_url,
                internal_url=internal_url,
                status=status,
                is_primary=True,
                commit=False,
            )
        )

        if safe_str(chunk_universe_id, "", 255):
            rows.append(
                upsert_chunk_service_link(
                    project_id=project_id,
                    resource_type=RESOURCE_CHUNK_UNIVERSE,
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    app_project_public_id=app_project_public_id,
                    route_hints=route_hints,
                    public_url=public_url,
                    internal_url=internal_url,
                    status=status,
                    is_primary=False,
                    commit=False,
                )
            )

        if safe_str(chunk_world_id, "", 255):
            rows.append(
                upsert_chunk_service_link(
                    project_id=project_id,
                    resource_type=RESOURCE_WORLD,
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    app_project_public_id=app_project_public_id,
                    route_hints=route_hints,
                    public_url=public_url,
                    internal_url=internal_url,
                    status=status,
                    is_primary=False,
                    commit=False,
                )
            )

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return rows

    except Exception:
        if commit:
            try:
                db.session.rollback()
            except Exception:
                pass
        return rows


def get_project_links_model_classes() -> List[Any]:
    return [ProjectServiceLink]


def get_project_links_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(ProjectServiceLink.query.count())
        except Exception:
            count = -1

        chunk_count = -1
        try:
            chunk_count = int(
                ProjectServiceLink.query.filter_by(service=SERVICE_CHUNK).count()
            )
        except Exception:
            chunk_count = -1

        return {
            "ok": True,
            "models": ["ProjectServiceLink"],
            "tables": [getattr(ProjectServiceLink, "__tablename__", "project_service_links")],
            "count": count,
            "chunkCount": chunk_count,
            "services": sorted(KNOWN_SERVICES),
            "resource_types": sorted(KNOWN_RESOURCE_TYPES),
            "chunkResourceTypes": sorted(CHUNK_RESOURCE_TYPES),
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectServiceLink"],
            "tables": ["project_service_links"],
            "error": str(exc),
        }


__all__ = [
    "SERVICE_APP",
    "SERVICE_CHAT",
    "SERVICE_CHUNK",
    "SERVICE_EDITOR3D",
    "SERVICE_OPENLAYER",
    "SERVICE_2D",
    "SERVICE_LV",
    "SERVICE_GEOSERVER",
    "SERVICE_FILES",
    "SERVICE_VERSIONING",
    "SERVICE_EXTERNAL",
    "KNOWN_SERVICES",
    "RESOURCE_PROJECT",
    "RESOURCE_CONVERSATION",
    "RESOURCE_WORLD",
    "RESOURCE_CHUNK_PROJECT",
    "RESOURCE_CHUNK_UNIVERSE",
    "RESOURCE_PLAN2D",
    "RESOURCE_LV",
    "RESOURCE_BLOB",
    "RESOURCE_VERSION",
    "RESOURCE_ARTIFACT",
    "RESOURCE_DATASET",
    "RESOURCE_LAYER",
    "RESOURCE_URL",
    "KNOWN_RESOURCE_TYPES",
    "CHUNK_RESOURCE_TYPES",
    "LINK_STATUS_ACTIVE",
    "LINK_STATUS_PENDING",
    "LINK_STATUS_DISABLED",
    "LINK_STATUS_ERROR",
    "LINK_STATUS_DELETED",
    "LINK_STATUSES",
    "ProjectServiceLink",
    "normalize_service",
    "normalize_resource_type",
    "normalize_link_status",
    "normalize_capabilities",
    "default_resource_type_for_service",
    "build_chunk_reference",
    "chunk_resource_id_for_type",
    "build_service_link",
    "build_chunk_service_link",
    "get_service_link_by_id",
    "find_project_service_link",
    "find_chunk_service_link",
    "list_project_service_links",
    "list_project_chunk_links",
    "serialize_service_link",
    "serialize_service_links",
    "upsert_service_link",
    "upsert_chunk_service_link",
    "upsert_chunk_service_links",
    "get_project_links_model_classes",
    "get_project_links_model_status",
]