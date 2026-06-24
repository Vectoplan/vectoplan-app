# services/vectoplan-app/models/legacy.py
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    deep_copy_json,
    isoformat,
    json_type,
    make_uuid,
    normalize_status,
    safe_bool,
    safe_dict,
    safe_int,
    safe_list,
    safe_str,
    utcnow,
)


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

    Current state:
    - old models/core.py may still define model classes.
    - new split modules are being introduced file by file.

    If a table is already registered, prefer the existing core model to avoid
    duplicate SQLAlchemy table/class registration. Compatibility methods are
    installed after model resolution so returned core models get the same API.
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
# JSON/state helpers
# ─────────────────────────────────────────────────────────────

_MAX_MERGE_DEPTH = 16


def _json_mapping(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, MappingABC):
            return dict(value)
        return safe_dict(value)
    except Exception:
        return {}


def _json_list(value: Any) -> List[Any]:
    try:
        return safe_list(value)
    except Exception:
        return []


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """
    Convert arbitrary values to JSON-safe values for legacy JSON columns.

    This avoids storing unserializable Python objects inside ConversationState.
    """
    if depth > _MAX_MERGE_DEPTH:
        return None

    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, datetime):
            return isoformat(value)

        if isinstance(value, MappingABC):
            result: Dict[str, Any] = {}
            for key, item in value.items():
                key_text = safe_str(key, "", 240)
                if not key_text:
                    continue
                result[key_text] = _json_safe(item, depth=depth + 1)
            return result

        if isinstance(value, (list, tuple, set, frozenset)):
            return [_json_safe(item, depth=depth + 1) for item in list(value)]

        return safe_str(value, "", 20_000)

    except Exception:
        return None


def _deep_merge_state(
    base: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
    *,
    depth: int = 0,
) -> Dict[str, Any]:
    """
    Defensive recursive merge for ConversationState.

    Used by ConversationState.merge_patch() and by legacy state endpoints.
    """
    if depth > _MAX_MERGE_DEPTH:
        return _json_mapping(base)

    try:
        merged = _json_mapping(base)

        for key, value in _json_mapping(patch).items():
            key_text = safe_str(key, "", 240)
            if not key_text:
                continue

            existing = merged.get(key_text)

            if isinstance(existing, MappingABC) and isinstance(value, MappingABC):
                merged[key_text] = _deep_merge_state(
                    existing,
                    value,
                    depth=depth + 1,
                )
            else:
                merged[key_text] = _json_safe(value, depth=depth + 1)

        return merged

    except Exception:
        return _json_mapping(patch)


def _state_payload_from_row(row: Any) -> Dict[str, Any]:
    """Return canonical state payload from ConversationState row."""
    try:
        return _json_mapping(getattr(row, "state", None))
    except Exception:
        return {}


def _set_row_state(row: Any, value: Mapping[str, Any] | None) -> None:
    """Set canonical state payload on ConversationState row."""
    try:
        setattr(row, "state", _json_mapping(value))
    except Exception:
        pass


def _sync_selection_from_state(row: Any, state: Mapping[str, Any]) -> None:
    """
    Keep optional `selection` column aligned when selection-like state exists.
    """
    try:
        for key in ("workspace_selection", "viewer_selection", "selection"):
            candidate = state.get(key)
            if isinstance(candidate, MappingABC):
                setattr(row, "selection", _json_mapping(candidate))
                return
    except Exception:
        pass


def _touch_if_available(row: Any) -> None:
    try:
        touch = getattr(row, "touch", None)
        if callable(touch):
            touch()
        elif hasattr(row, "updated_at"):
            row.updated_at = utcnow()
    except Exception:
        pass


def _conversation_state_query(model_class: Any, conversation_id: Any, key: str = "default") -> Any | None:
    """Query ConversationState by conversation_id/key."""
    conv_id = safe_str(conversation_id, "", 80)
    state_key = safe_str(key, "default", 160) or "default"

    if not conv_id:
        raise ValueError("conversation_id is required.")

    try:
        return model_class.query.filter_by(
            conversation_id=conv_id,
            key=state_key,
        ).one_or_none()
    except Exception as exc:
        raise RuntimeError(f"ConversationState lookup failed: {exc}") from exc


