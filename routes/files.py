# services/app/routes/files.py
from __future__ import annotations

import base64
import hashlib
import mimetypes
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from extensions import db
from models import Blob


bp = Blueprint("files", __name__)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_MIME = "application/octet-stream"
DEFAULT_FILENAME = "file"
MAX_FILENAME_LENGTH = 240
MAX_MIME_LENGTH = 160

INLINE_B64_MIMES = {
    "application/json",
    "application/xml",
    "text/json",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".csv",
    ".json",
    ".xml",
    ".md",
    ".dxf",
    ".obj",
    ".ifc",
}

KNOWN_EXTENSION_MIMES = {
    ".dxf": "application/dxf",
    ".ifc": "model/ifc",
    ".obj": "model/obj",
    ".stl": "model/stl",
    ".gltf": "model/gltf+json",
    ".glb": "model/gltf-binary",
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
        "error": {
            "message": str(message or "error"),
        },
        "status": status,
        "legacy_3d_backend": False,
    }

    if code:
        payload["error"]["code"] = code

    return _json_response(payload, status=status)


def _apply_file_security_headers(response):
    try:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return response


def _apply_cache_headers(response, *, strong: bool = False, inline: bool = False):
    try:
        if strong:
            max_age = _cfg_int("FILE_CACHE_MAX_AGE", 3600)
            response.headers.setdefault("Cache-Control", f"private, max-age={max_age}")
        elif inline:
            max_age = _cfg_int("FILE_CONTENT_CACHE_MAX_AGE", 3600)
            response.headers.setdefault("Cache-Control", f"public, max-age={max_age}")
        else:
            response.headers.setdefault("Cache-Control", "private, max-age=60")
    except Exception:
        pass

    return response


# ───────────────────────── Config / generic helpers ─────────────────────────

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

        # secure_filename can remove unicode-only names entirely.
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


def _infer_mime_from_bytes(data: bytes, current_mime: str, filename: str) -> str:
    """
    Lightweight magic-byte inference.

    Keeps explicit known model/CAD MIME types from filename where possible.
    """
    try:
        ext = _extension(filename)

        if ext in KNOWN_EXTENSION_MIMES:
            return KNOWN_EXTENSION_MIMES[ext]

        mime = _safe_mime(current_mime, filename)

        if data.startswith(b"%PDF-"):
            return "application/pdf"

        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"

        if data.startswith(b"PK\x03\x04"):
            return "application/zip"

        if b"ISO-10303-21" in data[:512]:
            return "model/ifc"

        if ext in TEXT_EXTENSIONS:
            return KNOWN_EXTENSION_MIMES.get(ext) or "text/plain"

        return mime or DEFAULT_MIME

    except Exception:
        return current_mime or DEFAULT_MIME


def _etag(sha256_hex: str) -> str:
    try:
        value = _clean_text(sha256_hex, "", 128)

        if not value:
            return ""

        return f"\"{value}\""

    except Exception:
        return ""


def _etag_matches(etag: str) -> bool:
    try:
        if not etag:
            return False

        incoming = request.headers.get("If-None-Match", "") or ""

        if not incoming:
            return False

        values = [item.strip() for item in incoming.split(",") if item.strip()]

        return etag in values or etag.strip('"') in [item.strip('"') for item in values]

    except Exception:
        return False


def _file_id_for_url(file_id: Any) -> str:
    try:
        return quote(str(file_id or ""), safe="")
    except Exception:
        return ""


def _make_urls(file_id: str) -> Dict[str, str]:
    try:
        safe_id = _file_id_for_url(file_id)

        return {
            "content_url": f"/v1/files/{safe_id}/content",
            "download_url": f"/v1/files/{safe_id}/download",
            "meta_url": f"/v1/files/{safe_id}",
        }

    except Exception:
        return {}


def _should_inline_b64(size: int, mime: str) -> bool:
    try:
        limit = _cfg_int("ATTACHMENT_INLINE_BASE64_MAX", 0)

        if limit <= 0:
            return False

        if int(size or 0) > limit:
            return False

        normalized = str(mime or "").strip().lower()

        return (
            normalized.startswith("image/")
            or normalized.startswith("text/")
            or normalized in INLINE_B64_MIMES
        )

    except Exception:
        return False


