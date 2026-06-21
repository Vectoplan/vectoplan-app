# services/vectoplan-app/versioning.py
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from extensions import db
from models import Blob, Conversation


# ───────────────────────── Constants ─────────────────────────

VERSION_MESSAGE_TYPE = "version"
VERSION_ROLE = "service"

DEFAULT_VERSION_STATUS = "stored"
DEFAULT_SOURCE_SERVICE = "vectoplan-app"

MAX_LABEL_LENGTH = 240
MAX_KIND_LENGTH = 80
MAX_STATUS_LENGTH = 80
MAX_META_DEPTH = 8

_LEGACY_3D_PREFIX = "spe" + "ckle"
_LEGACY_DROP_KEYS = {
    _LEGACY_3D_PREFIX,
    f"{_LEGACY_3D_PREFIX}_project_id",
    f"{_LEGACY_3D_PREFIX}_model_id",
    f"{_LEGACY_3D_PREFIX}_version_id",
    f"{_LEGACY_3D_PREFIX}_commit_id",
    "old_viewer",
    "old_viewer_url",
    "viewer_url",
    "raw_viewer_url",
}


# ───────────────────────── Time / text helpers ─────────────────────────

def _utcnow_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return datetime.utcnow().isoformat() + "Z"


def _clean_text(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _clean_kind(kind: Any) -> str:
    text = _clean_text(kind, default="FILE", max_len=MAX_KIND_LENGTH)
    return text or "FILE"


def _clean_label(label: Any, fallback: str) -> str:
    text = _clean_text(label, default="", max_len=MAX_LABEL_LENGTH)
    return text or fallback or "Version"


def _clean_status(status: Any) -> str:
    text = _clean_text(status, default=DEFAULT_VERSION_STATUS, max_len=MAX_STATUS_LENGTH)
    return text or DEFAULT_VERSION_STATUS


def _ensure_list(value: Any) -> List[dict]:
    if isinstance(value, list):
        return value
    return []


def _ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


# ───────────────────────── Sanitizing helpers ─────────────────────────

def _is_legacy_key(key: Any) -> bool:
    try:
        key_text = str(key or "").strip().lower()

        if not key_text:
            return False

        if key_text in _LEGACY_DROP_KEYS:
            return True

        if key_text.startswith(_LEGACY_3D_PREFIX):
            return True

        return False

    except Exception:
        return False


def _sanitize_meta(value: Any, *, depth: int = 0) -> Any:
    """
    Return JSON-safe metadata without old backend fields.

    Keeps normal file/blob metadata intact, but removes old 3D backend keys from
    new and old payloads. This avoids leaking legacy viewer references through
    version APIs while still allowing old transcript entries to be listed.
    """
    if depth > MAX_META_DEPTH:
        return None

    try:
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_key(key):
                    continue

                key_text = _clean_text(key, default="", max_len=160)
                if not key_text:
                    continue

                clean[key_text] = _sanitize_meta(item, depth=depth + 1)

            return clean

        if isinstance(value, list):
            return [_sanitize_meta(item, depth=depth + 1) for item in value]

        if isinstance(value, tuple):
            return [_sanitize_meta(item, depth=depth + 1) for item in value]

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        return str(value)

    except Exception:
        return None


def _blob_meta(blob: Optional[Blob]) -> Dict[str, Any]:
    if not blob:
        return {}

    try:
        return {
            "file_id": getattr(blob, "id", None),
            "name": getattr(blob, "filename", None),
            "mime": getattr(blob, "mime", None),
            "size": getattr(blob, "size", None),
            "sha256": getattr(blob, "sha256", None),
        }
    except Exception:
        return {}


def _get_blob(blob_id: Optional[str]) -> Optional[Blob]:
    if not blob_id:
        return None

    try:
        return db.session.get(Blob, str(blob_id))
    except Exception:
        try:
            return Blob.query.get(str(blob_id))
        except Exception:
            return None


# ───────────────────────── Transcript helpers ─────────────────────────

def _iter_version_msgs(conv: Conversation) -> Iterable[dict]:
    for item in _ensure_list(getattr(conv, "transcript", None)):
        try:
            if not isinstance(item, dict):
                continue

            meta = item.get("meta") or {}

            if meta.get("type") != VERSION_MESSAGE_TYPE:
                continue

            if not isinstance(meta.get("version"), dict):
                continue

            yield item

        except Exception:
            continue


def _next_version_idx(conv: Conversation, kind: str) -> int:
    max_idx = 0

    for item in _iter_version_msgs(conv):
        try:
            version = item.get("meta", {}).get("version", {})
            if version.get("kind") != kind:
                continue

            max_idx = max(max_idx, int(version.get("version_idx") or 0))

        except Exception:
            continue

    return max_idx + 1


def _artifact_id(conversation_id: str, kind: str) -> str:
    try:
        base = f"{conversation_id}:{kind}"
        return sha256(base.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return sha256(_utcnow_iso().encode("utf-8")).hexdigest()[:16]


def _version_id(conversation_id: str, kind: str, label: str) -> str:
    try:
        base = f"{conversation_id}:{_utcnow_iso()}:{kind}:{label}"
        return sha256(base.encode("utf-8")).hexdigest()[:20]
    except Exception:
        return sha256(_utcnow_iso().encode("utf-8")).hexdigest()[:20]


def _append_version_message(conv: Conversation, version_payload: Dict[str, Any]) -> None:
    """
    Append a version entry to Conversation.transcript.

    Uses Conversation.append_message when available. Falls back to direct
    transcript mutation when older model implementations do not provide it.
    """
    message = {
        "role": VERSION_ROLE,
        "text": "",
        "trace": [version_payload.get("kind") or "version"],
        "meta": {
            "type": VERSION_MESSAGE_TYPE,
            "version": deepcopy(version_payload),
        },
    }

    try:
        append_message = getattr(conv, "append_message", None)

        if callable(append_message):
            append_message(
                role=VERSION_ROLE,
                text="",
                trace=[version_payload.get("kind") or "version"],
                meta={
                    "type": VERSION_MESSAGE_TYPE,
                    "version": deepcopy(version_payload),
                },
            )
            return

    except Exception:
        pass

    transcript = list(_ensure_list(getattr(conv, "transcript", None)))
    transcript.append(message)
    conv.transcript = transcript


def _compact_version_dict(version: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Compact, frontend-safe representation for version lists.

    This intentionally returns only neutral fields.
    """
    raw_meta = _ensure_dict(version.get("meta"))

    clean_meta = _sanitize_meta(raw_meta)
    if not isinstance(clean_meta, dict):
        clean_meta = {}

    return {
        "version_id": version.get("version_id"),
        "artifact_id": version.get("artifact_id"),
        "artifact_ref": version.get("artifact_ref"),
        "kind": version.get("kind"),
        "label": version.get("label"),
        "version_idx": version.get("version_idx"),
        "created_at": version.get("created_at"),
        "source_message_id": version.get("source_message_id"),
        "source_service": version.get("source_service") or DEFAULT_SOURCE_SERVICE,
        "source_kind": version.get("source_kind"),
        "project_id": version.get("project_id"),
        "project_version_id": version.get("project_version_id"),
        "world_id": version.get("world_id"),
        "snapshot_id": version.get("snapshot_id"),
        "runtime_ref": version.get("runtime_ref"),
        "editor_url": version.get("editor_url"),
        "input_blob_id": version.get("input_blob_id"),
        "blob_id": version.get("blob_id") or version.get("input_blob_id"),
        "status": version.get("status") or DEFAULT_VERSION_STATUS,
        "meta": clean_meta,
    }


def _load_conversation(conversation_id: str) -> Optional[Conversation]:
    try:
        return db.session.get(Conversation, str(conversation_id))
    except Exception:
        try:
            return Conversation.query.get(str(conversation_id))
        except Exception:
            return None


# ───────────────────────── Public API ─────────────────────────

def record_version(
    *,
    conversation_id: str,
    kind: str,
    label: str = "",
    source_message_id: Optional[str] = None,
    input_blob_id: Optional[str] = None,
    blob_id: Optional[str] = None,
    status: str = DEFAULT_VERSION_STATUS,
    meta: Optional[Dict[str, Any]] = None,
    source_service: Optional[str] = None,
    source_kind: Optional[str] = None,
    project_id: Optional[str] = None,
    project_version_id: Optional[str] = None,
    world_id: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    artifact_ref: Optional[str] = None,
    runtime_ref: Optional[Dict[str, Any] | str] = None,
    editor_url: Optional[str] = None,
    **ignored_legacy_kwargs: Any,
) -> Dict[str, Any]:
    """
    Record a neutral local version entry as a service message in the transcript.

    This is still transcript-based and intentionally does not introduce new
    tables. It is a safe transitional versioning layer for files, DXF plans,
    generated artifacts and later editor/runtime references.

    Unknown legacy keyword arguments are accepted and ignored so that files not
    migrated yet do not immediately break the app during the step-by-step
    cleanup.
    """
    conv = _load_conversation(conversation_id)

    if not conv:
        raise ValueError("conversation not found")

    clean_kind = _clean_kind(kind)
    clean_label = _clean_label(label, fallback=clean_kind)
    clean_status = _clean_status(status)

    effective_blob_id = input_blob_id or blob_id
    blob = _get_blob(effective_blob_id)

    clean_meta = _sanitize_meta(meta or {})
    if not isinstance(clean_meta, dict):
        clean_meta = {}

    if ignored_legacy_kwargs:
        clean_meta.setdefault(
            "ignored_legacy_args",
            sorted(
                [
                    _clean_text(key, max_len=120)
                    for key in ignored_legacy_kwargs.keys()
                    if _clean_text(key, max_len=120)
                ]
            ),
        )

    if blob:
        clean_meta.setdefault("input_blob", _blob_meta(blob))

    now = _utcnow_iso()
    version_idx = _next_version_idx(conv, clean_kind)
    generated_version_id = _version_id(str(conversation_id), clean_kind, clean_label)
    generated_artifact_id = _artifact_id(str(conversation_id), clean_kind)

    version_payload: Dict[str, Any] = {
        "version_id": generated_version_id,
        "artifact_id": generated_artifact_id,
        "artifact_ref": artifact_ref,
        "kind": clean_kind,
        "label": clean_label,
        "version_idx": version_idx,
        "created_at": now,
        "source_message_id": source_message_id,
        "source_service": _clean_text(source_service, DEFAULT_SOURCE_SERVICE, 120),
        "source_kind": _clean_text(source_kind, "", 120) or None,
        "project_id": _clean_text(project_id, "", 160) or None,
        "project_version_id": _clean_text(project_version_id, "", 160) or None,
        "world_id": _clean_text(world_id, "", 160) or None,
        "snapshot_id": _clean_text(snapshot_id, "", 160) or None,
        "runtime_ref": _sanitize_meta(runtime_ref),
        "editor_url": _clean_text(editor_url, "", 1000) or None,
        "input_blob_id": effective_blob_id,
        "blob_id": effective_blob_id,
        "status": clean_status,
        "meta": clean_meta,
    }

    try:
        _append_version_message(conv, version_payload)
        db.session.add(conv)
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return {
        "artifact_id": generated_artifact_id,
        "artifact_ref": artifact_ref,
        "version_id": generated_version_id,
        "version_idx": version_idx,
    }


def list_versions_by_conversation(
    *,
    conversation_id: str,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conv = _load_conversation(conversation_id)

    if not conv:
        return []

    requested_kind = _clean_kind(kind) if kind else None
    items: List[Dict[str, Any]] = []

    for item in _iter_version_msgs(conv):
        try:
            version = item.get("meta", {}).get("version", {})

            if requested_kind and version.get("kind") != requested_kind:
                continue

            items.append(_compact_version_dict(version))

        except Exception:
            continue

    items.sort(
        key=lambda entry: (
            int(entry.get("version_idx") or 0),
            str(entry.get("created_at") or ""),
        ),
        reverse=True,
    )

    return items


def get_version(*, version_id: str) -> Optional[Dict[str, Any]]:
    """
    Return one compact version by id.
    """
    clean_version_id = _clean_text(version_id, "", 160)

    if not clean_version_id:
        return None

    try:
        for conv in Conversation.query:
            for item in _iter_version_msgs(conv):
                version = item.get("meta", {}).get("version", {})
                if version.get("version_id") == clean_version_id:
                    return _compact_version_dict(version)
    except Exception:
        return None

    return None


def delete_version(*, version_id: str) -> bool:
    """
    Remove one version message from the transcript.
    """
    clean_version_id = _clean_text(version_id, "", 160)

    if not clean_version_id:
        return False

    found: Optional[Conversation] = None

    try:
        for conv in Conversation.query:
            for item in _iter_version_msgs(conv):
                version = item.get("meta", {}).get("version", {})

                if version.get("version_id") == clean_version_id:
                    found = conv
                    break

            if found:
                break

    except Exception:
        return False

    if not found:
        return False

    try:
        new_transcript: List[dict] = []

        for item in _ensure_list(getattr(found, "transcript", None)):
            keep = True

            try:
                meta = item.get("meta") or {}

                if meta.get("type") == VERSION_MESSAGE_TYPE:
                    version = meta.get("version") or {}

                    if version.get("version_id") == clean_version_id:
                        keep = False

            except Exception:
                pass

            if keep:
                new_transcript.append(item)

        found.transcript = new_transcript
        db.session.add(found)
        db.session.commit()

        return True

    except Exception:
        db.session.rollback()
        raise


def prune(*, conversation_id: str, kind: str, keep: int = 10) -> None:
    """
    Keep at most `keep` versions of one kind in the transcript.

    Older entries are removed. This function is intentionally conservative and
    only touches entries with meta.type == "version".
    """
    conv = _load_conversation(conversation_id)

    if not conv:
        return

    clean_kind = _clean_kind(kind)

    try:
        keep_count = max(0, int(keep))
    except Exception:
        keep_count = 10

    versions: List[Tuple[int, str]] = []

    for item in _iter_version_msgs(conv):
        try:
            version = item.get("meta", {}).get("version", {})

            if version.get("kind") != clean_kind:
                continue

            version_idx = int(version.get("version_idx") or 0)
            version_id = str(version.get("version_id") or "")

            if version_id:
                versions.append((version_idx, version_id))

        except Exception:
            continue

    versions.sort(key=lambda item: item[0], reverse=True)

    if len(versions) <= keep_count:
        return

    to_remove = {version_id for _, version_id in versions[keep_count:]}

    if not to_remove:
        return

    try:
        new_transcript: List[dict] = []

        for item in _ensure_list(getattr(conv, "transcript", None)):
            remove = False

            try:
                meta = item.get("meta") or {}

                if meta.get("type") == VERSION_MESSAGE_TYPE:
                    version = meta.get("version") or {}

                    if (
                        version.get("kind") == clean_kind
                        and version.get("version_id") in to_remove
                    ):
                        remove = True

            except Exception:
                pass

            if not remove:
                new_transcript.append(item)

        conv.transcript = new_transcript
        db.session.add(conv)
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise


__all__ = [
    "record_version",
    "list_versions_by_conversation",
    "get_version",
    "delete_version",
    "prune",
]