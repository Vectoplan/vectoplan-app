# /services/app/routes/versions_api.py
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request, current_app

from extensions import db
from models import Conversation, Blob

bp = Blueprint("versions_api", __name__)

# ───────────────────────── helpers ─────────────────────────

def _json_error(msg_text: str, code: int = 400):
    try:
        return jsonify({"error": msg_text}), code
    except Exception:
        return {"error": msg_text}, code

def _cfg_int(key: str, default: int) -> int:
    try:
        v = int(current_app.config.get(key, default))
        return v if v >= 0 else default
    except Exception:
        return default

def _sanitize_label(label: str) -> str:
    try:
        base = (label or "artifact").strip()
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base)
        if not safe:
            safe = "artifact"
        ts = datetime.now().strftime("%d.%m.%Y__%H.%M")
        return f"{safe}__{ts}"
    except Exception:
        return "artifact__" + datetime.now().strftime("%d.%m.%Y__%H.%M")

def _make_urls(file_id: str) -> Dict[str, str]:
    try:
        from urllib.parse import quote as _q
        fid = _q(str(file_id or ""), safe="")
        base = ""
        return {
            "content_url": f"{base}/v1/files/{fid}/content",
            "download_url": f"{base}/v1/files/{fid}/download",
            "meta_url": f"{base}/v1/files/{fid}",
        }
    except Exception:
        return {}

def _strip_data_url(b64: str) -> Tuple[str, Optional[str]]:
    try:
        s = (b64 or "").strip()
        if not s.startswith("data:"):
            return s, None
        head, _, body = s.partition(",")
        if ";base64" not in head.lower():
            return s, None
        mime = head.split(":", 1)[1].split(";", 1)[0].strip() or None
        return body.strip(), mime
    except Exception:
        return b64, None

def _decode_base64_limit(b64: str, mime_hint: Optional[str]) -> Tuple[bytes, str]:
    max_mb = _cfg_int("BASE64_UPLOAD_MAX_MB", 50)
    limit = max(1, max_mb) * 1024 * 1024

    s = (b64 or "").strip()
    s, mime_from_data = _strip_data_url(s)
    # remove whitespace
    try:
        s = "".join(ch for ch in s if not ch.isspace())
    except Exception:
        pass

    if not s:
        raise ValueError("empty base64")

    approx = int(len(s) * 3 / 4)
    if approx > limit:
        raise ValueError("payload too large")

    try:
        content = base64.b64decode(s, validate=False)
    except Exception:
        try:
            content = base64.b64decode(s)
        except Exception:
            raise ValueError("invalid base64")

    if not content:
        raise ValueError("empty content")
    if len(content) > limit:
        raise ValueError("payload too large")

    mime = (mime_from_data or mime_hint or "").strip() or "application/octet-stream"
    # quick magic
    try:
        if content.startswith(b"%PDF-"):
            mime = "application/pdf"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif b"ISO-10303-21" in content[:256]:
            mime = "model/ifc"
        elif content[:1] in (b"{", b"["):
            # likely text/json
            mime = "application/json" if mime == "application/octet-stream" else mime
    except Exception:
        pass
    return content, mime

def _ensure_blob_from_inline(inline: Dict[str, Any]) -> Blob:
    """
    Erzeugt einen Blob aus inline-Daten.
      Variante A: {"base64":"...","filename":"x","mime":"y"}
      Variante B: {"json":{...},"filename":"x.json","mime":"application/json"}
    """
    try:
        if "json" in inline and inline["json"] is not None:
            try:
                payload = json.dumps(inline["json"], ensure_ascii=False, indent=None).encode("utf-8")
            except Exception as ex:
                raise ValueError(f"invalid json: {ex}")
            filename = str(inline.get("filename") or "data.json")
            mime = str(inline.get("mime") or "application/json")
            sha = hashlib.sha256(payload).hexdigest()
            b = Blob(filename=filename, mime=mime, size=len(payload), sha256=sha, data=payload)
            db.session.add(b)
            db.session.flush()
            return b

        b64 = str(inline.get("base64") or "")
        filename = str(inline.get("filename") or "file.bin")
        mime_hint = str(inline.get("mime") or "") or None
        content, mime = _decode_base64_limit(b64, mime_hint)
        sha = hashlib.sha256(content).hexdigest()
        b = Blob(filename=filename, mime=mime, size=len(content), sha256=sha, data=content)
        db.session.add(b)
        db.session.flush()
        return b
    except ValueError:
        raise
    except Exception as ex:
        raise ValueError(f"inline decode failed: {ex}")

