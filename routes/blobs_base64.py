# services/app/routes/blobs_base64.py
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from extensions import db
from models import Blob, Conversation

try:
    from routes.chat.helpers import attachment_meta_for_transcript
except Exception:
    attachment_meta_for_transcript = None  # type: ignore


bp = Blueprint("blobs_base64", __name__)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_FILENAME = "file.bin"
DEFAULT_MIME = "application/octet-stream"
MAX_FILENAME_LENGTH = 240
MAX_MIME_LENGTH = 160

KNOWN_EXTENSION_MIMES = {
    ".dxf": "application/dxf",
    ".ifc": "model/ifc",
    ".obj": "model/obj",
    ".stl": "model/stl",
    ".gltf": "model/gltf+json",
    ".glb": "model/gltf-binary",
    ".json": "application/json",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

LEGACY_DROP_KEYS = {
    "spe" + "ckle",
    "spe" + "ckle_project_id",
    "spe" + "ckle_model_id",
    "spe" + "ckle_version_id",
    "viewer_url",
    "raw_viewer_url",
    "old_viewer_url",
    "model_id",
    "commit_id",
    "stream_id",
}


# ───────────────────────── Response helpers ─────────────────────────

def _apply_json_headers(response):
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return response


def _json_response(payload: Dict[str, Any], status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    return _apply_json_headers(response)


def _json_error(message: str, status: int = 400, *, code: Optional[str] = None):
    payload: Dict[str, Any] = {
        "ok": False,
        "status": "error",
        "error": {
            "message": str(message or "error"),
        },
        "legacy_3d_backend": False,
    }

    if code:
        payload["error"]["code"] = code

    return _json_response(payload, status=status)


# ───────────────────────── Generic helpers ─────────────────────────

def _cfg_int(key: str, default: int) -> int:
    try:
        value = int(current_app.config.get(key, default))
        return value if value >= 0 else default
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
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


def _extension(filename: str) -> str:
    try:
        if "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[-1].lower()
    except Exception:
        return ""


def _safe_filename(filename: Any, default: str = DEFAULT_FILENAME) -> str:
    try:
        raw = _clean_text(filename, default, MAX_FILENAME_LENGTH)
        raw = raw.replace("\\", "/").split("/")[-1].replace("\x00", "").strip()

        if not raw:
            raw = default

        secured = secure_filename(raw)

        if not secured:
            secured = default

        if len(secured) > MAX_FILENAME_LENGTH:
            root = secured
            ext = ""

            if "." in secured:
                root, ext = secured.rsplit(".", 1)
                ext = "." + ext

            secured = root[: MAX_FILENAME_LENGTH - len(ext)] + ext

        return secured or default

    except Exception:
        return default


def _safe_mime(mime: Any, filename: str = "") -> str:
    try:
        value = _clean_text(mime, "", MAX_MIME_LENGTH).lower()

        if value and "/" in value and not any(ch in value for ch in "\r\n\t"):
            return value

        ext = _extension(filename)

        if ext in KNOWN_EXTENSION_MIMES:
            return KNOWN_EXTENSION_MIMES[ext]

        guessed, _encoding = mimetypes.guess_type(filename or "")

        if guessed:
            return guessed

        return DEFAULT_MIME

    except Exception:
        return DEFAULT_MIME


def _infer_mime_from_bytes(content: bytes, mime_hint: Optional[str], filename: str) -> str:
    try:
        ext = _extension(filename)

        if ext in KNOWN_EXTENSION_MIMES:
            return KNOWN_EXTENSION_MIMES[ext]

        mime = _safe_mime(mime_hint, filename)

        if content.startswith(b"%PDF-"):
            return "application/pdf"

        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return "image/gif"

        if content.startswith(b"RIFF") and b"WEBP" in content[:16]:
            return "image/webp"

        if content[:4] == b"glTF":
            return "model/gltf-binary"

        if b"ISO-10303-21" in content[:512]:
            return "model/ifc"

        if content[:1] in (b"{", b"["):
            try:
                json.loads(content.decode("utf-8", errors="strict"))
                return "application/json"
            except Exception:
                pass

        head = content[:512].decode("utf-8", errors="ignore").upper()
        if "SECTION" in head and ("ENTITIES" in head or "HEADER" in head):
            return "application/dxf"

        return mime or DEFAULT_MIME

    except Exception:
        return _safe_mime(mime_hint, filename)


def _is_legacy_key(key: Any) -> bool:
    try:
        text = str(key or "").strip().lower()

        if not text:
            return False

        if text in LEGACY_DROP_KEYS:
            return True

        if text.startswith("spe" + "ckle"):
            return True

        return False

    except Exception:
        return False


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None

    try:
        if isinstance(value, dict):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_key(key):
                    continue

                key_text = _clean_text(key, "", 160)
                if not key_text:
                    continue

                clean[key_text] = _json_safe(item, depth=depth + 1)

            return clean

        if isinstance(value, list):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, tuple):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        return str(value)

    except Exception:
        return None


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


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return db.session.get(Conversation, str(chat_id))
    except Exception:
        try:
            return Conversation.query.get(str(chat_id))
        except Exception:
            return None


def _strip_data_url(value: str) -> Tuple[str, Optional[str]]:
    """
    data:<mime>;base64,<data> -> (<data>, <mime>)
    """
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


def _approx_bytes_from_b64(value: str) -> int:
    try:
        return int(len(value or "") * 3 / 4)
    except Exception:
        return 0


def _decode_base64_limit(value: str, mime_hint: Optional[str]) -> Tuple[bytes, Optional[str]]:
    """
    Decode base64 safely with BASE64_UPLOAD_MAX_MB limit.

    Supports data:<mime>;base64,... URLs.
    """
    max_mb = _cfg_int("BASE64_UPLOAD_MAX_MB", 50)
    limit = max(1, max_mb) * 1024 * 1024

    text = str(value or "").strip()
    text, mime_from_data_url = _strip_data_url(text)

    try:
        text = "".join(char for char in text if not char.isspace())
    except Exception:
        pass

    approx_size = _approx_bytes_from_b64(text)

    if approx_size <= 0:
        raise ValueError("empty base64")

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

    return content, mime_from_data_url or mime_hint


def _blob_payload(blob: Blob) -> Dict[str, Any]:
    try:
        urls = _make_urls(blob.id)

        return {
            "ok": True,
            "file_id": blob.id,
            "blob_id": blob.id,
            "id": blob.id,
            "filename": blob.filename,
            "name": blob.filename,
            "mime": blob.mime,
            "size": blob.size,
            "sha256": blob.sha256,
            "urls": urls,
            **urls,
            "legacy_3d_backend": False,
        }

    except Exception:
        return {
            "ok": False,
            "file_id": getattr(blob, "id", None),
            "id": getattr(blob, "id", None),
            "legacy_3d_backend": False,
        }


def _attachment_meta(blob: Blob) -> Dict[str, Any]:
    try:
        if callable(attachment_meta_for_transcript):
            meta = attachment_meta_for_transcript(blob)  # type: ignore
            if isinstance(meta, dict):
                meta["legacy_3d_backend"] = False
                return meta
    except Exception:
        pass

    try:
        urls = _make_urls(blob.id)

        return {
            "id": blob.id,
            "file_id": blob.id,
            "blob_id": blob.id,
            "filename": blob.filename,
            "name": blob.filename,
            "mime": blob.mime,
            "size": blob.size,
            "sha256": blob.sha256,
            **urls,
            "legacy_3d_backend": False,
        }

    except Exception:
        return {
            "id": getattr(blob, "id", None),
            "legacy_3d_backend": False,
        }


def _append_attachment_message(
    conv: Conversation,
    *,
    blob: Blob,
    title: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        attachment = _attachment_meta(blob)

        clean_meta = {
            "type": "attachment",
            "source": "base64_upload",
            "legacy_3d_backend": False,
        }

        if meta:
            safe_meta = _json_safe(meta)
            if isinstance(safe_meta, dict):
                clean_meta.update(safe_meta)

        try:
            return conv.append_message(
                "service",
                text=title,
                attachments=[attachment],
                trace=["upload"],
                meta=clean_meta,
            )
        except TypeError:
            return conv.append_message(
                role="service",
                text=title,
                attachments=[attachment],
                trace=["upload"],
                meta=clean_meta,
            )

    except Exception:
        try:
            current_app.logger.warning("append base64 attachment message failed", exc_info=True)
        except Exception:
            pass
        return None


# ───────────────────────── Route ─────────────────────────

@bp.post("/v1/chats/<chat_id>/blobs/base64")
def post_blob_base64(chat_id: str):
    """
    Create a Blob from base64 input.

    Body JSON:
    {
      "filename": "file.ifc",
      "mime": "model/ifc",
      "base64": "...",
      "attach_message": false,
      "message_title": "optional",
      "meta": {}
    }

    This endpoint only stores a Blob and optionally writes a transcript
    attachment message. It does not publish, import or send files to any 3D
    backend.
    """
    try:
        data = request.get_json(silent=True) or {}

        if not isinstance(data, dict):
            return _json_error("invalid json body", 400, code="invalid_json")

        base64_value = data.get("base64")

        if isinstance(base64_value, bytes):
            base64_text = base64_value.decode("utf-8", errors="ignore")
        elif isinstance(base64_value, str):
            base64_text = base64_value
        else:
            return _json_error("base64 required", 400, code="base64_required")

        if not base64_text.strip():
            return _json_error("base64 required", 400, code="base64_required")

        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        filename = _safe_filename(data.get("filename"), DEFAULT_FILENAME)
        mime_hint = _safe_mime(data.get("mime"), filename)

        content, mime_from_payload = _decode_base64_limit(base64_text, mime_hint)
        mime = _infer_mime_from_bytes(content, mime_from_payload, filename)
        sha = hashlib.sha256(content).hexdigest()

        blob = Blob(
            filename=filename,
            mime=mime or DEFAULT_MIME,
            size=len(content),
            sha256=sha,
            data=content,
        )

        db.session.add(blob)
        db.session.flush()

        assistant_msg: Optional[Dict[str, Any]] = None

        attach_message = _as_bool(data.get("attach_message"), False)

        if attach_message:
            title = _clean_text(data.get("message_title"), "Datei gespeichert", 240)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else None

            assistant_msg = _append_attachment_message(
                conv,
                blob=blob,
                title=title,
                meta=meta,
            )

        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        payload = {
            "ok": True,
            "status": "ok",
            "chat_id": conv.id,
            **_blob_payload(blob),
            "legacy_3d_backend": False,
        }

        if assistant_msg:
            payload["assistant_msg"] = assistant_msg

        return _json_response(payload, status=200)

    except ValueError as exc:
        message = str(exc)
        status = 413 if "too large" in message.lower() else 400
        return _json_error(message, status, code="invalid_base64_payload")

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass

        current_app.logger.exception("post_blob_base64 failed")
        return _json_error(str(exc), 500, code="post_blob_base64_failed")