def _conversation_state_get_or_create(
    model_class: Any,
    conversation_id: Any,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    """
    Canonical get-or-create implementation used by both classmethod and helper.

    It raises on database errors so route code can return a proper 500 instead
    of silently pretending that state was persisted.
    """
    conv_id = safe_str(conversation_id, "", 80)
    state_key = safe_str(key, "default", 160) or "default"

    if not conv_id:
        raise ValueError("conversation_id is required.")

    existing = _conversation_state_query(model_class, conv_id, state_key)
    if existing is not None:
        return existing

    row = model_class()
    row.conversation_id = conv_id
    row.key = state_key
    row.state = {}
    row.selection = None
    row.status = "active"
    row.metadata_json = {}

    normalize = getattr(row, "normalize", None)
    if callable(normalize):
        normalize()

    db.session.add(row)

    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        db.session.rollback()
        raise

    return row


def _conversation_state_merge_patch(
    model_class: Any,
    conversation_id: Any,
    patch: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    """
    Canonical ConversationState.merge_patch implementation.

    Merges into the `state` column and supports `state_json` compatibility.
    """
    conv_id = safe_str(conversation_id, "", 80)
    state_key = safe_str(key, "default", 160) or "default"

    if not conv_id:
        raise ValueError("conversation_id is required.")

    patch_dict = _json_mapping(patch)

    row = _conversation_state_get_or_create(
        model_class,
        conv_id,
        state_key,
        commit=False,
    )

    current_state = _state_payload_from_row(row)
    merged_state = _deep_merge_state(current_state, patch_dict)

    _set_row_state(row, merged_state)
    _sync_selection_from_state(row, merged_state)
    _touch_if_available(row)

    db.session.add(row)

    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        db.session.rollback()
        raise

    return row


def _conversation_state_replace_state(
    model_class: Any,
    conversation_id: Any,
    state: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    """Replace ConversationState.state completely."""
    conv_id = safe_str(conversation_id, "", 80)
    state_key = safe_str(key, "default", 160) or "default"

    row = _conversation_state_get_or_create(
        model_class,
        conv_id,
        state_key,
        commit=False,
    )

    state_dict = _json_mapping(state)

    _set_row_state(row, state_dict)
    _sync_selection_from_state(row, state_dict)
    _touch_if_available(row)

    db.session.add(row)

    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except Exception:
        db.session.rollback()
        raise

    return row


def _conversation_state_state_json_getter(self: Any) -> Dict[str, Any]:
    """Compatibility alias: state_json -> state."""
    return _state_payload_from_row(self)


def _conversation_state_state_json_setter(self: Any, value: Mapping[str, Any] | None) -> None:
    """Compatibility alias: state_json -> state."""
    _set_row_state(self, value)


def _conversation_state_cls_get_or_create(
    cls: Any,
    conversation_id: Any,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    return _conversation_state_get_or_create(
        cls,
        conversation_id,
        key,
        commit=commit,
    )


def _conversation_state_cls_merge_patch(
    cls: Any,
    conversation_id: Any,
    patch: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    return _conversation_state_merge_patch(
        cls,
        conversation_id,
        patch,
        key,
        commit=commit,
    )


def _conversation_state_cls_replace_state(
    cls: Any,
    conversation_id: Any,
    state: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> Any:
    return _conversation_state_replace_state(
        cls,
        conversation_id,
        state,
        key,
        commit=commit,
    )


def _install_conversation_state_compatibility(model_class: Any) -> Any:
    """
    Install compatibility API on ConversationState.

    This is deliberately applied after `_resolve_model(...)` so it also patches
    a model class returned from old `models.core`.
    """
    if model_class is None:
        return model_class

    try:
        setattr(
            model_class,
            "state_json",
            property(
                _conversation_state_state_json_getter,
                _conversation_state_state_json_setter,
            ),
        )
    except Exception:
        pass

    try:
        setattr(
            model_class,
            "get_or_create",
            classmethod(_conversation_state_cls_get_or_create),
        )
    except Exception:
        pass

    try:
        setattr(
            model_class,
            "merge_patch",
            classmethod(_conversation_state_cls_merge_patch),
        )
    except Exception:
        pass

    try:
        setattr(
            model_class,
            "replace_state",
            classmethod(_conversation_state_cls_replace_state),
        )
    except Exception:
        pass

    return model_class


# ─────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────

def _define_client_model(*, extend_existing: bool = False):
    class Client(TimestampMixin, SerializationMixin, db.Model):
        """
        Optional customer/client record.

        The app can attach projects or conversations to a client, but the new
        project shell does not require this table to be populated.
        """

        __tablename__ = "clients"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.String(80), primary_key=True, default=make_uuid)

        name = db.Column(db.String(255), nullable=False, default="Kunde")
        company = db.Column(db.String(255), nullable=True)
        email = db.Column(db.String(255), nullable=True, index=True)
        phone = db.Column(db.String(80), nullable=True)

        address_text = db.Column(db.Text, nullable=True)
        street = db.Column(db.String(255), nullable=True)
        house_number = db.Column(db.String(80), nullable=True)
        postal_code = db.Column(db.String(40), nullable=True)
        city = db.Column(db.String(160), nullable=True)
        region = db.Column(db.String(160), nullable=True)
        country = db.Column(db.String(160), nullable=True)

        status = db.Column(db.String(40), nullable=False, default="active", index=True)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<Client id={self.id!r} name={self.name!r}>"
            except Exception:
                return "<Client>"

        def normalize(self) -> "Client":
            try:
                if not self.id:
                    self.id = make_uuid()

                self.name = safe_str(self.name, "Kunde", 255) or "Kunde"
                self.company = safe_str(self.company, "", 255) or None
                self.email = safe_str(self.email, "", 255) or None
                self.phone = safe_str(self.phone, "", 80) or None

                self.address_text = safe_str(self.address_text, "", 2000) or None
                self.street = safe_str(self.street, "", 255) or None
                self.house_number = safe_str(self.house_number, "", 80) or None
                self.postal_code = safe_str(self.postal_code, "", 40) or None
                self.city = safe_str(self.city, "", 160) or None
                self.region = safe_str(self.region, "", 160) or None
                self.country = safe_str(self.country, "", 160) or None

                self.status = normalize_status(self.status, "active")
                self.metadata_json = safe_dict(self.metadata_json)

                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> None:
            try:
                data = safe_dict(payload)

                for field in (
                    "name",
                    "company",
                    "email",
                    "phone",
                    "address_text",
                    "street",
                    "house_number",
                    "postal_code",
                    "city",
                    "region",
                    "country",
                    "status",
                ):
                    if field in data:
                        setattr(self, field, data.get(field))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                self.normalize()
                self.touch()

            except Exception:
                pass

        def to_dict(self, *, include_private: bool = True) -> Dict[str, Any]:
            try:
                return {
                    "id": self.id,
                    "name": self.name,
                    "company": self.company,
                    "email": self.email,
                    "phone": self.phone,
                    "address_text": self.address_text,
                    "street": self.street,
                    "house_number": self.house_number,
                    "postal_code": self.postal_code,
                    "city": self.city,
                    "region": self.region,
                    "country": self.country,
                    "status": self.status,
                    "metadata": safe_dict(self.metadata_json) if include_private else {},
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }
            except Exception:
                return {"id": getattr(self, "id", None), "name": getattr(self, "name", None)}

    return Client


# ─────────────────────────────────────────────────────────────
# IdempotencyKey
# ─────────────────────────────────────────────────────────────

def _define_idempotency_key_model(*, extend_existing: bool = False):
    class IdempotencyKey(TimestampMixin, SerializationMixin, db.Model):
        """
        Generic idempotency store for API write operations.
        """

        __tablename__ = "idempotency_keys"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        key = db.Column(db.String(255), unique=True, nullable=False, index=True)
        scope = db.Column(db.String(120), nullable=True, index=True)
        method = db.Column(db.String(16), nullable=True)
        path = db.Column(db.Text, nullable=True)

        request_hash = db.Column(db.String(128), nullable=True, index=True)
        request_json = db.Column(json_type(), nullable=True)

        status = db.Column(db.String(40), nullable=False, default="pending", index=True)
        status_code = db.Column(db.Integer, nullable=True)
        response_json = db.Column(json_type(), nullable=True)

        locked_at = db.Column(db.DateTime, nullable=True)
        expires_at = db.Column(db.DateTime, nullable=True, index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<IdempotencyKey key={self.key!r} status={self.status!r}>"
            except Exception:
                return "<IdempotencyKey>"

        @property
        def is_complete(self) -> bool:
            try:
                return str(self.status or "").lower() in {"done", "complete", "completed", "success"}
            except Exception:
                return False

        @property
        def is_expired(self) -> bool:
            try:
                return bool(self.expires_at and self.expires_at <= utcnow())
            except Exception:
                return False

        def normalize(self) -> "IdempotencyKey":
            try:
                self.key = safe_str(self.key, "", 255)
                self.scope = safe_str(self.scope, "", 120) or None
                self.method = safe_str(self.method, "", 16).upper() or None
                self.path = safe_str(self.path, "", 2000) or None
                self.request_hash = safe_str(self.request_hash, "", 128) or None
                self.status = normalize_status(self.status, "pending")
                self.status_code = safe_int(self.status_code, 0) or None
                self.request_json = safe_dict(self.request_json) if self.request_json is not None else None
                self.response_json = safe_dict(self.response_json) if self.response_json is not None else None
                self.metadata_json = safe_dict(self.metadata_json)
                return self
            except Exception:
                return self

        def mark_locked(self) -> None:
            try:
                self.status = "locked"
                self.locked_at = utcnow()
                self.touch()
            except Exception:
                pass

        def mark_response(self, *, status_code: int, response_json: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.status = "complete"
                self.status_code = safe_int(status_code, 200, minimum=100, maximum=599)
                self.response_json = safe_dict(response_json)
                self.touch()
            except Exception:
                pass

        def to_dict(self, *, include_payload: bool = False) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "key": self.key,
                    "scope": self.scope,
                    "method": self.method,
                    "path": self.path,
                    "request_hash": self.request_hash,
                    "status": self.status,
                    "status_code": self.status_code,
                    "locked_at": isoformat(self.locked_at),
                    "expires_at": isoformat(self.expires_at),
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_payload:
                    payload["request_json"] = safe_dict(self.request_json)
                    payload["response_json"] = safe_dict(self.response_json)
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {"id": getattr(self, "id", None), "key": getattr(self, "key", None)}

    return IdempotencyKey


# ─────────────────────────────────────────────────────────────
# Job
# ─────────────────────────────────────────────────────────────

def _define_job_model(*, extend_existing: bool = False):
    class Job(TimestampMixin, SerializationMixin, db.Model):
        """
        Generic background job/status record.

        This app shell should not perform heavy work directly, but existing API
        code may still use Job records for uploads, analysis or async tasks.
        """

        __tablename__ = "jobs"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.String(80), primary_key=True, default=make_uuid)

        kind = db.Column(db.String(120), nullable=False, default="generic", index=True)
        status = db.Column(db.String(40), nullable=False, default="queued", index=True)

        progress = db.Column(db.Integer, nullable=False, default=0)
        label = db.Column(db.String(255), nullable=True)

        conversation_id = db.Column(db.String(80), nullable=True, index=True)
        project_id = db.Column(db.String(120), nullable=True, index=True)
        user_id = db.Column(db.Integer, nullable=True, index=True)

        payload = db.Column(json_type(), nullable=False, default=dict)
        result = db.Column(json_type(), nullable=True)
        error = db.Column(db.Text, nullable=True)
        trace = db.Column(json_type(), nullable=False, default=list)

        started_at = db.Column(db.DateTime, nullable=True, index=True)
        finished_at = db.Column(db.DateTime, nullable=True, index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<Job id={self.id!r} kind={self.kind!r} status={self.status!r}>"
            except Exception:
                return "<Job>"

        @property
        def is_finished(self) -> bool:
            try:
                return str(self.status or "").lower() in {"done", "complete", "completed", "failed", "cancelled"}
            except Exception:
                return False

        def normalize(self) -> "Job":
            try:
                if not self.id:
                    self.id = make_uuid()

                self.kind = safe_str(self.kind, "generic", 120) or "generic"
                self.status = normalize_status(self.status, "queued")
                self.progress = safe_int(self.progress, 0, minimum=0, maximum=100)
                self.label = safe_str(self.label, "", 255) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None
                self.project_id = safe_str(self.project_id, "", 120) or None
                self.user_id = safe_int(self.user_id, 0) or None
                self.payload = safe_dict(self.payload)
                self.result = safe_dict(self.result) if self.result is not None else None
                self.error = safe_str(self.error, "", 8000) or None
                self.trace = safe_list(self.trace)
                self.metadata_json = safe_dict(self.metadata_json)
                return self
            except Exception:
                return self

        def start(self) -> None:
            try:
                self.status = "running"
                self.started_at = self.started_at or utcnow()
                self.touch()
            except Exception:
                pass

        def set_progress(self, value: Any) -> None:
            try:
                self.progress = safe_int(value, 0, minimum=0, maximum=100)
                self.touch()
            except Exception:
                pass

        def finish(self, result: Optional[Mapping[str, Any]] = None) -> None:
            try:
                self.status = "complete"
                self.progress = 100
                self.result = safe_dict(result)
                self.finished_at = utcnow()
                self.touch()
            except Exception:
                pass

        def fail(self, error: Any) -> None:
            try:
                self.status = "failed"
                self.error = safe_str(error, "unknown error", 8000)
                self.finished_at = utcnow()
                self.touch()
            except Exception:
                pass

        def to_dict(self, *, include_payload: bool = True) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "kind": self.kind,
                    "status": self.status,
                    "progress": self.progress,
                    "label": self.label,
                    "conversation_id": self.conversation_id,
                    "project_id": self.project_id,
                    "user_id": self.user_id,
                    "error": self.error,
                    "started_at": isoformat(self.started_at),
                    "finished_at": isoformat(self.finished_at),
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_payload:
                    payload["payload"] = safe_dict(self.payload)
                    payload["result"] = safe_dict(self.result)
                    payload["trace"] = safe_list(self.trace)
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {"id": getattr(self, "id", None), "status": getattr(self, "status", None)}

    return Job


# ─────────────────────────────────────────────────────────────
# Blob
# ─────────────────────────────────────────────────────────────

def _define_blob_model(*, extend_existing: bool = False):
    class Blob(TimestampMixin, SerializationMixin, db.Model):
        """
        Binary file storage.

        Current usage:
        - UI uploads store DXF/IFC/OBJ/STL/GLTF/GLB as Blob.
        - 2D routes stream DXF blobs.
        - Versioning can reference blob ids.
        """

        __tablename__ = "blobs"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.Integer, primary_key=True)

        filename = db.Column(db.String(512), nullable=False, default="file")
        mime = db.Column(db.String(255), nullable=True)
        size = db.Column(db.Integer, nullable=False, default=0)
        sha256 = db.Column(db.String(128), nullable=True, index=True)

        data = db.Column(db.LargeBinary, nullable=True)

        storage = db.Column(db.String(40), nullable=False, default="db")
        uri = db.Column(db.Text, nullable=True)

        uploaded_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        conversation_id = db.Column(db.String(80), nullable=True, index=True)
        project_id = db.Column(db.String(120), nullable=True, index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<Blob id={self.id!r} filename={self.filename!r} size={self.size!r}>"
            except Exception:
                return "<Blob>"

        @property
        def ext(self) -> str:
            try:
                import os

                return os.path.splitext(self.filename or "")[1].lower()
            except Exception:
                return ""

        @property
        def has_data(self) -> bool:
            try:
                return bool(self.data)
            except Exception:
                return False

        def normalize(self) -> "Blob":
            try:
                self.filename = safe_str(self.filename, "file", 512) or "file"
                self.mime = safe_str(self.mime, "", 255) or None
                self.size = safe_int(self.size, 0, minimum=0)
                self.sha256 = safe_str(self.sha256, "", 128) or None
                self.storage = safe_str(self.storage, "db", 40) or "db"
                self.uri = safe_str(self.uri, "", 2000) or None
                self.uploaded_by_user_id = safe_int(self.uploaded_by_user_id, 0) or None
                self.conversation_id = safe_str(self.conversation_id, "", 80) or None
                self.project_id = safe_str(self.project_id, "", 120) or None
                self.metadata_json = safe_dict(self.metadata_json)

                if self.data is not None and not self.size:
                    try:
                        self.size = len(self.data)
                    except Exception:
                        pass

                return self

            except Exception:
                return self

        def to_dict(self, *, include_data: bool = False) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "blob_id": self.id,
                    "filename": self.filename,
                    "mime": self.mime,
                    "size": self.size,
                    "sha256": self.sha256,
                    "storage": self.storage,
                    "uri": self.uri,
                    "ext": self.ext,
                    "uploaded_by_user_id": self.uploaded_by_user_id,
                    "conversation_id": self.conversation_id,
                    "project_id": self.project_id,
                    "metadata": safe_dict(self.metadata_json),
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_data:
                    payload["data"] = self.data

                return payload

            except Exception:
                return {"id": getattr(self, "id", None), "filename": getattr(self, "filename", None)}

    return Blob


# ─────────────────────────────────────────────────────────────
# Conversation
# ─────────────────────────────────────────────────────────────

def _define_conversation_model(*, extend_existing: bool = False):
    class Conversation(TimestampMixin, SerializationMixin, db.Model):
        """
        Chat/conversation transcript.

        The new project model owns project metadata. Conversation remains only
        the chat/history container and a compatibility anchor for older routes.
        """

        __tablename__ = "conversations"
        __table_args__ = _table_args(extend_existing)

        id = db.Column(db.String(80), primary_key=True, default=make_uuid)

        title = db.Column(db.String(255), nullable=True)
        project_id = db.Column(db.String(120), nullable=True, index=True)
        client_id = db.Column(db.String(80), nullable=True, index=True)
        owner_user_id = db.Column(db.Integer, nullable=True, index=True)

        transcript = db.Column(json_type(), nullable=False, default=list)
        summary = db.Column(db.Text, nullable=True)
        state = db.Column(json_type(), nullable=False, default=dict)

        status = db.Column(db.String(40), nullable=False, default="active", index=True)
        archived_at = db.Column(db.DateTime, nullable=True, index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<Conversation id={self.id!r} title={self.title!r}>"
            except Exception:
                return "<Conversation>"

        @property
        def messages(self) -> List[Dict[str, Any]]:
            try:
                return safe_list(self.transcript)
            except Exception:
                return []

        @messages.setter
        def messages(self, value: Any) -> None:
            try:
                self.transcript = safe_list(value)
            except Exception:
                self.transcript = []

        @property
        def is_archived(self) -> bool:
            try:
                return self.archived_at is not None or self.status == "archived"
            except Exception:
                return False

        def normalize(self) -> "Conversation":
            try:
                if not self.id:
                    self.id = make_uuid()

                self.title = safe_str(self.title, "", 255) or None
                self.project_id = safe_str(self.project_id, "", 120) or None
                self.client_id = safe_str(self.client_id, "", 80) or None
                self.owner_user_id = safe_int(self.owner_user_id, 0) or None
                self.transcript = safe_list(self.transcript)
                self.summary = safe_str(self.summary, "", 10000) or None
                self.state = safe_dict(self.state)
                self.status = normalize_status(self.status, "active")
                self.metadata_json = safe_dict(self.metadata_json)
                return self
            except Exception:
                return self

        def append_message(
            self,
            *,
            role: str,
            content: Any = "",
            meta: Optional[Mapping[str, Any]] = None,
            created_at: Optional[Any] = None,
            **extra: Any,
        ) -> Dict[str, Any]:
            try:
                message: Dict[str, Any] = {
                    "role": safe_str(role, "assistant", 80),
                    "content": content if content is not None else "",
                    "created_at": isoformat(created_at or utcnow()),
                }

                if meta is not None:
                    message["meta"] = safe_dict(meta)

                for key, value in extra.items():
                    if key and key not in message:
                        message[str(key)] = deep_copy_json(value)

                items = safe_list(self.transcript)
                items.append(message)
                self.transcript = items
                self.touch()

                return message

            except Exception:
                return {}

        def add_message(self, role: str, content: Any = "", **kwargs: Any) -> Dict[str, Any]:
            return self.append_message(role=role, content=content, **kwargs)

        def clear_transcript(self) -> None:
            try:
                self.transcript = []
                self.touch()
            except Exception:
                pass

        def archive(self) -> None:
            try:
                self.status = "archived"
                self.archived_at = utcnow()
                self.touch()
            except Exception:
                pass

        def restore(self) -> None:
            try:
                self.status = "active"
                self.archived_at = None
                self.touch()
            except Exception:
                pass

        def to_dict(
            self,
            *,
            include_transcript: bool = True,
            include_state: bool = True,
            include_meta: bool = True,
        ) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "chat_id": self.id,
                    "conversation_id": self.id,
                    "title": self.title,
                    "project_id": self.project_id,
                    "client_id": self.client_id,
                    "owner_user_id": self.owner_user_id,
                    "summary": self.summary,
                    "status": self.status,
                    "is_archived": self.is_archived,
                    "archived_at": isoformat(self.archived_at),
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_transcript:
                    payload["transcript"] = safe_list(self.transcript)
                    payload["messages"] = safe_list(self.transcript)

                if include_state:
                    payload["state"] = safe_dict(self.state)

                if include_meta:
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {"id": getattr(self, "id", None), "chat_id": getattr(self, "id", None)}

    return Conversation


# ─────────────────────────────────────────────────────────────
# MessageTemplate
# ─────────────────────────────────────────────────────────────

def _define_message_template_model(*, extend_existing: bool = False):
    class MessageTemplate(TimestampMixin, SerializationMixin, db.Model):
        """
        UI/message card template record.

        This remains app-local and must not depend on external 3D backends.
        """

        __tablename__ = "message_templates"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint("key", "version", name="uq_message_templates_key_version"),
        )

        id = db.Column(db.Integer, primary_key=True)

        key = db.Column(db.String(160), nullable=False, index=True)
        version = db.Column(db.Integer, nullable=False, default=1, index=True)

        renderer = db.Column(db.String(160), nullable=False, default="InfoCard")
        title = db.Column(db.String(255), nullable=True)
        description = db.Column(db.Text, nullable=True)

        template = db.Column(json_type(), nullable=False, default=dict)
        schema_json = db.Column("schema", json_type(), nullable=False, default=dict)
        ui_schema = db.Column(json_type(), nullable=False, default=dict)
        defaults = db.Column(json_type(), nullable=False, default=dict)

        active = db.Column(db.Boolean, nullable=False, default=True, index=True)
        status = db.Column(db.String(40), nullable=False, default="active", index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return f"<MessageTemplate key={self.key!r} version={self.version!r}>"
            except Exception:
                return "<MessageTemplate>"

        def normalize(self) -> "MessageTemplate":
            try:
                self.key = safe_str(self.key, "", 160)
                self.version = safe_int(self.version, 1, minimum=1)
                self.renderer = safe_str(self.renderer, "InfoCard", 160) or "InfoCard"
                self.title = safe_str(self.title, "", 255) or self.key or None
                self.description = safe_str(self.description, "", 4000) or None
                self.template = safe_dict(self.template)
                self.schema_json = safe_dict(self.schema_json)
                self.ui_schema = safe_dict(self.ui_schema)
                self.defaults = safe_dict(self.defaults)
                self.active = safe_bool(self.active, True)
                self.status = normalize_status(self.status, "active")
                self.metadata_json = safe_dict(self.metadata_json)
                return self
            except Exception:
                return self

        def to_dict(self, *, include_template: bool = True) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "key": self.key,
                    "version": self.version,
                    "renderer": self.renderer,
                    "title": self.title or self.key,
                    "description": self.description,
                    "active": bool(self.active),
                    "status": self.status,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_template:
                    payload["template"] = safe_dict(self.template)
                    payload["schema"] = safe_dict(self.schema_json)
                    payload["ui_schema"] = safe_dict(self.ui_schema)
                    payload["defaults"] = safe_dict(self.defaults)
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {"id": getattr(self, "id", None), "key": getattr(self, "key", None)}

    return MessageTemplate


# ─────────────────────────────────────────────────────────────
# ConversationState
# ─────────────────────────────────────────────────────────────

def _define_conversation_state_model(*, extend_existing: bool = False):
    class ConversationState(TimestampMixin, SerializationMixin, db.Model):
        """
        JSON state attached to a conversation.

        Used for viewer selection, workspace state and transient UI state.

        Compatibility API:
        - state_json is an alias for state.
        - get_or_create(...) is available as classmethod.
        - merge_patch(...) is available as classmethod.
        """

        __tablename__ = "conversation_states"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint("conversation_id", "key", name="uq_conversation_states_conversation_key"),
        )

        id = db.Column(db.Integer, primary_key=True)

        conversation_id = db.Column(db.String(80), nullable=False, index=True)
        key = db.Column(db.String(160), nullable=False, default="default", index=True)

        state = db.Column(json_type(), nullable=False, default=dict)
        selection = db.Column(json_type(), nullable=True)

        status = db.Column(db.String(40), nullable=False, default="active", index=True)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        @property
        def state_json(self) -> Dict[str, Any]:
            return _state_payload_from_row(self)

        @state_json.setter
        def state_json(self, value: Mapping[str, Any] | None) -> None:
            _set_row_state(self, value)

        def __repr__(self) -> str:
            try:
                return f"<ConversationState conversation_id={self.conversation_id!r} key={self.key!r}>"
            except Exception:
                return "<ConversationState>"

        @classmethod
        def get_or_create(
            cls,
            conversation_id: Any,
            key: str = "default",
            *,
            commit: bool = True,
        ) -> "ConversationState":
            return _conversation_state_get_or_create(
                cls,
                conversation_id,
                key,
                commit=commit,
            )

        @classmethod
        def merge_patch(
            cls,
            conversation_id: Any,
            patch: Mapping[str, Any] | None,
            key: str = "default",
            *,
            commit: bool = True,
        ) -> "ConversationState":
            return _conversation_state_merge_patch(
                cls,
                conversation_id,
                patch,
                key,
                commit=commit,
            )

        @classmethod
        def replace_state(
            cls,
            conversation_id: Any,
            state: Mapping[str, Any] | None,
            key: str = "default",
            *,
            commit: bool = True,
        ) -> "ConversationState":
            return _conversation_state_replace_state(
                cls,
                conversation_id,
                state,
                key,
                commit=commit,
            )

        def normalize(self) -> "ConversationState":
            try:
                self.conversation_id = safe_str(self.conversation_id, "", 80)
                self.key = safe_str(self.key, "default", 160) or "default"
                self.state = safe_dict(self.state)
                self.selection = safe_dict(self.selection) if self.selection is not None else None
                self.status = normalize_status(self.status, "active")
                self.metadata_json = safe_dict(self.metadata_json)
                return self
            except Exception:
                return self

        def update_state(self, patch: Optional[Mapping[str, Any]] = None, *, replace: bool = False) -> None:
            try:
                data = safe_dict(patch)

                if replace:
                    self.state = data
                else:
                    self.state = _deep_merge_state(self.state, data)

                _sync_selection_from_state(self, self.state)
                self.touch()

            except Exception:
                pass

        def set_selection(self, selection: Optional[Mapping[str, Any]]) -> None:
            try:
                self.selection = safe_dict(selection)
                self.touch()
            except Exception:
                pass

        def to_dict(self, *, include_meta: bool = True) -> Dict[str, Any]:
            try:
                state_dict = safe_dict(self.state)
                payload = {
                    "id": self.id,
                    "conversation_id": self.conversation_id,
                    "chat_id": self.conversation_id,
                    "key": self.key,
                    "state": state_dict,
                    "state_json": state_dict,
                    "selection": safe_dict(self.selection),
                    "status": self.status,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_meta:
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "conversation_id": getattr(self, "conversation_id", None),
                    "key": getattr(self, "key", None),
                }

    return ConversationState


# ─────────────────────────────────────────────────────────────
# Resolve models
# ─────────────────────────────────────────────────────────────

Client = _resolve_model("Client", "clients", _define_client_model)
IdempotencyKey = _resolve_model("IdempotencyKey", "idempotency_keys", _define_idempotency_key_model)
Job = _resolve_model("Job", "jobs", _define_job_model)
Blob = _resolve_model("Blob", "blobs", _define_blob_model)
Conversation = _resolve_model("Conversation", "conversations", _define_conversation_model)
MessageTemplate = _resolve_model("MessageTemplate", "message_templates", _define_message_template_model)
ConversationState = _install_conversation_state_compatibility(
    _resolve_model("ConversationState", "conversation_states", _define_conversation_state_model)
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def get_legacy_model_classes() -> List[Any]:
    return [
        Client,
        IdempotencyKey,
        Job,
        Blob,
        Conversation,
        MessageTemplate,
        ConversationState,
    ]


def get_legacy_model_table_names() -> List[str]:
    result: List[str] = []

    for model in get_legacy_model_classes():
        try:
            table_name = str(getattr(model, "__tablename__", "") or "")
            if table_name:
                result.append(table_name)
        except Exception:
            continue

    return result


def get_conversation(conversation_id: Any) -> Optional[Conversation]:
    try:
        conv_id = safe_str(conversation_id, "", 80)
        if not conv_id:
            return None

        return Conversation.query.get(conv_id)

    except Exception:
        return None


def create_conversation(
    *,
    title: str = "",
    project_id: str = "",
    client_id: str = "",
    owner_user_id: Optional[int] = None,
    commit: bool = True,
) -> Conversation:
    conv = Conversation()

    try:
        conv.title = safe_str(title, "", 255) or None
        conv.project_id = safe_str(project_id, "", 120) or None
        conv.client_id = safe_str(client_id, "", 80) or None
        conv.owner_user_id = safe_int(owner_user_id, 0) or None
        conv.transcript = []
        conv.state = {}
        conv.metadata_json = {}
        conv.normalize()

        db.session.add(conv)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return conv

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return conv


def serialize_conversation(
    conversation: Any,
    *,
    include_transcript: bool = True,
    include_state: bool = True,
    include_meta: bool = True,
) -> Dict[str, Any]:
    try:
        if conversation is None:
            return {}

        if hasattr(conversation, "to_dict"):
            try:
                return conversation.to_dict(
                    include_transcript=include_transcript,
                    include_state=include_state,
                    include_meta=include_meta,
                )
            except TypeError:
                return conversation.to_dict()

        return {
            "id": getattr(conversation, "id", None),
            "chat_id": getattr(conversation, "id", None),
            "title": getattr(conversation, "title", None),
            "project_id": getattr(conversation, "project_id", None),
            "created_at": isoformat(getattr(conversation, "created_at", None)),
            "updated_at": isoformat(getattr(conversation, "updated_at", None)),
        }

    except Exception:
        return {}


def append_conversation_message(
    conversation: Any,
    *,
    role: str,
    content: Any = "",
    meta: Optional[Mapping[str, Any]] = None,
    commit: bool = True,
    **extra: Any,
) -> Dict[str, Any]:
    try:
        if conversation is None:
            return {}

        if hasattr(conversation, "append_message"):
            message = conversation.append_message(role=role, content=content, meta=meta, **extra)
        else:
            message = {
                "role": safe_str(role, "assistant", 80),
                "content": content,
                "meta": safe_dict(meta),
                "created_at": isoformat(utcnow()),
            }
            message.update(safe_dict(extra))
            transcript = safe_list(getattr(conversation, "transcript", []))
            transcript.append(message)
            setattr(conversation, "transcript", transcript)

        db.session.add(conversation)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return message

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {}


def get_or_create_conversation_state(
    conversation_id: Any,
    key: str = "default",
    *,
    commit: bool = True,
) -> ConversationState:
    """
    Compatibility helper.

    Prefer ConversationState.get_or_create(...) in new route code.
    """
    try:
        return ConversationState.get_or_create(
            conversation_id,
            key,
            commit=commit,
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        fallback = ConversationState()
        fallback.conversation_id = safe_str(conversation_id, "", 80)
        fallback.key = safe_str(key, "default", 160) or "default"
        fallback.state = {}
        fallback.selection = None
        fallback.metadata_json = {}
        return fallback


def merge_conversation_state_patch(
    conversation_id: Any,
    patch: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> ConversationState:
    """
    Compatibility helper for merging state without importing the class directly.
    """
    return ConversationState.merge_patch(
        conversation_id,
        patch,
        key,
        commit=commit,
    )


def replace_conversation_state(
    conversation_id: Any,
    state: Mapping[str, Any] | None,
    key: str = "default",
    *,
    commit: bool = True,
) -> ConversationState:
    """
    Compatibility helper for replacing state without importing the class directly.
    """
    return ConversationState.replace_state(
        conversation_id,
        state,
        key,
        commit=commit,
    )


def get_legacy_model_status() -> Dict[str, Any]:
    try:
        models = get_legacy_model_classes()
        tables = get_legacy_model_table_names()

        counts: Dict[str, int] = {}

        for model in models:
            try:
                table_name = str(getattr(model, "__tablename__", "") or model.__name__)
                counts[table_name] = int(model.query.count())
            except Exception:
                counts[str(getattr(model, "__tablename__", model.__name__))] = -1

        conversation_state_api = {
            "hasGetOrCreate": callable(getattr(ConversationState, "get_or_create", None)),
            "hasMergePatch": callable(getattr(ConversationState, "merge_patch", None)),
            "hasReplaceState": callable(getattr(ConversationState, "replace_state", None)),
            "hasStateJson": hasattr(ConversationState, "state_json"),
        }

        return {
            "ok": True,
            "models": [getattr(model, "__name__", str(model)) for model in models],
            "tables": tables,
            "counts": counts,
            "conversationStateApi": conversation_state_api,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "models": [],
            "tables": [],
        }


__all__ = [
    "Client",
    "IdempotencyKey",
    "Job",
    "Blob",
    "Conversation",
    "MessageTemplate",
    "ConversationState",
    "get_legacy_model_classes",
    "get_legacy_model_table_names",
    "get_conversation",
    "create_conversation",
    "serialize_conversation",
    "append_conversation_message",
    "get_or_create_conversation_state",
    "merge_conversation_state_patch",
    "replace_conversation_state",
    "get_legacy_model_status",
]