def _record_version(
    *,
    conv: Conversation,
    kind: str,
    label: str,
    source_message_id: Optional[str],
    speckle: Optional[Dict[str, Any]],
    input_blob: Optional[Blob],
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        from versioning import record_version, prune
    except Exception:
        raise RuntimeError("versioning not available")

    meta_out: Dict[str, Any] = {"speckle": dict(speckle or {})}
    if input_blob:
        meta_out.update({
            "filename": input_blob.filename,
            "mime": input_blob.mime,
            "size": input_blob.size,
            "sha256": input_blob.sha256,
            **_make_urls(input_blob.id),
        })
    if meta:
        try:
            meta_out.update(dict(meta))
        except Exception:
            pass

    row = record_version(
        conversation_id=conv.id,
        kind=str(kind or "ARTIFACT").strip() or "ARTIFACT",
        label=label,
        source_message_id=source_message_id,
        input_blob_id=input_blob.id if input_blob else None,
        speckle_project_id=(speckle or {}).get("project_id"),
        speckle_model_id=(speckle or {}).get("model_id"),
        speckle_version_id=(speckle or {}).get("version_id"),
        status="ok",
        meta=meta_out,
    )

    keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", 10)) or 10
    try:
        prune(conversation_id=conv.id, kind=row.get("kind") or kind, keep=keep)
    except Exception:
        current_app.logger.warning("versions prune failed", exc_info=True)

    return {
        "artifact_id": row.get("artifact_id"),
        "version_id": row.get("version_id"),
        "version_idx": row.get("version_idx"),
        "kind": row.get("kind") or kind,
        "label": row.get("label") or label,
        "created_at": row.get("created_at"),
        "meta": meta_out,
    }

# ───────────────────────── routes ─────────────────────────

@bp.post("/v1/chats/<chat_id>/versions")
def create_version():
    """
    Legt eine Version für ein Artefakt an.

    Body JSON:
    {
      "kind": "BGA_IFC|BGA_MESH|DAA_JSON|FPA_SEED|BPA_DXF|BPA_PDF|...",
      "label": "optional, wird mit Zeitstempel ergänzt",
      "source_message_id": "optional",
      "input_blob_id": "optional",                // Alternative zu "inline"
      "inline": {                                  // optional
         "base64":"...","filename":"name.ext","mime":"type"   // oder
         // JSON Shortcut:
         "json": {...}, "filename":"data.json","mime":"application/json"
      },
      "speckle": { "project_id":"...", "model_id":"...", "version_id":"..." },  // optional
      "meta": { ... }                       // optional, wird in meta gemerged
    }

    Antwort 200:
    {
      "status": "ok",
      "chat_id": "...",
      "version": { artifact_id, version_id, version_idx, kind, label, created_at, meta },
      "input_blob": { file_id, filename, mime, size, sha256, urls? }  // wenn vorhanden
    }
    """
    conv = None
    try:
        data = request.get_json(silent=True) or {}
        kind = str(data.get("kind") or "").strip()
        label_in = str(data.get("label") or "").strip()
        source_message_id = str(data.get("source_message_id") or "").strip() or None
        input_blob_id = str(data.get("input_blob_id") or "").strip() or None
        inline = data.get("inline") if isinstance(data.get("inline"), dict) else None
        speckle = data.get("speckle") if isinstance(data.get("speckle"), dict) else None
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else None

        if not kind:
            return _json_error("kind required", 400)

        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        # Blob besorgen/erzeugen (optional)
        blob: Optional[Blob] = None
        if input_blob_id:
            blob = Blob.query.get(input_blob_id)
            if not blob:
                return _json_error("input_blob not found", 404)
        elif inline:
            try:
                blob = _ensure_blob_from_inline(inline)
            except ValueError as ve:
                return _json_error(str(ve), 400)

        # Label aufbereiten
        label = _sanitize_label(label_in or (blob.filename if blob else kind))

        try:
            ver = _record_version(
                conv=conv,
                kind=kind,
                label=label,
                source_message_id=source_message_id,
                speckle=speckle,
                input_blob=blob,
                meta=meta,
            )
            db.session.add(conv)
            db.session.commit()
        except RuntimeError as re:
            current_app.logger.warning("versioning not available: %s", re)
            return _json_error("versioning not available", 503)
        except Exception as ex:
            db.session.rollback()
            current_app.logger.exception("create_version failed")
            return _json_error(str(ex), 500)

        input_blob_meta = None
        if blob:
            input_blob_meta = {
                "file_id": blob.id,
                "filename": blob.filename,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
                **_make_urls(blob.id),
            }

        return jsonify({
            "status": "ok",
            "chat_id": conv.id,
            "version": ver,
            "input_blob": input_blob_meta,
        }), 200

    except ValueError as ve:
        msg = str(ve)
        code = 413 if "too large" in msg else 400
        return _json_error(msg, code)
    except Exception as ex:
        current_app.logger.exception("versions.create failed")
        return _json_error(str(ex), 500)