def _blob_data_bytes(blob: Blob) -> bytes:
    try:
        data = getattr(blob, "data", None)

        if data is None:
            return b""

        if isinstance(data, bytes):
            return data

        if isinstance(data, bytearray):
            return bytes(data)

        return bytes(data)

    except Exception:
        return b""


def _meta_from_blob(blob: Blob, include_b64: bool = False) -> Dict[str, Any]:
    try:
        size = int(getattr(blob, "size", 0) or 0)
        mime = _safe_mime(getattr(blob, "mime", DEFAULT_MIME), getattr(blob, "filename", ""))

        meta: Dict[str, Any] = {
            "ok": True,
            "id": blob.id,
            "file_id": blob.id,
            "blob_id": blob.id,
            "filename": blob.filename,
            "name": blob.filename,
            "mime": mime,
            "size": size,
            "sha256": getattr(blob, "sha256", "") or "",
            "legacy_3d_backend": False,
            **_make_urls(blob.id),
        }

        if include_b64 and _should_inline_b64(size, mime):
            try:
                meta["base64"] = base64.b64encode(_blob_data_bytes(blob)).decode("ascii")
            except Exception:
                pass

        return meta

    except Exception:
        return {
            "ok": False,
            "id": getattr(blob, "id", None),
            "legacy_3d_backend": False,
        }


def _get_blob(file_id: str) -> Optional[Blob]:
    """
    Robust Blob lookup supporting int-like and string/UUID-like IDs.
    """
    raw = _clean_text(file_id, "", 160)

    if not raw:
        return None

    candidates: List[Any] = []

    if raw.isdigit():
        try:
            candidates.append(int(raw))
        except Exception:
            pass

    candidates.append(raw)

    for candidate in candidates:
        try:
            blob = db.session.get(Blob, candidate)
            if blob:
                return blob
        except Exception:
            pass

        try:
            blob = Blob.query.get(candidate)
            if blob:
                return blob
        except Exception:
            pass

    return None


def _uploaded_files() -> List[Any]:
    try:
        if "files" in request.files:
            return request.files.getlist("files")

        if "file" in request.files:
            return [request.files["file"]]

        return []

    except Exception:
        return []


def _create_blob_from_upload(file_storage) -> Blob:
    raw_filename = getattr(file_storage, "filename", None)
    filename = _safe_filename(raw_filename, DEFAULT_FILENAME)
    raw_data = file_storage.read()

    if raw_data is None:
        raw_data = b""

    if not isinstance(raw_data, (bytes, bytearray)):
        raw_data = bytes(raw_data)

    data = bytes(raw_data)

    mime = _safe_mime(getattr(file_storage, "mimetype", None), filename)
    mime = _infer_mime_from_bytes(data, mime, filename)

    sha = hashlib.sha256(data).hexdigest()

    blob = Blob(
        filename=filename,
        mime=mime,
        size=len(data),
        sha256=sha,
        data=data,
    )

    db.session.add(blob)
    db.session.flush()

    return blob


def _content_disposition_filename(filename: Any) -> str:
    try:
        safe_name = _safe_filename(filename, DEFAULT_FILENAME)
        encoded = quote(safe_name)
        return safe_name, encoded
    except Exception:
        return DEFAULT_FILENAME, DEFAULT_FILENAME


# ───────────────────────── Routes ─────────────────────────

