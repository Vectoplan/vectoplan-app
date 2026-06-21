# services/vectoplan-app/routes/versions_api.py
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Blob, Conversation


bp = Blueprint("versions_api", __name__)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_KIND = "ARTIFACT"
DEFAULT_SOURCE_SERVICE = "vectoplan-app"

MAX_LABEL_LENGTH = 180
MAX_KIND_LENGTH = 80
MAX_FILENAME_LENGTH = 240
MAX_META_DEPTH = 8

_LEGACY_3D_PREFIX = "spe" + "ckle"
_LEGACY_DROP_KEYS = {
    _LEGACY_3D_PREFIX,
    f"{_LEGACY_3D_PREFIX}_project_id",
    f"{_LEGACY_3D_PREFIX}_model_id",
    f"{_LEGACY_3D_PREFIX}_version_id",
    f"{_LEGACY_3D_PREFIX}_commit_id",
    "viewer_url",
    "raw_viewer_url",
    "old_viewer",
    "old_viewer_url",
    "commit_id",
    "model_id",
}


# ───────────────────────── Response helpers ─────────────────────────

def _apply_no_cache_headers(response):
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return response


def _json_response(payload: Mapping[str, Any], status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    return _apply_no_cache_headers(response)


def _json_error(message: str, status: int = 400, *, code: Optional[str] = None):
    payload: Dict[str, Any] = {
        "ok": False,
        "error": {
            "message": str(message or "error"),
        },
        "status": status,
    }

    if code:
        payload["error"]["code"] = code

    return _json_response(payload, status=status)


# ───────────────────────── Config / parsing helpers ─────────────────────────

def _cfg_int(key: str, default: int) -> int:
    try:
        value = int(current_app.config.get(key, default))
        return value if value >= 0 else default
    except Exception:
        return default


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


def _clean_kind(value: Any) -> str:
    text = _clean_text(value, DEFAULT_KIND, MAX_KIND_LENGTH)

    if not text:
        return DEFAULT_KIND

    safe = []
    for char in text:
        if char.isalnum() or char in {"_", "-", "."}:
            safe.append(char)
        else:
            safe.append("_")

    result = "".join(safe).strip("._-")
    return result or DEFAULT_KIND


def _sanitize_filename(filename: Any, default: str = "file.bin") -> str:
    text = _clean_text(filename, default, MAX_FILENAME_LENGTH)

    if not text:
        return default

    # Keep this conservative. File download routes can still serve the original
    # name from Blob, but generated inline blobs should not contain path pieces.
    text = text.replace("\\", "/").split("/")[-1].strip()
    text = text.replace("\x00", "")

    safe = []
    for char in text:
        if char.isalnum() or char in {" ", "_", "-", ".", "(", ")"}:
            safe.append(char)
        else:
            safe.append("_")

    result = "".join(safe).strip()

    return result or default


def _sanitize_label(label: Any, fallback: str = "artifact") -> str:
    base = _clean_text(label, fallback, MAX_LABEL_LENGTH)

    if not base:
        base = fallback or "artifact"

    safe = []
    for char in base:
        if char.isalnum() or char in {" ", "_", "-", ".", "(", ")"}:
            safe.append(char)
        else:
            safe.append("_")

    result = "".join(safe).strip(" ._-")

    if not result:
        result = fallback or "artifact"

    # Keep existing UI behavior: generated labels are sortable and unique enough.
    try:
        timestamp = datetime.now().strftime("%d.%m.%Y__%H.%M")
    except Exception:
        timestamp = "now"

    if result.endswith(timestamp):
        return result

    return f"{result}__{timestamp}"


def _as_optional_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return dict(value)
    return None


def _request_json() -> Dict[str, Any]:
    try:
        payload = request.get_json(silent=True) or {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


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
    Return JSON-safe metadata while dropping legacy 3D backend fields.

    This API should remain neutral:
    - no old viewer URLs
    - no legacy project/model/version IDs
    - no backend-specific upload metadata
    """
    if depth > MAX_META_DEPTH:
        return None

    try:
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_key(key):
                    continue

                key_text = _clean_text(key, "", 160)
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


# ───────────────────────── Blob helpers ─────────────────────────

def _make_urls(file_id: str) -> Dict[str, str]:
    try:
        safe_id = quote(str(file_id or ""), safe="")

        return {
            "content_url": f"/v1/files/{safe_id}/content",
            "download_url": f"/v1/files/{safe_id}/download",
            "meta_url": f"/v1/files/{safe_id}",
        }

    except Exception:
        return {}


def _blob_payload(blob: Optional[Blob]) -> Optional[Dict[str, Any]]:
    if not blob:
        return None

    try:
        return {
            "file_id": blob.id,
            "blob_id": blob.id,
            "filename": blob.filename,
            "mime": blob.mime,
            "size": blob.size,
            "sha256": blob.sha256,
            **_make_urls(blob.id),
        }

    except Exception:
        return None


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return db.session.get(Conversation, str(chat_id))
    except Exception:
        try:
            return Conversation.query.get(str(chat_id))
        except Exception:
            return None


def _get_blob(blob_id: str) -> Optional[Blob]:
    try:
        return db.session.get(Blob, str(blob_id))
    except Exception:
        try:
            return Blob.query.get(str(blob_id))
        except Exception:
            return None


def _strip_data_url(value: str) -> Tuple[str, Optional[str]]:
    try:
        text = str(value or "").strip()

        if not text.startswith("data:"):
            return text, None

        header, separator, body = text.partition(",")

        if not separator:
            return text, None

        if ";base64" not in header.lower():
            return text, None

        mime = header.split(":", 1)[1].split(";", 1)[0].strip() or None
        return body.strip(), mime

    except Exception:
        return value, None


def _decode_base64_limit(value: str, mime_hint: Optional[str]) -> Tuple[bytes, str]:
    max_mb = _cfg_int("BASE64_UPLOAD_MAX_MB", 50)
    limit = max(1, max_mb) * 1024 * 1024

    text = str(value or "").strip()
    text, mime_from_data_url = _strip_data_url(text)

    try:
        text = "".join(char for char in text if not char.isspace())
    except Exception:
        pass

    if not text:
        raise ValueError("empty base64")

    approx_size = int(len(text) * 3 / 4)

    if approx_size > limit:
        raise ValueError("payload too large")

    try:
        content = base64.b64decode(text, validate=False)
    except Exception:
        try:
            content = base64.b64decode(text)
        except Exception as exc:
            raise ValueError("invalid base64") from exc

    if not content:
        raise ValueError("empty content")

    if len(content) > limit:
        raise ValueError("payload too large")

    mime = (mime_from_data_url or mime_hint or "").strip() or "application/octet-stream"

    try:
        if content.startswith(b"%PDF-"):
            mime = "application/pdf"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif b"ISO-10303-21" in content[:512]:
            mime = "model/ifc"
        elif content[:1] in (b"{", b"[") and mime == "application/octet-stream":
            mime = "application/json"
    except Exception:
        pass

    return content, mime


def _ensure_blob_from_inline(inline: Dict[str, Any]) -> Blob:
    """
    Create a Blob from inline data.

    Supported input:
    - {"base64": "...", "filename": "x", "mime": "y"}
    - {"json": {...}, "filename": "x.json", "mime": "application/json"}
    """
    try:
        if "json" in inline and inline["json"] is not None:
            try:
                payload = json.dumps(
                    inline["json"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except Exception as exc:
                raise ValueError(f"invalid json: {exc}") from exc

            filename = _sanitize_filename(inline.get("filename"), "data.json")
            mime = _clean_text(inline.get("mime"), "application/json", 160)
            sha = hashlib.sha256(payload).hexdigest()

            blob = Blob(
                filename=filename,
                mime=mime,
                size=len(payload),
                sha256=sha,
                data=payload,
            )

            db.session.add(blob)
            db.session.flush()

            return blob

        base64_value = str(inline.get("base64") or "")
        filename = _sanitize_filename(inline.get("filename"), "file.bin")
        mime_hint = _clean_text(inline.get("mime"), "", 160) or None

        content, mime = _decode_base64_limit(base64_value, mime_hint)
        sha = hashlib.sha256(content).hexdigest()

        blob = Blob(
            filename=filename,
            mime=mime,
            size=len(content),
            sha256=sha,
            data=content,
        )

        db.session.add(blob)
        db.session.flush()

        return blob

    except ValueError:
        raise

    except Exception as exc:
        raise ValueError(f"inline decode failed: {exc}") from exc


# ───────────────────────── Version helper ─────────────────────────

def _record_version(
    *,
    conv: Conversation,
    kind: str,
    label: str,
    source_message_id: Optional[str],
    input_blob: Optional[Blob],
    meta: Optional[Dict[str, Any]],
    source_service: Optional[str],
    source_kind: Optional[str],
    project_id: Optional[str],
    project_version_id: Optional[str],
    world_id: Optional[str],
    snapshot_id: Optional[str],
    artifact_ref: Optional[str],
    runtime_ref: Any,
    editor_url: Optional[str],
) -> Dict[str, Any]:
    try:
        from versioning import prune, record_version
    except Exception as exc:
        raise RuntimeError("versioning not available") from exc

    meta_out: Dict[str, Any] = {
        "source": DEFAULT_SOURCE_SERVICE,
        "legacy_3d_backend": False,
    }

    if input_blob:
        meta_out.update(
            {
                "filename": input_blob.filename,
                "mime": input_blob.mime,
                "size": input_blob.size,
                "sha256": input_blob.sha256,
                **_make_urls(input_blob.id),
            }
        )

    if meta:
        sanitized_meta = _sanitize_meta(meta)
        if isinstance(sanitized_meta, dict):
            meta_out.update(sanitized_meta)

    row = record_version(
        conversation_id=conv.id,
        kind=_clean_kind(kind),
        label=label,
        source_message_id=source_message_id,
        input_blob_id=input_blob.id if input_blob else None,
        status="stored",
        meta=meta_out,
        source_service=source_service or DEFAULT_SOURCE_SERVICE,
        source_kind=source_kind,
        project_id=project_id,
        project_version_id=project_version_id,
        world_id=world_id,
        snapshot_id=snapshot_id,
        artifact_ref=artifact_ref,
        runtime_ref=_sanitize_meta(runtime_ref),
        editor_url=editor_url,
    )

    keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", 10)) or 10

    try:
        prune(conversation_id=conv.id, kind=kind, keep=keep)
    except Exception:
        current_app.logger.warning("versions prune failed", exc_info=True)

    return {
        "artifact_id": row.get("artifact_id"),
        "artifact_ref": row.get("artifact_ref") or artifact_ref,
        "version_id": row.get("version_id"),
        "version_idx": row.get("version_idx"),
        "kind": row.get("kind") or kind,
        "label": row.get("label") or label,
        "created_at": row.get("created_at"),
        "source_service": source_service or DEFAULT_SOURCE_SERVICE,
        "source_kind": source_kind,
        "project_id": project_id,
        "project_version_id": project_version_id,
        "world_id": world_id,
        "snapshot_id": snapshot_id,
        "runtime_ref": _sanitize_meta(runtime_ref),
        "editor_url": editor_url,
        "meta": meta_out,
    }


# ───────────────────────── Routes ─────────────────────────

@bp.post("/v1/chats/<chat_id>/versions")
def create_version(chat_id: str):
    """
    Create a neutral local artifact version.

    Supported JSON body:

    {
      "kind": "BGA_IFC|BGA_MESH|DAA_JSON|FPA_SEED|BPA_DXF|BPA_PDF|...",
      "label": "optional",
      "source_message_id": "optional",

      "input_blob_id": "optional",
      "blob_id": "optional alias",

      "inline": {
        "base64": "...",
        "filename": "name.ext",
        "mime": "type"
      },

      "inline": {
        "json": {...},
        "filename": "data.json",
        "mime": "application/json"
      },

      "source_service": "vectoplan-app",
      "source_kind": "optional",
      "project_id": "optional",
      "project_version_id": "optional",
      "world_id": "optional",
      "snapshot_id": "optional",
      "artifact_ref": "optional",
      "runtime_ref": "optional object/string",
      "editor_url": "optional",
      "meta": {}
    }

    This endpoint intentionally does not publish to any 3D backend.
    """
    try:
        data = _request_json()

        kind = _clean_kind(data.get("kind") or DEFAULT_KIND)

        label_in = _clean_text(data.get("label"), "", MAX_LABEL_LENGTH)
        source_message_id = _clean_text(data.get("source_message_id"), "", 160) or None

        input_blob_id = (
            _clean_text(data.get("input_blob_id"), "", 160)
            or _clean_text(data.get("blob_id"), "", 160)
            or None
        )

        inline = _as_optional_dict(data.get("inline"))
        meta = _as_optional_dict(data.get("meta"))

        source_service = _clean_text(
            data.get("source_service"),
            DEFAULT_SOURCE_SERVICE,
            120,
        ) or DEFAULT_SOURCE_SERVICE

        source_kind = _clean_text(data.get("source_kind"), "", 120) or None
        project_id = _clean_text(data.get("project_id"), "", 160) or None
        project_version_id = _clean_text(data.get("project_version_id"), "", 160) or None
        world_id = _clean_text(data.get("world_id"), "", 160) or None
        snapshot_id = _clean_text(data.get("snapshot_id"), "", 160) or None
        artifact_ref = _clean_text(data.get("artifact_ref"), "", 240) or None
        editor_url = _clean_text(data.get("editor_url"), "", 1000) or None
        runtime_ref = data.get("runtime_ref")

        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        blob: Optional[Blob] = None

        if input_blob_id:
            blob = _get_blob(input_blob_id)
            if not blob:
                return _json_error("input_blob not found", 404, code="input_blob_not_found")

        elif inline:
            try:
                blob = _ensure_blob_from_inline(inline)
            except ValueError as exc:
                message = str(exc)
                status = 413 if "too large" in message.lower() else 400
                return _json_error(message, status, code="invalid_inline_payload")

        label = _sanitize_label(label_in or (blob.filename if blob else kind), fallback=kind)

        try:
            version = _record_version(
                conv=conv,
                kind=kind,
                label=label,
                source_message_id=source_message_id,
                input_blob=blob,
                meta=meta,
                source_service=source_service,
                source_kind=source_kind,
                project_id=project_id,
                project_version_id=project_version_id,
                world_id=world_id,
                snapshot_id=snapshot_id,
                artifact_ref=artifact_ref,
                runtime_ref=runtime_ref,
                editor_url=editor_url,
            )

            db.session.add(conv)
            db.session.commit()

        except RuntimeError as exc:
            current_app.logger.warning("versioning not available: %s", exc)
            return _json_error(
                "versioning not available",
                503,
                code="versioning_not_available",
            )

        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("create_version failed")
            return _json_error(str(exc), 500, code="create_version_failed")

        return _json_response(
            {
                "ok": True,
                "status": "ok",
                "chat_id": conv.id,
                "version": version,
                "input_blob": _blob_payload(blob),
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except ValueError as exc:
        message = str(exc)
        status = 413 if "too large" in message.lower() else 400
        return _json_error(message, status, code="invalid_request")

    except Exception as exc:
        current_app.logger.exception("versions.create failed")
        return _json_error(str(exc), 500, code="versions_create_failed")


@bp.get("/v1/chats/<chat_id>/versions")
def list_versions(chat_id: str):
    """
    List neutral local versions for a conversation.
    """
    try:
        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        try:
            from versioning import list_versions_by_conversation
        except Exception:
            return _json_response(
                {
                    "ok": True,
                    "chat_id": conv.id,
                    "items": [],
                    "total": 0,
                    "legacy_3d_backend": False,
                },
                status=200,
            )

        kind = _clean_text(request.args.get("kind"), "", MAX_KIND_LENGTH) or None
        items = list_versions_by_conversation(conversation_id=conv.id, kind=kind) or []

        return _json_response(
            {
                "ok": True,
                "chat_id": conv.id,
                "items": items,
                "total": len(items),
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except Exception as exc:
        current_app.logger.exception("versions.list failed")
        return _json_error(str(exc), 500, code="versions_list_failed")


@bp.get("/v1/chats/<chat_id>/versions/<version_id>")
def get_version(chat_id: str, version_id: str):
    """
    Return one local version by id, scoped to the given chat.
    """
    try:
        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        try:
            from versioning import list_versions_by_conversation
        except Exception:
            return _json_error(
                "versioning not available",
                503,
                code="versioning_not_available",
            )

        clean_version_id = _clean_text(version_id, "", 160)

        for item in list_versions_by_conversation(conversation_id=conv.id):
            if str(item.get("version_id") or "") == clean_version_id:
                return _json_response(
                    {
                        "ok": True,
                        "chat_id": conv.id,
                        "version": item,
                        "legacy_3d_backend": False,
                    },
                    status=200,
                )

        return _json_error("version not found", 404, code="version_not_found")

    except Exception as exc:
        current_app.logger.exception("versions.get failed")
        return _json_error(str(exc), 500, code="versions_get_failed")


@bp.delete("/v1/chats/<chat_id>/versions/<version_id>")
def delete_version(chat_id: str, version_id: str):
    """
    Delete one local transcript-based version entry.

    The chat_id is validated first so the route remains scoped to the current
    conversation even though the underlying transitional versioning helper only
    deletes by version_id.
    """
    try:
        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        try:
            from versioning import delete_version as delete_version_entry
            from versioning import list_versions_by_conversation
        except Exception:
            return _json_error(
                "versioning not available",
                503,
                code="versioning_not_available",
            )

        clean_version_id = _clean_text(version_id, "", 160)

        exists_in_chat = False
        for item in list_versions_by_conversation(conversation_id=conv.id):
            if str(item.get("version_id") or "") == clean_version_id:
                exists_in_chat = True
                break

        if not exists_in_chat:
            return _json_error("version not found", 404, code="version_not_found")

        deleted = bool(delete_version_entry(version_id=clean_version_id))

        if not deleted:
            return _json_error("version not found", 404, code="version_not_found")

        return _json_response(
            {
                "ok": True,
                "status": "deleted",
                "chat_id": conv.id,
                "version_id": clean_version_id,
            },
            status=200,
        )

    except Exception as exc:
        current_app.logger.exception("versions.delete failed")
        return _json_error(str(exc), 500, code="versions_delete_failed")