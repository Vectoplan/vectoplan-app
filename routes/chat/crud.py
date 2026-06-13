# /services/app/routes/chat/crud.py
from __future__ import annotations

from flask import request, jsonify, current_app

from extensions import db
from models import Conversation

from . import bp
from .helpers import ensure_and_refresh, resolve_viewer_url, msg  # msg nur für type hints / imports


@bp.post("/v1/chats")
def create_chat():
    try:
        c = Conversation()
        db.session.add(c)
        db.session.commit()

        try:
            ensure_and_refresh(c)
        except Exception:
            current_app.logger.warning("vectoplan ensure failed", exc_info=True)

        # Startkarte posten (idempotent, ohne Validierung)
        try:
            payload = {
                "wfs_url": current_app.config.get("PROJECT_WELCOME_WFS_URL", ""),
                "layer": current_app.config.get("PROJECT_WELCOME_LAYER", ""),
                "hint": current_app.config.get("PROJECT_WELCOME_HINT", "Dies ist eine offene Alpha-Testversion von Vectoplan. Alle Systeme können kostenlos genutzt werden. Hier bauen testen wir neue System zur BigData-Auswertung und automatischen Gebäudegenerierung"),
            }
            import messages as _msg
            _msg.post_card_message(conversation=c, template_key="project_welcome", payload=payload, role="service", trace=["system"], validate=False)
        except Exception:
            current_app.logger.warning("failed to post project_welcome", exc_info=True)

        vurl = resolve_viewer_url(c)
        return jsonify({"chat_id": c.id, "viewer_url": vurl}), 201
    except Exception as ex:
        current_app.logger.exception("create_chat failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/v1/chats/<chat_id>")
def get_chat(chat_id: str):
    try:
        c = Conversation.query.get(chat_id)
        if not c:
            return jsonify({"error": "not found"}), 404
        try:
            ensure_and_refresh(c)
        except Exception:
            pass
        vurl = resolve_viewer_url(c)
        return jsonify({
            "chat_id": c.id,
            "title": c.title,
            "transcript": c.transcript or [],
            "viewer_url": vurl,
            "created_at": c.created_at.isoformat() + "Z",
            "updated_at": c.updated_at.isoformat() + "Z"
        })
    except Exception as ex:
        current_app.logger.exception("get_chat failed")
        return jsonify({"error": str(ex)}), 500
