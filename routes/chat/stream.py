# /services/app/routes/chat/stream.py
from __future__ import annotations

from flask import Response, stream_with_context, request, current_app

from extensions import db
from models import Conversation, Blob

from . import bp
from .helpers import (
    sse, chunk_text, load_attachments, attachment_meta_for_transcript,
    auto_upload_supported_attachments, resolve_viewer_url, call_chatai, apply_chatai_actions,
    numeric_project_id,
)


def _as_bool(v) -> bool:
    try:
        if isinstance(v, bool):
            return v
        s = str(v or "").strip().lower()
        return s in {"1", "true", "yes", "y", "on"}
    except Exception:
        return False


def _normalize_file_ids(seq) -> list[str]:
    out = []
    try:
        for x in (seq or []):
            if isinstance(x, (str, bytes)):
                s = x.decode("utf-8", "ignore") if isinstance(x, bytes) else str(x)
                s = s.strip()
                if s:
                    out.append(s)
            elif isinstance(x, dict):
                s = str(x.get("file_id") or x.get("id") or "").strip()
                if s:
                    out.append(s)
    except Exception:
        pass
    return out


def _should_upload_from(out_json: dict | None) -> tuple[bool, str]:
    """
    True, wenn ChatAI den Upload freigibt (Intent OK, keine need_info).
    Liefert (flag, intent).
    """
    try:
        if not isinstance(out_json, dict):
            return False, ""
        status = str(out_json.get("status") or "ok").lower()
        intent = str(out_json.get("intent") or "").upper()
        if status == "need_info":
            return False, intent
        return intent in {"UPLOAD_VERSION", "PUBLISH_3D"}, intent
    except Exception:
        return False, ""


@bp.post("/v1/chat/stream")
def chat_stream():
    """
    Streaming-Chat-API (SSE).
    Request JSON:
      {
        "chat_id": "<optional>",
        "message": "Text oder leer",
        "attachments": ["<file_id>" | {"file_id":"..."} | {"id":"..."}, ...],
        "defer_upload": true|false,
        "include_attachments": true|false
      }

    Events:
      start   { event:"start",   chat_id }
      upload  { event:"upload",  items:[...] }        # nur bei synchronem Upload
      actions { event:"actions", result:{...} }
      delta   { event:"delta",   text:"..." }
      done    { event:"done",    assistant_msg:{...}, viewer_url:"..." }
    """
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "")
    raw_atts = data.get("attachments") or []
    file_ids = _normalize_file_ids(raw_atts)
    current_app.logger.info("chat(stream): raw_atts=%s -> file_ids=%s", raw_atts, file_ids)

    if not text and not file_ids:
        return {"error": "empty message"}, 400

    defer_upload = _as_bool(request.args.get("defer_upload")) or _as_bool(data.get("defer_upload"))
    include_att = not (
        _as_bool(request.args.get("include_attachments") == "0")
        or _as_bool(data.get("include_attachments") == 0)
        or _as_bool(data.get("no_att"))
    )

    try:
        # Conversation holen/erzeugen
        if data.get("chat_id"):
            c = Conversation.query.get(str(data["chat_id"]))
            if not c:
                return {"error": "chat not found"}, 404
        else:
            c = Conversation()
            db.session.add(c)
            db.session.flush()

        # User-Nachricht speichern (mit vollständigen Attachment-Metadaten inkl. URLs)
        atts_meta = []
        for fid in file_ids:
            try:
                b = Blob.query.get(str(fid))
                if b:
                    atts_meta.append(attachment_meta_for_transcript(b))
            except Exception:
                continue
        user_msg = c.append_message("user", text=text, attachments=atts_meta)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        # Payload für ChatAI vorbereiten (chat_id explizit mitgeben)
        payload = {
            "chat_id": c.id,
            "message": text,
            "coordinate": [0.0, 0.0],
            "project_id": numeric_project_id(c),
            "chat_message_id": __import__("time").time_ns() % 2_000_000_000,
            "attachments": load_attachments(file_ids) if (include_att and file_ids) else [],
        }

        def generate():
            # Startsignal
            yield sse({"event": "start", "chat_id": c.id})

            # ChatAI aufrufen (erst Intent ermitteln, DANN ggf. uploaden)
            try:
                out_json, full_reply = call_chatai(payload)
            except Exception as ex:
                current_app.logger.warning("ChatAI call failed (stream): %s", ex, exc_info=True)
                out_json, full_reply = None, f"Fehler in ChatAI: {ex}"

            # Intent auswerten
            do_upload, intent = _should_upload_from(out_json)
            current_app.logger.info("chat(stream): intent=%s do_upload=%s defer=%s files=%d", intent, do_upload, defer_upload, len(file_ids))

            # Upload nur bei Freigabe
            if do_upload and file_ids:
                if not defer_upload:
                    # synchron → sofort Event senden
                    try:
                        uploads = auto_upload_supported_attachments(
                            conv=c, file_ids=file_ids, source_message_id=user_msg.get("id")
                        )
                    except Exception:
                        uploads = []
                    if uploads:
                        yield sse({"event": "upload", "items": uploads})
                else:
                    # Hintergrund-Upload mit App-Context
                    try:
                        import threading
                        app = current_app._get_current_object()

                        def _bg():
                            try:
                                with app.app_context():
                                    auto_upload_supported_attachments(
                                        conv=c, file_ids=file_ids, source_message_id=user_msg.get("id")
                                    )
                            except Exception as _thr_ex:
                                app.logger.warning("bg upload failed: %s", _thr_ex, exc_info=True)

                        threading.Thread(target=_bg, daemon=True).start()
                    except Exception as _thr_ex:
                        current_app.logger.warning("start bg upload failed: %s", _thr_ex, exc_info=True)

            # Aktionen sofort ausführen
            if isinstance(out_json, dict):
                try:
                    acts = apply_chatai_actions(c, out_json)
                    yield sse({"event": "actions", "result": acts})
                except Exception:
                    pass

            # Antwort stückweise liefern
            acc = ""
            try:
                for piece in chunk_text(full_reply or ""):
                    acc += piece
                    yield sse({"event": "delta", "text": piece})
            except Exception:
                acc = str(full_reply or "")

            # final speichern
            msg_ = c.append_message("assistant", text=acc, trace=["ChatAI"])
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

            vurl = resolve_viewer_url(c)
            yield sse({"event": "done", "assistant_msg": msg_, "viewer_url": vurl})

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return Response(stream_with_context(generate()), headers=headers)
    except Exception as ex:
        current_app.logger.exception("chat_stream failed")
        return {"error": str(ex)}, 500
