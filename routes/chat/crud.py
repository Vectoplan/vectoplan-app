# services/app/routes/chat/crud.py
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import current_app, jsonify, request

from extensions import db
from models import Conversation

from . import bp
from .helpers import resolve_viewer_url


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


def _json_response(payload: Dict[str, Any], status: int = 200):
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


# ───────────────────────── Generic helpers ─────────────────────────

def _request_json() -> Dict[str, Any]:
    try:
        data = request.get_json(silent=True) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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


def _iso(value: Any) -> Optional[str]:
    try:
        if value is None:
            return None

        if hasattr(value, "isoformat"):
            text = value.isoformat()
        else:
            text = str(value)

        if text and not text.endswith("Z") and "+" not in text:
            text += "Z"

        return text

    except Exception:
        return None


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception:
        try:
            current_app.logger.exception("conversation lookup failed")
        except Exception:
            pass
        return None


def _has_start_card(conv: Conversation) -> bool:
    try:
        transcript = getattr(conv, "transcript", None)

        if not isinstance(transcript, list):
            return False

        for item in transcript:
            if not isinstance(item, dict):
                continue

            meta = item.get("meta") or {}

            if (
                meta.get("type") == "card"
                and str(meta.get("template") or "") == "project_welcome"
            ):
                return True

        return False

    except Exception:
        return False


def _post_start_card_if_missing(conv: Conversation) -> bool:
    """
    Post the project welcome card once.

    This is UI/chat bootstrap only. It does not create editor/project/world
    structures and does not call any legacy viewer backend.
    """
    try:
        if _has_start_card(conv):
            return False

        import messages as msg

        payload = {
            "wfs_url": current_app.config.get("PROJECT_WELCOME_WFS_URL", ""),
            "layer": current_app.config.get("PROJECT_WELCOME_LAYER", ""),
            "hint": current_app.config.get(
                "PROJECT_WELCOME_HINT",
                (
                    "Dies ist eine offene Alpha-Testversion von Vectoplan. "
                    "Alle Systeme können kostenlos genutzt werden."
                ),
            ),
        }

        msg.post_card_message(
            conversation=conv,
            template_key="project_welcome",
            payload=payload,
            role="service",
            trace=["system"],
            validate=False,
        )

        return True

    except Exception:
        try:
            current_app.logger.warning("failed to post project_welcome", exc_info=True)
        except Exception:
            pass
        return False


def _conversation_payload(conv: Conversation, *, include_transcript: bool = False) -> Dict[str, Any]:
    editor_url = resolve_viewer_url(conv)

    payload: Dict[str, Any] = {
        "ok": True,
        "chat_id": conv.id,
        "title": getattr(conv, "title", None),
        "viewer_url": editor_url,
        "editor_url": editor_url,
        "workspace": {
            "mode": "editor",
            "editor_url": editor_url,
        },
        "legacy_3d_backend": False,
        "created_at": _iso(getattr(conv, "created_at", None)),
        "updated_at": _iso(getattr(conv, "updated_at", None)),
    }

    if include_transcript:
        try:
            payload["transcript"] = conv.transcript or []
        except Exception:
            payload["transcript"] = []

    return payload


# ───────────────────────── Routes ─────────────────────────

@bp.post("/v1/chats")
def create_chat():
    """
    Create a new chat/conversation.

    Speckle-free behavior:
    - create Conversation
    - optionally set a simple title
    - post project_welcome card once
    - return local editor iframe route as viewer_url compatibility value

    No ensure/refresh.
    No old viewer lookup.
    No external 3D backend call.
    """
    try:
        data = _request_json()

        conv = Conversation()

        title = _clean_text(data.get("title"), "", 180)
        if title and hasattr(conv, "title"):
            try:
                conv.title = title
            except Exception:
                pass

        db.session.add(conv)
        db.session.flush()

        _post_start_card_if_missing(conv)

        db.session.add(conv)
        db.session.commit()

        payload = _conversation_payload(conv, include_transcript=True)

        return _json_response(payload, status=201)

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("create_chat failed")
        return _json_error(str(exc), 500, code="create_chat_failed")


@bp.get("/v1/chats/<chat_id>")
def get_chat(chat_id: str):
    """
    Load one chat/conversation.

    The returned `viewer_url` remains for frontend compatibility and points to
    the local editor iframe route.
    """
    try:
        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("not found", 404, code="chat_not_found")

        payload = _conversation_payload(conv, include_transcript=True)

        return _json_response(payload, status=200)

    except Exception as exc:
        current_app.logger.exception("get_chat failed")
        return _json_error(str(exc), 500, code="get_chat_failed")