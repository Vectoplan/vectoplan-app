# /services/app/routes/blobs_base64.py
from __future__ import annotations

import base64
import hashlib
from typing import Dict, Any, Optional, Tuple

from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models import Conversation, Blob

# Optional: einheitliche Attachment-Metadaten für Transcript übernehmen
try:
    from routes.chat.helpers import attachment_meta_for_transcript  # type: ignore
except Exception:  # Fallback, falls Helper nicht verfügbar
    attachment_meta_for_transcript = None  # type: ignore

bp = Blueprint("blobs_base64", __name__)


# ───────────────────────── helpers ─────────────────────────

def _cfg_int(key: str, default: int) -> int:
    try:
        v = int(current_app.config.get(key, default))
        return v if v >= 0 else default
    except Exception:
        return default


def _json_error(msg_text: str, code: int = 400):
    try:
        return jsonify({"error": msg_text}), code
    except Exception:
        return {"error": msg_text}, code


def _strip_data_url(b64: str) -> Tuple[str, Optional[str]]:
    """
    data:<mime>;base64,<data> → (<data>, <mime>)
    Sonst (<input>, None)
    """
    try:
        s = (b64 or "").strip()
        if not s.startswith("data:"):
            return s, None
        head, _, body = s.partition(",")
        if ";base64" not in head.lower():
            # Nicht unterstützte data:-Variante
            return s, None
        mime = head.split(":", 1)[1].split(";", 1)[0].strip() or None
        return body.strip(), mime
    except Exception:
        return b64, None


def _approx_bytes_from_b64(s: str) -> int:
    try:
        # konservative Abschätzung
        ln = len(s or "")
        return int(ln * 3 / 4)
    except Exception:
        return 0


def _make_urls(file_id: str) -> Dict[str, str]:
    try:
        base = ""  # relative Pfade
        from urllib.parse import quote as _q
        fid = _q(str(file_id or ""), safe="")
        return {
            "content_url": f"{base}/v1/files/{fid}/content",
            "download_url": f"{base}/v1/files/{fid}/download",
            "meta_url": f"{base}/v1/files/{fid}",
        }
    except Exception:
        return {}


def _decode_base64_limit(b64: str, mime_hint: Optional[str]) -> Tuple[bytes, str]:
    """
    Dekodiert Base64 sicher, mit Größenlimit aus Config:
      BASE64_UPLOAD_MAX_MB (Default 50 MB)
    Entfernt Whitespaces. Unterstützt data:-URLs.
    Liefert (bytes, mime).
    """
    # Limit
    max_mb = _cfg_int("BASE64_UPLOAD_MAX_MB", 50)
    limit = max(1, max_mb) * 1024 * 1024

    # data:-URL und Whitespace entfernen
    s = (b64 or "").strip()
    s, mime_from_data = _strip_data_url(s)
    s = "".join(ch for ch in s if not ch.isspace())

    # Preflight-Größencheck
    approx = _approx_bytes_from_b64(s)
    if approx <= 0:
        raise ValueError("empty base64")
    if approx > limit:
        raise ValueError("payload too large")

    try:
        content = base64.b64decode(s, validate=False)
    except Exception:
        # letzte Rettung ohne validate
        try:
            content = base64.b64decode(s)
        except Exception:
            raise ValueError("invalid base64")

    if not content:
        raise ValueError("empty content")

    if len(content) > limit:
        raise ValueError("payload too large")

    # MIME bestimmen
    mime = (mime_from_data or mime_hint or "").strip() or "application/octet-stream"
    try:
        # einfache Magic Bytes
        if content.startswith(b"%PDF-"):
            mime = "application/pdf"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif content[:4] == b"glTF":
            mime = "model/gltf-binary"
        elif b"ISO-10303-21" in content[:256]:
            mime = "model/ifc"
    except Exception:
        pass

    return content, mime


# ───────────────────────── route ─────────────────────────

@bp.post("/v1/chats/<chat_id>/blobs/base64")
def post_blob_base64(chat_id: str):
    """
    Body JSON:
      {
        "filename": "file.ifc",
        "mime": "model/ifc",            // optional, wird ggf. überschrieben
        "base64": "....",               // erlaubt auch data:<mime>;base64,<...>
        "attach_message": false,        // optional
        "message_title": "optional"     // optional, nur bei attach_message
      }

    Antwort 200:
      {
        "status": "ok",
        "chat_id": "...",
        "file_id": "...",
        "filename": "...",
        "mime": "...",
        "size": 123,
        "sha256": "...",
        "urls": {content_url, download_url, meta_url},
        "assistant_msg": {...}          // nur wenn attach_message==true
      }
    """
    conv: Optional[Conversation] = None
    try:
        data = request.get_json(silent=True) or {}
        filename = str(data.get("filename") or "file.bin")
        mime_in = str(data.get("mime") or "").strip() or None
        b64 = data.get("base64")
        attach_message = bool(data.get("attach_message") or False)
        message_title = str(data.get("message_title") or "").strip()

        if not isinstance(b64, (str, bytes)) or not b64:
            return _json_error("base64 required", 400)

        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        # Base64 dekodieren
        content, mime = _decode_base64_limit(b64.decode("utf-8", "ignore") if isinstance(b64, bytes) else b64, mime_in)

        # Blob anlegen
        sha = hashlib.sha256(content).hexdigest()
        blob = Blob(
            filename=filename or "file.bin",
            mime=mime or "application/octet-stream",
            size=len(content),
            sha256=sha,
            data=content,
        )
        db.session.add(blob)
        db.session.flush()  # blob.id verfügbar

        # Optional: Nachricht im Transcript
        assistant_msg: Dict[str, Any] | None = None
        if attach_message:
            try:
                att = None
                if callable(attachment_meta_for_transcript):
                    att = attachment_meta_for_transcript(blob)  # type: ignore
                else:
                    # Minimal-Metadaten fallback
                    att = {
                        "id": blob.id,
                        "file_id": blob.id,
                        "filename": blob.filename,
                        "mime": blob.mime,
                        "size": blob.size,
                        **_make_urls(blob.id),
                    }
                title = message_title or "Datei gespeichert"
                assistant_msg = conv.append_message("service", text=title, attachments=[att], trace=["upload"], meta={"type": "attachment"})
            except Exception:
                assistant_msg = None

        # Commit
        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        resp = {
            "status": "ok",
            "chat_id": conv.id,
            "file_id": blob.id,
            "filename": blob.filename,
            "mime": blob.mime,
            "size": blob.size,
            "sha256": blob.sha256,
            "urls": _make_urls(blob.id),
        }
        if assistant_msg:
            resp["assistant_msg"] = assistant_msg
        return jsonify(resp), 200

    except ValueError as ve:
        # z. B. invalid base64, payload too large
        msg = str(ve)
        code = 413 if "too large" in msg else 400
        return _json_error(msg, code)
    except Exception as ex:
        current_app.logger.exception("post_blob_base64 failed")
        return _json_error(str(ex), 500)