@bp.post("/v1/files")
def upload_files():
    """
    Store uploaded files as Blob rows.

    This endpoint only stores files. It does not import, publish, or send them to
    any 3D backend.
    """
    try:
        files = _uploaded_files()

        if not files:
            return _json_error("no files", 400, code="no_files")

        items: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []

        for file_storage in files:
            original_filename = getattr(file_storage, "filename", "file")

            try:
                blob = _create_blob_from_upload(file_storage)
                items.append(_meta_from_blob(blob, include_b64=True))

            except Exception as exc:
                current_app.logger.warning(
                    "file upload item failed: %s",
                    exc,
                    exc_info=True,
                )
                errors.append(
                    {
                        "filename": _clean_text(original_filename, "file", MAX_FILENAME_LENGTH),
                        "error": str(exc),
                    }
                )

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("file upload commit failed")
            return _json_error(str(exc), 500, code="upload_commit_failed")

        status = 201 if items and not errors else (207 if items and errors else 422)

        return _json_response(
            {
                "ok": bool(items),
                "status": "ok" if items else "error",
                "items": items,
                "results": items,
                "errors": errors,
                "total": len(items),
                "legacy_3d_backend": False,
            },
            status=status,
        )

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("upload_files failed")
        return _json_error(str(exc), 500, code="upload_files_failed")


@bp.get("/v1/files/<file_id>")
def file_meta(file_id: str):
    try:
        blob = _get_blob(file_id)

        if not blob:
            return _json_error("not found", 404, code="file_not_found")

        include_b64 = _as_bool(request.args.get("include_base64"), False)
        payload = _meta_from_blob(blob, include_b64=include_b64)

        response = jsonify(payload)

        etag = _etag(getattr(blob, "sha256", "") or "")
        if etag:
            response.headers.setdefault("ETag", etag)

        _apply_file_security_headers(response)
        _apply_cache_headers(response, strong=False)

        return response, 200

    except Exception as exc:
        current_app.logger.exception("file_meta failed")
        return _json_error(str(exc), 500, code="file_meta_failed")


@bp.get("/v1/files/<file_id>/download")
def file_download(file_id: str):
    try:
        blob = _get_blob(file_id)

        if not blob:
            return _json_error("not found", 404, code="file_not_found")

        data = _blob_data_bytes(blob)
        etag = _etag(getattr(blob, "sha256", "") or "")

        if etag and _etag_matches(etag):
            response = Response(status=304)
            response.headers.setdefault("ETag", etag)
            return _apply_file_security_headers(response)

        filename, encoded_filename = _content_disposition_filename(getattr(blob, "filename", None))
        mime = _safe_mime(getattr(blob, "mime", DEFAULT_MIME), filename)

        response = send_file(
            BytesIO(data),
            mimetype=mime,
            as_attachment=True,
            download_name=filename,
            conditional=False,
        )

        try:
            response.headers["Content-Disposition"] = (
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
            )
            response.headers["Content-Length"] = str(len(data))

            if etag:
                response.headers.setdefault("ETag", etag)
        except Exception:
            pass

        _apply_file_security_headers(response)
        _apply_cache_headers(response, strong=True)

        return response

    except Exception as exc:
        current_app.logger.exception("file_download failed")
        return _json_error(str(exc), 500, code="file_download_failed")


@bp.get("/v1/files/<file_id>/content")
def file_content(file_id: str):
    try:
        blob = _get_blob(file_id)

        if not blob:
            return _json_error("not found", 404, code="file_not_found")

        data = _blob_data_bytes(blob)
        etag = _etag(getattr(blob, "sha256", "") or "")

        if etag and _etag_matches(etag):
            response = Response(status=304)
            response.headers.setdefault("ETag", etag)
            return _apply_file_security_headers(response)

        filename, encoded_filename = _content_disposition_filename(getattr(blob, "filename", None))
        mime = _safe_mime(getattr(blob, "mime", DEFAULT_MIME), filename)

        response = send_file(
            BytesIO(data),
            mimetype=mime,
            as_attachment=False,
            download_name=filename,
            conditional=False,
        )

        try:
            response.headers["Content-Disposition"] = (
                f'inline; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
            )
            response.headers["Content-Length"] = str(len(data))

            if etag:
                response.headers.setdefault("ETag", etag)
        except Exception:
            pass

        _apply_file_security_headers(response)
        _apply_cache_headers(response, inline=True)

        return response

    except Exception as exc:
        current_app.logger.exception("file_content failed")
        return _json_error(str(exc), 500, code="file_content_failed")