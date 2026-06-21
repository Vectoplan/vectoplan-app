# services/app/routes/chat/sync.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app, jsonify, request

from extensions import db
from models import Blob, Conversation

from . import bp
from .helpers import (
    apply_chatai_actions,
    attachment_meta_for_transcript,
    call_chatai,
    load_attachments,
    numeric_project_id,
    resolve_viewer_url,
)


# ───────────────────────── Constants ─────────────────────────

LEGACY_PUBLISH_INTENTS = {
    "UPLOAD_VERSION",
    "PUBLISH_3D",
    "PUBLISH_MODEL",
    "UPLOAD_MODEL",
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


# ───────────────────────── Parsing helpers ─────────────────────────

def _as_bool(value: Any) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on"}

    except Exception:
        return False


def _request_json() -> Dict[str, Any]:
    try:
        data = request.get_json(silent=True) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalized_file_ids(raw_attachments: Any) -> List[str]:
    out: List[str] = []

    try:
        for item in raw_attachments or []:
            value = ""

            if isinstance(item, bytes):
                value = item.decode("utf-8", "ignore").strip()

            elif isinstance(item, str):
                value = item.strip()

            elif isinstance(item, dict):
                value = str(
                    item.get("file_id")
                    or item.get("blob_id")
                    or item.get("id")
                    or ""
                ).strip()

            if value:
                out.append(value)

    except Exception:
        pass

    # Preserve order while removing duplicates.
    deduped: List[str] = []
    seen = set()

    for item in out:
        if item in seen:
            continue

        seen.add(item)
        deduped.append(item)

    return deduped


def _include_attachments(data: Dict[str, Any]) -> bool:
    """
    Default: include attachments in the ChatAI payload.

    Explicit opt-outs:
    - query ?include_attachments=0
    - body {"include_attachments": 0|false}
    - body {"no_att": true}
    """
    try:
        query_value = request.args.get("include_attachments")

        if query_value is not None and str(query_value).strip() == "0":
            return False

        if data.get("include_attachments") in {0, "0", False}:
            return False

        if _as_bool(data.get("no_att")):
            return False

        return True

    except Exception:
        return True


def _legacy_publish_intent(out_json: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Detect old publish/upload intents without executing them.

    This route does not publish to any legacy 3D backend anymore.
    """
    try:
        if not isinstance(out_json, dict):
            return False, ""

        status = str(out_json.get("status") or "ok").strip().lower()
        intent = str(out_json.get("intent") or "").strip().upper()

        if status == "need_info":
            return False, intent

        return intent in LEGACY_PUBLISH_INTENTS, intent

    except Exception:
        return False, ""


def _chat_message_id() -> int:
    try:
        return time.time_ns() % 2_000_000_000
    except Exception:
        return int(time.time()) % 2_000_000_000


# ───────────────────────── DB helpers ─────────────────────────

def _get_or_create_conversation(data: Dict[str, Any]) -> Tuple[Optional[Conversation], Optional[Tuple[Dict[str, Any], int]]]:
    """
    Return (conversation, error_response_tuple).

    Keeps the outer route readable and ensures creation errors are handled
    consistently.
    """
    try:
        chat_id = str(data.get("chat_id") or "").strip()

        if chat_id:
            conv = Conversation.query.get(chat_id)

            if not conv:
                return None, (
                    {
                        "ok": False,
                        "error": {
                            "code": "chat_not_found",
                            "message": "chat not found",
                        },
                        "status": 404,
                    },
                    404,
                )

            return conv, None

        conv = Conversation()
        db.session.add(conv)
        db.session.flush()

        return conv, None

    except Exception as exc:
        current_app.logger.exception("conversation create/load failed")
        return None, (
            {
                "ok": False,
                "error": {
                    "code": "conversation_failed",
                    "message": str(exc),
                },
                "status": 500,
            },
            500,
        )


def _load_attachment_meta(file_ids: List[str]) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []

    for file_id in file_ids or []:
        try:
            blob = Blob.query.get(str(file_id))

            if not blob:
                continue

            attachments.append(attachment_meta_for_transcript(blob))

        except Exception:
            continue

    return attachments


def _append_user_message(
    conv: Conversation,
    *,
    text: str,
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        return conv.append_message(
            "user",
            text=text,
            attachments=attachments,
        )
    except TypeError:
        return conv.append_message(
            role="user",
            text=text,
            attachments=attachments,
        )


def _append_assistant_message(
    conv: Conversation,
    *,
    text: str,
) -> Dict[str, Any]:
    try:
        return conv.append_message(
            "assistant",
            text=text,
            trace=["ChatAI"],
        )
    except TypeError:
        return conv.append_message(
            role="assistant",
            text=text,
            trace=["ChatAI"],
        )


# ───────────────────────── Route ─────────────────────────

@bp.post("/v1/chat")
def chat_message():
    """
    Synchronous Chat API.

    Request JSON:
    {
      "chat_id": "<optional>",
      "message": "Text oder leer",
      "attachments": [
        "<file_id>",
        {"file_id": "..."},
        {"id": "..."}
      ],
      "include_attachments": true|false
    }

    Speckle-free behavior:
    - stores the user message
    - forwards text + optional attachment metadata to ChatAI
    - applies safe ChatAI actions
    - stores the assistant reply
    - returns the editor iframe URL as viewer_url compatibility value
    - does not start background uploads
    - does not publish 3D files
    """
    try:
        data = _request_json()
        text = str(data.get("message") or "")

        raw_attachments = data.get("attachments") or []
        file_ids = _normalized_file_ids(raw_attachments)

        current_app.logger.info(
            "chat(sync): raw_attachments=%s -> file_ids=%s",
            raw_attachments,
            file_ids,
        )

        if not text and not file_ids:
            return _json_error("empty message", 400, code="empty_message")

        include_attachments = _include_attachments(data)

        # `defer_upload` is accepted for compatibility but intentionally ignored.
        defer_upload_requested = (
            _as_bool(request.args.get("defer_upload"))
            or _as_bool(data.get("defer_upload"))
        )

        conv, error = _get_or_create_conversation(data)

        if error:
            payload, status = error
            return _json_response(payload, status=status)

        if conv is None:
            return _json_error("conversation unavailable", 500, code="conversation_unavailable")

        attachments_meta = _load_attachment_meta(file_ids)

        user_msg = _append_user_message(
            conv,
            text=text,
            attachments=attachments_meta,
        )

        current_app.logger.info(
            "chat(sync): saved user message attachments=%s",
            attachments_meta,
        )

        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        payload = {
            "chat_id": conv.id,
            "message": text,
            "coordinate": [0.0, 0.0],
            "project_id": numeric_project_id(conv),
            "chat_message_id": _chat_message_id(),
            "attachments": load_attachments(file_ids) if (include_attachments and file_ids) else [],
        }

        out_json, reply_text = call_chatai(payload)

        legacy_publish_requested, intent = _legacy_publish_intent(out_json)

        current_app.logger.info(
            "chat(sync): intent=%s legacy_publish_requested=%s defer_upload_ignored=%s files=%d",
            intent,
            legacy_publish_requested,
            defer_upload_requested,
            len(file_ids),
        )

        actions_result: Dict[str, Any] = {}

        if isinstance(out_json, dict):
            actions_result = apply_chatai_actions(conv, out_json)

        assistant_msg = _append_assistant_message(
            conv,
            text=reply_text,
        )

        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        editor_url = resolve_viewer_url(conv)

        response_payload: Dict[str, Any] = {
            "ok": True,
            "chat_id": conv.id,
            "user_msg": user_msg,
            "assistant_msg": assistant_msg,
            "viewer_url": editor_url,
            "editor_url": editor_url,
            "legacy_3d_backend": False,
            "publish": {
                "requested": bool(legacy_publish_requested),
                "intent": intent or None,
                "executed": False,
                "reason": "legacy_publish_disabled" if legacy_publish_requested else None,
                "defer_upload_ignored": bool(defer_upload_requested),
            },
        }

        if actions_result:
            response_payload["actions"] = actions_result

        return _json_response(response_payload, status=200)

    except Exception as exc:
        current_app.logger.exception("chat_message failed")
        return _json_error(str(exc), 500, code="chat_message_failed")