# /services/app/routes/chat/sync.py
from __future__ import annotations

from flask import request, jsonify, current_app

from extensions import db
from models import Conversation, Blob

from . import bp
from .helpers import (
    load_attachments,
    attachment_meta_for_transcript,
    auto_upload_supported_attachments,
    resolve_viewer_url,
    call_chatai,
    apply_chatai_actions,
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


def _normalized_file_ids(raw_atts) -> list[str]:
    out: list[str] = []
    try:
        for it in (raw_atts or []):
            if isinstance(it, (str, bytes)):
                s = it.decode("utf-8", "ignore") if isinstance(it, bytes) else str(it)
                s = s.strip()
                if s:
                    out.append(s)
            elif isinstance(it, dict):
                s = str(it.get("file_id") or it.get("id") or "").strip()
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


@bp.post("/v1/chat")
def chat_message():
    """
    Synchrone Chat-API.
    Request JSON:
      {
        "chat_id": "<optional>",
        "message": "Text oder leer",
        "attachments": ["<file_id>" | {"file_id": "..."} | {"id": "..."}, ...],
        "defer_upload": true|false,
        "include_attachments": true|false
      }
    """
    data = request.get_json(silent=True) or {}
    text = str(data.get("message") or "")

    raw_atts = data.get("attachments") or []
    file_ids = _normalized_file_ids(raw_atts)
    current_app.logger.info("chat(s): raw_atts=%s -> file_ids=%s", raw_atts, file_ids)

    if not text and not file_ids:
        return jsonify({"error": "empty message"}), 400

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
                return jsonify({"error": "chat not found"}), 404
        else:
            c = Conversation()
            db.session.add(c)
            db.session.flush()

        # User-Message speichern mit vollwertigen Attachment-Metadaten
        atts_meta = []
        for fid in file_ids:
            try:
                b = Blob.query.get(str(fid))
                if b:
                    atts_meta.append(attachment_meta_for_transcript(b))
            except Exception:
                continue

        user_msg = c.append_message("user", text=text, attachments=atts_meta)
        current_app.logger.info("chat_message: saved user_msg.attachments=%s", atts_meta)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        # ChatAI call (chat_id explizit übergeben)
        payload = {
            "chat_id": c.id,
            "message": text,
            "coordinate": [0.0, 0.0],
            "project_id": numeric_project_id(c),
            "chat_message_id": __import__("time").time_ns() % 2_000_000_000,
            "attachments": load_attachments(file_ids) if (include_att and file_ids) else [],
        }
        out_json, reply_text = call_chatai(payload)

        # Intent auswerten
        do_upload, intent = _should_upload_from(out_json)
        current_app.logger.info("chat(s): intent=%s do_upload=%s defer=%s files=%d", intent, do_upload, defer_upload, len(file_ids))

        uploads = []
        if do_upload and file_ids:
            if defer_upload:
                # Hintergrund-Upload (App-Context sichern)
                try:
                    import threading
                    app = current_app._get_current_object()

                    def _bg():
                        try:
                            with app.app_context():
                                auto_upload_supported_attachments(
                                    conv=c, file_ids=file_ids, source_message_id=user_msg.get("id")
                                )
                        except Exception as ex_bg:
                            app.logger.warning("bg upload failed: %s", ex_bg, exc_info=True)

                    threading.Thread(target=_bg, daemon=True).start()
                except Exception as ex_thr:
                    current_app.logger.warning("start bg upload failed: %s", ex_thr, exc_info=True)
            else:
                # Synchroner Upload
                try:
                    uploads = auto_upload_supported_attachments(
                        conv=c, file_ids=file_ids, source_message_id=user_msg.get("id")
                    )
                except Exception:
                    uploads = []

        # Aktionen anwenden
        actions_result = {}
        if isinstance(out_json, dict):
            actions_result = apply_chatai_actions(c, out_json)

        # Textantwort persistieren
        assistant_msg = c.append_message("assistant", text=reply_text, trace=["ChatAI"])

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        vurl = resolve_viewer_url(c)
        resp = {
            "chat_id": c.id,
            "user_msg": user_msg,
            "assistant_msg": assistant_msg,
            "viewer_url": vurl,
        }
        if uploads:
            resp["uploads"] = uploads
        if actions_result:
            resp["actions"] = actions_result

        return jsonify(resp), 200

    except Exception as ex:
        current_app.logger.exception("chat_message failed")
        return jsonify({"error": str(ex)}), 500
