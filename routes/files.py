# /services/app/routes/files.py
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from typing import List, Dict, Any

from flask import Blueprint, request, jsonify, send_file, abort, current_app, Response

from extensions import db
from models import Blob

bp = Blueprint("files", __name__)


# ───────────────────────── helpers ─────────────────────────

def _cfg_int(key: str, default: int) -> int:
    try:
        v = int(current_app.config.get(key, default))
        return v if v >= 0 else default
    except Exception:
        return default


def _etag(sha256_hex: str) -> str:
    try:
        # Starkes ETag
        return f"\"{sha256_hex}\""
    except Exception:
        return ""


def _make_urls(file_id: str) -> Dict[str, str]:
    try:
        base = ""  # relative Pfade, Frontend kann host_url voranstellen
        return {
            "content_url": f"{base}/v1/files/{file_id}/content",
            "download_url": f"{base}/v1/files/{file_id}/download",
            "meta_url": f"{base}/v1/files/{file_id}",
        }
    except Exception:
        return {}


def _should_inline_b64(size: int, mime: str) -> bool:
    try:
        limit = _cfg_int("ATTACHMENT_INLINE_BASE64_MAX", 0)
        if limit <= 0:
            return False
        if size > limit:
            return False
        # Bilder und Text eignen sich eher
        m = (mime or "").lower()
        return m.startswith("image/") or m.startswith("text/") or m in {"application/json", "application/xml"}
    except Exception:
        return False


def _meta_from_blob(b: Blob, include_b64: bool = False) -> Dict[str, Any]:
    try:
        meta = {
            "id": b.id,
            "file_id": b.id,         # Kompatibilität
            "filename": b.filename,
            "mime": b.mime,
            "size": b.size,
            "sha256": b.sha256,
            **_make_urls(b.id),
        }
        if include_b64 and _should_inline_b64(b.size or 0, b.mime or ""):
            try:
                meta["base64"] = base64.b64encode(b.data).decode("ascii")
            except Exception:
                pass
        return meta
    except Exception:
        return {"id": getattr(b, "id", None)}


# ───────────────────────── routes ─────────────────────────

@bp.post("/v1/files")
def upload_files():
    try:
        files: List = []
        if "files" in request.files:
            files = request.files.getlist("files")
        elif "file" in request.files:
            files = [request.files["file"]]

        if not files:
            return jsonify({"error": "no files"}), 400

        out: List[Dict[str, Any]] = []
        errs: List[Dict[str, str]] = []

        for f in files:
            try:
                data = f.read()  # kann leer sein, ist ok
                sha = hashlib.sha256(data).hexdigest()
                b = Blob(
                    filename=(f.filename or "file"),
                    mime=(f.mimetype or "application/octet-stream"),
                    size=len(data),
                    sha256=sha,
                    data=data,
                )
                db.session.add(b)
                # ID sofort materialisieren
                db.session.flush()

                meta = _meta_from_blob(b, include_b64=True)
                out.append(meta)
            except Exception as ex_item:
                current_app.logger.warning("file upload item failed: %s", ex_item, exc_info=True)
                errs.append({"filename": getattr(f, "filename", "file"), "error": str(ex_item)})

        try:
            db.session.commit()
        except Exception as ex_commit:
            db.session.rollback()
            current_app.logger.exception("file upload commit failed")
            return jsonify({"error": str(ex_commit)}), 500

        status = 201 if out and not errs else (207 if out and errs else 422)
        return jsonify({"items": out, "errors": errs, "total": len(out)}), status

    except Exception as ex:
        db.session.rollback()
        current_app.logger.exception("upload_files failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/v1/files/<file_id>")
def file_meta(file_id: str):
    try:
        b = Blob.query.get(str(file_id))
        if not b:
            return jsonify({"error": "not found"}), 404
        resp = jsonify(_meta_from_blob(b, include_b64=False))
        try:
            resp.headers.setdefault("ETag", _etag(b.sha256 or ""))
            resp.headers.setdefault("Cache-Control", "public, max-age=60")
        except Exception:
            pass
        return resp
    except Exception as ex:
        current_app.logger.exception("file_meta failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/v1/files/<file_id>/download")
def file_download(file_id: str):
    try:
        b = Blob.query.get(str(file_id))
        if not b:
            abort(404)

        etag = _etag(b.sha256 or "")
        inm = request.headers.get("If-None-Match", "")
        if etag and inm and etag in inm:
            return Response(status=304)

        resp = send_file(BytesIO(b.data), mimetype=b.mime, as_attachment=True, download_name=b.filename)
        try:
            resp.headers.setdefault("ETag", etag)
            max_age = _cfg_int("FILE_CACHE_MAX_AGE", 3600)
            resp.headers.setdefault("Cache-Control", f"private, max-age={max_age}")
        except Exception:
            pass
        return resp
    except Exception as ex:
        current_app.logger.exception("file_download failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/v1/files/<file_id>/content")
def file_content(file_id: str):
    try:
        b = Blob.query.get(str(file_id))
        if not b:
            abort(404)

        etag = _etag(b.sha256 or "")
        inm = request.headers.get("If-None-Match", "")
        if etag and inm and etag in inm:
            return Response(status=304)

        # inline, damit <img>/<video> rendern
        resp = send_file(BytesIO(b.data), mimetype=b.mime, as_attachment=False, download_name=b.filename)
        try:
            max_age = _cfg_int("FILE_CONTENT_CACHE_MAX_AGE", 3600)
            resp.headers.setdefault("Cache-Control", f"public, max-age={max_age}")
            resp.headers.setdefault("ETag", etag)
        except Exception:
            pass
        return resp
    except Exception as ex:
        current_app.logger.exception("file_content failed")
        return jsonify({"error": str(ex)}), 500
