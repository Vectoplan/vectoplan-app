# services/vectoplan-app/models/projects.py
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .base import (
    SerializationMixin,
    SoftDeleteMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    merge_dicts,
    normalize_status,
    normalize_visibility,
    project_public_id,
    safe_bool,
    safe_dict,
    safe_float,
    safe_int,
    safe_str,
    utcnow,
)


PROJECT_SETUP_DRAFT = "draft"
PROJECT_SETUP_DEFINED = "defined"
PROJECT_SETUP_CONFIGURED = "configured"

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUS_DELETED = "deleted"

PROJECT_VISIBILITY_PRIVATE = "private"
PROJECT_VISIBILITY_SHARED = "shared"
PROJECT_VISIBILITY_PUBLIC = "public"

CHUNK_STATUS_DISABLED = "disabled"
CHUNK_STATUS_PENDING = "pending"
CHUNK_STATUS_READY = "ready"
CHUNK_STATUS_ERROR = "error"

VALID_CHUNK_STATUSES = frozenset(
    {
        CHUNK_STATUS_DISABLED,
        CHUNK_STATUS_PENDING,
        CHUNK_STATUS_READY,
        CHUNK_STATUS_ERROR,
    }
)


# ─────────────────────────────────────────────────────────────
# Transitional import helpers
# ─────────────────────────────────────────────────────────────

def _metadata_has_table(table_name: str) -> bool:
    try:
        return str(table_name) in db.metadata.tables
    except Exception:
        return False


def _table_args(extend_existing: bool) -> Dict[str, Any]:
    try:
        return {"extend_existing": True} if extend_existing else {}
    except Exception:
        return {"extend_existing": True}


def _model_has_columns(model: Any, required_columns: List[str]) -> bool:
    try:
        table = getattr(model, "__table__", None)
        columns = getattr(table, "columns", None)

        if columns is None:
            return False

        available = {str(column.name) for column in columns}
        return all(column in available for column in required_columns)

    except Exception:
        return False


def _core_model_if_registered(model_name: str, table_name: str) -> Any:
    """
    Transitional guard.

    While models/core.py still defines Project, this module can return that
    already registered model instead of defining a duplicate SQLAlchemy table.

    However, if the registered model is missing the chunk-provisioning columns,
    this module defines its own extended model with extend_existing=True.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None and _model_has_columns(
                model,
                [
                    "chunk_project_id",
                    "chunk_universe_id",
                    "chunk_world_id",
                    "chunk_status",
                    "chunk_ready",
                ],
            ):
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

def normalize_project_setup_status(value: Any, default: str = PROJECT_SETUP_DRAFT) -> str:
    try:
        text = safe_str(value, default, 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "new": PROJECT_SETUP_DRAFT,
            "empty": PROJECT_SETUP_DRAFT,
            "draft": PROJECT_SETUP_DRAFT,
            "created": PROJECT_SETUP_DRAFT,
            "definition": PROJECT_SETUP_DEFINED,
            "defined": PROJECT_SETUP_DEFINED,
            "metadata": PROJECT_SETUP_DEFINED,
            "ready": PROJECT_SETUP_CONFIGURED,
            "complete": PROJECT_SETUP_CONFIGURED,
            "completed": PROJECT_SETUP_CONFIGURED,
            "configured": PROJECT_SETUP_CONFIGURED,
            "active": PROJECT_SETUP_CONFIGURED,
        }

        return aliases.get(text, text or default)

    except Exception:
        return default


def is_configured_status(value: Any) -> bool:
    try:
        return normalize_project_setup_status(value) in {
            PROJECT_SETUP_CONFIGURED,
            "ready",
            "complete",
            "completed",
            "active",
        }
    except Exception:
        return False


def normalize_project_status(value: Any, default: str = PROJECT_STATUS_ACTIVE) -> str:
    try:
        text = normalize_status(value, default)

        if text in {"archive", "archived"}:
            return PROJECT_STATUS_ARCHIVED

        if text in {"delete", "deleted", "removed"}:
            return PROJECT_STATUS_DELETED

        if text in {"active", "enabled", "live"}:
            return PROJECT_STATUS_ACTIVE

        return text or default

    except Exception:
        return default


def normalize_chunk_status(
    value: Any,
    default: str = CHUNK_STATUS_PENDING,
    *,
    has_refs: bool = False,
) -> str:
    try:
        text = safe_str(value, "", 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "": CHUNK_STATUS_READY if has_refs else default,
            "ok": CHUNK_STATUS_READY,
            "active": CHUNK_STATUS_READY,
            "linked": CHUNK_STATUS_READY,
            "provisioned": CHUNK_STATUS_READY,
            "created": CHUNK_STATUS_READY,
            "ready": CHUNK_STATUS_READY,
            "pending": CHUNK_STATUS_PENDING,
            "waiting": CHUNK_STATUS_PENDING,
            "queued": CHUNK_STATUS_PENDING,
            "disabled": CHUNK_STATUS_DISABLED,
            "off": CHUNK_STATUS_DISABLED,
            "error": CHUNK_STATUS_ERROR,
            "failed": CHUNK_STATUS_ERROR,
            "failure": CHUNK_STATUS_ERROR,
            "unavailable": CHUNK_STATUS_ERROR,
        }

        status = aliases.get(text, text or default)

        if status not in VALID_CHUNK_STATUSES:
            return CHUNK_STATUS_READY if has_refs else default

        return status

    except Exception:
        return CHUNK_STATUS_READY if has_refs else default


def _has_text(value: Any) -> bool:
    try:
        return bool(safe_str(value, "", 2000))
    except Exception:
        return False


def _clean_ref_id(value: Any, max_len: int = 160) -> Optional[str]:
    try:
        text = safe_str(value, "", max_len).strip()
        return text or None
    except Exception:
        return None


def _safe_route_hints(value: Any) -> Dict[str, Any]:
    try:
        return safe_dict(value)
    except Exception:
        return {}


def _chunk_payload_from_mapping(value: Any) -> Dict[str, Any]:
    try:
        data = safe_dict(value)
        chunk = safe_dict(data.get("chunk"))

        if not chunk and any(
            key in data
            for key in (
                "chunk_project_id",
                "chunkProjectId",
                "chunk_world_id",
                "chunkWorldId",
                "chunk_universe_id",
                "chunkUniverseId",
            )
        ):
            chunk = data

        ids = safe_dict(chunk.get("ids"))

        return {
            "status": (
                chunk.get("status")
                or chunk.get("chunkStatus")
                or data.get("chunk_status")
                or data.get("chunkStatus")
            ),
            "chunk_project_id": (
                chunk.get("chunk_project_id")
                or chunk.get("chunkProjectId")
                or ids.get("chunkProjectId")
                or ids.get("chunk_project_id")
                or data.get("chunk_project_id")
                or data.get("chunkProjectId")
            ),
            "chunk_universe_id": (
                chunk.get("chunk_universe_id")
                or chunk.get("chunkUniverseId")
                or ids.get("chunkUniverseId")
                or ids.get("chunk_universe_id")
                or data.get("chunk_universe_id")
                or data.get("chunkUniverseId")
            ),
            "chunk_world_id": (
                chunk.get("chunk_world_id")
                or chunk.get("chunkWorldId")
                or ids.get("chunkWorldId")
                or ids.get("chunk_world_id")
                or data.get("chunk_world_id")
                or data.get("chunkWorldId")
            ),
            "route_hints": (
                chunk.get("route_hints")
                or chunk.get("routeHints")
                or data.get("route_hints")
                or data.get("routeHints")
                or {}
            ),
            "error": (
                chunk.get("error")
                or data.get("chunk_error")
                or data.get("chunkError")
                or {}
            ),
            "provisioned_at": (
                chunk.get("provisioned_at")
                or chunk.get("provisionedAt")
                or data.get("chunk_provisioned_at")
                or data.get("chunkProvisionedAt")
            ),
        }

    except Exception:
        return {}


def build_chunk_refs(
    *,
    chunk_project_id: Any = None,
    chunk_universe_id: Any = None,
    chunk_world_id: Any = None,
    status: Any = None,
    route_hints: Any = None,
    error: Any = None,
    provisioned_at: Any = None,
) -> Dict[str, Any]:
    try:
        clean_chunk_project_id = _clean_ref_id(chunk_project_id)
        clean_chunk_universe_id = _clean_ref_id(chunk_universe_id)
        clean_chunk_world_id = _clean_ref_id(chunk_world_id)
        clean_route_hints = _safe_route_hints(route_hints)
        clean_error = safe_dict(error)

        has_refs = bool(clean_chunk_project_id and clean_chunk_world_id)
        clean_status = normalize_chunk_status(status, has_refs=has_refs)

        ready = bool(has_refs and clean_status == CHUNK_STATUS_READY)

        return {
            "status": clean_status,
            "ready": ready,
            "chunk_project_id": clean_chunk_project_id,
            "chunkProjectId": clean_chunk_project_id,
            "chunk_universe_id": clean_chunk_universe_id,
            "chunkUniverseId": clean_chunk_universe_id,
            "chunk_world_id": clean_chunk_world_id,
            "chunkWorldId": clean_chunk_world_id,
            "route_hints": clean_route_hints,
            "routeHints": clean_route_hints,
            "error": clean_error,
            "provisioned_at": isoformat(provisioned_at) if provisioned_at else None,
            "provisionedAt": isoformat(provisioned_at) if provisioned_at else None,
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
            "ready": False,
            "chunk_project_id": None,
            "chunkProjectId": None,
            "chunk_universe_id": None,
            "chunkUniverseId": None,
            "chunk_world_id": None,
            "chunkWorldId": None,
            "route_hints": {},
            "routeHints": {},
            "error": {},
            "provisioned_at": None,
            "provisionedAt": None,
        }


def _chunk_refs_from_sources(
    *,
    direct_project_id: Any = None,
    direct_universe_id: Any = None,
    direct_world_id: Any = None,
    direct_status: Any = None,
    direct_ready: Any = None,
    direct_route_hints: Any = None,
    direct_error: Any = None,
    direct_provisioned_at: Any = None,
    service_refs: Any = None,
    metadata_json: Any = None,
) -> Dict[str, Any]:
    try:
        refs = safe_dict(service_refs)
        metadata = safe_dict(metadata_json)

        chunk_from_refs = _chunk_payload_from_mapping(refs)
        chunk_from_meta = _chunk_payload_from_mapping(metadata)

        chunk_project_id = (
            _clean_ref_id(direct_project_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_project_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_project_id"))
        )

        chunk_universe_id = (
            _clean_ref_id(direct_universe_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_universe_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_universe_id"))
        )

        chunk_world_id = (
            _clean_ref_id(direct_world_id)
            or _clean_ref_id(chunk_from_refs.get("chunk_world_id"))
            or _clean_ref_id(chunk_from_meta.get("chunk_world_id"))
        )

        route_hints = (
            _safe_route_hints(direct_route_hints)
            or _safe_route_hints(chunk_from_refs.get("route_hints"))
            or _safe_route_hints(chunk_from_meta.get("route_hints"))
        )

        error = (
            safe_dict(direct_error)
            or safe_dict(chunk_from_refs.get("error"))
            or safe_dict(chunk_from_meta.get("error"))
        )

        status = (
            direct_status
            or chunk_from_refs.get("status")
            or chunk_from_meta.get("status")
        )

        provisioned_at = (
            direct_provisioned_at
            or chunk_from_refs.get("provisioned_at")
            or chunk_from_meta.get("provisioned_at")
        )

        built = build_chunk_refs(
            chunk_project_id=chunk_project_id,
            chunk_universe_id=chunk_universe_id,
            chunk_world_id=chunk_world_id,
            status=status,
            route_hints=route_hints,
            error=error,
            provisioned_at=provisioned_at,
        )

        if direct_ready is not None:
            explicit_ready = safe_bool(direct_ready, built["ready"])
            if not explicit_ready:
                built["ready"] = False
            elif built["chunk_project_id"] and built["chunk_world_id"] and built["status"] != CHUNK_STATUS_ERROR:
                built["ready"] = True
                built["status"] = CHUNK_STATUS_READY

        return built

    except Exception:
        return build_chunk_refs(status=CHUNK_STATUS_ERROR)


def _chunk_refs_to_service_ref(chunk_refs: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        refs = safe_dict(chunk_refs)

        return {
            "status": refs.get("status") or CHUNK_STATUS_PENDING,
            "ready": safe_bool(refs.get("ready"), False),
            "chunk_project_id": refs.get("chunk_project_id") or refs.get("chunkProjectId"),
            "chunkProjectId": refs.get("chunk_project_id") or refs.get("chunkProjectId"),
            "chunk_universe_id": refs.get("chunk_universe_id") or refs.get("chunkUniverseId"),
            "chunkUniverseId": refs.get("chunk_universe_id") or refs.get("chunkUniverseId"),
            "chunk_world_id": refs.get("chunk_world_id") or refs.get("chunkWorldId"),
            "chunkWorldId": refs.get("chunk_world_id") or refs.get("chunkWorldId"),
            "route_hints": safe_dict(refs.get("route_hints") or refs.get("routeHints")),
            "routeHints": safe_dict(refs.get("route_hints") or refs.get("routeHints")),
            "error": safe_dict(refs.get("error")),
            "provisioned_at": refs.get("provisioned_at") or refs.get("provisionedAt"),
            "provisionedAt": refs.get("provisioned_at") or refs.get("provisionedAt"),
        }

    except Exception:
        return {
            "status": CHUNK_STATUS_ERROR,
            "ready": False,
        }


def _public_project_url(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/project=new"
        return f"/project={value}"
    except Exception:
        return "/project=new"


def _project_workspace_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/project"
    except Exception:
        return "/ui/project/new"


def _project_editor_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/editor"
        return f"/ui/project/{value}/editor"
    except Exception:
        return "/ui/editor"


def _project_map_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/map"
        return f"/ui/project/{value}/map"
    except Exception:
        return "/ui/map"


def _project_cad2d_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return "/ui/project/new"
        return f"/ui/project/{value}/cad2d"
    except Exception:
        return "/ui/project/new"


def _project_plan2d_json_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return ""
        return f"/ui/project/{value}/plan2d.json"
    except Exception:
        return ""


def _project_cad_embed_json_path(public_id: Any) -> str:
    try:
        value = safe_str(public_id, "", 180)
        if not value or value == "new":
            return ""
        return f"/ui/project/{value}/cad-embed.json"
    except Exception:
        return ""


def build_project_paths(public_id: Any) -> Dict[str, str]:
    try:
        value = safe_str(public_id, "new", 180) or "new"

        return {
            "projectPublicUrl": _public_project_url(value),
            "projectPagePath": _project_workspace_path(value),
            "projectUrl": _project_workspace_path(value),
            "editorPagePath": _project_editor_path(value),
            "initialEditorUrl": _project_editor_path(value),
            "mapPagePath": _project_map_path(value),
            "cad2dPagePath": _project_cad2d_path(value),
            "plan2dJsonPath": _project_plan2d_json_path(value),
            "cadEmbedJsonPath": _project_cad_embed_json_path(value),
            "adminPagePath": f"/ui/project/{value}/admin" if value != "new" else "",
            "lvPagePath": f"/ui/project/{value}/lv" if value != "new" else "",
        }

    except Exception:
        return {
            "projectPublicUrl": "/project=new",
            "projectPagePath": "/ui/project/new",
            "projectUrl": "/ui/project/new",
        }


# ─────────────────────────────────────────────────────────────
# Project model
# ─────────────────────────────────────────────────────────────

def _define_project_model(*, extend_existing: bool = False):
    class Project(TimestampMixin, SoftDeleteMixin, SerializationMixin, db.Model):
        """
        App-owned project shell model.

        This model is the app portal's central project reference.
        It stores:
        - owner/user/project metadata
        - address and coordinates
        - visibility and access lifecycle
        - references to microservice resources

        It does not store:
        - chunk contents
        - 3D world truth
        - OpenLayer feature data
        - CAD geometry truth
        - LV contents
        """

        __tablename__ = "projects"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        public_id = db.Column(
            db.String(120),
            unique=True,
            nullable=False,
            index=True,
            default=project_public_id,
        )

        owner_user_id = db.Column(db.Integer, nullable=False, default=1, index=True)
        client_id = db.Column(db.String(80), nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)

        name = db.Column(db.String(255), nullable=False, default="Neues Projekt", index=True)
        description = db.Column(db.Text, nullable=True)

        address_text = db.Column(db.Text, nullable=True)
        street = db.Column(db.String(255), nullable=True)
        house_number = db.Column(db.String(80), nullable=True)
        postal_code = db.Column(db.String(40), nullable=True)
        city = db.Column(db.String(160), nullable=True)
        region = db.Column(db.String(160), nullable=True)
        country = db.Column(db.String(160), nullable=True)

        latitude = db.Column(db.Float, nullable=True, index=True)
        longitude = db.Column(db.Float, nullable=True, index=True)
        coordinate_srid = db.Column(db.String(40), nullable=True, default="EPSG:4326")
        geocode_source = db.Column(db.String(120), nullable=True)
        geocode_quality = db.Column(db.String(120), nullable=True)
        geocode_raw = db.Column(json_type(), nullable=True)

        # References into vectoplan-chunk.
        # These are service references, not foreign keys.
        chunk_project_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_universe_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_world_id = db.Column(db.String(160), nullable=True, index=True)
        chunk_status = db.Column(db.String(40), nullable=False, default=CHUNK_STATUS_PENDING, index=True)
        chunk_ready = db.Column(db.Boolean, nullable=False, default=False, index=True)
        chunk_provisioned_at = db.Column(db.DateTime, nullable=True, index=True)
        chunk_last_error = db.Column(json_type(), nullable=True)
        chunk_route_hints = db.Column(json_type(), nullable=False, default=dict)

        # References into other microservices.
        plan2d_id = db.Column(db.String(160), nullable=True, index=True)
        lv_id = db.Column(db.String(160), nullable=True, index=True)

        service_refs = db.Column(json_type(), nullable=False, default=dict)
        artifact_refs = db.Column(json_type(), nullable=False, default=dict)

        visibility = db.Column(db.String(40), nullable=False, default=PROJECT_VISIBILITY_PRIVATE, index=True)
        is_public = db.Column(db.Boolean, nullable=False, default=False, index=True)

        setup_status = db.Column(db.String(40), nullable=False, default=PROJECT_SETUP_DRAFT, index=True)
        setup_completed_at = db.Column(db.DateTime, nullable=True, index=True)

        status = db.Column(db.String(40), nullable=False, default=PROJECT_STATUS_ACTIVE, index=True)

        archived_at = db.Column(db.DateTime, nullable=True, index=True)
        archived_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        archive_reason = db.Column(db.Text, nullable=True)

        transferred_at = db.Column(db.DateTime, nullable=True)
        transferred_from_user_id = db.Column(db.Integer, nullable=True, index=True)

        last_opened_at = db.Column(db.DateTime, nullable=True, index=True)
        last_activity_at = db.Column(db.DateTime, nullable=True, index=True)

        sort_index = db.Column(db.Integer, nullable=False, default=0, index=True)

        settings = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        __serialize_exclude__ = ()

        def __repr__(self) -> str:
            try:
                return f"<Project id={self.id!r} public_id={self.public_id!r} name={self.name!r}>"
            except Exception:
                return "<Project>"

        @property
        def project_id(self) -> str:
            try:
                return self.public_id or str(self.id or "")
            except Exception:
                return ""

        @property
        def app_project_public_id(self) -> str:
            return self.project_id

        @property
        def display_name(self) -> str:
            try:
                return safe_str(self.name, "Unbenanntes Projekt", 255) or "Unbenanntes Projekt"
            except Exception:
                return "Unbenanntes Projekt"

        @property
        def title(self) -> str:
            return self.display_name

        @property
        def is_archived(self) -> bool:
            try:
                return self.archived_at is not None or self.status == PROJECT_STATUS_ARCHIVED
            except Exception:
                return False

        @property
        def is_active(self) -> bool:
            try:
                return (
                    self.status == PROJECT_STATUS_ACTIVE
                    and self.deleted_at is None
                    and self.archived_at is None
                )
            except Exception:
                return False

        @property
        def is_configured(self) -> bool:
            try:
                return bool(
                    is_configured_status(self.setup_status)
                    or self.setup_completed_at is not None
                )
            except Exception:
                return False

        @property
        def has_coordinates(self) -> bool:
            try:
                return self.latitude is not None and self.longitude is not None
            except Exception:
                return False

        @property
        def coordinates(self) -> Optional[Dict[str, Any]]:
            try:
                if not self.has_coordinates:
                    return None

                return {
                    "lat": self.latitude,
                    "lng": self.longitude,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "srid": self.coordinate_srid or "EPSG:4326",
                }

            except Exception:
                return None

        @property
        def address(self) -> Dict[str, Any]:
            try:
                return {
                    "text": self.address_text,
                    "street": self.street,
                    "house_number": self.house_number,
                    "postal_code": self.postal_code,
                    "city": self.city,
                    "region": self.region,
                    "country": self.country,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "coordinate_srid": self.coordinate_srid,
                }
            except Exception:
                return {}

        @property
        def public_url(self) -> str:
            return _public_project_url(self.public_id)

        @property
        def workspace_url(self) -> str:
            return _project_workspace_path(self.public_id)

        @property
        def has_chunk_project(self) -> bool:
            try:
                return bool(self.chunk_project_id)
            except Exception:
                return False

        @property
        def has_chunk_world(self) -> bool:
            try:
                return bool(self.chunk_project_id and self.chunk_world_id)
            except Exception:
                return False

        @property
        def is_chunk_ready(self) -> bool:
            try:
                return bool(self.chunk_ready and self.has_chunk_world and self.chunk_status == CHUNK_STATUS_READY)
            except Exception:
                return False

        @property
        def chunk_refs(self) -> Dict[str, Any]:
            return _chunk_refs_from_sources(
                direct_project_id=self.chunk_project_id,
                direct_universe_id=self.chunk_universe_id,
                direct_world_id=self.chunk_world_id,
                direct_status=self.chunk_status,
                direct_ready=self.chunk_ready,
                direct_route_hints=self.chunk_route_hints,
                direct_error=self.chunk_last_error,
                direct_provisioned_at=self.chunk_provisioned_at,
                service_refs=self.service_refs,
                metadata_json=self.metadata_json,
            )

        def sync_chunk_refs(self) -> None:
            """
            Synchronize direct chunk columns with service_refs["chunk"] and metadata["chunk"].

            This keeps legacy consumers and new service-link consumers aligned.
            """
            try:
                refs = self.chunk_refs

                self.chunk_project_id = _clean_ref_id(refs.get("chunk_project_id"))
                self.chunk_universe_id = _clean_ref_id(refs.get("chunk_universe_id"))
                self.chunk_world_id = _clean_ref_id(refs.get("chunk_world_id"))
                self.chunk_status = normalize_chunk_status(
                    refs.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                )
                self.chunk_ready = bool(
                    self.chunk_project_id
                    and self.chunk_world_id
                    and self.chunk_status == CHUNK_STATUS_READY
                )
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                service_refs = safe_dict(self.service_refs)
                service_refs["chunk"] = _chunk_refs_to_service_ref(
                    build_chunk_refs(
                        chunk_project_id=self.chunk_project_id,
                        chunk_universe_id=self.chunk_universe_id,
                        chunk_world_id=self.chunk_world_id,
                        status=self.chunk_status,
                        route_hints=self.chunk_route_hints,
                        error=self.chunk_last_error,
                        provisioned_at=self.chunk_provisioned_at,
                    )
                )
                self.service_refs = service_refs

                metadata = safe_dict(self.metadata_json)
                metadata["chunk"] = _chunk_refs_to_service_ref(service_refs["chunk"])
                self.metadata_json = metadata

            except Exception:
                pass

        def set_chunk_refs(
            self,
            *,
            chunk_project_id: Any = None,
            chunk_universe_id: Any = None,
            chunk_world_id: Any = None,
            route_hints: Optional[Mapping[str, Any]] = None,
            status: Any = CHUNK_STATUS_READY,
            error: Optional[Mapping[str, Any]] = None,
            provisioned_at: Any = None,
        ) -> None:
            try:
                refs = build_chunk_refs(
                    chunk_project_id=chunk_project_id,
                    chunk_universe_id=chunk_universe_id,
                    chunk_world_id=chunk_world_id,
                    status=status,
                    route_hints=route_hints,
                    error=error,
                    provisioned_at=provisioned_at or self.chunk_provisioned_at or utcnow(),
                )

                self.chunk_project_id = refs.get("chunk_project_id")
                self.chunk_universe_id = refs.get("chunk_universe_id")
                self.chunk_world_id = refs.get("chunk_world_id")
                self.chunk_status = refs.get("status") or CHUNK_STATUS_PENDING
                self.chunk_ready = bool(refs.get("ready"))
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                if self.chunk_ready and not self.chunk_provisioned_at:
                    self.chunk_provisioned_at = utcnow()

                self.sync_chunk_refs()
                self.touch()

            except Exception:
                pass

        def mark_chunk_pending(self) -> None:
            try:
                self.chunk_status = CHUNK_STATUS_PENDING
                self.chunk_ready = False
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_ready(
            self,
            *,
            chunk_project_id: Any = None,
            chunk_universe_id: Any = None,
            chunk_world_id: Any = None,
            route_hints: Optional[Mapping[str, Any]] = None,
        ) -> None:
            self.set_chunk_refs(
                chunk_project_id=chunk_project_id or self.chunk_project_id,
                chunk_universe_id=chunk_universe_id or self.chunk_universe_id,
                chunk_world_id=chunk_world_id or self.chunk_world_id,
                route_hints=route_hints or self.chunk_route_hints,
                status=CHUNK_STATUS_READY,
                error=None,
                provisioned_at=self.chunk_provisioned_at or utcnow(),
            )

        def mark_chunk_error(self, error: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.chunk_status = CHUNK_STATUS_ERROR
                self.chunk_ready = False
                self.chunk_last_error = safe_dict(error)
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def mark_chunk_disabled(self) -> None:
            try:
                self.chunk_status = CHUNK_STATUS_DISABLED
                self.chunk_ready = False
                self.sync_chunk_refs()
                self.touch()
            except Exception:
                pass

        def normalize_lifecycle(self) -> "Project":
            try:
                if not self.public_id:
                    self.public_id = project_public_id()

                self.name = safe_str(self.name, "Neues Projekt", 255) or "Neues Projekt"
                self.description = safe_str(self.description, "", 10000) or None

                self.owner_user_id = safe_int(self.owner_user_id, 1, minimum=1)
                self.client_id = safe_str(self.client_id, "", 80) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None

                self.address_text = safe_str(self.address_text, "", 2000) or None
                self.street = safe_str(self.street, "", 255) or None
                self.house_number = safe_str(self.house_number, "", 80) or None
                self.postal_code = safe_str(self.postal_code, "", 40) or None
                self.city = safe_str(self.city, "", 160) or None
                self.region = safe_str(self.region, "", 160) or None
                self.country = safe_str(self.country, "", 160) or None

                self.latitude = safe_float(self.latitude, None)
                self.longitude = safe_float(self.longitude, None)
                self.coordinate_srid = safe_str(self.coordinate_srid, "EPSG:4326", 40) or "EPSG:4326"
                self.geocode_source = safe_str(self.geocode_source, "", 120) or None
                self.geocode_quality = safe_str(self.geocode_quality, "", 120) or None
                self.geocode_raw = safe_dict(self.geocode_raw) if self.geocode_raw is not None else None

                self.service_refs = safe_dict(self.service_refs)
                self.artifact_refs = safe_dict(self.artifact_refs)
                self.settings = safe_dict(self.settings)
                self.metadata_json = safe_dict(self.metadata_json)

                refs = _chunk_refs_from_sources(
                    direct_project_id=self.chunk_project_id,
                    direct_universe_id=self.chunk_universe_id,
                    direct_world_id=self.chunk_world_id,
                    direct_status=self.chunk_status,
                    direct_ready=self.chunk_ready,
                    direct_route_hints=self.chunk_route_hints,
                    direct_error=self.chunk_last_error,
                    direct_provisioned_at=self.chunk_provisioned_at,
                    service_refs=self.service_refs,
                    metadata_json=self.metadata_json,
                )

                self.chunk_project_id = _clean_ref_id(refs.get("chunk_project_id"))
                self.chunk_universe_id = _clean_ref_id(refs.get("chunk_universe_id"))
                self.chunk_world_id = _clean_ref_id(refs.get("chunk_world_id"))
                self.chunk_status = normalize_chunk_status(
                    refs.get("status"),
                    has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                )
                self.chunk_ready = bool(
                    self.chunk_project_id
                    and self.chunk_world_id
                    and self.chunk_status == CHUNK_STATUS_READY
                )
                self.chunk_route_hints = safe_dict(refs.get("route_hints"))
                self.chunk_last_error = safe_dict(refs.get("error")) or None

                self.sync_chunk_refs()

                self.plan2d_id = safe_str(self.plan2d_id, "", 160) or None
                self.lv_id = safe_str(self.lv_id, "", 160) or None

                self.visibility = normalize_visibility(self.visibility, PROJECT_VISIBILITY_PRIVATE)
                self.is_public = self.visibility == PROJECT_VISIBILITY_PUBLIC or safe_bool(self.is_public, False)

                if self.is_public:
                    self.visibility = PROJECT_VISIBILITY_PUBLIC

                self.setup_status = normalize_project_setup_status(self.setup_status, PROJECT_SETUP_DRAFT)

                if self.setup_completed_at and self.setup_status == PROJECT_SETUP_DRAFT:
                    self.setup_status = PROJECT_SETUP_CONFIGURED

                self.status = normalize_project_status(self.status, PROJECT_STATUS_ACTIVE)

                if self.deleted_at is not None:
                    self.status = PROJECT_STATUS_DELETED

                if self.archived_at is not None and self.status != PROJECT_STATUS_DELETED:
                    self.status = PROJECT_STATUS_ARCHIVED

                self.archived_by_user_id = safe_int(self.archived_by_user_id, 0) or None
                self.archive_reason = safe_str(self.archive_reason, "", 2000) or None
                self.transferred_from_user_id = safe_int(self.transferred_from_user_id, 0) or None
                self.sort_index = safe_int(self.sort_index, 0)

                return self

            except Exception:
                return self

        def normalize(self) -> "Project":
            return self.normalize_lifecycle()

        def update_metadata(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.metadata_json = data
                else:
                    self.metadata_json = merge_dicts(self.metadata_json, data)

                if "chunk" in data:
                    self.sync_chunk_refs()

                self.touch()

            except Exception:
                pass

        def update_settings(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.settings = data
                else:
                    self.settings = merge_dicts(self.settings, data)

                self.touch()

            except Exception:
                pass

        def update_address(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                if "address_text" in data or "address" in data:
                    address_value = data.get("address_text")
                    if address_value is None and not isinstance(data.get("address"), Mapping):
                        address_value = data.get("address")
                    self.address_text = safe_str(address_value, "", 2000) or None

                address_obj = safe_dict(data.get("address")) if isinstance(data.get("address"), Mapping) else data

                for field in ("street", "house_number", "postal_code", "city", "region", "country"):
                    if field in address_obj:
                        setattr(self, field, safe_str(address_obj.get(field), "", 255) or None)

                self.touch()

            except Exception:
                pass

        def update_coordinates(
            self,
            *,
            latitude: Any = None,
            longitude: Any = None,
            srid: Any = None,
            geocode_source: Any = None,
            geocode_quality: Any = None,
            geocode_raw: Any = None,
        ) -> None:
            try:
                lat = safe_float(latitude, None)
                lon = safe_float(longitude, None)

                if lat is not None:
                    self.latitude = lat

                if lon is not None:
                    self.longitude = lon

                if srid is not None:
                    self.coordinate_srid = safe_str(srid, "EPSG:4326", 40) or "EPSG:4326"

                if geocode_source is not None:
                    self.geocode_source = safe_str(geocode_source, "", 120) or None

                if geocode_quality is not None:
                    self.geocode_quality = safe_str(geocode_quality, "", 120) or None

                if geocode_raw is not None:
                    self.geocode_raw = safe_dict(geocode_raw)

                self.touch()

            except Exception:
                pass

        def update_service_refs(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.service_refs = data
                else:
                    self.service_refs = merge_dicts(self.service_refs, data)

                direct_or_nested = _chunk_payload_from_mapping(data)

                if data.get("chunk_project_id") or data.get("chunkProjectId") or direct_or_nested.get("chunk_project_id"):
                    self.chunk_project_id = (
                        _clean_ref_id(data.get("chunk_project_id"))
                        or _clean_ref_id(data.get("chunkProjectId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_project_id"))
                    )

                if data.get("chunk_universe_id") or data.get("chunkUniverseId") or direct_or_nested.get("chunk_universe_id"):
                    self.chunk_universe_id = (
                        _clean_ref_id(data.get("chunk_universe_id"))
                        or _clean_ref_id(data.get("chunkUniverseId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_universe_id"))
                    )

                if data.get("chunk_world_id") or data.get("chunkWorldId") or direct_or_nested.get("chunk_world_id"):
                    self.chunk_world_id = (
                        _clean_ref_id(data.get("chunk_world_id"))
                        or _clean_ref_id(data.get("chunkWorldId"))
                        or _clean_ref_id(direct_or_nested.get("chunk_world_id"))
                    )

                if direct_or_nested.get("route_hints"):
                    self.chunk_route_hints = safe_dict(direct_or_nested.get("route_hints"))

                if direct_or_nested.get("error"):
                    self.chunk_last_error = safe_dict(direct_or_nested.get("error"))

                if direct_or_nested.get("status"):
                    self.chunk_status = normalize_chunk_status(
                        direct_or_nested.get("status"),
                        has_refs=bool(self.chunk_project_id and self.chunk_world_id),
                    )

                if data.get("plan2d_id"):
                    self.plan2d_id = safe_str(data.get("plan2d_id"), "", 160) or None

                if data.get("lv_id"):
                    self.lv_id = safe_str(data.get("lv_id"), "", 160) or None

                self.sync_chunk_refs()
                self.touch()

            except Exception:
                pass

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                for field in (
                    "name",
                    "description",
                    "address_text",
                    "street",
                    "house_number",
                    "postal_code",
                    "city",
                    "region",
                    "country",
                    "client_id",
                    "conversation_id",
                    "chunk_project_id",
                    "chunk_universe_id",
                    "chunk_world_id",
                    "chunk_status",
                    "plan2d_id",
                    "lv_id",
                    "setup_status",
                    "status",
                    "visibility",
                ):
                    if field in data:
                        setattr(self, field, data.get(field))

                if "chunkProjectId" in data:
                    self.chunk_project_id = _clean_ref_id(data.get("chunkProjectId"))

                if "chunkUniverseId" in data:
                    self.chunk_universe_id = _clean_ref_id(data.get("chunkUniverseId"))

                if "chunkWorldId" in data:
                    self.chunk_world_id = _clean_ref_id(data.get("chunkWorldId"))

                if "chunkStatus" in data:
                    self.chunk_status = normalize_chunk_status(data.get("chunkStatus"))

                if "chunkReady" in data or "chunk_ready" in data:
                    self.chunk_ready = safe_bool(data.get("chunkReady", data.get("chunk_ready")), False)

                if "chunkRouteHints" in data or "chunk_route_hints" in data:
                    self.chunk_route_hints = safe_dict(data.get("chunkRouteHints") or data.get("chunk_route_hints"))

                if "chunkError" in data or "chunk_last_error" in data:
                    self.chunk_last_error = safe_dict(data.get("chunkError") or data.get("chunk_last_error"))

                if "address" in data:
                    self.update_address(data)

                if "latitude" in data or "longitude" in data or "lat" in data or "lng" in data:
                    self.update_coordinates(
                        latitude=data.get("latitude", data.get("lat")),
                        longitude=data.get("longitude", data.get("lng")),
                        srid=data.get("coordinate_srid", data.get("srid")),
                    )

                if "is_public" in data or "public" in data:
                    self.is_public = safe_bool(data.get("is_public", data.get("public")), False)
                    self.visibility = PROJECT_VISIBILITY_PUBLIC if self.is_public else PROJECT_VISIBILITY_PRIVATE

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                if "service_refs" in data or "serviceRefs" in data:
                    self.update_service_refs(data.get("service_refs") or data.get("serviceRefs"))

                self.normalize_lifecycle()
                self.touch()

            except Exception:
                pass

        def mark_defined(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_DEFINED
                self.touch()
            except Exception:
                pass

        def mark_configured(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_CONFIGURED
                self.setup_completed_at = self.setup_completed_at or utcnow()
                self.touch()
            except Exception:
                pass

        def mark_draft(self) -> None:
            try:
                self.setup_status = PROJECT_SETUP_DRAFT
                self.setup_completed_at = None
                self.touch()
            except Exception:
                pass

        def mark_opened(self) -> None:
            try:
                self.last_opened_at = utcnow()
                self.last_activity_at = self.last_opened_at
                self.touch()
            except Exception:
                pass

        def archive(self, *, user_id: Optional[int] = None, reason: str = "") -> None:
            try:
                self.status = PROJECT_STATUS_ARCHIVED
                self.archived_at = utcnow()
                self.archived_by_user_id = safe_int(user_id, 0) or None
                self.archive_reason = safe_str(reason, "", 2000) or None
                self.touch()
            except Exception:
                pass

        def restore_archive(self) -> None:
            try:
                self.status = PROJECT_STATUS_ACTIVE
                self.archived_at = None
                self.archived_by_user_id = None
                self.archive_reason = None
                self.touch()
            except Exception:
                pass

        def transfer_ownership(self, new_owner_user_id: Any) -> None:
            try:
                old_owner = safe_int(self.owner_user_id, 0) or None
                new_owner = safe_int(new_owner_user_id, 0, minimum=1)

                if not new_owner:
                    return

                self.transferred_from_user_id = old_owner
                self.owner_user_id = new_owner
                self.transferred_at = utcnow()
                self.touch()

            except Exception:
                pass

        def set_visibility(self, visibility: Any = None, *, is_public: Optional[bool] = None) -> None:
            try:
                if is_public is not None:
                    self.is_public = bool(is_public)
                    self.visibility = PROJECT_VISIBILITY_PUBLIC if self.is_public else PROJECT_VISIBILITY_PRIVATE
                else:
                    self.visibility = normalize_visibility(visibility, PROJECT_VISIBILITY_PRIVATE)
                    self.is_public = self.visibility == PROJECT_VISIBILITY_PUBLIC

                self.touch()

            except Exception:
                pass

        def build_paths(self) -> Dict[str, str]:
            return build_project_paths(self.public_id)

        def to_dict(
            self,
            *,
            include_private: bool = False,
            include_paths: bool = True,
            include_refs: bool = True,
            include_address: bool = True,
            include_permissions: bool = False,
            include_service_links: bool = False,
            include_versions: bool = False,
            include_embed_policy: bool = False,
            access: Optional[Mapping[str, Any]] = None,
            **_: Any,
        ) -> Dict[str, Any]:
            try:
                self.sync_chunk_refs()

                public_id = self.public_id or str(self.id or "")
                chunk_refs = self.chunk_refs

                payload: Dict[str, Any] = {
                    "id": self.id,
                    "project_id": self.id,
                    "public_id": public_id,
                    "projectPublicId": public_id,
                    "appProjectPublicId": public_id,
                    "name": self.name,
                    "display_name": self.display_name,
                    "displayName": self.display_name,
                    "description": self.description or "",
                    "owner_user_id": self.owner_user_id,
                    "client_id": self.client_id,
                    "conversation_id": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "visibility": self.visibility,
                    "is_public": bool(self.is_public),
                    "setup_status": self.setup_status,
                    "setupStatus": self.setup_status,
                    "setup_completed_at": isoformat(self.setup_completed_at),
                    "is_configured": self.is_configured,
                    "isConfigured": self.is_configured,
                    "status": self.status,
                    "is_active": self.is_active,
                    "is_archived": self.is_archived,
                    "is_deleted": self.is_deleted,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                    "archived_at": isoformat(self.archived_at),
                    "deleted_at": isoformat(self.deleted_at),
                    "last_opened_at": isoformat(self.last_opened_at),
                    "last_activity_at": isoformat(self.last_activity_at),
                    "url": _public_project_url(public_id),
                    "href": _public_project_url(public_id),
                    "chunk": chunk_refs,
                    "chunk_ready": chunk_refs.get("ready"),
                    "chunkReady": chunk_refs.get("ready"),
                    "chunk_status": chunk_refs.get("status"),
                    "chunkStatus": chunk_refs.get("status"),
                    "chunk_project_id": chunk_refs.get("chunk_project_id"),
                    "chunkProjectId": chunk_refs.get("chunk_project_id"),
                    "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                    "chunkUniverseId": chunk_refs.get("chunk_universe_id"),
                    "chunk_world_id": chunk_refs.get("chunk_world_id"),
                    "chunkWorldId": chunk_refs.get("chunk_world_id"),
                    "chunk_route_hints": chunk_refs.get("route_hints"),
                    "chunkRouteHints": chunk_refs.get("route_hints"),
                }

                if include_address:
                    payload.update(
                        {
                            "address_text": self.address_text or "",
                            "address": self.address,
                            "street": self.street,
                            "house_number": self.house_number,
                            "postal_code": self.postal_code,
                            "city": self.city,
                            "region": self.region,
                            "country": self.country,
                            "latitude": self.latitude,
                            "longitude": self.longitude,
                            "coordinate_srid": self.coordinate_srid,
                            "coordinates": self.coordinates,
                            "geocode_source": self.geocode_source,
                            "geocode_quality": self.geocode_quality,
                        }
                    )

                if include_refs:
                    payload.update(
                        {
                            "plan2d_id": self.plan2d_id,
                            "plan2dId": self.plan2d_id,
                            "lv_id": self.lv_id,
                            "lvId": self.lv_id,
                            "service_refs": safe_dict(self.service_refs),
                            "serviceRefs": safe_dict(self.service_refs),
                            "artifact_refs": safe_dict(self.artifact_refs),
                            "artifactRefs": safe_dict(self.artifact_refs),
                        }
                    )

                if include_paths:
                    payload["paths"] = self.build_paths()
                    payload.update(
                        {
                            "projectPublicUrl": payload["paths"].get("projectPublicUrl"),
                            "projectPagePath": payload["paths"].get("projectPagePath"),
                            "projectUrl": payload["paths"].get("projectUrl"),
                        }
                    )

                if include_permissions and access is not None:
                    payload["access"] = safe_dict(access)

                if include_service_links:
                    payload.setdefault("service_links", [])

                if include_versions:
                    payload.setdefault("versions", [])

                if include_embed_policy:
                    payload.setdefault("embed_policy", None)

                if include_private:
                    payload.update(
                        {
                            "settings": safe_dict(self.settings),
                            "metadata": safe_dict(self.metadata_json),
                            "geocode_raw": safe_dict(self.geocode_raw),
                            "sort_index": self.sort_index,
                            "archived_by_user_id": self.archived_by_user_id,
                            "archive_reason": self.archive_reason,
                            "deleted_by_user_id": self.deleted_by_user_id,
                            "delete_reason": self.delete_reason,
                            "transferred_at": isoformat(self.transferred_at),
                            "transferred_from_user_id": self.transferred_from_user_id,
                            "chunk_last_error": safe_dict(self.chunk_last_error),
                            "chunkLastError": safe_dict(self.chunk_last_error),
                            "chunk_provisioned_at": isoformat(self.chunk_provisioned_at),
                            "chunkProvisionedAt": isoformat(self.chunk_provisioned_at),
                        }
                    )

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "public_id": getattr(self, "public_id", None),
                    "name": getattr(self, "name", "Projekt"),
                    "is_configured": False,
                    "chunk_ready": False,
                    "chunkReady": False,
                }

        def to_sidebar_item(
            self,
            *,
            current_project_id: Optional[str] = None,
            current_chat_id: Optional[str] = None,
            include_meta: bool = False,
            **_: Any,
        ) -> Dict[str, Any]:
            try:
                self.sync_chunk_refs()

                public_id = self.public_id or str(self.id or "")
                subtitle = (
                    self.address_text
                    or self.city
                    or ("Projekt aktiv" if self.is_configured else "Projekt definieren")
                )

                is_active = False

                if current_project_id and str(current_project_id) == str(public_id):
                    is_active = True

                if current_chat_id and self.conversation_id and str(current_chat_id) == str(self.conversation_id):
                    is_active = True

                chunk_refs = self.chunk_refs

                item: Dict[str, Any] = {
                    "id": public_id,
                    "projectId": public_id,
                    "project_id": public_id,
                    "public_id": public_id,
                    "title": self.display_name,
                    "name": self.name,
                    "subtitle": subtitle,
                    "description": self.description or "",
                    "href": _public_project_url(public_id),
                    "url": _public_project_url(public_id),
                    "chatId": self.conversation_id or "",
                    "chat_id": self.conversation_id or "",
                    "conversationId": self.conversation_id or "",
                    "conversation_id": self.conversation_id or "",
                    "isActive": is_active,
                    "is_active": is_active,
                    "isConfigured": self.is_configured,
                    "is_configured": self.is_configured,
                    "setupStatus": self.setup_status,
                    "setup_status": self.setup_status,
                    "visibility": self.visibility,
                    "is_public": bool(self.is_public),
                    "chunkReady": chunk_refs.get("ready"),
                    "chunk_ready": chunk_refs.get("ready"),
                    "chunkStatus": chunk_refs.get("status"),
                    "chunk_status": chunk_refs.get("status"),
                    "chunkProjectId": chunk_refs.get("chunk_project_id"),
                    "chunk_project_id": chunk_refs.get("chunk_project_id"),
                    "chunkUniverseId": chunk_refs.get("chunk_universe_id"),
                    "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                    "chunkWorldId": chunk_refs.get("chunk_world_id"),
                    "chunk_world_id": chunk_refs.get("chunk_world_id"),
                    "source": "api",
                    "initial": (self.display_name[:1] or "P").upper(),
                    "updatedAt": isoformat(self.updated_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_meta:
                    item["meta"] = {
                        "owner_user_id": self.owner_user_id,
                        "chunk": chunk_refs,
                        "chunk_project_id": chunk_refs.get("chunk_project_id"),
                        "chunk_universe_id": chunk_refs.get("chunk_universe_id"),
                        "chunk_world_id": chunk_refs.get("chunk_world_id"),
                        "plan2d_id": self.plan2d_id,
                        "lv_id": self.lv_id,
                        "status": self.status,
                    }

                return item

            except Exception:
                return {
                    "id": getattr(self, "public_id", None) or getattr(self, "id", None),
                    "title": getattr(self, "name", "Projekt"),
                    "href": "/",
                    "source": "fallback",
                    "chunkReady": False,
                    "chunk_ready": False,
                }

    return Project


Project = _resolve_model("Project", "projects", _define_project_model)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def get_project_by_id(project_id: Any) -> Optional[Project]:
    try:
        resolved_id = safe_int(project_id, 0)
        if not resolved_id:
            return None

        return Project.query.get(resolved_id)

    except Exception:
        return None


def get_project_by_public_id(public_id_value: Any) -> Optional[Project]:
    try:
        value = safe_str(public_id_value, "", 180)
        if not value:
            return None

        return Project.query.filter_by(public_id=value).one_or_none()

    except Exception:
        return None


def get_project_by_conversation_id(conversation_id: Any) -> Optional[Project]:
    try:
        value = safe_str(conversation_id, "", 120)
        if not value:
            return None

        return Project.query.filter_by(conversation_id=value).one_or_none()

    except Exception:
        return None


def resolve_project(project_ref: Any) -> Optional[Project]:
    try:
        value = safe_str(project_ref, "", 180)
        if not value:
            return None

        project = get_project_by_public_id(value)
        if project is not None:
            return project

        numeric_id = safe_int(value, 0)
        if numeric_id:
            project = get_project_by_id(numeric_id)
            if project is not None:
                return project

        project = get_project_by_conversation_id(value)
        if project is not None:
            return project

        return None

    except Exception:
        return None


def serialize_project(project: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        if hasattr(project, "to_dict"):
            return project.to_dict(**kwargs)

        return {
            "id": getattr(project, "id", None),
            "public_id": getattr(project, "public_id", None),
            "name": getattr(project, "name", "Projekt"),
            "description": getattr(project, "description", ""),
            "conversation_id": getattr(project, "conversation_id", None),
            "is_configured": bool(getattr(project, "is_configured", False)),
            "chunk_ready": bool(getattr(project, "chunk_ready", False)),
            "chunk_project_id": getattr(project, "chunk_project_id", None),
            "chunk_universe_id": getattr(project, "chunk_universe_id", None),
            "chunk_world_id": getattr(project, "chunk_world_id", None),
        }

    except Exception:
        return {}


def serialize_project_sidebar_item(project: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        if hasattr(project, "to_sidebar_item"):
            return project.to_sidebar_item(**kwargs)

        payload = serialize_project(project)
        public_id = payload.get("public_id") or payload.get("id") or ""

        return {
            "id": public_id,
            "projectId": public_id,
            "public_id": public_id,
            "title": payload.get("name") or "Projekt",
            "subtitle": payload.get("address_text") or payload.get("setup_status") or "Projekt",
            "href": _public_project_url(public_id),
            "chunkReady": bool(payload.get("chunk_ready")),
            "chunkProjectId": payload.get("chunk_project_id"),
            "chunkWorldId": payload.get("chunk_world_id"),
            "source": "fallback",
        }

    except Exception:
        return {}


def build_project(
    *,
    owner_user_id: int = 1,
    name: str = "",
    description: str = "",
    address_text: str = "",
    visibility: str = PROJECT_VISIBILITY_PRIVATE,
    conversation_id: str = "",
    client_id: str = "",
    **extra: Any,
) -> Project:
    project = Project()

    try:
        project.owner_user_id = safe_int(owner_user_id, 1, minimum=1)
        project.name = safe_str(name, "Neues Projekt", 255) or "Neues Projekt"
        project.description = safe_str(description, "", 10000) or None
        project.address_text = safe_str(address_text, "", 2000) or None
        project.visibility = normalize_visibility(visibility, PROJECT_VISIBILITY_PRIVATE)
        project.is_public = project.visibility == PROJECT_VISIBILITY_PUBLIC
        project.conversation_id = safe_str(conversation_id, "", 80) or None
        project.client_id = safe_str(client_id, "", 80) or None

        if extra:
            project.update_from_payload(extra)

        project.normalize_lifecycle()

        return project

    except Exception:
        return project


def get_project_model_classes() -> List[Any]:
    return [Project]


def get_project_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(Project.query.count())
        except Exception:
            count = -1

        table = getattr(Project, "__table__", None)
        columns = []

        try:
            columns = [str(column.name) for column in table.columns]
        except Exception:
            columns = []

        required_chunk_columns = [
            "chunk_project_id",
            "chunk_universe_id",
            "chunk_world_id",
            "chunk_status",
            "chunk_ready",
            "chunk_route_hints",
            "chunk_last_error",
            "chunk_provisioned_at",
        ]

        return {
            "ok": True,
            "models": ["Project"],
            "tables": [getattr(Project, "__tablename__", "projects")],
            "count": count,
            "columns": columns,
            "chunkIntegrationReady": all(column in columns for column in required_chunk_columns),
            "missingChunkColumns": [
                column
                for column in required_chunk_columns
                if column not in columns
            ],
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["Project"],
            "tables": ["projects"],
            "error": str(exc),
        }


__all__ = [
    "PROJECT_SETUP_DRAFT",
    "PROJECT_SETUP_DEFINED",
    "PROJECT_SETUP_CONFIGURED",
    "PROJECT_STATUS_ACTIVE",
    "PROJECT_STATUS_ARCHIVED",
    "PROJECT_STATUS_DELETED",
    "PROJECT_VISIBILITY_PRIVATE",
    "PROJECT_VISIBILITY_SHARED",
    "PROJECT_VISIBILITY_PUBLIC",
    "CHUNK_STATUS_DISABLED",
    "CHUNK_STATUS_PENDING",
    "CHUNK_STATUS_READY",
    "CHUNK_STATUS_ERROR",
    "VALID_CHUNK_STATUSES",
    "Project",
    "normalize_project_setup_status",
    "normalize_project_status",
    "normalize_chunk_status",
    "is_configured_status",
    "build_chunk_refs",
    "build_project_paths",
    "get_project_by_id",
    "get_project_by_public_id",
    "get_project_by_conversation_id",
    "resolve_project",
    "serialize_project",
    "serialize_project_sidebar_item",
    "build_project",
    "get_project_model_classes",
    "get_project_model_status",
]